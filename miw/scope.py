"""Run scoping: audit one course, one session, or one slice of the inventory.

A weekly sweep over everything is the cron case. The interactive case is narrower and
more common — "we're re-recording session 12 of LLM Apps, check just that" — and it
needs to be cheap enough to run on demand. Probing is politeness-throttled at 1.5s per
domain, so scope is what makes an on-demand run finish in a minute instead of an hour.

Scope is resolved against a dependency's **locations**, not its name: a dependency
belongs to a session because something in that session references it. One tool can
therefore be in scope for several courses at once, which is the whole point of having
deduplicated the inventory in the first place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from miw.schema import Dependency

SESSION_RANGE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


def parse_sessions(spec: str) -> set[int]:
    """Parse "3", "3-7", "3,5,9-11" into a set of session numbers."""
    out: set[int] = set()
    for part in (spec or "").split(","):
        if not part.strip():
            continue
        m = SESSION_RANGE.match(part)
        if not m:
            raise ValueError(f"cannot read session spec {part!r}; use 3, 3-7 or 3,5,9-11")
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if hi < lo:
            lo, hi = hi, lo
        out.update(range(lo, hi + 1))
    return out


@dataclass
class Scope:
    courses: set[str] = field(default_factory=set)      # course titles; empty = all
    sessions: set[int] = field(default_factory=set)     # session numbers; empty = all
    tiers: set[str] = field(default_factory=set)        # watch tiers; empty = all
    kinds: set[str] = field(default_factory=set)        # dependency kinds; empty = all
    # An exact set of dependencies, which is what a vendor signal resolves to. When
    # present it wins outright: "Groq retired these two model ids" is not a course or a
    # session filter, it is a named list.
    dep_ids: set[str] = field(default_factory=set)
    limit: Optional[int] = None

    @property
    def is_everything(self) -> bool:
        return not (self.courses or self.sessions or self.tiers or self.kinds
                    or self.dep_ids)

    def describe(self) -> str:
        bits = []
        bits.append(", ".join(sorted(self.courses)) if self.courses else "all courses")
        if self.sessions:
            bits.append(f"sessions {_compact(self.sessions)}")
        if self.tiers:
            bits.append("tiers " + ",".join(sorted(self.tiers)))
        if self.kinds:
            bits.append("kinds " + ",".join(sorted(self.kinds)))
        if self.dep_ids:
            bits = [f"{len(self.dep_ids)} named dependency(ies)"]
        if self.limit:
            bits.append(f"first {self.limit}")
        return " · ".join(bits)

    def matches(self, dep: Dependency) -> bool:
        if self.dep_ids:
            return dep.dep_id in self.dep_ids
        if self.kinds and dep.kind not in self.kinds:
            return False
        if self.tiers and dep.watch_tier not in self.tiers:
            return False
        if not (self.courses or self.sessions):
            return True
        # A session filter with no course filter means "that session number in any
        # course", which is what someone auditing "session 12" across a shared module
        # actually wants.
        for loc in dep.locations:
            if self.courses and loc.course not in self.courses:
                continue
            if self.sessions and loc.session_no not in self.sessions:
                continue
            return True
        return False

    def select(self, deps: Iterable[Dependency]) -> list[Dependency]:
        picked = [d for d in deps if self.matches(d)]
        return picked[:self.limit] if self.limit else picked

    # --- serialisation, for the job record and the API ----------------------

    def to_dict(self) -> dict:
        return {"courses": sorted(self.courses), "sessions": sorted(self.sessions),
                "tiers": sorted(self.tiers), "kinds": sorted(self.kinds),
                "dep_ids": sorted(self.dep_ids), "limit": self.limit}

    @classmethod
    def from_dict(cls, d: dict) -> "Scope":
        return cls(courses=set(d.get("courses") or []),
                   sessions={int(s) for s in (d.get("sessions") or [])},
                   tiers=set(d.get("tiers") or []),
                   kinds=set(d.get("kinds") or []),
                   dep_ids=set(d.get("dep_ids") or []),
                   limit=d.get("limit"))

    def to_cli_args(self) -> list[str]:
        """The CLI flags that reproduce this scope, so a run is replayable by hand."""
        args: list[str] = []
        for c in sorted(self.courses):
            args += ["--course", c]
        if self.sessions:
            args += ["--session", _compact(self.sessions)]
        if self.tiers:
            args += ["--tiers", ",".join(sorted(self.tiers))]
        if self.kinds:
            args += ["--kinds", ",".join(sorted(self.kinds))]
        for d in sorted(self.dep_ids):
            args += ["--dep-id", d]
        if self.limit:
            args += ["--limit", str(self.limit)]
        return args

    @classmethod
    def from_args(cls, args) -> "Scope":
        return cls(
            courses=set(getattr(args, "course", None) or []),
            sessions=parse_sessions(getattr(args, "session", "") or ""),
            tiers={t.strip() for t in (getattr(args, "tiers", "") or "").split(",") if t.strip()},
            kinds={k.strip() for k in (getattr(args, "kinds", "") or "").split(",") if k.strip()},
            dep_ids=set(getattr(args, "dep_id", None) or []),
            limit=getattr(args, "limit", None))


def _compact(nums: Iterable[int]) -> str:
    """[3,4,5,9] -> "3-5,9" — the form parse_sessions reads back."""
    s = sorted(set(nums))
    if not s:
        return ""
    out, start, prev = [], s[0], s[0]
    for n in s[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ",".join(out)


# --- course identity -------------------------------------------------------

# `config.constants.COURSES` is the only place course slugs exist, and until now they
# died in `cmd_ingest`. They are the natural URL identifier - stable, lowercase, no
# escaping - so they are exposed here, where every other course concept already lives.
# Verified: the four titles in the inventory match COURSES exactly, so this is a
# straight lookup and not a fuzzy match.
def _course_tables() -> tuple[dict[str, str], dict[str, str]]:
    from config.constants import COURSES
    slug_by_title = {v["title"]: k for k, v in COURSES.items()}
    return slug_by_title, {k: v["title"] for k, v in COURSES.items()}


def slugify(title: str) -> str:
    """Fallback slug for a course the constants file has not been told about.

    A course renamed in an export but not in `COURSES` must stay addressable rather
    than vanishing from the UI, so it gets a derived slug instead of nothing.
    """
    out = re.sub(r"[^a-z0-9]+", "_", (title or "").strip().lower()).strip("_")
    return out or "unknown"


def slug_of(title: str) -> str:
    return _course_tables()[0].get(title) or slugify(title)


def title_of(slug: str) -> str:
    """The declared title for a slug, or "" when the slug is not a known course."""
    return _course_tables()[1].get((slug or "").strip().lower(), "")


def resolve_courses(tokens: Iterable[str]) -> set[str]:
    """Course titles from a mix of slugs and titles.

    Lenient on purpose: `?course=my_course` and `?course=My Course` must both work, so
    a URL can carry the slug while the existing UI keeps sending titles. `Scope.courses`
    stays a set of TITLES, because that is what `Location.course` holds and what
    `to_cli_args()` emits as `--course`.
    """
    _, title_by_slug = _course_tables()
    out: set[str] = set()
    for tok in tokens:
        tok = (tok or "").strip()
        if not tok:
            continue
        out.add(title_by_slug.get(tok.lower(), tok))
    return out


def course_map(deps: Iterable[Dependency]) -> dict[str, list[int]]:
    """course -> sorted session numbers seen, for populating a picker."""
    out: dict[str, set[int]] = {}
    for d in deps:
        for loc in d.locations:
            if loc.course:
                out.setdefault(loc.course, set())
                if loc.session_no:
                    out[loc.course].add(loc.session_no)
    return {c: sorted(s) for c, s in sorted(out.items())}
