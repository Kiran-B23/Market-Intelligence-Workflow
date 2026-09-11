"""A finding shown on one course's page must describe that course, not the whole set.

Findings are identified by (dependency, signal) with no course in them, and 133 of 464
dependencies are referenced by more than one course. So a course page shows a
*projection*, and the numbers have to be recomputed. Copying them is not a rounding
error: on live data `llama-3.3-70b-versatile` carries a global blast radius of 65 while
its Intro to Gen AI share is 12.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.analyse import score
from miw.analyse.project import (also_in, course_dependency, project_all,
                                 project_finding)
from miw.analyse.score import blast_radius, findings_for
from miw.schema import Dependency, Location, ProbeResult

INTRO, APPS, FIN = "Intro to Gen AI", "Building LLM Applications", "AI for Finance"


def _loc(course, object_type, n, session=6, start=0):
    return [Location(course=course, topic_name="t", unit_id=f"u{start+i}",
                     unit_name="n", content_id=f"{course[:3]}-q{start+i}",
                     field_path="f", evidence_source="model_id",
                     object_type=object_type, session_no=session)
            for i in range(n)]


def _dep():
    """Shaped like the real llama-3.3-70b-versatile: executions in APPS, prose in INTRO."""
    return Dependency(
        kind="model", canonical_name="llama-3.3-70b-versatile",
        official_domains=["ai.meta.com"],
        locations=(_loc(APPS, "CODING_QUESTIONS", 5, session=12)
                   + _loc(APPS, "OBJECTIVE_QUESTIONS", 10, session=11, start=5)
                   + _loc(INTRO, "LEARNING_RESOURCE", 3, session=6)))


def _probe(dep):
    r = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                    status="broken", provider="Groq",
                    provider_domains=["groq.com"],
                    evidence_url="https://console.groq.com/docs/deprecations")
    r.flag("model_shutdown_passed")
    return r


def _finding(dep):
    return [f for f in findings_for(dep, _probe(dep), None) if f.signal == "S7"][0]


# --------------------------------------------------------------- the numbers

def test_counts_are_recomputed_not_copied():
    dep = _dep()
    f = _finding(dep)
    assert f.blast_radius == blast_radius(dep) and f.graded_locations == 15

    intro = project_finding(f, dep, INTRO)
    assert intro.local_locations == 3 and intro.total_locations == 18
    assert intro.graded_locations == 0, "INTRO holds only reading material"
    assert intro.questions_executing == 0
    assert intro.blast_radius < f.blast_radius


def test_the_projection_does_not_read_the_truncated_finding_locations():
    """`score.py` stores dep.locations[:12]; APPS really has 15. Projecting off the
    finding would understate the busiest course by a third."""
    dep = _dep()
    f = _finding(dep)
    assert len(f.locations) == 12 < len(dep.locations) == 18
    apps = project_finding(f, dep, APPS)
    assert apps.local_locations == 15, "must join back to the inventory dependency"


def test_per_course_blast_radii_sum_to_the_global_one():
    """One assertion catching double-counting, truncation and apportioning at once.
    Holds because the weights are additive over locations and locations partition
    cleanly by course."""
    dep = _dep()
    courses = sorted({l.course for l in dep.locations})
    parts = [blast_radius(course_dependency(dep, c)) for c in courses]
    assert sum(parts) == blast_radius(dep)


def test_a_course_that_does_not_reference_it_gets_nothing():
    dep = _dep()
    assert project_finding(_finding(dep), dep, "Some Other Course") is None


# ------------------------------------------------------------- the prose

def test_the_note_is_rederived_because_it_names_the_course():
    """`recommend()` interpolates locations[0].course and `compose()` interpolates
    len(courses), so a carried-over triad would name the wrong course."""
    dep = _dep()
    f = _finding(dep)
    intro = project_finding(f, dep, INTRO)
    assert "session 6" in intro.when_to_act, "INTRO's own earliest session"
    assert "1 course(s)" in intro.why_to_act
    apps = project_finding(f, dep, APPS)
    # APPS holds sessions 11 and 12; the note names the EARLIEST, which is 11.
    assert "session 11" in apps.when_to_act
    assert APPS in apps.when_to_act and INTRO not in apps.when_to_act


def test_a_model_refined_note_is_restamped_as_template():
    """It was refined against the GLOBAL numbers, so it is no longer that model's
    text and must not keep its chip."""
    dep = _dep()
    f = _finding(dep)
    f.note_source, f.note_provider = "llm", "anthropic"
    f.what_to_act = "Model-written text about 15 graded items."
    out = project_finding(f, dep, INTRO)
    assert out.note_source == "template" and out.note_provider == ""
    assert out.what_to_act != f.what_to_act


# ---------------------------------------------------------- severity honesty

def test_local_severity_is_never_more_severe_than_the_global_one():
    """A page covering fewer locations cannot be worse than the whole picture. An
    earlier version computed this with a different rule and produced exactly that."""
    dep = _dep()
    f = _finding(dep)
    order = score.SEVERITY_ORDER
    for course in (INTRO, APPS):
        p = project_finding(f, dep, course)
        assert order.index(p.local_severity) <= order.index(p.severity), course


def test_the_executing_course_stays_critical_and_the_prose_course_does_not():
    dep = _dep()
    f = _finding(dep)
    assert f.severity == "critical"
    assert project_finding(f, dep, APPS).local_severity == "critical"
    assert project_finding(f, dep, INTRO).local_severity != "critical"


def test_the_global_severity_is_carried_across_unchanged():
    """It is what finding_state stores and what precision_stats counts; a page-local
    disagreement with the database would be its own lie."""
    dep = _dep()
    f = _finding(dep)
    for course in (INTRO, APPS):
        assert project_finding(f, dep, course).severity == f.severity


def test_identity_never_moves():
    """Per-course finding ids would multiply finding_state rows for the 133 shared
    dependencies and ask a reviewer to reject the same event up to four times."""
    dep = _dep()
    f = _finding(dep)
    assert all(project_finding(f, dep, c).finding_id == f.finding_id
               for c in (INTRO, APPS))


# -------------------------------------------------------- not siloed

def test_a_projected_finding_says_where_else_it_reaches():
    dep = _dep()
    intro = project_finding(_finding(dep), dep, INTRO)
    others = {a["course"]: a for a in intro.also_in}
    assert set(others) == {APPS}
    assert others[APPS]["locations"] == 15
    assert others[APPS]["slug"] == "llm_applications"
    assert INTRO not in others, "a course must not list itself"


def test_vendor_facts_survive_the_projection():
    """A model Groq retired is retired in every course too."""
    dep = _dep()
    f = _finding(dep)
    p = project_finding(f, dep, INTRO)
    assert p.signal == f.signal and p.summary == f.summary
    assert p.probe_signals == f.probe_signals
    assert p.diff_class == f.diff_class


# ------------------------------------------------------------- project_all

def test_project_all_selects_only_the_findings_that_touch_the_course():
    dep = _dep()
    f = _finding(dep)
    by_id = {dep.dep_id: dep}
    assert len(project_all([f], by_id, INTRO)) == 1
    assert project_all([f], by_id, "Some Other Course") == []


def test_a_dependency_dropped_from_the_inventory_is_flagged_not_faked():
    """A later `extract` can drop a dep while the day's artifact still carries it.
    Global numbers must never be quietly presented as this course's."""
    dep = _dep()
    f = _finding(dep)
    f.courses = [INTRO, APPS]
    out = project_all([f], {}, INTRO)
    assert len(out) == 1 and out[0].projection == "unavailable"
    assert out[0].blast_radius == f.blast_radius, "unchanged, and labelled as such"


def test_projection_fields_survive_serialisation():
    """`to_jsonable` uses dataclasses.asdict, so an undeclared attribute would be
    silently dropped and the UI would never see the projection at all."""
    from miw.schema import to_jsonable
    dep = _dep()
    d = to_jsonable(project_finding(_finding(dep), dep, INTRO))
    for key in ("projection", "local_locations", "total_locations", "local_severity",
                "also_in"):
        assert key in d, f"{key} lost on serialisation"


def test_the_urgency_line_agrees_with_the_severity_shown_for_this_course():
    """Rendering "in this course: high" beside "This sprint" (the critical wording)
    leaves the reader reconciling two of our own statements. `compose()` reads
    `severity`, so it must see the LOCAL one — while the field itself stays global,
    because the store and the precision metric key on that."""
    dep = _dep()
    f = _finding(dep)

    # INTRO holds reading material only in this fixture: nothing executed, nothing
    # graded, so the ladder lands below `high`.
    intro = project_finding(f, dep, INTRO)
    assert intro.local_severity == "medium"
    assert intro.when_to_act.startswith("Next curriculum cycle")
    assert not intro.when_to_act.startswith("This sprint")
    assert intro.severity == "critical", "the stored severity is still the global one"

    apps = project_finding(f, dep, APPS)
    assert apps.local_severity == "critical"
    assert apps.when_to_act.startswith("This sprint")
