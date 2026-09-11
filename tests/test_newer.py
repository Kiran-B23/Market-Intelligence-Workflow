"""S12 — a newer option from a vendor we already use.

The rule this module lives or dies by is that **a set difference is not news**. Diffed
raw against the inventory the vendors list 45 models we do not teach and n8n ships 527
nodes we do not teach, and almost all of that is deliberate curriculum scoping rather
than change. So everything here is about what APPEARED since the last look, and about
the filters that keep the appeared set down to things a reviewer could act on.
"""
import pytest

from miw.analyse.newer import base_node, family, find_newer
from miw.probe.catalogue import CatalogueEntry
from miw.schema import Dependency, Location
from miw.state import State
from miw.vendors.base import Catalogue


class _Adapter:
    key, vendor = "acme", "Acme"
    official_domains = ("acme.test",)

    def __init__(self, entries):
        self._entries = entries

    def catalogue(self):
        return Catalogue(vendor=self.vendor, entries=self._entries, ok=True,
                         supported=True, sources=["https://acme.test/models"])


def _entry(eid, status="available", price="", quote=None):
    return CatalogueEntry(entry_id=eid, status=status,
                          quote=quote or f"{eid} | available | general purpose model",
                          evidence_url="https://acme.test/models", price=price)


def _dep(name, *, kind="model", sessions=(4,), course="Intro to Gen AI"):
    locs = [Location(course=course, topic_name="T", unit_id="u", unit_name="Unit",
                     content_id="c", field_path="f", evidence_source="model_id",
                     object_type="CODING_QUESTIONS", session_no=s) for s in sessions]
    return Dependency(kind=kind, canonical_name=name, locations=locs)


@pytest.fixture
def st(tmp_path):
    """A database of its own per test.

    `State()` defaults to the real `state/miw.db`, and these tests WRITE snapshots — the
    whole signal is a diff against stored state, so a shared database would both pollute
    the developer's own baseline and let one test seed another's. Every other state test
    in this suite passes an explicit path for the same reason.
    """
    s = State(tmp_path / "t.db")
    yield s
    s.close()


def _run(st, entries, deps, **kw):
    """Never touches n8n: the node path is exercised in its own tests."""
    return find_newer(deps, st, adapters=[_Adapter(entries)],
                      upstream={"ok": True, "nodes": [], "node_paths": {}}, **kw)


# ------------------------------------------------------------------ the "new since" rule

def test_the_first_look_seeds_a_baseline_and_raises_nothing(st):
    """There is nothing to compare against, so there is nothing to say.

    A quiet first run is the rule working. It has to be reported as such, or it reads
    as a broken feature — which is why `cmd_gaps` prints FIRST LOOK rather than a zero.
    """
    ents = {"m-1": _entry("m-1"), "m-2": _entry("m-2")}
    rep = _run(st, ents, [_dep("m-1")])
    assert rep.findings == []
    assert rep.stats.seeded and "acme" in rep.stats.seeded[0]
    assert st.snapshot("catalogue:acme") == {"m-1", "m-2"}


def test_an_unchanged_catalogue_raises_nothing(st):
    ents = {"m-1": _entry("m-1"), "m-2": _entry("m-2")}
    deps = [_dep("m-1")]
    _run(st, ents, deps)                      # seed
    again = _run(st, ents, deps)
    assert again.findings == [] and again.stats.appeared == 0
    assert not again.stats.seeded, "the second look is not a first look"


def test_a_dry_run_must_not_consume_the_baseline(st):
    """The snapshot IS the state this signal depends on.

    A dry run that saved it would silently spend the one baseline it was previewing
    against, and the next real run would find nothing new and report nothing — with no
    error anywhere to say why.
    """
    ents = {"m-1": _entry("m-1")}
    _run(st, ents, [_dep("m-1")], persist=False)
    assert st.snapshot("catalogue:acme") is None
    second = _run(st, ents, [_dep("m-1")], persist=False)
    assert second.stats.seeded, "still a first look, because nothing was written"


def test_a_row_that_appeared_raises_one_finding_placed_on_the_siblings_sessions(st):
    """Placement here is exact, unlike S11: the sessions come from the inventory."""
    taught = _dep("acme-2.0-flash", sessions=(8, 12))
    _run(st, {"acme-2.0-flash": _entry("acme-2.0-flash")}, [taught])   # seed
    grown = {"acme-2.0-flash": _entry("acme-2.0-flash"),
             "acme-3.0-flash": _entry("acme-3.0-flash", price="$0.05 input")}
    rep = _run(st, grown, [taught])

    assert len(rep.findings) == 1
    f = rep.findings[0]
    assert f.signal == "S12" and f.kind_of_signal == "opportunity"
    assert f.canonical_name == "acme-3.0-flash"
    assert sorted({l.session_no for l in f.locations}) == [8, 12]
    assert f.is_substantiated and f.claims[0].quote
    # It says consider, never replace: nothing here measures which is better.
    assert "Consider whether" in f.recommendation
    assert "replace" not in f.recommendation.lower()
    assert "acme-2.0-flash is still served" in f.recommendation


# ------------------------------------------------------------------ the filters

def test_a_new_row_in_no_family_we_teach_is_not_a_finding(st):
    """Otherwise the whole catalogue qualifies — a Saudi-Arabic TTS voice is a real
    vendor row and has nothing to do with any session."""
    taught = _dep("acme-2.0-flash")
    _run(st, {"acme-2.0-flash": _entry("acme-2.0-flash")}, [taught])
    rep = _run(st, {"acme-2.0-flash": _entry("acme-2.0-flash"),
                    "orpheus-arabic-saudi": _entry("orpheus-arabic-saudi")}, [taught])
    assert rep.findings == [] and rep.stats.no_sibling == 1


def test_a_row_we_already_teach_is_not_a_finding(st):
    taught = [_dep("acme-2.0-flash"), _dep("acme-3.0-flash")]
    _run(st, {"acme-2.0-flash": _entry("acme-2.0-flash")}, taught)
    rep = _run(st, {"acme-2.0-flash": _entry("acme-2.0-flash"),
                    "acme-3.0-flash": _entry("acme-3.0-flash")}, taught)
    assert rep.findings == [] and rep.stats.already_taught == 1


def test_a_retired_row_is_never_suggested(st):
    """Never propose adopting something the vendor is already sunsetting."""
    taught = _dep("acme-2.0-flash")
    _run(st, {"acme-2.0-flash": _entry("acme-2.0-flash")}, [taught])
    rep = _run(st, {"acme-2.0-flash": _entry("acme-2.0-flash"),
                    "acme-3.0-flash": _entry("acme-3.0-flash", status="shut down")},
               [taught])
    assert rep.findings == []
    # It never even counted as having appeared: retired rows are out of the live set.
    assert rep.stats.appeared == 0


def test_quote_only_pricing_is_not_a_student_path(st):
    taught = _dep("acme-2.0-flash")
    _run(st, {"acme-2.0-flash": _entry("acme-2.0-flash")}, [taught])
    rep = _run(st, {"acme-2.0-flash": _entry("acme-2.0-flash"),
                    "acme-3.0-flash": _entry("acme-3.0-flash", price="Contact Sales")},
               [taught])
    assert rep.findings == [] and rep.stats.not_offerable == 1


def test_an_unreadable_catalogue_is_recorded_not_skipped_quietly(st):
    """S12 depends entirely on these catalogues, so a silent parser break would make it
    report nothing for ever while looking perfectly healthy."""
    class _Broken(_Adapter):
        def catalogue(self):
            return Catalogue(vendor=self.vendor, entries={}, ok=True, supported=False,
                             error="no role-typed catalogue table found")

    rep = find_newer([_dep("m-1")], st, adapters=[_Broken({})],
                     upstream={"ok": True, "nodes": [], "node_paths": {}})
    assert rep.findings == []
    assert rep.stats.unreadable and "role-typed" in rep.stats.unreadable[0]
    assert rep.stats.sources_read == 0


# ------------------------------------------------------------------ n8n specifics

@pytest.mark.parametrize("variant", ["agentV1", "agentV2", "agentToolV3"])
def test_n8n_version_variants_are_one_node(variant):
    """692 shipped node types are 565 distinct nodes. `agentV2` appearing is not a new
    node when `agent` is taught, and counting it as one is pure noise."""
    assert base_node(f"@n8n/n8n-nodes-langchain.{variant}") == \
        f"@n8n/n8n-nodes-langchain.{variant.rstrip('V0123456789')}"


def test_a_new_n8n_node_cites_the_repo_path(st):
    """The node type is OUR derivation from a filename; the path is a line n8n's own
    repository tree contains, which is what `Claim.build` can be handed."""
    taught = _dep("@n8n/n8n-nodes-langchain.agent", kind="n8n_node", sessions=(6,))
    base = {"ok": True, "nodes": ["@n8n/n8n-nodes-langchain.agent"],
            "node_paths": {"@n8n/n8n-nodes-langchain.agent":
                           "packages/@n8n/nodes-langchain/nodes/agents/Agent/Agent.node.ts"}}
    find_newer([taught], st, adapters=[], upstream=base)          # seed

    path = ("packages/@n8n/nodes-langchain/nodes/chains/ChainSummarization/"
            "ChainSummarization.node.ts")
    grown = {"ok": True,
             "nodes": base["nodes"] + ["@n8n/n8n-nodes-langchain.chainSummarization"],
             "node_paths": {**base["node_paths"],
                            "@n8n/n8n-nodes-langchain.chainSummarization": path}}
    rep = find_newer([taught], st, adapters=[], upstream=grown)
    assert len(rep.findings) == 1
    f = rep.findings[0]
    assert f.signal == "S12"
    assert f.claims[0].quote == path
    assert f.claims[0].source_url.endswith(path)
    assert f.is_substantiated, "github is authoritative about what n8n ships"
    assert sorted({l.session_no for l in f.locations}) == [6]


def test_an_n8n_node_in_a_package_we_never_touch_is_not_a_finding(st):
    """A course that builds langchain workflows is not in the market for a CRM
    connector it has never used."""
    taught = _dep("@n8n/n8n-nodes-langchain.agent", kind="n8n_node")
    base = {"ok": True, "nodes": ["@n8n/n8n-nodes-langchain.agent"],
            "node_paths": {"@n8n/n8n-nodes-langchain.agent": "a/Agent.node.ts"}}
    find_newer([taught], st, adapters=[], upstream=base)
    grown = {"ok": True, "nodes": base["nodes"] + ["some-other-pkg.salesforce"],
             "node_paths": {**base["node_paths"],
                            "some-other-pkg.salesforce": "b/Salesforce.node.ts"}}
    rep = find_newer([taught], st, adapters=[], upstream=grown)
    assert rep.findings == [] and rep.stats.no_sibling == 1


# ------------------------------------------------------------------ readability

def test_a_long_session_list_is_counted_not_enumerated():
    """A taught model is often referenced across most of a course, and a sentence
    carrying 37 session numbers is one nobody finishes reading."""
    from miw.analyse.newer import _where
    many = _dep("m", sessions=tuple(range(3, 30)))
    line = _where(many)
    assert "27 sessions" in line and "first is 3" in line
    few = _dep("m", sessions=(4, 9))
    assert _where(few) == "Intro to Gen AI sessions 4, 9"


@pytest.mark.parametrize("a,b,same", [
    ("gemini-2.0-flash", "gemini-3.8-flash", True),
    ("openai/gpt-oss-20b", "openai/gpt-oss-120b", True),
    ("gemini-2.0-flash", "canopylabs/orpheus-v1-english", False),
])
def test_family_is_the_stem_a_vendor_varies(a, b, same):
    assert (family(a) == family(b)) is same


def test_a_cache_without_node_paths_says_so_once(st):
    """A cache written before paths were kept cannot cite anything.

    Reported as one line about the cache, not one line per node: the failure has a
    single cause and a single fix, and 565 identical complaints would bury the other
    sources' real errors.
    """
    taught = [_dep("a.one", kind="n8n_node")]
    seed = {"ok": True, "nodes": ["a.one"], "node_paths": {"a.one": "x/One.node.ts"}}
    find_newer(taught, st, adapters=[], upstream=seed)          # baseline

    stale = {"ok": True, "nodes": ["a.one", "a.two", "a.three"], "node_paths": {}}
    rep = find_newer(taught, st, adapters=[], upstream=stale)
    assert rep.findings == []
    assert len(rep.stats.unreadable) == 1
    assert "predates node paths" in rep.stats.unreadable[0]
    assert rep.stats.uncitable == []
    # And the browsable list is unaffected — it needs no citation, so a cache that
    # cannot be quoted still answers "what ships that we do not teach?".
    assert {r["identifier"] for r in rep.never_covered} == {"a.two", "a.three"}


def test_the_browsable_list_exists_on_the_very_first_look(st):
    """The run where someone is most likely to go looking is the one that raises nothing.

    "What does this vendor list that we do not teach?" needs no baseline to answer, so
    computing it after the seed return left the list empty on exactly that run.
    """
    ents = {"m-1": _entry("m-1"), "m-2": _entry("m-2"), "other-9": _entry("other-9")}
    rep = _run(st, ents, [_dep("m-1")])
    assert rep.stats.seeded, "this is the first look"
    assert rep.findings == []
    assert {r["identifier"] for r in rep.never_covered} == {"m-2", "other-9"}
    # It carries the sibling where there is one, so the list can say why a row matters.
    by_id = {r["identifier"]: r for r in rep.never_covered}
    assert by_id["m-2"]["sibling"] == "m-1"
    assert by_id["other-9"]["sibling"] == ""


def test_the_browsable_list_never_becomes_a_finding(st):
    """It is a coverage view, not news: no severity, no due date, never in the digest."""
    ents = {"m-1": _entry("m-1"), "m-2": _entry("m-2")}
    _run(st, ents, [_dep("m-1")])
    again = _run(st, ents, [_dep("m-1")])
    assert again.never_covered and again.findings == []
    assert all("severity" not in r and "due_by" not in r for r in again.never_covered)


def test_an_implausible_diff_is_distrusted_rather_than_reported(st):
    """A run once reported 564 of 565 n8n nodes as new and wrote 554 findings.

    n8n did not ship 554 nodes in a week. The snapshot was wrong — a half-written cache,
    a schema change, a restored database — and the signal had no way to say so. The
    honest reading of "almost everything is new" is that our baseline is untrustworthy,
    so the run reseeds, raises nothing, and says why. Same discipline as `supported=False`
    in the extractors: an answer we cannot trust must never be dressed as a finding.
    """
    taught = _dep("acme-1.0-flash")
    _run(st, {"acme-1.0-flash": _entry("acme-1.0-flash")}, [taught])      # tiny baseline
    flood = {f"acme-{i}.0-flash": _entry(f"acme-{i}.0-flash") for i in range(1, 40)}
    rep = _run(st, flood, [taught])
    assert rep.findings == []
    assert rep.stats.distrusted and "not trustworthy" in rep.stats.distrusted[0]
    # And it reseeded, so the next run compares against reality rather than tripping again.
    assert st.snapshot("catalogue:acme") == set(flood)


def test_a_handful_of_new_rows_in_a_small_catalogue_still_reports(st):
    """The guard must not swallow ordinary weeks. One new model out of Groq's fourteen
    is 7%; one out of two is 50% and just as ordinary — so a share alone is not enough."""
    taught = _dep("acme-1.0-flash")
    _run(st, {"acme-1.0-flash": _entry("acme-1.0-flash")}, [taught])
    rep = _run(st, {"acme-1.0-flash": _entry("acme-1.0-flash"),
                    "acme-2.0-flash": _entry("acme-2.0-flash")}, [taught])
    assert not rep.stats.distrusted
    assert [f.canonical_name for f in rep.findings] == ["acme-2.0-flash"]
