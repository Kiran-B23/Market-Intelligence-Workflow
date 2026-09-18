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

from miw.schema import Dependency, Finding

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
MAX_LOCATIONS = 12
MAX_QUESTION_IDS = 6


def _rule_of(f: Finding) -> str:
    for sig in f.probe_signals:
        if sig.startswith("n8n_rule:"):
            return sig.split(":", 1)[1]
    return ""


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
        carrier.blast_radius = sum(f.blast_radius for f in members)
        carrier.graded_locations = sum(f.graded_locations for f in members)
        carrier.questions_executing = sum(f.questions_executing for f in members)
        carrier.questions_mentioning = sum(f.questions_mentioning for f in members)
        carrier.courses = sorted({c for f in members for c in f.courses})
        carrier.question_ids = list(dict.fromkeys(
            [q for f in members for q in f.question_ids]))[:MAX_QUESTION_IDS]

        # Strongest evidence first, then cap. Without the sort the carrier's own wired
        # locations were pushed out by an absorbed member's 32 reference-table
        # mentions, so a finding about 4 real workflow instances displayed 12 glossary
        # rows - the same confusion between naming a thing and building with it that
        # `n8n.nodes` exists to keep apart.
        from miw.analyse.score import EVIDENCE_WEIGHT
        pool, seen = list(carrier.locations), {id(l) for l in carrier.locations}
        for f in others:
            for l in f.locations:
                if id(l) not in seen:
                    pool.append(l)
                    seen.add(id(l))
        pool.sort(key=lambda l: -EVIDENCE_WEIGHT.get(l.evidence_source, 1.0))
        carrier.locations = pool[:MAX_LOCATIONS]
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
            from miw.analyse import notes, score
            carrier.recommendation = score.recommend(dep, carrier)
            notes.compose(dep, carrier)

    return [f for f in findings if f.finding_id not in absorbed], len(absorbed)
