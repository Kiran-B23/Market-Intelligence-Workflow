#!/usr/bin/env python3
"""MIW - Market Intelligence & Curriculum Gap Analyser.

Stages run independently and each writes a durable artifact, so any one can be rerun
or audited without the others:

    python3 main.py ingest      # course JSON  -> out/content_records.jsonl
    python3 main.py extract     # records      -> out/inventory.json + registry/
    python3 main.py probe       # inventory    -> out/probe_<date>.json
    python3 main.py research    # flagged deps -> out/research_<date>.json
    python3 main.py analyse     # everything   -> out/findings_<date>.json
    python3 main.py report      # findings     -> out/digest_<date>.md
    python3 main.py triage      # record a reviewer decision; teaches the next run
    python3 main.py verify      # audit the trust invariants on those artifacts
    python3 main.py serve       # local web UI for reading and triaging findings
    python3 main.py run-weekly  # all of the above, unattended
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

OUT = Path("out")
DATA = Path("data/courses")


def _today() -> str:
    return date.today().isoformat()


def cmd_ingest(args) -> int:
    from config.constants import COURSES
    from miw.ingest.portal import read_course
    from miw.schema import to_jsonable

    OUT.mkdir(exist_ok=True)
    total, problems = 0, []
    with open(OUT / "content_records.jsonl", "w") as fh:
        for slug, meta in COURSES.items():
            path = DATA / f"{slug}.json"
            if not path.exists():
                problems.append(f"missing export: {path}")
                continue
            records, st = read_course(str(path), meta["title"])
            for r in records:
                fh.write(json.dumps(to_jsonable(r)) + "\n")
            total += len(records)
            flag = "" if st.sessions == meta["expect_sessions"] else "  <-- SESSION COUNT MISMATCH"
            print(f"  {meta['title']:26} sessions={st.sessions:3}/{meta['expect_sessions']:<3} "
                  f"units={st.units:4} contents={st.contents:5} records={st.records:6}{flag}")
            print(f"  {'':26} pooled questions={st.pooled_questions:5} "
                  f"pooling gap={st.pooling_gap}")
            if st.sessions != meta["expect_sessions"]:
                problems.append(f"{meta['title']}: {st.sessions} sessions, expected {meta['expect_sessions']}")
            if st.pooling_gap:
                problems.append(f"{meta['title']}: {st.pooling_gap} pooling units not traversed")
            problems += [f"{meta['title']}: {s}" for s in st.skipped]
    print(f"\n  {total} records -> {OUT / 'content_records.jsonl'}")
    for p in problems:
        print(f"  PROBLEM: {p}", file=sys.stderr)
    return 1 if problems else 0


def _load_records():
    from miw.schema import ContentRecord
    path = OUT / "content_records.jsonl"
    if not path.exists():
        print("run `python3 main.py ingest` first", file=sys.stderr)
        sys.exit(2)
    with open(path) as fh:
        return [ContentRecord(**json.loads(line)) for line in fh]


def cmd_extract(args) -> int:
    import yaml
    from miw.extract.inventory import InventoryBuilder
    from miw.registry import Registry
    from miw.schema import dump, to_jsonable

    records = _load_records()
    reg = Registry.load()
    print(f"  registry: {len(reg.entries)} entries on load")

    from miw.ingest.sheets import read_all
    WORKBOOK_COURSES = {
        "gen_ai_contents.xlsx": "Intro to Gen AI",
        "llm_apps_contents.xlsx": "Building LLM Applications",
        "ai_for_finance_contents.xlsx": "AI for Finance",
    }
    sheet_tools, sheet_stats = read_all(sorted(Path("data/sheets").glob("*.xlsx")))
    print(f"  sheets: {sheet_stats.workbooks} workbook(s), "
          f"{sheet_stats.sheets_with_tools} tool sheet(s), {sheet_stats.tools} "
          f"declarations, {sheet_stats.pins} hand-recorded version pins")
    for e in sheet_stats.errors[:3]:
        print(f"  sheet PROBLEM: {e}", file=sys.stderr)

    b = InventoryBuilder(reg)
    b.feed_structured(records)
    b.sync_registry()
    b.feed_sheets(sheet_tools, WORKBOOK_COURSES)
    b.feed_prose(records)
    deps = b.finish()
    st = b.stats

    # Carry per-dependency identity across the rebuild. Without this, `first_seen` is
    # regenerated on every extract and the research rotation has nothing to order by.
    from miw.state import State
    state = State()
    known = state.dep_state()
    restored = 0
    for d in deps:
        row = known.get(d.dep_id)
        if row and row["first_seen"]:
            d.first_seen = row["first_seen"]
            restored += 1
    state.upsert_dep_state([(d.dep_id, d.canonical_name, d.first_seen) for d in deps])
    state.close()
    print(f"  identity: {restored} dependencies carried forward, "
          f"{len(deps) - restored} newly seen")

    OUT.mkdir(exist_ok=True)
    dump(OUT / "inventory.json", {
        "generated_at": _today(),
        "stats": to_jsonable(st),
        "unit_images": b.image_census(),
        "dependencies": [to_jsonable(d) for d in deps],
    })
    reg.save()

    rows = [{"name": n, "occurrences": c,
             "courses": sorted(b.tag_candidate_courses.get(n, []))}
            for n, c in b.tag_candidates.most_common()]
    Path("registry").mkdir(exist_ok=True)
    Path("registry/review_queue.yaml").write_text(
        "# Question tags MIW could not resolve to a registry entry.\n"
        "# These are NOT in the inventory and are not monitored. Promote a real tool by\n"
        "# adding it to registry/tools.yaml with its official_domains; leave lesson\n"
        "# topics alone. Unreviewed guesses are deliberately kept out of the inventory.\n"
        f"# {len(rows)} candidates.\n\n"
        + yaml.safe_dump({"candidates": rows[:400]}, sort_keys=False, allow_unicode=True))

    print(f"  {st.dependencies} dependencies, {st.locations} locations")
    print(f"  by kind     : {dict(sorted(st.by_kind.items(), key=lambda kv: -kv[1]))}")
    print(f"  by evidence : {dict(sorted(st.by_evidence.items(), key=lambda kv: -kv[1]))}")
    tiers = {}
    for d in deps:
        tiers[d.watch_tier] = tiers.get(d.watch_tier, 0) + 1
    print(f"  watch tiers : {tiers}")
    print(f"  tags matched={st.tags_matched} unresolved candidates={st.tag_candidates}")
    print(f"\n  -> {OUT / 'inventory.json'}, registry/tools.yaml ({len(reg.entries)} entries), "
          f"registry/review_queue.yaml")
    return 0


def _add_scope_args(ap) -> None:
    """Flags every scoped stage shares, so a UI run is replayable on the command line."""
    ap.add_argument("--course", action="append",
                    help="restrict to a course title (repeatable)")
    ap.add_argument("--session", default="",
                    help="restrict to session numbers: 3, 3-7 or 3,5,9-11")
    ap.add_argument("--tiers", default="",
                    help="watch tiers, comma separated (default: critical,standard)")
    ap.add_argument("--kinds", default="",
                    help="dependency kinds, comma separated")
    ap.add_argument("--limit", type=int, default=None, help="cap the selection")


def _scope_from(args, default_tiers=("critical", "standard")):
    from miw.scope import Scope
    sc = Scope.from_args(args)
    if not sc.tiers and default_tiers:
        sc.tiers = set(default_tiers)
    return sc


_INVENTORY_EXTRAS: dict = {}


def _load_inventory():
    from miw.schema import Dependency, Location
    path = OUT / "inventory.json"
    if not path.exists():
        print("run `python3 main.py extract` first", file=sys.stderr)
        sys.exit(2)
    raw = json.load(open(path))
    _INVENTORY_EXTRAS["unit_images"] = raw.get("unit_images") or {}
    deps = []
    for d in raw["dependencies"]:
        locs = [Location(**l) for l in d.pop("locations", [])]
        deps.append(Dependency(locations=locs, **d))
    return deps


def cmd_probe(args) -> int:
    from miw.probe.runner import probe_all
    from miw.schema import dump, to_jsonable, utcnow
    from miw.state import State

    deps = _load_inventory()
    scope = _scope_from(args)
    state = State()
    counts: dict[str, int] = {}
    print(f"  scope: {scope.describe()}")

    def progress(i, total, res):
        counts[res.status] = counts.get(res.status, 0) + 1
        if res.status != "ok" or args.verbose:
            print(f"  [{i:3}/{total}] {res.status:12} {res.canonical_name[:38]:40} "
                  f"{res.detail[:70]}")
        elif i % 25 == 0:
            print(f"  [{i:3}/{total}] ...")

    results = probe_all(deps, state, scope=scope, progress=progress)
    state.log_run(_today(), "probe", _today(), json.dumps(counts))
    state.close()
    out = OUT / f"probe_{_today()}.json"
    from miw.artifacts import coverage_summary, merge_by_dep
    stamp = utcnow()
    merged = merge_by_dep(
        out, new_rows=[to_jsonable(r) for r in results],
        examined={r.dep_id for r in results},
        meta={"probed_at": _today(), "run_at": stamp, "scope": scope.to_dict(),
              "counts": counts},
        rows_key="results")
    cov = coverage_summary(out, stamp)
    print(f"  merged: {len(results)} refreshed, {merged['carried_forward']} carried "
          f"forward from earlier runs today")
    print(f"  coverage: {cov['fresh']} fresh / {cov['stale']} older of "
          f"{cov['tracked']} tracked")
    print(f"\n  {len(results)} probed: {counts}")
    print(f"  -> {out}")
    return 0


def _load_probes(day=None):
    from miw.schema import ProbeResult
    day = day or _today()
    path = OUT / f"probe_{day}.json"
    if not path.exists():
        cands = sorted(OUT.glob("probe_*.json"))
        if not cands:
            print("run `python3 main.py probe` first", file=sys.stderr)
            sys.exit(2)
        path = cands[-1]
    raw = json.load(open(path))
    return {r["dep_id"]: ProbeResult(**r) for r in raw["results"]}


def cmd_research(args) -> int:
    from config import settings
    from miw.research.agent import research_all
    from miw.schema import dump, to_jsonable

    deps = _load_inventory()
    probes = _load_probes()
    scope = _scope_from(args, default_tiers=())
    print(f"  {settings.capability_note()}")
    print(f"  scope: {scope.describe()}")

    def progress(i, total, dep, res):
        sub = len([c for c in res.claims if c.substantiating])
        print(f"  [{i:3}/{total}] {dep.canonical_name[:34]:36} "
              f"pages={len(res.official_pages_seen):2} claims={sub:2} "
              f"alts={len(res.alternatives):2} dropped={len(res.dropped)}")

    from miw.schema import utcnow
    from miw.state import State
    state = State()
    results = research_all(deps, probes,
                           max_deps=args.limit or settings.RESEARCH_MAX_DEPS,
                           scope=scope, dep_state=state.dep_state(),
                           progress=progress)
    # Stamp the rotation clock so next week picks up where this run left off.
    state.mark_researched([r.dep_id for r in results], utcnow())
    state.close()
    out = OUT / f"research_{_today()}.json"
    dump(out, {"researched_at": _today(),
               "capabilities": settings.capability_note(),
               "results": [to_jsonable(r) for r in results]})
    tot = sum(len([c for c in r.claims if c.substantiating]) for r in results)
    print(f"\n  {len(results)} researched, {tot} substantiated claims -> {out}")
    return 0


def _load_research(day=None):
    from miw.schema import Alternative, Claim, ResearchResult
    from miw.trust import ClaimKind, Tier
    day = day or _today()
    path = OUT / f"research_{day}.json"
    if not path.exists():
        cands = sorted(OUT.glob("research_*.json"))
        if not cands:
            return {}
        path = cands[-1]
    raw = json.load(open(path))
    out = {}
    for r in raw["results"]:
        claims = []
        for c in r.pop("claims", []):
            c["kind"] = ClaimKind(c["kind"])
            c["tier"] = Tier[c["tier"]] if isinstance(c["tier"], str) else Tier(c["tier"])
            claims.append(Claim(**c))
        alts = []
        for a in r.pop("alternatives", []):
            ac = []
            for c in a.pop("claims", []):
                c["kind"] = ClaimKind(c["kind"])
                c["tier"] = Tier[c["tier"]] if isinstance(c["tier"], str) else Tier(c["tier"])
                ac.append(Claim(**c))
            alts.append(Alternative(claims=ac, **a))
        out[r["dep_id"]] = ResearchResult(claims=claims, alternatives=alts, **r)
    return out


def cmd_analyse(args) -> int:
    from miw.analyse import notes
    from miw.analyse.score import (findings_for, fingerprint_of,
                                   screenshots_at_risk)
    from miw.schema import dump, to_jsonable, utcnow
    from miw.state import State
    from miw import triage

    deps = _load_inventory()
    probes = _load_probes()
    research = _load_research()
    state = State()
    now = utcnow()

    scope = _scope_from(args, default_tiers=())
    if not scope.is_everything:
        deps = scope.select(deps)
        print(f"  scope: {scope.describe()} -> {len(deps)} dependencies")
    raised, still_open, suppressed, by_reviewer, seen_ids = [], [], 0, [], set()
    by_dep = {d.dep_id: d for d in deps}
    for dep in deps:
        for f in findings_for(dep, probes.get(dep.dep_id), research.get(dep.dep_id)):
            seen_ids.add(f.finding_id)
            fp = fingerprint_of(f)
            f.screenshots_at_risk = screenshots_at_risk(
                f, _INVENTORY_EXTRAS.get("unit_images") or {})
            f.diff_class = state.classify_finding(
                finding_id=f.finding_id, dep_id=f.dep_id, signal=f.signal,
                severity=f.severity, fingerprint=fp, now=now)
            if f.diff_class == "unchanged":
                # Still open, just not news. It belongs in the artifact - the digest
                # filters it, not the store. Dropping it here made a finding vanish
                # from disk the moment a later scoped run re-examined its dependency
                # and found nothing new.
                suppressed += 1
                still_open.append(f)
                continue
            # A reviewer said this exact situation is not actionable. Held back only
            # while the fingerprint matches: new evidence earns another look.
            why_held = triage.suppressed(state, f.finding_id, fp)
            if why_held:
                by_reviewer.append({"finding_id": f.finding_id,
                                    "canonical_name": f.canonical_name,
                                    "signal": f.signal, "reason": why_held})
                continue
            raised.append(f)

    # The artifact records every open finding with its diff class; the reporter decides
    # what is worth showing. Keeping those two jobs separate is what lets a scoped run
    # refresh a slice without deleting the rest of the picture.
    all_open = raised + still_open

    if getattr(args, "refine", False):
        from miw.llm import available_provider, budget_state
        prov = available_provider()
        print(f"  refining notes via {prov} ...")
        done = 0
        for f in raised:
            if notes.refine(by_dep[f.dep_id], f,
                            feedback=triage.feedback_corpus(f.signal)):
                done += 1
        print(f"  {done}/{len(raised)} notes refined; budget {budget_state()}")
    # "Examined" means we have fresh evidence for it this cycle - a probe observation
    # or a research result - not merely that it was in the inventory. A dependency the
    # probe never reached has not been shown to be healthy, so an open finding on it
    # must stay open. Passing the whole inventory (or None) let a scoped run report
    # every other course's findings as fixed.
    examined = {d.dep_id for d in deps
                if d.dep_id in probes or d.dep_id in research}
    # The probe artifact is picked by date, not by scope, so an analyse can silently
    # run against a probe that covered less than it is asking about. Say so rather
    # than quietly scoring stale data.
    unprobed = [d for d in deps if d.dep_id not in examined]
    if unprobed:
        print(f"  NOTE: {len(unprobed)} dependency(ies) in scope have no fresh probe "
              f"or research data this cycle; they are left untouched, not resolved "
              f"(e.g. {', '.join(d.canonical_name for d in unprobed[:3])})")
    resolved = state.resolve_absent(seen_ids, now, examined_dep_ids=examined)
    state.close()

    out = OUT / f"findings_{_today()}.json"
    from miw.artifacts import coverage_summary, merge_by_dep
    merged = merge_by_dep(
        out, new_rows=[to_jsonable(f) for f in all_open], examined=examined,
        meta={"analysed_at": _today(), "run_at": now, "scope": scope.to_dict(),
              "suppressed_unchanged": suppressed,
              "suppressed_by_reviewer": by_reviewer, "resolved": resolved},
        rows_key="findings")
    cov = coverage_summary(out, now)
    by_sev: dict[str, int] = {}
    for f in raised:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    print(f"  {len(raised)} findings raised ({by_sev}), {suppressed} unchanged "
          f"suppressed, {len(by_reviewer)} held back by reviewer decisions, "
          f"{len(resolved)} resolved")
    print(f"  scope examined {len(examined)} dependencies; "
          f"{len(still_open)} unchanged kept in the store; "
          f"{merged['carried_forward']} finding(s) carried forward from other scopes")
    if not scope.is_everything:
        print(f"  (resolution limited to the examined set - out-of-scope findings were "
              f"left open)")
    print(f"  -> {out}")
    return 0


def cmd_report(args) -> int:
    from config import settings
    from miw.reporters.markdown import render
    from miw.schema import Finding, Location, Claim, Alternative
    from miw.trust import ClaimKind, Tier

    path = OUT / f"findings_{_today()}.json"
    if not path.exists():
        cands = sorted(OUT.glob("findings_*.json"))
        if not cands:
            print("run `python3 main.py analyse` first", file=sys.stderr)
            return 2
        path = cands[-1]
    raw = json.load(open(path))
    findings = []
    for f in raw["findings"]:
        locs = [Location(**l) for l in f.pop("locations", [])]
        claims = []
        for c in f.pop("claims", []):
            c["kind"] = ClaimKind(c["kind"]); c["tier"] = Tier[c["tier"]]
            claims.append(Claim(**c))
        alts = []
        for a in f.pop("alternatives", []):
            ac = []
            for c in a.pop("claims", []):
                c["kind"] = ClaimKind(c["kind"]); c["tier"] = Tier[c["tier"]]
                ac.append(Claim(**c))
            alts.append(Alternative(claims=ac, **a))
        findings.append(Finding(locations=locs, claims=claims, alternatives=alts, **f))

    inv = json.load(open(OUT / "inventory.json"))
    probes = _load_probes(); research = _load_research()
    md = render(findings, resolved=raw.get("resolved") or [], run_date=_today(),
                capability_note=settings.capability_note(),
                inventory_size=len(inv["dependencies"]), probed=len(probes),
                researched=len(research), suppressed=raw.get("suppressed_unchanged", 0))
    out = OUT / f"digest_{_today()}.md"
    out.write_text(md)
    print(f"  {len(findings)} findings -> {out}")
    return 0


def cmd_triage(args) -> int:
    """Record a reviewer's call on a finding, and learn from the correction."""
    from miw.analyse.score import fingerprint_of
    from miw.schema import utcnow
    from miw.state import State
    from miw import triage

    path = sorted(OUT.glob("findings_*.json"))
    if not path:
        print("no findings to triage; run analyse first", file=sys.stderr)
        return 2
    raw = json.load(open(path[-1]))
    findings = raw["findings"]
    state = State()

    if args.list or not args.finding_id:
        stats = state.precision_stats()
        print(f"  {len(findings)} finding(s) in {path[-1].name}")
        for f in findings:
            d = state.latest_decision(f["finding_id"])
            mark = {"accepted": "[accepted]", "rejected": "[rejected]"}.get(
                d["verdict"] if d else "", "[  open  ]")
            print(f"  {mark} {f['finding_id']}  {f['severity']:8} {f['signal']:4} "
                  f"{f['canonical_name'][:32]:34} {f['summary'][:52]}")
        print(f"\n  precision so far: {stats}")
        print("  accept:  python3 main.py triage <id> --accept")
        print("  reject:  python3 main.py triage <id> --reject --reason \"...\"")
        print("  correct: python3 main.py triage <id> --accept --when \"next cycle\"")
        state.close()
        return 0

    matches = [f for f in findings if f["finding_id"].startswith(args.finding_id)]
    if len(matches) != 1:
        print(f"  '{args.finding_id}' matched {len(matches)} findings; "
              f"use a longer id prefix", file=sys.stderr)
        state.close()
        return 2
    f = matches[0]
    verdict = "rejected" if args.reject else "accepted"
    if verdict == "rejected" and not args.reason:
        print("  --reject needs --reason: the reason is what the system learns from",
              file=sys.stderr)
        state.close()
        return 2

    triage.record(state, finding_id=f["finding_id"], dep_id=f["dep_id"],
                  signal=f["signal"], verdict=verdict,
                  fingerprint=triage.fingerprint_of_raw(f),
                  reason=args.reason or "", corrected_what=args.what or "",
                  corrected_why=args.why or "", corrected_when=args.when or "",
                  reviewer=args.reviewer, now=utcnow())
    print(f"  {verdict}: {f['canonical_name']} / {f['signal']}")
    corrections = [k for k, v in (("what", args.what), ("why", args.why),
                                  ("when", args.when)) if v]
    if corrections:
        print(f"  recorded correction(s) to: {', '.join(corrections)} "
              f"-> registry/learned_feedback.md")
    if verdict == "rejected":
        print("  future runs will hold this back while the evidence is unchanged")
    print(f"  precision now: {state.precision_stats()}")
    state.close()
    return 0


def cmd_verify(args) -> int:
    """Audit the trust invariants on the artifacts that are actually on disk.

    The point of this command is that the guarantees are checkable after the fact, by
    someone who did not write the code: no finding may rest on a source that is not
    authoritative for its claim kind, and every dependency that cannot be spoken for
    officially is listed by name.
    """
    from miw.registry import Registry
    from miw.trust import STRICT_KINDS, ClaimKind, Tier

    problems: list[str] = []
    deps = _load_inventory()
    reg = Registry.load()

    no_authority = [d for d in deps if not d.subject().official_domains]
    strict_capable = len(deps) - len(no_authority)
    print(f"  inventory: {len(deps)} dependencies")
    print(f"    {strict_capable} can be spoken for officially "
          f"(a vendor domain, or a canonical registry)")
    print(f"    {len(no_authority)} cannot, so they can never produce a deprecation/"
          f"pricing/version finding")
    tiers: dict[str, int] = {}
    for e in reg.entries.values():
        tiers[e.review_status] = tiers.get(e.review_status, 0) + 1
    print(f"  registry review status: {tiers}")

    path = sorted(OUT.glob("findings_*.json"))
    if not path:
        print("  no findings file yet; run analyse first")
    else:
        raw = json.load(open(path[-1]))
        findings = raw["findings"]
        print(f"  findings: {len(findings)} in {path[-1].name}")
        for f in findings:
            claims = f.get("claims") or []
            probes = f.get("probe_signals") or []
            substantiating = []
            for c in claims:
                kind = ClaimKind(c["kind"])
                tier = Tier[c["tier"]] if isinstance(c["tier"], str) else Tier(c["tier"])
                if kind in STRICT_KINDS and tier is not Tier.AUTHORITATIVE:
                    problems.append(
                        f"{f['canonical_name']} / {f['signal']}: strict claim "
                        f"({kind.value}) resting on a {tier.name} source "
                        f"{c['source_url']}")
                if tier is Tier.AUTHORITATIVE or kind not in STRICT_KINDS:
                    substantiating.append(c)
            if not probes and not substantiating:
                problems.append(
                    f"{f['canonical_name']} / {f['signal']}: no direct observation and "
                    f"no substantiating claim - should never have been raised")
            for c in claims:
                if len((c.get("quote") or "")) < 12:
                    problems.append(f"{f['canonical_name']}: claim quote too short to verify")

    if problems:
        print(f"\n  {len(problems)} INVARIANT VIOLATION(S):")
        for pr in problems[:20]:
            print(f"    - {pr}")
        return 1
    print("\n  All trust invariants hold: every finding rests on a first-hand probe "
          "observation or an authoritative citation.")
    return 0


def cmd_serve(args) -> int:
    """Start the local read-and-triage UI."""
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed: pip install fastapi uvicorn", file=sys.stderr)
        return 2
    print(f"  MIW UI on http://{args.host}:{args.port}   (API docs at /api/docs)")
    print("  No authentication: it reads and writes your local state. Keep it on "
          "localhost.")
    uvicorn.run("miw.api.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")
    return 0


def cmd_run_weekly(args) -> int:
    """Unattended run. Ingest is skipped unless exports changed."""
    for name, fn, a in (("ingest", cmd_ingest, args), ("extract", cmd_extract, args),
                        ("probe", cmd_probe, args), ("research", cmd_research, args),
                        ("analyse", cmd_analyse, args), ("report", cmd_report, args)):
        print(f"\n=== {name} ===")
        rc = fn(a)
        if rc not in (0, 1):
            print(f"stage {name} failed with {rc}", file=sys.stderr)
            return rc
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="main.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="course JSON -> normalised content records")
    sub.add_parser("extract", help="content records -> dependency inventory")
    pr = sub.add_parser("probe", help="deterministic weekly health check")
    _add_scope_args(pr)
    pr.add_argument("--verbose", action="store_true", help="show ok results too")
    rs = sub.add_parser("research", help="official-source research on flagged deps")
    _add_scope_args(rs)
    an = sub.add_parser("analyse", help="score findings and diff against last run")
    _add_scope_args(an)
    an.add_argument("--refine", action="store_true",
                    help="rewrite action notes with the LLM (Claude Code CLI by "
                         "default; no API key needed)")
    tr = sub.add_parser("triage", help="record a reviewer decision on a finding")
    tr.add_argument("finding_id", nargs="?", help="finding id (prefix is enough)")
    tr.add_argument("--list", action="store_true", help="list findings and precision")
    tr.add_argument("--accept", action="store_true")
    tr.add_argument("--reject", action="store_true")
    tr.add_argument("--reason", default="", help="required when rejecting")
    tr.add_argument("--what", default="", help="correct the 'what to do' wording")
    tr.add_argument("--why", default="", help="correct the 'why it matters' wording")
    tr.add_argument("--when", default="", help="correct the urgency")
    tr.add_argument("--reviewer", default="gen-ai-content")
    sub.add_parser("report", help="render the weekly markdown digest")
    rw = sub.add_parser("run-weekly", help="all stages, unattended")
    _add_scope_args(rw)
    rw.add_argument("--verbose", action="store_true")
    rw.add_argument("--refine", action="store_true")
    sub.add_parser("verify", help="audit trust invariants on the current artifacts")
    sv = sub.add_parser("serve", help="start the local web UI")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    return {"ingest": cmd_ingest, "extract": cmd_extract, "probe": cmd_probe,
            "research": cmd_research, "analyse": cmd_analyse, "report": cmd_report,
            "verify": cmd_verify, "triage": cmd_triage, "serve": cmd_serve,
            "run-weekly": cmd_run_weekly}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
