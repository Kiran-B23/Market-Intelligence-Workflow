"""The one judgement in the system, and the wall around it.

"Does this replacement do what the session used the old tool for" cannot be settled by
any page, so it can never carry a citation. The model emits bounded per-factor
estimates; Python owns the arithmetic and the thresholds; the result is an
`AlternativeOpinion` that is rendered as opinion and can never become a `Claim`.

The two mistakes deliberately not copied from the prior project: substituting 0.5 for
an unparseable factor (which makes a real 0.5 indistinguishable from a crash), and
never clamping (so a model returning 1.5 inflates the total).
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.research import fit
from miw.research.fit import FIT_THRESHOLD, WEIGHTS, render, score
from miw.schema import Alternative, AlternativeOpinion, Claim, Dependency, Location
from miw.trust import ClaimKind

FULL = {"does_taught_job": 0.9, "free_student_path": 0.8, "signup_friction": 1.0,
        "maturity": 0.7, "coverage_of_steps": 0.6}


# ----------------------------------------------------- arithmetic in Python

def test_the_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_a_complete_set_scores_the_weighted_mean():
    got, cleaned, unknown = score(FULL)
    want = sum(FULL[k] * WEIGHTS[k] for k in WEIGHTS)
    assert abs(got - want) < 1e-3 and unknown == []
    assert cleaned == FULL


def test_out_of_range_values_are_clamped_not_trusted():
    got, cleaned, _ = score({**FULL, "does_taught_job": 1.5})
    assert cleaned["does_taught_job"] == 1.0
    assert got <= 1.0


def test_negative_values_are_clamped_too():
    _got, cleaned, _ = score({**FULL, "maturity": -3})
    assert cleaned["maturity"] == 0.0


def test_a_missing_factor_is_none_and_never_a_middle_default():
    """The prior project substitutes 0.5, which makes a real 0.5 and a parse failure
    identical in the database — and the urgency engine then stamps a deadline on a
    score that never existed."""
    _got, cleaned, unknown = score({**FULL, "maturity": "quite mature"})
    assert cleaned["maturity"] is None
    assert cleaned["maturity"] != 0.5
    assert "maturity" in unknown


def test_remaining_weights_are_renormalised_over_known_factors():
    partial = {"does_taught_job": 1.0, "free_student_path": 1.0, "signup_friction": 1.0}
    got, _c, unknown = score(partial)
    assert got == 1.0, "three perfect knowns must score 1.0, not 0.8"
    assert set(unknown) == {"maturity", "coverage_of_steps"}


def test_too_few_known_factors_means_no_score_at_all():
    got, _c, _u = score({"does_taught_job": 0.9, "maturity": 0.9})
    assert got is None, "two of five is a shrug with a number on it"


def test_the_central_factor_is_not_optional():
    """A score computed without `does_taught_job` measures everything except the
    question that was asked."""
    got, _c, _u = score({"free_student_path": 1.0, "signup_friction": 1.0,
                         "maturity": 1.0, "coverage_of_steps": 1.0})
    assert got is None


def test_junk_input_yields_no_score_and_does_not_raise():
    for junk in ({}, None, {"does_taught_job": "yes"}, {"does_taught_job": float("nan")}):
        assert score(junk or {})[0] is None


# ------------------------------------------------- it can never be evidence

def _alt_with_claim():
    alt = Alternative(name="ElevenLabs", homepage="https://elevenlabs.io")
    alt.claims.append(Claim.build(
        kind=ClaimKind.PRICING, statement="ElevenLabs publishes plan pricing",
        source_url="https://elevenlabs.io/pricing",
        quote="Free $0 (10,000 credits); Starter $6 (30,000 credits) per month.",
        subject=Dependency(kind="service", canonical_name="ElevenLabs",
                           official_domains=["elevenlabs.io"]).subject()))
    return alt


def test_an_opinion_has_no_fields_a_claim_could_be_built_from():
    op = AlternativeOpinion(fit_score=0.8, one_line="looks like a fit")
    assert not hasattr(op, "source_url") and not hasattr(op, "quote")


def test_the_opinion_never_enters_the_claims_list():
    dep = Dependency(kind="service", canonical_name="Murf.AI",
                     official_domains=["murf.ai"])
    alt = _alt_with_claim()
    from miw.llm import LLMResult
    reply = LLMResult(text='{"factors": {"does_taught_job": 0.9, '
                           '"free_student_path": 0.8, "signup_friction": 0.7}, '
                           '"unknown_factors": ["maturity"], '
                           '"one_line": "Same job, free tier documented."}',
                      ok=True, provider="claude_code", model="haiku")
    with patch("miw.llm.complete", return_value=reply):
        op = fit.assess(dep, alt)
    assert op is not None and op.assessed
    assert len(alt.claims) == 1, "assessing fit must not add a claim"
    assert op.provider == "claude_code" and op.assessed_at
    assert op.basis_urls == ["https://elevenlabs.io/pricing"], \
        "a reviewer must be able to check the opinion against the same text"


def test_fit_is_not_assessed_without_substantiating_evidence():
    """The model is only ever shown quotes we fetched ourselves."""
    dep = Dependency(kind="service", canonical_name="Murf.AI",
                     official_domains=["murf.ai"])
    bare = Alternative(name="Nothing", homepage="https://nothing.example")
    with patch("miw.llm.complete", side_effect=AssertionError("must not be called")):
        assert fit.assess(dep, bare) is None


def test_a_failed_model_call_yields_no_opinion_rather_than_a_bad_one():
    from miw.llm import LLMResult
    dep = Dependency(kind="service", canonical_name="Murf.AI",
                     official_domains=["murf.ai"])
    with patch("miw.llm.complete", return_value=LLMResult(ok=False, error="no provider")):
        assert fit.assess(dep, _alt_with_claim()) is None


# ------------------------------------------------------------- rendering

def test_the_rendered_line_says_it_is_not_evidence():
    op = AlternativeOpinion(fit_score=0.72, does_taught_job=True,
                            one_line="Same job, free tier documented.",
                            unknown_factors=["maturity"],
                            provider="claude_code", model="haiku",
                            assessed_at="2026-09-10T00:00:00+00:00")
    line = render(op)
    assert "not evidence" in line and "claude_code" in line
    assert "0.72" in line and "maturity" in line


def test_an_unassessed_opinion_says_so_rather_than_showing_a_number():
    line = render(AlternativeOpinion(provider="claude_code", model="haiku"))
    assert "not assessed" in line and "0.0" not in line


def test_the_threshold_decides_the_verdict_not_the_model():
    assert FIT_THRESHOLD > 0.5, "recommending a worse tool is the costly direction"
    below, _c, _u = score({**FULL, "does_taught_job": 0.1, "free_student_path": 0.1,
                           "signup_friction": 0.1, "maturity": 0.1,
                           "coverage_of_steps": 0.1})
    assert below < FIT_THRESHOLD


# ------------------------------------------------------ SEVERITY_OFFSETS

def test_a_due_date_is_derived_from_severity_not_guessed_separately():
    """`SEVERITY_OFFSETS` gives a sortable deadline. The prose in `when_to_act` stays
    the single thing a person reads, and both come from one mapping so the digest and
    the field cannot disagree about the same deadline."""
    from datetime import date, timedelta
    from miw.analyse.notes import SEVERITY_OFFSETS, compose
    from miw.analyse.score import findings_for
    from miw.schema import Location, ProbeResult

    assert SEVERITY_OFFSETS == {"critical": 7, "high": 30, "medium": 90,
                                "low": 180, "info": 365}

    dep = Dependency(kind="model", canonical_name="m", official_domains=["x.com"],
                     locations=[Location(course="c", topic_name="t", unit_id="u",
                                         unit_name="n", content_id="q",
                                         field_path="f", evidence_source="model_id",
                                         object_type="CODING_QUESTIONS",
                                         session_no=6)])
    pr = ProbeResult(dep_id=dep.dep_id, canonical_name="m", status="broken")
    pr.flag("model_shutdown_passed")
    f = findings_for(dep, pr, None)[0]
    compose(dep, f)
    assert f.severity == "critical"
    want = (date.today() + timedelta(days=7)).isoformat()
    assert f.due_by == want


def test_the_deadline_moves_with_the_severity():
    """It is derived, so the execution-aware severity ladder reaches it for free."""
    from datetime import date, timedelta
    from miw.analyse.notes import compose
    from miw.analyse.score import findings_for
    from miw.schema import Location, ProbeResult

    # prose-only: nothing executes it, so not critical, so a later deadline
    dep = Dependency(kind="model", canonical_name="m", official_domains=["x.com"],
                     locations=[Location(course="c", topic_name="t", unit_id="u",
                                         unit_name="n", content_id="", field_path="f",
                                         evidence_source="model_id",
                                         object_type="LEARNING_RESOURCE",
                                         session_no=6)])
    pr = ProbeResult(dep_id=dep.dep_id, canonical_name="m", status="broken")
    pr.flag("model_shutdown_passed")
    f = findings_for(dep, pr, None)[0]
    compose(dep, f)
    assert f.severity != "critical"
    assert f.due_by > (date.today() + timedelta(days=7)).isoformat()
