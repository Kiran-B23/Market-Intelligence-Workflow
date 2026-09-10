"""A retired model id is only an outage if the curriculum calls it.

All four of the first real S7 findings were true positives, and three were described
wrongly: every one came out `critical`, every one said "fails at call time, so every
example in the session stops working", and every one said "This sprint". Only one of the
four had anything that executes the id. These tests pin the distinction.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.analyse.notes import compose
from miw.analyse.score import findings_for
from miw.schema import Dependency, Location, ProbeResult

RETIRED = {
    "rule_id": "groq:llama-3.3-70b-versatile",
    "title": "llama-3.3-70b-versatile is deprecated on Groq",
    "description": "llama-3.3-70b-versatile | 08/16/26 | openai/gpt-oss-120b",
    "doc_url": "https://console.groq.com/docs/deprecations",
    "node_types": ["llama-3.3-70b-versatile"],
    "replacement_ids": ["openai/gpt-oss-120b"],
    "shutdown_date": "08/16/26",
}


def _loc(object_type, n=1, course="Intro to Gen AI"):
    return [Location(course=course, topic_name="t", unit_id=f"u{i}", unit_name="n",
                     content_id=f"q{i}", field_path="f", evidence_source="model_id",
                     object_type=object_type, session_no=6) for i in range(n)]


def _dep(locs):
    return Dependency(kind="model", canonical_name="llama-3.3-70b-versatile",
                      official_domains=["ai.meta.com"], locations=locs)


def _probe(dep):
    r = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                    status="broken", provider="Groq",
                    provider_domains=["groq.com", "console.groq.com"],
                    evidence_url="https://console.groq.com/docs/deprecations",
                    declared_changes=[RETIRED])
    r.flag("model_shutdown_passed")
    return r


def _one(dep):
    fs = [f for f in findings_for(dep, _probe(dep), None) if f.signal == "S7"]
    assert len(fs) == 1, f"expected exactly one S7, got {[f.signal for f in fs]}"
    compose(dep, fs[0])
    return fs[0]


# ------------------------------------------------------- the severity ladder

def test_an_executed_id_is_critical():
    """5 coding questions pass this string to an API; they fail on the next run."""
    f = _one(_dep(_loc("CODING_QUESTIONS", 5)))
    assert f.questions_executing == 5
    assert f.severity == "critical"


def test_a_graded_but_unexecuted_id_is_high_not_critical():
    """16 MCQs naming a dead model is wrong, but nothing fails at call time."""
    f = _one(_dep(_loc("OBJECTIVE_QUESTIONS", 16)))
    assert f.questions_executing == 0 and f.graded_locations == 16
    assert f.severity == "high"


def test_a_prose_only_mention_is_not_a_fire():
    f = _one(_dep(_loc("LEARNING_RESOURCE", 3)))
    assert f.questions_executing == 0 and f.graded_locations == 0
    assert f.severity in ("medium", "low"), f.severity


def test_the_one_urgent_finding_is_not_down_ranked_by_the_fix():
    """The regression that would matter most: quieting the real outage too."""
    mixed = _dep(_loc("CODING_QUESTIONS", 5) + _loc("OBJECTIVE_QUESTIONS", 10)
                 + _loc("LEARNING_RESOURCE", 2))
    assert _one(mixed).severity == "critical"


def test_a_model_provider_states_no_severity_of_its_own():
    """n8n declares a severity per breaking-change rule and scoring honours it. A model
    provider declares none, so anything there would be our inference wearing the
    vendor's authority - and it silently overrode the ladder above."""
    from miw.probe.models import probe_model_dependency  # noqa: F401  (import guard)
    assert "severity" not in RETIRED
    f = _one(_dep(_loc("LEARNING_RESOURCE", 3)))
    assert f.severity != "critical", "a fabricated vendor severity is overriding the ladder"


# ------------------------------------------------- the "why it matters" line

def test_the_why_line_claims_call_time_failure_only_when_something_executes():
    executed = _one(_dep(_loc("CODING_QUESTIONS", 5)))
    assert "fails at call time" in executed.why_to_act
    assert "5 graded item(s) that run it" in executed.why_to_act

    graded = _one(_dep(_loc("OBJECTIVE_QUESTIONS", 16)))
    assert "fails at call time" not in graded.why_to_act
    assert "16 graded question(s)" in graded.why_to_act

    prose = _one(_dep(_loc("LEARNING_RESOURCE", 3)))
    assert "fails at call time" not in prose.why_to_act
    assert "stops working" not in prose.why_to_act


def test_urgency_follows_severity_and_still_names_the_earliest_session():
    executed = _one(_dep(_loc("CODING_QUESTIONS", 5)))
    assert executed.when_to_act.startswith("This sprint")
    assert "session 6" in executed.when_to_act

    prose = _one(_dep(_loc("LEARNING_RESOURCE", 3)))
    assert not prose.when_to_act.startswith("This sprint")
    assert "session 6" in prose.when_to_act, "the useful location must survive"


def test_a_broken_why_resolver_never_loses_the_digest():
    """The deterministic note is the product; a bad resolver must degrade, not raise."""
    from miw.analyse import notes
    original = notes.WHY["S7"]
    notes.WHY["S7"] = lambda f: 1 / 0
    try:
        f = _one(_dep(_loc("CODING_QUESTIONS", 5)))
        assert f.why_to_act and "Scope:" in f.why_to_act
    finally:
        notes.WHY["S7"] = original
