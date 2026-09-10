"""The vendor adapter interface.

`miw/probe/n8n_upstream.py` was already an adapter in all but name — it reads what n8n
currently ships and what n8n itself declares broken. This generalises that shape so a
model provider can be added as a config-shaped file rather than a new subsystem.

Two fields carry most of the weight, and they are deliberately separate:

    ok         we were able to read the vendor's page
    supported  the vendor publishes this kind of information at all

Collapsing them is how a monitoring system starts inventing outages. A 503 from Groq
is `ok=False`; a vendor with no deprecation page at all is `supported=False`; and
**neither may ever reach a finding.** Only `ok=True, supported=True` plus an actual
retired row is evidence of anything.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from miw.net import fetch
from miw.probe.catalogue import CatalogueEntry, entries

CACHE_DIR = Path("state/vendor_cache")
CACHE_TTL_S = 12 * 3600          # a deprecation announcement is news for longer than this


@dataclass
class Catalogue:
    """What a vendor currently lists, keyed by exact identifier."""
    vendor: str
    entries: dict[str, CatalogueEntry] = field(default_factory=dict)
    ok: bool = False
    supported: bool = True
    error: str = ""
    sources: list[str] = field(default_factory=list)

    def get(self, identifier: str) -> Optional[CatalogueEntry]:
        """Exact, case-folded lookup.

        Never a substring match. The inventory holds `gemini-2.0-flash`,
        `gemini-2.0-flash-lite`, `gemini-3.1-flash-lite` and
        `gemini-3.1-flash-lite-preview`; a substring rule would implicate all four from
        one retired row. It also makes extraction noise harmless — a malformed id simply
        fails to match a real catalogue and produces nothing.
        """
        if not identifier:
            return None
        want = identifier.strip().casefold()
        for k, v in self.entries.items():
            if k.strip().casefold() == want:
                return v
        return None

    @property
    def usable(self) -> bool:
        return self.ok and self.supported and bool(self.entries)


@runtime_checkable
class VendorAdapter(Protocol):
    key: str
    official_domains: tuple[str, ...]
    kinds: tuple[str, ...]

    def catalogue(self) -> Catalogue: ...


# --- shared TTL cache -------------------------------------------------------

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def cached_pages(key: str, urls: list[str], *, refresh: bool = False,
                 ttl: int = CACHE_TTL_S) -> tuple[dict[str, str], list[str]]:
    """Fetch and cache page bodies. Returns (url -> html, errors).

    Cached so a daily poll across many vendors costs one fetch per vendor per TTL
    window rather than one per dependency.
    """
    path = _cache_path(key)
    if not refresh and path.exists():
        try:
            d = json.loads(path.read_text())
            if time.time() - d.get("fetched_at", 0) < ttl:
                return d.get("pages", {}), []
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    pages, errors = {}, []
    for u in urls:
        f = fetch(u, timeout=30)
        if f.ok and f.body:
            pages[u] = f.body
        else:
            errors.append(f"{u}: http {f.status or f.error}")
    if pages:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps({"fetched_at": time.time(), "pages": pages}))
        except OSError:
            pass
    return pages, errors


def build_catalogue(vendor: str, key: str, urls: list[str], *,
                    refresh: bool = False) -> Catalogue:
    """The common adapter body: read the vendor's pages, keep the id-column rows.

    Rows found in a *replacement* column are dropped here. A model that was the
    successor to something older is not thereby retired — on Groq's page
    `llama-3.3-70b-versatile` sits in the replacement column of seven rows and the
    deprecated column of one, and only the latter is a finding.
    """
    pages, errors = cached_pages(key, urls, refresh=refresh)
    if not pages:
        return Catalogue(vendor=vendor, ok=False,
                         error="; ".join(errors) or "no pages readable", sources=urls)

    found: dict[str, CatalogueEntry] = {}
    for url, html_text in pages.items():
        for e in entries(html_text, evidence_url=url):
            if e.column_role != "id":
                continue
            prev = found.get(e.entry_id)
            # A retired row still wins as the PRIMARY record - vendors list a model in
            # the current table and again in the retirement table during the wind-down,
            # and the retirement is the news. But the availability sighting is kept
            # alongside it rather than discarded, because the two together say something
            # neither says alone: Groq's deprecations table gives
            # `llama-3.3-70b-versatile` a shutdown date that has passed, while its
            # models table still lists that exact id as "Llama 3.3 70B Enterprise" at
            # "Contact Sales". The id did not disappear - it left the developer plan.
            # Reporting only the first half is how a reviewer opens the vendor's page,
            # sees the id listed, and stops believing the digest.
            if prev is None:
                found[e.entry_id] = e
            elif e.retired and not prev.retired:
                e.still_listed, e.listed_price = True, prev.price
                e.listed_quote, e.listed_url = prev.quote, prev.evidence_url
                found[e.entry_id] = e
            elif prev.retired and not e.retired:
                prev.still_listed, prev.listed_price = True, e.price
                prev.listed_quote, prev.listed_url = e.quote, e.evidence_url

    if not found:
        # Parsed cleanly but nothing role-typed: say so rather than falling back to a
        # text search, which is how "we could not read it" becomes "it is gone".
        return Catalogue(vendor=vendor, ok=True, supported=False,
                         error="no role-typed catalogue table found",
                         sources=list(pages))
    return Catalogue(vendor=vendor, entries=found, ok=True, supported=True,
                     sources=list(pages))


# --- registry ---------------------------------------------------------------

def all_adapters() -> list[VendorAdapter]:
    from miw.vendors.google_ai import GoogleAIAdapter
    from miw.vendors.groq import GroqAdapter
    return [GroqAdapter(), GoogleAIAdapter()]


def adapters_for(kind: str) -> list[VendorAdapter]:
    return [a for a in all_adapters() if kind in a.kinds]
