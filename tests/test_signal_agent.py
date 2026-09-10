"""The signal agent: a real graph, with the model confined to choosing URLs.

Every test here runs offline. The LLM is stubbed and the fetcher is injected, because
the point is to pin the CONTROL FLOW and the guards — not to check that a vendor's
website is up.

What must hold, and why each was worth a test:

* the model's output is a list of URLs and nothing else reaches a finding;
* a URL off the subject's own domains is never fetched (a hostile page cannot steer
  the agent to another host);
* the replan loop is bounded and does not re-propose a URL it already tried;
* "this vendor publishes nothing" is recorded as a refutation, never as a claim;
* a claim still comes from `Claim.build`, so it carries a fetched URL, a retrieval
  date and a verbatim quote;
* the graph resumes from its checkpointer rather than restarting.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("langgraph")

from miw.agents import signal_agent
from miw.agents.nodes import plan as plan_mod
from miw.agents.nodes import read as read_mod
from miw.llm import LLMResult
from miw.net import Fetch
from miw.schema import Dependency

DEPRECATION_PAGE = """<html><body><main>
<h1>Deprecations</h1>
<p>The model <code>gizmo-1</code> is deprecated and will be shut down on 2026-11-01.
Please migrate to gizmo-2 before that date.</p>
</main></body></html>"""


def _dep() -> Dependency:
    return Dependency(kind="model", canonical_name="gizmo-1",
                      homepage="https://gizmo.example",
                      docs_url="https://docs.gizmo.example",
                      official_domains=["gizmo.example", "docs.gizmo.example"],
                      vendor="Gizmo")


def _stub_llm(monkeypatch, *replies):
    """Feed the planner canned JSON, one reply per call."""
    calls = {"n": 0}

    def fake(prompt, **kw):
        i = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        body = replies[i]
        # `ok` is an explicit field on LLMResult, not derived from `text` — a stub that
        # forgets it silently exercises the provider-unavailable path instead.
        return LLMResult(text=json.dumps(body), ok=True, provider="stub", model="stub")

    monkeypatch.setattr(plan_mod, "complete", fake)
    return calls


def _stub_fetch(pages: dict):
    def fetcher(url, **kw):
        if url in pages:
            return Fetch(url=url, status=200, body=pages[url], final_url=url)
        return Fetch(url=url, status=404, final_url=url)
    return fetcher


@pytest.fixture()
def app():
    return signal_agent.compile_signal_graph(checkpointer=False)


# ------------------------------------------------------------------ happy path

def test_a_planned_page_becomes_a_cited_claim(monkeypatch, app):
    _stub_llm(monkeypatch, {"urls": ["https://docs.gizmo.example/deprecations"],
                            "publishes_nothing": False, "why": "docs host"})
    out = signal_agent.run_signal_agent(
        _dep(), kinds=("DEPRECATION",), graph=app,
        fetcher=_stub_fetch({"https://docs.gizmo.example/deprecations": DEPRECATION_PAGE}))

    assert out["status"] == "verified"
    assert out["claims"], "a substantiated claim should have been produced"
    c = out["claims"][0]
    assert c["source_url"] == "https://docs.gizmo.example/deprecations"
    assert c["retrieved_at"], "a claim carries the date we read it"
    assert len(c["quote"]) >= 12, "and a verbatim quote, per Claim.build"
    assert "deprecated" in c["quote"].lower()
    assert out["llm_calls"] == 1, "one planning call, no more"


def test_the_quote_is_lifted_from_the_page_not_from_the_model(monkeypatch, app):
    """The model never sees the page, so it cannot supply the quote."""
    _stub_llm(monkeypatch, {"urls": ["https://docs.gizmo.example/deprecations"],
                            "why": "x", "quote": "TOTALLY MADE UP",
                            "version": "9.9.9"})
    out = signal_agent.run_signal_agent(
        _dep(), kinds=("DEPRECATION",), graph=app,
        fetcher=_stub_fetch({"https://docs.gizmo.example/deprecations": DEPRECATION_PAGE}))
    blob = json.dumps(out)
    assert "TOTALLY MADE UP" not in blob
    assert "9.9.9" not in blob, "extra keys the model invents are ignored entirely"


# ------------------------------------------------------------------ the guards

def test_a_url_off_the_subjects_own_domains_is_never_fetched(monkeypatch, app):
    """A hostile page must not be able to steer the agent to another host."""
    fetched = []

    def watching(url, **kw):
        fetched.append(url)
        return Fetch(url=url, status=200, body=DEPRECATION_PAGE, final_url=url)

    _stub_llm(monkeypatch,
              {"urls": ["https://evil.example/steal", "https://gizmo.example.evil/x"],
               "why": "off-host"},
              {"urls": [], "publishes_nothing": False, "why": "nothing left"})
    out = signal_agent.run_signal_agent(_dep(), kinds=("DEPRECATION",), graph=app,
                                        fetcher=watching)
    assert fetched == [], "no off-allowlist URL may be requested"
    assert sorted(out["rejected_urls"]) == ["https://evil.example/steal",
                                            "https://gizmo.example.evil/x"]
    assert not out.get("claims")


def test_the_replan_loop_is_bounded_and_does_not_repeat_itself(monkeypatch, app):
    """Without the visited set the planner re-proposes the same dead path forever."""
    seen = []

    def fetcher(url, **kw):
        seen.append(url)
        return Fetch(url=url, status=404, final_url=url)

    calls = _stub_llm(monkeypatch,
                      {"urls": ["https://gizmo.example/a"], "why": "try a"},
                      {"urls": ["https://gizmo.example/a",       # already tried
                                "https://gizmo.example/b"], "why": "try b"})
    out = signal_agent.run_signal_agent(_dep(), kinds=("DEPRECATION",),
                                        max_attempts=2, graph=app, fetcher=fetcher)
    assert out["status"] == "exhausted"
    assert seen == ["https://gizmo.example/a", "https://gizmo.example/b"], \
        "the repeat was filtered by the visited set"
    assert calls["n"] == 2, "bounded by max_attempts"
    assert out["attempt"] == 2


def test_publishes_nothing_is_recorded_as_a_refutation_not_a_claim(monkeypatch, app):
    """Cached silence is what stops the system guessing four dead paths every week."""
    _stub_llm(monkeypatch, {"urls": [], "publishes_nothing": True,
                            "why": "no changelog anywhere on this site"})
    out = signal_agent.run_signal_agent(_dep(), kinds=("DEPRECATION",), graph=app,
                                        fetcher=_stub_fetch({}))
    assert out["status"] == "no_source"
    assert out["claims"] == [] if "claims" in out else True
    assert out["refuted"] and "publishes no" in out["refuted"][0]


def test_a_dependency_with_no_official_domain_stops_before_any_call(monkeypatch, app):
    """203 of 460 dependencies have no authority set; they must cost nothing."""
    called = {"n": 0}
    monkeypatch.setattr(plan_mod, "complete",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    bare = Dependency(kind="tool", canonical_name="Nameless")
    out = signal_agent.run_signal_agent(bare, graph=app, fetcher=_stub_fetch({}))
    assert out["status"] == "no_source"
    assert called["n"] == 0, "no model call for a subject we cannot speak for"


def test_an_unavailable_model_degrades_instead_of_raising(monkeypatch, app):
    monkeypatch.setattr(plan_mod, "complete",
                        lambda *a, **k: LLMResult(error="no provider", provider="none"))
    out = signal_agent.run_signal_agent(_dep(), graph=app, fetcher=_stub_fetch({}))
    assert out["status"] == "blocked"
    assert any("no provider" in e for e in out["errors"])


# ------------------------------------------------------------------ the graph itself

def test_the_graph_has_the_loop_edge_that_makes_it_a_graph():
    g = signal_agent.build_signal_graph()
    assert {"plan", "read", "record_silence", "give_up"} <= set(g.nodes)


def test_it_resumes_from_its_checkpointer_rather_than_restarting(monkeypatch, tmp_path):
    """A sweep over many vendors must survive a crash; that is why there is a saver."""
    from langgraph.checkpoint.memory import MemorySaver
    saver = MemorySaver()
    app = signal_agent.compile_signal_graph(checkpointer=saver)
    _stub_llm(monkeypatch, {"urls": ["https://docs.gizmo.example/deprecations"],
                            "why": "docs"})
    out = signal_agent.run_signal_agent(
        _dep(), kinds=("DEPRECATION",), graph=app, thread_id="t-1",
        fetcher=_stub_fetch({"https://docs.gizmo.example/deprecations": DEPRECATION_PAGE}))
    assert out["claims"]
    snap = app.get_state({"configurable": {"thread_id": "t-1"}})
    assert snap.values.get("status") == "verified", "state was persisted under the thread"
    assert snap.values.get("trajectory"), "the audit trail survives with it"


# ------------------------------------------- the table reader, wired in beside prose

DEPRECATION_TABLE = """<html><body><main>
<h1>Model Deprecation</h1>
<table>
  <tr><th>Model</th><th>Shutdown date</th><th>Recommended replacement</th></tr>
  <tr><td>gizmo-1</td><td>08/16/26</td><td>gizmo-2 or gizmo-3</td></tr>
  <tr><td>gizmo-1-lite</td><td>09/01/26</td><td>gizmo-2-lite</td></tr>
</table>
</main></body></html>"""


def test_a_deprecation_stated_in_a_TABLE_becomes_a_cited_claim(monkeypatch, app):
    """The measured gap this closes.

    Groq states retirements as a table row, which `official._is_prose` correctly refuses
    — so wiring only the prose reader returned 0 claims AND 0 rejections against exactly
    the right page. The row's verbatim text is the evidence.
    """
    _stub_llm(monkeypatch, {"urls": ["https://gizmo.example/deprecations"], "why": "x"})
    out = signal_agent.run_signal_agent(
        _dep(), kinds=("DEPRECATION",), graph=app,
        fetcher=_stub_fetch({"https://gizmo.example/deprecations": DEPRECATION_TABLE}))

    assert out["status"] == "verified"
    assert len(out["claims"]) == 1
    c = out["claims"][0]
    assert c["kind"] == "deprecation"
    assert "gizmo-1" in c["quote"] and "08/16/26" in c["quote"]
    assert "08/16/26" in c["statement"], "the shutdown date is carried into the statement"
    assert "from tables" in " ".join(out["trajectory"])


def test_a_neighbouring_id_in_the_same_table_is_not_implicated(monkeypatch, app):
    """Exact, case-folded matching only.

    The inventory holds `gemini-2.0-flash`, `gemini-2.0-flash-lite` and
    `gemini-3.1-flash-lite-preview`. A substring rule would report all three from one
    row, so this mirrors `vendors.Catalogue.get`.
    """
    _stub_llm(monkeypatch, {"urls": ["https://gizmo.example/deprecations"], "why": "x"})
    out = signal_agent.run_signal_agent(
        _dep(), kinds=("DEPRECATION",), graph=app,
        fetcher=_stub_fetch({"https://gizmo.example/deprecations": DEPRECATION_TABLE}))
    quotes = " ".join(c["quote"] for c in out["claims"])
    assert "gizmo-1-lite" not in quotes, "the lite variant is a different dependency"


def test_both_readers_run_on_the_same_page_without_fetching_it_twice(monkeypatch, app):
    """One request per page; the prose reader is handed the response we already have."""
    hits = []

    def counting(url, **kw):
        hits.append(url)
        return Fetch(url=url, status=200, body=DEPRECATION_TABLE, final_url=url)

    _stub_llm(monkeypatch, {"urls": ["https://gizmo.example/deprecations"], "why": "x"})
    signal_agent.run_signal_agent(_dep(), kinds=("DEPRECATION",), graph=app,
                                  fetcher=counting)
    assert hits.count("https://gizmo.example/deprecations") == 1


def test_a_table_row_too_short_to_quote_is_recorded_not_silently_dropped(monkeypatch, app):
    """`Claim.build` refuses a quote under 12 characters; a label is not evidence."""
    tiny = ("<html><body><main><h1>Deprecations</h1><table>"
            "<tr><th>Model</th><th>Status</th></tr>"
            "<tr><td>gizmo-1</td><td>x</td></tr></table></main></body></html>")
    _stub_llm(monkeypatch, {"urls": ["https://gizmo.example/d"], "why": "x"},
              {"urls": [], "why": "nothing else"})
    out = signal_agent.run_signal_agent(
        _dep(), kinds=("DEPRECATION",), graph=app,
        fetcher=_stub_fetch({"https://gizmo.example/d": tiny}))
    assert not out["claims"]
    assert out["status"] == "exhausted"


def test_provider_widening_reaches_the_extractor_not_just_the_allowlist(monkeypatch, app):
    """A taught model's owner and its server come apart.

    `llama-3.3-70b-versatile` carries Meta's domains, but Groq serves and retires it.
    Widening only the allowlist let the agent FETCH Groq's page while the claim built
    against a Meta subject classified LEAD_ONLY — non-substantiating, silently dropped.
    Measured on the live page: AUTHORITATIVE with widening, LEAD_ONLY without.
    """
    owned = Dependency(kind="model", canonical_name="gizmo-1",
                       official_domains=["owner.example"])
    _stub_llm(monkeypatch, {"urls": ["https://server.example/deprecations"], "why": "x"})
    pages = {"https://server.example/deprecations": DEPRECATION_TABLE}

    without = signal_agent.run_signal_agent(
        owned, kinds=("DEPRECATION",), graph=app, fetcher=_stub_fetch(pages))
    assert without["rejected_urls"], "the server's host is not on the owner's allowlist"
    assert not without.get("claims")

    _stub_llm(monkeypatch, {"urls": ["https://server.example/deprecations"], "why": "x"})
    with_prov = signal_agent.run_signal_agent(
        owned, kinds=("DEPRECATION",), graph=app, fetcher=_stub_fetch(pages),
        provider_domains=["server.example"])
    assert with_prov["claims"], "earned widening makes the server authoritative"
    assert with_prov["claims"][0]["tier"] == "AUTHORITATIVE"


def test_widening_is_refused_for_kinds_where_owner_and_server_cannot_differ(monkeypatch, app):
    """`subject_with_provider` is models-only: a package's registry IS its vendor."""
    pkg = Dependency(kind="package", canonical_name="gizmo-lib",
                     official_domains=["owner.example"])
    _stub_llm(monkeypatch, {"urls": ["https://server.example/x"], "why": "x"},
              {"urls": [], "why": "done"})
    out = signal_agent.run_signal_agent(
        pkg, kinds=("DEPRECATION",), graph=app, fetcher=_stub_fetch({}),
        provider_domains=["server.example"])
    assert out["rejected_urls"], "no widening for a package, so the host stays off-list"
