"""How a vendor lets you in, as opposed to whether it does.

S2 answers the second: a wall appeared and the taught step now demands an account. This
is the case S2 cannot see — the tool is perfectly open, it authenticates differently, and
every taught setup step and every screenshot of its key page is wrong while nothing is
broken.

The design decision worth pinning is the evidence bar. `ClaimKind.AVAILABILITY` sits
outside `STRICT_KINDS`, so `substantiates()` would let a merely CORROBORATING source
settle "this tool now requires OAuth". An independent blog observing a login screen is
not the vendor changing its contract, and a session's setup steps get rewritten off this.
So the bar is raised at the call site rather than by loosening the policy for every other
AVAILABILITY claim.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.probe.http_probe import UrlObservation
from miw.probe.runner import res_from_urls
from miw.schema import Dependency, ProbeResult


def _obs(url, phrases):
    o = UrlObservation(url=url, final_url=url)
    o.reachable = True
    o.auth_phrases = list(phrases)
    return o


def _dep():
    return Dependency(kind="service", canonical_name="Acme",
                      homepage="https://acme.test", official_domains=["acme.test"])


def test_a_mechanism_gained_or_lost_is_the_signal():
    res = ProbeResult(dep_id="d", canonical_name="Acme")
    res_from_urls(res, [_obs("https://acme.test/docs", ["oauth", "client secret"])],
                  "", _dep(), seen_auth=["api key"])
    assert "auth_method_changed" in res.signals
    assert res.auth_change["gained"] == ["client secret", "oauth"]
    assert res.auth_change["lost"] == ["api key"]


def test_MUST_NOT_fire_on_the_first_look():
    """Never looked and looked-and-found-none are different facts, and only the second
    can produce a change. `state.auth_signals` returns None for the first."""
    res = ProbeResult(dep_id="d", canonical_name="Acme")
    res_from_urls(res, [_obs("https://acme.test/docs", ["api key"])], "", _dep(),
                  seen_auth=None)
    assert "auth_method_changed" not in res.signals
    assert res.auth_signals == ["api key"]      # ...but the baseline is recorded


def test_MUST_NOT_fire_when_the_mechanisms_are_the_same():
    """Presence is not the signal. Every docs page names a mechanism, so reporting
    presence would report every dependency, every week, for ever."""
    res = ProbeResult(dep_id="d", canonical_name="Acme")
    res_from_urls(res, [_obs("https://acme.test/docs", ["oauth", "api key"])], "",
                  _dep(), seen_auth=["api key", "oauth"])
    assert "auth_method_changed" not in res.signals


def test_MUST_NOT_fire_WHEN_NOTHING_COULD_BE_READ():
    """A run that read nothing has not observed a change; it has observed nothing.

    Both halves matter. An unreachable page's phrases are ignored, because an error page
    that happens to contain "api key" is not the vendor's documentation. And an EMPTY
    current set never fires at all, because "names nothing" would otherwise read as
    every mechanism being lost at once — on the week a vendor's docs host is down, for
    every dependency it serves.
    """
    dead = UrlObservation(url="https://acme.test/docs")
    dead.reachable = False
    dead.auth_phrases = ["oauth"]              # an error page that matched anyway
    res = ProbeResult(dep_id="d", canonical_name="Acme")
    res_from_urls(res, [dead], "", _dep(), seen_auth=["api key"])
    assert "auth_method_changed" not in res.signals
    assert res.auth_signals == [], "an unreadable page contributes nothing"


def test_the_finding_ships_only_on_the_vendors_own_say_so():
    """The decision this signal turns on: AUTHORITATIVE, explicitly, not whatever
    `substantiates()` allows for a non-strict kind."""
    from miw.analyse.score import findings_for
    from miw.trust import ClaimKind, Tier, substantiates

    # The policy this bypasses, asserted so a future loosening is visible here.
    assert substantiates(Tier.CORROBORATING, ClaimKind.AVAILABILITY), \
        "AVAILABILITY is non-strict; that is exactly why S15 raises its own bar"

    dep = _dep()
    def probe(url):
        p = ProbeResult(dep_id=dep.dep_id, canonical_name="Acme", status="changed")
        p.flag("auth_method_changed")
        p.auth_change = {"gained": ["oauth"], "lost": ["api key"],
                         "now": ["oauth"], "was": ["api key"], "evidence_url": url}
        return p

    own = [f for f in findings_for(dep, probe("https://acme.test/docs"), None)
           if f.signal == "S15"]
    assert own and own[0].claims, "the vendor's own page must substantiate it"
    assert own[0].claims[0].tier is Tier.AUTHORITATIVE

    # A reputable independent watched the login screen change. `techcrunch.com` is in
    # CORROBORATING_DOMAINS, so `substantiates()` WOULD accept it for a non-strict kind
    # — which is precisely the case this signal refuses. An unknown blog would prove
    # nothing here: it classifies LEAD_ONLY and fails either bar.
    from miw.trust import classify
    news = "https://techcrunch.com/acme-switches-to-oauth"
    assert classify(news, dep.subject(), ClaimKind.AVAILABILITY) is Tier.CORROBORATING
    assert substantiates(classify(news, dep.subject(), ClaimKind.AVAILABILITY),
                         ClaimKind.AVAILABILITY), "the bar we are deliberately raising"

    third = [f for f in findings_for(dep, probe(news), None) if f.signal == "S15"]
    assert third, "the observation is real and still reported"
    assert not third[0].claims, "a reputable outsider is not the vendor's contract"
    assert any(s.startswith("auth_change_unciteable:")
               for s in third[0].probe_signals), "and the refusal is counted"
