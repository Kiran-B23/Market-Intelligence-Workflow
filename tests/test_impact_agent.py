"""The impact agent: verified evidence -> per-course findings, and a real HITL pause.

The interrupt test is the one that matters most. The prior Curriculum Gap Analyzer's
architecture document called `interrupt()` "a critical LangGraph feature" while both of
its graphs compiled with no checkpointer, so nothing could actually suspend. These tests
assert that this graph does suspend, that the state survives, and that resuming
continues from the gate instead of re-running the work.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from miw.agents import impact_agent
from miw.schema import Dependency, Location

INTRO, APPS = "Intro to Gen AI", "Building LLM Applications"


@pytest.fixture(autouse=True)
def _no_real_artifact(tmp_path, monkeypatch):
    """The review gate writes an artifact; tests must never write the real one.

    Without this the suite left four fixture runs named `gizmo-1` in
    `out/agent_findings_<date>.json`, and they duly rendered in the UI.
    """
    from miw.agents.nodes import review
    monkeypatch.setattr(review, "OUT", tmp_path)


def _dep() -> Dependency:
    """Shared across two courses, with a graded item in each."""
    locs = [
        Location(course=INTRO, topic_name="T", unit_id="u1", unit_name="Coding Practice",
                 content_id="q1", field_path="[0]", evidence_source="model_id",
                 object_type="OBJECTIVE_QUESTIONS", session_no=10),
        Location(course=INTRO, topic_name="T", unit_id="u2", unit_name="Module Quiz",
                 content_id="q2", field_path="[1]", evidence_source="model_id",
                 object_type="OBJECTIVE_QUESTIONS", session_no=16),
        Location(course=APPS, topic_name="T", unit_id="u3", unit_name="Practice",
                 content_id="q3", field_path="[2]", evidence_source="solution_import",
                 object_type="CODING_QUESTIONS", session_no=4),
        Location(course=APPS, topic_name="T", unit_id="u4", unit_name="Reading",
                 content_id="", field_path="[3]", evidence_source="prose_name",
                 object_type="LEARNING_RESOURCE", session_no=5),
    ]
    return Dependency(kind="model", canonical_name="gizmo-1",
                      official_domains=["gizmo.example"], locations=locs)


CLAIM = {"source_url": "https://gizmo.example/deprecations",
         "quote": "gizmo-1 is deprecated and will be shut down",
         "tier": "AUTHORITATIVE", "kind": "deprecation"}


# ------------------------------------------------------- findings, per course

def test_one_finding_per_affected_course_not_one_global_row():
    """129 of 460 dependencies are shared; a global number overstates each course."""
    app = impact_agent.compile_impact_graph(checkpointer=False)
    st, _ = impact_agent.run_impact_agent(
        _dep(), ["model_shutdown_passed"], [CLAIM], graph=app)
    courses = [f["course"] for f in st["findings"]]
    assert courses == [APPS, INTRO], "one per course, sorted"
    per = {f["course"]: f["blast_radius"] for f in st["findings"]}
    assert per[INTRO] > 0 and per[APPS] > 0
    assert st["blast_radius"] >= max(per.values()), "global is at least any single course"


def test_the_signal_class_comes_from_the_deterministic_mapping():
    app = impact_agent.compile_impact_graph(checkpointer=False)
    st, _ = impact_agent.run_impact_agent(
        _dep(), ["model_shutdown_passed"], [CLAIM], graph=app)
    assert st["signal"] == "S7", "a retired model id is S7, by PROBE_TO_SIGNAL"
    assert st["severity"] in ("critical", "high", "medium", "low", "info")


def test_affected_artifact_kinds_are_counted_from_locations_not_asked_of_a_model():
    """This is the prior system's assessment_gap / quiz_gap idea, taken from data."""
    app = impact_agent.compile_impact_graph(checkpointer=False)
    st, _ = impact_agent.run_impact_agent(
        _dep(), ["model_shutdown_passed"], [CLAIM], graph=app)
    assert st["artifacts"] == {"OBJECTIVE_QUESTIONS": 2, "CODING_QUESTIONS": 1,
                               "LEARNING_RESOURCE": 1}


def test_sessions_are_reported_per_course(): 
    app = impact_agent.compile_impact_graph(checkpointer=False)
    st, _ = impact_agent.run_impact_agent(
        _dep(), ["model_shutdown_passed"], [CLAIM], graph=app)
    by = {f["course"]: f["sessions"] for f in st["findings"]}
    assert by[INTRO] == [10, 16] and by[APPS] == [4, 5]


def test_an_unrecognised_probe_signal_produces_no_finding_rather_than_a_guess():
    app = impact_agent.compile_impact_graph(checkpointer=False)
    st, _ = impact_agent.run_impact_agent(_dep(), ["something_new"], [CLAIM], graph=app)
    assert st.get("signal") == ""
    assert not st.get("findings")


def test_no_model_is_consulted_anywhere_in_this_graph():
    """Impact is a join over data we already hold, so the cost must be zero."""
    app = impact_agent.compile_impact_graph(checkpointer=False)
    st, _ = impact_agent.run_impact_agent(
        _dep(), ["model_shutdown_passed"], [CLAIM], graph=app)
    assert st["llm_calls"] == 0


# ------------------------------------------------- the human gate actually pauses

def test_the_review_gate_suspends_the_run(): 
    """A checkpointer is what makes `interrupt` able to suspend at all."""
    app = impact_agent.compile_impact_graph(checkpointer=MemorySaver())
    st, app = impact_agent.run_impact_agent(
        _dep(), ["model_shutdown_passed"], [CLAIM], graph=app, thread_id="t-1")
    cfg = {"configurable": {"thread_id": "t-1"}}
    snap = app.get_state(cfg)
    assert snap.next, "the run is suspended, not finished"
    assert "review" not in st or not st.get("review")
    assert st["findings"], "the work before the gate did complete"


def test_resuming_continues_from_the_gate_and_records_the_verdict():
    app = impact_agent.compile_impact_graph(checkpointer=MemorySaver())
    dep = _dep()
    _st, app = impact_agent.run_impact_agent(
        dep, ["model_shutdown_passed"], [CLAIM], graph=app, thread_id="t-2")
    cfg = {"configurable": {"thread_id": "t-2", "dep": dep}}
    final = app.invoke(Command(resume={"decision": "accepted"}), config=cfg)
    assert final["review"] == "accepted"
    assert not app.get_state(cfg).next, "and now it is finished"
    assert any("review: accepted" in t for t in final["trajectory"])


def test_what_the_reviewer_is_shown_includes_the_evidence():
    """A reviewer cannot accept a finding they cannot check."""
    app = impact_agent.compile_impact_graph(checkpointer=MemorySaver())
    dep = _dep()
    impact_agent.run_impact_agent(dep, ["model_shutdown_passed"], [CLAIM],
                                  graph=app, thread_id="t-3")
    snap = app.get_state({"configurable": {"thread_id": "t-3"}})
    payload = snap.tasks[0].interrupts[0].value
    assert payload["kind"] == "finding_review"
    assert payload["evidence"][0]["source_url"] == CLAIM["source_url"]
    assert payload["evidence"][0]["quote"] == CLAIM["quote"]
    assert payload["findings"] and payload["artifacts"]
