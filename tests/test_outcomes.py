"""A promise, checked against the holes a gap run found.

Three inputs, three questions. The inventory says what the curriculum USES; it is built
from course content, so anything untaught has no row. `registry/topics.yaml` says what
EXISTS in an area, which is why `gaps` reads an input that does not come from the
courses. Neither records a promise made to a student, so `registry/outcomes.yaml` is the
third — and like the second, it is human-owned and reports nothing until somebody writes
in it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.analyse.gaps import Area, Source
from miw.analyse.outcomes import (Outcome, OutcomesUnreadable, check_outcomes,
                                  load_outcomes)
from miw.schema import Claim, Finding
from miw.trust import ClaimKind, Subject


def _area(area_id="tool-use", title="Tool use"):
    return Area(area_id=area_id, title=title, scope_terms=("tool",),
                sources=(Source(url="https://vendor-a.test/docs", subject="Vendor A",
                                official_domains=("vendor-a.test",)),
                         Source(url="https://vendor-b.test/docs", subject="Vendor B",
                                official_domains=("vendor-b.test",))))


def _gap(name, dep_id, courses, url="https://vendor-a.test/docs", subject="Vendor A",
         domains=("vendor-a.test",)):
    c = Claim.build(kind=ClaimKind.EXISTENCE, statement=f"{subject} documents {name}",
                    source_url=url,
                    quote=f"{name} is documented on this page as part of the area.",
                    subject=Subject(name=subject, official_domains=domains))
    return Finding(dep_id=dep_id, canonical_name=name, signal="S11",
                   signal_label="Curriculum topic gap", kind_of_signal="opportunity",
                   severity="low", courses=list(courses), claims=[c])


def test_a_promise_resting_on_an_area_with_holes_is_a_finding():
    area = _area()
    outcome = Outcome(outcome_id="call-tools", course="C1",
                      statement="Call external tools from an LLM", areas=["tool-use"])
    gaps = [_gap("Parallel function calling", "topic:tool-use:parallel", ["C1"]),
            _gap("Compositional function calling", "topic:tool-use:comp", ["C1"])]
    rows = [{"dep_id": "topic:tool-use:parallel", "area_id": "tool-use"},
            {"dep_id": "topic:tool-use:comp", "area_id": "tool-use"}]

    rep = check_outcomes([outcome], [area], gaps, rows)
    assert rep.stats.findings == 1
    f = rep.findings[0]
    assert f.signal == "S14" and f.courses == ["C1"]
    # It names the promise AND the documented parts nothing covers — that is the whole
    # value over S11, which says a topic is missing and not why it matters.
    assert "Call external tools from an LLM" in f.summary
    assert "Parallel function calling" in f.what_to_act
    assert "Compositional function calling" in f.what_to_act
    # ...and it is cited, because "this is a documented part of the area" is a claim
    # about the world. `main.py verify` refuses a finding that asserts it uncited.
    assert f.claims and all(c.substantiating for c in f.claims)


def test_MUST_NOT_fire_for_an_area_whose_gaps_land_in_a_different_course():
    """A promise belongs to one course. "Six sessions touch retrieval" says nothing
    about whether the course that PROMISED retrieval is one of them."""
    area = _area()
    outcome = Outcome(outcome_id="call-tools", course="C1",
                      statement="Call external tools", areas=["tool-use"])
    gaps = [_gap("Parallel function calling", "topic:tool-use:parallel", ["C2"])]
    rows = [{"dep_id": "topic:tool-use:parallel", "area_id": "tool-use"}]

    rep = check_outcomes([outcome], [area], gaps, rows)
    assert rep.findings == []
    assert rep.stats.served == 1


def test_MUST_NOT_invent_a_promise_nobody_declared():
    """An empty file reports nothing, the same way an area with one source reports
    nothing and says so. A promise the team has not written down is not one this system
    gets to guess at."""
    assert check_outcomes([], [_area()], [], []).findings == []


def test_an_outcome_naming_an_area_that_does_not_exist_is_reported_not_ignored():
    outcome = Outcome(outcome_id="x", course="C1", statement="Do a thing",
                      areas=["no-such-area"])
    rep = check_outcomes([outcome], [_area()], [], [])
    assert rep.findings == []
    assert any("no-such-area" in p for p in rep.stats.unknown_areas)


def test_a_malformed_outcomes_file_is_reported_not_a_crash(tmp_path):
    """Hand-edited, so a typo is a matter of time — and it must not take down the stage
    that reads it."""
    bad = tmp_path / "outcomes.yaml"
    bad.write_text("outcomes: [\n  - broken: yaml: here\n")
    with pytest.raises(OutcomesUnreadable):
        load_outcomes(bad)

    missing = tmp_path / "absent.yaml"
    assert load_outcomes(missing) == []        # absent is a valid state

    empty = tmp_path / "empty.yaml"
    empty.write_text("outcomes: []\n")
    assert load_outcomes(empty) == []


def test_an_outcome_needs_a_course_and_a_statement(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text("outcomes:\n"
                 "  - outcome_id: a\n    course: C1\n    statement: Real one\n"
                 "    areas: [tool-use]\n"
                 "  - outcome_id: b\n    course: C1\n"          # no statement
                 "  - outcome_id: c\n    statement: No course\n")
    got = load_outcomes(p)
    assert [o.outcome_id for o in got] == ["a"]
