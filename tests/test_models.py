"""Model liveness: is a taught id still served, and who is allowed to say so.

The finding that drove this: `llama-3.3-70b-versatile` is attributed to Meta by the
extractor's prefix match, but Groq serves it and Groq retired it. Without provider
authority the evidence was dropped for a model sitting in 15 graded items.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.probe.catalogue import CatalogueEntry
from miw.probe.models import parse_shutdown, probe_model_dependency, verified_replacements
from miw.schema import Dependency, Location
from miw.trust import ClaimKind, classify, substantiates
from miw.vendors.base import Catalogue

TODAY = date(2026, 9, 9)


class FakeAdapter:
    """A vendor adapter with a fixed catalogue, so every case runs offline."""
    kinds = ("model",)

    def __init__(self, key, vendor, domains, entries=None, ok=True, supported=True,
                 error=""):
        self.key, self.vendor, self.official_domains = key, vendor, domains
        self._cat = Catalogue(vendor=vendor, entries=entries or {}, ok=ok,
                              supported=supported, error=error)

    def catalogue(self, refresh=False):
        return self._cat


def entry(eid, status, date_="", repl=(), url="https://groq.com/docs/deprecations"):
    return CatalogueEntry(entry_id=eid, status=status, shutdown_date=date_,
                          replacement_ids=list(repl), column_role="id",
                          quote=f"{eid} | {date_} | {' or '.join(repl)}",
                          evidence_url=url)


def model(name, kind_locs=1, otype="CODING_QUESTIONS", vendor="Meta"):
    return Dependency(
        kind="model", canonical_name=name, vendor=vendor,
        official_domains=["ai.meta.com", "llama.com"],
        locations=[Location(course="C", topic_name="t", unit_id="u", unit_name="n",
                            content_id=f"q{i}", field_path="f",
                            evidence_source="model_id", object_type=otype,
                            session_no=6) for i in range(kind_locs)])


GROQ = ["groq.com", "console.groq.com"]


# ------------------------------------------------------------------ the ladder

def test_a_passed_shutdown_date_is_a_live_outage_not_a_warning():
    a = FakeAdapter("groq", "Groq", GROQ,
                    {"llama-3.3-70b-versatile":
                     entry("llama-3.3-70b-versatile", "deprecated", "08/16/26",
                           ["openai/gpt-oss-120b"])})
    r = probe_model_dependency(model("llama-3.3-70b-versatile"), today=TODAY,
                               adapters=[a])
    assert r.status == "broken"
    assert "model_shutdown_passed" in r.signals
    assert r.provider == "Groq" and "groq.com" in r.provider_domains
    assert "24 days ago" in r.detail


def test_a_future_shutdown_date_is_a_warning():
    a = FakeAdapter("groq", "Groq", GROQ,
                    {"m": entry("m", "deprecated", "12/31/26")})
    r = probe_model_dependency(model("m"), today=TODAY, adapters=[a])
    assert r.status == "changed" and "model_deprecation_declared" in r.signals


def test_a_listed_available_model_fires_nothing():
    a = FakeAdapter("google_ai", "Google", ["ai.google.dev"],
                    {"gemini-2.5-flash": entry("gemini-2.5-flash", "available")})
    r = probe_model_dependency(model("gemini-2.5-flash"), today=TODAY, adapters=[a])
    assert r.status == "ok" and "model_listed_available" in r.signals


def test_an_unreadable_catalogue_is_never_a_retirement():
    """The most important must-not-fire case in the phase: a vendor page returning 503
    must not read as "the model is gone"."""
    a = FakeAdapter("groq", "Groq", GROQ, ok=False, error="http 503")
    r = probe_model_dependency(model("llama-3.3-70b-versatile"), today=TODAY,
                               adapters=[a])
    assert r.status == "inconclusive"
    assert "model_catalogue_unreadable" in r.signals
    assert "model_shutdown_passed" not in r.signals


def test_a_vendor_that_publishes_no_catalogue_asserts_nothing():
    a = FakeAdapter("x", "X", ["x.test"], supported=False)
    r = probe_model_dependency(model("whatever"), today=TODAY, adapters=[a])
    assert r.status == "ok" and "model_provider_unknown" in r.signals


def test_a_model_no_configured_provider_serves_is_left_alone():
    a = FakeAdapter("groq", "Groq", GROQ, {"other": entry("other", "available")})
    r = probe_model_dependency(model("qwen3.5"), today=TODAY, adapters=[a])
    assert r.status == "ok" and r.provider == ""
    assert "model_provider_unknown" in r.signals


# ------------------------------------------------------- P0: provider authority

def test_the_serving_provider_is_authoritative_over_the_family_owner():
    dep = model("llama-3.3-70b-versatile")          # attributed to Meta
    url = "https://console.groq.com/docs/deprecations"
    assert classify(url, dep.subject(), ClaimKind.DEPRECATION).name == "LEAD_ONLY"
    widened = dep.subject_with_provider(GROQ)
    tier = classify(url, widened, ClaimKind.DEPRECATION)
    assert tier.name == "AUTHORITATIVE"
    assert substantiates(tier, ClaimKind.DEPRECATION)


def test_widening_is_refused_for_anything_but_a_model():
    pkg = Dependency(kind="package", canonical_name="gradio", registry="pypi",
                     registry_id="gradio")
    assert pkg.subject_with_provider(["evil.test"]).official_domains == ("pypi.org",)


# ------------------------------------------------------------- replacements

def test_a_named_replacement_is_confirmed_against_the_same_catalogue():
    a = FakeAdapter("groq", "Groq", GROQ, {
        "llama-3.3-70b-versatile": entry("llama-3.3-70b-versatile", "deprecated",
                                         "08/16/26", ["openai/gpt-oss-120b", "gone"]),
        "openai/gpt-oss-120b": entry("openai/gpt-oss-120b", "available"),
    })
    r = probe_model_dependency(model("llama-3.3-70b-versatile"), today=TODAY,
                               adapters=[a])
    names = [v["name"] for v in verified_replacements(r, adapters=[a])]
    assert names == ["openai/gpt-oss-120b"], "a successor the vendor no longer lists is dropped"


# ---------------------------------------------------------------- date parsing

def test_shutdown_dates_parse_in_the_forms_vendors_actually_use():
    assert parse_shutdown("08/16/26") == date(2026, 8, 16)
    assert parse_shutdown("2026-08-16") == date(2026, 8, 16)
    assert parse_shutdown("August 16, 2026") == date(2026, 8, 16)
    assert parse_shutdown("") is None
    assert parse_shutdown("soon") is None


def test_a_model_id_in_a_coding_question_counts_as_executed():
    """It is passed to an API at run time, so a retired id fails at call time — not a
    wording problem."""
    dep = model("m", kind_locs=3, otype="CODING_QUESTIONS")
    assert len(dep.questions_that_execute_it) == 3
    mcq = model("m", kind_locs=3, otype="OBJECTIVE_QUESTIONS")
    assert len(mcq.questions_that_execute_it) == 0
