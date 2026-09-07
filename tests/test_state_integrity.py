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
        for fid, dep in (("f-llm", "dep-llm"), ("f-pse", "dep-pse")):
            st.classify_finding(finding_id=fid, dep_id=dep, signal="S1",
                                severity="critical", fingerprint="fp", now="t0")

        # A run that only examined the PSE dependency and found nothing wrong there.
        resolved = st.resolve_absent(seen_ids=set(), now="t1",
                                     examined_dep_ids={"dep-pse"})
        assert [r["dep_id"] for r in resolved] == ["dep-pse"]

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

        def fake(dep, probe=None):
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
