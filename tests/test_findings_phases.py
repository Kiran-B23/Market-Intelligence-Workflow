"""`findings_for` is a pipeline of named phases, and has to stay one.

It was a single 423-line function with fifty branches, and that shape had a cost that
showed up repeatedly rather than once: every defect of the "claimed more than the
evidence justified" family lived inside it, and they kept recurring because a special
case added to one part of the function could not see the special cases in the others.

Splitting it changed no behaviour — proved output-identical across all 68 findings the
live artifact produces — so what these tests protect is the separation itself, which is
the only thing the split bought.
"""
import ast
import pathlib

import pytest

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "miw" / "analyse" / "score.py").read_text()
TREE = ast.parse(SRC)
FUNCS = {n.name: n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)}

PHASES = ("_from_probe", "_from_research", "_reconcile", "_finalise")


def _size(name):
    n = FUNCS[name]
    return n.end_lineno - n.lineno + 1


def test_the_phases_exist():
    for name in PHASES + ("_ensure", "_attach_claim", "_probe_severity",
                          "_from_declared_changes"):
        assert name in FUNCS, name


def test_findings_for_reads_as_the_pipeline_it_is():
    """The entry point should say WHAT happens in order, not do it."""
    assert _size("findings_for") <= 40, _size("findings_for")
    body = ast.get_source_segment(SRC, FUNCS["findings_for"])
    for phase in PHASES:
        assert phase + "(" in body, phase


def test_the_phases_run_in_the_order_the_docstring_claims():
    """Each may only add to or narrow what the one before established, so the order is
    load-bearing: research consults `out` to decide whether an alternative belongs on
    an existing finding, and `_finalise` scopes what the first three produced."""
    body = ast.get_source_segment(SRC, FUNCS["findings_for"])
    at = [body.index(p + "(") for p in PHASES]
    assert at == sorted(at), dict(zip(PHASES, at))


@pytest.mark.parametrize("name", PHASES + ("_from_declared_changes",))
def test_no_phase_grows_back_into_the_thing_that_was_split(name):
    """A ratchet. None of these is small, and none needs to be - what matters is that
    the next special case lands in the phase it belongs to rather than extending one
    function past the point where it can be read."""
    assert _size(name) <= 120, f"{name} is {_size(name)} lines; split it further"


def test_the_claim_discipline_is_written_once():
    """Build, test that it substantiates its own kind, attach or COUNT the refusal.
    That was written out three times - auth change, retired field, sunset API version -
    and it is the discipline the whole system rests on."""
    src = ast.get_source_segment(SRC, FUNCS["_attach_claim"])
    assert "substantiating" in src and "authoritative_only" in src
    # The refusal is recorded, never swallowed: silence is what let an uncitable
    # claim stay invisible until `verify` shouted about it.
    assert "probe_signals" in src
    for caller in ("_from_probe",):
        body = ast.get_source_segment(SRC, FUNCS[caller])
        assert body.count("_attach_claim(") >= 3, caller


def test_the_entry_point_holds_no_policy():
    """No severity, no trust decision, no scoping in `findings_for` itself — those
    belong to a phase, where the next reader will look for them."""
    body = ast.get_source_segment(SRC, FUNCS["findings_for"])
    for leaked in ("severity", "Claim.build", "scope_locations", "recommend("):
        assert leaked not in body, leaked
