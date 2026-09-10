"""The state that flows through MIW's agent graphs.

One rule shapes every field here: **the model contributes plans, never facts.** So the
state separates what a model proposed (`planned_urls`, `plan_reason`) from what our own
code established (`claims`, `refuted`, `unreadable`). A reader of a finished run can
always tell which is which, and the audit trail (`trajectory`) records the order things
happened in so a bad run is debuggable rather than mysterious.

Why LangGraph state at all, when the six pipeline stages are a plain CLI: acquisition
genuinely branches. "Which page does this vendor publish changes on" depends on what the
last page turned out to contain, the visited set has to be carried, and a sweep over
many vendors must resume after a crash. That is a state machine; the stages are not.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, TypedDict


def _extend(a: list, b: list) -> list:
    """Reducer: nodes append to these rather than replacing them."""
    return [*(a or []), *(b or [])]


class SignalState(TypedDict, total=False):
    """Acquire and verify evidence about ONE dependency."""
    # --- input, set by the caller -------------------------------------------
    dep_id: str
    canonical_name: str
    allowed_domains: list[str]        # the ONLY hosts this run may fetch
    kinds: list[str]                  # ClaimKind names to look for

    # --- what the model proposed (never a fact) -----------------------------
    planned_urls: list[str]
    plan_reason: str
    plan_says_nothing: bool           # model's claim that this vendor publishes none

    # --- what our code established ------------------------------------------
    claims: Annotated[list[dict], _extend]
    unreadable: Annotated[list[str], _extend]
    refuted: Annotated[list[str], _extend]
    dropped: Annotated[list[str], _extend]     # a page said something we could not trust
    rejected_urls: Annotated[list[str], _extend]   # off-allowlist or invented
    pages_read: Annotated[list[str], _extend]

    # --- loop control --------------------------------------------------------
    attempt: int
    max_attempts: int
    visited: Annotated[list[str], _extend]
    status: Literal["planning", "reading", "verified", "exhausted", "no_source", "blocked"]

    # --- audit + cost --------------------------------------------------------
    trajectory: Annotated[list[str], _extend]
    llm_calls: int
    errors: Annotated[list[str], _extend]


class ImpactState(TypedDict, total=False):
    """Turn ONE verified signal into findings, per course.

    Deliberately almost all-deterministic. The prior Curriculum Gap Analyzer asked a
    model to name impacted sessions, projects and assessments; MIW reads them off the
    inventory, because every Location already records its course, session and
    `object_type`. A model guessing at a join we can compute is how a monitor starts
    inventing scope.
    """
    dep_id: str
    canonical_name: str
    probe_signals: list[str]
    claims: list[dict]

    locations: list[dict]
    signal: str
    severity: str
    blast_radius: int
    # NOT a reducer field: only `score_and_project_node` writes it, and an append
    # reducer meant a resumed thread re-ran that node and doubled every course row —
    # `gemini-2.0-flash` was reported against four courses when it touches two.
    # A reducer belongs only on fields several nodes contribute to.
    findings: list[dict]
    artifacts: dict                              # object_type -> count

    note_source: str
    review: str                                  # set by the human gate
    trajectory: Annotated[list[str], _extend]
    llm_calls: int
    errors: Annotated[list[str], _extend]
