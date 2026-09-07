"""Artifact merge semantics.

Stage outputs are named by date, which was fine while every run swept everything. Once
runs became scopable it stopped being fine: a scoped run overwrote the day's file with
its own slice, so `probe_<date>.json` came to hold 91 Gen-AI-only results in place of
the 229 from the weekly sweep, and the digest presented that partial audit as the
week's state.

The fix is to merge rather than replace. A scoped run refreshes the entries it actually
examined and leaves the rest untouched, so the day's file always represents the best
current knowledge — with `checked_at` on each entry making staleness visible instead of
invisible.

Every merged artifact also carries `coverage`: which dependencies this run examined,
and when each entry was last refreshed. That is what lets the digest say "these four
courses were checked today, the other two on Monday" rather than implying everything
was just verified.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def _load(path: Path) -> dict:
    if not Path(path).exists():
        return {}
    try:
        return json.loads(Path(path).read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _index(rows: list[dict], key: str) -> dict[str, dict]:
    return {r[key]: r for r in rows if r.get(key)}


def merge_by_dep(path: Path, *, new_rows: list[dict], examined: set[str],
                 meta: dict[str, Any], rows_key: str,
                 key: str = "dep_id") -> dict:
    """Merge `new_rows` into the artifact at `path`, keyed on `dep_id`.

    Entries for dependencies this run examined are replaced — including being *dropped*
    when the run examined a dependency and produced nothing for it, which is how a
    resolved finding disappears. Entries for dependencies the run did not look at are
    carried forward verbatim.
    """
    prev = _load(path)
    kept = [r for r in (prev.get(rows_key) or [])
            if r.get(key) and r[key] not in examined]
    merged = kept + new_rows

    coverage = dict(prev.get("coverage") or {})
    stamp = meta.get("run_at") or meta.get("probed_at") or meta.get("analysed_at") or ""
    for dep_id in examined:
        coverage[dep_id] = stamp

    out = {**prev, **meta, rows_key: merged, "coverage": coverage,
           "examined_this_run": sorted(examined),
           "carried_forward": len(kept)}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(out, indent=2, default=str))
    return out


def coverage_summary(path: Path, run_stamp: str = "") -> dict:
    """How fresh the artifact is, split by whether this run refreshed an entry."""
    data = _load(path)
    cov = data.get("coverage") or {}
    if not cov:
        return {"tracked": 0, "fresh": 0, "stale": 0, "oldest": ""}
    fresh = sum(1 for v in cov.values() if run_stamp and v == run_stamp)
    stamps = sorted(v for v in cov.values() if v)
    return {"tracked": len(cov), "fresh": fresh, "stale": len(cov) - fresh,
            "oldest": stamps[0] if stamps else ""}
