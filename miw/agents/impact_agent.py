"""Graph B — verified evidence becomes findings a reviewer can act on.

    classify ──▶ artifacts ──▶ score_and_project ──▶ review_gate ──▶ END

Linear on purpose. There is nothing here for a model to plan: the signal class comes
from `PROBE_TO_SIGNAL`, the affected artifacts from each Location's `object_type`, and
the severity from the existing ladder. It is a graph rather than a function only so the
`review_gate` can genuinely suspend and resume — which needs a checkpointer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from langgraph.graph import END, START, StateGraph

from miw.agents.nodes.impact import (artifacts_node, classify_node,
                                     score_and_project_node)
from miw.agents.nodes.review import review_gate_node
from miw.agents.state import ImpactState

CHECKPOINT_DB = Path("state/agent_checkpoints.db")


def build_impact_graph():
    g = StateGraph(ImpactState)
    g.add_node("classify", classify_node)
    g.add_node("artifacts", artifacts_node)
    g.add_node("score", score_and_project_node)
    g.add_node("review_gate", review_gate_node)
    g.add_edge(START, "classify")
    g.add_edge("classify", "artifacts")
    g.add_edge("artifacts", "score")
    g.add_edge("score", "review_gate")
    g.add_edge("review_gate", END)
    return g


def compile_impact_graph(checkpointer=None, db_path: Optional[Path] = None):
    g = build_impact_graph()
    if checkpointer is False:
        return g.compile()
    if checkpointer is None:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver
        path = db_path or CHECKPOINT_DB
        path.parent.mkdir(parents=True, exist_ok=True)
        checkpointer = SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))
    return g.compile(checkpointer=checkpointer)


def run_impact_agent(dep, probe_signals, claims=(), graph=None,
                     thread_id: str = "") -> tuple[dict, object]:
    """Run to the review gate. Returns (state, app) so the caller can resume it."""
    from miw.schema import to_jsonable

    app = graph or compile_impact_graph()
    init = {
        "dep_id": dep.dep_id,
        "canonical_name": dep.canonical_name,
        "probe_signals": list(probe_signals),
        "claims": [c if isinstance(c, dict) else to_jsonable(c) for c in claims],
        "locations": [to_jsonable(l) for l in dep.locations],
        "llm_calls": 0,
    }
    # Fresh thread per invocation, for the same reason as the signal agent: the
    # checkpointer is for resuming a crashed run, not for continuing a finished one.
    from miw.schema import utcnow
    tid = thread_id or f"impact:{dep.dep_id}:{utcnow()}"
    cfg = {"configurable": {"thread_id": tid, "dep": dep}}
    return dict(app.invoke(init, config=cfg)), app
