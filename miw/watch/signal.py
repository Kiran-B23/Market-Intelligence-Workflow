"""What a trigger emits, and what stops it emitting twice.

Two rules carry this module:

**A watermark is compared, never just recorded.** A poll that reads the same value as
last time writes nothing and emits nothing, so n8n 2.39.0 fires once rather than every
day for a week.

**The first observation is a baseline, not an event.** With no prior value there is no
change, and treating one as an event would make the first deploy emit a wall of
historical deprecations as though they had just happened. This is the same rule
`pricing.compare()` already applies to a pricing page — a first sighting records and
reports nothing.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable

# Borrowed from the prior Curriculum Gap Analyzer, where it was prompt-only and
# unvalidated. Validated here.
TRIGGERS = ("new_release", "deprecation", "breaking_change", "catalogue_changed",
            "security_update", "new_tool", "baseline")


def _id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def row_set_hash(rows: Iterable[tuple[str, str, str]]) -> str:
    """Stable hash of a catalogue's meaning, not its markup.

    Hashes the sorted `(id, status, replacement)` triples, so a vendor rewriting its
    marketing copy, reordering a table or restyling a page produces no event, while
    adding or retiring a model produces one. A page simhash cannot make that
    distinction — it is the fallback for vendors with no parseable table.
    """
    joined = "\n".join(f"{a}\x1f{b}\x1f{c}" for a, b, c in sorted(rows))
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Watermark:
    source_key: str          # "groq:catalogue" | "pypi:langchain" | "n8n:releases"
    kind: str                # row_set_hash | version | content_hash
    value: str
    evidence_url: str = ""
    quote: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.value


@dataclass
class Signal:
    vendor_key: str
    source_key: str
    trigger: str
    from_value: str
    to_value: str
    evidence_url: str = ""
    quote: str = ""
    # Only what is NEW versus the previous watermark — the rows that changed, not the
    # whole catalogue. This is what keeps an investigation scoped.
    declared: list[dict] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)     # identifiers the change names
    observed_at: str = ""
    signal_id: str = ""

    def __post_init__(self) -> None:
        if not self.signal_id:
            # Derived from the watermark value, so re-observing the same state is an
            # idempotent upsert rather than a new event.
            self.signal_id = _id(self.vendor_key, self.source_key, self.trigger,
                                 self.to_value)

    @property
    def is_baseline(self) -> bool:
        return self.trigger == "baseline" or not self.from_value
