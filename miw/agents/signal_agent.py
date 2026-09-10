"""Graph A — acquire and verify evidence about one dependency.

    plan ──▶ read ──▶ done
      │        │
      │        └──▶ replan ──▶ read      (bounded by max_attempts)
      ├──▶ record_silence                 (vendor publishes nothing)
      └──▶ give_up

This is the one part of MIW that genuinely wants a graph. It branches on what the last
page contained, it carries a visited set, and a sweep over many vendors has to resume
after a crash — so it is compiled with a real `SqliteSaver`, unlike the prior
Curriculum Gap Analyzer whose graphs called `compile()` with no checkpointer while its
architecture document described `interrupt()` as a critical feature it relied on.

**LangGraph orchestrates; it does not call the model.** Nodes go through
`miw.llm.complete`, MIW's own provider layer, so the Claude CLI / Anthropic / OpenRouter
choice, the 16 denied tools, the 120-call and $2.00 daily caps and the byte-for-byte
provider parity test all keep applying inside the graph. Introducing
`langchain_anthropic` here would fork the provider path and silently void all four.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from langgraph.graph import END, START, StateGraph

from miw.agents.nodes.gate import (give_up_node, record_silence_node,
                                   route_after_plan, route_after_read)
from miw.agents.nodes.plan import plan_pages_node
from miw.agents.nodes.read import read_pages_node
from miw.agents.state import SignalState

CHECKPOINT_DB = Path("state/agent_checkpoints.db")


def build_signal_graph():
    g = StateGraph(SignalState)
    g.add_node("plan", plan_pages_node)
    g.add_node("read", read_pages_node)
    g.add_node("record_silence", record_silence_node)
    g.add_node("give_up", give_up_node)

    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", route_after_plan, {
        "read": "read", "record_silence": "record_silence",
        "give_up": "give_up", "stop": END})
    # The loop: nothing substantiated and attempts left -> plan again with the failed
    # paths in hand, which is what stops it re-proposing the same dead URL.
    g.add_conditional_edges("read", route_after_read, {
        "done": END, "replan": "plan", "give_up": "give_up"})
    g.add_edge("record_silence", END)
    g.add_edge("give_up", END)
    return g


def compile_signal_graph(checkpointer=None, db_path: Optional[Path] = None):
    """Compile with a durable checkpointer so a long sweep can resume.

    `checkpointer=False` compiles without one, which is what the tests use.
    """
    g = build_signal_graph()
    if checkpointer is False:
        return g.compile()
    if checkpointer is None:
        from langgraph.checkpoint.sqlite import SqliteSaver
        import sqlite3
        path = db_path or CHECKPOINT_DB
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        checkpointer = SqliteSaver(conn)
    return g.compile(checkpointer=checkpointer)


def run_signal_agent(dep, kinds=("DEPRECATION", "PRICING"), max_attempts: int = 2,
                     graph=None, thread_id: str = "", fetcher=None,
                     provider_domains=()) -> dict:
    """Acquire evidence for one dependency. Never raises; failures land on the state.

    `provider_domains` widens the allowlist to the party that actually SERVES a taught
    model, which is not always the party that owns it. Measured, and the reason this
    argument exists: `llama-3.3-70b-versatile` carries Meta's domains
    (`ai.meta.com`, `llama.com`, `huggingface.co`), so a run confined to those reads
    Meta's pages and finds nothing — Groq is who serves the id and therefore who
    publishes its retirement. Without this the agent read 6 pages and produced 0 claims
    on a model sitting in 20 curriculum locations.

    The widening must be EARNED, exactly as `trust.with_provider` requires: the caller
    passes only domains from a provider whose own catalogue named this exact id, which
    `probe/models.py` establishes and `main.py verify` asserts. The agent does not get
    to widen its own authority.
    """
    # Widening has to reach the EXTRACTOR, not just the allowlist. `gather_url` builds
    # its subject from `dep.subject()` internally, so passing the widened subject only
    # to the allowlist left every Groq-hosted quote classifying LEAD_ONLY against a
    # Meta-attributed model — non-substantiating, and silently dropped. So the widened
    # authority is folded into the Dependency the graph carries.
    #
    # `subject_with_provider` restricts widening to `kind == "model"`, which is the only
    # place owner and server come apart; that guard is honoured here rather than
    # bypassed by editing `official_domains` directly for every kind.
    subject = (dep.subject_with_provider(provider_domains) if provider_domains
               else dep.subject())
    if provider_domains and set(subject.official_domains) != set(dep.official_domains):
        import copy
        dep = copy.copy(dep)
        dep.official_domains = list(subject.official_domains)
    app = graph or compile_signal_graph()
    init: dict = {
        "dep_id": dep.dep_id,
        "canonical_name": dep.canonical_name,
        "allowed_domains": list(subject.official_domains),
        "kinds": list(kinds),
        "attempt": 0,
        "max_attempts": max_attempts,
        "status": "planning",
        "llm_calls": 0,
    }
    # Runtime dependencies go in `configurable`, never on the state: the state is
    # checkpointed and must stay plain serialisable data.
    # A FRESH thread per invocation by default. The checkpointer exists so a crashed
    # sweep can resume, not so that re-running a dependency continues its last run: the
    # list reducers append, so a reused thread accumulated a second run's trajectory on
    # top of the first and reported 12 pages read when it had read 5. Pass an explicit
    # `thread_id` to deliberately resume one.
    from miw.schema import utcnow
    tid = thread_id or f"signal:{dep.dep_id}:{utcnow()}"
    cfg = {"configurable": {"thread_id": tid, "dep": dep, "fetcher": fetcher}}
    return dict(app.invoke(init, config=cfg))
