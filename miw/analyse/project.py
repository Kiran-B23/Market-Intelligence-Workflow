"""What a finding means inside one course.

A finding's identity is `(dependency, signal)` — `Finding.finding_id = _id(dep_id,
signal)` — with no course in it, and **133 of 464 dependencies are referenced by more
than one course**. So findings cannot be partitioned per course; they have to be
*projected* onto one, and the numbers recomputed rather than copied.

That distinction is not pedantic. Measured on the live artifact:

    llama-3.3-70b-versatile   global blast 65 / 15 graded
                              LLM Apps 53/12  ·  Intro to Gen AI 12/3

An "Intro to Gen AI" page that copied the stored `blast_radius` would claim 65 where
the honest figure is 12 — a 5.4x overstatement, on the page whose whole purpose is to
tell that course's owner how much work they have.

Two traps this module exists to avoid:

1. **`Finding.locations` is truncated.** `score.py` stores `dep.locations[:12]`.
   `llama-3.3-70b-versatile` has 18; projecting off the finding shows Building LLM
   Applications with 9 instead of 15. So the projection joins back to the inventory
   dependency by `dep_id` and never reads `f.locations`.
2. **Prose embeds the course.** `score.recommend()` interpolates
   `f.locations[0].course` and `notes.compose()` interpolates `len(f.courses)`, so the
   triad is course-contaminated and must be re-derived, not carried over.

An invariant worth keeping (and asserted in the tests): the per-course blast radii sum
to the global one, because the weights are additive over locations and locations
partition cleanly by course. That single equality catches double-counting, truncation
and any attempt to apportion a global number.
"""
from __future__ import annotations

import copy
from typing import Iterable, Optional

from miw.analyse import score
from miw.schema import Dependency, Finding

# Facts about a vendor, not about a course. A model Groq retired is retired in every
# course that teaches it, so these are carried across untouched.
VENDOR_FACTS = ("finding_id", "dep_id", "canonical_name", "signal", "signal_label",
                "kind_of_signal", "severity", "diff_class", "summary", "probe_signals",
                "claims", "alternatives", "latest_version", "raised_at",
                "affected_urls")


def course_dependency(dep: Dependency, course: str) -> Dependency:
    """The same dependency seen only through one course's locations."""
    out = copy.copy(dep)
    out.locations = [l for l in dep.locations if l.course == course]
    return out


def also_in(dep: Dependency, course: str) -> list[dict]:
    """The other courses this dependency reaches, with their own counts.

    Without this a course page silos: a reviewer fixing session 6 cannot see that the
    same id is wired into two other courses, and 133 of 464 dependencies are shared.
    """
    from miw.scope import slug_of
    out = []
    for other in sorted({l.course for l in dep.locations if l.course != course}):
        d = course_dependency(dep, other)
        out.append({"course": other, "slug": slug_of(other),
                    "locations": len(d.locations),
                    "graded_locations": d.graded_locations,
                    "blast_radius": score.blast_radius(d)})
    return out


def project_finding(f: Finding, dep: Dependency, course: str,
                    census: Optional[dict] = None) -> Optional[Finding]:
    """This finding as it applies to `course`, or None if it does not apply.

    `dep` must be the INVENTORY dependency, not one rebuilt from `f.locations` — see
    the module docstring. `census` is the screenshot census `score` already builds; when
    omitted the screenshot count is dropped rather than guessed at.
    """
    local = course_dependency(dep, course)
    if not local.locations:
        return None

    out = Finding(**{k: getattr(f, k) for k in VENDOR_FACTS
                     if k not in ("finding_id",)})
    out.finding_id = f.finding_id          # identity is global and must not move

    # --- recomputed, every one of them ------------------------------------
    out.courses = [course]
    out.locations = local.locations[:12]   # display cap only; counts come from `local`
    out.blast_radius = score.blast_radius(local)
    out.graded_locations = local.graded_locations
    executing = local.questions_that_execute_it
    mentioning = local.questions_that_mention_it
    out.questions_executing = len(executing)
    out.questions_mentioning = len(mentioning)
    out.question_ids = list(dict.fromkeys(l.content_id for l in executing + mentioning))[:6]
    out.screenshots_at_risk = (score.screenshots_at_risk(out, census) if census else 0)

    # --- the facts that make the projection legible as a projection -------
    out.local_locations = len(local.locations)
    out.total_locations = len(dep.locations)
    out.also_in = also_in(dep, course)
    # Severity stays global as the HEADLINE: it is what `finding_state` stores, what
    # `diff_class` was computed against and what `precision_stats()` counts, so a chip
    # disagreeing with the digest and the DB would be its own lie. But `critical` above
    # "three prose mentions in this course" is also a lie, so both are carried and the
    # UI shows the local one whenever they differ.
    if {"model_shutdown_passed", "model_tier_restricted",
        "model_deprecation_declared"} & set(f.probe_signals or []):
        out.local_severity = score.retirement_severity(
            f.signal, local, out.blast_radius,
            out.questions_executing, out.graded_locations)
    else:
        out.local_severity = score.severity_for(f.signal, local, out.blast_radius)

    # --- the prose, re-derived because it names the course ----------------
    # `compose()` reads `severity` to pick the urgency line, so it has to see the LOCAL
    # one: rendering "in this course: high" beside "This sprint" (the critical wording)
    # left the reader to reconcile two of our own statements. The global severity is
    # put back immediately, because that is what the rest of the system keys on.
    from miw.analyse import notes
    out.recommendation = score.recommend(local, out)
    global_severity, out.severity = out.severity, out.local_severity
    try:
        notes.compose(local, out)
    finally:
        out.severity = global_severity
    # `compose` stamps note_source="template", which is the honest label: a note
    # refined by a model was refined against the GLOBAL numbers, so re-deriving it here
    # means it is no longer that model's text and must not wear its chip.
    return out


def project_all(findings: Iterable[Finding], deps_by_id: dict[str, Dependency],
                course: str, census: Optional[dict] = None) -> list[Finding]:
    """Every finding that touches `course`, projected onto it.

    A finding whose dependency is missing from the inventory (a later `extract` dropped
    it while the day's artifact still carries it) is passed through with its global
    numbers and flagged, never silently presented as local.
    """
    out = []
    for f in findings:
        dep = deps_by_id.get(f.dep_id)
        if dep is None:
            if course in (f.courses or []):
                stale = copy.deepcopy(f)
                stale.projection = "unavailable"
                out.append(stale)
            continue
        p = project_finding(f, dep, course, census)
        if p is not None:
            p.projection = "ok"
            out.append(p)
    return out
