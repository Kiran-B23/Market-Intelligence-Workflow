"""Where did the page go?

A dead-link finding used to say "repoint or replace it" and stop, which hands the
reviewer's whole job back: open the URL, see the 404, then try the obvious candidates on
the vendor's own site by hand. That is a deterministic search, it belongs in the stage
that already makes HTTP requests, and the answers for the live artifact are:

    docs.langchain.com/.../pypdfloader  -> .../document_loaders   (the section above)
    mcp.composio.dev/dashboard          -> composio.dev/toolkits
    api.stability.ai/v2beta/...         -> platform.stability.ai/docs/api-reference
    earth-ai.com/technology             -> earth-ai.com/
    xxxxx.gradio.live                   -> not a link at all
    abc123.ngrok.io                     -> not a link at all
"""
from dataclasses import dataclass

from miw.probe.successor import (MAX_TRIES, find_successor, is_placeholder,
                                 _candidates)


@dataclass
class Obs:
    status: int = 200
    final_url: str = ""
    gone: bool = False
    reachable: bool = True
    parked: bool = False
    title: str = ""


def _world(live: dict):
    """A fake web: every URL 404s unless it is in `live`."""
    seen = []

    def look(url):
        seen.append(url)
        if url in live:
            return Obs(status=200, final_url=live[url], title=live[url])
        return Obs(status=404, gone=True)
    return look, seen


# ------------------------------------------------------------------ placeholders

def test_an_ephemeral_tunnel_url_is_not_a_broken_link():
    assert is_placeholder("https://abc123.ngrok.io")
    assert is_placeholder("https://xxxxx.gradio.live")
    assert is_placeholder("https://your-subdomain.ngrok-free.app")


def test_a_real_url_on_the_same_vendor_is_not_a_placeholder():
    """ngrok.com is the product. Only the ephemeral hosts are examples."""
    assert not is_placeholder("https://ngrok.com/docs")
    assert not is_placeholder("https://dashboard.ngrok.com")


def test_a_placeholder_shaped_label_on_an_ordinary_host_is_not_one():
    """Both tests have to fire: an ephemeral host AND a stand-in label."""
    assert not is_placeholder("https://abc123.example.com/page")


def test_a_placeholder_search_looks_for_nothing():
    look, seen = _world({})
    r = find_successor("https://abc123.ngrok.io", homepage="https://ngrok.com",
                       official_domains=["ngrok.com", "ngrok.io"], observer=look)
    assert r.placeholder and r.found is None
    assert seen == [], "no request may be spent looking for a page that never existed"


# ------------------------------------------------------------------ the candidates

def test_the_redirect_target_is_tried_first_and_is_not_trusted():
    """Composio's dashboard redirects to a URL that also 404s."""
    cands = _candidates("https://mcp.composio.dev/dashboard",
                        "https://composio.dev/toolkits/dashboard",
                        "https://composio.dev")
    assert cands[0] == ("https://composio.dev/toolkits/dashboard", "redirect")


def test_the_path_walks_up_before_falling_back_to_the_home_page():
    rules = [r for _, r in _candidates(
        "https://docs.x.com/a/b/c", "", "https://x.com")]
    assert rules.index("trimmed") < rules.index("home")


def test_a_deep_path_resolves_to_the_section_above_it():
    dead = "https://docs.langchain.com/oss/python/integrations/document_loaders/pypdfloader"
    parent = "https://docs.langchain.com/oss/python/integrations/document_loaders"
    look, _ = _world({parent: parent})
    r = find_successor(dead, homepage="https://langchain.com",
                       official_domains=["langchain.com", "docs.langchain.com"],
                       observer=look)
    assert r.found and r.found.url == parent and r.found.rule == "trimmed"
    assert "section above" in r.found.note


# ------------------------------------------------- only the vendor's own pages

def test_a_candidate_on_somebody_elses_domain_is_never_tried():
    """A wrong guess here sends a student somewhere the course never intended."""
    look, seen = _world({"https://evil.example/dashboard": "x"})
    find_successor("https://mcp.composio.dev/dashboard",
                   final_url="https://evil.example/dashboard",
                   homepage="https://composio.dev",
                   official_domains=["composio.dev", "mcp.composio.dev"],
                   observer=look)
    assert "https://evil.example/dashboard" not in seen


def test_nothing_is_reported_when_nothing_answers():
    look, seen = _world({})
    r = find_successor("https://gone.example/a/b", homepage="https://gone.example",
                       official_domains=["gone.example"], observer=look)
    assert r.found is None
    assert r.tried, "the attempt is recorded even when it fails"
    assert len(seen) <= MAX_TRIES


def test_a_parked_page_is_not_a_successor():
    def look(url):
        return Obs(status=200, final_url=url, parked=True)
    r = find_successor("https://x.example/a", homepage="https://x.example",
                       official_domains=["x.example"], observer=look)
    assert r.found is None


def test_a_probe_that_raises_does_not_break_the_search():
    calls = []

    def look(url):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("network")
        return Obs(status=200, final_url=url)
    r = find_successor("https://x.example/a/b", homepage="https://x.example",
                       official_domains=["x.example"], observer=look)
    assert r.found is not None


# ------------------------------------------------------- what the finding says

def test_the_action_names_the_new_url():
    from miw.analyse.score import findings_for
    from miw.schema import Dependency, Location, ProbeResult

    dep = Dependency(kind="service", canonical_name="Composio",
                     homepage="https://composio.dev",
                     official_domains=["composio.dev", "mcp.composio.dev"],
                     locations=[Location(course="Intro to Gen AI", topic_name="T",
                                         unit_id="u", unit_name="U", content_id="c",
                                         field_path="f", evidence_source="link:a_href",
                                         object_type="LEARNING_RESOURCE", session_no=1,
                                         url="https://mcp.composio.dev/dashboard")])
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                        status="broken")
    probe.flag("url_gone")
    probe.affected_urls = ["https://mcp.composio.dev/dashboard"]
    probe.successors = [{"dead": "https://mcp.composio.dev/dashboard",
                         "url": "https://composio.dev/toolkits", "rule": "home",
                         "note": "the product's home page; the specific page is gone"}]
    f = findings_for(dep, probe, None)[0]
    assert "https://composio.dev/toolkits" in f.recommendation
    assert "confirm it is the page the session meant" in f.recommendation


def test_a_placeholder_changes_the_instruction_not_just_adds_to_it():
    """"Repoint the dead link" is the wrong instruction when there is no link."""
    from miw.analyse.score import findings_for
    from miw.schema import Dependency, Location, ProbeResult

    dep = Dependency(kind="service", canonical_name="Ngrok",
                     homepage="https://ngrok.com", official_domains=["ngrok.com"],
                     locations=[Location(course="Intro to Gen AI", topic_name="T",
                                         unit_id="u", unit_name="U", content_id="c",
                                         field_path="f", evidence_source="link:a_href",
                                         object_type="LEARNING_RESOURCE", session_no=15,
                                         url="https://abc123.ngrok.io")])
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                        status="broken")
    probe.flag("url_gone")
    probe.affected_urls = ["https://abc123.ngrok.io"]
    probe.successors = [{"dead": "https://abc123.ngrok.io", "placeholder": True}]
    f = findings_for(dep, probe, None)[0]
    assert "Not a broken link" in f.recommendation
    assert "Repoint" not in f.recommendation
    assert "show it as sample text" in f.recommendation
