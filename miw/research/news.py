"""News about tools we already teach — as a reason to look, never as a reason to believe.

`research/launch.py` reads the same feeds and deliberately stops at a browsable list,
because judging whether an unknown product belongs in a session is an opinion about a
LEAD_ONLY source and an opinion can never be evidence here. That refusal stands and this
module does not touch it.

The question here is narrower and does not hit that wall: **has anything been said about
a dependency the curriculum already depends on?** A headline naming Groq is not evidence
that Groq changed anything — but it is a perfectly good reason to go and read Groq's own
pages this week instead of waiting for the rotation to reach it.

So this produces no claim, no finding and no severity. It produces a set of dependency
ids, which `research_all` adds to the ones the probe flagged. Everything downstream is
unchanged: `official.gather` fetches the vendor's own pages, lifts verbatim sentences and
hands them to `Claim.build`, which computes the tier against that vendor's authority set.
A finding that results rests on the vendor's page and cites it. The article is never
quoted, never stored as evidence, and never seen by a model.

This is the same shape `research/search.py` already runs on, and its comment says it
best: *search POINTS; it does not testify.*

Why it is worth the two requests. The rotation researches `ROTATION_SLICE` critical
dependencies a week — 12, against 93 that have an authority set — so a vendor announcing
something on a Tuesday waits a median four weeks to be looked at. A headline is the
cheapest signal available that the wait is the wrong call for this one.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from miw.schema import Dependency

# Names too short or too common to match on. A three-letter id matches inside words, and
# a name that is also an English word matches every article ever written — "Gamma",
# "Whisper" and "Wait" are all real dependency names in this inventory.
MIN_NAME = 4
AMBIGUOUS = frozenset({
    "whisper", "gamma", "wait", "code", "agent", "agents", "chat", "search", "vector",
    "embed", "model", "models", "cloud", "studio", "notebook", "canvas", "flow",
    "stack", "cursor", "windsurf", "lovable", "replit", "bolt", "claude", "gemini",
})
MAX_ITEMS = 100
MAX_HITS = 12


def _name_pattern(name: str) -> Optional[re.Pattern]:
    """A whole-word matcher for a name distinctive enough to match on."""
    n = (name or "").strip()
    if len(n) < MIN_NAME or n.lower() in AMBIGUOUS:
        return None
    return re.compile(rf"(?<![\w.-]){re.escape(n)}(?![\w-])", re.I)


def read_items(feeds: Iterable[tuple], fetcher=None) -> list[dict]:
    """`[{title, url, source}]` from the launch feeds. Failures are not fatal."""
    from miw.net import fetch

    _fetch = fetcher or fetch
    out: list[dict] = []
    for source, url in feeds:
        f = _fetch(url, timeout=20)
        if not (f.ok and f.body):
            continue
        try:
            hits = (json.loads(f.body) or {}).get("hits") or []
        except (json.JSONDecodeError, AttributeError):
            continue
        for h in hits[:MAX_ITEMS]:
            title = (h.get("title") or "").strip()
            if title:
                out.append({"title": title, "source": source,
                            "url": (h.get("url") or h.get("story_url") or "").strip()})
    return out


def dependencies_in_the_news(deps: Iterable[Dependency],
                             feeds: Iterable[tuple] = (),
                             fetcher=None) -> dict[str, dict]:
    """`{dep_id: {name, headline, source}}` for tracked dependencies a feed names.

    Only dependencies that can be spoken for officially: without an authority set there
    are no pages to go and read, so a mention is not actionable and pointing the
    research budget at it would waste the one thing this is spending.
    """
    from miw.research.launch import FEEDS

    items = read_items(feeds or FEEDS, fetcher=fetcher)
    if not items:
        return {}
    patterns = []
    for d in deps:
        if not d.subject().official_domains:
            continue
        pat = _name_pattern(d.canonical_name)
        if pat is not None:
            patterns.append((d, pat))

    hits: dict[str, dict] = {}
    for item in items:
        for d, pat in patterns:
            if d.dep_id in hits or not pat.search(item["title"]):
                continue
            hits[d.dep_id] = {"name": d.canonical_name, "headline": item["title"][:160],
                              "source": item["source"], "url": item["url"]}
            if len(hits) >= MAX_HITS:
                return hits
    return hits
