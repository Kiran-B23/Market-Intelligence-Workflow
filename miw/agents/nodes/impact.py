"""Impact nodes: verified evidence -> findings, per course. Deliberately no model.

The prior Curriculum Gap Analyzer asked a model to list impacted courses, sessions,
projects, assessments and quizzes. MIW does not, because it does not have to: every
`Location` already records its course, its session number and its `object_type`, so the
join is a `groupby`, not a judgement. A model guessing at something we can compute is
how a monitor starts inventing scope — and it was the source of that system's
`impacted_sessions` being prose rather than ids.

`classify` and `score` reuse `analyse.score` and `analyse.project` unchanged, so an
agent-produced finding is scored by exactly the same ladder as a scheduled one.
"""
from __future__ import annotations

from collections import Counter


def classify_node(state: dict, config=None) -> dict:
    """Probe signals -> one S-class, by the existing deterministic mapping."""
    from miw.analyse.score import PROBE_TO_SIGNAL, SIGNALS

    signals = state.get("probe_signals") or []
    hit = next((PROBE_TO_SIGNAL[s] for s in signals if s in PROBE_TO_SIGNAL), "")
    if not hit:
        return {"signal": "", "trajectory": ["classify: no signal class for "
                                             f"{signals or 'no probe signals'}"]}
    return {"signal": hit,
            "trajectory": [f"classify: {signals} -> {hit} ({SIGNALS[hit][0]})"]}


def artifacts_node(state: dict, config=None) -> dict:
    """Which KINDS of curriculum artifact are affected, counted from the locations.

    This is the prior system's `assessment_gap` / `quiz_gap` / `project_gap` idea, taken
    from data instead of from a prompt: `object_type` is already on every Location.
    """
    locs = state.get("locations") or []
    counts = Counter(l.get("object_type") or "UNKNOWN" for l in locs)
    return {"artifacts": dict(counts),
            "trajectory": [f"artifacts: {dict(counts)}"]}


def score_and_project_node(state: dict, config=None) -> dict:
    """Severity, blast radius and one finding per affected course.

    Per course, not filtered: 129 of 460 dependencies are shared, and
    `llama-3.3-70b-versatile` is blast radius 67 across the curriculum but 13 inside
    Intro to Gen AI. A page that copied the global number would overstate by 5.2x.
    """
    from miw.analyse import project, score
    from miw.schema import to_jsonable

    cfg = (config or {}).get("configurable") or {}
    dep = cfg.get("dep")
    if dep is None:
        return {"errors": ["score: no dependency in config"]}
    signal = state.get("signal")
    if not signal:
        return {"trajectory": ["score: skipped, no signal class"]}

    findings = []
    for course in sorted({l.course for l in dep.locations}):
        local = project.course_dependency(dep, course)
        radius = score.blast_radius(local)
        findings.append({
            "course": course,
            "signal": signal,
            "severity": score.severity_for(signal, local, radius),
            "blast_radius": radius,
            "graded_locations": local.graded_locations,
            "questions_executing": len(local.questions_that_execute_it),
            "locations": len(local.locations),
            "sessions": sorted({l.session_no for l in local.locations if l.session_no}),
        })
    glob = score.blast_radius(dep)
    return {"findings": findings, "blast_radius": glob,
            "severity": max((f["severity"] for f in findings),
                            key=lambda s: score.SEVERITY_ORDER.index(s), default=""),
            "trajectory": [f"scored {len(findings)} course finding(s); "
                           f"global blast {glob}"]}
