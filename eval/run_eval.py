#!/usr/bin/env python3
"""Scored regression harness for MIW. Exits non-zero so it can gate a change.

Three deterministic suites plus one measured metric:

  trust       — can this source settle this kind of claim about this subject?
  extraction  — does the inventory contain what it should, and *not* contain what it
                shouldn't? Half these cases are must-not-contain, because the failure
                mode that matters is a phantom dependency, not a missed one.
  findings    — given a probe result, does the right signal fire at the right severity?
                Half are must-not-fire: a healthy tool, our own network failing, an
                anti-bot 403, a robots refusal, docs churn on a passing mention.
  precision   — accepted ÷ triaged from real reviewer decisions, scored on the
                HELD-OUT half only.

That last split is the point, and it is borrowed from
`agentic-interview-question-generator/eval/run_eval.py`, whose docstring records what
happens without it: runs scored 0.9 on their own confidence while reviewers rejected
most of the set (corr = 0.16 over 36 runs). MIW's severity score is likewise its own
judgement, so it is never the metric. Only a human's accept/reject is.

Everything here runs offline: no network, no LLM, no API key. That is deliberate — an
eval you cannot afford to run is an eval you don't run.

Usage:
  python3 eval/run_eval.py                # all suites
  python3 eval/run_eval.py --suite trust  # one suite
  python3 eval/run_eval.py -v             # show every case, not just failures
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GOLDEN = ROOT / "eval" / "golden"

from miw.analyse.score import SEVERITY_ORDER, findings_for            # noqa: E402
from miw.extract.inventory import InventoryBuilder                    # noqa: E402
from miw.registry import Registry                                     # noqa: E402
from miw.schema import (Claim, ContentRecord, Dependency, Location,    # noqa: E402
                        ProbeResult, ResearchResult, UncitedClaim)
from miw.trust import ClaimKind, Subject, Tier, classify, substantiates  # noqa: E402

# Minimum share of cases that must pass for the suite to pass. Deterministic suites
# are held to 100%: every case encodes a rule the code is supposed to enforce, so a
# single failure is a regression, not noise.
THRESHOLDS = {"trust": 1.0, "extraction": 1.0, "findings": 1.0}
PRECISION_TARGET = 0.70          # PRD supporting metric, by week 4


class Suite:
    def __init__(self, name: str):
        self.name, self.results = name, []

    def case(self, label: str, ok: bool, detail: str = "") -> None:
        self.results.append((label, ok, detail))

    @property
    def passed(self) -> int:
        return sum(1 for _, ok, _ in self.results if ok)

    @property
    def rate(self) -> float:
        return self.passed / len(self.results) if self.results else 1.0

    def report(self, verbose: bool) -> bool:
        thr = THRESHOLDS.get(self.name, 1.0)
        ok = self.rate >= thr
        print(f"\n  {self.name}: {self.passed}/{len(self.results)} "
              f"({self.rate:.0%}, need {thr:.0%})  {'PASS' if ok else 'FAIL'}")
        for label, cok, detail in self.results:
            if cok and not verbose:
                continue
            print(f"    {'ok  ' if cok else 'FAIL'} {label}")
            if detail and not cok:
                print(f"         {detail}")
        return ok


def _load(name: str) -> list:
    return json.loads((GOLDEN / f"{name}.json").read_text())


# --------------------------------------------------------------------- trust

def suite_trust(verbose: bool) -> Suite:
    s = Suite("trust")
    for c in _load("trust_cases"):
        subj = Subject(name=c["subject"]["name"],
                       homepage=c["subject"].get("homepage", ""),
                       docs_url=c["subject"].get("docs_url", "")).with_domains_from_urls()
        kind = ClaimKind(c["kind"])
        tier = classify(c["url"], subj, kind)
        sub = substantiates(tier, kind)
        ok = tier.name == c["expect_tier"] and sub == c["expect_substantiates"]
        s.case(c["name"], ok,
               f"got tier={tier.name} substantiates={sub}, "
               f"want tier={c['expect_tier']} substantiates={c['expect_substantiates']}")
    return s


# ---------------------------------------------------------------- extraction

def _record(spec: dict) -> ContentRecord:
    return ContentRecord(
        course="EvalCourse", topic_name="t", unit_id="u1", unit_name="Unit",
        unit_type="LEARNING_SET", content_id=spec.get("content_id", "c1"),
        object_type=spec.get("object_type", "LEARNING_RESOURCE"),
        content_type="MARKDOWN", title="Eval", body_text=spec["body_text"],
        field_path="[0].eval", evidence_source=spec.get("evidence_source", "markdown"),
        source_file="eval", session_no=spec.get("session_no", 1))


def suite_extraction(verbose: bool) -> Suite:
    s = Suite("extraction")
    for c in _load("extraction_cases"):
        records = [_record(r) for r in c["records"]]
        b = InventoryBuilder(Registry())
        b.feed_structured(records)
        b.sync_registry()
        b.feed_prose(records)
        deps = b.finish()
        by_name = {d.canonical_name.lower(): d for d in deps}
        exp, problems = c["expect"], []

        if "max_dependencies" in exp and len(deps) > exp["max_dependencies"]:
            problems.append(f"expected <= {exp['max_dependencies']} deps, got "
                            f"{[d.canonical_name for d in deps]}")
        for want in exp.get("must_contain", []):
            d = by_name.get(want["canonical_name"].lower())
            if d is None:
                problems.append(f"missing {want['canonical_name']}; "
                                f"got {[x.canonical_name for x in deps]}")
                continue
            if want.get("kind") and d.kind != want["kind"]:
                problems.append(f"{d.canonical_name}: kind {d.kind} != {want['kind']}")
            if want.get("has_domain") and want["has_domain"] not in d.official_domains:
                problems.append(f"{d.canonical_name}: {want['has_domain']} not in "
                                f"{d.official_domains}")
            if want.get("taught_version") and d.taught_version != want["taught_version"]:
                problems.append(f"{d.canonical_name}: version {d.taught_version} != "
                                f"{want['taught_version']}")
        for bad in exp.get("must_not_contain", []):
            if bad.lower() in by_name:
                problems.append(f"phantom dependency '{bad}' was inventoried")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


# ------------------------------------------------------------------ findings

def _dependency(spec: dict) -> Dependency:
    locs = [Location(course=l.get("course", "EvalCourse"), topic_name="t",
                     unit_id="u1", unit_name=f"Session {l.get('session_no', 1)}",
                     content_id=l.get("content_id", ""), field_path="[0].eval",
                     evidence_source=l["evidence_source"],
                     object_type=l.get("object_type", "LEARNING_RESOURCE"),
                     session_no=l.get("session_no"))
            for l in spec.pop("locations", [])]
    return Dependency(locations=locs, **spec)


def suite_findings(verbose: bool) -> Suite:
    s = Suite("findings")
    for c in _load("finding_cases"):
        dep = _dependency(dict(c["dependency"]))
        pspec = dict(c.get("probe") or {})
        probe = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                            **pspec) if pspec else None

        research = None
        if c.get("research"):
            research = ResearchResult(dep_id=dep.dep_id,
                                      canonical_name=dep.canonical_name)
            for cl in c["research"].get("claims", []):
                try:
                    research.claims.append(Claim.build(
                        kind=ClaimKind(cl["kind"]), statement=cl["statement"],
                        source_url=cl["source_url"], quote=cl["quote"],
                        subject=dep.subject()))
                except UncitedClaim:
                    pass          # rejected at construction: that is the behaviour

        findings = findings_for(dep, probe, research)
        fired = {f.signal for f in findings}
        exp, problems = c["expect"], []

        if "min_findings" in exp and len(findings) < exp["min_findings"]:
            problems.append(f"expected >= {exp['min_findings']} findings, got {len(findings)}")
        if "max_findings" in exp and len(findings) > exp["max_findings"]:
            problems.append(f"expected <= {exp['max_findings']} findings, got "
                            f"{sorted(fired)} ({[f.summary[:50] for f in findings]})")
        for sig in exp.get("must_fire", []):
            if sig not in fired:
                problems.append(f"{sig} did not fire (fired: {sorted(fired) or 'nothing'})")
        for sig in exp.get("must_not_fire", []):
            if sig in fired:
                problems.append(f"{sig} fired but should not have")
        if exp.get("severity_range") and findings:
            sev = findings[0].severity
            if sev not in exp["severity_range"]:
                problems.append(f"severity {sev} not in {exp['severity_range']}")
        for token in exp.get("must_mention", []):
            blob = " ".join(f"{f.summary} {f.recommendation} {f.what_to_act}"
                            for f in findings)
            if token.lower() not in blob.lower():
                problems.append(f"output never mentions '{token}'")
        if "min_questions_executing" in exp:
            got = max((f.questions_executing for f in findings), default=0)
            if got < exp["min_questions_executing"]:
                problems.append(f"questions_executing {got} < "
                                f"{exp['min_questions_executing']}")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


# ----------------------------------------------------------------- precision

def suite_precision(verbose: bool) -> tuple[Suite, dict]:
    """Precision on held-out reviewer decisions. Never on MIW's own severity score."""
    from miw import triage
    from miw.state import State

    s = Suite("precision")
    state = State()
    rows = state.decisions()
    latest: dict[str, str] = {}
    for r in rows:                      # rows are newest-first
        latest.setdefault(r["finding_id"], r["verdict"])
    state.close()

    held = {fid: v for fid, v in latest.items() if triage.split_of(fid) == "score"}
    learned = {fid: v for fid, v in latest.items() if triage.split_of(fid) == "learn"}
    stats = {"triaged_total": len(latest), "held_out": len(held),
             "informing_the_system": len(learned)}
    if not held:
        s.case("held-out precision measurable", True,
               "no held-out decisions yet - triage more findings")
        stats["precision_heldout"] = None
        return s, stats

    acc = sum(1 for v in held.values() if v == "accepted")
    p = acc / len(held)
    stats["precision_heldout"] = round(p, 3)
    s.case(f"held-out precision {p:.0%} >= {PRECISION_TARGET:.0%} "
           f"(n={len(held)})", p >= PRECISION_TARGET,
           f"accepted {acc} of {len(held)} held-out findings")
    return s, stats


SUITES = {"trust": suite_trust, "extraction": suite_extraction,
          "findings": suite_findings}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=[*SUITES, "precision"], help="run one suite")
    ap.add_argument("--with-precision", action="store_true",
                    help="also report held-out reviewer precision. Off by default: it "
                         "reads live triage state, so it is not reproducible and must "
                         "not gate CI.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    print("MIW eval — deterministic, offline, no API key")
    names = [args.suite] if args.suite else list(SUITES)
    if args.with_precision and "precision" not in names:
        names.append("precision")
    all_ok, stats = True, {}
    for name in names:
        if name == "precision":
            s, stats = suite_precision(args.verbose)
            s.report(args.verbose)      # advisory: needs human triage to be meaningful
            continue
        all_ok &= SUITES[name](args.verbose).report(args.verbose)

    if stats:
        print(f"\n  precision detail: {stats}")
        if stats.get("precision_heldout") is None:
            print("    (advisory only until findings are triaged: "
                  "python3 main.py triage --list)")
    print(f"\n  {'ALL DETERMINISTIC SUITES PASS' if all_ok else 'FAILURES ABOVE'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
