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
from typing import Callable, Iterable, Optional

from miw.extract.links import registrable
from miw.net import domain, fetch, main_text
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
# Smart quotes are included because a forum post pasting a workflow renders `"id":` as
# `“id”:`, which walked straight past the straight-quote patterns and got quoted as
# deprecation evidence: `“position”: [ -700, 760 ], “name”: “Chat Memory Manager”`.
_MACHINE = re.compile(
    r'[\{\[]\s*["\u201c\u201d]'                # { "  or  [ "  (either quote style)
    r'|["\u201c\u201d]\s*:\s*[\[{"\u201c\d]'    # "key":  value
    r'|\\u00|\\/|=>|\bfunction\s*\(|;\s*\}'
    r'|^\s*[-\d]+,\s*$'                        # a bare coordinate on its own line
)


# Page titles and breadcrumbs. `Pricing | Zite - The AI builder that means business`
# passed every prose test - 0.40 caps ratio, 10 words, a lowercase bigram - and matched
# the PRICING keyword using the word "Pricing" from its own title. So a "verified"
# alternative rested on a page title that says nothing about pricing at all. Evidence
# has to be a statement, and a pipe-delimited fragment with no terminal punctuation is
# a title: real prose essentially never contains a pipe.
_TITLEISH = re.compile(r"[|•»›]|\s[–—]\s")


def _is_prose(s: str) -> bool:
    if _MACHINE.search(s):
        return False
    if _TITLEISH.search(s):
        return False
    # A fragment with no terminal punctuation is a heading, not a claim. Long text is
    # exempt because a genuine sentence can be truncated by the quote cap.
    if len(s) < 90 and not re.search(r"[.!?][\"')\]]?$", s.strip()):
        return False
    # A question is not an assertion. FAQ headings match the topic keywords perfectly
    # - "How do text characters and credits work?" was quoted as pricing evidence -
    # and they state nothing at all.
    if s.strip().endswith("?"):
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
#
# The CUE is case-insensitive because a vendor writes it at the start of a sentence -
# "Superseded by DeepWiki", "Migrate to X" - and a case-sensitive pattern missed every
# one of those. The NAME stays case-sensitive: making the whole pattern `re.I` lets the
# second group swallow ordinary lowercase words ("migrate to DeepWiki now" captured
# "DeepWiki now"), so the flags are scoped rather than global.
SUCCESSOR = re.compile(
    r"(?i:migrate to|move to|switch to|replaced by|superseded by|successor is|"
    r"please use|we recommend|we suggest|use)\s+"
    # An outer CAPTURING group around the scoped-flag group: `(?-i:...)` does not
    # capture, so without this `findall` returns the whole match, cue included.
    r"((?-i:[A-Z][\w.+-]{2,30}(?:\s[A-Z][\w.+-]{2,20})?))")

# Words that match the successor pattern but are not products. Without this, "the
# legacy endpoint is retired, please use HTTPS for all requests" nominates `HTTPS` -
# and now that a nomination carries a citation from the vendor's own page, that false
# positive arrives AUTHORITATIVE and reaches the digest looking verified.
GENERIC_TOKENS = frozenset({
    "http", "https", "api", "apis", "sdk", "cli", "gui", "ui", "url", "uri", "json",
    "yaml", "xml", "csv", "html", "css", "rest", "graphql", "grpc", "websocket",
    "oauth", "oauth2", "saml", "sso", "jwt", "ssl", "tls", "dns", "ip", "tcp", "udp",
    "version", "versions", "beta", "alpha", "ga", "stable", "latest", "preview",
    "python", "javascript", "typescript", "java", "go", "rust", "node", "nodejs",
    "docker", "kubernetes", "linux", "windows", "macos", "git", "github", "gitlab",
    "documentation", "docs", "support", "console", "dashboard", "settings", "account",
})


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text or "")
            if MIN_QUOTE <= len(s.strip()) and _is_prose(s.strip())]


def _successor_leads(text: str, quotes: list[str],
                     terms: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """(successor name, quote) pairs from a deprecation notice. Nominations, not proof.

    Vendors write the successor in a SEPARATE sentence that does not repeat the subject:
    "CodeToTutorial is deprecated. Please migrate to DeepWiki." `_relevant` keeps only
    sentences that name the subject, so the second sentence never reached the regex and
    every vendor-named successor was lost before it could be considered.

    The neighbour is read from the RAW sentence split rather than the prose-filtered
    list, because `_is_prose` rejects "Please migrate to DeepWiki." outright - 2 of its
    4 tokens are capitalised, over the 0.45 cap - which is the shortest and commonest
    form of the notice. That cap exists to stop nav chrome being quoted as *evidence*;
    a lead is not evidence, and the quote handed to `Claim.build` is the pair, whose
    first half already passed `_is_prose`. `_MACHINE` still applies to the neighbour, so
    a JSON payload cannot arrive this way.

    Proximity is used here and nowhere else, deliberately. Elsewhere nearby text is
    banned as evidence, because a status sitting near an identifier says nothing about
    it. Here the output is a *nomination*, the quote carries both sentences so a
    reviewer sees what produced it, and a name still has to survive verification
    against its own domain before anything is claimed about the tool itself.
    """
    raw = [s.strip() for s in _SENT.split(text or "") if s.strip()]
    low_terms = {t.casefold() for t in terms if t}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, sent in enumerate(raw):
        # Anchor on a sentence already accepted as a deprecation quote about the
        # subject, then look at it and its immediate neighbour only.
        if not any(sent[:60] == q[:60] for q in quotes):
            continue
        neighbour = raw[i + 1] if i + 1 < len(raw) else ""
        if neighbour and (_MACHINE.search(neighbour) or len(neighbour) < MIN_QUOTE):
            neighbour = ""
        for cand, is_self in ((sent, True), (neighbour, False)):
            for name in SUCCESSOR.findall(cand or ""):
                # `[\w.+-]` legitimately admits dots for names like `Node.js`, so it
                # also swallows the sentence-ending period. Strip trailing punctuation
                # rather than narrowing the class.
                name = name.strip().rstrip(".,;:!?)")
                key = name.casefold()
                if not name or key in seen:
                    continue
                if key in GENERIC_TOKENS or key.replace(" ", "") in GENERIC_TOKENS:
                    continue                      # a protocol is not a replacement
                if key in low_terms or any(key in t or t in key for t in low_terms):
                    continue                      # a tool cannot replace itself
                seen.add(key)
                out.append((name, (sent if is_self else f"{sent} {cand}")[:MAX_QUOTE]))
    return out


# Claim kinds whose pages are inherently about MANY subjects: a changelog lists every
# release, a deprecations index lists every retired node. On those a sentence has to
# name its subject, or you attribute one product's retirement to another - which is how
# seven n8n nodes that are not deprecated acquired critical findings.
MULTI_SUBJECT_KINDS = frozenset({
    ClaimKind.DEPRECATION, ClaimKind.VERSION, ClaimKind.IMPLEMENTATION,
})


def _site_is_the_product(dep: Dependency, url: str) -> bool:
    """Is this whole site about this one thing?

    `elevenlabs.io/pricing` is ElevenLabs' pricing; every sentence on it is about
    ElevenLabs whether or not it repeats the name, and requiring the name destroyed
    recall completely - 21 keyword-matching sentences on that page, zero survivors.

    But `ai.google.dev/pricing` and `huggingface.co/pricing` are PLATFORM pages listing
    many products, and relaxing the check there immediately attributed "5,000 free
    search requests (shared across all Gemini 3.x models)" to `gemini-2.0-flash`, and
    HuggingFace's per-TB storage pricing to a Meta model served by Groq.

    The discriminator is not the claim kind, it is whether the subject IS the site: a
    name that matches the domain stem owns everything on it. A product hosted on
    somebody's platform does not.
    """
    stem = registrable(domain(url)).split(".")[0].replace("-", "")
    if not stem:
        return False
    for name in {dep.canonical_name, *dep.aliases}:
        flat = re.sub(r"[^a-z0-9]", "", (name or "").lower())
        if flat and (flat == stem or flat.startswith(stem) or stem.startswith(flat)):
            return True
    return False


def _relevant(sentences: Iterable[str], keywords: tuple[str, ...],
              terms: tuple[str, ...], require_subject: bool = True) -> list[str]:
    """Sentences on the claim's topic, and — when the page could be about several
    things — on the claim's subject too."""
    out = []
    low_terms = [t.lower() for t in terms if t and len(t) > 2]
    for s in sentences:
        low = s.lower()
        if not any(k in low for k in keywords):
            continue
        if require_subject and low_terms and not any(t in low for t in low_terms):
            continue
        out.append(s[:MAX_QUOTE])
    return out


def gather(dep: Dependency, kinds: Optional[Iterable[ClaimKind]] = None,
           result: Optional[ResearchResult] = None,
           fetcher: Optional[Callable] = None) -> ResearchResult:
    """Fetch the dependency's own pages and lift verbatim evidence from them.

    `fetcher` is injectable so the evidence path can be exercised offline against saved
    vendor HTML - the same seam `probe/models.probe_model_dependency(adapters=...)`
    already uses. Without it, testing a nomination end to end needs the network, which
    means it does not get tested.
    """
    _fetch = fetcher or fetch
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
                f = _fetch(url)
                text = main_text(f.body) if (f.ok and f.body) else ""
                cache[url] = text
                if text and url not in res.official_pages_seen:
                    res.official_pages_seen.append(url)
                elif not f.ok:
                    res.unreadable.append(
                        f"{url}: not readable (http {f.status or f.error})")
            if not text:
                continue
            read += 1

            sents = _sentences(text)
            # The subject term is only required where the page could be about
            # something else. `official_targets` reached this URL from the subject's
            # OWN authority set, so for a pricing or access page the page is the
            # subject by construction.
            quotes = _relevant(
                sents, KIND_KEYWORDS.get(kind, ()), terms,
                require_subject=(kind in MULTI_SUBJECT_KINDS
                                 or not _site_is_the_product(dep, url)))
            for q in quotes[:2]:
                try:
                    res.claims.append(Claim.build(
                        kind=kind,
                        statement=_statement_for(kind, dep.canonical_name, q),
                        source_url=url, quote=q, subject=subject))
                except UncitedClaim as exc:
                    res.dropped.append(str(exc))

            if kind is ClaimKind.DEPRECATION:
                for name, q in _successor_leads(text, quotes, terms)[:3]:
                    alt = Alternative(
                        name=name, nominated_by=url,
                        maturity_note="named as the successor by the vendor itself")
                    # Attach the citation, or this alternative is unusable: with an
                    # empty `claims` list `Alternative.verified` is False and
                    # `score.findings_for` filters it out - so the one producer that
                    # needs no API key was silently discarding every result it found.
                    #
                    # The quote is on the OLD vendor's own authoritative page, so it is
                    # legitimately citable for exactly what that page can settle: that
                    # this vendor names this successor. NOT that the successor does the
                    # taught job, which only its own pages can establish (see
                    # agent._verify_candidate).
                    try:
                        alt.claims.append(Claim.build(
                            kind=ClaimKind.ALTERNATIVE,
                            statement=(f"{dep.canonical_name}'s own documentation "
                                       f"names {name} as the successor"),
                            source_url=url, quote=q, subject=subject))
                    except UncitedClaim as exc:
                        res.dropped.append(str(exc))
                    # `gather` walks several well-known paths per kind, and a vendor
                    # repeats its migration notice on all of them, so dedupe across the
                    # whole result rather than per page.
                    if not any(a.name.casefold() == name.casefold()
                               for a in res.alternatives):
                        res.alternatives.append(alt)
    return res


def gather_url(dep: Dependency, url: str, kind: ClaimKind,
               result: Optional[ResearchResult] = None,
               fetcher: Optional[Callable] = None) -> ResearchResult:
    """Lift evidence for one claim kind from ONE url, under the ordinary rules.

    Exists so a search hit can only ever point at a page: we fetch it and read it
    ourselves, rather than quoting the search engine's snippet. Quoting the snippet
    bypassed `_is_prose` and the subject-term check entirely, and a domain-restricted
    search made the result AUTHORITATIVE - so a vendor's generic "Deprecated nodes"
    index page became a critical deprecation finding against seven nodes that are not
    deprecated.
    """
    _fetch = fetcher or fetch
    res = result or ResearchResult(dep_id=dep.dep_id,
                                   canonical_name=dep.canonical_name)
    subject = dep.subject()
    terms = tuple({dep.canonical_name, dep.canonical_name.rsplit(".", 1)[-1],
                   dep.registry_id, *dep.aliases} - {""})
    f = _fetch(url)
    text = main_text(f.body) if (f.ok and f.body) else ""
    if not text:
        res.unreadable.append(f"{url}: not readable (http {f.status or f.error})")
        return res
    if url not in res.official_pages_seen:
        res.official_pages_seen.append(url)
    # A search hit can point anywhere on the domain, including a shared page, so the
    # subject check stays on for the multi-subject kinds here too.
    for q in _relevant(
            _sentences(text), KIND_KEYWORDS.get(kind, ()), terms,
            require_subject=(kind in MULTI_SUBJECT_KINDS
                             or not _site_is_the_product(dep, url)))[:2]:
        try:
            res.claims.append(Claim.build(
                kind=kind, statement=_statement_for(kind, dep.canonical_name, q),
                source_url=url, quote=q, subject=subject))
        except UncitedClaim as exc:
            res.dropped.append(str(exc))
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
