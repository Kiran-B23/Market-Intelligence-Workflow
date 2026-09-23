"""The rule that stops the same mistake being made a seventh time.

Six defects in this system have had one shape: **the finding claimed more than the
observation behind it justified.**

    Composio S1   the dependency's whole footprint, for one dead URL
    OpenAI   S5   21 reading materials that say "OpenAI", for a docs reorganisation
    lmOpenAi S9   a glossary row scored as a wired node
    Nango    S10  `verified` - meaning "this company exists" - rendered as
                  "candidate replacement"
    "Coding Practice"  the unit's NAME printed as the artifact's TYPE
    n8n agent     a rule about versions below 2, raised against typeVersion 2.2

Each was found by a reader noticing, patched at its own site, and the next one arrived.
They kept arriving because the reach was declared against the finding SIGNAL, and a
finding signal is a bucket that several different observations pour into:

    S4  registry_missing | registry_deprecated | no_release_in_2y   a PACKAGE is dead
        sunset_language_about_subject                               the VENDOR says so
    S3  free_tier_language_lost | pricing_restriction_language      money changed
        pricing_page_changed                                        a PAGE was rewritten

A reach declared on the bucket is therefore too wide for some of its members, and too
wide is the failure that ships: it reads as a bigger finding, no test fails, and the
reviewer is sent to places where nothing happened.

So the reach is keyed on the observation, and this file holds it there.
"""
import pytest

from miw.analyse.score import (EVIDENCE_REACH, PROBE_TO_SIGNAL, SIGNAL_EVIDENCE,
                               evidence_reach)


def test_every_observation_declares_what_it_reaches():
    """A new probe signal must say what it justifies. Without this it inherits the
    per-signal fallback silently, which is how the bucket-level reach kept coming back.
    """
    undeclared = sorted(set(PROBE_TO_SIGNAL) - set(EVIDENCE_REACH))
    assert not undeclared, f"probe signals with no declared reach: {undeclared}"


def test_nothing_is_declared_that_is_never_produced():
    """A reach for an observation nothing emits is a rule nobody can check."""
    orphan = sorted(set(EVIDENCE_REACH) - set(PROBE_TO_SIGNAL))
    assert not orphan, f"declared but never produced: {orphan}"


def test_reaching_everywhere_is_rare_and_every_case_is_named():
    """`None` means "everywhere the dependency is taught". It is for events that change
    what the course should TEACH, not events about one of its pages, and it has to be
    argued for one observation at a time rather than inherited."""
    everywhere = sorted(k for k, v in EVIDENCE_REACH.items() if v is None)
    assert everywhere == ["deprecation_notice_added", "free_tier_language_lost",
                          "model_price_changed", "model_rate_limit_changed",
                          "pricing_restriction_language",
                          "sunset_language_about_subject",
                          "taught_field_deprecated"], everywhere
    # The argument for the two newest: what a model COSTS and how much of it a student
    # may use are facts about the model, not about one page that mentions it. A price
    # that doubles or a quota that halves changes the instruction wherever the session
    # tells a student to call it — which is the same reason `free_tier_language_lost`
    # sits here, and it has sat here since before either of these existed.
    # `taught_field_deprecated` is the one entry here that is not a widening. It abstains,
    # because the question this table asks — which EVIDENCE KINDS does an observation
    # reach — is the wrong question for a field. `evidence_source` says how the
    # dependency was found in a record, not what the record contains, and Murf's four
    # records writing `multiNativeLocale` carry link:a_href, link:markdown and
    # prose_name. `scope_locations` narrows S13 by `Dependency.taught_param_at` instead,
    # which is measured rather than inferred and is strictly narrower than any rule
    # available here.
    # The argument for the newest member, made here because this test exists to force
    # one: `deprecation_notice_added` fires when the vendor's own pages gained a
    # sentence saying this dependency is ending. That is a fact about the DEPENDENCY,
    # not about one of its URLs - the same category as the standing
    # `sunset_language_about_subject` beside it - so every place the course teaches it
    # is affected, prose included. A reader who is told the tool is being retired needs
    # the paragraph that recommends it changed, not just the link.


def test_a_page_event_never_reaches_past_the_links_to_that_page():
    """Every one of these is "something happened to a URL", and the places that link to
    it are the whole of what is affected."""
    for observed in ("url_gone", "domain_parked", "redirected_off_path",
                     "access_wall_language", "page_text_changed",
                     "pricing_page_changed"):
        reach = EVIDENCE_REACH[observed]
        assert reach is not None, observed
        assert all(s.startswith("link:") for s in reach), (observed, reach)


def test_a_registry_event_never_reaches_a_prose_mention():
    """`registry_missing` is a package that left PyPI. It reaches where the package is
    installed, imported, pinned or declared - not every paragraph that names it."""
    for observed in ("registry_missing", "registry_deprecated", "no_release_in_2y",
                     "new_release", "major_behind_taught_pin"):
        assert "prose_name" not in (EVIDENCE_REACH[observed] or ()), observed


def test_the_union_of_several_observations_is_their_union():
    """A finding carries every observation that produced it."""
    both = evidence_reach("S6", ["major_behind_taught_pin", "url_gone"])
    assert "install_command" in both and "link:a_href" in both


def test_one_observation_reaching_everywhere_wins_the_union():
    assert evidence_reach("S3", ["pricing_page_changed",
                                 "free_tier_language_lost"]) is None


def test_a_finding_with_no_observation_falls_back_to_the_signal():
    """S11 and S12 are produced by `gaps.py` and `newer.py`, not by the probe."""
    assert evidence_reach("S11", []) is SIGNAL_EVIDENCE["S11"]
    assert evidence_reach("S1", []) == SIGNAL_EVIDENCE["S1"]


def test_an_unknown_observation_does_not_silently_widen_anything():
    """`alternatives_not_surfaced:2` and `n8n_rule:x` are bookkeeping, not evidence."""
    assert evidence_reach("S1", ["url_gone", "alternatives_not_surfaced:2"]) \
        == EVIDENCE_REACH["url_gone"]


# ------------------------------------------------- the sentence and the list agree

def test_a_finding_may_not_claim_a_number_its_locations_do_not_support():
    """`recommend()`'s S1 line once read "the 6 place(s) Composio is linked" directly
    above twelve rows: the sentence counted `dep.link_locations` and the list held
    `dep.locations[:12]`. Both read `f.locations` now, and this refuses to let them
    part again."""
    from miw.analyse.score import _assert_claim_fits
    from miw.schema import Finding, Location

    f = Finding(dep_id="d", canonical_name="X", signal="S1",
                signal_label="Dead / moved URL", severity="high")
    f.locations = [Location(course="C", topic_name="T", unit_id="u", unit_name="U",
                            content_id="c", field_path="f",
                            evidence_source="link:a_href")]
    f.recommendation = "Repoint the dead link in the 1 place(s) X is linked."
    _assert_claim_fits(f)                       # agrees: fine

    f.recommendation = "Repoint the dead link in the 6 place(s) X is linked."
    with pytest.raises(AssertionError, match="claims 6"):
        _assert_claim_fits(f)


def test_the_live_artifact_obeys_the_rule():
    """Not a unit test: the real findings, re-derived. A finding whose stored locations
    exceed what its own observations justify is the bug, wherever it came from."""
    import json
    import pathlib

    from miw.analyse.score import evidence_reach

    arts = sorted((pathlib.Path(__file__).resolve().parents[1] / "out")
                  .glob("findings_*.json"))
    if not arts:
        pytest.skip("no findings artifact in this checkout")
    bad = []
    for f in json.loads(arts[-1].read_text()).get("findings") or []:
        reach = evidence_reach(f["signal"], f.get("probe_signals") or [])
        if reach is None:
            continue
        for l in f.get("locations") or []:
            if l["evidence_source"] not in reach and not (
                    f["signal"] == "S5" and l["evidence_source"] in ("prose_name",
                                                                     "title")):
                bad.append((f["canonical_name"], f["signal"], l["evidence_source"]))
    assert not bad, bad[:10]
