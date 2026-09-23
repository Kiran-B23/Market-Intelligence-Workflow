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


def test_a_configured_adapter_that_stops_role_typing_is_an_incident():
    """`supported=False` from a CONFIGURED adapter means its page was restructured.

    The two cases that used to share this branch are not the same thing. A vendor that
    publishes no readable table has no adapter at all — `miw/vendors/openai.py` records
    which pages were tested and rejected — and that case is covered by the test below,
    where no adapter serves the id and the answer is a quiet `model_provider_unknown`.
    An adapter only exists because its page role-typed once, so one that stops is news.

    It used to `continue` without recording, which made a restructure indistinguishable
    from a healthy check: every model from that vendor read `status="ok"`.
    """
    a = FakeAdapter("x", "X", ["x.test"], supported=False)
    r = probe_model_dependency(model("whatever"), today=TODAY, adapters=[a])
    assert r.status == "inconclusive"
    assert "model_catalogue_unreadable" in r.signals


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


def test_an_announced_shutdown_on_a_still_listed_model_is_a_warning():
    """The advance warning this system exists to give, and had never once given.

    `probe_model_dependency` returned "ok" the moment the status column looked healthy,
    one line before the shutdown date it had already parsed was read. Measured on the
    live artifacts: `model_deprecation_declared` fired 0 times across all five
    production runs, while `model_shutdown_passed` fired twice per run — so every model
    finding the system had ever produced was about an endpoint that was already dead.
    """
    from datetime import date

    from miw.probe.catalogue import CatalogueEntry
    from miw.probe.models import probe_model_dependency
    from miw.schema import Dependency

    entry = CatalogueEntry(entry_id="gemini-3.1-flash-lite", status="deprecated",
                           shutdown_date="May 7, 2027",
                           replacement_ids=["gemini-3.5-flash-lite"],
                           quote="gemini-3.1-flash-lite | May 7, 2026 | May 7, 2027",
                           evidence_url="https://ai.google.dev/gemini-api/docs/deprecations")

    class Cat:
        ok, supported, error = True, True, ""
        entries = {"gemini-3.1-flash-lite": entry}

        def get(self, i):
            return self.entries.get(i)

    class Adapter:
        key, vendor, official_domains = "google_ai", "Google", ("ai.google.dev",)

        def catalogue(self, refresh=False):
            return Cat()

    dep = Dependency(kind="model", canonical_name="gemini-3.1-flash-lite")
    res = probe_model_dependency(dep, today=date(2026, 9, 18), adapters=[Adapter()])
    assert res.status == "changed"
    assert "model_deprecation_declared" in res.signals
    assert "model_shutdown_passed" not in res.signals
    assert "vendor_named_replacement" in res.signals
    assert res.declared_changes[0]["shutdown_date"] == "May 7, 2027"

    # ...and once the date passes it is an outage, not a warning.
    after = probe_model_dependency(dep, today=date(2027, 6, 1), adapters=[Adapter()])
    assert after.status == "broken"
    assert "model_shutdown_passed" in after.signals


def test_a_listed_model_with_no_announced_date_stays_ok():
    """The guard against turning every live model into a warning."""
    from datetime import date

    from miw.probe.catalogue import CatalogueEntry
    from miw.probe.models import probe_model_dependency
    from miw.schema import Dependency

    entry = CatalogueEntry(entry_id="gemini-2.5-flash", status="available",
                           shutdown_date="", quote="gemini-2.5-flash | available")

    class Cat:
        ok, supported, error = True, True, ""

        def get(self, i):
            return entry if i == "gemini-2.5-flash" else None

    class Adapter:
        key, vendor, official_domains = "google_ai", "Google", ("ai.google.dev",)

        def catalogue(self, refresh=False):
            return Cat()

    res = probe_model_dependency(Dependency(kind="model", canonical_name="gemini-2.5-flash"),
                                 today=date(2026, 9, 18), adapters=[Adapter()])
    assert res.status == "ok"
    assert res.signals == ["model_listed_available"]


def test_a_restructured_page_is_inconclusive_not_ok():
    """The one failure mode that looked like success.

    A 500 or a timeout already produced `inconclusive`. A 200 whose table had moved
    returned `supported=False`, which the loop skipped without recording, so every
    model from that vendor silently became `model_provider_unknown` at `status="ok"` -
    indistinguishable from a healthy check, for as long as the page stayed that way.
    """
    from datetime import date

    from miw.probe.models import probe_model_dependency
    from miw.schema import Dependency
    from miw.vendors.base import Catalogue

    def adapter_for(cat):
        class A:
            key, vendor, official_domains = "google_ai", "Google", ("ai.google.dev",)

            def catalogue(self, refresh=False):
                return cat
        return A()

    dep = Dependency(kind="model", canonical_name="gemini-2.5-flash")
    loud = [
        Catalogue(vendor="Google", ok=False, error="ai.google.dev: http 500"),
        Catalogue(vendor="Google", ok=False, error="ai.google.dev: http 429"),
        Catalogue(vendor="Google", ok=False, error="ai.google.dev: ConnectionError"),
        # 200, parsed, but nothing role-types: the page was restructured.
        Catalogue(vendor="Google", ok=True, supported=False,
                  error="no role-typed catalogue table found"),
    ]
    for cat in loud:
        res = probe_model_dependency(dep, today=date(2026, 9, 18),
                                     adapters=[adapter_for(cat)])
        assert res.status == "inconclusive", (cat.error, res.status)
        assert "model_catalogue_unreadable" in res.signals

    # A catalogue that read fine and simply does not serve this id is NOT a failure.
    served = Catalogue(vendor="Google", ok=True, supported=True, entries={})
    res = probe_model_dependency(dep, today=date(2026, 9, 18),
                                 adapters=[adapter_for(served)])
    assert res.status == "ok"
    assert "model_provider_unknown" in res.signals


def test_a_change_in_what_a_model_costs_or_allows_is_news():
    """Both cells were parsed and never compared to last week.

    `probe/catalogue.py` has read `price` and `rate_limit` since it was written; they fed
    `quoted_only` and the agent path and were otherwise discarded. So "Groq halved the
    free quota on Whisper" was a fact the system fetched, parsed and threw away every
    run — on the signal class a student notices first.
    """
    from datetime import date

    from miw.probe.catalogue import CatalogueEntry
    from miw.probe.models import probe_model_dependency
    from miw.schema import Dependency
    from miw.state import State

    def cat_with(price, rate):
        entry = CatalogueEntry(entry_id="whisper-large-v3", status="available",
                               price=price, rate_limit=rate,
                               quote="whisper-large-v3 | available")

        class Cat:
            ok = supported = True
            error = ""

            def get(self, i):
                return entry if i == "whisper-large-v3" else None

        class Adapter:
            key, vendor, official_domains = "groq", "Groq", ("groq.com",)

            def catalogue(self, refresh=False):
                return Cat()
        return Adapter()

    dep = Dependency(kind="model", canonical_name="whisper-large-v3")
    st = State(":memory:")
    try:
        # First sight is a baseline and raises nothing — the rule every diff here uses.
        first = probe_model_dependency(dep, today=date(2026, 9, 23), state=st,
                                       adapters=[cat_with("$0.04 per hour",
                                                          "400K ASH 400 RPM")])
        assert "model_price_changed" not in first.signals
        assert "model_rate_limit_changed" not in first.signals

        # Unchanged terms stay quiet.
        same = probe_model_dependency(dep, today=date(2026, 9, 23), state=st,
                                      adapters=[cat_with("$0.04 per hour",
                                                         "400K ASH 400 RPM")])
        assert "model_price_changed" not in same.signals

        # A price rise and a quota cut are both news, and both quote the page.
        moved = probe_model_dependency(dep, today=date(2026, 9, 23), state=st,
                                       adapters=[cat_with("$0.08 per hour",
                                                          "200K ASH 400 RPM")])
        assert moved.status == "changed"
        assert "model_price_changed" in moved.signals
        assert "model_rate_limit_changed" in moved.signals
        assert "$0.04 per hour" in moved.detail and "$0.08 per hour" in moved.detail
        assert "400K ASH" in moved.detail and "200K ASH" in moved.detail
    finally:
        st.close()


def test_terms_drift_is_checked_even_while_the_model_is_perfectly_alive():
    """The retirement branch returns early, and a model can be alive and twice the
    price."""
    from datetime import date

    from miw.probe.catalogue import CatalogueEntry
    from miw.probe.models import probe_model_dependency
    from miw.schema import Dependency
    from miw.state import State

    def adapter(price):
        entry = CatalogueEntry(entry_id="m", status="available", price=price,
                               quote="m | available")

        class Cat:
            ok = supported = True
            error = ""

            def get(self, i):
                return entry
        return type("A", (), {"key": "groq", "vendor": "Groq",
                              "official_domains": ("groq.com",),
                              "catalogue": lambda self, refresh=False: Cat()})()

    dep = Dependency(kind="model", canonical_name="m")
    st = State(":memory:")
    try:
        probe_model_dependency(dep, today=date(2026, 9, 23), state=st,
                               adapters=[adapter("$1")])
        res = probe_model_dependency(dep, today=date(2026, 9, 23), state=st,
                                     adapters=[adapter("$2")])
        assert "model_listed_available" in res.signals   # still alive
        assert "model_price_changed" in res.signals      # ...and dearer
    finally:
        st.close()
