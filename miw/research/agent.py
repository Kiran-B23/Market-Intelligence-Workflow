"""Research orchestration for one dependency.

Order is the policy: **official pages first, always.** Search is consulted only for
questions the vendor's own pages did not answer, and only within those same domains
when the question is a strict one. Open search is used for exactly one purpose —
nominating a replacement we have never heard of — and its output is a lead that must
survive verification against the candidate's own official domain before it becomes a
claim.

The LLM never supplies facts. It is handed already-verified claims and asked to write
the recommendation sentence. With no LLM key the recommendation is composed from a
template instead, which is why the pipeline is fully functional without one.
"""
from __future__ import annotations

import re

from typing import Iterable, Optional

from config import settings
from miw.extract.links import service_name
from miw.net import domain
from miw.research import nominate, official, search
from miw.research.search import candidate_domains
from miw.schema import (Alternative, Claim, Dependency, ProbeResult, ResearchResult,
                        UncitedClaim)
from miw.trust import ClaimKind, Subject, Tier, classify

# Which questions a probe outcome makes worth asking.
SIGNAL_KINDS: dict[str, tuple[ClaimKind, ...]] = {
    "url_gone": (ClaimKind.EXISTENCE, ClaimKind.DEPRECATION),
    "domain_parked": (ClaimKind.EXISTENCE,),
    "registry_missing": (ClaimKind.EXISTENCE, ClaimKind.DEPRECATION),
    "registry_deprecated": (ClaimKind.DEPRECATION,),
    "sunset_language_about_subject": (ClaimKind.DEPRECATION,),
    "access_wall_language": (ClaimKind.PRICING, ClaimKind.AVAILABILITY),
    "redirected_off_path": (ClaimKind.IMPLEMENTATION,),
    "page_text_changed": (ClaimKind.IMPLEMENTATION,),
    "new_release": (ClaimKind.VERSION,),
    "n8n_new_release": (ClaimKind.VERSION,),
    "node_named_in_release_notes": (ClaimKind.VERSION, ClaimKind.IMPLEMENTATION),
    "major_behind_taught_pin": (ClaimKind.VERSION,),
    "no_release_in_2y": (ClaimKind.DEPRECATION,),
}

NEEDS_ALTERNATIVES = {"url_gone", "domain_parked", "registry_missing",
                      "registry_deprecated", "sunset_language_about_subject"}

# Speculative on the rotation, urgent on breakage: a dependency that already 404s is
# worth three candidate fetches, one that merely might have a better option is not.
MAX_CANDIDATES = 3
MAX_CANDIDATES_ROTATION = 2


def _search_purpose(dep: Dependency, limit: int = 3) -> str:
    """A few words about what the course uses this for, to narrow an open search.

    Taken from unit names, which are the curriculum's own words and already in the
    index - no course body text, so the stage's context cost does not move.
    """
    words: list[str] = []
    # The tool's own name is already in the query, so repeating it from a unit title
    # wastes one of the few words we get.
    own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", dep.canonical_name)}
    seen: set[str] = set(own)
    for loc in dep.locations:
        for w in re.findall(r"[A-Za-z][A-Za-z+.-]{3,}", loc.unit_name or ""):
            k = w.lower()
            if k in seen or k in _PURPOSE_STOP:
                continue
            seen.add(k)
            words.append(w)
            if len(words) >= limit:
                return " ".join(words)
    return " ".join(words)


# Words that say nothing about what a tool does.
_PURPOSE_STOP = {
    "session", "sessions", "part", "practice", "coding", "questions", "question",
    "reading", "resource", "material", "using", "with", "your", "from", "into",
    "intro", "introduction", "overview", "hands", "lets", "build", "building",
    "understand", "understanding", "what", "when", "where", "which", "learn",
}


def alternatives_reason(probe: Optional[ProbeResult], in_discovery_slice: bool) -> str:
    """Why alternatives are being sought: "" | breakage | rotation.

    Recorded rather than inferred, because S10 ("still works, no longer best") is only
    an honest signal when discovery ran on a HEALTHY dependency. Inferring it from
    which findings happen to exist made a stale research artifact - one carrying last
    week's alternatives against this week's clean probe - look like a discovery.
    """
    if set(probe.signals if probe else ()) & NEEDS_ALTERNATIVES:
        return "breakage"
    if in_discovery_slice and (probe is None or probe.status == "ok"):
        return "rotation"
    return ""


def kinds_for(probe: Optional[ProbeResult], dep: Dependency) -> tuple[ClaimKind, ...]:
    kinds: list[ClaimKind] = []
    for sig in (probe.signals if probe else []):
        for k in SIGNAL_KINDS.get(sig, ()):
            if k not in kinds:
                kinds.append(k)
    if not kinds:
        # Rotation sweep: ask the questions that a 200-OK page hides.
        kinds = [ClaimKind.PRICING, ClaimKind.DEPRECATION]
    return tuple(kinds)


def _verify_candidate(name: str, dom: str, nominated_by: str) -> Alternative:
    """Re-verify a nominated replacement against its own official domain."""
    subject = Subject(name=name, official_domains=(dom,),
                      homepage=f"https://{dom}").with_domains_from_urls()
    alt = Alternative(name=name, homepage=f"https://{dom}", nominated_by=nominated_by)
    probe_dep = Dependency(kind="service", canonical_name=name,
                           homepage=f"https://{dom}", official_domains=[dom])
    res = official.gather(probe_dep, kinds=(ClaimKind.PRICING, ClaimKind.AVAILABILITY))
    alt.claims = [c for c in res.claims if c.tier is Tier.AUTHORITATIVE]
    for c in alt.claims:
        low = c.quote.lower()
        if "free" in low and alt.free_student_path is None:
            alt.free_student_path = True
        if any(w in low for w in ("sign up", "create an account", "api key")):
            alt.signup_required = True
    if not alt.claims:
        alt.maturity_note = ("nominated but not verifiable from its own official pages "
                             "- needs a human look")
    return alt


def research_dependency(dep: Dependency, probe: Optional[ProbeResult] = None,
                        *, in_discovery_slice: bool = False,
                        use_model: bool = False) -> ResearchResult:
    reason = alternatives_reason(probe, in_discovery_slice)
    kinds = kinds_for(probe, dep)
    if reason == "rotation" and ClaimKind.DEPRECATION not in kinds:
        # The rotation is looking for "the vendor recommends something else while the
        # old thing still answers" - which lives in deprecation prose, so it has to be
        # asked even on a healthy dependency.
        kinds = kinds + (ClaimKind.DEPRECATION,)
    res = official.gather(dep, kinds=kinds)

    # Search, restricted to official domains, for anything the pages did not answer.
    answered = {c.kind for c in res.claims if c.substantiating}
    subject = dep.subject()
    for kind in kinds:
        if kind in answered or not subject.official_domains:
            continue
        question = {
            ClaimKind.DEPRECATION: "deprecated OR sunset OR discontinued",
            ClaimKind.PRICING: "pricing free tier limits",
            ClaimKind.VERSION: "changelog release notes latest version",
            ClaimKind.IMPLEMENTATION: "breaking change migration renamed",
            ClaimKind.EXISTENCE: "status service availability",
            ClaimKind.AVAILABILITY: "sign up requirements api key",
        }.get(kind, "")
        out = search.verify_on_official(subject, question)
        if out.disabled or out.errors:
            res.dropped += [e for e in out.errors if e not in res.dropped]
            continue
        # Search POINTS; it does not testify. A Tavily snippet was previously quoted
        # verbatim as the evidence, with no prose check and no relevance check, and
        # stamped AUTHORITATIVE because the search was domain-restricted - so n8n's
        # generic "Deprecated nodes" index page, nav chrome and all, became a critical
        # deprecation finding against seven nodes that are not deprecated.
        #
        # A hit now only tells us which URL to read. `official.gather` fetches it and
        # lifts prose under exactly the same rules as any other page, so a claim still
        # rests only on text we read ourselves.
        for h in out.hits[:2]:
            if h.url in res.official_pages_seen:
                continue
            official.gather_url(dep, h.url, kind, result=res)

    # --- alternatives: nominate, then adjudicate -------------------------
    # Every candidate goes through the same deterministic ladder whether it came from
    # the vendor's own migration notice, from search, or from a model. The nomination
    # and its verdict are recorded either way, refutations included: "we looked and it
    # is not there" is a result, and dropping it silently would be indistinguishable
    # from never having looked.
    res.discovery_reason = reason
    if reason:
        cap = MAX_CANDIDATES if reason == "breakage" else MAX_CANDIDATES_ROTATION
        # Vendor-named successors are already on `res.alternatives` from
        # `official.gather`; turn each into a nomination so it is audited like the rest.
        # Keep each vendor-named successor's citation before clearing: it lives on the
        # Alternative that `official.gather` built (cited to the OLD vendor's page),
        # not on `res.claims`, so looking for it there found nothing and the successor
        # came back unverified - silently filtered out again, one layer further on.
        vendor_claims = {a.name.casefold(): list(a.claims)
                         for a in res.alternatives if a.claims}
        vendor_named = [
            nominate.from_vendor_name(a.name, a.nominated_by)
            for a in res.alternatives if a.claims]
        res.alternatives = []

        searched: list = []
        # `purpose` narrows the query to what the SESSION does with the tool, which is
        # the difference between "Murf.AI alternative" and "Murf.AI alternative text to
        # speech voiceover". The parameter existed and was never passed.
        out = search.discover_alternatives(dep.canonical_name,
                                           purpose=_search_purpose(dep))
        if out.disabled:
            res.dropped.append(
                "alternative discovery skipped: no TAVILY_API_KEY. Vendor-named "
                "successors (if any) are still reported.")
        else:
            exclude = set(dep.official_domains) | {domain(dep.homepage)}
            for dom in candidate_domains(out.hits, exclude)[:cap]:
                url = next((h.url for h in out.hits if dom in h.url), f"https://{dom}")
                searched.append(nominate.from_search_hit(url, service_name(dom)))

        # The model nominator, last and strictly additive. `candidate_domains()` ranks
        # search hits by frequency, which is why open search offered `Youtube` as a
        # replacement for a web-app builder: it appears in every result set. A model
        # reading the taught job alongside the same snippets is better at "would this
        # do the same thing in this session", and it is the only judgement asked of it
        # - our code still resolves the domain and fetches the page.
        modelled: list = []
        if use_model:
            modelled, refutation = nominate.model_nominations(
                dep, probe.signals if probe else (), out.hits)
            if refutation:
                # The named hard case: asked about something that does not exist, an
                # agent must refute the premise rather than invent a structure for it.
                # Recorded as the model's opinion; the actual refutation is R1's DNS
                # check, which does not need a model to agree with it.
                res.premise_note = f"model: {refutation}"

        for nom in vendor_named + searched + modelled:
            nom, alt = nominate.adjudicate(nom, dep)
            res.nominations.append(nom)
            if alt is not None:
                alt.evidence = ("vendor_named" if nom.source == "vendor_named"
                                else "self")
                # A vendor-named successor keeps the citation `official.gather` built
                # from the deprecating vendor's own page; the ladder only ever adds.
                if nom.source == "vendor_named" and not alt.claims:
                    alt.claims = vendor_claims.get(nom.name.casefold(), [])
                res.alternatives.append(alt)
            elif nom.refuted:
                res.refuted.append(f"{nom.name}: {nom.verdict_detail}")

        # Fit is a JUDGEMENT, so it is only asked once the facts are settled, and only
        # for candidates whose own pages already substantiated something. It never
        # becomes a Claim and never enters the note-refinement prompt.
        if use_model:
            from miw.research import fit
            for alt in res.alternatives:
                if alt.verified:
                    alt.opinion = fit.assess(dep, alt)

    # De-duplicate vendor-named successors against discovered ones.
    seen, alts = set(), []
    for a in res.alternatives:
        key = a.name.lower()
        if key not in seen:
            seen.add(key)
            alts.append(a)
    res.alternatives = alts
    return res


def research_all(deps: Iterable[Dependency], probes: dict[str, ProbeResult], *,
                 max_deps: int = settings.RESEARCH_MAX_DEPS,
                 rotation_slice: int = settings.ROTATION_SLICE,
                 discovery_slice: int = settings.DISCOVERY_SLICE,
                 use_model: bool = False,
                 scope=None, dep_state: Optional[dict] = None,
                 progress=None) -> list[ResearchResult]:
    """Research everything the probe flagged, plus a rotating slice of critical deps.

    The rotation is what catches pricing changes and better alternatives while a URL
    is still cheerfully returning 200 - drift no deterministic probe can see.

    It is ordered by **`last_researched_at` from persistent state**, never-researched
    first. Ordering by `Dependency.first_seen` does not work: `extract` rebuilds every
    Dependency, so that field is always "now" and the sort is effectively constant -
    the same handful of dependencies got researched every week and the rest never did,
    which quietly disabled the pricing and alternative signals entirely.
    """
    # A quota or auth error earlier in the process must not silently disable search
    # for this run too.
    search.reset_preflight()
    deps = list(deps)
    if scope is not None and not scope.is_everything:
        deps = scope.select(deps)
    by_id = {d.dep_id: d for d in deps}
    flagged = [by_id[p.dep_id] for p in probes.values()
               if p.status in ("broken", "changed") and p.dep_id in by_id]
    flagged_ids = {d.dep_id for d in flagged}

    rotation = [d for d in deps
                if d.watch_tier == "critical" and d.dep_id not in flagged_ids]
    ds = dep_state or {}

    def rotation_key(d: Dependency):
        row = ds.get(d.dep_id)
        last = (row["last_researched_at"] if row else None) or ""
        # "" sorts first, so anything never researched is picked up before anything
        # that has been.
        return (last, d.canonical_name.lower())

    rotation.sort(key=rotation_key)
    todo = (flagged + rotation[:rotation_slice])[:max_deps]

    # The opportunity slice: a sub-slice of the rotation, not a second clock. A second
    # clock would fight the first, because `mark_researched` stamps every dependency
    # the stage touched. Eligible means a curriculum decision is actually possible -
    # a tool or a service with an authority set, still healthy. Packages are excluded
    # because "an alternative to `requests`" is not a curriculum question; n8n nodes
    # because that is an n8n-internal choice already covered by S9; models because the
    # same-vendor catalogue path is strictly better and already live.
    eligible = [d for d in rotation[:rotation_slice]
                if d.kind in ("tool", "service") and d.subject().official_domains
                and (probes.get(d.dep_id) is None
                     or probes[d.dep_id].status == "ok")]
    discovery = {d.dep_id for d in eligible[:discovery_slice]}

    out = []
    for i, dep in enumerate(todo, 1):
        res = research_dependency(dep, probes.get(dep.dep_id),
                                  in_discovery_slice=dep.dep_id in discovery,
                                  use_model=use_model)
        out.append(res)
        if progress:
            progress(i, len(todo), dep, res)
    return out
