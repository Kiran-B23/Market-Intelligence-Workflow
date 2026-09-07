"""Run scoping. Getting this wrong either probes the whole inventory when someone
asked for one session, or silently probes nothing and reports all-clear."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from miw.schema import Dependency, Location
from miw.scope import Scope, _compact, course_map, parse_sessions


def loc(course, session, evidence="link:a_href"):
    return Location(course=course, topic_name="t", unit_id="u", unit_name="n",
                    content_id="", field_path="f", evidence_source=evidence,
                    object_type="LEARNING_RESOURCE", session_no=session)


def dep(name, locations, tier="critical", kind="service"):
    return Dependency(kind=kind, canonical_name=name, watch_tier=tier,
                      locations=locations)


def test_session_spec_parsing():
    assert parse_sessions("3") == {3}
    assert parse_sessions("3-5") == {3, 4, 5}
    assert parse_sessions("3,5,9-11") == {3, 5, 9, 10, 11}
    assert parse_sessions("7-5") == {5, 6, 7}, "a reversed range is still a range"
    assert parse_sessions("") == set()
    with pytest.raises(ValueError):
        parse_sessions("session twelve")


def test_compact_round_trips_through_the_parser():
    for spec in ({3}, {3, 4, 5}, {3, 5, 9, 10, 11}, {1, 2, 8}):
        assert parse_sessions(_compact(spec)) == spec


def test_scope_matches_on_locations_not_names():
    d = dep("Groq", [loc("Intro to Gen AI", 4), loc("AI for Finance", 11)])
    assert Scope(courses={"Intro to Gen AI"}).matches(d)
    assert Scope(courses={"PSE"}).matches(d) is False
    assert Scope(sessions={11}).matches(d)
    assert Scope(sessions={99}).matches(d) is False


def test_course_and_session_must_hold_in_the_same_location():
    """Session 4 of Gen AI must not match a dependency that is in Gen AI *and*
    separately in session 4 of a different course."""
    d = dep("X", [loc("Intro to Gen AI", 20), loc("PSE", 4)])
    assert Scope(courses={"Intro to Gen AI"}, sessions={4}).matches(d) is False
    assert Scope(courses={"PSE"}, sessions={4}).matches(d)


def test_session_without_course_spans_courses():
    d = dep("X", [loc("PSE", 4)])
    assert Scope(sessions={4}).matches(d)


def test_tier_and_kind_filters_are_independent_of_location():
    d = dep("X", [loc("PSE", 4)], tier="mention-only", kind="package")
    assert Scope(tiers={"critical"}).matches(d) is False
    assert Scope(tiers={"mention-only"}).matches(d)
    assert Scope(kinds={"service"}).matches(d) is False
    assert Scope(kinds={"package"}).matches(d)


def test_empty_scope_selects_everything():
    deps = [dep("a", [loc("PSE", 1)]), dep("b", [loc("PSE", 2)])]
    s = Scope()
    assert s.is_everything and len(s.select(deps)) == 2


def test_limit_caps_the_selection():
    deps = [dep(str(i), [loc("PSE", i)]) for i in range(10)]
    assert len(Scope(limit=3).select(deps)) == 3


def test_cli_args_round_trip():
    s = Scope(courses={"AI for Finance"}, sessions={4, 5, 6}, tiers={"critical"},
              kinds={"package"}, limit=7)
    args = s.to_cli_args()
    assert args.count("--course") == 1
    assert "4-6" in args, "sessions must be compacted for the CLI"
    assert Scope.from_dict(s.to_dict()).describe() == s.describe()


def test_course_map_lists_sessions_per_course():
    deps = [dep("a", [loc("PSE", 3), loc("PSE", 1)]),
            dep("b", [loc("Intro to Gen AI", 9)])]
    assert course_map(deps) == {"Intro to Gen AI": [9], "PSE": [1, 3]}
