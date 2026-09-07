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

from typing import Iterable, Optional

from config import settings
from miw.extract.links import service_name
from miw.net import domain
from miw.research import official, search
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

MAX_CANDIDATES = 3


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


def research_dependency(dep: Dependency, probe: Optional[ProbeResult] = None
                        ) -> ResearchResult:
    kinds = kinds_for(probe, dep)
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
        for h in out.hits[:2]:
            try:
                res.claims.append(Claim.build(
                    kind=kind,
                    statement=f"{dep.canonical_name}: {h.title[:90]}",
                    source_url=h.url, quote=h.snippet, subject=subject))
            except UncitedClaim as exc:
                res.dropped.append(str(exc))

    # Alternatives: open discovery, then verification on the candidate's own domain.
    signals = set(probe.signals if probe else [])
    if signals & NEEDS_ALTERNATIVES:
        out = search.discover_alternatives(dep.canonical_name)
        if out.disabled:
            res.dropped.append(
                "alternative discovery skipped: no TAVILY_API_KEY. Vendor-named "
                "successors (if any) are still reported.")
        exclude = set(dep.official_domains) | {domain(dep.homepage)}
        for dom in candidate_domains(out.hits, exclude)[:MAX_CANDIDATES]:
            nominated = next((h.url for h in out.hits if dom in h.url), "")
            res.alternatives.append(
                _verify_candidate(service_name(dom), dom, nominated))

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

    out = []
    for i, dep in enumerate(todo, 1):
        res = research_dependency(dep, probes.get(dep.dep_id))
        out.append(res)
        if progress:
            progress(i, len(todo), dep, res)
    return out
