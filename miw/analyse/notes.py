"""The action note: what to act, why, and when.

Two layers, and the order matters. `compose()` builds all three parts
deterministically from the finding's own fields, so a digest is complete with no LLM
and no key. `refine()` then optionally asks a model to rewrite the same three parts —
handed only facts that are already verified, and forbidden from adding any.

Splitting the note in three is what makes reviewer feedback actionable. "This
recommendation was wrong" teaches nothing; "the urgency was wrong" is a correction the
next run can apply, which is how `miw/triage.py` feeds corrections back in.
"""
from __future__ import annotations

import re
from typing import Optional

from miw.llm import complete, load_prompt
from miw.schema import Dependency, Finding

WHY = {
    "S1": "Students following this session hit a dead end at the taught step, which "
          "arrives as a support ticket rather than a question.",
    "S2": "The taught step now demands an account or payment the session does not "
          "prepare students for, so some cohort members simply cannot complete it.",
    "S3": "The free allowance the session relies on no longer covers what students are "
          "asked to do.",
    "S4": "Teaching an abandoned tool costs students time on a skill the industry has "
          "already moved off.",
    "S5": "The written steps and screenshots no longer match what students see, which "
          "reads to them as the course being wrong.",
    "S6": "Code written against the taught version may no longer run on what students "
          "install today.",
    "S7": "A retired model id fails at call time, so every example in the session stops "
          "working.",
    "S8": "Screenshots and click-paths drift out of date, eroding trust in the material "
          "even where the tool still works.",
    "S10": "A better-suited tool exists; students are learning the second-best option.",
    "S11": "Course coverage has fallen behind what employers now expect.",
}

# Drift classes where merely naming the dependency dates a question. A dead docs link
# does not make every MCQ that mentions the tool wrong.
MENTION_RELEVANT_SIGNALS = {"S4", "S7"}

WHEN_BY_SEVERITY = {
    "critical": "This sprint — before any live cohort reaches the affected session.",
    "high": "Within two weeks.",
    "medium": "Next curriculum cycle.",
    "low": "Opportunistically, when the session is next touched.",
    "info": "No action needed yet; recorded for awareness.",
}


def compose(dep: Dependency, f: Finding) -> None:
    """Fill the triad deterministically. Always runs; never needs a model."""
    f.what_to_act = f.recommendation or f"Review {dep.canonical_name}."

    why = WHY.get(f.signal, "This affects material students are working through.")
    impact = []
    if f.questions_executing:
        impact.append(f"{f.questions_executing} graded item(s) execute it")
    if f.courses:
        impact.append(f"{len(f.courses)} course(s)")
    if f.blast_radius:
        impact.append(f"blast radius {f.blast_radius}")
    f.why_to_act = why + (f" Scope: {', '.join(impact)}." if impact else "")

    when = WHEN_BY_SEVERITY.get(f.severity, "Next curriculum cycle.")
    # Take the course and session from the SAME location. Pairing courses[0] with
    # sessions[0] from two independently sorted lists named a course that the earliest
    # session does not belong to.
    dated = [l for l in f.locations if l.session_no]
    if f.severity in ("critical", "high") and dated:
        first = min(dated, key=lambda l: (l.session_no, l.course))
        when = (f"This sprint — the earliest affected session is {first.course} "
                f"session {first.session_no}.")
    f.when_to_act = when
    f.note_source = "template"


# Sequences a hostile page could use to escape the untrusted block or impersonate the
# prompt's own structure. Neutralised rather than removed, so the quote a reviewer sees
# in the digest still matches what the page said.
_ESCAPE = re.compile(r"</?untrusted>|^\s*(#{1,6}|##\s*[A-Z])", re.I | re.M)


def _neutralise(text: str) -> str:
    return _ESCAPE.sub(lambda m: m.group(0).replace("<", "\u2039").replace(">", "\u203a")
                       .replace("#", "\u266f"), text or "")


def _fmt_evidence(f: Finding) -> str:
    lines = []
    for c in f.claims:
        if c.substantiating:
            lines.append(f"- [{c.tier.name}] {c.source_url} (retrieved "
                         f"{c.retrieved_at[:10]})\n  \"{_neutralise(c.quote[:280])}\"")
    if not lines and f.probe_signals:
        lines.append("- (no external citation needed: observed directly by our own "
                     f"HTTP/registry probe — {', '.join(sorted(set(f.probe_signals)))})")
    return "\n".join(lines) or "- none"


def _fmt_alternatives(f: Finding) -> str:
    out = []
    for a in f.alternatives:
        if not a.verified:
            continue
        bits = [a.homepage or "no homepage"]
        if a.free_student_path:
            bits.append("free student path confirmed")
        if a.signup_required:
            bits.append("signup required")
        out.append(f"- {a.name}: {', '.join(bits)}")
    return "\n".join(out) or "none"


def _fmt_locations(f: Finding) -> str:
    out = []
    for l in f.locations[:8]:
        where = f"session {l.session_no}" if l.session_no else l.evidence_source
        out.append(f"- {l.course} / {where} / {l.unit_name[:60]}")
    return "\n".join(out) or "- unknown"


def refine(dep: Dependency, f: Finding, feedback: str = "") -> bool:
    """Ask the model to rewrite the triad. Returns True only if it was accepted.

    Rejected outright if the model drops a part, or if it introduces a URL that is not
    already in the finding's evidence — the cheapest possible check that it invented a
    source. On any rejection the deterministic triad stays exactly as composed.
    """
    prompt = load_prompt(
        "recommendation_v1",
        signal=f.signal, signal_label=f.signal_label,
        dependency=dep.canonical_name, kind=dep.kind,
        summary=f.summary or "(see probe signals)",
        probe_signals=", ".join(sorted(set(f.probe_signals))) or "none",
        affected_urls=", ".join(f.affected_urls[:4]) or "none",
        taught_version=dep.taught_version or "not pinned in the course",
        latest_version=f.latest_version or "unknown",
        courses=", ".join(f.courses) or "unknown",
        locations=_fmt_locations(f),
        questions_executing=f.questions_executing,
        # Only pass the "merely mentions it" count for drift classes where the tool
        # itself is gone. Passing it for a dead docs link let the model write "814
        # course items reference it" - the same overstatement that was removed from
        # the deterministic template, reintroduced through the prompt.
        questions_mentioning=(f.questions_mentioning
                              if f.signal in MENTION_RELEVANT_SIGNALS
                              else "not relevant for this drift class"),
        evidence=_fmt_evidence(f),
        alternatives=_fmt_alternatives(f),
        feedback=feedback or "none recorded yet",
    )
    res = complete(prompt)
    if not res.ok:
        return False
    data = res.json()
    if not isinstance(data, dict):
        return False
    what, why, when = (str(data.get(k) or "").strip()
                       for k in ("what_to_act", "why_to_act", "when_to_act"))
    if not (what and why and when):
        return False

    allowed = " ".join([*f.affected_urls, *(c.source_url for c in f.claims),
                        *(a.homepage for a in f.alternatives), dep.homepage or "",
                        dep.docs_url or ""])
    import re
    for url in re.findall(r"https?://[^\s,)\]]+", f"{what} {why} {when}"):
        if url.rstrip("/.,);") not in allowed:
            return False           # invented a source: reject the whole rewrite

    f.what_to_act, f.why_to_act, f.when_to_act = what, why, when
    f.note_source = "llm"
    return True
