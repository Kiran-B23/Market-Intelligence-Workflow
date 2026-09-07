"""Source authority policy — what may be treated as ground truth.

The rule this module exists to enforce: **a finding about a tool is only ever
substantiated by that tool's own official pages, or by a canonical machine-readable
registry.** Everything else can point us at something to check; nothing else can
settle it.

Authority is *relative to the subject*. `docs.n8n.io` is ground truth for a claim
about n8n and merely corroborating for a claim about Groq. That asymmetry is why this
is a function of (url, dependency) and not a global allowlist.

Four roles, in descending order of what they are allowed to do:

  AUTHORITATIVE   the vendor's own domain for this dependency, or a canonical registry
                  for this claim kind (PyPI for a Python version, the GitHub API for
                  repo status). May substantiate any claim.
  CORROBORATING   a reputable independent source. May strengthen a claim that already
                  has authoritative backing; may never be the sole basis for one.
  LEAD_ONLY       user-generated directories and comparison sites. May *nominate* a
                  candidate — this is how a replacement like deepwiki gets discovered
                  in the first place — but every factual claim about that candidate
                  must then be re-verified against its own official domain.
  EXCLUDED        content farms, scraped mirrors, SEO listicles. Never fetched, never
                  cited, never shown to the model.

The gate is `substantiates()`. `Claim.tier` is set from `classify()` at construction,
so an uncited or under-sourced claim cannot reach a finding by being phrased
confidently.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Iterable, Optional

from miw.net import domain


class Tier(enum.IntEnum):
    EXCLUDED = 0
    LEAD_ONLY = 1
    CORROBORATING = 2
    AUTHORITATIVE = 3


class ClaimKind(enum.Enum):
    """What a claim asserts. Determines which sources may settle it."""
    EXISTENCE = "existence"            # the tool is alive / dead / sunset
    DEPRECATION = "deprecation"        # officially deprecated, retired, EOL
    PRICING = "pricing"                # free tier, quota, paywall
    VERSION = "version"                # latest version, breaking release
    IMPLEMENTATION = "implementation"  # the taught flow / API / UI changed
    AVAILABILITY = "availability"      # reachable from India, signup required
    ALTERNATIVE = "alternative"        # a replacement exists and does the taught job


# Claim kinds that may never rest on anything below AUTHORITATIVE. These are the ones
# that make us change published curriculum, so "a blog said so" is not enough.
STRICT_KINDS = frozenset({
    ClaimKind.EXISTENCE, ClaimKind.DEPRECATION, ClaimKind.PRICING,
    ClaimKind.VERSION, ClaimKind.IMPLEMENTATION,
})

# Canonical registries: authoritative, but only for the claim kinds they actually
# govern. PyPI is ground truth for a package version and says nothing about pricing.
REGISTRY_AUTHORITY: dict[str, frozenset[ClaimKind]] = {
    "pypi.org": frozenset({ClaimKind.VERSION, ClaimKind.EXISTENCE, ClaimKind.DEPRECATION}),
    "files.pythonhosted.org": frozenset({ClaimKind.VERSION}),
    "registry.npmjs.org": frozenset({ClaimKind.VERSION, ClaimKind.EXISTENCE, ClaimKind.DEPRECATION}),
    "npmjs.com": frozenset({ClaimKind.VERSION, ClaimKind.EXISTENCE, ClaimKind.DEPRECATION}),
    "api.github.com": frozenset({ClaimKind.EXISTENCE, ClaimKind.DEPRECATION, ClaimKind.VERSION}),
    "github.com": frozenset({ClaimKind.EXISTENCE, ClaimKind.DEPRECATION, ClaimKind.VERSION}),
    "raw.githubusercontent.com": frozenset({ClaimKind.VERSION, ClaimKind.IMPLEMENTATION}),
    "huggingface.co": frozenset({ClaimKind.EXISTENCE, ClaimKind.VERSION}),
}

# Independent but reputable: may corroborate, never decide.
CORROBORATING_DOMAINS = frozenset({
    "stackoverflow.com", "serverfault.com", "arxiv.org", "endoflife.date",
    "developer.mozilla.org", "wikipedia.org", "techcrunch.com", "theverge.com",
    "arstechnica.com", "zdnet.com", "infoworld.com", "thenewstack.io",
    "reuters.com", "bloomberg.com", "theregister.com", "hn.algolia.com",
    "news.ycombinator.com",
})

# Directories and comparison sites. Genuinely useful for *finding* a candidate
# replacement, worthless as evidence about it.
LEAD_ONLY_DOMAINS = frozenset({
    "alternativeto.net", "producthunt.com", "g2.com", "capterra.com",
    "slant.co", "saashub.com", "sourceforge.net", "libhunt.com",
    "reddit.com", "medium.com", "dev.to", "hashnode.dev", "substack.com",
    "youtube.com", "quora.com", "linkedin.com", "x.com", "twitter.com",
})

# Never fetched and never shown to the model. Scraped mirrors and generated listicles
# are the main way a research agent ends up confidently repeating something false.
EXCLUDED_DOMAINS = frozenset({
    "topai.tools", "futurepedia.io", "aitoolhunt.com", "theresanaiforthat.com",
    "toolify.ai", "aitools.fyi", "futuretools.io", "supertools.therundown.ai",
    "geeksforgeeks.org", "w3schools.com", "tutorialspoint.com", "javatpoint.com",
    "educba.com", "simplilearn.com", "guru99.com", "codegrepper.com",
})

_EXCLUDED_HINTS = ("best-", "top-10", "top10", "-alternatives-2024", "-alternatives-2025",
                   "-alternatives-2026", "listicle", ".blogspot.", "翻译", "aggregator")


def _host_matches(host: str, allowed: Iterable[str]) -> bool:
    """True if host equals, or is a subdomain of, any allowed domain."""
    host = (host or "").lower()
    for d in allowed:
        d = d.lower()
        if host == d or host.endswith("." + d):
            return True
    return False


@dataclass(frozen=True)
class Subject:
    """The thing a claim is about, and the domains that speak for it officially.

    Built from a registry entry. `official_domains` is the authority set: a vendor's
    marketing site, its docs host, and any other host it publishes on (a status page,
    a separate changelog domain).
    """
    name: str
    official_domains: tuple[str, ...] = ()
    docs_url: str = ""
    homepage: str = ""
    changelog_url: str = ""
    pricing_url: str = ""
    status_url: str = ""

    def with_domains_from_urls(self) -> "Subject":
        """Fold any host appearing in the entry's own URLs into the authority set."""
        found = set(self.official_domains)
        for u in (self.homepage, self.docs_url, self.changelog_url,
                  self.pricing_url, self.status_url):
            d = domain(u)
            if d:
                found.add(d)
        return Subject(
            name=self.name, official_domains=tuple(sorted(found)),
            docs_url=self.docs_url, homepage=self.homepage,
            changelog_url=self.changelog_url, pricing_url=self.pricing_url,
            status_url=self.status_url,
        )


def classify(url: str, subject: Optional[Subject] = None,
             kind: Optional[ClaimKind] = None) -> Tier:
    """Authority of `url` for a claim of `kind` about `subject`."""
    host = domain(url)
    if not host:
        return Tier.EXCLUDED

    low = (url or "").lower()
    if _host_matches(host, EXCLUDED_DOMAINS) or any(h in low for h in _EXCLUDED_HINTS):
        return Tier.EXCLUDED

    # The subject's own domains outrank everything else, for every claim kind.
    if subject and _host_matches(host, subject.official_domains):
        return Tier.AUTHORITATIVE

    # A canonical registry, but only within its remit.
    for reg, kinds in REGISTRY_AUTHORITY.items():
        if _host_matches(host, [reg]):
            if kind is None or kind in kinds:
                return Tier.AUTHORITATIVE
            return Tier.CORROBORATING

    if _host_matches(host, LEAD_ONLY_DOMAINS):
        return Tier.LEAD_ONLY
    if _host_matches(host, CORROBORATING_DOMAINS):
        return Tier.CORROBORATING

    # Unknown host: usable as a pointer, never as proof. Deliberately not
    # CORROBORATING — an unrecognised domain is exactly where SEO spam lives, and
    # treating it as evidence is how a weekly report starts inventing deprecations.
    return Tier.LEAD_ONLY


def substantiates(tier: Tier, kind: ClaimKind) -> bool:
    """May a source of this tier settle a claim of this kind on its own?"""
    if kind in STRICT_KINDS:
        return tier is Tier.AUTHORITATIVE
    return tier >= Tier.CORROBORATING


def search_include_domains(subject: Subject) -> list[str]:
    """Domain filter to hand a search API so results cannot leave official ground.

    Used for every strict-kind question. Discovery of *alternatives* runs unfiltered
    and then re-verifies each candidate against its own official domains, which is the
    only way a tool we have never heard of can enter the picture at all.
    """
    return sorted(set(subject.official_domains))


# Conventional paths vendors actually use. Tried against each official domain before
# any search engine is consulted: the vendor's own changelog is both cheaper and more
# trustworthy than a search result about the vendor's changelog.
WELL_KNOWN_PATHS: dict[str, tuple[str, ...]] = {
    "changelog": ("/changelog", "/changelog/", "/releases", "/release-notes",
                  "/docs/changelog", "/whats-new", "/blog/releases"),
    "pricing": ("/pricing", "/pricing/", "/plans", "/#pricing", "/docs/pricing"),
    "deprecation": ("/docs/deprecations", "/deprecations", "/docs/migration",
                    "/docs/legacy", "/sunset"),
    "status": ("/status", "/status/"),
}


def official_targets(subject: Subject, kind: ClaimKind) -> list[str]:
    """Ordered official URLs to try for this claim kind, most specific first."""
    out: list[str] = []
    explicit = {
        ClaimKind.VERSION: subject.changelog_url,
        ClaimKind.DEPRECATION: subject.changelog_url,
        ClaimKind.PRICING: subject.pricing_url,
        ClaimKind.EXISTENCE: subject.status_url or subject.homepage,
        ClaimKind.IMPLEMENTATION: subject.docs_url,
        ClaimKind.AVAILABILITY: subject.homepage,
        ClaimKind.ALTERNATIVE: subject.homepage,
    }.get(kind, "")
    if explicit:
        out.append(explicit)

    path_key = {
        ClaimKind.VERSION: "changelog", ClaimKind.DEPRECATION: "deprecation",
        ClaimKind.PRICING: "pricing", ClaimKind.EXISTENCE: "status",
    }.get(kind)
    if path_key:
        for d in subject.official_domains:
            for p in WELL_KNOWN_PATHS[path_key]:
                out.append(f"https://{d}{p}")

    for u in (subject.docs_url, subject.homepage):
        if u:
            out.append(u)

    seen, uniq = set(), []
    for u in out:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq
