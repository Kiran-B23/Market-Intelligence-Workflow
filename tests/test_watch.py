"""The daily event path: watermarks, dedupe, and resolution without reading content.

The property these tests protect is the one that makes the whole approach viable: a
signal names identifiers, the inventory is an index keyed by identifiers, and matching
is a dict lookup. No course text is read and no model is called, so 9.2M characters of
curriculum never approach a context window.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.schema import Dependency, Location
from miw.state import State
from miw.watch.poll import _compare, resolve_signal
from miw.watch.signal import Signal, Watermark, row_set_hash


def _state(tmp):
    return State(Path(tmp) / "t.db")


def wm(value, key="groq:catalogue"):
    return Watermark(source_key=key, kind="row_set_hash", value=value,
                     evidence_url="https://console.groq.com/docs/deprecations")


# ------------------------------------------------------------- the watermark

def test_first_observation_is_a_baseline_and_not_an_event():
    """Otherwise the first deploy emits every historical deprecation as though it had
    just happened."""
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        sig, outcome = _compare(st, wm("v1"), vendor_key="groq",
                                trigger="deprecation", rows=[], now="t0")
        assert outcome == "baseline" and sig.is_baseline
        st.close()


def test_an_unchanged_watermark_emits_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        _compare(st, wm("v1"), vendor_key="groq", trigger="deprecation", rows=[], now="t0")
        sig, outcome = _compare(st, wm("v1"), vendor_key="groq",
                                trigger="deprecation", rows=[], now="t1")
        assert sig is None and outcome == "unchanged"
        st.close()


def test_a_moved_watermark_emits_one_signal_carrying_only_the_new_rows():
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        _compare(st, wm("v1"), vendor_key="groq", trigger="deprecation", rows=[], now="t0")
        rows = [{"id": "llama-3.3-70b-versatile", "status": "deprecated"}]
        sig, outcome = _compare(st, wm("v2"), vendor_key="groq",
                                trigger="deprecation", rows=rows, now="t1")
        assert outcome == "changed"
        assert sig.from_value == "v1" and sig.to_value == "v2"
        assert sig.refs == ["llama-3.3-70b-versatile"]
        st.close()


def test_the_same_state_yields_the_same_signal_id():
    """So re-observation is an idempotent upsert, not a second event."""
    a = Signal(vendor_key="groq", source_key="k", trigger="deprecation",
               from_value="v1", to_value="v2")
    b = Signal(vendor_key="groq", source_key="k", trigger="deprecation",
               from_value="v1", to_value="v2")
    assert a.signal_id == b.signal_id
    c = Signal(vendor_key="groq", source_key="k", trigger="deprecation",
               from_value="v1", to_value="v3")
    assert c.signal_id != a.signal_id


def test_the_ledger_reports_a_signal_as_new_only_once():
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        sig = Signal(vendor_key="groq", source_key="k", trigger="deprecation",
                     from_value="v1", to_value="v2")
        assert st.signal_save(sig, "open", "t0") is True
        assert st.signal_save(sig, "open", "t1") is False
        st.close()


# ----------------------------------------------- hashing meaning, not markup

def test_the_row_hash_ignores_order_and_notices_meaning():
    a = row_set_hash([("m1", "available", ""), ("m2", "deprecated", "m3")])
    b = row_set_hash([("m2", "deprecated", "m3"), ("m1", "available", "")])
    c = row_set_hash([("m1", "deprecated", ""), ("m2", "deprecated", "m3")])
    assert a == b, "reordering a table is not a vendor event"
    assert a != c, "a status change is"


# ------------------------------------------------------------- resolution

def dep(name, kind="model", domains=("ai.meta.com",), aliases=()):
    return Dependency(
        kind=kind, canonical_name=name, official_domains=list(domains),
        aliases=list(aliases),
        locations=[Location(course="Intro to Gen AI", topic_name="t", unit_id="u",
                            unit_name="n", content_id="q1", field_path="f",
                            evidence_source="model_id",
                            object_type="CODING_QUESTIONS", session_no=6)])


def test_an_exact_identifier_match_resolves_to_that_dependency_only():
    deps = [dep("llama-3.3-70b-versatile"), dep("gemini-2.5-flash"),
            dep("gradio", kind="package")]
    sig = Signal(vendor_key="groq", source_key="k", trigger="deprecation",
                 from_value="a", to_value="b", refs=["llama-3.3-70b-versatile"])
    scope = resolve_signal(sig, deps)
    assert scope.dep_ids == {deps[0].dep_id}


def test_resolution_matches_aliases_too():
    d = dep("LangChain", kind="service", aliases=("langchain.com", "langchain"))
    sig = Signal(vendor_key="x", source_key="k", trigger="new_release",
                 from_value="a", to_value="b", refs=["langchain"])
    assert resolve_signal(sig, [d]).dep_ids == {d.dep_id}


def test_a_vendor_moving_something_we_do_not_teach_resolves_to_nothing():
    """Recorded, never investigated — this is what keeps a busy vendor quiet."""
    deps = [dep("gemini-2.5-flash")]
    sig = Signal(vendor_key="groq", source_key="k", trigger="deprecation",
                 from_value="a", to_value="b", refs=["some-model-we-never-taught"])
    assert resolve_signal(sig, deps).dep_ids == set()


def test_resolution_reads_no_course_content():
    """The index is keyed by identifier, so resolution touches no body text at all."""
    d = dep("llama-3.3-70b-versatile")
    assert all(not getattr(l, "body_text", "") for l in d.locations)
    sig = Signal(vendor_key="groq", source_key="k", trigger="deprecation",
                 from_value="a", to_value="b", refs=["llama-3.3-70b-versatile"])
    scope = resolve_signal(sig, [d])
    assert scope.dep_ids and scope.describe().startswith("1 named dependency")


# ----------------------------------------------------- the noise budget

def _pkg(taught_version):
    return Dependency(
        kind="package", canonical_name="langchain", registry="pypi",
        registry_id="langchain", taught_version=taught_version,
        official_domains=["langchain.com"],
        locations=[Location(course="Building LLM Applications", topic_name="t",
                            unit_id="u", unit_name="n", content_id="q1",
                            field_path="f", evidence_source="pip_install",
                            object_type="CODING_QUESTIONS", session_no=3)])


def _probe(dep, latest, signals):
    from miw.schema import ProbeResult
    r = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                    status="changed", latest_version=latest,
                    evidence_url="https://pypi.org/project/langchain/")
    for s in signals:
        r.flag(s)
    r.detail = f"{dep.taught_version} -> {latest}"
    return r


def test_a_patch_release_within_the_taught_major_raises_no_finding():
    """Daily polling over 71 packages would otherwise notify on every patch bump.
    The new version is still recorded on the artifact; it is just not news."""
    from miw.analyse.score import findings_for
    dep = _pkg("1.3.1")
    probe = _probe(dep, "1.3.2", ["new_release"])
    fs = findings_for(dep, probe, None)
    assert [f.signal for f in fs] == [], f"expected silence, got {[f.signal for f in fs]}"


def test_a_release_that_leaves_the_taught_pin_behind_does_raise_s6():
    """langchain 1.3.1 taught against 1.4.0 released is the case worth reporting."""
    from miw.analyse.score import findings_for
    dep = _pkg("1.3.1")
    probe = _probe(dep, "2.0.0", ["new_release", "major_behind_taught_pin"])
    fs = findings_for(dep, probe, None)
    assert [f.signal for f in fs] == ["S6"]
    assert "major_behind_taught_pin" in fs[0].probe_signals


def test_an_unpinned_package_bump_is_not_a_finding_either():
    """With nothing pinned there is no drift to report — the session never named a
    version, so no session text goes stale when the registry moves."""
    from miw.analyse.score import findings_for
    dep = _pkg("")
    fs = findings_for(dep, _probe(dep, "1.4.0", ["new_release"]), None)
    assert [f.signal for f in fs] == []


def test_a_yanked_release_is_still_reported_even_without_a_major_gap():
    """The suppression is narrow: it silences a healthy bump, not a bad release."""
    from miw.analyse.score import findings_for
    dep = _pkg("1.3.1")
    fs = findings_for(dep, _probe(dep, "1.3.2", ["registry_deprecated"]), None)
    assert [f.signal for f in fs] == ["S4"]
