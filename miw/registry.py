"""The tool registry: human-owned config that says who speaks for each dependency.

`registry/tools.yaml` is the file a curriculum engineer edits. Each entry names a
dependency, its aliases, and — the part that matters — the domains that are
authoritative about it. `miw.trust` reads nothing else.

Entries are bootstrapped from the curriculum itself (see `bootstrap.py`): a link the
content author wrote to `console.groq.com` is itself the assertion that `groq.com` is
Groq's official home. Bootstrapped entries carry `review_status: derived`; a human
promotes them to `approved`. Nothing about that status gates monitoring — it exists so
a reviewer can see what was inferred rather than stated.

The safe-failure direction is deliberate: a dependency with no official domain simply
has no authoritative source, so under `miw.trust` it can never produce a
deprecation/pricing/version finding. A missing registry entry costs recall, never
correctness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml

REGISTRY_PATH = Path("registry/tools.yaml")
REVIEW_PATH = Path("registry/review_queue.yaml")


def norm(name: str) -> str:
    """Alias key: lowercase, punctuation folded to single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w+#.]+", " ", (name or "").lower())).strip()


@dataclass
class Entry:
    canonical_name: str
    kind: str = "tool"                 # tool | service | package | model | n8n_node
    aliases: list[str] = field(default_factory=list)
    homepage: str = ""
    docs_url: str = ""
    changelog_url: str = ""
    pricing_url: str = ""
    status_url: str = ""
    official_domains: list[str] = field(default_factory=list)
    registry: str = ""
    registry_id: str = ""
    vendor: str = ""
    watch_tier: str = ""
    review_status: str = "derived"
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{norm(self.canonical_name)}"

    def alias_keys(self) -> list[str]:
        return sorted({norm(a) for a in [self.canonical_name, *self.aliases] if norm(a)})


class Registry:
    def __init__(self, entries: Iterable[Entry] = ()):
        self.entries: dict[str, Entry] = {}
        # One alias can name several kinds: "langchain" is both a hosted service and a
        # PyPI distribution. Mapping an alias to a single entry made kind-specific
        # lookup unable to reach the second one, so a version pin recorded against the
        # distribution was attributed to the service instead.
        self._alias: dict[str, list[Entry]] = {}
        self._domain: dict[str, Entry] = {}
        for e in entries:
            self.add(e)

    def add(self, e: Entry) -> Entry:
        existing = self.entries.get(e.key)
        if existing:
            existing.aliases = sorted(set(existing.aliases) | set(e.aliases))
            existing.official_domains = sorted(set(existing.official_domains) | set(e.official_domains))
            for fld in ("homepage", "docs_url", "changelog_url", "pricing_url",
                        "status_url", "registry", "registry_id", "vendor"):
                if not getattr(existing, fld) and getattr(e, fld):
                    setattr(existing, fld, getattr(e, fld))
            e = existing
        else:
            self.entries[e.key] = e
        for k in e.alias_keys():
            bucket = self._alias.setdefault(k, [])
            if e not in bucket:
                bucket.append(e)
        for d in e.official_domains:
            self._domain.setdefault(d.lower(), e)
        return e

    def resolve(self, name: str, kind: Optional[str] = None) -> Optional[Entry]:
        """Entry for an alias. With `kind`, only an entry of that kind is returned."""
        bucket = self._alias.get(norm(name)) or []
        if kind is not None:
            return next((e for e in bucket if e.kind == kind), None)
        return bucket[0] if bucket else None

    def by_domain(self, dom: str) -> Optional[Entry]:
        return self._domain.get((dom or "").lower())

    def alias_index(self) -> dict[str, Entry]:
        """First entry per alias, for prose matching."""
        return {k: v[0] for k, v in self._alias.items() if v}

    # --- persistence --------------------------------------------------------

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> "Registry":
        if not Path(path).exists():
            return cls()
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls(Entry(**row) for row in (raw.get("tools") or []))

    def save(self, path: Path = REGISTRY_PATH) -> None:
        rows = []
        for e in sorted(self.entries.values(), key=lambda x: (x.kind, x.canonical_name.lower())):
            row = {"canonical_name": e.canonical_name, "kind": e.kind}
            for fld in ("aliases", "homepage", "docs_url", "changelog_url", "pricing_url",
                        "status_url", "official_domains", "registry", "registry_id",
                        "vendor", "watch_tier", "review_status", "notes"):
                v = getattr(e, fld)
                if v:
                    row[fld] = v
            rows.append(row)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            "# MIW tool registry - human-owned.\n"
            "#\n"
            "# `official_domains` is the authority set: the ONLY domains that may\n"
            "# substantiate a deprecation, pricing, version or implementation claim about\n"
            "# this dependency (see miw/trust.py). Widen it only to hosts the vendor\n"
            "# actually publishes on.\n"
            "#\n"
            "# review_status: derived = inferred from the curriculum's own links;\n"
            "#                approved = a human has checked it.\n"
            f"# {len(rows)} entries.\n\n"
            + yaml.safe_dump({"tools": rows}, sort_keys=False, allow_unicode=True, width=100)
        )
