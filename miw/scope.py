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
    limit: Optional[int] = None

    @property
    def is_everything(self) -> bool:
        return not (self.courses or self.sessions or self.tiers or self.kinds)

    def describe(self) -> str:
        bits = []
        bits.append(", ".join(sorted(self.courses)) if self.courses else "all courses")
        if self.sessions:
            bits.append(f"sessions {_compact(self.sessions)}")
        if self.tiers:
            bits.append("tiers " + ",".join(sorted(self.tiers)))
        if self.kinds:
            bits.append("kinds " + ",".join(sorted(self.kinds)))
        if self.limit:
            bits.append(f"first {self.limit}")
        return " · ".join(bits)

    def matches(self, dep: Dependency) -> bool:
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
                "limit": self.limit}

    @classmethod
    def from_dict(cls, d: dict) -> "Scope":
        return cls(courses=set(d.get("courses") or []),
                   sessions={int(s) for s in (d.get("sessions") or [])},
                   tiers=set(d.get("tiers") or []),
                   kinds=set(d.get("kinds") or []),
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
