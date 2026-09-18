"""A redirect is two different events wearing one name.

S5 is labelled "the taught steps changed" and every one of its findings is produced by a
redirect. But:

    cookbook.openai.com  -> developers.openai.com    OpenAI reorganised its docs
    windsurf.com         -> devin.ai/desktop         the product was absorbed

The first implicates the two links that point at it. The second makes the prose that
names the tool wrong as well. Reporting both as "redirected" - and widening S5 to every
location whose `object_type` was reading material or a deck - is why an OpenAI docs move
claimed **24 places, 21 of them reading material that merely says the word "OpenAI"**,
while the tool itself had not changed at all.

Measured before and after, on the live artifact:

    Google 65 -> 23   OpenAI 24 -> 2   Composio 12 -> 2   Mistral 8 -> 1   Chroma 5 -> 1
    Windsurf 2 -> 17  ProtectAI 1 -> 11        <- these two rose, and should have
"""
import pytest

from miw.analyse.score import findings_for, reaches, reaching_locations, s5_reach
from miw.schema import Dependency, Location, ProbeResult

COURSE = "Intro to Gen AI"


def loc(evidence, object_type="LEARNING_RESOURCE", url=""):
    return Location(course=COURSE, topic_name="T", unit_id="u", unit_name="U",
                    content_id="c", field_path="f", evidence_source=evidence,
                    object_type=object_type, session_no=3, url=url)


INSIDE = [{"from": "https://cookbook.openai.com/",
           "to": "https://developers.openai.com/cookbook", "off_site": False}]
OUTSIDE = [{"from": "https://windsurf.com", "to": "https://devin.ai/desktop",
            "off_site": True}]


def test_the_three_events_are_told_apart():
    assert s5_reach(INSIDE) == "links"
    assert s5_reach(OUTSIDE) == "moved"
    # No redirect at all means a researched IMPLEMENTATION claim: the vendor says it
    # changed how the thing works, which is the only case where a deck goes stale.
    assert s5_reach([]) == "behaviour"


def test_a_vendor_reorganising_its_own_site_does_not_touch_prose():
    """The complaint, exactly: an OpenAI docs move should point at the URL, not at every
    session that says "OpenAI"."""
    locs = [loc("link:a_href", url="https://cookbook.openai.com/"),
            loc("link:markdown", url="https://cookbook.openai.com/examples/x")] \
        + [loc("prose_name") for _ in range(10)] \
        + [loc("prose_name", "SESSION_PPT") for _ in range(3)]
    reached, rest = reaching_locations(
        "S5", ["https://cookbook.openai.com/"], locs, INSIDE)
    assert len(reached) == 1                      # narrowed to the URL that moved
    assert reached[0].evidence_source == "link:a_href"
    assert len(rest) == 14


def test_a_product_that_moved_off_its_own_domain_does_touch_prose():
    """windsurf.com -> devin.ai. Every place that NAMES it now names something that has
    been rebranded, and that is a real edit rather than a link repoint."""
    locs = [loc("prose_name") for _ in range(12)] + [loc("title")]
    reached, _ = reaching_locations("S5", ["https://windsurf.com"], locs, OUTSIDE)
    assert len(reached) == 13


def test_a_deck_goes_stale_only_when_the_behaviour_changed():
    deck = loc("prose_name", "SESSION_PPT")
    assert reaches("S5", deck, "behaviour")
    assert not reaches("S5", deck, "links")


def test_a_reading_material_that_merely_names_the_tool_is_never_a_depiction():
    """The `_DEPICTED` widening keyed on `object_type` alone, so the word "OpenAI" in a
    reading material counted as showing OpenAI's UI."""
    assert not reaches("S5", loc("prose_name"), "links")


# ------------------------------------------------- the probe has to record it

def _obs(url, final):
    from miw.probe.http_probe import UrlObservation
    return UrlObservation(url=url, status=200, final_url=final,
                          redirected_off_path=True, reachable=True)


def test_the_probe_marks_a_redirect_inside_the_vendors_estate():
    from miw.probe.runner import res_from_urls
    dep = Dependency(kind="service", canonical_name="OpenAI",
                     homepage="https://openai.com",
                     official_domains=["openai.com", "cookbook.openai.com"])
    res = ProbeResult(dep_id="x", canonical_name="OpenAI")
    res_from_urls(res, [_obs("https://cookbook.openai.com/",
                             "https://developers.openai.com/cookbook")], "", dep)
    assert res.redirects == [{"from": "https://cookbook.openai.com/",
                              "to": "https://developers.openai.com/cookbook",
                              "off_site": False}]


def test_the_probe_marks_a_redirect_that_left_it():
    from miw.probe.runner import res_from_urls
    dep = Dependency(kind="tool", canonical_name="Windsurf",
                     homepage="https://windsurf.com",
                     official_domains=["windsurf.com", "docs.windsurf.com"])
    res = ProbeResult(dep_id="y", canonical_name="Windsurf")
    res_from_urls(res, [_obs("https://windsurf.com", "https://devin.ai/desktop")],
                  "", dep)
    assert res.redirects[0]["off_site"] is True


def test_a_redirect_is_recorded_even_when_another_url_is_dead():
    """`url_gone` returns before the redirect branch. Composio has exactly that shape -
    a dead dashboard and a moved docs path - and its S5 was left with no redirect
    record, so the scoper could not tell a reorganisation from a rebrand."""
    from miw.probe.http_probe import UrlObservation
    from miw.probe.runner import res_from_urls
    dep = Dependency(kind="service", canonical_name="Composio",
                     homepage="https://composio.dev",
                     official_domains=["composio.dev", "mcp.composio.dev"])
    res = ProbeResult(dep_id="z", canonical_name="Composio")
    res_from_urls(res, [
        UrlObservation(url="https://mcp.composio.dev/dead", status=404, gone=True,
                       reachable=True),
        _obs("https://mcp.composio.dev/dashboard",
             "https://composio.dev/toolkits/dashboard")], "", dep)
    assert res.signals and "url_gone" in res.signals
    assert res.redirects and res.redirects[0]["off_site"] is False


# ------------------------------------------------------------ what it then says

def _finding(redirects, locs):
    dep = Dependency(kind="service", canonical_name="OpenAI",
                     homepage="https://openai.com", official_domains=["openai.com"],
                     locations=locs)
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                        status="changed")
    probe.flag("redirected_off_path")
    probe.affected_urls = [r["from"] for r in redirects]
    probe.redirects = redirects
    return next(f for f in findings_for(dep, probe, None) if f.signal == "S5")


def test_a_reorganisation_says_repoint_the_link_and_nothing_more():
    f = _finding(INSIDE, [loc("link:a_href", url="https://cookbook.openai.com/")])
    assert "Repoint the link" in f.recommendation
    assert "nothing about how OpenAI works has changed" in f.recommendation
    assert "screenshots" not in f.recommendation


def test_a_move_says_check_whether_it_was_rebranded():
    f = _finding(OUTSIDE, [loc("prose_name")])
    assert "moved off its own domain" in f.recommendation
    assert "rebranded or acquired" in f.recommendation
    assert "the prose that names it is wrong too" in f.recommendation


def test_an_acquirers_domain_is_not_the_acquired_products_authority():
    """Seeding `devin.ai` into Windsurf's authority set made its own acquisition read as
    an internal reorganisation. The registry records why it must not be there."""
    import pathlib

    import yaml
    reg = yaml.safe_load((pathlib.Path(__file__).resolve().parents[1]
                          / "registry" / "tools.yaml").read_text())["tools"]
    e = next(x for x in reg if x["canonical_name"] == "Windsurf")
    assert not any("devin.ai" in d for d in e["official_domains"])
    assert "acquirer does not speak for the product it absorbed" in e["notes"]
