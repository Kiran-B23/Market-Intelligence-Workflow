"""URL health, content drift, and access-wall detection.

The URLs probed are the ones the curriculum actually links to, not just a vendor
homepage: a live marketing page says nothing about a dead deep link, and the deep link
is what a student clicks.

Nothing here reports a wall or a rewrite on its own. Almost every SaaS homepage says
"Sign in" and lists prices, so an absolute reading of those phrases would flag every
tool we teach in week one. What matters is *change* against last week's snapshot,
which is why this module records signals and leaves the judgement to `analyse`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from miw.net import Fetch, fetch, main_text, text_hash

# Phrasing that gates content, as opposed to merely offering a login.
WALL_PHRASES = (
    "sign in to continue", "log in to continue", "login to continue",
    "sign up to continue", "create an account to continue", "subscribe to continue",
    "to continue reading", "members only", "upgrade to view", "upgrade your plan",
    "start your free trial", "free trial has ended", "trial expired",
    "you have reached your limit", "quota exceeded", "requires a paid plan",
    "available on paid plans", "this feature requires",
)
# Split by strength. A vendor docs index legitimately contains "migrate to" and
# "sunset" while describing something else entirely - on Google's model docs those
# words sit next to a *different* model's deprecation notice. Weak phrases are recorded
# as flags; only strong phrases, and only near the subject's own name, move a status.
SUNSET_PHRASES_STRONG = (
    "has been deprecated", "is deprecated", "now deprecated", "no longer maintained",
    "no longer available", "has been discontinued", "is shutting down",
    "will be shut down", "has been archived", "we are winding down",
    "has been retired", "is retired", "has been sunset", "was sunset",
)
SUNSET_PHRASES_WEAK = (
    "sunset", "end of life", "end-of-life", "migrate to", "successor",
    "legacy version", "deprecation", "will be removed",
)
SUNSET_PHRASES = SUNSET_PHRASES_STRONG + SUNSET_PHRASES_WEAK

# How close a phrase must sit to the subject's name to be about the subject.
PROXIMITY_CHARS = 240
PAID_PHRASES = (
    "per month", "/month", "per seat", "billed annually", "upgrade to pro",
    "free plan", "free tier", "pricing", "credits",
)
PARKED_PHRASES = (
    "domain is for sale", "buy this domain", "this domain may be for sale",
    "parked domain", "domain parking", "expired domain",
)


def _hits(text: str, phrases: tuple[str, ...]) -> list[str]:
    low = text.lower()
    return [p for p in phrases if p in low]


def _hits_near(text: str, phrases: tuple[str, ...], terms: tuple[str, ...]) -> list[str]:
    """Phrases occurring within PROXIMITY_CHARS of one of the subject's own names.

    This is what separates "n8n has been deprecated" from an n8n docs page that
    happens to describe a deprecated *node*. Without it, every well-documented vendor
    looks like it is shutting down.
    """
    low = text.lower()
    spots = [m.start() for t in terms if t for m in re.finditer(re.escape(t.lower()), low)]
    if not spots:
        return []
    out = []
    for p in phrases:
        for m in re.finditer(re.escape(p), low):
            if any(abs(m.start() - s) <= PROXIMITY_CHARS for s in spots):
                out.append(p)
                break
    return out


@dataclass
class UrlObservation:
    url: str
    status: int | None = None
    final_url: str = ""
    redirected_off_path: bool = False
    gone: bool = False
    blocked: bool = False
    reachable: bool = False
    error: str = ""
    text_hash: str = ""
    wall_phrases: list[str] = field(default_factory=list)
    sunset_phrases: list[str] = field(default_factory=list)
    sunset_near_subject: list[str] = field(default_factory=list)
    paid_phrases: list[str] = field(default_factory=list)
    parked: bool = False
    title: str = ""


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def observe(url: str, subject_terms: tuple[str, ...] = ()) -> UrlObservation:
    f: Fetch = fetch(url)
    o = UrlObservation(
        url=url, status=f.status, final_url=f.final_url or "",
        redirected_off_path=f.redirected_off_path, gone=f.gone, blocked=f.blocked,
        reachable=f.reachable, error=f.error,
    )
    if f.body:
        text = main_text(f.body)
        o.text_hash = text_hash(text)
        o.wall_phrases = _hits(text, WALL_PHRASES)
        o.sunset_phrases = _hits(text, SUNSET_PHRASES)
        o.sunset_near_subject = _hits_near(text, SUNSET_PHRASES_STRONG, subject_terms)
        o.paid_phrases = _hits(text, PAID_PHRASES)
        o.parked = bool(_hits(text, PARKED_PHRASES))
        m = _TITLE.search(f.body)
        if m:
            o.title = re.sub(r"\s+", " ", m.group(1)).strip()[:160]
    return o
