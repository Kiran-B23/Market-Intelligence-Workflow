"""The human gate — a real LangGraph `interrupt`, and the point where output persists.

Worth being precise about, because the prior Curriculum Gap Analyzer's architecture
document described `interrupt()` as "a critical LangGraph feature" it depended on, while
both of its graphs called `compile()` with **no checkpointer**. Without one, `interrupt`
cannot suspend anything, so its human-in-the-loop was in practice a `status` column plus
a separately triggered second run — and its own `CLAUDE.md` says so.

Here the graph is compiled with a `SqliteSaver`, so this node genuinely suspends: the run
stops, the state is durable, and resuming with `Command(resume=...)` continues from this
point rather than re-running the pipeline.

**Why it also writes a file.** A checkpoint is not a product. The first version of this
node suspended correctly and the UI showed nothing, because the UI reads
`out/findings_*.json` and the agent's output existed only inside
`state/agent_checkpoints.db`. So the state is written to its own artifact here, before
the interrupt — the same shape as a terminal node persisting its output, minus the trap
of putting I/O after a call that suspends.

It is a SEPARATE artifact, deliberately. Pipeline findings have been through `analyse`:
two-run confirmation, week-over-week diffing, reviewer suppression. An agent finding
suspended at this gate has been through none of that, and merging the two would
misrepresent both.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from langgraph.types import interrupt

# Module-level so a test can point it somewhere temporary. It was a hardcoded relative
# path, and the test suite duly wrote four fixture runs called `gizmo-1` into the real
# `out/agent_findings_<date>.json`, which then rendered in the UI.
OUT = Path("out")


def _artifact_path(day: str = "") -> Path:
    return OUT / f"agent_findings_{day or date.today().isoformat()}.json"


def persist(state: dict, thread_id: str) -> Path:
    """Merge this run into today's agent artifact, keyed by thread id.

    Keyed rather than appended so a resumed or re-run thread replaces its own row
    instead of accumulating duplicates.
    """
    path = _artifact_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        blob = json.loads(path.read_text()) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        blob = {}
    rows = {r["thread_id"]: r for r in blob.get("runs", []) if r.get("thread_id")}
    rows[thread_id] = {
        "thread_id": thread_id,
        "dep_id": state.get("dep_id", ""),
        "canonical_name": state.get("canonical_name", ""),
        "signal": state.get("signal", ""),
        "severity": state.get("severity", ""),
        "blast_radius": state.get("blast_radius", 0),
        "findings": state.get("findings", []),
        "artifacts": state.get("artifacts", {}),
        "evidence": [{"source_url": c.get("source_url"), "quote": c.get("quote"),
                      "tier": c.get("tier"), "kind": c.get("kind"),
                      "statement": c.get("statement"),
                      "retrieved_at": c.get("retrieved_at")}
                     for c in (state.get("claims") or [])],
        "trajectory": state.get("trajectory", []),
        "review": state.get("review", ""),
        "awaiting_review": not state.get("review"),
    }
    path.write_text(json.dumps(
        {"written_at": date.today().isoformat(), "runs": list(rows.values())}, indent=1))
    return path


def review_gate_node(state: dict, config=None) -> dict:
    """Persist, then suspend for a reviewer. Resumes with their verdict."""
    cfg = (config or {}).get("configurable") or {}
    thread_id = str(cfg.get("thread_id") or f"impact:{state.get('dep_id','?')}")
    persist(state, thread_id)

    verdict = interrupt({
        "kind": "finding_review",
        "thread_id": thread_id,
        "dependency": state.get("canonical_name", ""),
        "signal": state.get("signal", ""),
        "severity": state.get("severity", ""),
        "findings": state.get("findings", []),
        "artifacts": state.get("artifacts", {}),
        "evidence": [{"source_url": c.get("source_url"), "quote": c.get("quote"),
                      "tier": c.get("tier")} for c in (state.get("claims") or [])],
    })
    decision = (verdict or {}).get("decision", "") if isinstance(verdict, dict) else str(verdict)
    out = {"review": decision or "unreviewed",
           "trajectory": [f"review: {decision or 'resumed with no decision'}"]}
    # Record the verdict in the artifact too, so the UI stops showing it as pending.
    persist({**state, **out}, thread_id)
    return out
