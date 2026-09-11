"""Regressions for three bugs that made the system quietly lie.

Each of these produced a plausible-looking digest that was wrong, which is worse than
a crash: nobody investigates a report that reads fine.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from miw.artifacts import merge_by_dep
from miw.schema import Dependency, Location, ProbeResult, ResearchResult
from miw.state import State


def _state(tmp):
    return State(Path(tmp) / "t.db")


# --------------------------------------------------------------- bug #3

def test_a_scoped_run_never_resolves_what_it_did_not_examine():
    """Silence is not evidence of repair. A run over one course reported every other
    course's open findings as fixed."""
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        for fid, dep in (("f-llm", "dep-llm"), ("f-two", "dep-two")):
            st.classify_finding(finding_id=fid, dep_id=dep, signal="S1",
                                severity="critical", fingerprint="fp", now="t0")

        # A run that examined only one dependency and found nothing wrong there.
        resolved = st.resolve_absent(seen_ids=set(), now="t1",
                                     examined_dep_ids={"dep-two"})
        assert [r["dep_id"] for r in resolved] == ["dep-two"]

        open_now = {r["dep_id"] for r in st.conn.execute(
            "SELECT dep_id FROM finding_state WHERE resolved_at IS NULL").fetchall()}
        assert open_now == {"dep-llm"}, "the unexamined finding must stay open"
        st.close()


def test_unscoped_run_still_resolves_everything_it_examined():
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        st.classify_finding(finding_id="f1", dep_id="d1", signal="S1",
                            severity="high", fingerprint="fp", now="t0")
        resolved = st.resolve_absent(set(), "t1", examined_dep_ids={"d1"})
        assert len(resolved) == 1
        st.close()


# --------------------------------------------------------------- bug #4

def test_scoped_artifact_merge_keeps_out_of_scope_rows():
    """A scoped run overwrote the day's file with its own slice, so the digest showed a
    partial audit as the week's state."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "findings.json"
        merge_by_dep(path, new_rows=[{"dep_id": "a", "n": 1}, {"dep_id": "b", "n": 1}],
                     examined={"a", "b"}, meta={"run_at": "t0"}, rows_key="findings")
        out = merge_by_dep(path, new_rows=[{"dep_id": "c", "n": 2}],
                           examined={"c"}, meta={"run_at": "t1"}, rows_key="findings")
        ids = sorted(r["dep_id"] for r in out["findings"])
        assert ids == ["a", "b", "c"], "out-of-scope rows must survive"
        assert out["carried_forward"] == 2
        assert out["coverage"]["a"] == "t0" and out["coverage"]["c"] == "t1", \
            "coverage must show which rows are stale"


def test_re_examining_a_dependency_with_no_finding_drops_it():
    """That is how a genuinely resolved finding disappears."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "f.json"
        merge_by_dep(path, new_rows=[{"dep_id": "a"}], examined={"a"},
                     meta={"run_at": "t0"}, rows_key="findings")
        out = merge_by_dep(path, new_rows=[], examined={"a"},
                           meta={"run_at": "t1"}, rows_key="findings")
        assert out["findings"] == []


# --------------------------------------------------------------- bug #5

def test_first_seen_survives_an_extract_rebuild():
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        st.upsert_dep_state([("d1", "Alpha", "2026-01-01T00:00:00")])
        st.upsert_dep_state([("d1", "Alpha", "2026-09-07T00:00:00")])   # re-extract
        assert st.dep_state()["d1"]["first_seen"] == "2026-01-01T00:00:00"
        st.close()


def test_rotation_advances_across_runs():
    """Ordering on `Dependency.first_seen` did not rotate: extract regenerates that
    field, so the same handful were researched every week and the rest never were."""
    from miw.research.agent import research_all

    deps = [Dependency(kind="service", canonical_name=f"tool{i}", watch_tier="critical",
                       official_domains=[f"tool{i}.test"],
                       locations=[Location(course="C", topic_name="t", unit_id="u",
                                           unit_name="n", content_id="",
                                           field_path="f",
                                           evidence_source="solution_import",
                                           object_type="CODING_QUESTIONS",
                                           session_no=1)])
            for i in range(6)]

    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        st.upsert_dep_state([(d.dep_id, d.canonical_name, "2026-01-01") for d in deps])

        def fake(dep, probe=None, **kw):
            return ResearchResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name)

        picked = []
        with patch("miw.research.agent.research_dependency", side_effect=fake):
            for week in range(3):
                res = research_all(deps, {}, rotation_slice=2, max_deps=2,
                                   dep_state=st.dep_state())
                names = [r.canonical_name for r in res]
                picked.append(names)
                st.mark_researched([r.dep_id for r in res], f"2026-0{week + 2}-01")

        flat = [n for wk in picked for n in wk]
        assert len(set(flat)) == 6, f"rotation repeated instead of advancing: {picked}"
        assert not set(picked[0]) & set(picked[1]), "week 2 must pick different deps"
        st.close()


def test_research_results_are_joined_by_dep_id_never_by_position():
    """`research_all` returns results in ROTATION order, not input order — it sorts by
    `last_researched_at`. Anything pairing the two lists positionally would attach one
    dependency's alternatives to another, which is how a scraping API came to be
    offered as a replacement for a text-to-speech tool in a throwaway script.

    The production path joins on `dep_id`; this asserts that and pins the ordering
    property that makes it necessary.
    """
    import inspect
    import main

    src = inspect.getsource(main.cmd_analyse)
    assert "research.get(dep.dep_id)" in src, "analyse must join research by dep_id"
    assert "zip(" not in src, "positional pairing would silently mismatch"

    # `_load_research` returns a dict keyed by dep_id, so positional access is not even
    # available to a caller.
    load_src = inspect.getsource(main._load_research)
    assert "out[r[\"dep_id\"]]" in load_src


def test_research_all_returns_results_in_rotation_order_not_input_order():
    """The property that makes positional pairing dangerous, asserted directly."""
    from unittest.mock import patch
    from miw.research import agent
    from miw.schema import Dependency, Location, ResearchResult

    def mk(name, session):
        return Dependency(
            kind="service", canonical_name=name, official_domains=[f"{name.lower()}.example"],
            watch_tier="critical",
            locations=[Location(course="c", topic_name="t", unit_id="u", unit_name="n",
                                content_id="q", field_path="f",
                                evidence_source="markdown",
                                object_type="LEARNING_RESOURCE", session_no=session)])

    deps = [mk("Zeta", 9), mk("Alpha", 3)]        # input order Zeta, Alpha

    def fake(dep, probe=None, **kw):
        return ResearchResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name)

    with patch("miw.research.agent.research_dependency", side_effect=fake):
        res = agent.research_all(deps, {}, max_deps=2, rotation_slice=2)

    assert [r.canonical_name for r in res] == ["Alpha", "Zeta"], \
        "rotation sorts by name when the clock is equal, so order differs from input"
    # And every result still carries its own identity.
    by_id = {d.dep_id: d.canonical_name for d in deps}
    assert all(by_id[r.dep_id] == r.canonical_name for r in res)


def test_a_scoped_run_on_a_new_day_carries_the_previous_artifact_forward(tmp_path):
    """A narrow run must not truncate the day's view of the world.

    `merge_by_dep` merges into *today's* file, so on the first run of a new day there
    was nothing to carry forward and a `--limit 3` run left the artifact holding three
    rows. `main.py verify` reads the LATEST probe artifact to rebuild provider-widened
    authority, so it then reported false trust violations for every dependency outside
    that scope — the tool that exists to prove the artifacts are sound was made unsound
    by a legitimate scoped run.
    """
    import json

    from miw.artifacts import latest_sibling, merge_by_dep

    yesterday = tmp_path / "probe_2026-01-01.json"
    yesterday.write_text(json.dumps({
        "results": [{"dep_id": "a", "canonical_name": "A"},
                    {"dep_id": "b", "canonical_name": "B"},
                    {"dep_id": "c", "canonical_name": "C"}],
        "coverage": {"a": "x", "b": "x", "c": "x"}}))

    today = tmp_path / "probe_2026-01-02.json"
    assert latest_sibling(today) == yesterday

    out = merge_by_dep(today, new_rows=[{"dep_id": "a", "canonical_name": "A2"}],
                       examined={"a"}, meta={"run_at": "y"}, rows_key="results")
    ids = {r["dep_id"] for r in out["results"]}
    assert ids == {"a", "b", "c"}, "b and c were carried forward from yesterday"
    assert out["carried_forward"] == 2
    assert next(r for r in out["results"] if r["dep_id"] == "a")["canonical_name"] == "A2"


def test_seeding_only_happens_when_todays_artifact_is_absent(tmp_path):
    """Once today's file exists it is the base; yesterday must not resurrect rows."""
    import json

    from miw.artifacts import merge_by_dep

    (tmp_path / "probe_2026-01-01.json").write_text(json.dumps(
        {"results": [{"dep_id": "old", "canonical_name": "Gone"}]}))
    today = tmp_path / "probe_2026-01-02.json"
    today.write_text(json.dumps({"results": [{"dep_id": "a", "canonical_name": "A"}]}))

    out = merge_by_dep(today, new_rows=[], examined={"a"}, meta={"run_at": "y"},
                       rows_key="results")
    assert out["results"] == [], "an examined dep producing nothing is dropped"
    assert not any(r["dep_id"] == "old" for r in out["results"])


def test_a_declared_change_cited_to_a_non_authoritative_source_is_not_attached():
    """A strict claim must not be attached merely because it could be constructed.

    `Claim.build` refuses only an EXCLUDED source, so an `implementation` claim citing
    `npmjs.com` — a canonical registry for versions and existence, not for how a
    vendor's embedded chat protocol changed — was built CORROBORATING and appended
    anyway. The finding then LOOKED cited while resting on nothing, and only
    `main.py verify` noticed. Dropped now, and the drop is recorded.
    """
    from miw.analyse import score
    from miw.schema import Dependency, Location, ProbeResult

    dep = Dependency(kind="n8n_node", canonical_name="@n8n/x.chatTrigger",
                     official_domains=["n8n.io", "docs.n8n.io"],
                     locations=[Location(course="C", topic_name="T", unit_id="u",
                                         unit_name="U", content_id="q",
                                         field_path="[0]",
                                         evidence_source="n8n_workflow",
                                         object_type="CODING_QUESTIONS",
                                         session_no=1)])
    probe = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                        status="ok")
    probe.flag("breaking_change_declared")      # the name PROBE_TO_SIGNAL maps to S9
    probe.declared_changes = [{
        "rule_id": "n8n:x", "title": "Embedded chat now uses a JSON WebSocket format",
        "description": "Every frame is JSON now, which changes the taught snippet.",
        "n8n_version": "3.0.0", "severity": "low",
        # npm is authoritative for a version, not for an implementation change.
        "doc_url": "https://www.npmjs.com/package/@n8n/chat",
    }]

    findings = score.findings_for(dep, probe, None)
    strict = [c for f in findings for c in f.claims]
    assert all(c.substantiating for c in strict), \
        "no attached claim may fail to substantiate its own kind"
    assert any("declared_change_unciteable" in s
               for f in findings for s in (f.probe_signals or [])), \
        "the drop has to be recorded, or it is invisible again"
