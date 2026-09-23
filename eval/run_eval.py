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

from eval import cases                                               # noqa: E402
from miw.analyse.score import SEVERITY_ORDER, findings_for            # noqa: E402
from miw.extract.inventory import InventoryBuilder                    # noqa: E402
from miw.registry import Registry                                     # noqa: E402
from miw.schema import (Claim, ContentRecord, Dependency, Finding,     # noqa: E402
                        Location, ProbeResult, ResearchResult, UncitedClaim)
from miw.trust import ClaimKind, Subject, Tier, classify, substantiates  # noqa: E402

# Minimum share of cases that must pass for the suite to pass. Deterministic suites
# are held to 100%: every case encodes a rule the code is supposed to enforce, so a
# single failure is a regression, not noise.
THRESHOLDS = {"trust": 1.0, "extraction": 1.0, "findings": 1.0, "discovery": 1.0,
              "catalogue": 1.0, "probe": 1.0, "reach": 1.0, "news": 1.0,
              "notice": 1.0, "newer": 1.0, "coverage": 1.0, "fields": 1.0,
              "prose": 1.0}
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
        # Name the gate, not just the suite. A failure here is a claim the system is
        # now allowed to make and was not before, and the consequence is the thing
        # worth reading at the moment it breaks.
        gate = GATES.get(self.name)
        if gate and (verbose or not ok):
            print(f"    gate: {gate}")
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

    # `classify()` above decides what a source may settle. These decide whether a claim
    # may EXIST - the constructor checks that make "there is no code path from model
    # recall to a finding" true rather than merely intended. Added after
    # `eval/mutations.py` found them unguarded: deleting the 12-character quote floor
    # broke no case in any suite.
    for c in _load("claim_cases"):
        spec = c["subject"]
        if spec.get("registry"):
            # Built through `Dependency.subject()`, which is what LENDS a package the
            # registry's authority. Constructing the Subject by hand instead would put
            # `pypi.org` in `official_domains` as if it were the package's own site,
            # and the remit exists precisely to tell those two apart.
            dep = Dependency(kind=("model" if spec.get("provider_domains")
                                   else "package"),
                             canonical_name=spec["name"], registry=spec["registry"],
                             registry_id=spec.get("registry_id", ""))
            subj = (dep.subject_with_provider(spec["provider_domains"])
                    if spec.get("provider_domains") else dep.subject())
        else:
            subj = Subject(name=spec["name"], homepage=spec.get("homepage", ""),
                           docs_url=spec.get("docs_url", "")).with_domains_from_urls()
        exp, problems = c["expect"], []
        try:
            claim = Claim.build(kind=ClaimKind(c["kind"]), statement=c["statement"],
                                source_url=c["source_url"], quote=c["quote"],
                                subject=subj)
        except UncitedClaim as exc:
            if exp["built"]:
                problems.append(f"refused a claim it should have built: {exc}")
            elif exp.get("error_mentions", "").lower() not in str(exc).lower():
                problems.append(f"refused for {str(exc)!r}, expected a reason "
                                f"mentioning {exp['error_mentions']!r}")
        else:
            if not exp["built"]:
                problems.append("built a claim that should have been refused")
            else:
                if "tier" in exp and claim.tier.name != exp["tier"]:
                    problems.append(f"tier {claim.tier.name}, want {exp['tier']}")
                if ("substantiates" in exp
                        and claim.substantiating != exp["substantiates"]):
                    problems.append(f"substantiates={claim.substantiating}, "
                                    f"want {exp['substantiates']}")
                if exp.get("through_verify"):
                    # Through the real check, not its helper: a finding whose
                    # alternative carries an opinion AND this substantiating claim must
                    # raise nothing. The inlined version read a verdict the artifact
                    # never carries, so it flagged every such alternative.
                    import main as cli
                    from miw.schema import to_jsonable
                    row = to_jsonable(claim)
                    finding = {"canonical_name": subj.name,
                               "alternatives": [{"name": subj.name,
                                                 "opinion": {"does_taught_job": 0.9, "source": "llm"},
                                                 "claims": [row]}]}
                    got = cli._opinion_violations(finding)
                    if got:
                        problems.append(f"verify rejected a substantiated "
                                        f"alternative: {got[0]}")
                    # ...and an opinion with nothing behind it must still be caught.
                    bare = {"canonical_name": subj.name,
                            "alternatives": [{"name": subj.name,
                                              "opinion": {"does_taught_job": 0.9, "source": "llm"},
                                              "claims": []}]}
                    if not cli._opinion_violations(bare):
                        problems.append("verify let an opinion travel alone")
                if exp.get("serialised_verdict_absent"):
                    # The artifact carries tier and kind, never the verdict, so any
                    # check reading it from the row gets None for every claim ever
                    # written. `main._claim_substantiates` has to reach the same
                    # answer from the outside, and this pins both halves.
                    import main as cli
                    from miw.schema import to_jsonable
                    row = to_jsonable(claim)
                    if row.get("substantiating"):
                        problems.append("the verdict IS serialised now — a check may "
                                        "read it, and this case should be retired")
                    if not cli._claim_substantiates(row):
                        problems.append("recomputing from the serialised row "
                                        "disagrees with Claim.substantiating")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


# ---------------------------------------------------------------- extraction

# Both of these delegate to `eval/cases.py` so the suites and `eval/parity.py` build
# identical fixtures. Two copies drifting apart would leave a parity number that looks
# healthy while comparing different things.
def _record(spec: dict) -> ContentRecord:
    return cases.record(spec)


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
    return cases.dependency(spec)


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





# ----------------------------------------------------------------- discovery

def suite_discovery(verbose: bool) -> Suite:
    """Claim-level audit of the nomination ladder. Offline: DNS is a set, HTTP a dict.

    The 2026 measurements are the brief - 3-13% of URLs cited by research agents are
    fabricated, and citation-support metrics overstate reliability because they never
    check the URL resolves. So every case here asks one of two questions: did a
    fabricated thing die deterministically, and did a real thing survive with a
    citation we fetched ourselves. Half are must-not-fire, matching the convention of
    the other suites.
    """
    from miw.research.nominate import adjudicate, from_search_hit, from_vendor_name
    from miw.schema import AlternativeNomination, Dependency
    from miw.trust import Tier

    s = Suite("discovery")
    dep = Dependency(kind="service", canonical_name="CodeToTutorial",
                     official_domains=["codetotutorial.com"],
                     homepage="https://codetotutorial.com")

    PRICING = ("<html><body><p>DeepWiki has a generous free tier for public "
               "repositories and needs no credit card to start.</p></body></html>")
    BLANK = "<html><body><p>Welcome. Home About Contact Careers</p></body></html>"

    def resolver(known):
        from miw.net import domain
        return lambda url: "ok" if domain(url) in known else "unresolvable"

    def fetcher(pages, status=200):
        class F:
            def __init__(self, url):
                from miw.net import domain
                self.url, self.status = url, status
                self.body = pages.get(domain(url), "") if status == 200 else ""
                self.final_url, self.error, self.redirects = url, "", []
            reachable = property(lambda s2: s2.status is not None)
            ok = property(lambda s2: 200 <= (s2.status or 0) < 300)
            gone = property(lambda s2: s2.status in (404, 410))
            blocked = property(lambda s2: s2.status in (401, 403, 429))
        return lambda url, **k: F(url)

    # --- must fire ------------------------------------------------------
    nom, alt = adjudicate(from_search_hit("https://deepwiki.com/p", "DeepWiki"), dep,
                          fetcher=fetcher({"deepwiki.com": PRICING}),
                          resolver=resolver({"deepwiki.com"}))
    s.case("a live candidate whose own site says something is verified",
           nom.verdict == "verified" and alt is not None and alt.verified,
           f"verdict={nom.verdict}")
    s.case("every claim on a verified candidate cites the candidate's own domain",
           bool(alt) and all("deepwiki.com" in c.source_url for c in alt.claims)
           and all(c.tier is Tier.AUTHORITATIVE for c in alt.claims),
           "a claim escaped the candidate's authority set")

    nom, alt = adjudicate(from_vendor_name("DeepWiki", "https://codetotutorial.com/docs"),
                          dep, fetcher=fetcher({}), resolver=resolver(set()))
    s.case("a vendor-named successor is usable with no key and no domain",
           nom.verdict == "vendor_named" and alt is not None, f"verdict={nom.verdict}")
    s.case("a vendor-named successor claims nothing about itself",
           bool(alt) and alt.homepage == "" and alt.free_student_path is None,
           "claimed something its nominating page cannot settle")

    # --- must NOT fire --------------------------------------------------
    touched = []
    nom, alt = adjudicate(
        AlternativeNomination(name="GhostTool", candidate_domain="ghosttool.invalid",
                              source="model"), dep,
        fetcher=lambda u, **k: touched.append(u), resolver=resolver(set()))
    s.case("a fabricated domain is refuted at DNS",
           nom.verdict == "refuted_no_such_domain" and alt is None,
           f"verdict={nom.verdict}")
    s.case("a fabricated domain is never fetched", touched == [], f"fetched {touched}")

    touched = []
    nom, alt = adjudicate(
        AlternativeNomination(name="GhostTool", candidate_domain="", source="model"),
        dep, fetcher=lambda u, **k: touched.append(u), resolver=lambda u: "ok")
    s.case("a bare name is never turned into a guessed URL",
           nom.verdict == "unresolved_name_only" and alt is None and touched == [],
           f"verdict={nom.verdict} fetched={touched}")

    nom, alt = adjudicate(from_search_hit("https://deepwiki.com/p", "DeepWiki"), dep,
                          fetcher=fetcher({}, status=404),
                          resolver=resolver({"deepwiki.com"}))
    s.case("a dead candidate is refuted", nom.verdict == "refuted_dead" and alt is None,
           f"verdict={nom.verdict}")

    nom, alt = adjudicate(from_search_hit("https://deepwiki.com/p", "DeepWiki"), dep,
                          fetcher=fetcher({}, status=403),
                          resolver=resolver({"deepwiki.com"}))
    s.case("an anti-bot 403 is NOT reported as refuted",
           nom.verdict == "unverifiable_blocked" and not nom.refuted and alt is None,
           f"verdict={nom.verdict} refuted={nom.refuted}")

    nom, alt = adjudicate(from_search_hit("https://deepwiki.com/p", "DeepWiki"), dep,
                          fetcher=fetcher({"deepwiki.com": BLANK}),
                          resolver=resolver({"deepwiki.com"}))
    s.case("a candidate whose site says nothing checkable is refuted",
           nom.verdict == "refuted_no_evidence" and alt is None,
           f"verdict={nom.verdict}")

    for bad in ("https://deepwiki.com", "deepwiki.com/docs", "deep wiki.com"):
        nom, alt = adjudicate(
            AlternativeNomination(name="DeepWiki", candidate_domain=bad,
                                  source="model"), dep,
            fetcher=fetcher({}), resolver=resolver({"deepwiki.com"}))
        s.case(f"a model-supplied URL is rejected, not repaired ({bad[:22]})",
               nom.verdict == "rejected_malformed_domain" and alt is None,
               f"verdict={nom.verdict}")

    nom, alt = adjudicate(from_vendor_name("HTTPS", "https://codetotutorial.com/docs"),
                          dep, fetcher=fetcher({}), resolver=resolver(set()))
    s.case("a generic protocol is never nominated",
           nom.verdict == "rejected_generic_token" and alt is None,
           f"verdict={nom.verdict}")

    touched = []
    nom, alt = adjudicate(
        AlternativeNomination(name="Top AI Tools", candidate_domain="geeksforgeeks.org",
                              source="search"), dep,
        fetcher=lambda u, **k: touched.append(u), resolver=lambda u: "ok")
    s.case("an excluded source is never fetched or nominated",
           nom.verdict == "rejected_excluded" and alt is None and touched == [],
           f"verdict={nom.verdict} fetched={touched}")

    nom, alt = adjudicate(
        AlternativeNomination(name="CodeToTutorial Docs",
                              candidate_domain="codetotutorial.com", source="search"),
        dep, fetcher=fetcher({}), resolver=resolver({"codetotutorial.com"}))
    s.case("a tool is never its own replacement",
           nom.verdict == "rejected_same_vendor" and alt is None,
           f"verdict={nom.verdict}")

    # --- the audit trail exists -----------------------------------------
    s.case("a refutation records why, so it can be reported not dropped",
           bool(nom.verdict_detail) and bool(nom.checked_at), "no detail recorded")
    return s


# ------------------------------------------------------------------ the gates
#
# Everything above and below is organised around one question: where could this system
# say something that is not so? Each suite guards one such place, and the must-not-fire
# half of each is the half that matters — a missed change costs a cohort one broken
# lab, and a fabricated one costs the team its willingness to read the next digest.


def suite_catalogue(verbose: bool) -> Suite:
    """Gate: a vendor's table -> what we claim it says.

    The highest-risk fabrication surface in the system. A mis-bound column does not
    fail loudly; it produces a confident, well-cited, wrong retirement on a model the
    curriculum teaches in three figures of places.
    """
    from miw.probe.catalogue import entries as cat_entries

    s = Suite("catalogue")
    for c in _load("catalogue_cases"):
        got = cat_entries(c["html"], evidence_url="https://vendor.test/models")
        ids = {e.entry_id: e for e in got if e.column_role == "id"}
        exp, problems = c["expect"], []

        if exp.get("no_entries") and ids:
            problems.append(f"claimed {sorted(ids)} from a table that role-types nothing")
        for name, want in (exp.get("entries") or {}).items():
            e = ids.get(name)
            if e is None:
                problems.append(f"{name} not read from the table")
                continue
            for field, value in want.items():
                actual = getattr(e, field)
                if actual != value:
                    problems.append(f"{name}.{field} = {actual!r}, want {value!r}")
        for name in exp.get("absent", []):
            if name in ids:
                problems.append(f"{name} was invented (exact lookup only)")
        for name in exp.get("not_id_column", []):
            if name in ids:
                problems.append(f"{name} is a replacement, not a retired id")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


def suite_probe(verbose: bool) -> Suite:
    """Gate: what an observation is allowed to mean.

    Half of these are failure injections. The dangerous outcome is not a crash but a
    cycle that completes and looks clean: a 200 whose table has moved used to read
    exactly like a healthy check, for as long as the page stayed restructured.
    """
    from datetime import date

    from miw.probe.catalogue import CatalogueEntry
    from miw.probe.models import probe_model_dependency
    from miw.vendors.base import Catalogue

    s = Suite("probe")
    for c in _load("probe_cases"):
        spec = dict(c["catalogue"])
        raw = spec.pop("entries", None)
        cat = Catalogue(vendor="Vendor", **spec)
        if raw is not None:
            cat.entries = {k: CatalogueEntry(entry_id="m", **v) for k, v in raw.items()}

        class Adapter:
            key, vendor = "vendor", "Vendor"
            official_domains = ("vendor.test",)
            kinds = ("model",)

            def catalogue(self, refresh=False):
                return cat

        res = probe_model_dependency(Dependency(kind="model", canonical_name="m"),
                                     today=date(2026, 9, 18), adapters=[Adapter()])
        exp, problems = c["expect"], []
        if res.status != exp["status"]:
            problems.append(f"status {res.status!r}, want {exp['status']!r}")
        for f in exp.get("must_flag", []):
            if f not in res.signals:
                problems.append(f"{f} did not fire (fired: {res.signals or 'nothing'})")
        for f in exp.get("must_not_flag", []):
            if f in res.signals:
                problems.append(f"{f} fired but must not have")
        for token in exp.get("detail_mentions", []):
            if token.lower() not in (res.detail or "").lower():
                problems.append(f"detail never mentions {token!r}: {res.detail!r}")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


def suite_reach(verbose: bool) -> Suite:
    """Gate: how much of the curriculum one observation is allowed to implicate.

    The over-claim gate. A dead dashboard URL cannot make a quiz question that merely
    says the vendor's name wrong, and attributing a dependency's whole footprint to
    every signal is what made a 6-link finding list 12 locations.
    """
    from miw.analyse.score import reaching_locations

    s = Suite("reach")
    for c in _load("reach_cases"):
        locs = c["locations"]
        reached, _ = reaching_locations(c["signal"], c.get("affected_urls") or [], locs,
                                        c.get("redirects") or [],
                                        c.get("probe_signals") or [])
        exp, problems = c["expect"], []
        if "reached_count" in exp and len(reached) != exp["reached_count"]:
            problems.append(f"reached {len(reached)}, want {exp['reached_count']}")
        if "reached" in exp:
            got = sorted(l["evidence_source"] for l in reached)
            if got != sorted(exp["reached"]):
                problems.append(f"reached {got}, want {sorted(exp['reached'])}")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


def suite_news(verbose: bool) -> Suite:
    """Gate: is this news, and has anyone been told?

    Two watermarks, and the difference between them is the whole gate. Classifying
    against what the last RUN saw rather than what a human was last SHOWN meant a
    second `analyse` on the same inputs consumed the week's findings and the digest
    printed "No new or worsened findings this week."
    """
    import tempfile

    from miw.state import State

    s = Suite("news")
    for c in _load("news_cases"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.db"
            st, problems, step_no = State(path), [], 0
            for step in c["script"]:
                step_no += 1
                if step.get("reconnect"):
                    st.close()
                    st = State(path)
                    continue
                if step.get("resolve"):
                    st.resolve_absent(set(), "t-resolve")
                    continue
                if "report" in step:
                    st.mark_reported([("f", step["report"]["fp"],
                                       step["report"]["sev"])], "t-report")
                    continue
                see = step["see"]
                got = st.classify_finding(finding_id="f", dep_id="d", signal="S1",
                                          severity=see["sev"], fingerprint=see["fp"],
                                          now=f"t{step_no}")
                if got != step["expect"]:
                    problems.append(f"step {step_no}: got {got!r}, want {step['expect']!r}")
            st.close()
        s.case(c["name"], not problems, "; ".join(problems))
    return s


def suite_notice(verbose: bool) -> Suite:
    """Gate: has a deprecation ARRIVED, as opposed to being discussed?

    Neither existing detector could answer this. The page hash is tuned so rotating
    banners do not read as change — on the live 5,790-word Gemini release notes,
    adding a shutdown announcement moves 0 of 64 bits. The keyword flag is saturated
    on exactly those pages, answering "now deprecated" every week before anything is
    added. Only counting the notices themselves separates the two.
    """
    from miw.probe.http_probe import UrlObservation, notice_key
    from miw.probe.runner import res_from_urls

    s = Suite("notice")
    for c in _load("notice_cases"):
        o = UrlObservation(url="https://vendor.test/changelog")
        o.reachable = True
        o.sunset_sentences = list(c["seen"])
        res = ProbeResult(dep_id="d", canonical_name="Acme")
        base = (None if c["baseline"] is None
                else {notice_key(x) for x in c["baseline"]})
        res_from_urls(res, [o], "", None, seen_notices=base)

        fired = "deprecation_notice_added" in res.signals
        exp, problems = c["expect"], []
        if fired != exp["fires"]:
            problems.append(f"fired={fired}, want {exp['fires']}")
        if exp.get("records") and not res.notice_keys:
            problems.append("no baseline recorded on the first look")
        if "new_notices" in exp and res.new_notices != exp["new_notices"]:
            problems.append(f"new_notices={res.new_notices}, want {exp['new_notices']}")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


def suite_prose(verbose: bool) -> Suite:
    """Gate: may a model's words become the reviewer's instruction?

    The only place generated text reaches a human. `Claim.build` keeps the model out
    of the evidence path entirely; this keeps it out of the ACTION path. It matters
    even though refinement is opt-in and every finding on the live artifact reads
    `note_source: "template"` — the day someone adds `--refine` to the cron, this is
    the only thing standing between a fabricated version number and a curriculum edit.
    """
    from miw.analyse.notes import judge_rewrite

    s = Suite("prose")
    for c in _load("prose_cases"):
        spec = c["finding"]
        dep = Dependency(kind="package", canonical_name=spec["canonical_name"],
                         homepage=spec.get("homepage", ""),
                         taught_version=spec.get("taught_version"))
        f = Finding(dep_id="d", canonical_name=spec["canonical_name"],
                    signal=spec["signal"], signal_label="l", kind_of_signal="regression",
                    severity=spec["severity"], summary=spec.get("summary", ""),
                    latest_version=spec.get("latest_version", ""),
                    affected_urls=list(spec.get("affected_urls") or []))
        triad, reason = judge_rewrite(dep, f, c["reply"])
        accepted = triad is not None
        exp, problems = c["expect"], []
        if accepted != exp["accepted"]:
            problems.append(f"accepted={accepted} ({reason}), want {exp['accepted']}")
        if exp.get("reason_mentions") and exp["reason_mentions"].lower() not in reason.lower():
            problems.append(f"reason {reason!r} never mentions "
                            f"{exp['reason_mentions']!r}")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


def suite_newer(verbose: bool) -> Suite:
    """Gate: a "newer option" must actually be newer.

    The diff path never needed this - an id absent from last week's snapshot is new by
    construction. `--reconcile` considers everything a vendor lists today, which is how
    it came to offer `gemini-2.5-flash-lite` as the newer option for the taught
    `gemini-3.1-flash-lite`.
    """
    from miw.analyse.newer import _is_newer

    s = Suite("newer")
    for c in _load("newer_cases"):
        got = _is_newer(c["candidate"], c["taught"])
        s.case(c["name"], got == c["expect"],
               f"_is_newer({c['candidate']!r}, {c['taught']!r}) = {got}, "
               f"want {c['expect']}")
    return s


def suite_coverage(verbose: bool) -> Suite:
    """Gate: the digest may not present an unchecked dependency as a healthy one.

    `status="ok"` carries two different meanings and the digest reported only the
    count. A reader cannot tell a quiet week from a blind one unless the two are
    different sentences.
    """
    import main as cli

    s = Suite("coverage")
    for c in _load("coverage_cases"):
        probes = {}
        for i, spec in enumerate(c["probes"]):
            r = ProbeResult(dep_id=f"d{i}", canonical_name=f"n{i}",
                            status=spec["status"], signals=list(spec["signals"]),
                            evidence_url=spec.get("evidence_url", ""))
            probes[r.dep_id] = r
        got = cli._probe_coverage(probes)
        exp, problems = c["expect"], []
        for key in ("checked", "unchecked", "inconclusive"):
            if key in exp and got[key] != exp[key]:
                problems.append(f"{key}={got[key]}, want {exp[key]}")
        if "sums_to" in exp and sum(got.values()) != exp["sums_to"]:
            problems.append(f"parts sum to {sum(got.values())}, want {exp['sums_to']}")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


def suite_fields(verbose: bool) -> Suite:
    """Gate: a vendor's reference page -> which taught fields it retires.

    Six of the eight fixtures are HELD OUT — saved from vendors the detector was never
    shown while it was being written. That split is the whole point of the suite, and it
    was added after a reviewer observed that the method here had been: see an example,
    encode the example, verify against the example. A detector fitted to two pages
    passes those two whatever it does.

    Run held out, it found two real defects immediately. On Stripe it reported the
    field `Create`, out of the section heading "Create a charge deprecated"; requiring a
    field to declare a type rejects that and keeps every true positive, because a
    reference page states one. And ElevenLabs' `use_pvc_as_ivc` row says "we won't use
    PVC versioning", from which the bare cue `use` lifted `PVC` — a capitalised acronym
    mid-sentence offered to a reviewer as the field to rename to.
    """
    from miw.probe.http_probe import deprecated_fields

    fx = ROOT / "tests" / "fixtures" / "reference_pages"
    s = Suite("fields")
    for c in _load("field_cases"):
        text = (fx / f"{c['fixture']}.txt").read_text()
        got = {d["field"]: d["successor"] for d in deprecated_fields(text)}
        exp, problems = c["expect"], []
        for field, successor in (exp.get("must_find") or {}).items():
            if field not in got:
                problems.append(f"missed {field!r} (found {sorted(got) or 'nothing'})")
            elif successor and got[field] != successor:
                problems.append(f"{field}: successor {got[field]!r}, want {successor!r}")
        for field, forbidden in (exp.get("successor_must_not_be") or {}).items():
            if got.get(field) == forbidden:
                problems.append(f"{field}: lifted {forbidden!r} out of prose")
        if "max_fields" in exp and len(got) > exp["max_fields"]:
            problems.append(f"claimed {got}, expected at most {exp['max_fields']}")
        s.case(c["name"], not problems, "; ".join(problems))
    return s


SUITES = {"trust": suite_trust, "extraction": suite_extraction,
          "findings": suite_findings, "discovery": suite_discovery,
          "catalogue": suite_catalogue, "probe": suite_probe, "reach": suite_reach,
          "news": suite_news, "notice": suite_notice, "newer": suite_newer,
          "coverage": suite_coverage, "fields": suite_fields,
          "prose": suite_prose}

# What each suite guards, printed with the results so a failure names the consequence
# rather than only the assertion.
GATES = {
    "trust":      "a claim needs a fetched source and a checkable quote, and may "
                  "only settle what that source has authority over",
    "extraction": "a dependency must be in the curriculum, not inferred from it",
    "catalogue":  "a vendor's table means what it says, and nothing more",
    "probe":      "a failed check is never a finding about the world",
    "reach":      "an observation implicates only what it can actually invalidate",
    "findings":   "the right signal, at a severity earned by what the course DOES",
    "news":       "news is what a human has not been shown, not what a run has not seen",
    "notice":     "a deprecation that ARRIVED, not a page that discusses deprecations",
    "newer":      "a newer option is one the vendor released LATER, not merely one we do not teach",
    "discovery":  "a replacement is verified on its own pages or it is refuted",
    "coverage":   "an unchecked dependency is never reported as a healthy one",
    "fields":     "a field the course writes is retired only when the vendor's own "
                  "reference says so — measured on vendors it was never fitted to",
    "prose":      "a model may reword a finding; it may not add a fact to one",
}


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
