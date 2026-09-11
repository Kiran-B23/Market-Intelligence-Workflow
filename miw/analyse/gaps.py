"""S11 — the topic the curriculum does not teach yet.

Every other finding in MIW starts inside the courses and looks outward: here is a tool
we teach, is it still true? This module starts outside and looks in: here is what the
vendor's own documentation says exists in an area, and here is the part of it that
appears nowhere in our decks.

That inversion is the whole point, and it is why nothing else in the pipeline could
ever have produced an S11. `extract/inventory.py` derives the dependency list FROM the
course content, so the probe, the research stage and the scorer are all confined to
what is already taught. Their answer to "is our prompting session complete?" is
structurally always yes.

The finding has to survive two independent checks, and they are deliberately not the
same check:

* **It has to exist**, and existence is a STRICT claim kind in `miw/trust.py`. The
  quote comes off the vendor's own page through `Claim.build()`, so an S11 can only
  rest on an AUTHORITATIVE source. A blog post, a newsletter or a model's recollection
  cannot raise one. If the page's prose about an item is too thin to quote, the item is
  dropped rather than asserted - a bare heading is a label, not evidence.
* **It has to be absent**, and absence is measured against the workbook, not the JSON
  export. The export carries units, questions and links; it does not carry any deck's
  outline. So `analyse/curriculum.py` reads the same `Course Outline` sheet that settles
  session numbering and asks whether the topic is already in some session's own words.

What comes out names a session, because that is what makes it a task. "Course coverage
has fallen behind" is not something a curriculum reviewer can schedule;
"self-consistency is documented by Google, appears in no session, and belongs in Intro
to Gen AI session 8 (Advanced Prompt Engineering), whose Key Takeaways already list
Zero-shot, One-shot, Few-shot and CoT" is a half-day of work with a known owner.

Two honesty rules are load-bearing:

* A topic already taught ANYWHERE is not a gap, even outside its area. ReAct is a
  prompting technique and Intro to Gen AI teaches it in session 19 as an agent pattern;
  reporting it as a missing prompting technique would be wrong and would be the kind of
  wrong that costs the digest its authority.
* When no session matches the topic's own terms well enough, the finding says the area
  is known and the session is a judgement call, and lists the candidates with scores.
  A confidently wrong session number is worse than an honest shrug.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import yaml

from miw.analyse import notes
from miw.analyse.curriculum import CurriculumIndex, SessionDoc, _tokens
from miw.probe.frontier import enumerate_page, normalise
from miw.schema import Claim, Dependency, Finding, Location, UncitedClaim, utcnow
from miw.trust import ClaimKind, Subject

TOPICS_PATH = Path("registry/topics.yaml")

# S11 is an opportunity, never an outage. A missing topic does not break a student's
# session, so it must never outrank a dead link - `SIGNALS["S11"]` says "low" and this
# module does not raise it. The one thing that moves it is how much of the area is
# missing: a session missing one technique is housekeeping, a session missing most of
# an area is a rewrite, and `severity_for_coverage` is where that judgement lives.
_SEVERITY_BY_MISSING_SHARE = ((0.6, "medium"), (0.3, "low"), (0.0, "info"))

# How much two headings must agree, as name-token Jaccard, to be the same topic. An
# item named by only ONE vendor is not reported at all, and this is the threshold that
# decides "named by another vendor too".
#
# This rule is the whole of the module's precision, and it was added after measuring
# what happened without it. Reading four Google documentation pages as enumerations
# produced 32 findings, of which most were not teachable topics at all:
# "Batch embeddings", "Migration from gemini-embedding-001",
# "Start building with embeddings", "Workarounds for pre-tool text requirements". They
# are real headings, genuinely absent from the curriculum, and completely useless -
# API mechanics and calls to action, not things a session could teach.
#
# What separates a topic from a vendor's API detail is that other vendors name it too.
# So corroboration here is the same discipline the rest of MIW already runs on: one
# source may nominate, an independent official source must confirm. "Independent" is
# enforced structurally - the two sources' authority sets must be disjoint, so two
# Google pages cannot corroborate each other.
#
# Measured at 0.4 on the Google x Microsoft prompting pair: it keeps
# "Clear and specific instructions" ~ "Start with clear instructions" (0.50),
# "Zero-shot vs few-shot prompts" ~ "Few-shot learning" (0.43) and
# "Break down prompts into components" ~ "Break the task down" (0.40), and drops
# "Add context" ~ "Add clear syntax" (0.25) and every pair below it. The cost is
# recall: real topics phrased differently by each vendor ("Grounding and code
# execution" ~ "Provide grounding context", 0.20) are missed. That is the right
# direction to fail in. A digest that reports three real gaps is read; one that reports
# thirty, of which nine are real, is not read twice.
MIN_CORROBORATION = 0.4

# A session may only host a topic if it is about the area at least this much, as a
# fraction of the most on-area session in the whole curriculum. Relative rather than
# absolute because the scale is not comparable between areas: the strongest image
# session scores 1.00 for "image generation" while the strongest prompting session
# scores 0.47 for "prompt engineering", so any fixed floor either admits everything in
# one area or nothing in the other.
#
# What it fixes: a topic was being placed into EVERY course with any session in the
# area, so "Function calling modes" landed in Intro to Gen AI session 3 ("Exploring Gen
# AI Capabilities", affinity 0.13 against a best of 0.64). At 0.4 that session is
# excluded and AI for Finance session 9 ("Integrating Langsmith with the trading Agent",
# 0.36) is kept - which is right: that course teaches an agent that calls tools.
RELATIVE_AREA_FLOOR = 0.4


@dataclass(frozen=True)
class Source:
    """One official page read as an enumeration."""
    url: str
    subject: str
    official_domains: tuple[str, ...] = ()
    levels: tuple[int, ...] = (2,)
    under: str = ""
    list_items: bool = False
    exclude: frozenset = frozenset()

    def subject_of(self) -> Subject:
        """Who speaks officially for this page.

        Only the DECLARED domains, and deliberately not `with_domains_from_urls()`.
        Folding the source URL's own host into the authority set would make every entry
        in `registry/topics.yaml` authoritative about itself, so a newsletter added to
        the registry by mistake would substantiate an EXISTENCE claim - the one thing
        the trust layer exists to prevent. Declaring the domains separately means the
        registry states a claim about authority that `Claim.build` can then refuse.
        """
        return Subject(name=self.subject,
                       official_domains=tuple(self.official_domains),
                       docs_url=self.url)


@dataclass(frozen=True)
class Area:
    """A curriculum area: what it is called, which sessions it covers, who documents it."""
    area_id: str
    title: str
    scope_terms: tuple[str, ...] = ()
    sources: tuple[Source, ...] = ()


def load_areas(path: Path | str = TOPICS_PATH) -> list[Area]:
    """Read `registry/topics.yaml`. Hand-owned; nothing writes it."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    out = []
    for a in raw.get("areas") or []:
        sources = tuple(
            Source(url=(s.get("url") or "").strip(),
                   subject=(s.get("subject") or "").strip(),
                   official_domains=tuple(s.get("official_domains") or ()),
                   levels=tuple(s.get("levels") or (2,)),
                   under=(s.get("under") or "").strip(),
                   list_items=bool(s.get("list_items")),
                   # Case-folded exact match, never substring - the rule
                   # `vendors.Catalogue.get` follows. "Overview" is furniture and
                   # "Overview of retrieval" is a topic.
                   exclude=frozenset(normalise(x) for x in (s.get("exclude") or ())))
            for s in a.get("sources") or [])
        out.append(Area(area_id=(a.get("area_id") or "").strip(),
                        title=(a.get("title") or "").strip(),
                        scope_terms=tuple(a.get("scope_terms") or ()),
                        sources=tuple(s for s in sources if s.url)))
    return [a for a in out if a.area_id and a.sources]


# ---------------------------------------------------------------- the synthetic subject

def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (text or "").lower())).strip("-")


def topic_dependency(area: Area, item_name: str, source: Source) -> Dependency:
    """A Dependency standing for a topic, so the rest of the pipeline needs no changes.

    A Finding's identity is (dep_id, signal) and its note is composed from a Dependency,
    so a gap needs something to point at. This is that something: a `kind="topic"`
    dependency with a stable `topic:` id and the documenting vendor's authority set.

    It is NOT written to the inventory. The inventory is the record of what the
    curriculum uses, and a topic we do not teach is precisely not that - adding it would
    corrupt the one number the whole system is built on. `main.py verify` instead reads
    the authority set back off the gaps artifact, the same way it reads provider
    widening off the probe artifact.
    """
    return Dependency(
        kind="topic",
        canonical_name=item_name,
        dep_id=f"topic:{area.area_id}:{_slug(item_name)}",
        homepage=source.url, docs_url=source.url,
        official_domains=list(source.official_domains),
        vendor=source.subject, watch_tier="mention-only",
        review_status="proposed",
        notes=f"curriculum area: {area.title}")


def severity_for_coverage(missing: int, total: int) -> str:
    """How bad an area's incompleteness is, as a share of what the area contains."""
    share = (missing / total) if total else 0.0
    for floor, sev in _SEVERITY_BY_MISSING_SHARE:
        if share >= floor:
            return sev
    return "info"


# --------------------------------------------------------------- cross-vendor agreement

def agreement(a_name: str, b_name: str) -> float:
    """How much two headings agree that they name the same thing, on 0..1.

    Jaccard over the headings' distinctive words. Deliberately computed on the NAME
    only: including each page's prose collapsed every pair towards zero (the two pages
    share "model", "prompt" and "response" and little else), so the context helps
    placement and hurts identity.
    """
    ta, tb = set(_tokens(a_name)), set(_tokens(b_name))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def independent(a: Source, b: Source) -> bool:
    """Do these two sources speak with separate authority?

    Enforced on the authority sets, not on the URLs: two pages under ai.google.dev are
    the same vendor agreeing with itself, which is not corroboration of anything. A
    hand-written "these are different vendors" flag would be a promise; disjoint
    `official_domains` is a fact the trust layer already relies on.
    """
    return not (set(a.official_domains) & set(b.official_domains))


@dataclass
class Cluster:
    """One topic, as named by two or more independent official sources."""
    members: list = field(default_factory=list)      # [(Source, FrontierItem)]
    agreement: float = 0.0

    @property
    def primary(self):
        """The member whose name is most canonical.

        Shortest name wins: vendors pad a heading with their own framing
        ("Zero-shot vs few-shot prompts") around a term of art ("Few-shot learning"),
        and the shorter one is closer to what a curriculum outline would call it.
        """
        return min(self.members, key=lambda m: (len(m[1].name), m[1].name))

    @property
    def primary_source(self) -> Source:
        return self.primary[0]

    @property
    def name(self) -> str:
        return self.primary[1].name

    @property
    def context(self) -> str:
        """Every source's prose, for placement. More vocabulary, better ranking."""
        return " ".join(i.context for _s, i in self.members)

    def key_set(self) -> frozenset:
        return frozenset(i.key for _s, i in self.members)

    def names_and_keys(self) -> list:
        return [(i.name, i.key) for _s, i in self.members]

    def subjects(self) -> list:
        return sorted({s.subject for s, _i in self.members})

    def aliases(self) -> list:
        return sorted({i.name for _s, i in self.members} - {self.name})


def corroborate(readings: list) -> list:
    """Group items that two or more INDEPENDENT sources name the same way.

    `readings` is [(Source, [FrontierItem])]. An item named by only one source is not
    a candidate at all - see `MIN_CORROBORATION` for why that is the precision rule
    this whole module rests on.
    """
    clusters: dict = {}
    for i, (src_a, items_a) in enumerate(readings):
        for item in items_a:
            found = []
            for j, (src_b, items_b) in enumerate(readings):
                if j == i or not independent(src_a, src_b):
                    continue
                best, score = None, 0.0
                for cand in items_b:
                    sc = agreement(item.name, cand.name)
                    if sc > score:
                        best, score = cand, sc
                if best is not None and score >= MIN_CORROBORATION:
                    found.append((src_b, best, score))
            if not found:
                continue
            cluster = Cluster(members=[(src_a, item)] + [(s, it) for s, it, _sc in found],
                              agreement=min(sc for _s, _it, sc in found))
            # A pair is discovered twice, once from each side. Keyed on the member set
            # so the second discovery updates rather than duplicates - without this an
            # area with two sources reported every corroborated topic twice.
            clusters.setdefault(cluster.key_set(), cluster)
    return list(clusters.values())


# ------------------------------------------------------------------------- the analyser

@dataclass
class GapStats:
    areas: int = 0
    sources_read: int = 0
    sources_unsupported: list = field(default_factory=list)
    items_enumerated: int = 0
    candidates: int = 0                  # corroborated by two independent sources
    uncorroborated_areas: list = field(default_factory=list)
    already_taught: int = 0
    uncitable: list = field(default_factory=list)
    excluded: int = 0
    unplaced: int = 0
    findings: int = 0


def area_coverage(areas: Iterable[Area], index: CurriculumIndex) -> dict:
    """Which indexed sessions fall inside at least one declared area.

    Reported rather than assumed, because this is the one number that says how general
    the analysis actually is. The code is course-agnostic - it indexes every workbook it
    can map and loops every course - but it can only ever look at areas
    `registry/topics.yaml` declares, so an undeclared subject cluster is invisible in
    exactly the way an un-taught topic is invisible to the rest of the pipeline. Making
    it a printed list is what stops "we check the whole curriculum" from quietly
    becoming untrue as the courses grow.
    """
    inside: dict = {}
    for area in areas:
        for d in index.area_sessions(area.scope_terms):
            inside.setdefault((d.course, d.session_no), []).append(area.area_id)
    outside = [f"{d.course} s{d.session_no} — {d.session_name}"
               for d in sorted(index.docs, key=lambda x: (x.course, x.session_no))
               if (d.course, d.session_no) not in inside]
    return {"sessions": len(index.docs), "in_an_area": len(inside),
            "not_in_any_area": outside,
            "areas": {a.area_id: sum(1 for v in inside.values() if a.area_id in v)
                      for a in areas}}


@dataclass
class GapReport:
    findings: list = field(default_factory=list)
    rows: list = field(default_factory=list)      # the gaps artifact sidecar
    stats: GapStats = field(default_factory=GapStats)


def find_gaps(areas: Iterable[Area], index: CurriculumIndex, *,
              fetcher: Optional[Callable] = None,
              courses: Iterable[str] = ()) -> GapReport:
    """Compare what each area's official documentation enumerates against what we teach.

    `fetcher(url) -> Fetch` is injected so the whole comparison is testable against
    saved pages, which is what lets the four worked placement cases be pinned rather
    than described.
    """
    if fetcher is None:
        from miw import net
        fetcher = net.fetch

    rep = GapReport()
    st = rep.stats
    want_courses = [c for c in courses] or sorted({d.course for d in index.docs})

    for area in areas:
        st.areas += 1
        # Which sessions could host this area at all, per course. Computed once: it is
        # the same candidate pool for every item in the area.
        pool = {c: index.area_sessions(area.scope_terms, course=c) for c in want_courses}
        # Keep only sessions that are genuinely about the area, measured against the
        # most on-area session anywhere in the curriculum. A course left with no such
        # session does not receive the topic at all - "we do not teach this area" is
        # not the same finding as "this area is incomplete", and only the second one
        # belongs in a curriculum digest.
        ranked = [(index.affinity(d, area.scope_terms), d)
                  for docs in pool.values() for d in docs]
        floor = RELATIVE_AREA_FLOOR * max([a for a, _d in ranked] or [0.0])
        pool = {c: [d for d in docs
                    if index.affinity(d, area.scope_terms) >= floor]
                for c, docs in pool.items()}
        pool = {c: docs for c, docs in pool.items() if docs}
        if not pool:
            st.sources_unsupported.append(
                f"{area.area_id}: no session in any course matches this area")
            continue

        readings = []
        for source in area.sources:
            got = fetcher(source.url)
            body = getattr(got, "body", "") or ""
            if not getattr(got, "ok", False) or not body:
                st.sources_unsupported.append(
                    f"{source.url}: not readable (status "
                    f"{getattr(got, 'status', None)}) - no enumeration, and NOT a gap")
                continue
            listing = enumerate_page(body, source_url=source.url, under=source.under,
                                     levels=source.levels, list_items=source.list_items)
            st.sources_read += 1
            if not listing.supported:
                # The critical negative case: a page we could not parse says nothing
                # about the curriculum. It must never read as "the area is complete"
                # nor as "everything is missing".
                st.sources_unsupported.append(f"{source.url}: {listing.reason}")
                continue
            usable = [i for i in listing.items if i.key not in source.exclude]
            st.excluded += len(listing.items) - len(usable)
            st.items_enumerated += len(usable)
            readings.append((source, usable))

        if len(readings) < 2:
            # One source cannot corroborate itself. Say so rather than reporting its
            # headings as gaps: this is the difference between "the area looks complete"
            # and "we have no second opinion", and only one of those is true.
            st.uncorroborated_areas.append(
                f"{area.area_id}: {len(readings)} readable source(s); a topic must be "
                f"named by two INDEPENDENT official sources to be reported")
            continue

        clusters = corroborate(readings)
        st.candidates += len(clusters)

        missing = []
        for cluster in clusters:
            # Any of the cluster's names being taught settles it. Vendors phrase the
            # same technique differently, and "Zero-shot vs few-shot prompts" matches
            # session 8's Key Takeaways where "Few-shot learning" does not.
            if any(index.teaches_anywhere(k, index.topic_terms(n))
                   for n, k in cluster.names_and_keys()):
                st.already_taught += 1
                continue
            missing.append(cluster)

        for cluster in missing:
            dep = topic_dependency(area, cluster.name, cluster.primary_source)
            claims, rejected = [], []
            for source, item in cluster.members:
                try:
                    claim = Claim.build(
                        kind=ClaimKind.EXISTENCE,
                        statement=(f"{item.name} is documented by {source.subject} as "
                                   f"part of {area.title}"),
                        source_url=item.source_url, quote=item.context,
                        subject=source.subject_of())
                except UncitedClaim as exc:
                    rejected.append(f"{item.name} @ {source.url}: {exc}")
                    continue
                if not claim.substantiating:
                    # EXISTENCE is a STRICT kind, so this is the trust layer refusing a
                    # source that is not the area's own documentation. Recorded, not
                    # silently dropped: "we looked and could not cite it" is a result.
                    rejected.append(
                        f"{item.name} @ {claim.source_url}: classifies as "
                        f"{claim.tier.name}, and existence needs AUTHORITATIVE")
                    continue
                claims.append(claim)
            # Corroboration is only real if BOTH citations survived the trust layer.
            # Dropping to one would quietly turn a corroborated topic back into a
            # single vendor's API detail while still reporting it as the former.
            if len(claims) < 2:
                st.uncitable += rejected or [f"{cluster.name}: corroboration lost at "
                                             f"the trust layer"]
                continue

            locations, placements, placed = [], [], False
            for course, docs in pool.items():
                p = index.place(cluster.name, cluster.context, docs,
                                area_terms=area.scope_terms)
                placements.append({
                    "course": course,
                    "session_no": p.session.session_no if p.session else None,
                    "session_name": p.session.session_name if p.session else "",
                    "score": p.score, "matched_terms": p.matched_terms,
                    "reason": p.reason,
                    "runners_up": [{"session_no": d.session_no,
                                    "session_name": d.session_name, "score": s}
                                   for d, s in p.runners_up],
                })
                if p.session is None:
                    continue
                placed = True
                locations.append(_location_of(p.session, area))
            if not placed:
                st.unplaced += 1

            f = Finding(
                dep_id=dep.dep_id, canonical_name=cluster.name,
                signal="S11", signal_label="Curriculum topic gap",
                kind_of_signal="opportunity",
                severity=severity_for_coverage(len(missing), len(clusters)),
                summary=(f"{cluster.name} is documented by "
                         f"{' and '.join(cluster.subjects())} under {area.title}, and "
                         f"appears in no session's outline"),
                courses=sorted({l.course for l in locations}),
                locations=locations, claims=claims,
                affected_urls=sorted({c.source_url for c in claims}),
                raised_at=utcnow())
            f.recommendation = _recommend(cluster.name, area, placements)
            # The same deterministic triad every other finding gets, from the same
            # composer - so a gap sorts, schedules and reads like the rest of the
            # digest instead of being a second class of thing.
            notes.compose(dep, f)
            rep.findings.append(f)
            rep.rows.append({
                "dep_id": dep.dep_id, "canonical_name": cluster.name,
                "area_id": area.area_id, "area_title": area.title,
                "also_documented_as": cluster.aliases(),
                "corroboration": round(cluster.agreement, 3),
                "sources": [{"url": c.source_url, "subject": c.subject_name,
                             # Read back by `main.py verify` to re-classify the claim
                             # without trusting the tier the artifact records.
                             "official_domains": sorted(
                                 src.subject_of().official_domains)}
                            for (src, _i), c in zip(cluster.members, claims)],
                "quote": cluster.context,
                "placements": placements,
                "area_candidates": len(clusters), "area_missing": len(missing),
            })
    st.findings = len(rep.findings)
    return rep


def _location_of(session: SessionDoc, area: Area) -> Location:
    """Point a gap at the session that should host it.

    `field_path` is the session's own `Outline` cell in the workbook, in the grammar
    `extract/locate.py` already resolves - so the UI's finding panel shows the reviewer
    the exact text the placement was judged against, instead of a score to trust.
    """
    return Location(
        course=session.course, topic_name=session.topic_name or area.title,
        unit_id="", unit_name=session.session_name,
        content_id=f"session-{session.session_no}",
        field_path=session.field_path,
        evidence_source="markdown", object_type="SESSION_PPT",
        session_no=session.session_no)


def _recommend(name: str, area: Area, placements: list) -> str:
    """The one-line action, naming the session and quoting what it already covers."""
    named = [p for p in placements if p.get("session_no")]
    if not named:
        alts = placements[0].get("runners_up") if placements else []
        where = (", ".join(f"session {a['session_no']} ({a['session_name']})"
                           for a in (alts or [])[:2])
                 or "no clear session")
        return (f"Add {name} to the {area.title} area - the session is a judgement "
                f"call between {where}; pick one and extend its outline.")
    first = sorted(named, key=lambda p: (p["course"], p["session_no"]))[0]
    rest = [p for p in named if p is not first]
    also = (f" Also missing from {', '.join(sorted(p['course'] for p in rest))}."
            if rest else "")
    return (f"Add {name} to {first['course']} session {first['session_no']} "
            f"({first['session_name']}) - extend that deck's outline and its "
            f"Key Takeaways.{also}")
