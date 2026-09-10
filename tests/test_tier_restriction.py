"""A vendor can say two true things at once, and collapsing them invents an outage.

Groq's deprecations table gives `llama-3.3-70b-versatile` a shutdown date that has
passed. Groq's models table still lists that exact id, as "Llama 3.3 70B Enterprise" at
"Contact Sales". Both are official. Reading only the first told a reviewer the model was
shut down - which they disprove in one click on the vendor's own page.

This is the same distinction `Catalogue` already draws between `ok` and `supported`,
one level down: retired-on-the-developer-plan is not gone.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.probe.catalogue import _role_of, entries
from miw.vendors.base import build_catalogue

DEPRECATIONS = """
<table><tr><th>Deprecated Model</th><th>Shutdown Date</th>
<th>Recommended Replacement Model ID</th></tr>
<tr><td><code>llama-3.3-70b-versatile</code></td><td>08/16/26</td>
    <td><code>openai/gpt-oss-120b</code></td></tr></table>
"""

MODELS = """
<table><tr><th>MODEL ID</th><th>SPEED (T/SEC)</th><th>PRICE PER 1M TOKENS</th>
<th>RATE LIMITS (DEVELOPER PLAN)</th></tr>
<tr><td>Llama 3.3 70B Enterprise <code>llama-3.3-70b-versatile</code></td><td>280</td>
    <td>Contact Sales</td><td>Contact Sales</td></tr>
<tr><td>MiniMax M2.7 Enterprise <code>minimaxai/minimax-m2.7</code></td><td>500</td>
    <td>Contact Sales</td><td>Contact Sales</td></tr>
<tr><td>GPT OSS 120B <code>openai/gpt-oss-120b</code></td><td>500</td>
    <td>$0.15 input $0.60 output</td><td>250K TPM 1K RPM</td></tr></table>
"""


def _cat(pages, monkeypatch):
    """Build a catalogue from literal HTML, bypassing the network."""
    from miw.vendors import base
    monkeypatch.setattr(base, "cached_pages", lambda k, urls, refresh=False: (pages, []))
    return build_catalogue("Groq", "groq", list(pages))


# ------------------------------------------------------- the new column roles

def test_price_and_rate_limit_headers_now_carry_a_role():
    assert _role_of("PRICE PER 1M TOKENS") == "price"
    assert _role_of("RATE LIMITS (DEVELOPER PLAN)") == "rate_limit"


def test_the_new_roles_do_not_steal_an_existing_column():
    """Purely additive: every header on both vendors' pages keeps the role it had."""
    assert _role_of("Deprecated Model") == "id"
    assert _role_of("MODEL ID") == "id"
    assert _role_of("Endpoint") == "id"
    assert _role_of("Model") == "id"
    assert _role_of("Shutdown Date") == "date"
    assert _role_of("Recommended Replacement Model ID") == "replacement"


def test_the_price_cell_reaches_the_record():
    rows = [e for e in entries(MODELS) if e.entry_id == "llama-3.3-70b-versatile"]
    assert rows and rows[0].price == "Contact Sales"
    assert rows[0].rate_limit == "Contact Sales"


# ------------------------------------------------------ the reconciliation

def test_both_sightings_are_kept_and_yield_tier_restriction(monkeypatch):
    cat = _cat({"https://x/deprecations": DEPRECATIONS,
                "https://x/models": MODELS}, monkeypatch)
    e = cat.get("llama-3.3-70b-versatile")
    assert e.retired, "the retirement is still the primary record"
    assert e.still_listed, "the availability sighting must not be discarded"
    assert e.listed_price == "Contact Sales"
    assert e.tier_restricted


def test_the_merge_is_order_independent(monkeypatch):
    """A dict has no page order guarantee; both orders must reconcile identically."""
    a = _cat({"https://x/deprecations": DEPRECATIONS, "https://x/models": MODELS},
             monkeypatch).get("llama-3.3-70b-versatile")
    b = _cat({"https://x/models": MODELS, "https://x/deprecations": DEPRECATIONS},
             monkeypatch).get("llama-3.3-70b-versatile")
    assert a.tier_restricted and b.tier_restricted
    assert a.listed_price == b.listed_price


# --------------------------------------------------------- must not fire

def test_quote_only_pricing_alone_is_a_tier_not_a_retirement(monkeypatch):
    """`minimaxai/minimax-m2.7` is also Contact Sales and is in no deprecation table.
    That is a pricing tier. It must raise nothing."""
    cat = _cat({"https://x/deprecations": DEPRECATIONS,
                "https://x/models": MODELS}, monkeypatch)
    e = cat.get("minimaxai/minimax-m2.7")
    assert e is not None and not e.retired
    assert not e.tier_restricted, "Enterprise pricing is not a deprecation"


def test_a_normally_priced_model_is_untouched(monkeypatch):
    cat = _cat({"https://x/deprecations": DEPRECATIONS,
                "https://x/models": MODELS}, monkeypatch)
    e = cat.get("openai/gpt-oss-120b")
    assert not e.retired and not e.tier_restricted and not e.quoted_only


def test_a_retirement_with_no_live_listing_stays_a_plain_retirement(monkeypatch):
    """The narrow case must stay narrow: only the deprecations page, so still 'gone'."""
    cat = _cat({"https://x/deprecations": DEPRECATIONS}, monkeypatch)
    e = cat.get("llama-3.3-70b-versatile")
    assert e.retired and not e.still_listed and not e.tier_restricted


def test_a_replacement_column_id_never_becomes_tier_restricted(monkeypatch):
    """`openai/gpt-oss-120b` sits in the replacement column of a deprecated row."""
    cat = _cat({"https://x/deprecations": DEPRECATIONS,
                "https://x/models": MODELS}, monkeypatch)
    assert not cat.get("openai/gpt-oss-120b").retired


# ----------------------------------------------- what the reviewer is told

def test_the_finding_says_left_the_developer_plan_not_shut_down():
    from miw.analyse.notes import compose
    from miw.analyse.score import findings_for
    from miw.schema import Dependency, Location, ProbeResult

    dep = Dependency(kind="model", canonical_name="llama-3.3-70b-versatile",
                     official_domains=["ai.meta.com"],
                     locations=[Location(course="Intro to Gen AI", topic_name="t",
                                         unit_id="u", unit_name="n", content_id="q1",
                                         field_path="f", evidence_source="model_id",
                                         object_type="CODING_QUESTIONS", session_no=6)])
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                        status="broken", provider="Groq",
                        provider_domains=["groq.com", "console.groq.com"],
                        evidence_url="https://console.groq.com/docs/deprecations",
                        detail="Groq lists it as deprecated but still offers it at "
                               "“Contact Sales”")
    probe.flag("model_tier_restricted")
    f = [x for x in findings_for(dep, probe, None) if x.signal == "S7"][0]
    compose(dep, f)
    assert f.severity == "critical", "a student's free key still fails"
    assert "developer plan" in f.what_to_act
    assert "developer plan" in f.why_to_act
    assert "retired at its provider" not in f.what_to_act
