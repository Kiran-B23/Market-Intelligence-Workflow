"""Reviewer triage, and the feedback it produces.

This is the loop that decides whether the digest survives contact with the team. A
weekly report nobody can push back on gets skimmed once and ignored; a report where
"not actionable" actually changes next week's output gets read.

Three things happen with a decision:

1. **Precision becomes measurable.** `State.precision_stats()` is accepted ÷ triaged,
   which is the PRD's supporting metric. Before this it could not be computed at all.
2. **Rejections suppress recurrences — but only while nothing changed.** Suppression is
   keyed on the finding's *fingerprint* (its signal plus the evidence URLs). If the
   situation moves, the fingerprint moves and the finding comes back. A blanket
   "never show me this again" would hide a tool going from *redirected* to *dead*.
3. **Per-dimension corrections become guidance.** Correcting "when" is a different
   lesson from correcting "what", and `feedback_corpus()` keeps them labelled so the
   refinement prompt can apply the right one. This is the `learned_rules.md` pattern
   from `agentic-interview-question-generator/src/memory.py`, kept human-readable.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from miw.state import State

FEEDBACK_FILE = Path("registry/learned_feedback.md")
MAX_FEEDBACK_ITEMS = 12
MAX_ITEM_CHARS = 400

HEADER = (
    "# Learned reviewer feedback\n"
    "<!-- Appended by `main.py triage`. Read into the recommendation prompt.\n"
    "     Human-editable: delete a line to stop the system learning from it. -->\n\n"
)


def split_of(finding_id: str) -> str:
    """Deterministic held-out split: "learn" or "score".

    Half of all reviewer decisions inform the system (they reach
    `learned_feedback.md` and therefore the refinement prompt); the other half are
    reserved purely for measurement. Without this, precision degrades into
    self-assessment the moment the pipeline starts learning from feedback - the exact
    failure documented in `agentic-interview-question-generator/eval/run_eval.py`,
    where runs scored 0.9 while reviewers rejected most of the set.

    Suppression is deliberately NOT split: a reviewer who says "stop showing me this"
    must be obeyed regardless of which bucket the decision landed in. Only the
    *learning* is held back.
    """
    h = int(hashlib.sha256(finding_id.encode()).hexdigest()[:8], 16)
    return "learn" if h % 2 == 0 else "score"


def fingerprint_of_raw(f: dict) -> str:
    """Recompute a finding's fingerprint from its serialised form.

    Shared by the CLI and the API so a decision recorded in the browser suppresses the
    same thing a decision recorded on the command line would.
    """
    from miw.analyse.score import _fingerprint
    urls = sorted({c["source_url"] for c in (f.get("claims") or [])
                   if c.get("source_url")})
    return _fingerprint(f.get("signal", ""),
                        ",".join(sorted(set(f.get("probe_signals") or []))),
                        ",".join(urls))


def suppressed(state: State, finding_id: str, fingerprint: str) -> Optional[str]:
    """Reason this finding should be held back, or None.

    Held back only when a reviewer rejected *this same situation*. A changed
    fingerprint means new evidence, and new evidence deserves another look.
    """
    row = state.latest_decision(finding_id)
    if row is None or row["verdict"] != "rejected":
        return None
    if (row["fingerprint"] or "") != fingerprint:
        return None
    return row["reason"] or "previously marked not actionable"


def record(state: State, *, finding_id: str, dep_id: str, signal: str, verdict: str,
           fingerprint: str, reason: str = "", corrected_what: str = "",
           corrected_why: str = "", corrected_when: str = "", reviewer: str = "",
           now: str = "") -> None:
    state.record_decision(
        finding_id=finding_id, dep_id=dep_id, signal=signal, verdict=verdict,
        reason=reason, fingerprint=fingerprint, corrected_what=corrected_what,
        corrected_why=corrected_why, corrected_when=corrected_when,
        reviewer=reviewer, now=now)
    # Only the "learn" half shapes future output; the "score" half stays clean for
    # measurement. Suppression above already applied to both.
    if split_of(finding_id) == "learn":
        _append_feedback(signal, reason, corrected_what, corrected_why,
                         corrected_when, verdict)


def _append_feedback(signal: str, reason: str, what: str, why: str, when: str,
                     verdict: str) -> None:
    lines = []
    if verdict == "rejected" and reason:
        lines.append(f"- [{signal}] not actionable because: {reason[:MAX_ITEM_CHARS]}")
    for label, text in (("what", what), ("why", why), ("when", when)):
        if text:
            lines.append(f"- [{signal}] reviewer corrected \"{label}\" to: "
                         f"{text[:MAX_ITEM_CHARS]}")
    if not lines:
        return
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = FEEDBACK_FILE.read_text() if FEEDBACK_FILE.exists() else HEADER
    kept = [ln for ln in existing.splitlines() if ln.startswith("- ")]
    for ln in lines:
        if ln not in kept:
            kept.append(ln)
    FEEDBACK_FILE.write_text(HEADER + "\n".join(kept[-40:]) + "\n")


def feedback_corpus(signal: str = "", limit: int = MAX_FEEDBACK_ITEMS) -> str:
    """Recorded corrections, most relevant first, for the refinement prompt.

    Corrections on the same drift class are listed first: how urgently to treat a dead
    link is a different judgement from how urgently to treat a docs rewrite.
    """
    if not FEEDBACK_FILE.exists():
        return ""
    lines = [ln for ln in FEEDBACK_FILE.read_text().splitlines() if ln.startswith("- ")]
    if signal:
        same = [ln for ln in lines if ln.startswith(f"- [{signal}]")]
        other = [ln for ln in lines if not ln.startswith(f"- [{signal}]")]
        lines = same + other
    return "\n".join(lines[:limit])
