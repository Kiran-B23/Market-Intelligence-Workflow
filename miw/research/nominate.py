"""Adjudicating a nominated replacement: a deterministic ladder, cheapest rung first.

A nomination arrives as a name and at most a bare domain — from a vendor's own migration
notice, from a search hit, or from a model. Nothing about it is believed. This module
decides what, if anything, can be said about it, and every rung is a fact we established
ourselves:

    R0  policy          excluded host, the vendor's own domain, a generic token
    R1  does it exist?  DNS. A fabricated domain dies here, for free.
    R2  no domain       resolve by registry, or report a name-only nomination.
                        NEVER guess `https://<name>.com` - a guess that happens to
                        return 200 is invented evidence.
    R3  is it alive?    404/410 refutes. 401/403/429 does NOT - that proves the host
                        is serving. A redirect to another host is a correction, not a
                        failure: the candidate just told us its real domain.
    R4  does its own site say anything?  `official.gather` against the candidate's own
                        domain, AUTHORITATIVE claims only.

The rungs are ordered by cost so a fabrication is refuted before anything is fetched,
and the distinction R3 draws is the single highest-value line in the file: reporting our
own blocked request as a dead tool is the fastest way to lose a reviewer's trust, and
reporting a hallucinated tool as real is the fastest way to lose it permanently.

`resolver` and `fetcher` are injectable so the whole ladder is testable offline: a
fabricated domain is tested by simply being absent from a dict.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

from miw import net
from miw.extract.links import registrable, service_name
from miw.net import domain
from miw.analyse.notes import _neutralise
from miw.research import official
from miw.schema import Alternative, AlternativeNomination, Dependency, utcnow
from miw.trust import ClaimKind, Subject, Tier, classify

# A bare registrable host. Anything with a scheme, a path, a query or whitespace is
# rejected rather than repaired: a model that hands back a full URL is trying to give
# us a citation, and the one thing it may never do is choose what we fetch.
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

# Names that match a successor pattern but are not products. Without this, "the legacy
# endpoint is retired, please use HTTPS for all requests" nominates `HTTPS` - and a
# nomination now carries a citation, so that arrives AUTHORITATIVE looking verified.
GENERIC_TOKENS = frozenset({
    "http", "https", "api", "apis", "sdk", "cli", "gui", "ui", "url", "uri", "json",
    "yaml", "xml", "csv", "html", "css", "rest", "graphql", "grpc", "websocket",
    "oauth", "oauth2", "saml", "sso", "jwt", "ssl", "tls", "dns", "ip", "tcp", "udp",
    "version", "versions", "beta", "alpha", "ga", "stable", "latest", "preview",
    "python", "javascript", "typescript", "java", "go", "rust", "node", "nodejs",
    "docker", "kubernetes", "linux", "windows", "macos", "git", "documentation",
    "docs", "support", "console", "dashboard", "settings", "account", "email",
})

MAX_REDIRECT_CORRECTIONS = 2


def _reject(nom: AlternativeNomination, verdict: str, detail: str
            ) -> AlternativeNomination:
    nom.verdict, nom.verdict_detail = verdict, detail
    nom.checked_at = utcnow()
    return nom


def adjudicate(nom: AlternativeNomination, dep: Dependency, *,
               fetcher: Optional[Callable] = None,
               resolver: Optional[Callable] = None,
               ) -> tuple[AlternativeNomination, Optional[Alternative]]:
    """Run the ladder. Returns the nomination with its verdict, and an `Alternative`
    only when something was actually established.
    """
    fetch = fetcher or net.fetch
    safety = resolver or net.url_safety
    name = (nom.name or "").strip()
    dom = (nom.candidate_domain or "").strip().lower()

    # --- R0: policy, before anything is fetched --------------------------
    if not name or len(name) > 60:
        return _reject(nom, "rejected_malformed_domain", "empty or overlong name"), None
    if name.casefold() in GENERIC_TOKENS:
        return _reject(nom, "rejected_generic_token",
                       f"{name} is a protocol or generic noun, not a product"), None
    if dom:
        if not DOMAIN_RE.match(dom) or registrable(dom) != dom and dom.count(".") > 3:
            return _reject(nom, "rejected_malformed_domain",
                           f"{dom!r} is not a bare registrable host"), None
        if classify(f"https://{dom}", None) is Tier.EXCLUDED:
            return _reject(nom, "rejected_excluded",
                           f"{dom} is an excluded source"), None
        own = set(dep.official_domains) | {domain(dep.homepage)} - {""}
        if dom in own or any(dom.endswith("." + o) for o in own if o):
            return _reject(nom, "rejected_same_vendor",
                           f"{dom} already belongs to {dep.canonical_name}"), None

    # --- R2 (early): a name with no domain -------------------------------
    if not dom:
        # Never guess a domain from the name. A guessed host that happens to answer is
        # invented evidence, and it is exactly how a hallucinated tool acquires a
        # citation. A name-only nomination is reportable, with nothing claimed about it.
        if nom.source == "vendor_named":
            nom.verdict = "vendor_named"
            nom.verdict_detail = ("named by the deprecating vendor; not verified "
                                  "against its own site")
            nom.checked_at = utcnow()
            return nom, Alternative(
                name=name, nominated_by=nom.nominated_by,
                maturity_note="named as the successor by the vendor itself")
        return _reject(nom, "unresolved_name_only",
                       "no candidate domain, and none may be guessed"), None

    # --- R1: does the premise exist? DNS only, no HTTP -------------------
    verdict = safety(f"https://{dom}")
    if verdict == "unresolvable":
        return _reject(nom, "refuted_no_such_domain",
                       f"{dom} does not resolve in DNS"), None
    if verdict != "ok":
        return _reject(nom, "rejected_excluded",
                       f"{dom} is not fetchable ({verdict})"), None

    # --- R3: is it alive? ------------------------------------------------
    seen, hops, corrections = {dom}, 0, []
    while True:
        f = fetch(f"https://{dom}")
        if f.gone:
            return _reject(nom, "refuted_dead",
                           f"{dom} returns {f.status}"), None
        if f.blocked:
            # Our own blocked request is not evidence of absence. This is the same
            # distinction `net.Fetch` draws, and it is load-bearing here too.
            return _reject(nom, "unverifiable_blocked",
                           f"{dom} returned {f.status}; the host is alive but will "
                           f"not serve us"), None
        moved = domain(f.final_url) if f.final_url else ""
        if (moved and moved != dom and hops < MAX_REDIRECT_CORRECTIONS
                and moved not in seen):
            # A candidate that redirects elsewhere has just told us its real domain.
            hops += 1
            seen.add(moved)
            corrections.append(f"{dom} redirects to {moved}")
            dom = moved
            continue
        if not f.reachable:
            return _reject(nom, "unverifiable_blocked",
                           f"{dom}: {f.error or 'unreachable'}"), None
        break

    # --- R4: does its own site substantiate anything? --------------------
    probe_dep = Dependency(kind="service", canonical_name=name,
                           homepage=f"https://{dom}", official_domains=[dom])
    res = official.gather(probe_dep,
                          kinds=(ClaimKind.PRICING, ClaimKind.AVAILABILITY),
                          fetcher=fetch)
    claims = [c for c in res.claims if c.tier is Tier.AUTHORITATIVE]
    alt = Alternative(name=name, homepage=f"https://{dom}",
                      nominated_by=nom.nominated_by, claims=claims)
    for c in claims:
        low = c.quote.lower()
        if "free" in low and alt.free_student_path is None:
            alt.free_student_path = True
        if any(w in low for w in ("sign up", "create an account", "api key")):
            alt.signup_required = True

    if not claims:
        if res.unreadable and not res.official_pages_seen:
            return _reject(nom, "unverifiable_blocked",
                           f"{dom} is alive but none of its pages could be read"), None
        return _reject(nom, "refuted_no_evidence",
                       f"{dom} serves pages but none says anything checkable "
                       f"about pricing or access"), None

    nom.verdict = "verified"
    # Keep the redirect corrections. "deepwiki.io redirects to deepwiki.com" is the
    # candidate telling us its real home, and a reviewer needs to see that the domain
    # verified is not the one nominated.
    nom.candidate_domain = dom
    nom.verdict_detail = "; ".join(
        corrections + [f"{len(claims)} authoritative claim(s) on {dom}"])
    nom.checked_at = utcnow()
    alt.maturity_note = "verified on its own official pages"
    return nom, alt


# --- the model nominator ----------------------------------------------------

MAX_MODEL_NOMINATIONS = 3
_MAX_NAME = 40


def _taught_job(dep: Dependency, limit: int = 6) -> str:
    """What the curriculum does with this tool, in the curriculum's own words.

    Unit names and evidence sources only - never body text. The pipeline's context
    property is that course content is read once at ingest to build an index and never
    re-read, and a prompt that quoted lessons would end that. Six lines is enough to
    say "this is used to scaffold a web app in session 12".
    """
    seen, out = set(), []
    for loc in dep.locations:
        key = (loc.course, loc.session_no, loc.unit_name[:60])
        if key in seen:
            continue
        seen.add(key)
        where = f"session {loc.session_no}" if loc.session_no else loc.evidence_source
        out.append(f"- {loc.course} / {where} / {loc.unit_name[:70]}")
        if len(out) >= limit:
            break
    return "\n".join(out) or "- (no unit detail recorded)"


def _clean_domain(raw: str) -> str:
    """A bare registrable host, or "". Rejects rather than repairs.

    A model that hands back a full URL is trying to give us a citation, and choosing
    what we fetch is the one thing it may never do. Stripping a scheme here would be
    accepting the attempt.
    """
    d = (raw or "").strip().lower()
    if not d or not DOMAIN_RE.match(d):
        return ""
    return d if registrable(d) == d or d.count(".") <= 3 else ""


def parse_nominations(payload: object, nominated_by: str = "") -> tuple[list, str]:
    """Validate a model reply into nominations. Returns (nominations, refutation).

    Strict, and silent on failure: a malformed reply yields no nominations and the
    deterministic nominators carry on unchanged. The model is strictly additive, so
    removing it changes yield and never correctness - the same contract
    `notes.refine()` has when it returns False.
    """
    if not isinstance(payload, dict):
        return [], ""
    rows = payload.get("nominations")
    if not isinstance(rows, list):
        return [], ""
    out: list[AlternativeNomination] = []
    seen: set[str] = set()
    # Cap the ACCEPTED nominations, not the rows examined: slicing the input meant
    # three malformed entries could starve a good fourth one, so a sloppy reply lost
    # its own best candidate. Bound the scan anyway so a huge reply cannot spin.
    for row in rows[:20]:
        if len(out) >= MAX_MODEL_NOMINATIONS:
            break
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not (2 <= len(name) <= _MAX_NAME) or name.casefold() in seen:
            continue
        if "://" in name or "/" in name:
            continue                       # a URL wearing a name's clothes
        seen.add(name.casefold())
        out.append(AlternativeNomination(
            name=name,
            candidate_domain=_clean_domain(row.get("candidate_domain", "")),
            source="model", nominated_by=nominated_by,
            nominator_tier=Tier.LEAD_ONLY.name,
            # Kept for the reviewer and for the audit trail, never rendered as fact and
            # never fed back into a later prompt.
            verdict_detail=str(row.get("why_plausible") or "")[:200]))
    refutation = ""
    if payload.get("premise_refuted"):
        refutation = str(payload.get("refutation") or "")[:200]
    return out, refutation


def model_nominations(dep: Dependency, probe_signals: Iterable[str] = (),
                      hits: Iterable = ()) -> tuple[list, str]:
    """Ask a model for candidate replacements. Never raises; degrades to no candidates.

    Our code did the searching and hands over the text. The model receives no tool and
    returns no citation - only a name and, at most, a bare domain that we then resolve
    ourselves. That is what makes a fabricated candidate cheap to refute rather than
    expensive to believe.
    """
    from miw.llm import complete, load_prompt

    lines = []
    for h in list(hits)[:6]:
        title = getattr(h, "title", "") or ""
        snippet = (getattr(h, "snippet", "") or "")[:220]
        url = getattr(h, "url", "") or ""
        lines.append(f"- {_neutralise(title)[:110]}\n  {domain(url)}\n  "
                     f"{_neutralise(snippet)}")
    prompt = load_prompt(
        "nominate_alternatives_v1",
        dependency=dep.canonical_name, kind=dep.kind,
        vendor=dep.vendor or "unknown",
        homepage=dep.homepage or "unknown",
        probe_signals=", ".join(sorted(set(probe_signals))) or "nothing yet",
        taught_job=_taught_job(dep),
        search_results="\n".join(lines) or "- (no search results)")
    res = complete(prompt)
    if not res.ok:
        return [], ""
    return parse_nominations(res.json(), nominated_by=f"model:{res.provider}")


def from_vendor_name(name: str, nominated_by: str) -> AlternativeNomination:
    """A successor the deprecating vendor named. The cheapest nomination there is."""
    return AlternativeNomination(
        name=name, source="vendor_named", nominated_by=nominated_by,
        nominator_tier=Tier.AUTHORITATIVE.name)


# NOT BUILT: a keyless nominator from our own registry. The plan called for one, and
# the data refuses it. Two rankings were measured against the live inventory:
#
#   same-kind, alphabetical      -> a stock-trading API proposed for a text-to-speech
#                                   tool. "Same kind" is no signal; every SaaS product
#                                   is a `service`.
#   co-occurrence in a unit      -> ElevenLabs top for Murf.AI, Tavily top for
#                                   SerpAPI - genuinely right - but GitHub, OpenAI and
#                                   n8n co-occur with everything, being infrastructure.
#   ...divided by ubiquity       -> over-rewards anything appearing in exactly one
#                                   unit, so AssemblyAI and Discord outranked
#                                   ElevenLabs.
#
# The reason no ranking works is that co-occurrence conflates SUBSTITUTES with
# COMPLEMENTS, and those are opposites: Murf.AI and ElevenLabs are alternatives, while
# Murf.AI and Lovable are co-taught in one project. Nothing in the inventory
# distinguishes them, and the registry has no category or capability field to lean on.
#
# Shipping it anyway would spend fetches on irrelevant candidates and pad the audit
# trail with refutations that teach nobody anything - which is the failure the plan's
# own risk section warns about. The model nominator already does this judgement well
# (ElevenLabs, Bubble, WeWeb from the same inputs), so the gap is covered where it can
# be answered rather than where it cannot.


def from_search_hit(url: str, name: str = "") -> AlternativeNomination:
    """An open-web hit. LEAD_ONLY by construction: it may nominate, never substantiate."""
    dom = registrable(domain(url))
    return AlternativeNomination(
        name=name or service_name(dom), candidate_domain=dom, source="search",
        nominated_by=url, nominator_tier=classify(url, None).name)
