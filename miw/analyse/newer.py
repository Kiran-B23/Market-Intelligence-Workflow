"""S12 — a newer option from a vendor we already use.

The third enumerator feeding **Changes**, alongside `analyse/gaps.py` (concepts, from
documentation headings). Same question in a different shape: what exists in an area we
teach that we do not teach? Here the enumeration is the vendor's own catalogue — the
model tables `probe/catalogue.py` already reads, and n8n's own repository tree, which
`probe/n8n_upstream.py` already downloads daily and until now only ever consulted in
reverse, to check that a node we teach still exists.

**A set difference is not news.** That is the rule this module is built around, and it
was measured. Diffed raw against the inventory, the vendors list 45 models we do not
teach and n8n ships 527 nodes we do not teach — and almost all of that is deliberate
curriculum scoping, not change. n8n has always shipped `chainSummarization`; not
teaching it is a design decision, not something that happened this week. What is
actionable is what **appeared since we last looked**, which is why every enumerator here
goes through `State.snapshot`.

The consequence has to be said out loud wherever this is surfaced: **the first run
raises nothing.** It has no baseline to compare against, so it seeds one. A quiet first
run is the rule working, not a failure, and a UI that does not say so invites exactly
the wrong conclusion.

Not S10, which means "we searched for alternatives and verified one could do the taught
job" — a fit judgement, made by a model, about suitability. S12 asserts only what the
vendor's own table says: this exists, it is served, and we do not teach it. Different
evidence, different signal. The recommendation says *consider whether*, never *replace
with*, because nothing here establishes that the new thing is better.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from miw.analyse import notes
from miw.schema import Claim, Dependency, Finding, Location, UncitedClaim, utcnow
from miw.trust import ClaimKind, Subject

# n8n ships a node and its version variants as separate files — `agent`, `agentV1`,
# `agentV2`, `agentTool`, `agentToolV2`, `agentToolV3`. They are one node to a
# curriculum, and counting them separately is most of the difference between 692 shipped
# node types and 565 distinct ones.
_VERSION_SUFFIX = re.compile(r"(V\d+)+$")

# The catalogue is a machine list, and some of what it lists is not a teaching option.
# Each of these is a property of the ROW, never a guess about the product.
#   retired      - never suggest adopting something the vendor is already sunsetting
#   quoted_only  - "Contact Sales" is not a path a student on a free key can take


def base_node(node_type: str) -> str:
    """`@n8n/n8n-nodes-langchain.agentToolV3` -> `@n8n/n8n-nodes-langchain.agentTool`."""
    pkg, _, name = (node_type or "").rpartition(".")
    return f"{pkg}.{_VERSION_SUFFIX.sub('', name)}" if pkg else node_type


def family(identifier: str) -> str:
    """The stem a vendor varies: `gemini-2.0-flash` and `gemini-3.8-flash` share one.

    Digits and the segments after them are dropped, because a version is exactly what
    changes between a model we teach and the newer one that replaces it. What is left is
    the product line, which is the only sense in which a new row is "related" to
    something a session teaches.
    """
    ident = (identifier or "").strip().casefold()
    parts = [p for p in re.split(r"[-_/.]", ident) if p]
    keep = [p for p in parts if not any(ch.isdigit() for ch in p)]
    return "-".join(keep) or ident


@dataclass
class NewerStats:
    sources_read: int = 0
    unreadable: list = field(default_factory=list)
    seeded: list = field(default_factory=list)     # first look: baseline only
    appeared: int = 0
    already_taught: int = 0
    no_sibling: int = 0
    not_offerable: int = 0
    uncitable: list = field(default_factory=list)
    findings: int = 0


@dataclass
class NewerReport:
    findings: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    considered: set = field(default_factory=set)
    stats: NewerStats = field(default_factory=NewerStats)


def _taught_index(deps: Iterable[Dependency], kinds: tuple) -> dict:
    """`{exact casefolded id: Dependency}` for the kinds we are diffing against."""
    return {d.canonical_name.strip().casefold(): d
            for d in deps if d.kind in kinds and d.canonical_name}


def _sibling(ident: str, taught: dict, keyer: Callable) -> Optional[Dependency]:
    """A taught id from the same family, which is what makes a new row relevant.

    Without this the vendors' full catalogues qualify: a Saudi-Arabic text-to-speech
    voice is a real Groq row and has nothing to do with any session. With it, a new
    `gemini-*-flash` is interesting precisely because a session teaches
    `gemini-2.0-flash`, and the finding can name that session as a fact rather than
    guessing at one.
    """
    want = keyer(ident)
    if not want:
        return None
    best = None
    for key, dep in taught.items():
        if keyer(key) != want:
            continue
        # Prefer the sibling the curriculum leans on hardest: it is the one whose
        # session a reviewer will actually be deciding about.
        if best is None or len(dep.locations) > len(best.locations):
            best = dep
    return best


def _finding_for(*, dep_id: str, name: str, vendor: str, summary: str,
                 recommendation: str, claim: Claim, sibling: Dependency,
                 source_url: str) -> Finding:
    locs = [l for l in sibling.locations if l.session_no][:12] or sibling.locations[:12]
    f = Finding(
        dep_id=dep_id, canonical_name=name,
        signal="S12", signal_label="Newer option from a vendor we already use",
        kind_of_signal="opportunity", severity="low",
        summary=summary, courses=sorted({l.course for l in locs}),
        locations=locs, claims=[claim], affected_urls=[source_url],
        raised_at=utcnow())
    f.recommendation = recommendation
    # The same deterministic triad every other finding carries, from the same composer.
    notes.compose(Dependency(kind="model", canonical_name=name, vendor=vendor), f)
    f.what_to_act = recommendation
    return f


# Above this, a course's sessions are counted rather than listed. A taught model id is
# often referenced across most of a course, and enumerating 37 session numbers inside a
# sentence produces a recommendation nobody finishes reading — which is the same failure
# as saying nothing.
_NAME_SESSIONS_UPTO = 3


def _where(sibling: Dependency) -> str:
    """Where the sibling is taught, in words a sentence can carry."""
    bits = []
    by_course = {}
    for l in sibling.locations:
        if l.session_no:
            by_course.setdefault(l.course, set()).add(l.session_no)
    for course in sorted(by_course):
        sess = sorted(by_course[course])
        if len(sess) <= _NAME_SESSIONS_UPTO:
            bits.append(f"{course} session{'' if len(sess) == 1 else 's'} "
                        f"{', '.join(map(str, sess))}")
        else:
            bits.append(f"{course} ({len(sess)} sessions, first is {sess[0]})")
    if not bits:
        return "; ".join(sorted({l.course for l in sibling.locations})) or "this curriculum"
    return "; ".join(bits)


# --------------------------------------------------------------------- the enumerators

def _models(deps, state, rep, *, adapters=None, now: str, persist: bool = True) -> None:
    """Vendor model catalogues, diffed against the last snapshot."""
    from miw.vendors.base import all_adapters

    taught = _taught_index(deps, ("model",))
    for adapter in (adapters if adapters is not None else all_adapters()):
        key = f"catalogue:{adapter.key}"
        cat = adapter.catalogue()
        if not cat.usable:
            # The same refusal `probe/catalogue.py` makes: a page we could not bind says
            # nothing about the curriculum. Recorded loudly, because S12 depends entirely
            # on these catalogues and a silent parser break would make it report nothing
            # for ever while looking healthy.
            rep.stats.unreadable.append(
                f"{adapter.key}: {cat.error or 'catalogue not usable'}")
            continue
        rep.stats.sources_read += 1
        live = {e.entry_id for e in cat.entries.values() if not e.retired}
        seen = state.snapshot(key)
        if persist:
            state.snapshot_save(source_key=key, ids=live, now=now)
        if seen is None:
            rep.stats.seeded.append(f"{adapter.key} ({len(live)} entries)")
            continue

        for ident in sorted(live - seen):
            rep.stats.appeared += 1
            entry = cat.get(ident)
            if entry is None:
                continue
            if entry.quoted_only:
                rep.stats.not_offerable += 1
                continue
            if ident.strip().casefold() in taught:
                rep.stats.already_taught += 1
                continue
            sib = _sibling(ident, taught, family)
            if sib is None:
                rep.stats.no_sibling += 1
                continue
            subject = Subject(name=adapter.vendor,
                              official_domains=tuple(adapter.official_domains))
            url = entry.listed_url or entry.evidence_url or (cat.sources or [""])[0]
            try:
                claim = Claim.build(
                    kind=ClaimKind.EXISTENCE,
                    statement=f"{adapter.vendor} lists {ident} as available",
                    source_url=url, quote=entry.listed_quote or entry.quote,
                    subject=subject)
            except UncitedClaim as exc:
                rep.stats.uncitable.append(f"{ident}: {exc}")
                continue
            if not claim.substantiating:
                rep.stats.uncitable.append(
                    f"{ident}: {claim.source_url} classifies as {claim.tier.name}")
                continue

            dep_id = f"newer:{adapter.key}:{ident}"
            rep.considered.add(dep_id)
            price = f" at {entry.price}" if entry.price else ""
            f = _finding_for(
                dep_id=dep_id, name=ident, vendor=adapter.vendor,
                summary=(f"{adapter.vendor} now lists {ident}{price}. The curriculum "
                         f"teaches {sib.canonical_name} from the same family."),
                recommendation=(f"Consider whether {_where(sib)} should move from "
                                f"{sib.canonical_name} to {ident}. Nothing is broken — "
                                f"{sib.canonical_name} is still served."),
                claim=claim, sibling=sib, source_url=url)
            rep.findings.append(f)
            rep.rows.append({"dep_id": dep_id, "identifier": ident,
                             "vendor": adapter.vendor, "source": "catalogue",
                             "source_url": url, "price": entry.price,
                             "sibling": sib.canonical_name, "sibling_dep_id": sib.dep_id,
                             "official_domains": sorted(adapter.official_domains)})


def _n8n_nodes(deps, state, rep, *, upstream=None, now: str,
               persist: bool = True) -> None:
    """n8n's own repository tree, diffed against the last snapshot.

    We already download all 692 node types every week to check that the 41 we teach
    still exist. This reads the same data the other way round.
    """
    from miw.probe import n8n_upstream as up

    data = upstream if upstream is not None else up.upstream()
    if not data.get("ok", True) and not data.get("nodes"):
        rep.stats.unreadable.append(f"n8n: {data.get('error') or 'tree unreadable'}")
        return
    paths = data.get("node_paths") or {}
    nodes = data.get("nodes") or []
    if not nodes:
        rep.stats.unreadable.append("n8n: tree carried no node types")
        return
    if not paths:
        # A cache written before node paths were kept. Every node would fail to cite,
        # so this would otherwise print one "no repo path" line per node — hundreds of
        # them — for a condition with a single cause and a single fix. Say it once.
        rep.stats.unreadable.append(
            "n8n: the cached tree predates node paths, so nothing can be cited yet — "
            "run `python3 main.py watch` or wait for the 7-day cache to expire")
        return
    rep.stats.sources_read += 1

    taught = _taught_index(deps, ("n8n_node",))
    # One entry per node, not per version variant: `agentV2` appearing is not a new node
    # when `agent` is already taught, and treating it as one is 692 rows pretending to
    # be 565.
    live = {base_node(n) for n in nodes}
    key = "catalogue:n8n_nodes"
    seen = state.snapshot(key)
    if persist:
        state.snapshot_save(source_key=key, ids=live, now=now)
    if seen is None:
        rep.stats.seeded.append(f"n8n_nodes ({len(live)} nodes)")
        return

    taught_bases = {base_node(k): d for k, d in taught.items()}
    for node in sorted(live - seen):
        rep.stats.appeared += 1
        if node in taught_bases:
            rep.stats.already_taught += 1
            continue
        # Relevance for a node is the package it ships in — a course that builds n8n
        # workflows with langchain nodes is in the market for another langchain node,
        # and is not in the market for a CRM connector it has never touched.
        sib = _sibling(node, taught, lambda k: base_node(k).rpartition(".")[0])
        if sib is None:
            rep.stats.no_sibling += 1
            continue
        path = paths.get(node) or next(
            (p for n, p in paths.items() if base_node(n) == node), "")
        if not path:
            rep.stats.uncitable.append(f"{node}: no repo path to quote")
            continue
        url = f"https://github.com/n8n-io/n8n/blob/master/{path}"
        try:
            claim = Claim.build(
                kind=ClaimKind.EXISTENCE,
                statement=f"n8n ships the node {node}",
                source_url=url, quote=path,
                subject=Subject(name="n8n", official_domains=("n8n.io", "github.com")))
        except UncitedClaim as exc:
            rep.stats.uncitable.append(f"{node}: {exc}")
            continue
        if not claim.substantiating:
            rep.stats.uncitable.append(
                f"{node}: {url} classifies as {claim.tier.name}")
            continue

        dep_id = f"newer:n8n:{node}"
        rep.considered.add(dep_id)
        short = node.rpartition(".")[2]
        f = _finding_for(
            dep_id=dep_id, name=node, vendor="n8n",
            summary=(f"n8n now ships the {short} node. The curriculum builds n8n "
                     f"workflows in these sessions and does not use it."),
            recommendation=(f"Consider whether {_where(sib)} could use n8n's {short} "
                            f"node. Nothing is broken — this is a new option."),
            claim=claim, sibling=sib, source_url=url)
        rep.findings.append(f)
        rep.rows.append({"dep_id": dep_id, "identifier": node, "vendor": "n8n",
                         "source": "n8n_tree", "source_url": url, "price": "",
                         "sibling": sib.canonical_name, "sibling_dep_id": sib.dep_id,
                         "official_domains": ["n8n.io", "github.com"]})


def find_newer(deps, state, *, adapters=None, upstream=None,
               now: Optional[str] = None, persist: bool = True) -> NewerReport:
    """Everything a vendor we already use has added since we last looked.

    `persist=False` for a dry run. It matters more here than for any other stage: the
    snapshot IS the state this signal depends on, so a dry run that saved it would
    silently consume the one baseline it was supposed to preview against, and the next
    real run would find nothing new and report nothing — with no error anywhere.
    """
    rep = NewerReport()
    now = now or utcnow()
    _models(deps, state, rep, adapters=adapters, now=now, persist=persist)
    _n8n_nodes(deps, state, rep, upstream=upstream, now=now, persist=persist)
    rep.stats.findings = len(rep.findings)
    return rep
