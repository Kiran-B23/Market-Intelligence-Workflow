"""What a course PROMISES, checked against what it covers.

Every other stage is confined to what the curriculum already contains. `extract` builds
the inventory from course content, so anything not taught has no row; `gaps` exists
precisely because of that, and reads `registry/topics.yaml` — an input that does not come
from the courses — to ask whether the material is complete against a subject area.

This asks a third question, and it needs a third input for the same reason: **is the
course complete against its own stated outcomes?** An outcome is a promise made to a
student ("you will be able to build a retrieval pipeline"). Nothing in the exports or the
workbooks records it as a promise, so it cannot be derived — `registry/outcomes.yaml` is
where a human writes it down, exactly as `topics.yaml` is where a human writes down which
areas are worth watching.

**It reuses the areas rather than inventing a second trust path.** An outcome names the
areas that serve it, and those areas already carry the discipline: two independent
official sources, `MIN_CORROBORATION`, headings read structurally by `probe/frontier.py`,
and a refusal to report anything a second vendor does not document. So an outcome finding
inherits all of it and adds one thing the system could not say before — *which promise*
the gap threatens.

What it will not do:

* **It does not invent outcomes.** An empty `outcomes.yaml` reports nothing, the same
  way an area with one source reports nothing and says so. A promise the team has not
  written down is not a promise this system gets to guess at.
* **It does not duplicate S11.** S11 says a topic is missing. This says a PROMISE rests
  on an area with holes in it — one finding per (outcome, area), not per topic, because
  "you promise X and the area it needs has four documented parts you do not teach" is
  one decision.
* **It does not measure at the wrong grain.** Session-level coverage was the obvious
  design and it says nothing: all three courses fall inside all ten declared areas,
  because `scope_terms` match loosely and a course that mentions retrieval once is
  "inside" retrieval. What separates a promise that holds from one that does not is
  which of the area's corroborated topics the course teaches, which `find_gaps` has
  already computed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from miw.analyse import notes
from miw.analyse.gaps import Area, _slug
from miw.schema import Dependency, Finding, Location, utcnow

OUTCOMES_PATH = Path("registry") / "outcomes.yaml"


class OutcomesUnreadable(Exception):
    """The human-owned file could not be parsed. Reported, never swallowed."""


@dataclass
class Outcome:
    """One promise a course makes, and the areas that serve it."""
    outcome_id: str
    course: str
    statement: str
    areas: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class OutcomeStats:
    declared: int = 0
    courses: list = field(default_factory=list)
    unknown_areas: list = field(default_factory=list)
    served: int = 0
    findings: int = 0


@dataclass
class OutcomeReport:
    findings: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    considered: set = field(default_factory=set)
    stats: OutcomeStats = field(default_factory=OutcomeStats)


def load_outcomes(path: Path | str = OUTCOMES_PATH) -> list[Outcome]:
    """Read the human-owned outcomes file. Absent or empty is a valid state."""
    import yaml

    p = Path(path)
    if not p.exists():
        return []
    try:
        doc = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        # Hand-edited, so a typo is a matter of time. A malformed file must not take
        # down the stage that reads it - the same discipline `sheets.read_all` applies
        # to a workbook it cannot parse. Reported by the caller, which has stderr.
        raise OutcomesUnreadable(f"{p}: {exc}") from None
    if not isinstance(doc, dict):
        raise OutcomesUnreadable(f"{p}: expected a mapping with an `outcomes:` key")
    out = []
    for row in doc.get("outcomes") or []:
        if not isinstance(row, dict):
            continue
        course, statement = (row.get("course") or "").strip(), (row.get("statement") or "").strip()
        if not course or not statement:
            continue
        out.append(Outcome(
            outcome_id=(row.get("outcome_id") or _slug(statement))[:64],
            course=course, statement=statement,
            areas=[str(a) for a in (row.get("areas") or [])],
            notes=(row.get("notes") or "").strip()))
    return out


def outcome_dependency(o: Outcome, area: Area) -> Dependency:
    """A Dependency standing for a promise, so nothing downstream needs changing.

    Same device `gaps.topic_dependency` uses and for the same reason: a Finding's
    identity is (dep_id, signal) and its note is composed from a Dependency. Never
    written to the inventory — the inventory records what the curriculum USES, and a
    promise is not a tool.
    """
    src = area.sources[0] if area.sources else None
    return Dependency(
        kind="outcome",
        canonical_name=o.statement[:80],
        dep_id=f"outcome:{_slug(o.course)}:{o.outcome_id}:{area.area_id}",
        homepage=(src.url if src else ""), docs_url=(src.url if src else ""),
        official_domains=list(src.official_domains) if src else [],
        vendor=(src.subject if src else ""), watch_tier="mention-only",
        review_status="proposed",
        notes=f"promised by {o.course}; served by {area.title}")


def _location(o: Outcome, area: Area) -> Location:
    """The promise itself is the place. There is no session to point at — that is the
    finding."""
    return Location(
        course=o.course, topic_name=area.title, unit_id=o.outcome_id,
        unit_name=o.statement[:120], content_id=f"outcome:{o.outcome_id}",
        field_path="registry/outcomes.yaml", evidence_source="outcome_declared",
        object_type="LEARNING_RESOURCE")


def check_outcomes(outcomes: Iterable[Outcome], areas: Iterable[Area],
                   gap_findings: Iterable, gap_rows: Iterable) -> OutcomeReport:
    """Which promises rest on an area the course has real holes in.

    Measured against the TOPICS a gap run found missing, not against whether any session
    touches the area. Session-level coverage was tried first and says nothing: all three
    courses fall inside all ten declared areas, because `scope_terms` match loosely and
    a course that mentions retrieval once is "inside" retrieval. What distinguishes a
    promise that holds from one that does not is which of the area's corroborated topics
    the course actually teaches — which `find_gaps` has already computed, under the
    two-independent-sources rule.
    """
    rep = OutcomeReport()
    by_id = {a.area_id: a for a in areas}
    outcomes = list(outcomes)
    rep.stats.declared = len(outcomes)
    rep.stats.courses = sorted({o.course for o in outcomes})

    # area -> course -> the topics that area is missing there, and the citations that
    # established each one exists. The coverage half is first-hand - our own curriculum,
    # read by us - but "this is a documented part of the area" is a claim about the
    # world, and `verify` is right to refuse a finding that asserts it uncited. So the
    # S11 claims travel with it: same sources, same tiers, already corroborated by two
    # independent vendors under `MIN_CORROBORATION`.
    holes: dict = {}
    cites: dict = {}
    rows = {r.get("dep_id"): r for r in gap_rows}
    for f in gap_findings:
        row = rows.get(getattr(f, "dep_id", ""))
        if not row:
            continue
        area_id = row.get("area_id")
        for course in (getattr(f, "courses", None) or []):
            holes.setdefault(area_id, {}).setdefault(course, []).append(
                getattr(f, "canonical_name", ""))
            for c in (getattr(f, "claims", None) or []):
                cites.setdefault((area_id, course), []).append(c)

    for o in outcomes:
        for area_id in o.areas:
            area = by_id.get(area_id)
            if area is None:
                note = f"{o.outcome_id}: no area named {area_id!r} in topics.yaml"
                if note not in rep.stats.unknown_areas:
                    rep.stats.unknown_areas.append(note)
                continue
            missing = sorted(set((holes.get(area_id) or {}).get(o.course) or []))
            dep = outcome_dependency(o, area)
            rep.considered.add(dep.dep_id)
            if not missing:
                rep.stats.served += 1
                continue

            named = ", ".join(f"`{m}`" for m in missing[:4])
            more = f" (and {len(missing) - 4} more)" if len(missing) > 4 else ""
            one = len(missing) == 1
            loc = _location(o, area)
            # Deduped on (url, quote): one topic's sources routinely document several,
            # and a finding that cites the same sentence four times reads as padding.
            # The DECLARED domains for each source, not the URL's own host — see
            # `gaps.Source.subject_of`: folding the host in would make every entry in
            # the registry authoritative about itself.
            declared_domains = {src.url: tuple(src.official_domains)
                                for src in area.sources}
            claims, seen = [], set()
            for c in cites.get((area_id, o.course), []):
                key = (c.source_url, c.quote)
                if key not in seen and c.substantiating:
                    seen.add(key)
                    claims.append(c)
            f = Finding(
                dep_id=dep.dep_id, canonical_name=o.statement[:80],
                signal="S14", signal_label="Promised outcome not covered",
                kind_of_signal="opportunity",
                severity="high" if len(missing) > 2 else "medium",
                summary=(f"{o.course} promises \u201c{o.statement}\u201d, which rests on "
                         f"{area.title} \u2014 and {len(missing)} corroborated "
                         f"{'topic is' if one else 'topics are'} taught in no session "
                         f"of this course."),
                courses=[o.course], locations=[loc], claims=claims,
                # Set here rather than by `scope_locations`, which the gap stages do not
                # run: a promise has exactly one place, the line that declares it, and
                # the topics it is missing are named in the recommendation.
                affects_counts={loc.object_type: 1}, affects_total=1,
                affected_urls=sorted({c.source_url for c in claims})
                or [s.url for s in area.sources[:3]],
                raised_at=utcnow())
            f.recommendation = (
                f"Decide whether {o.course} still promises this. If it does, "
                f"{named}{more} {'is a documented part' if one else 'are documented parts'} "
                f"of {area.title} that no session covers; if it does not, retire the "
                f"outcome in "
                f"`registry/outcomes.yaml` rather than leaving it unmet.")
            notes.compose(dep, f)
            rep.findings.append(f)
            rep.rows.append({
                "dep_id": dep.dep_id, "outcome_id": o.outcome_id, "course": o.course,
                "statement": o.statement, "area_id": area.area_id,
                "area_title": area.title, "missing_topics": missing,
                # Same shape the S11 rows use, because `main.py verify` reads this
                # sidecar to re-classify every citation without trusting the tier the
                # artifact records. A bare URL list crashed it.
                "sources": [{"url": c.source_url, "subject": c.subject_name,
                             "official_domains": sorted(
                                 declared_domains.get(c.source_url, ()))}
                            for c in claims]})
    rep.stats.findings = len(rep.findings)
    return rep


def starting_point(coverage: dict) -> str:
    """A YAML skeleton seeded with each course's OBSERVED areas.

    Deliberately not a guess at anyone's outcomes: it lists which areas each course's
    sessions already fall inside, so a curriculum owner turns "this course touches these
    six areas" into "this course promises these things, served by these areas". The
    promises are theirs to write; the arithmetic is ours.
    """
    lines = ["# Outcomes a course promises, and the areas that serve them.",
             "#",
             "# Human-owned, like `topics.yaml`, and for the same reason: an outcome is",
             "# a promise made to a student, and nothing in the course exports or the",
             "# workbooks records it as one. An empty file reports nothing.",
             "#",
             "# Each `areas` entry must name an `area_id` from `topics.yaml`, so the",
             "# corroboration rules there apply unchanged - two independent official",
             "# sources, or the area reports nothing and says so.",
             "#",
             "# outcomes:",
             "#   - outcome_id: build-a-rag-pipeline",
             "#     course: Building LLM Applications",
             "#     statement: Build a retrieval-augmented pipeline end to end",
             "#     areas: [embeddings-and-retrieval, evaluation-and-observability]",
             "#",
             "# Observed today - which areas each course's sessions already fall inside.",
             "# A starting point for the `areas` lists above, not a set of outcomes:"]
    for course, areas in sorted((coverage.get("by_course") or {}).items()):
        touched = sorted(a for a, n in areas.items() if n)
        lines.append(f"#   {course}: {', '.join(touched) if touched else '(none)'}")
    lines += ["", "outcomes: []", ""]
    return "\n".join(lines)
