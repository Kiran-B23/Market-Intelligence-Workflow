"""The founding story, finally reaching a finding.

The curriculum taught `codetotutorial`. It died. `deepwiki` existed and nobody knew
until a student complained. A probe can see the 404; what was missing was the
replacement — and the vendor's own deprecation notice usually names it.

Two defects stopped that working, both keyless and both independent of any agent:

  1. `_relevant` keeps only sentences that NAME the subject, and vendors write the
     successor in a separate sentence that does not repeat it: "X is deprecated.
     Migrate to Y." So the successor sentence never reached the regex.
  2. The resulting `Alternative` was built with `claims=[]`, so `.verified` was False
     and `score.findings_for` filtered it out.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.analyse.score import findings_for
from miw.research import official
from miw.research.official import _sentences, _successor_leads
from miw.schema import Dependency, Location, ProbeResult
from miw.trust import ClaimKind, Tier

TWO_SENTENCE = ("CodeToTutorial has been deprecated and is no longer maintained. "
                "Please migrate to DeepWiki, which generates the same repository "
                "walkthroughs from any public GitHub project.")


def _page(body):
    return f"<html><body><p>{body}</p></body></html>"


def _dep(name="CodeToTutorial", domain="codetotutorial.com"):
    return Dependency(
        kind="service", canonical_name=name, official_domains=[domain],
        homepage=f"https://{domain}",
        locations=[Location(course="Intro to Gen AI", topic_name="t", unit_id="u",
                            unit_name="Understand any repo", content_id="q1",
                            field_path="f", evidence_source="markdown",
                            object_type="LEARNING_RESOURCE", session_no=4)])


def _gather(body, dep=None, kinds=(ClaimKind.DEPRECATION,)):
    dep = dep or _dep()
    class F:
        ok, final_url, error, status = True, "", "", 200
        def __init__(self, url):
            self.url, self.body = url, _page(body)
    with patch.object(official, "fetch", lambda url, **k: F(url)):
        return dep, official.gather(dep, kinds=kinds)


# ------------------------------------------------- the successor scanner

def _leads(text):
    """Drive the scanner the way `gather` does: prose-filtered quotes, raw text."""
    quotes = [s for s in _sentences(text) if "CodeToTutorial" in s]
    return _successor_leads(text, quotes, ("CodeToTutorial",))


def test_a_successor_named_in_the_next_sentence_is_found():
    """The whole bug: the sentence naming the replacement does not name the subject."""
    assert [n for n, _q in _leads(TWO_SENTENCE)] == ["DeepWiki"]


def test_the_shortest_form_of_the_notice_is_not_lost_to_the_prose_filter():
    """`_is_prose("Please migrate to DeepWiki.")` is False - 2 of 4 tokens are
    capitalised, over the 0.45 cap - so the commonest wording was silently skipped."""
    short = ("CodeToTutorial has been deprecated and is no longer maintained. "
             "Please migrate to DeepWiki.")
    assert [n for n, _q in _leads(short)] == ["DeepWiki"]


def test_a_sentence_initial_cue_is_matched():
    """The cue was case-sensitive, so every sentence-initial form missed."""
    for text in (
        "CodeToTutorial is deprecated and will be removed soon. Superseded by DeepWiki.",
        "CodeToTutorial is deprecated and will be removed soon. Migrate to DeepWiki.",
        "CodeToTutorial is deprecated and no longer maintained. We recommend DeepWiki.",
    ):
        assert [n for n, _q in _leads(text)] == ["DeepWiki"], text


def test_a_generic_protocol_is_never_nominated():
    """"...is retired, please use HTTPS for all requests" nominated `HTTPS` - and once
    a nomination carries a citation, that arrives AUTHORITATIVE and looks verified."""
    text = ("The legacy CodeToTutorial endpoint is deprecated and retired. "
            "Please use HTTPS for all requests.")
    assert _leads(text) == []


def test_a_tool_is_never_nominated_as_its_own_replacement():
    text = ("CodeToTutorial is deprecated and unmaintained. "
            "Please migrate to CodeToTutorial Cloud.")
    assert _leads(text) == []


def test_a_trailing_period_is_not_part_of_the_name():
    """`[\\w.+-]` admits dots for `Node.js`, so it also swallowed the full stop."""
    text = "CodeToTutorial is deprecated and unmaintained. Please migrate to Node.js now."
    assert [n for n, _q in _leads(text)] == ["Node.js"]


def test_the_quote_carries_both_sentences_so_the_context_is_visible():
    """Proximity is used to NOMINATE, so the reviewer must see what produced it."""
    _name, quote = _leads(TWO_SENTENCE)[0]
    assert "deprecated" in quote and "DeepWiki" in quote


def test_a_successor_in_the_same_sentence_still_works():
    one = "CodeToTutorial is deprecated; please migrate to DeepWiki instead."
    assert [n for n, _q in _leads(one)] == ["DeepWiki"]


def test_proximity_does_not_reach_beyond_the_next_sentence():
    """A lead may be proximate. It may not be arbitrary — an unrelated migration
    notice two paragraphs down must not be attributed to this subject."""
    text = ("CodeToTutorial has been deprecated and is no longer maintained. "
            "Our billing portal moved to a new host last year. "
            "Customers of SomethingElse should migrate to UnrelatedProduct.")
    assert _leads(text) == [], "reached beyond the neighbouring sentence"


def test_nothing_is_nominated_without_a_deprecation_quote_to_anchor_on():
    text = "Please migrate to DeepWiki for the best experience."
    assert _successor_leads(text, [], ("CodeToTutorial",)) == []


# ------------------------------------------------ the citation it carries

def test_the_successor_carries_an_authoritative_citation():
    dep, res = _gather(TWO_SENTENCE)
    alts = res.alternatives
    assert [a.name for a in alts] == ["DeepWiki"]
    alt = alts[0]
    assert alt.verified, "an alternative with no claim is silently discarded"
    c = alt.claims[0]
    assert c.kind is ClaimKind.ALTERNATIVE
    assert c.tier is Tier.AUTHORITATIVE, "the old vendor's own page"
    assert c.substantiating
    assert c.source_url.startswith("https://codetotutorial.com")


def test_the_statement_claims_only_what_the_old_page_can_settle():
    """The old vendor's page proves it NAMES a successor. It cannot prove the
    successor is any good — that needs the successor's own pages."""
    _dep_, res = _gather(TWO_SENTENCE)
    stmt = res.alternatives[0].claims[0].statement
    assert "names DeepWiki as the successor" in stmt
    assert "does the taught job" not in stmt.lower()


def test_a_successor_is_reported_once_however_many_pages_repeat_it():
    """`gather` walks several well-known paths and vendors repeat the notice on all."""
    _dep_, res = _gather(TWO_SENTENCE)
    names = [a.name for a in res.alternatives]
    assert len(names) == len(set(n.casefold() for n in names)) == 1


# --------------------------------------------- and it reaches the finding

def test_the_replacement_reaches_the_dead_tool_finding():
    dep, res = _gather(TWO_SENTENCE)
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                        status="broken", detail="404")
    probe.flag("url_gone")
    fs = findings_for(dep, probe, res)
    s1 = next(f for f in fs if f.signal == "S1")
    assert [a.name for a in s1.alternatives] == ["DeepWiki"]


def test_the_recommendation_does_not_advertise_an_empty_homepage():
    """A vendor-named successor is a name only until it is verified against its own
    domain. "DeepWiki ()" is worse than saying so."""
    from miw.analyse.notes import compose
    dep, res = _gather(TWO_SENTENCE)
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                        status="broken")
    probe.flag("url_gone")
    f = next(x for x in findings_for(dep, probe, res) if x.signal == "S1")
    compose(dep, f)
    assert "()" not in f.what_to_act
    assert "homepage not yet verified" in f.what_to_act


def test_a_healthy_tool_with_no_deprecation_notice_nominates_nothing():
    """Must-not-fire: the scanner is anchored on a deprecation quote."""
    dep, res = _gather("CodeToTutorial turns a repository into a guided tutorial. "
                       "You can also use DeepWiki for a different view.")
    assert res.alternatives == []


# ------------------------------------- S10, and the gate that silently ate it

def _verified_alt(name="NewTool", dom="newtool.com"):
    from miw.schema import Alternative, Claim
    alt = Alternative(name=name, homepage=f"https://{dom}")
    alt.claims.append(Claim.build(
        kind=ClaimKind.ALTERNATIVE,
        statement=f"{name} documents what it does",
        source_url=f"https://{dom}/docs",
        quote=f"{name} turns any repository into an explorable wiki for your team.",
        subject=Dependency(kind="service", canonical_name=name,
                           official_domains=[dom]).subject()))
    return alt


def _research(dep, alt):
    from miw.schema import ResearchResult
    res = ResearchResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name)
    res.alternatives.append(alt)
    return res


def test_an_s10_carries_its_alternatives_citations_so_it_can_be_reported():
    """`Finding.is_substantiated` reads `probe_signals` and `self.claims`. An S10 has
    no probe signals and its evidence lives on `alternatives[*].claims`, so before the
    fix `findings_for` returned [] even with a genuinely verified alternative — every
    other S10 fix would have been silently eaten by this gate."""
    dep = _dep("OldTool", "oldtool.com")
    alt = _verified_alt()
    fs = findings_for(dep, None, _research(dep, alt))
    s10 = next((f for f in fs if f.signal == "S10"), None)
    assert s10 is not None, "S10 was dropped at the is_substantiated gate"
    assert s10.is_substantiated
    assert [c.source_url for c in s10.claims] == ["https://newtool.com/docs"]
    assert s10.kind_of_signal == "opportunity"


def test_an_s10_never_outranks_a_regression():
    """An opportunity must not bury a blocked student."""
    dep = _dep("OldTool", "oldtool.com")
    fs = findings_for(dep, None, _research(dep, _verified_alt()))
    s10 = next(f for f in fs if f.signal == "S10")
    assert s10.severity in ("info", "low", "medium"), s10.severity


def test_an_unverified_alternative_produces_no_s10():
    """Must-not-fire: a nomination with no citation is not an opportunity."""
    from miw.schema import Alternative
    dep = _dep("OldTool", "oldtool.com")
    bare = Alternative(name="Unproven", homepage="https://unproven.example")
    assert not bare.verified
    fs = findings_for(dep, None, _research(dep, bare))
    assert [f.signal for f in fs] == []


def test_a_broken_dependency_reports_alternatives_on_s1_not_as_a_separate_s10():
    """Pins the working path so the S10 work cannot regress it."""
    dep = _dep("OldTool", "oldtool.com")
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                        status="broken", detail="404")
    probe.flag("url_gone")
    fs = findings_for(dep, probe, _research(dep, _verified_alt()))
    sigs = [f.signal for f in fs]
    assert "S1" in sigs and "S10" not in sigs
    assert [a.name for a in next(f for f in fs if f.signal == "S1").alternatives] \
        == ["NewTool"]
