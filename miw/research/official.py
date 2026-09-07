"""Official-first evidence gathering: ground truth, with no model in the loop.

This is the primary research path, and it is deliberately deterministic. For a claim
about a tool we fetch **the tool's own pages** — its changelog, pricing page,
deprecation notes, status page, docs — and lift a verbatim sentence out of them. The
resulting `Claim` is AUTHORITATIVE under `miw.trust` because of where it came from,
not because a model was confident about it.

Consequences worth stating plainly:

* No LLM key and no search key are needed to produce authoritative findings. Search
  discovers candidates and the LLM writes prose; neither is load-bearing for evidence.
* A quote is always the vendor's own words. When there is no such sentence, the honest
  output is no claim, which is why `ResearchResult.dropped` exists.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from miw.net import fetch, main_text
from miw.probe.http_probe import (PAID_PHRASES, SUNSET_PHRASES_STRONG, WALL_PHRASES)
from miw.schema import Alternative, Claim, Dependency, ResearchResult, UncitedClaim
from miw.trust import ClaimKind, Subject, official_targets

# Budget is counted in pages *successfully read*, not URLs attempted. Well-known
# paths are guesses - `/pricing` on a docs subdomain usually 404s - and counting
# attempts let three dead guesses exhaust the budget before the apex domain, which is
# where the real page lives, was ever tried.
MAX_PAGES_PER_KIND = 3
MAX_ATTEMPTS_PER_KIND = 9
MAX_QUOTE = 300
MIN_QUOTE = 20

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")

# A quote must read like prose. Navigation and link lists survive extraction as runs
# of Capitalised fragments with no sentence punctuation; quoting one as evidence makes
# a finding unverifiable, so they are rejected outright.
_WORD = re.compile(r"[A-Za-z][a-z']+")


# Fragments of embedded JSON, JS or template data. Even with chrome stripped, some
# pages inline data payloads; a quote containing one is not human-verifiable evidence.
_MACHINE = re.compile(r'\{"|"\s*:\s*[\[{"\d]|\\u00|\\/|=>|\bfunction\s*\(|;\s*\}')


def _is_prose(s: str) -> bool:
    if _MACHINE.search(s):
        return False
    words = _WORD.findall(s)
    if len(words) < 5:
        return False
    caps = sum(1 for w in s.split() if w[:1].isupper())
    if caps / max(len(s.split()), 1) > 0.45:
        return False
    return bool(re.search(r"[a-z]{3}[ ,][a-z]{2}", s))

# Keywords that mark a sentence as being *about* each claim kind.
KIND_KEYWORDS: dict[ClaimKind, tuple[str, ...]] = {
    ClaimKind.DEPRECATION: SUNSET_PHRASES_STRONG,
    ClaimKind.PRICING: PAID_PHRASES + ("free tier", "free plan", "no credit card",
                                       "requests per", "rate limit", "quota"),
    ClaimKind.AVAILABILITY: WALL_PHRASES + ("sign up", "create an account", "api key"),
    ClaimKind.VERSION: ("version", "release", "changelog", "released", "v1.", "v2."),
    ClaimKind.EXISTENCE: ("operational", "all systems", "incident", "degraded",
                          "maintenance"),
    ClaimKind.IMPLEMENTATION: ("breaking change", "renamed", "moved", "replaced",
                               "new endpoint", "removed the", "no longer supports"),
}

# A successor named by the vendor itself. The highest-quality alternative signal there
# is: the people shutting a tool down usually say what to use instead.
SUCCESSOR = re.compile(
    r"(?:migrate to|move to|use|replaced by|superseded by|successor is|"
    r"please use|switch to)\s+([A-Z][\w.+-]{2,30}(?:\s[A-Z][\w.+-]{2,20})?)")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text or "")
            if MIN_QUOTE <= len(s.strip()) and _is_prose(s.strip())]


def _relevant(sentences: Iterable[str], keywords: tuple[str, ...],
              terms: tuple[str, ...]) -> list[str]:
    """Sentences that mention the claim's subject *and* the claim's topic."""
    out = []
    low_terms = [t.lower() for t in terms if t and len(t) > 2]
    for s in sentences:
        low = s.lower()
        if not any(k in low for k in keywords):
            continue
        if low_terms and not any(t in low for t in low_terms):
            continue
        out.append(s[:MAX_QUOTE])
    return out


def gather(dep: Dependency, kinds: Optional[Iterable[ClaimKind]] = None,
           result: Optional[ResearchResult] = None) -> ResearchResult:
    """Fetch the dependency's own pages and lift verbatim evidence from them."""
    subject: Subject = dep.subject()
    res = result or ResearchResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name)

    if not subject.official_domains:
        res.dropped.append(
            f"no official domain known for {dep.canonical_name}; cannot establish "
            "ground truth, so no claim was made")
        return res

    terms = tuple({dep.canonical_name, dep.canonical_name.rsplit(".", 1)[-1],
                   dep.registry_id, *dep.aliases} - {""})
    wanted = tuple(kinds or (ClaimKind.DEPRECATION, ClaimKind.PRICING,
                             ClaimKind.IMPLEMENTATION))

    cache: dict[str, str] = {}
    for kind in wanted:
        read = attempts = 0
        for url in official_targets(subject, kind):
            if read >= MAX_PAGES_PER_KIND or attempts >= MAX_ATTEMPTS_PER_KIND:
                break
            if url not in cache:
                attempts += 1
            if url in cache:
                text = cache[url]
            else:
                # One request per page. `observe()` keeps only a hash of the prose, and
                # quoting needs the prose itself, so fetch directly rather than
                # probing and then re-reading.
                f = fetch(url)
                text = main_text(f.body) if (f.ok and f.body) else ""
                cache[url] = text
                if text and url not in res.official_pages_seen:
                    res.official_pages_seen.append(url)
                elif not f.ok:
                    res.dropped.append(
                        f"{url}: not readable (http {f.status or f.error})")
            if not text:
                continue
            read += 1

            quotes = _relevant(_sentences(text), KIND_KEYWORDS.get(kind, ()), terms)
            for q in quotes[:2]:
                try:
                    res.claims.append(Claim.build(
                        kind=kind,
                        statement=_statement_for(kind, dep.canonical_name, q),
                        source_url=url, quote=q, subject=subject))
                except UncitedClaim as exc:
                    res.dropped.append(str(exc))

            if kind is ClaimKind.DEPRECATION:
                for q in quotes[:2]:
                    for name in SUCCESSOR.findall(q):
                        res.alternatives.append(Alternative(
                            name=name.strip(), nominated_by=url,
                            maturity_note="named as the successor by the vendor itself"))
    return res


def _statement_for(kind: ClaimKind, name: str, quote: str) -> str:
    """A short, checkable sentence. The quote alongside it is the actual evidence."""
    return {
        ClaimKind.DEPRECATION: f"{name}: its own documentation uses deprecation language",
        ClaimKind.PRICING: f"{name}: its own pricing page describes plan or quota limits",
        ClaimKind.IMPLEMENTATION: f"{name}: its own docs describe a breaking or renamed interface",
        ClaimKind.VERSION: f"{name}: its own release notes name a current version",
        ClaimKind.EXISTENCE: f"{name}: its own status page reports service state",
        ClaimKind.AVAILABILITY: f"{name}: its own pages describe signup or access requirements",
    }.get(kind, f"{name}: {quote[:80]}")
