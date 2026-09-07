"""MIW data model.

The one design decision worth stating: the citation rule is enforced in the
constructor, not in a prompt and not in a reviewer's discipline. `Claim` cannot be
built without a source URL, a retrieval timestamp and a verbatim quote, and it
computes its own authority tier from `miw.trust`. `Finding.from_claims` then refuses
to build a finding whose claims do not substantiate it. An LLM that asserts a tool is
deprecated without evidence produces a dropped claim, not a confident finding.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from miw.trust import ClaimKind, Subject, Tier, classify, substantiates


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- ingest

@dataclass
class ContentRecord:
    """One addressable piece of course text."""
    course: str
    topic_name: str
    unit_id: str
    unit_name: str
    unit_type: str
    content_id: str
    object_type: str
    content_type: str
    title: str
    body_text: str
    field_path: str
    evidence_source: str      # solution_code | question_tag | test_case_enum | markdown | deck | url_field
    source_file: str
    session_no: Optional[int] = None
    ingested_at: str = field(default_factory=utcnow)


# ------------------------------------------------------------------------ inventory

@dataclass(frozen=True)
class Location:
    """Where in the curriculum a dependency is referenced."""
    course: str
    topic_name: str
    unit_id: str
    unit_name: str
    content_id: str
    field_path: str
    evidence_source: str
    object_type: str = ""
    session_no: Optional[int] = None

    @property
    def is_graded(self) -> bool:
        """Student is doing the taught step, not reading about it."""
        return self.object_type in ("CODING_QUESTIONS", "OBJECTIVE_QUESTIONS")


WATCH_TIERS = ("critical", "standard", "mention-only")


@dataclass
class Dependency:
    kind: str                      # tool | service | package | model | n8n_node | url
    canonical_name: str
    dep_id: str = ""
    homepage: str = ""
    docs_url: str = ""
    changelog_url: str = ""
    pricing_url: str = ""
    status_url: str = ""
    official_domains: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    # The exact URLs the curriculum sends students to. Probed directly: a live
    # homepage tells us nothing about a dead deep link, and the deep link is what the
    # student actually clicks.
    referenced_urls: list[str] = field(default_factory=list)
    taught_version: Optional[str] = None
    registry: str = ""             # pypi | npm | github | n8n | ""
    registry_id: str = ""
    vendor: str = ""
    locations: list[Location] = field(default_factory=list)
    watch_tier: str = "standard"
    review_status: str = "registry"   # registry | proposed | approved | rejected
    first_seen: str = field(default_factory=utcnow)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.dep_id:
            self.dep_id = _id(self.kind, self.canonical_name.lower())
        # Deliberately NOT a dataclass field: `dataclasses.asdict` would serialise the
        # set into every artifact and then fail to reload it as a Dependency.
        self._loc_keys = set(self.locations)

    # A package's authority is its registry, not a website. Without this, every
    # package dependency has an empty authority set and the research stage correctly
    # but uselessly refuses to say anything about it.
    _REGISTRY_HOME = {
        "pypi": ("pypi.org", "https://pypi.org/project/{id}/"),
        "npm": ("npmjs.com", "https://www.npmjs.com/package/{id}"),
    }

    def subject(self) -> Subject:
        domains = list(self.official_domains)
        homepage, docs = self.homepage, self.docs_url
        reg = self._REGISTRY_HOME.get(self.registry)
        if reg:
            reg_domain, url_tpl = reg
            project = url_tpl.format(id=self.registry_id or self.canonical_name)
            if reg_domain not in domains:
                domains.append(reg_domain)
            homepage = homepage or project
            docs = docs or project
        return Subject(
            name=self.canonical_name,
            official_domains=tuple(domains),
            homepage=homepage, docs_url=docs,
            changelog_url=self.changelog_url, pricing_url=self.pricing_url,
            status_url=self.status_url,
        ).with_domains_from_urls()

    @property
    def courses(self) -> list[str]:
        return sorted({l.course for l in self.locations})

    @property
    def graded_locations(self) -> int:
        """Graded items that actually *depend* on this, excluding passing mentions.

        A tool named in an MCQ stem is prose that happens to be graded. Counting those
        reported "814 graded items" for LangChain, which is true of the word and false
        of the dependency.
        """
        return sum(1 for l in self.locations
                   if l.is_graded and l.evidence_source not in ("prose_name", "question_tag"))

    @property
    def link_locations(self) -> int:
        return sum(1 for l in self.locations if l.evidence_source.startswith("link:"))

    # When a tool changes, the questions about it go stale too - a point MIW had the
    # data for (content_id + object_type on every location) but never surfaced. The
    # two buckets need different work, so they are counted separately rather than
    # summed into one alarming number.
    RUNTIME_EVIDENCE_SOURCES = ("solution_import", "test_case_enum", "n8n_workflow",
                                "install_command")

    def _questions(self, runtime: bool) -> list[Location]:
        out = []
        for l in self.locations:
            if not l.content_id or l.object_type not in (
                    "OBJECTIVE_QUESTIONS", "CODING_QUESTIONS"):
                continue
            is_runtime = l.evidence_source in self.RUNTIME_EVIDENCE_SOURCES
            if is_runtime is runtime:
                out.append(l)
        return out

    @property
    def questions_that_execute_it(self) -> list[Location]:
        """Graded items whose solution code or test cases use this. These break."""
        return self._questions(True)

    @property
    def questions_that_mention_it(self) -> list[Location]:
        """Graded items that only name it. These may need rewording, not fixing."""
        return self._questions(False)

    def merge_location(self, loc: Location) -> None:
        """Add a location if it is new.

        Backed by a set rather than a linear scan of the list. LangChain carries 845
        locations, so the `in list` form cost ~350k frozen-dataclass comparisons for
        that one dependency and dominated the extract stage.
        """
        if loc in self._loc_keys:
            return
        self._loc_keys.add(loc)
        self.locations.append(loc)


# ---------------------------------------------------------------------------- probe

PROBE_STATUSES = ("ok", "changed", "broken", "unreachable", "inconclusive")


@dataclass
class ProbeResult:
    """One deterministic weekly observation of one dependency.

    `unreachable` is a first-class status, distinct from `broken`. Our network failing
    is not a dead tool, and the two must never collapse into one bucket.
    """
    dep_id: str
    canonical_name: str
    status: str = "inconclusive"
    checked_at: str = field(default_factory=utcnow)
    http_status: Optional[int] = None
    final_url: str = ""
    redirected: bool = False
    text_hash: str = ""
    latest_version: Optional[str] = None
    version_released_at: str = ""
    repo_archived: Optional[bool] = None
    last_commit_at: str = ""
    signals: list[str] = field(default_factory=list)     # machine-readable flags
    # Breaking changes the vendor itself declares, each with its own docs URL. Carried
    # here so `analyse` can build a properly cited Claim rather than paraphrasing.
    declared_changes: list[dict] = field(default_factory=list)
    # The specific URLs that triggered this result. A finding is about these, not
    # about every place the dependency is mentioned.
    affected_urls: list[str] = field(default_factory=list)
    detail: str = ""
    evidence_url: str = ""
    consecutive_failures: int = 0

    def flag(self, name: str) -> None:
        if name not in self.signals:
            self.signals.append(name)


# -------------------------------------------------------------------------- research

class UncitedClaim(ValueError):
    """Raised when something tries to build a claim without usable evidence."""


@dataclass
class Claim:
    """One assertion, with the evidence that permits it to exist.

    Constructing a Claim is the trust checkpoint. There is deliberately no way to
    build one from model recall alone.
    """
    kind: ClaimKind
    statement: str
    source_url: str
    quote: str
    subject_name: str = ""
    retrieved_at: str = field(default_factory=utcnow)
    tier: Tier = Tier.LEAD_ONLY

    @classmethod
    def build(cls, *, kind: ClaimKind, statement: str, source_url: str, quote: str,
              subject: Subject, retrieved_at: Optional[str] = None) -> "Claim":
        statement, source_url, quote = (statement or "").strip(), (source_url or "").strip(), (quote or "").strip()
        if not statement:
            raise UncitedClaim("empty statement")
        if not source_url:
            raise UncitedClaim(f"no source for: {statement[:80]}")
        # A quote short enough to be a label is not evidence of anything.
        if len(quote) < 12:
            raise UncitedClaim(f"quote too short to verify: {statement[:80]}")
        tier = classify(source_url, subject, kind)
        if tier is Tier.EXCLUDED:
            raise UncitedClaim(f"excluded source {source_url}")
        return cls(kind=kind, statement=statement, source_url=source_url, quote=quote,
                   subject_name=subject.name, tier=tier,
                   retrieved_at=retrieved_at or utcnow())

    @property
    def substantiating(self) -> bool:
        return substantiates(self.tier, self.kind)


@dataclass
class Alternative:
    """A candidate replacement. Nominated anywhere, verified officially."""
    name: str
    homepage: str = ""
    does_taught_job: Optional[bool] = None
    free_student_path: Optional[bool] = None
    signup_required: Optional[bool] = None
    maturity_note: str = ""
    nominated_by: str = ""            # url that suggested it (may be LEAD_ONLY)
    claims: list[Claim] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        """At least one claim about it rests on its own official domain."""
        return any(c.substantiating for c in self.claims)


@dataclass
class ResearchResult:
    dep_id: str
    canonical_name: str
    researched_at: str = field(default_factory=utcnow)
    claims: list[Claim] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)   # why evidence was rejected
    official_pages_seen: list[str] = field(default_factory=list)

    def substantiated(self, kind: Optional[ClaimKind] = None) -> list[Claim]:
        return [c for c in self.claims
                if c.substantiating and (kind is None or c.kind is kind)]


# -------------------------------------------------------------------------- findings

SEVERITIES = ("critical", "high", "medium", "low", "info")
DIFF_CLASSES = ("new", "worsened", "improved", "changed", "unchanged", "resolved")


@dataclass
class Finding:
    dep_id: str
    canonical_name: str
    signal: str                     # S1..S11
    signal_label: str
    severity: str
    diff_class: str = "new"
    summary: str = ""
    # The action note, split three ways. Splitting it is what makes reviewer feedback
    # useful: "the urgency was wrong" is a correctable signal, "the recommendation was
    # wrong" is not. Borrowed from the prior Curriculum Gap Analyzer's
    # what_to_act / why_to_act / when_to_act triad.
    recommendation: str = ""          # kept: the deterministic one-liner
    what_to_act: str = ""
    why_to_act: str = ""
    when_to_act: str = ""
    note_source: str = "template"     # template | llm
    blast_radius: int = 0
    graded_locations: int = 0
    courses: list[str] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    probe_signals: list[str] = field(default_factory=list)
    affected_urls: list[str] = field(default_factory=list)
    latest_version: str = ""
    screenshots_at_risk: int = 0
    questions_executing: int = 0
    questions_mentioning: int = 0
    question_ids: list[str] = field(default_factory=list)
    finding_id: str = ""
    raised_at: str = field(default_factory=utcnow)
    kind_of_signal: str = "regression"   # regression | opportunity

    def __post_init__(self) -> None:
        if not self.finding_id:
            self.finding_id = _id(self.dep_id, self.signal)

    @property
    def evidence_urls(self) -> list[str]:
        return sorted({c.source_url for c in self.claims if c.substantiating})

    @property
    def is_substantiated(self) -> bool:
        """Deterministic probe signals stand on their own; researched claims must cite.

        A probe result is a first-hand observation we made ourselves, so it needs no
        external citation. Anything the model concluded does.
        """
        return bool(self.probe_signals) or any(c.substantiating for c in self.claims)


def to_jsonable(obj: Any) -> Any:
    """Dataclass tree -> JSON-safe structure, with enums flattened to their names."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (ClaimKind,)):
        return obj.value
    if isinstance(obj, Tier):
        return obj.name
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def dump(path: str, obj: Any) -> None:
    with open(path, "w") as fh:
        json.dump(to_jsonable(obj), fh, indent=2, default=str)
