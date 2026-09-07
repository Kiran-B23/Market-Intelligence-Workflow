"""Free-tier erosion, detected as the *disappearance* of free-tier language.

The old S3 path could only fire if one of ten hardcoded paid-sounding phrases happened
to appear on a page the probe happened to fetch. Nothing tracked a pricing page as its
own artifact and nothing compared it week to week, so "Spaces are now paid for new
accounts" was caught only by luck, and then as an access-wall signal rather than a
pricing one.

The load-bearing idea here is the inversion. Enumerating the ways a vendor can announce
a charge is open-ended - "requires a Pro subscription", "new accounts need a paid
plan", "included with Team" - and every phrase you forget is a miss. But the *free*
vocabulary is small, stable, and something vendors advertise loudly while it is true:
"free tier", "no credit card required", "free forever". So the signal is a phrase that
**used to be there and no longer is**. That is cheap, specific, and needs no model.

Two other things this fixes:

* The working pricing URL is discovered once and remembered, so the guessing of
  `/pricing`, `/plans`, `/#pricing` is paid once per vendor rather than every week.
* The pricing page gets its own content fingerprint, separate from the docs page, so
  a pricing rewrite is visible even when the free vocabulary survives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from miw.net import SIMHASH_DISTANCE, fetch, hash_distance, main_text, text_hash
from miw.trust import ClaimKind, Subject, official_targets

# Vocabulary vendors use while a free path exists. Small and stable by nature - that is
# the whole reason to watch for its removal rather than for the arrival of paid wording.
FREE_SIGNALS = (
    "free tier", "free plan", "free forever", "free for ever", "always free",
    "no credit card", "free to start", "free trial", "free for personal",
    "free for open source", "free for students", "free community",
    "community edition", "$0", "0/month", "free of charge", "no cost",
    "get started for free", "start for free", "try for free", "free usage",
)

# Wording that marks a restriction arriving. Kept as a secondary signal: useful when
# present, never relied on, because the space of ways to say this has no bound.
PAID_SIGNALS = (
    "requires a paid", "paid plan required", "paid plans only", "no longer free",
    "removed the free", "discontinued the free", "free tier has been",
    "new accounts", "upgrade required", "subscription required",
)

MAX_CANDIDATES = 6


@dataclass
class PricingObservation:
    dep_id: str
    url: str = ""
    reachable: bool = False
    http_status: Optional[int] = None
    text_hash: str = ""
    free_present: list[str] = field(default_factory=list)
    paid_present: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.reachable and bool(self.text_hash)


def _hits(text: str, phrases: tuple[str, ...]) -> list[str]:
    low = text.lower()
    return [p for p in phrases if p in low]


def observe_pricing(dep, known_url: str = "") -> PricingObservation:
    """Fetch the dependency's pricing page and fingerprint its free-tier vocabulary.

    `known_url` short-circuits the candidate guessing once a URL has worked before.
    """
    obs = PricingObservation(dep_id=dep.dep_id)
    subject: Subject = dep.subject()
    if not subject.official_domains:
        obs.error = "no official domain; cannot locate a pricing page"
        return obs

    candidates = ([known_url] if known_url else []) + [
        u for u in official_targets(subject, ClaimKind.PRICING)[:MAX_CANDIDATES]
        if u != known_url]

    for url in candidates:
        f = fetch(url)
        if not f.ok or not f.body:
            obs.http_status = f.status
            obs.error = f.error or f"http {f.status}"
            continue
        text = main_text(f.body)
        if len(text) < 200:
            continue
        obs.url, obs.reachable, obs.http_status = url, True, f.status
        obs.text_hash = text_hash(text)
        obs.free_present = _hits(text, FREE_SIGNALS)
        obs.paid_present = _hits(text, PAID_SIGNALS)
        obs.error = ""
        return obs
    return obs


def compare(obs: PricingObservation, prev_hash: str, prev_free: list[str]
            ) -> tuple[list[str], list[str], Optional[int]]:
    """(signals, lost_free_phrases, simhash_distance) against the previous snapshot.

    A first observation establishes the baseline and reports nothing: with no prior
    snapshot there is no such thing as a change, and guessing one would manufacture a
    finding on week one for every vendor that has a pricing page.
    """
    if not obs.usable:
        return [], [], None
    if not prev_hash:
        return ["pricing_baseline_recorded"], [], None

    signals: list[str] = []
    lost = [p for p in (prev_free or []) if p not in obs.free_present]
    if lost:
        signals.append("free_tier_language_lost")
    if obs.paid_present:
        signals.append("pricing_restriction_language")

    dist = hash_distance(prev_hash, obs.text_hash)
    if dist is not None and dist > SIMHASH_DISTANCE:
        signals.append("pricing_page_changed")
    return signals, lost, dist
