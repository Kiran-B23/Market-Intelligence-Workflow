"""New tool launches — a lead source for a human, deliberately not a finding.

The third thing that could feed **Changes**: a tool that exists in the wider market and
might belong in a session. Unlike models and n8n nodes there is no authoritative
enumeration of "tools that exist", so this can only ever be a nomination problem — a
launch feed may propose a NAME and may never substantiate anything, which is the rule
`research/nominate.py` already runs on.

**It stops at nomination, and that is a measured decision rather than caution.** The
pipeline was built to the point of counting what it would report, against the real feed:

    feed items fetched                100
      with a product URL               86
      keyword-matching a capability    42
      not already taught               42

Forty-two findings per run, and the content was "Hebbian Robotics — scalable robotics
data pipelines" filed under `market-data`, "Discovered Materials — AI agents to discover
new materials" under `agent-framework`, and twenty-two more matched on `agent-framework`
solely because the word "agent" appears in an AI launch feed. That is not a precision
problem to tune; it is the same wall PRD §29 hit and recorded: relevance cannot be
inferred from co-occurrence, and a keyword is co-occurrence with extra steps.

Judging relevance properly means asking whether a product serves a session's purpose,
from its own marketing copy, which is an opinion about a LEAD_ONLY source. Under this
system's rules an opinion can never be evidence, so it could never raise a finding
anyway. What it can honestly be is a list somebody scans.

So these are **browsable, never scored, never in the digest, no severity and no due
date** — the same treatment as the "never covered" list, for the same reason: a thing
that did not *happen* is not news, and a digest that reports forty-two of them weekly
stops being read.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

# Hacker News through Algolia: JSON, no key, and it carries the product URL alongside the
# title, which is what makes a name checkable at all. Product Hunt's RSS returned an empty
# body to a plain fetch and is not used.
FEEDS = (
    ("Launch HN", "https://hn.algolia.com/api/v1/search_by_date"
                  "?query=%22Launch%20HN%22&tags=story&hitsPerPage=50"),
    ("Show HN", "https://hn.algolia.com/api/v1/search_by_date"
                "?query=%22Show%20HN%22%20AI&tags=story&hitsPerPage=50"),
)

# Words that suggest a capability. Deliberately NOT used to raise anything — see the
# module docstring for what happened when the same match was allowed to produce findings.
# Here it only orders and labels a browsable list, where being wrong costs a reader two
# seconds rather than costing the digest its credibility.
CAPABILITY_HINTS = {
    "agent-framework": ("agent", "agents", "agentic", "orchestration"),
    "llm-api": ("llm", "gpt", "inference", "model api", "tokens"),
    "vector-db": ("vector", "embedding", "embeddings", "rag", "retrieval"),
    "search-api": ("search", "scraping", "scraper", "crawl"),
    "voice-synthesis": ("voice", "tts", "text-to-speech", "speech synthesis"),
    "speech-recognition": ("transcription", "transcribe", "speech-to-text", "stt"),
    "image-generation": ("image generation", "text-to-image", "diffusion"),
    "observability": ("observability", "tracing", "evals", "evaluation"),
    "no-code-automation": ("workflow", "no-code", "nocode", "automation"),
    "doc-processing": ("pdf", "document parsing", "ocr", "chunking"),
    "deployment": ("deploy", "hosting", "serverless"),
    "ide": ("ide", "editor", "coding assistant"),
}

_TITLE_SPLIT = re.compile(r"\s*[–—:-]\s*")
_PREFIX = re.compile(r"^(launch|show|ask)\s+hn\s*:?\s*", re.I)
_YC_TAG = re.compile(r"\s*\((?:YC\s*)?[A-Z]?\d{2,4}\)\s*", re.I)


@dataclass(frozen=True)
class Launch:
    """One nominated tool. A name, a URL, and who said so — never a fact about it."""
    name: str
    title: str
    url: str
    source: str
    capability: str = ""
    matched_on: str = ""
    points: int = 0

    @property
    def key(self) -> str:
        return (self.url or self.name).strip().casefold()


def product_name(title: str) -> str:
    """`Launch HN: Speko (YC S26) – OpenRouter for Voice AI` -> `Speko`."""
    t = _PREFIX.sub("", title or "").strip()
    t = _TITLE_SPLIT.split(t)[0]
    return _YC_TAG.sub(" ", t).strip(" .-–—")


def capability_of(title: str, wanted: Iterable[str]) -> tuple:
    """`(capability, the phrase that matched)`, or `("", "")`.

    Only capabilities the curriculum actually teaches are considered — a launch in a
    category no session touches is not a curriculum question at all.
    """
    low = (title or "").lower()
    for cap in wanted:
        for hint in CAPABILITY_HINTS.get(cap, ()):
            if re.search(rf"(?<![a-z]){re.escape(hint)}(?![a-z])", low):
                return cap, hint
    return "", ""


@dataclass
class LaunchReport:
    launches: list = field(default_factory=list)
    fetched: int = 0
    no_url: int = 0
    off_topic: int = 0
    already_taught: int = 0
    errors: list = field(default_factory=list)


def read_launches(*, capabilities: Iterable[str], taught: Iterable[str],
                  feeds: Iterable = FEEDS,
                  fetcher: Optional[Callable] = None) -> LaunchReport:
    """Nominated tools in capabilities the curriculum teaches, newest first."""
    if fetcher is None:
        from miw import net
        fetcher = net.fetch

    wanted = [c for c in capabilities if c in CAPABILITY_HINTS]
    known = {t.strip().casefold() for t in taught if t}
    rep = LaunchReport()
    seen: set = set()

    for source, url in feeds:
        got = fetcher(url)
        if not getattr(got, "ok", False) or not getattr(got, "body", ""):
            rep.errors.append(f"{source}: {getattr(got, 'status', None) or 'no response'}")
            continue
        try:
            hits = json.loads(got.body).get("hits") or []
        except (json.JSONDecodeError, AttributeError):
            rep.errors.append(f"{source}: response was not the JSON we expect")
            continue

        for h in hits:
            rep.fetched += 1
            link = (h.get("url") or "").strip()
            title = (h.get("title") or "").strip()
            if not link.startswith("http"):
                # A self-post with no product behind it cannot be checked by anyone.
                rep.no_url += 1
                continue
            cap, hint = capability_of(title, wanted)
            if not cap:
                rep.off_topic += 1
                continue
            name = product_name(title)
            if name.strip().casefold() in known:
                rep.already_taught += 1
                continue
            item = Launch(name=name, title=title, url=link, source=source,
                          capability=cap, matched_on=hint,
                          points=int(h.get("points") or 0))
            if item.key in seen:
                continue
            seen.add(item.key)
            rep.launches.append(item)

    rep.launches.sort(key=lambda l: (-l.points, l.name.lower()))
    return rep
