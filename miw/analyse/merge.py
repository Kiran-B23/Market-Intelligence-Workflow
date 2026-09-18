"""One upstream event, one finding.

A finding's identity is `(dependency, signal)`. That is right for almost everything we
report — a dead URL belongs to the tool whose URL it is — and wrong for exactly one
class of evidence: a vendor rule that names many of our dependencies at once.

Measured on the 2026-09-18 run: 11 of the 14 n8n findings came from **7 rules**, and one
rule, `wait-node-subworkflow-v2`, produced **four**. n8n lists 16 node types on it; the
curriculum teaches Gmail, Telegram, Wait and Respond to Webhook, so the reviewer was
handed the same paragraph four times and had to work out for themselves that it was one
change and one decision.

So this module folds a rule's findings back into one, keeping the dependency with the
widest footprint as the carrier and listing the rest in `also_affects`. The carrier's
`finding_id` is one of the ids that already existed, which matters: the merged-away ids
simply stop appearing and retire through the same path as any finding that stopped being
true, instead of needing a migration.

It runs BEFORE `classify_finding`, because a diff class computed against a finding that
is about to be absorbed is a diff class about nothing.
"""
from __future__ import annotations

import copy
from typing import Optional

from miw.analyse import score
from miw.schema import Dependency, Finding

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
MAX_LOCATIONS = 12
MAX_QUESTION_IDS = 6


def _rule_of(f: Finding) -> str:
    for sig in f.probe_signals:
        if sig.startswith("n8n_rule:"):
            return sig.split(":", 1)[1]
    return ""


def _union(deps) -> Optional[Dependency]:
    """One stand-in dependency holding every distinct location the members touch.

    Only `locations` is meaningful on it: it exists so `score.blast_radius` and
    `Dependency.graded_locations` - both plain sums over `locations` - can be asked the
    merged question directly instead of being approximated by adding up their answers
    for each member. `None` when no member is in the inventory, which leaves the caller
    to fall back rather than lose the finding.
    """
    present = [d for d in deps if d is not None]
    if not present:
        return None
    out, seen = [], set()
    for dep in present:
        for l in dep.locations:
            if l not in seen:
                seen.add(l)
                out.append(l)
    stand_in = copy.copy(present[0])
    stand_in.locations = out
    return stand_in


def merge_n8n_breaks(findings: list[Finding],
                     by_dep: dict[str, Dependency]) -> tuple[list[Finding], int]:
    """Collapse S9 findings that share one breaking-change rule.

    Returns the new list and how many findings were absorbed. Order is preserved: the
    carrier keeps the position of the first member, so a caller that has already sorted
    does not need to sort again.
    """
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        if f.signal != "S9":
            continue
        rule = _rule_of(f)
        if rule:
            groups.setdefault(rule, []).append(f)

    absorbed: set[str] = set()
    for rule, members in groups.items():
        if len(members) < 2:
            continue
        # Widest footprint carries it - the same "best" rule `newer.py` uses, and for
        # the same reason: the reviewer should land on the node they will spend the
        # most time in.
        carrier = max(members, key=lambda f: (f.blast_radius, f.canonical_name))
        others = [f for f in members if f is not carrier]

        carrier.also_affects = sorted(f.canonical_name for f in others)
        carrier.severity = max(
            (f.severity for f in members),
            key=lambda s: SEVERITY_ORDER.index(s) if s in SEVERITY_ORDER else 0)
        # Measured over the UNION of the members' dependencies, not summed across them.
        # Summing multiply-counts a place two taught nodes share, which is the normal
        # case here: `wait-node-subworkflow-v2` covers four nodes listed in one
        # reference table, so the sum claimed ten places where there were two.
        # `InventoryBuilder._locate` mints a fresh `Location` per (dependency, record),
        # so identity cannot dedupe them - `Location` is a frozen dataclass whose `url`
        # is `compare=False`, and equality is the thing that means "the same place".
        footprint = _union([by_dep.get(f.dep_id) for f in members])
        if footprint is not None:
            carrier.blast_radius = score.blast_radius(footprint)
            carrier.graded_locations = footprint.graded_locations
        else:                                  # nothing to measure against; overstate
            carrier.blast_radius = sum(f.blast_radius for f in members)
            carrier.graded_locations = sum(f.graded_locations for f in members)
        carrier.questions_executing = sum(f.questions_executing for f in members)
        carrier.questions_mentioning = sum(f.questions_mentioning for f in members)
        carrier.courses = sorted({c for f in members for c in f.courses})
        carrier.question_ids = list(dict.fromkeys(
            [q for f in members for q in f.question_ids]))[:MAX_QUESTION_IDS]

        # The places the merged event reaches, re-derived rather than concatenated.
        # Each member's `locations` is already capped at 12, so pooling them cannot
        # give an honest `affects_total`; scoping each member's own dependency again
        # can, and it is the same call `score.scope_locations` makes.
        reached: list = []
        seen: set = set()
        for f in [carrier] + others:
            dep_of = by_dep.get(f.dep_id)
            if dep_of is not None and f.locations_scoped:
                got, _ = score.reaching_locations(
                    f.signal, f.affected_urls, dep_of.locations,
                    f.redirects, f.probe_signals)
            else:
                got = f.locations
            for l in got:
                if l not in seen:
                    seen.add(l)
                    reached.append(l)

        # Recomputed here because `scope_locations` - the only other writer - never
        # runs again after a merge. Every surface reads these rather than `locations`
        # (`markdown._loc_line`, the API's "N places" chip, `score.where_line`), so a
        # carrier keeping its own pre-merge counts under-reported its own reach.
        counts: dict = {}
        for l in reached:
            counts[l.object_type] = counts.get(l.object_type, 0) + 1
        carrier.affects_counts = counts
        carrier.affects_total = len(reached)

        # Strongest evidence first, then cap. Without the sort the carrier's own wired
        # locations were pushed out by an absorbed member's 32 reference-table
        # mentions, so a finding about 4 real workflow instances displayed 12 glossary
        # rows - the same confusion between naming a thing and building with it that
        # `n8n.nodes` exists to keep apart.
        reached.sort(key=lambda l: -score.EVIDENCE_WEIGHT.get(l.evidence_source, 1.0))
        carrier.locations = reached[:MAX_LOCATIONS]
        # Every member was scoped, or the merged finding cannot claim to have been.
        carrier.locations_scoped = all(f.locations_scoped for f in members)

        names = ", ".join([carrier.canonical_name] + carrier.also_affects)
        carrier.summary = (f"{carrier.summary.rstrip('.')}. Affects {len(members)} "
                           f"taught nodes: {names}.")
        absorbed.update(f.finding_id for f in others)

        # The prose was written against the carrier alone; every number in it has just
        # changed. Rewriting it here is cheaper than teaching the reader to distrust it.
        dep = by_dep.get(carrier.dep_id)
        if dep is not None:
            from miw.analyse import notes
            carrier.recommendation = score.recommend(dep, carrier)
            notes.compose(dep, carrier)

    return [f for f in findings if f.finding_id not in absorbed], len(absorbed)
