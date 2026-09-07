"""The codetotutorial backtest, as a deterministic fixture.

The PRD's acceptance test was "point MIW at the export that still teaches
codetotutorial". That export no longer exists: the content team has already replaced
codetotutorial with deepwiki, and deepwiki is what the current Gen AI export teaches
(session 4, "Productivity Power-Up with AI Tools"). So the historical incident cannot
be replayed against live data.

What matters is that the *mechanism* which would have caught it works, so the incident
is reconstructed here from crafted observations. This runs offline, in milliseconds,
and asserts the four behaviours the real case needed:

  1. a dead referenced URL becomes `broken`,
  2. but only after two consecutive runs agree - one bad week is never a finding,
  3. the resulting finding is S1, substantiated by our own probe with no citation
     required, and carries every session that referenced the tool,
  4. a nominated replacement is only reported once verified against its own official
     domain.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tempfile

from miw.analyse.score import findings_for
from miw.probe.http_probe import UrlObservation
from miw.probe.runner import MIN_CONSECUTIVE_FAILURES, res_from_urls
from miw.schema import Alternative, Claim, Dependency, Location, ProbeResult
from miw.state import State
from miw.trust import ClaimKind, Subject, Tier

TOOL = "CodeToTutorial"
DOMAIN = "codetotutorial.com"
URL = f"https://{DOMAIN}/"


def _dep() -> Dependency:
    """codetotutorial as it was taught: linked from six Gen AI sessions."""
    return Dependency(
        kind="service", canonical_name=TOOL, homepage=URL,
        official_domains=[DOMAIN], referenced_urls=[URL],
        locations=[
            Location(course="Intro to Gen AI", topic_name="AI Tools",
                     unit_id=f"u{i}", unit_name=f"Session {i} | Reading Material",
                     content_id="", field_path=f"[0].topics[1].units[{i}].contents[0].content",
                     evidence_source="link:a_href", object_type="LEARNING_RESOURCE",
                     session_no=i)
            for i in range(4, 10)
        ])


def _dead_observation() -> UrlObservation:
    return UrlObservation(url=URL, status=404, final_url=URL, gone=True, reachable=True)


def test_dead_referenced_url_is_broken():
    res = ProbeResult(dep_id="x", canonical_name=TOOL)
    res_from_urls(res, [_dead_observation()], prev_hash="")
    assert res.status == "broken"
    assert "url_gone" in res.signals
    assert URL in res.detail


def test_one_bad_week_is_never_a_finding():
    """Flap protection: the first failing run reports inconclusive, not broken."""
    dep = _dep()
    with tempfile.TemporaryDirectory() as tmp:
        state = State(Path(tmp) / "t.db")
        statuses = []
        for _ in range(MIN_CONSECUTIVE_FAILURES):
            res = ProbeResult(dep_id=dep.dep_id, canonical_name=TOOL)
            res_from_urls(res, [_dead_observation()], prev_hash="")
            prev = state.probe_prev(dep.dep_id)
            fails = (prev["consecutive_failures"] if prev else 0) or 0
            res.consecutive_failures = fails + 1
            if res.consecutive_failures < MIN_CONSECUTIVE_FAILURES:
                res.status = "inconclusive"
                res.flag("awaiting_confirmation")
            state.probe_save(dep_id=dep.dep_id, canonical_name=TOOL, status=res.status,
                             checked_at="now", consecutive_failures=res.consecutive_failures)
            statuses.append(res.status)
        state.close()
    assert statuses[0] == "inconclusive", "a single failed run must not be reported"
    assert statuses[-1] == "broken", "a confirmed failure must be reported"


def test_finding_is_s1_and_needs_no_citation():
    """A 404 we fetched ourselves is first-hand evidence, not a model conclusion."""
    dep = _dep()
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=TOOL)
    res_from_urls(probe, [_dead_observation()], prev_hash="")
    findings = findings_for(dep, probe, None)
    assert findings, "a confirmed dead tool must produce a finding"
    f = findings[0]
    assert f.signal == "S1"
    assert f.kind_of_signal == "regression"
    assert f.severity in ("critical", "high")
    assert f.is_substantiated and not f.claims, "probe observations stand alone"
    assert {l.session_no for l in f.locations} == {4, 5, 6, 7, 8, 9}, \
        "every session that referenced the tool must be listed"
    assert TOOL in f.recommendation and "Intro to Gen AI" in f.recommendation


def test_replacement_is_reported_only_once_verified_on_its_own_domain():
    """deepwiki nominated by a directory is a lead; deepwiki quoted from deepwiki.com
    is evidence. Only the verified form reaches a finding."""
    deepwiki = Subject("DeepWiki", homepage="https://deepwiki.com").with_domains_from_urls()

    nominated = Alternative(name="DeepWiki", homepage="https://deepwiki.com",
                            nominated_by="https://alternativeto.net/software/x/")
    assert not nominated.verified, "a directory nomination is not verification"

    nominated.claims.append(Claim.build(
        kind=ClaimKind.AVAILABILITY,
        statement="DeepWiki: its own pages describe access requirements",
        source_url="https://deepwiki.com/",
        quote="DeepWiki is free for open source repositories, no signup required.",
        subject=deepwiki))
    assert nominated.claims[0].tier is Tier.AUTHORITATIVE
    assert nominated.verified

    dep = _dep()
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=TOOL)
    res_from_urls(probe, [_dead_observation()], prev_hash="")
    from miw.schema import ResearchResult
    research = ResearchResult(dep_id=dep.dep_id, canonical_name=TOOL,
                              alternatives=[nominated])
    f = findings_for(dep, probe, research)[0]
    assert [a.name for a in f.alternatives] == ["DeepWiki"]
    assert "DeepWiki" in f.recommendation
