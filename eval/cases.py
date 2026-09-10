"""Shared construction of golden cases.

The eval suites and the provider-parity harness must build their fixtures the *same*
way, or the comparison quietly stops comparing the same thing. Two copies of
`_dependency` drifting apart is exactly the failure that would make a parity number
meaningless while still looking healthy, so the loaders live here and both callers
import them.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "eval" / "golden"

from miw.schema import (Claim, ContentRecord, Dependency, Location,   # noqa: E402
                        ProbeResult, ResearchResult, UncitedClaim)


def load(name: str) -> list:
    return json.loads((GOLDEN / f"{name}.json").read_text())


def record(spec: dict) -> ContentRecord:
    return ContentRecord(
        course="EvalCourse", topic_name="t", unit_id="u1", unit_name="Unit",
        unit_type="LEARNING_SET", content_id=spec.get("content_id", "c1"),
        object_type=spec.get("object_type", "LEARNING_RESOURCE"),
        content_type="MARKDOWN", title="Eval", body_text=spec["body_text"],
        field_path="[0].eval", evidence_source=spec.get("evidence_source", "markdown"),
        source_file="eval", session_no=spec.get("session_no", 1))


def dependency(spec: dict) -> Dependency:
    """Build a Dependency from a golden spec. Mutates `spec`, so pass a copy."""
    locs = [Location(course=l.get("course", "EvalCourse"), topic_name="t",
                     unit_id="u1", unit_name=f"Session {l.get('session_no', 1)}",
                     content_id=l.get("content_id", ""), field_path="[0].eval",
                     evidence_source=l["evidence_source"],
                     object_type=l.get("object_type", "LEARNING_RESOURCE"),
                     session_no=l.get("session_no"))
            for l in spec.pop("locations", [])]
    return Dependency(locations=locs, **spec)


def probe_of(case: dict, dep: Dependency):
    """The ProbeResult a golden case declares, or None when it declares none."""
    pspec = dict(case.get("probe") or {})
    if not pspec:
        return None
    return ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name, **pspec)


def research_of(case: dict, dep: Dependency):
    """The ResearchResult a golden case declares, with uncitable claims dropped."""
    if not case.get("research"):
        return None
    res = ResearchResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name)
    for cl in case["research"].get("claims", []):
        try:
            res.claims.append(Claim.build(**cl))
        except (UncitedClaim, TypeError):
            continue
    return res
