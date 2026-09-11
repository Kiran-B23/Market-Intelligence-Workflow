#!/usr/bin/env python3
"""MIW - Market Intelligence & Curriculum Gap Analyser.

Stages run independently and each writes a durable artifact, so any one can be rerun
or audited without the others:

    python3 main.py ingest      # course JSON  -> out/content_records.jsonl
    python3 main.py extract     # records      -> out/inventory.json + registry/
    python3 main.py watch       # vendors      -> out/signals_<date>.json  (daily)
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
import re
import sys
from datetime import date
from pathlib import Path

OUT = Path("out")
DATA = Path("data/courses")


def _today() -> str:
    return date.today().isoformat()


def _outlines_by_course() -> dict:
    """course title -> the workbook's PPT-level `Outline` object.

    Read at ingest time, not extract time, because the workbook is the only
    authoritative source for SESSION NUMBERING and numbering has to be settled before a
    single Location is written. An unmapped workbook is reported and skipped - the
    fallback to its own filename is what once invented three phantom courses.
    """
    from miw.ingest.outline import read_outline
    from miw.ingest.sheets import course_for_workbook

    out, problems = {}, []
    for book in sorted(Path("data/sheets").glob("*.xlsx")):
        title = course_for_workbook(book.name)
        if not title:
            problems.append(f"workbook {book.name!r} maps to no course in "
                            f"WORKBOOK_COURSES; its session numbering is SKIPPED")
            continue
        o = read_outline(book)
        problems += [f"{book.name}: {e}" for e in o.stats.errors]
        out[title] = o
    return out, problems


def cmd_ingest(args) -> int:
    from config.constants import COURSES
    from miw.ingest.outline import outline_records
    from miw.ingest.portal import read_course
    from miw.schema import to_jsonable

    OUT.mkdir(exist_ok=True)
    total, problems = 0, []
    outlines, problems = _outlines_by_course()
    with open(OUT / "content_records.jsonl", "w") as fh:
        for slug, meta in COURSES.items():
            path = DATA / f"{slug}.json"
            if not path.exists():
                problems.append(f"missing export: {path}")
                continue
            title = meta["title"]
            outline = outlines.get(title)
            records, st = read_course(str(path), title,
                                      outline.session_of_unit if outline else None)

            # The slide-level text, which reaches us nowhere else: measured on Intro to
            # Gen AI, 0 of 24 session outlines appear anywhere in the JSON export.
            ppt = outline_records(outline, title, str(path)) if outline else []
            for r in [*records, *ppt]:
                fh.write(json.dumps(to_jsonable(r)) + "\n")
            total += len(records) + len(ppt)

            # The workbook's session count is the curriculum's own, so it is what
            # `expect_sessions` is checked against when we have it. The positional
            # count is still printed, because a gap between the two is exactly the
            # `Common Mistakes` case and worth seeing.
            declared = outline.session_count if outline else 0
            counted = declared or st.sessions
            flag = "" if counted == meta["expect_sessions"] else "  <-- SESSION COUNT MISMATCH"
            src = "workbook" if declared else "position"
            print(f"  {title:26} sessions={counted:3}/{meta['expect_sessions']:<3} "
                  f"({src}) units={st.units:4} contents={st.contents:5} "
                  f"records={st.records:6}{flag}")
            print(f"  {'':26} pooled questions={st.pooled_questions:5} "
                  f"pooling gap={st.pooling_gap}")
            if outline:
                print(f"  {'':26} PPT outline: {st.authoritative_sessions} session(s) "
                      f"map {st.session_from_workbook} unit(s); {st.session_inferred} "
                      f"unit(s) numbered by position; {len(ppt)} slide-text record(s)")
                if st.session_conflicts:
                    # NOT a problem: the workbook winning is the correction. But it is
                    # printed, because it silently changed 81 session numbers the first
                    # time it ran and a reviewer comparing digests deserves the reason.
                    print(f"  {'':26} corrected {st.session_conflicts} unit(s) whose "
                          f"positional number disagreed with the workbook:")
                    for c in st.conflict_examples[:3]:
                        print(f"  {'':28} {c}")
                if declared and st.sessions != declared:
                    print(f"  {'':26} note: the export has {st.sessions} video "
                          f"session(s) but the workbook numbers {declared} - "
                          f"{st.sessions - declared} unit(s) look like sessions and "
                          f"are not numbered as one")
            if counted != meta["expect_sessions"]:
                problems.append(f"{title}: {counted} sessions ({src}), "
                                f"expected {meta['expect_sessions']}")
            if st.pooling_gap:
                problems.append(f"{title}: {st.pooling_gap} pooling units not traversed")
            problems += [f"{title}: {s}" for s in st.skipped]
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
    # Workbook filename -> course title. Matched on a NORMALISED stem rather than the
    # exact filename: the exact-match version silently broke when the workbooks were
    # renamed, and because the lookup fell back to the filename itself, 3,633 sheet
    # locations were quietly attributed to three phantom courses called
    # "AI for Finance - Course Contents.xlsx" and friends. An unmapped workbook is now
    # a loud problem rather than a new course.
    # The matcher itself lives in `miw/ingest/sheets.py`. It used to be duplicated here
    # as a local literal, which is precisely what that module's own comment warns
    # against - and the duplicate was not harmless: `ingest` used the shared version
    # while `extract` used this copy, so a course the shared map knew about would get
    # correct session numbering and then have EVERY workbook tool declaration and
    # version pin dropped by `extract` with only a `sheet PROBLEM` line to show for it.
    # Since S6 version-drift rests entirely on those hand-recorded pins, that course's
    # S6 findings would silently never exist.
    from miw.ingest.sheets import course_for_workbook

    sheet_tools, sheet_stats = read_all(sorted(Path("data/sheets").glob("*.xlsx")))
    seen_books = sorted({t.workbook for t in sheet_tools})
    mapping = {b: course_for_workbook(b) for b in seen_books}
    unmapped = [b for b, c in mapping.items() if not c]
    for b in unmapped:
        print(f"  sheet PROBLEM: workbook {b!r} maps to no course in WORKBOOK_COURSES; "
              f"its declarations would land in a phantom course and are SKIPPED",
              file=sys.stderr)
    WORKBOOK_COURSES = {b: c for b, c in mapping.items() if c}
    print(f"  sheets: {sheet_stats.workbooks} workbook(s), "
          f"{sheet_stats.sheets_with_tools} tool sheet(s), {sheet_stats.tools} "
          f"declarations, {sheet_stats.pins} hand-recorded version pins")
    for e in sheet_stats.errors[:3]:
        print(f"  sheet PROBLEM: {e}", file=sys.stderr)

    # (course, session-or-unit name) -> session number, from the records we just
    # loaded. The tool sheets name a session in their own words; this is what turns a
    # sheet declaration into a Location with a real session number instead of None.
    # Built by majority vote because a name can appear under two sessions (a "Part - 2"
    # unit reusing its parent's title), and the most-referenced session is the honest
    # answer rather than whichever row happened to come first.
    from collections import Counter, defaultdict
    votes: dict = defaultdict(Counter)
    for r in records:
        if not r.session_no:
            continue
        for nm in (r.unit_name, r.title):
            nm = (nm or "").strip().lower()
            if len(nm) >= 4:
                votes[(r.course, nm)][r.session_no] += 1
    session_of_name = {k: c.most_common(1)[0][0] for k, c in votes.items()}
    print(f"  session names resolvable from records: {len(session_of_name)}")

    b = InventoryBuilder(reg)
    b.feed_structured(records)
    b.sync_registry()
    b.feed_sheets(sheet_tools, WORKBOOK_COURSES, session_of_name)
    b.feed_prose(records)
    deps = b.finish()

    placed = sum(1 for d in deps for l in d.locations
                 if l.evidence_source.startswith("sheet") and l.session_no)
    unplaced = sum(1 for d in deps for l in d.locations
                   if l.evidence_source.startswith("sheet") and not l.session_no)
    print(f"  sheet locations placed on a session: {placed}, still unplaced: {unplaced}")
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
    ap.add_argument("--dep-id", action="append", dest="dep_id",
                    help="target exact dependency ids (repeatable); what a vendor "
                         "signal resolves to")
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
              f"alts={len(res.alternatives):2} rejected={len(res.dropped):2} "
              f"unreadable={len(res.unreadable):2}"
              + (f" refuted={len(res.refuted)}" if res.refuted else ""))

    from miw.schema import utcnow
    from miw.state import State
    state = State()
    if getattr(args, "nominate", False):
        from miw.llm import available_provider, budget_state
        print(f"  model nomination via {available_provider()}; "
              f"budget {budget_state()}")
    results = research_all(deps, probes,
                           max_deps=args.limit or settings.RESEARCH_MAX_DEPS,
                           scope=scope, dep_state=state.dep_state(),
                           use_model=getattr(args, "nominate", False),
                           progress=progress)
    # Stamp the rotation clock so next week picks up where this run left off.
    state.mark_researched([r.dep_id for r in results], utcnow())
    state.close()
    out = OUT / f"research_{_today()}.json"
    # Merge, do not replace. `probe` and `analyse` have gone through `merge_by_dep`
    # since a scoped run overwrote the day's probe file with its own slice; research
    # was still a plain dump, so a course-scoped research run erased every other
    # course's citations and nominations for the day. Same bug, one stage later.
    from miw.artifacts import merge_by_dep
    merged = merge_by_dep(
        out, new_rows=[to_jsonable(r) for r in results],
        examined={r.dep_id for r in results},
        meta={"researched_at": _today(), "run_at": utcnow(),
              "scope": scope.to_dict(),
              "capabilities": settings.capability_note()},
        rows_key="results")
    tot = sum(len([c for c in r.claims if c.substantiating]) for r in results)
    alts = sum(len([a for a in r.alternatives if a.verified]) for r in results)
    unread = sum(len(r.unreadable) for r in results)
    print(f"\n  {len(results)} researched, {tot} substantiated claim(s), "
          f"{alts} verified alternative(s) -> {out}")
    print(f"  merged: {len(results)} refreshed, {merged['carried_forward']} carried "
          f"forward from earlier runs today")
    if unread:
        # Said out loud because it used to hide inside `dropped` and look like
        # rejected evidence: these are guessed well-known paths that do not exist.
        print(f"  {unread} page(s) could not be read (mostly guessed well-known "
              f"paths that 404) - not rejected evidence, just absent")
    return 0


def _load_research(day=None):
    from miw.schema import (Alternative, AlternativeNomination, AlternativeOpinion,
                            Claim, ResearchResult)
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
            # Rehydrate the opinion too, or a consumer gets a raw dict where it
            # expects an object and crashes on the system's own artifact.
            op = a.pop("opinion", None)
            alt = Alternative(claims=ac, **a)
            if isinstance(op, dict):
                alt.opinion = AlternativeOpinion(
                    **{k: v for k, v in op.items()
                       if k in AlternativeOpinion.__dataclass_fields__})
            alts.append(alt)
        # Rehydrate nominations explicitly. `ResearchResult(**r)` would leave them as
        # raw dicts, and every consumer reads them as objects - `analyse` would crash
        # on the system's own artifact.
        noms = [AlternativeNomination(
                    **{k: v for k, v in n.items()
                       if k in AlternativeNomination.__dataclass_fields__})
                for n in r.pop("nominations", [])]
        out[r["dep_id"]] = ResearchResult(claims=claims, alternatives=alts,
                                          nominations=noms, **r)
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


def _nomination_tally(research) -> dict:
    """Count what discovery considered, so a refutation is reported rather than lost.

    "We looked and it is not there" is a result. Without this the digest cannot
    distinguish it from "nothing looked", which is exactly the complaint the 2026
    literature makes about citation-support metrics: they measure what survived and
    never what was rejected.
    """
    from miw.schema import AlternativeNomination as AN
    total = verified = refuted = blocked = rejected = 0
    examples = []
    for r in (research or {}).values() if isinstance(research, dict) else (research or []):
        for n in (getattr(r, "nominations", None) or []):
            total += 1
            v = getattr(n, "verdict", "")
            if v in ("verified", "vendor_named"):
                verified += 1
            elif v in AN.REFUTED:
                refuted += 1
                if len(examples) < 6:
                    examples.append(f"{n.name}: {n.verdict_detail}")
            elif v == "unverifiable_blocked":
                blocked += 1
            else:
                rejected += 1
    return {"total": total, "verified": verified, "refuted": refuted,
            "blocked": blocked, "rejected": rejected, "examples": examples}


def cmd_gaps(args) -> int:
    """S11: what the curriculum does not teach yet.

    The only stage that runs OUTSIDE-IN. Every other stage starts from the dependency
    inventory, which is extracted from the course content and therefore contains only
    what is already taught - so no other stage can ever notice that a session is
    incomplete. This one starts from `registry/topics.yaml`, reads official
    documentation as an enumeration of an area, and reports the part of that
    enumeration that appears in no session's outline.

    It writes its findings into the same `findings_<date>.json` the analyser writes, so
    a gap sorts, schedules, triages and renders exactly like every other finding. The
    per-topic evidence goes to a `gaps_<date>.json` sidecar, which `verify` reads to
    re-classify each citation without trusting the tier the artifact records - the same
    arrangement the probe artifact provides for provider widening.
    """
    from miw.analyse.curriculum import (CurriculumIndex, deck_bodies, merge_bodies,
                                        session_bodies)
    from miw.analyse.gaps import area_coverage, find_gaps, load_areas
    from miw.analyse.score import fingerprint_of
    from miw.artifacts import merge_by_dep
    from miw.schema import dump, to_jsonable, utcnow
    from miw.state import State
    from miw import triage

    outlines, problems = _outlines_by_course()
    for pr in problems:
        print(f"  WARNING: {pr}")
    if not outlines:
        print("no workbook could be mapped to a course, so no session has a "
              "description to compare against; nothing to do", file=sys.stderr)
        return 2
    # Everything the sessions actually contain, not just the workbook's summary of
    # them. The coverage check used to see 0.4% of the curriculum text already on disk.
    decks = deck_bodies()
    bodies = merge_bodies(session_bodies(), decks)
    index = CurriculumIndex.from_outlines(outlines, bodies)
    areas = load_areas(getattr(args, "topics", None) or "registry/topics.yaml")
    summary_chars = sum(len(d.outline) + len(d.key_takeaways) + len(d.session_name)
                        for d in index.docs)
    body_chars = sum(len(d.body) for d in index.docs)
    with_body = sum(1 for d in index.docs if d.body)
    print(f"  {len(index.docs)} session(s) indexed from {len(outlines)} workbook(s); "
          f"{len(areas)} curriculum area(s) declared")
    deck_chars = sum(len(t) for (c, n), t in decks.items()
                     if any(d.course == c and d.session_no == n for d in index.docs))
    print(f"  coverage reads {summary_chars + body_chars:,} char(s): "
          f"{summary_chars:,} of deck summary + {body_chars - deck_chars:,} of course "
          f"content + {deck_chars:,} of slide text "
          f"({with_body}/{len(index.docs)} session(s) have content)")
    if not decks:
        print("  NOTE: no deck artifact found — run `python3 main.py decks` so the "
              "coverage check can see what the slides actually say")
    if not areas:
        print("  registry/topics.yaml declares no area with a source; nothing to do")
        return 0

    # How much of the curriculum the declared areas can see at all. Printed before any
    # fetching, because a reader needs to know the denominator before they read the
    # numerator - "6 gaps found" means something different across 58 sessions than
    # across 71.
    cov = area_coverage(areas, index)
    print(f"  area coverage: {cov['in_an_area']}/{cov['sessions']} session(s) fall "
          f"inside at least one declared area")
    if cov["not_in_any_area"]:
        print(f"  {len(cov['not_in_any_area'])} session(s) are in NO declared area, so "
              f"no gap can ever be reported for them:")
        for line in cov["not_in_any_area"]:
            print(f"      {line}")

    scope = _scope_from(args, default_tiers=())
    courses = sorted(scope.courses) if scope.courses else []
    if courses:
        print(f"  scope: {', '.join(courses)}")

    # Placement always runs across EVERY course, even when the run is scoped, and the
    # scope then filters what gets written. A topic gap's identity is the topic, not the
    # course - the same row carries the sessions it belongs in across all of them - so a
    # scoped run that placed only within its own course replaced the global row with a
    # narrower one and silently deleted the other courses' placements. This is the same
    # rule the other scoped stages already follow: scope chooses what to LOOK at, and
    # for `report` what to WRITE, never what a finding is allowed to say.
    rep = find_gaps(areas, index)
    st = rep.stats
    if courses:
        wanted = set(courses)
        kept = [f for f in rep.findings
                if wanted & {l.course for l in f.locations}]
        print(f"  scope: {len(kept)} of {len(rep.findings)} gap(s) touch "
              f"{', '.join(courses)}; the rest are left exactly as they were")
        rep.findings = kept
    print(f"  read {st.sources_read} official source(s); {st.items_enumerated} item(s) "
          f"enumerated, {st.excluded} excluded by the registry")
    print(f"  {st.candidates} topic(s) corroborated by two independent sources; "
          f"{st.already_taught} already taught, {st.unplaced} could not be placed")
    for pr in st.sources_unsupported:
        print(f"  NOT READ: {pr}")
    for pr in st.uncorroborated_areas:
        print(f"  NO SECOND OPINION: {pr}")
    for pr in st.uncitable:
        print(f"  NOT CITEABLE: {pr}")

    if getattr(args, "dry_run", False):
        # Print what would be raised and write nothing. This is how a new source in
        # `registry/topics.yaml` gets checked before it can affect an artifact.
        for f in rep.findings:
            print(f"\n  [{f.severity}] {f.canonical_name}\n      {f.recommendation}")
        print(f"\n  dry run: {len(rep.findings)} finding(s), nothing written")
        return 0

    # Same diff and triage discipline as the analyser, for the same reason: a gap that
    # was reported last week and not acted on is not this week's news, and a reviewer
    # who rejected one must not be shown it again while the evidence is unchanged.
    state, now = State(), utcnow()
    findings_path = OUT / f"findings_{_today()}.json"
    # What this run is entitled to REMOVE from the artifact. An unscoped run examined
    # every corroborated topic, so one that no longer holds - because the session's
    # outline now covers it - is dropped, which is how a gap gets resolved. A scoped run
    # claims only the topics it kept: it looked at every course to place them, but it
    # has no mandate to delete a row it was not asked about.
    examined = set(rep.considered) if not courses else {f.dep_id for f in rep.findings}
    if not courses:
        # Plus every topic row already on file. A topic stops being a candidate for
        # more reasons than "the session now teaches it": the vendor rewrote the
        # heading, the registry excluded it, a new filter rejected it. In each case
        # `considered` no longer contains it, so its row stood forever and `verify`
        # then failed on it - the gaps sidecar no longer carries its authority set, so
        # its citations re-classify as LEAD_ONLY and a standing finding reads as
        # unsourced. That is how "Other image generation modes" outlived the filter
        # added to remove it.
        #
        # Taken from the FINDINGS artifact rather than the previous gaps sidecar, so
        # this also repairs a row already orphaned by an earlier run. An unscoped run
        # has looked at the whole declared area space, so it is entitled to retire any
        # topic finding it did not just re-raise.
        on_file = json.load(open(findings_path)) if findings_path.exists() else {}
        examined |= {f.get("dep_id") for f in (on_file.get("findings") or [])
                     if f.get("signal") == "S11" and f.get("dep_id")}
    raised, held = [], []
    for f in rep.findings:
        fp = fingerprint_of(f)
        f.diff_class = state.classify_finding(
            finding_id=f.finding_id, dep_id=f.dep_id, signal=f.signal,
            severity=f.severity, fingerprint=fp, now=now)
        why = triage.suppressed(state, f.finding_id, fp)
        if why:
            held.append({"finding_id": f.finding_id,
                         "canonical_name": f.canonical_name,
                         "signal": f.signal, "reason": why})
            continue
        raised.append(f)
    state.close()

    side = OUT / f"gaps_{_today()}.json"
    dump(side, {"generated_at": now, "areas": [a.area_id for a in areas],
                "scope": scope.to_dict(), "topics": rep.rows,
                "coverage": cov, "stats": to_jsonable(st)})
    merge_by_dep(
        findings_path, new_rows=[to_jsonable(f) for f in raised], examined=examined,
        meta={"gaps_at": _today(), "gaps_run_at": now,
              "gaps_held_by_reviewer": held},
        rows_key="findings")
    print(f"  {len(raised)} gap finding(s) written ({len(held)} held by reviewer "
          f"decisions)")
    print(f"  -> {side}")
    print(f"  -> {findings_path}")
    return 0


def cmd_decks(args) -> int:
    """Read the session decks: their text, and whether they are still reachable.

    Its own command rather than a step inside `gaps`, because the cost profile is
    completely different - 85 fetches of 0.6-14MB against a host that throttles, on a
    curriculum's revision cadence rather than a news cycle. `gaps` reads the artifact
    this writes and never fetches a deck itself, so the weekly run stays fast and works
    offline.

    Health comes free: we had to open the deck to read it, so a 404, or a deck that is
    no longer published to us, is observed on the way past rather than needing a second
    pass over the same 85 URLs.
    """
    from miw.ingest import decks as deckmod
    from miw.schema import dump, utcnow

    outlines, problems = _outlines_by_course()
    for pr in problems:
        print(f"  WARNING: {pr}")
    urls = deckmod.deck_urls(outlines)
    scope = _scope_from(args, default_tiers=())
    if scope.courses:
        urls = {k: v for k, v in urls.items() if k[0] in scope.courses}
        print(f"  scope: {', '.join(sorted(scope.courses))}")
    if not urls:
        print("  no session carries a deck URL; nothing to do")
        return 0

    ttl = 0 if getattr(args, "refresh", False) else deckmod.CACHE_TTL_S
    print(f"  {len(urls)} session(s) with a deck URL")
    rows = []
    counts = {"supported": 0, "restricted": 0, "gone": 0, "unreachable": 0}
    for i, ((course, session), url) in enumerate(sorted(urls.items()), 1):
        deck = deckmod.read_deck(url, ttl=ttl)
        state = ("ok" if deck.supported else "gone" if deck.gone
                 else "restricted" if deck.restricted else "unreachable")
        counts["supported" if state == "ok" else state] += 1
        rows.append({"course": course, "session_no": session, "url": url,
                     "status": deck.status, "supported": deck.supported,
                     "gone": deck.gone, "restricted": deck.restricted,
                     "reason": deck.reason, "slides": len(deck.slides),
                     "chars": len(deck.text), "text": deck.text,
                     "fetched_at": deck.fetched_at, "from_cache": deck.from_cache})
        if state != "ok" or getattr(args, "verbose", False):
            print(f"  [{i:3}/{len(urls)}] {state:11} {course[:22]:24} s{session:<3} "
                  f"{deck.reason[:52]}")

    total = sum(r["chars"] for r in rows)
    print(f"  {counts['supported']} read ({total:,} chars of slide text), "
          f"{counts['restricted']} not published to us, {counts['gone']} gone, "
          f"{counts['unreachable']} unreachable")
    out = OUT / f"decks_{_today()}.json"
    dump(out, {"generated_at": utcnow(), "counts": counts, "decks": rows})
    print(f"  -> {out}")
    return 0


def cmd_report(args) -> int:
    from config import settings
    from miw.reporters.markdown import render
    from miw.schema import (Alternative, AlternativeOpinion, Claim, Finding,
                            Location)
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
            # Rehydrate the opinion too, or a consumer gets a raw dict where it
            # expects an object and crashes on the system's own artifact.
            op = a.pop("opinion", None)
            alt = Alternative(claims=ac, **a)
            if isinstance(op, dict):
                alt.opinion = AlternativeOpinion(
                    **{k: v for k, v in op.items()
                       if k in AlternativeOpinion.__dataclass_fields__})
            alts.append(alt)
        findings.append(Finding(locations=locs, claims=claims, alternatives=alts, **f))

    inv = json.load(open(OUT / "inventory.json"))
    probes = _load_probes(); research = _load_research()
    # The roll-up is ALWAYS re-rendered, and always from the whole merged artifact.
    # Scoping `report` narrows which per-course digest it WRITES, never which findings
    # it READS — anything else reinvents the bug that had a 91-dependency slice
    # presented as the week's 229-dependency state.
    noms = _nomination_tally(research)
    md = render(findings, resolved=raw.get("resolved") or [], run_date=_today(),
                capability_note=settings.capability_note(),
                inventory_size=len(inv["dependencies"]), probed=len(probes),
                researched=len(research), suppressed=raw.get("suppressed_unchanged", 0),
                nominations=noms)
    out = OUT / f"digest_{_today()}.md"
    out.write_text(md)
    print(f"  {len(findings)} findings -> {out}")

    # --- and one digest per course ----------------------------------------
    from miw.analyse.project import project_all
    from miw.schema import Dependency as _Dep
    from miw.scope import resolve_courses, slug_of

    deps_by_id = {}
    for d in inv["dependencies"]:
        locs = [Location(**{k: v for k, v in l.items()
                            if k in Location.__dataclass_fields__})
                for l in d.get("locations", [])]
        deps_by_id[d["dep_id"]] = _Dep(
            **{k: v for k, v in d.items()
               if k in _Dep.__dataclass_fields__ and k != "locations"}, locations=locs)

    all_courses = sorted({l.course for dep in deps_by_id.values()
                          for l in dep.locations if l.course})
    wanted = sorted(resolve_courses(args.course)) if getattr(args, "course", None) \
        else all_courses
    for course in wanted:
        if course not in all_courses:
            print(f"  ! no course named {course!r} in the inventory", file=sys.stderr)
            continue
        local = project_all(findings, deps_by_id, course)
        # Per-course digests live in out/courses/<slug>/ because `_latest()` globs
        # `digest_*.md` lexicographically: a top-level digest_pse_<date>.md sorts AFTER
        # digest_<date>.md and would silently become "the" digest.
        cdir = OUT / "courses" / slug_of(course)
        cdir.mkdir(parents=True, exist_ok=True)
        cmd_md = render(local, resolved=[], run_date=_today(),
                        capability_note=settings.capability_note(),
                        inventory_size=sum(1 for d in deps_by_id.values()
                                           if any(l.course == course for l in d.locations)),
                        probed=len(probes), researched=len(research),
                        suppressed=sum(1 for f in local if f.diff_class == "unchanged"),
                        course=course)
        cpath = cdir / f"digest_{_today()}.md"
        cpath.write_text(cmd_md)
        reported = sum(1 for f in local if f.diff_class != "unchanged")
        print(f"    {course:28} {len(local):2} finding(s) ({reported} reported) "
              f"-> {cpath.relative_to(OUT.parent)}")
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


def cmd_watch(args) -> int:
    """Daily entry point: did a vendor move, and what does it touch?

    The other entry point is manual and already exists — `--course` / `--session` on any
    stage, or the Run tab. This one is unattended. It reads no course content and calls
    no model: a signal names identifiers, the inventory is an index keyed by those
    identifiers, and resolution is a dict lookup.
    """
    import subprocess
    from miw.schema import dump, to_jsonable
    from miw.state import State
    from miw.watch import poll_all, resolve_signal

    deps = _load_inventory()
    state = State()

    def progress(source, outcome, detail):
        if outcome != "unchanged" or args.verbose:
            print(f"  {outcome:11} {source:34} {detail}")

    signals, stats = poll_all(deps, state,
                              packages=not args.no_packages,
                              package_limit=args.package_limit,
                              progress=progress)
    print(f"\n  polled {stats['vendor_sources']} vendor catalogue(s) + "
          f"{stats['package_sources']} package(s)")
    print(f"  {stats['changed']} changed · {stats['baseline']} baseline · "
          f"{stats['unchanged']} unchanged · {stats['unreadable']} unreadable")

    out = OUT / f"signals_{_today()}.json"
    dump(out, {"polled_at": _today(), "stats": stats,
               "signals": [to_jsonable(s) for s in signals]})

    if not signals:
        print("  no vendor moved since the last poll")
        state.close()
        return 0

    print(f"\n{len(signals)} signal(s) to resolve:")
    plans = []
    for sig in signals:
        scope = resolve_signal(sig, deps)
        names = sorted({d.canonical_name for d in deps if d.dep_id in scope.dep_ids})
        print(f"   {sig.vendor_key}:{sig.trigger:14} -> "
              f"{len(scope.dep_ids)} taught dependency(ies) "
              f"{names[:4]}{'...' if len(names) > 4 else ''}")
        if scope.dep_ids:
            plans.append((sig, scope))
        else:
            # A vendor moved something we do not teach. Recorded, not investigated.
            state.signal_mark(sig.signal_id, "investigated")

    if not args.investigate:
        print("\n  pass --investigate to run the scoped stages for these")
        state.close()
        return 0

    for sig, scope in plans:
        print(f"\n=== investigating {sig.source_key} "
              f"({len(scope.dep_ids)} dependency(ies)) ===")
        for stage in ("probe", "analyse"):
            cmd = [sys.executable, "-u", "main.py", stage] + scope.to_cli_args()
            rc = subprocess.run(cmd, cwd=str(Path.cwd())).returncode
            if rc not in (0, 1):
                print(f"  stage {stage} exited {rc}", file=sys.stderr)
                break
        state.signal_mark(sig.signal_id, "investigated")
    subprocess.run([sys.executable, "-u", "main.py", "report"], cwd=str(Path.cwd()))
    state.close()
    return 0


def cmd_resolve_packages(args) -> int:
    """Retype sheet-declared names that are really distributions, verified on a registry.

    A workbook records `pydantic@2.11.10` in a tools column. `feed_sheets` has no way to
    know that is a PyPI distribution rather than a SaaS product, so it defaults to
    `kind: tool` - and a `tool` with no vendor domain has no authority set, so it can
    never produce a version, pricing or deprecation finding. Ten such entries were
    hand-corrected once; ten more arrived on the next export, because every new sheet
    declaration lands domainless forever. This is that fix, made repeatable.

    It is deterministic and evidence-based, not a guess: a name is retyped only if the
    registry actually serves a project under it, and the registry then becomes its
    authority via `Dependency._REGISTRY_HOME`. Names that resolve nowhere are listed by
    name rather than silently skipped, because that remainder is the real backlog.
    """
    from miw.probe import registries as R
    from miw.registry import Registry

    reg = Registry.load()
    deps = _load_inventory()
    # A hand-recorded VERSION PIN is the strong signal: nobody writes `pydantic@2.11.10`
    # about a SaaS product. A bare declaration with no pin is weak - "Telegram" is
    # sheet-declared and is not a distribution - so those are only checked with
    # `--include-unpinned`, which costs a registry round trip per name.
    def _pinned(d):
        return any(l.evidence_source == "sheet_pin" for l in d.locations)
    candidates = [d for d in deps
                  if d.kind == "tool" and not d.official_domains and not d.registry
                  and (_pinned(d) or args.include_unpinned)]
    pinned = {d.canonical_name for d in candidates}
    if args.only:
        pinned &= set(args.only)
    print(f"  {len(pinned)} sheet-declared name(s) with no authority to check")

    retyped, unresolved = [], []
    for name in sorted(pinned):
        entry = reg.resolve(name, "tool")
        if entry is None:
            continue
        hit = None
        for which, fn in (("pypi", R.pypi), ("npm", R.npm)):
            info = fn(name)
            if info.get("found"):
                hit = (which, info)
                break
        if not hit:
            unresolved.append(name)
            print(f"    {name:34} no registry project - stays a tool")
            continue
        which, info = hit
        # Re-key: `Entry.key` is `kind:norm(name)`, so changing kind moves the entry.
        reg.entries.pop(entry.key, None)
        entry.kind = "package"
        entry.registry = which
        entry.registry_id = name
        entry.review_status = "approved"
        entry.notes = ((entry.notes or "") + f" | retyped tool->package: {which} serves "
                       f"a project under this name, so the registry is its authority"
                       ).strip(" |")
        reg.add(entry)
        retyped.append((name, which, info.get("latest_version") or "?"))
        print(f"    {name:34} -> {which} (latest {info.get('latest_version')})")

    if args.dry_run:
        print(f"\n  dry run: {len(retyped)} would be retyped, nothing written")
        return 0
    reg.save()
    print(f"\n  {len(retyped)} retyped, {len(unresolved)} still unresolved "
          f"-> registry/tools.yaml")
    if unresolved:
        print(f"  unresolved (need a human to add an official domain): "
              f"{', '.join(unresolved[:12])}"
              + (f" +{len(unresolved) - 12} more" if len(unresolved) > 12 else ""))
    print("  run `python3 main.py extract` to pick the new kinds up")
    return 0


def _read_latest(pattern: str) -> dict:
    """The newest artifact matching `pattern`, or {}."""
    files = sorted(OUT.glob(pattern))
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def cmd_agent(args) -> int:
    """Run the agent graphs over one dependency, or a slice of the inventory.

    Acquisition first (`signal_agent`, which is where a model plans), then impact
    (`impact_agent`, which has no model in it). The impact graph stops at its review
    gate: the run is suspended and durable, so nothing is reported until a human
    resumes it. That is a real LangGraph interrupt backed by a SQLite checkpointer,
    not a status column.
    """
    try:
        from miw.agents import run_impact_agent, run_signal_agent
    except ImportError:
        print("the agent graphs need langgraph:\n"
              "  pip install -r requirements-optional.txt", file=sys.stderr)
        return 2

    from miw.llm import budget_state, provider_status

    deps = _load_inventory()
    # A taught model id has an owner and a SERVER, and only the server can retire it.
    # The probe artifact records which provider's catalogue named the exact id, which
    # is what earns the authority widening (see `trust.with_provider`).
    try:
        probes = _load_probes()
    except SystemExit:
        probes = {}

    if args.dep:
        want = args.dep.lower()
        chosen = [d for d in deps if d.canonical_name.lower() == want]
        if not chosen:
            chosen = [d for d in deps if want in d.canonical_name.lower()][:1]
        if not chosen:
            print(f"no dependency matching {args.dep!r}", file=sys.stderr)
            return 2
    else:
        # Only subjects we can speak for officially: the rest cannot produce a claim,
        # so spending a planning call on them is pure waste.
        pool = [d for d in deps if d.official_domains and d.watch_tier == "critical"]
        chosen = sorted(pool, key=lambda d: -len(d.locations))[:args.limit]

    st = provider_status()
    print(f"  provider: {st['provider']} ({st.get('model_logical','-')})  "
          f"budget: {budget_state()['calls_today']}/{budget_state()['max_calls']} calls today")
    print(f"  {len(chosen)} dependency(ies) selected\n")

    reviewed = 0
    for dep in chosen:
        print(f"  {dep.canonical_name}  ({dep.kind}, {len(dep.locations)} locations)")
        prov = getattr(probes.get(dep.dep_id), "provider_domains", None) or []
        if prov:
            print(f"      authority widened by serving provider: {', '.join(prov)}")
        sig = run_signal_agent(dep, kinds=tuple(args.kinds),
                               max_attempts=args.attempts, provider_domains=prov)
        for line in sig.get("trajectory", []):
            print(f"      {line}")
        for bad in sig.get("rejected_urls", []):
            print(f"      REJECTED off-allowlist: {bad}")
        claims = sig.get("claims") or []
        if not claims:
            print(f"      -> {sig.get('status')}: no substantiated evidence\n")
            continue
        for c in claims[:3]:
            print(f"      [{c.get('tier')}] {c.get('source_url')}")
            print(f"        \"{(c.get('quote') or '')[:110]}\"")

        imp, app = run_impact_agent(dep, sig.get("probe_signals") or args.signals,
                                    claims)
        for line in imp.get("trajectory", []):
            print(f"      {line}")
        for f in imp.get("findings", []):
            print(f"      {f['course']}: {f['signal']} {f['severity']} "
                  f"blast {f['blast_radius']} sessions {f['sessions']}")
        print(f"      -> suspended at review gate (thread impact:{dep.dep_id})\n")
        reviewed += 1

    print(f"  {reviewed} dependency(ies) reached the review gate and are awaiting a "
          f"human decision")
    print(f"  spend today: ${budget_state()['spent_usd_today']:.4f}")
    return 0


def cmd_verify(args) -> int:
    """Audit the trust invariants on the artifacts that are actually on disk.

    The point of this command is that the guarantees are checkable after the fact, by
    someone who did not write the code: no finding may rest on a source that is not
    authoritative for its claim kind, and every dependency that cannot be spoken for
    officially is listed by name.
    """
    from miw.registry import Registry
    from miw.trust import STRICT_KINDS, ClaimKind, Tier, classify

    problems: list[str] = []
    deps = _load_inventory()
    reg = Registry.load()

    by_id = {d.dep_id: d for d in deps}
    probe_rows = {r.get("dep_id"): r
                  for r in (_read_latest("probe_*.json") or {}).get("results", [])}
    # A gap finding's subject is a TOPIC, which is deliberately not in the inventory -
    # the inventory records what the curriculum uses, and a topic we do not teach is
    # precisely not that. Its authority set comes off the gaps artifact instead, the
    # same arrangement that lets provider widening be re-checked from the probe
    # artifact rather than believed from the finding.
    topic_rows = {r.get("dep_id"): r
                  for r in (_read_latest("gaps_*.json") or {}).get("topics", [])}
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

        def _subject_for_claim(f: dict, claim: dict, dep_subject):
            """Whose authority settles THIS claim.

            Usually the dependency's. But a finding also carries claims about its
            candidate REPLACEMENTS, lifted onto it so the digest can cite them, and
            those are about a different subject entirely: an S10 on Murf.AI carries a
            pricing claim sourced from `vozo.ai`, which is authoritative about Vozo and
            LEAD_ONLY about Murf.AI. Re-classifying it against the dependency reported
            a violation that was not one - the auditor rebuilding the wrong subject,
            which is the same mistake `with_provider` exists to prevent one step above.
            `Claim.subject_name` has recorded the true subject since the beginning, so
            this needs no new data, only for the auditor to read it.
            """
            from miw.trust import Subject, domain as _dom
            want = (claim.get("subject_name") or "").strip().casefold()
            if not want:
                return dep_subject
            for alt in (f.get("alternatives") or []):
                if (alt.get("name") or "").strip().casefold() != want:
                    continue
                doms = {d for d in (_dom(alt.get("homepage") or ""),) if d}
                for c in (alt.get("claims") or []):
                    d = _dom(c.get("source_url") or "")
                    if d:
                        doms.add(d)
                return Subject(name=alt.get("name", ""),
                               official_domains=tuple(sorted(doms)))
            return dep_subject
        print(f"  findings: {len(findings)} in {path[-1].name}")
        for f in findings:
            claims = f.get("claims") or []
            probes = f.get("probe_signals") or []
            substantiating = []
            for c in claims:
                kind = ClaimKind(c["kind"])
                stored = Tier[c["tier"]] if isinstance(c["tier"], str) else Tier(c["tier"])
                # RE-CLASSIFY, do not trust the stored tier. Reading the tier the
                # artifact records makes this check the artifact against itself, which
                # is the opposite of the point: the guarantee is meant to be verifiable
                # by someone who did not write the code. It also means a tightened
                # trust rule silently leaves old findings standing - which is exactly
                # what happened when forum pages on a vendor's own subdomain stopped
                # being authoritative and 14 stale criticals kept their AUTHORITATIVE
                # stamp.
                dep = by_id.get(f.get("dep_id"))
                subj = dep.subject() if dep else None
                topic = topic_rows.get(f.get("dep_id"))
                if dep is None and topic:
                    from miw.trust import Subject as _Subj
                    # Union of every corroborating source's authority set: the claim
                    # being checked may come from either of them.
                    doms = {d for src in (topic.get("sources") or [])
                            for d in (src.get("official_domains") or [])}
                    subj = _Subj(name=topic.get("canonical_name", ""),
                                 official_domains=tuple(sorted(doms)))
                # A model's authority set is widened by its SERVING provider, and that
                # widening is recorded on the probe result, not on the finding. Rebuild
                # it here or Groq's own deprecation table reads as LEAD_ONLY against a
                # subject attributed to Meta - the exact bug `with_provider` exists to
                # fix, reintroduced by the auditor rather than the analyser.
                prov = (probe_rows.get(f.get("dep_id")) or {}).get("provider_domains")
                if dep is not None and dep.kind == "model" and prov:
                    subj = dep.subject_with_provider(prov)
                # A claim about a candidate replacement is about the replacement.
                subj = _subject_for_claim(f, c, subj)
                actual = classify(c["source_url"], subj, kind)
                if actual is not stored:
                    problems.append(
                        f"{f['canonical_name']} / {f['signal']}: claim records "
                        f"{stored.name} but {c['source_url']} classifies as "
                        f"{actual.name} today - the finding predates a trust rule "
                        f"change and must be re-analysed")
                tier = min(stored, actual)
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

            # A model's fit judgement may never become evidence. It cannot be built
            # into a Claim by construction (no source_url, no quote), but assert the
            # invariant against the artifact anyway: this is the one thing in the
            # digest a model asserted rather than a page stated, and the whole point
            # of `verify` is that the guarantee is checkable from the outside.
            for a in f.get("alternatives") or []:
                op = a.get("opinion")
                if not op:
                    continue
                if not [c for c in (a.get("claims") or [])
                        if c.get("substantiating")]:
                    problems.append(
                        f"{f['canonical_name']}: alternative {a.get('name')!r} carries "
                        f"a model opinion but no substantiating claim - an opinion "
                        f"must never travel alone")
                if op.get("source") != "llm":
                    problems.append(
                        f"{f['canonical_name']}: alternative {a.get('name')!r} opinion "
                        f"is not labelled as a model judgement")
                blob = json.dumps(a.get("claims") or [])
                if "fit_score" in blob or "one_line" in blob:
                    problems.append(
                        f"{f['canonical_name']}: a fit judgement leaked into a claim")
            # Provider widening is the one place the trust layer was loosened, so it is
            # asserted here: an S7 finding must name the provider that granted the
            # authority, and that provider must have been discovered by its own
            # catalogue listing the exact id (which is what sets `provider`).
            # Corroboration is the only thing standing between an S11 and a vendor's
            # API reference, so assert it against the artifact rather than trusting the
            # analyser that wrote it: two substantiating claims, on DIFFERENT domains.
            if f.get("signal") == "S11":
                from miw.trust import domain as _dom
                hosts = {_dom(c["source_url"]) for c in substantiating}
                if len(hosts) < 2:
                    problems.append(
                        f"{f['canonical_name']} / S11: corroborated by {len(hosts)} "
                        f"independent source(s); a topic gap needs two, or it is one "
                        f"vendor's documentation detail")
            if f.get("signal") == "S7" and claims:
                probe_sigs = set(f.get("probe_signals") or [])
                if probe_sigs & {"model_shutdown_passed", "model_deprecation_declared"} \
                        and not (f.get("dep_id") and any(
                            c.get("tier") in ("AUTHORITATIVE", 3) for c in claims)):
                    problems.append(
                        f"{f['canonical_name']}: S7 with no authoritative claim - "
                        f"provider authority was not established")

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
                        ("analyse", cmd_analyse, args),
                        # Between analyse and report because it merges into the same
                        # findings artifact the reporter reads. `cmd_gaps` reads its
                        # two extra options through `getattr`, so the shared `args`
                        # namespace needs nothing added for it.
                        ("gaps", cmd_gaps, args), ("report", cmd_report, args)):
        print(f"\n=== {name} ===")
        rc = fn(a)
        if rc not in (0, 1):
            print(f"stage {name} failed with {rc}", file=sys.stderr)
            return rc
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The CLI, as a value.

    Split out of `main()` so a test can assert that the argv `miw/api/jobs.py` builds
    for each stage actually parses. That guard exists because it did not: `report` was
    added to `jobs.SCOPED`, started receiving `--tiers`, which its subparser does not
    declare, and every UI-initiated run died at its last stage with exit 2.
    """
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
    # Opt-in, like `analyse --refine`. The deterministic nominators run either way;
    # the model only adds candidates, and every one still faces the same ladder.
    rs.add_argument("--nominate", action="store_true",
                    help="also ask a model for candidate replacements (costs LLM calls)")
    an = sub.add_parser("analyse", help="score findings and diff against last run")
    _add_scope_args(an)
    an.add_argument("--refine", action="store_true",
                    help="rewrite action notes with the LLM (Claude Code CLI by "
                         "default; no API key needed)")
    dk = sub.add_parser("decks", help="read session decks: slide text and reachability")
    _add_scope_args(dk)
    dk.add_argument("--refresh", action="store_true",
                    help="ignore the cache and re-fetch every deck")
    dk.add_argument("--verbose", action="store_true",
                    help="show every deck, not only the ones we could not read")
    gp = sub.add_parser("gaps", help="topics the curriculum does not teach yet (S11)")
    _add_scope_args(gp)
    gp.add_argument("--topics", default="registry/topics.yaml",
                    help="curriculum-topic registry to read")
    gp.add_argument("--dry-run", action="store_true",
                    help="print what would be raised and write nothing")
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
    rpk = sub.add_parser("resolve-packages",
                         help="retype sheet-declared names that are really packages")
    rpk.add_argument("--dry-run", action="store_true")
    rpk.add_argument("--only", action="append", default=[],
                     help="restrict to these names; repeatable")
    rpk.add_argument("--include-unpinned", action="store_true",
                     help="also check names with no version pin (slower, weaker signal)")
    rp = sub.add_parser("report", help="render the weekly markdown digest")
    # `--course` narrows which per-course digest is WRITTEN. The roll-up is always
    # re-rendered from the complete merged artifact, so it never goes stale behind a
    # scoped run and never presents one slice as the whole week.
    rp.add_argument("--course", action="append", default=[],
                    help="course slug or title; repeatable. Default: every course.")
    rw = sub.add_parser("run-weekly", help="all stages, unattended")
    _add_scope_args(rw)
    rw.add_argument("--verbose", action="store_true")
    rw.add_argument("--refine", action="store_true")
    wt = sub.add_parser("watch", help="poll vendors for releases and deprecations")
    wt.add_argument("--investigate", action="store_true",
                    help="run the scoped stages for each resolved signal")
    wt.add_argument("--no-packages", action="store_true",
                    help="vendor catalogues only, skip registry polling")
    wt.add_argument("--package-limit", type=int, default=None)
    wt.add_argument("--verbose", action="store_true", help="show unchanged sources too")
    ag = sub.add_parser("agent", help="run the agent graphs (needs langgraph)")
    ag.add_argument("--dep", default="", help="one dependency by name")
    ag.add_argument("--limit", type=int, default=3, help="how many to sweep")
    ag.add_argument("--attempts", type=int, default=2, help="replan budget per dep")
    ag.add_argument("--kinds", nargs="+", default=["DEPRECATION", "PRICING"])
    ag.add_argument("--signals", nargs="+", default=["model_deprecation_declared"],
                    help="probe signals to classify against when none are on file")

    sub.add_parser("verify", help="audit trust invariants on the current artifacts")
    sv = sub.add_parser("serve", help="start the local web UI")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    return {"ingest": cmd_ingest, "extract": cmd_extract, "probe": cmd_probe,
            "research": cmd_research, "analyse": cmd_analyse, "report": cmd_report,
            "verify": cmd_verify, "triage": cmd_triage, "serve": cmd_serve,
            "watch": cmd_watch, "resolve-packages": cmd_resolve_packages,
            "run-weekly": cmd_run_weekly, "agent": cmd_agent,
            "gaps": cmd_gaps, "decks": cmd_decks}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
