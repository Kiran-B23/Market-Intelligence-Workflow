"""Web search, constrained by the trust policy.

Search plays two different roles here and they get different rules:

* **Verification** of a strict claim (deprecated? paid? version?) runs with
  `include_domains` set to the dependency's own official domains, so a result cannot
  come from anywhere else. Search is being used as an index over official pages, not
  as an oracle.
* **Discovery** of a replacement runs unfiltered, because a tool nobody has heard of
  cannot be found on domains we already know — this is how deepwiki would surface as a
  successor to codetotutorial. Whatever it turns up is LEAD_ONLY: a nomination, which
  is then re-verified against the candidate's own official domain before any claim
  about it exists.

Absent a key, both degrade to empty and the caller carries on with official-page
evidence, mirroring the `_tavily_preflight` pattern in the interview-question repo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config import settings
from miw.extract.links import registrable
from miw.net import domain
from miw.trust import (Subject, Tier, classify, search_include_domains)


@dataclass
class Hit:
    title: str
    url: str
    snippet: str
    tier: Tier = Tier.LEAD_ONLY


@dataclass
class SearchOutcome:
    hits: list[Hit] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    disabled: bool = False


_client = None
_preflight_failed = False


def _get_client():
    global _client, _preflight_failed
    if _preflight_failed or not settings.SEARCH_ENABLED:
        return None
    if _client is None:
        try:
            from tavily import TavilyClient
            _client = TavilyClient(api_key=settings.TAVILY_API_KEY)
        except Exception as exc:                      # missing lib or bad key
            _preflight_failed = True
            return None
    return _client


def _search(query: str, *, include_domains: Optional[list[str]] = None,
            max_results: int = 6) -> SearchOutcome:
    client = _get_client()
    if client is None:
        return SearchOutcome(disabled=True,
                             errors=["search disabled: no TAVILY_API_KEY"])
    kwargs = {"query": query, "max_results": max_results,
              "search_depth": "basic"}
    if include_domains:
        kwargs["include_domains"] = include_domains
    try:
        raw = client.search(**kwargs)
    except Exception as exc:
        global _preflight_failed
        msg = f"{type(exc).__name__}: {exc}"
        # Quota and auth failures are terminal for the run; degrade rather than
        # retry every dependency and burn the whole budget on errors.
        if any(w in msg.lower() for w in ("unauthorized", "quota", "forbidden", "401", "432")):
            _preflight_failed = True
        return SearchOutcome(errors=[msg])
    out = SearchOutcome()
    for r in raw.get("results") or []:
        out.hits.append(Hit(title=r.get("title") or "", url=r.get("url") or "",
                            snippet=(r.get("content") or "")[:600]))
    return out


def verify_on_official(subject: Subject, question: str) -> SearchOutcome:
    """Search restricted to the subject's own domains."""
    domains = search_include_domains(subject)
    if not domains:
        return SearchOutcome(errors=["no official domains; refusing to search openly "
                                     "for a claim that requires ground truth"])
    res = _search(f"{subject.name} {question}", include_domains=domains)
    for h in res.hits:
        h.tier = classify(h.url, subject)
    return res


# Terms that describe a replacement rather than an article about one.
DISCOVER_TEMPLATES = (
    "{name} alternative",
    "{name} replacement tool",
    "tools like {name}",
)


def discover_alternatives(name: str, purpose: str = "") -> SearchOutcome:
    """Unfiltered discovery. Everything returned is a nomination, never evidence."""
    out = SearchOutcome()
    for tpl in DISCOVER_TEMPLATES[:2]:
        q = tpl.format(name=name)
        if purpose:
            q = f"{q} {purpose}"
        res = _search(q, max_results=6)
        out.errors += res.errors
        out.disabled = out.disabled or res.disabled
        for h in res.hits:
            h.tier = classify(h.url, None)
            if h.tier is Tier.EXCLUDED:      # never surface content farms
                continue
            if all(h.url != e.url for e in out.hits):
                out.hits.append(h)
    return out


def candidate_domains(hits: list[Hit], exclude: set[str]) -> list[str]:
    """Registrable domains worth checking as candidate tools, most-cited first."""
    counts: dict[str, int] = {}
    for h in hits:
        d = registrable(domain(h.url))
        if not d or d in exclude:
            continue
        counts[d] = counts.get(d, 0) + 1
    return [d for d, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
