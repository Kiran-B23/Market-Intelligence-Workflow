#!/usr/bin/env python3
"""Does the eval actually catch anything? Revert each fix; its gate must fail.

An eval suite that passes is worth nothing until it has been shown to fail for the
right reason. Every deterministic suite in `run_eval.py` went green the first time it
ran — which is exactly what a suite asserting nothing would also do.

So each mutation below restores one real defect, verbatim, as it stood before it was
fixed, and asserts that the gate guarding it turns red. A mutation that is NOT caught
is the finding: it means that gate is decorative, and the thing it claims to prevent
can walk back in.

Every mutation is applied to a COPY of the tree and reverted immediately; the working
tree is never modified. Offline, no key, ~30s.

Usage:
  python3 eval/mutations.py            # all gates
  python3 eval/mutations.py --gate news
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

# (label, file, (find, replace), gate that must fail)
#
# `find` is the fixed code; `replace` is the defect. Each label names the audit finding
# it restores, so a MISSED line says which guarantee has quietly lapsed.
MUTATIONS: list[tuple[str, str, tuple[str, str], str]] = [
    # --- B2: Google announced shutdown dates were never read -----------------
    ("B2 a bolded-<td> header row is not promoted, so the table role-types as unknown",
     "miw/probe/catalogue.py",
     ("            headers, skip_first_row = _header_row(body)",
      "            headers, skip_first_row = (), False"),
     "catalogue"),
    ("B2 the first date column wins, so a release date is read as a shutdown date",
     "miw/probe/catalogue.py",
     ("            if r not in roles or fit > fits[r]:",
      "            if r not in roles:"),
     "catalogue"),
    ("B2 a deprecations table retires every row on it, including live models",
     "miw/probe/catalogue.py",
     ('    if not table.id_header_declares_retirement and "date" in table.roles:',
      "    if False:"),
     "catalogue"),
    ("B2 the probe returns ok one line before the parsed shutdown date is read",
     "miw/probe/models.py",
     ("            if when and when > today:", "            if False:"),
     "probe"),

    # --- B4: the failure mode that looked like success ----------------------
    ("B4 a restructured page is skipped silently instead of recorded",
     "miw/probe/models.py",
     ('            unreadable.append(f"{adapter.key}: '
      "{cat.error or 'catalogue not readable'}\")",
      "            pass"),
     "probe"),

    # --- B1: the week's news, consumed by re-running a stage ----------------
    ("B1 classification measures what the run saw, not what a human was shown",
     "miw/state.py",
     ('        reported_fp = prev["reported_fingerprint"]',
      '        reported_fp = prev["fingerprint"]'),
     "news"),
    ("B1 the reported backfill runs on every connection, not once",
     "miw/state.py",
     ('        if "reported_fingerprint" in added:', "        if True:"),
     "news"),

    # --- B3: a deprecation that ARRIVED vs a page that discusses them -------
    ("B3 the notice flag fires on any deprecation language, as the saturated one did",
     "miw/probe/runner.py",
     ("    if fresh and seen_notices is not None:",
      "    if current and seen_notices is not None:"),
     "notice"),
    ("B3 digits are normalised out of the notice key, merging v1 with v2",
     "miw/probe/http_probe.py",
     ('    norm = " ".join((sentence or "").lower().split())',
      '    import re as _re\n'
      '    norm = _re.sub(r"\\d+", "#", " ".join((sentence or "").lower().split()))'),
     "notice"),

    # --- B5: reconcile, without a direction check ---------------------------
    ("B5 a family match is taken as newer without comparing versions",
     "miw/analyse/newer.py",
     ("    return bool(a and b and a > b)", "    return True"),
     "newer"),

    # --- B6: the digest's coverage claim ------------------------------------
    ("B6 a dependency no authority serves is counted as checked",
     "main.py",
     ("        elif sigs & _UNCHECKED_SIGNALS or (not sigs and not url):",
      "        elif False:"),
     "coverage"),

    # --- S13: a field inside a live API, and the two defects the held-out
    # fixtures caught. Both mutations restore an overfit that passed on the pages
    # the detector was written from and failed on pages it was not.
    ("S13 a field need not declare a type, so a section heading becomes a field",
     "miw/probe/http_probe.py",
     ('        if not _TYPE_TOKEN.search(between + " " + after[:60]):\n            continue\n',
      ""),
     "fields"),
    ("S13 a successor need not look like an identifier, so prose becomes a field name",
     "miw/probe/http_probe.py",
     ("        if (successor.lower() in _NOT_A_FIELD\n"
      "                or not _LOOKS_LIKE_FIELD.match(successor or \"x\")):",
      "        if successor.lower() in _NOT_A_FIELD:"),
     "fields"),

    # --- A: the check must reach every kind, and every record ----------------
    ("A1 the field check goes back inside the URL branch of the kind switch",
     "miw/probe/runner.py",
     ("    _taught_field_pass(dep, res, seen_obs)", "    pass"),
     "pytest:tests/test_watch.py tests/test_location_scoping.py"),
    ("A2 field sites are filtered to records where the dependency was NAMED",
     "miw/analyse/score.py",
     ("        for site in PARAM_SITES.get(row.get(\"field\") or \"\", []):",
      "        for site in [s for s in PARAM_SITES.get(row.get(\"field\") or \"\", [])\n"
      "                     if any(l.content_id == s.get(\"content_id\")\n"
      "                            for l in dep.locations)]:"),
     "pytest:tests/test_location_scoping.py"),
    ("A3 a field site is not runtime evidence, so graded items stop executing it",
     "miw/schema.py",
     ('                                "payload_key")', '                                )'),
     "pytest:tests/test_location_scoping.py"),

    ("tier analyse honours the research tier, dropping 209 reference-only deps",
     "main.py",
     ("    return dataclasses.replace(scope, tiers=set())",
      "    return scope"),
     "pytest:tests/test_location_scoping.py"),

    # --- F: how a vendor lets you in ----------------------------------------
    ("F an auth change ships on a third party's say-so, not the vendor's",
     "miw/analyse/score.py",
     ("                if c is not None and c.tier is Tier.AUTHORITATIVE:",
      "                if c is not None and c.substantiating:"),
     "pytest:tests/test_auth_change.py"),
    ("F the first look at a dependency reports every mechanism as gained",
     "miw/probe/runner.py",
     ("    if seen_auth is not None and res.auth_signals:",
      "    if res.auth_signals:"),
     "pytest:tests/test_auth_change.py"),
    ("F a page we could not read reports every mechanism as lost",
     "miw/probe/runner.py",
     ("    res.auth_signals = sorted({p for o in obs if o.reachable for p in o.auth_phrases})",
      "    res.auth_signals = sorted({p for o in obs for p in o.auth_phrases})"),
     "pytest:tests/test_auth_change.py"),

    # --- F: commercial terms, parsed since day one and never compared --------
    ("F the terms diff is skipped for a model that is still listed",
     "miw/probe/models.py",
     ("        _terms_drift(dep, adapter, entry, res, state)", "        pass"),
     "pytest:tests/test_models.py"),
    ("F first sight of a price raises a change instead of a baseline",
     "miw/probe/models.py",
     ("    if prev is None:\n        return                 # first sight is a baseline",
      "    if False:\n        return                 # first sight is a baseline"),
     "pytest:tests/test_models.py"),
    ("F a model that is still listed is `ok` however its price moved",
     "miw/probe/models.py",
     ('            res.status = "changed" if _TERMS_SIGNALS & set(res.signals) else "ok"',
      '            res.status = "ok"'),
     "pytest:tests/test_models.py"),

    # --- E: a promise, checked against the holes a gap run found -------------
    ("E an outcome fires for gaps that land in a different course",
     "miw/analyse/outcomes.py",
     ("            missing = sorted(set((holes.get(area_id) or {}).get(o.course) or []))",
      "            missing = sorted({t for c in (holes.get(area_id) or {}).values()\n"
      "                              for t in c})"),
     "pytest:tests/test_outcomes.py"),
    ("E an outcome finding ships without the citations that document the area",
     "miw/analyse/outcomes.py",
     ("                courses=[o.course], locations=[loc], claims=claims,",
      "                courses=[o.course], locations=[loc],"),
     "pytest:tests/test_outcomes.py"),
    ("E a malformed outcomes file crashes the stage instead of reporting",
     "miw/analyse/outcomes.py",
     ("        raise OutcomesUnreadable(f\"{p}: {exc}\") from None",
      "        raise"),
     "pytest:tests/test_outcomes.py"),

    # --- D: news as a lead. The must-not cases are the whole design. ---------
    ("D a news feed matches a name that is also an ordinary English word",
     "miw/research/news.py",
     ("    if len(n) < MIN_NAME or n.lower() in AMBIGUOUS:", "    if False:"),
     "pytest:tests/test_news_leads.py"),
    ("D a news feed matches a name inside a longer word",
     "miw/research/news.py",
     ('    return re.compile(rf"(?<![\\w.-]){re.escape(n)}(?![\\w-])", re.I)',
      "    return re.compile(re.escape(n), re.I)"),
     "pytest:tests/test_news_leads.py"),
    ("D news points at a dependency with no pages to read",
     "miw/research/news.py",
     ("        if not d.subject().official_domains:\n            continue",
      "        if False:\n            continue"),
     "pytest:tests/test_news_leads.py"),

    # --- C11: a course run that answers all three questions -----------------
    ("C11 the run stages keep the caller's order instead of the pipeline's",
     "miw/api/jobs.py",
     ("        stages = [s for s in STAGES if s in wanted] or [\"probe\", \"analyse\", \"report\"]",
      "        stages = [s for s in stages if s in wanted] or [\"probe\", \"analyse\", \"report\"]"),
     "pytest:tests/test_jobs.py"),
    ("C11 gaps accepts a tier flag again and silently ignores it",
     "main.py",
     ('    gp.add_argument("--course", action="append", default=[],',
      '    _add_scope_args(gp)\n    gp.add_argument("--course2", action="append", default=[],'),
     "pytest:tests/test_jobs.py"),

    ("B the digest stops saying which taught names cannot be checked at all",
     "miw/reporters/markdown.py",
     ('        blind = coverage.get("no_authority") or 0', "        blind = 0"),
     "pytest:tests/test_ui_contract.py"),

    # --- the UI vocabulary, which nothing enumerated until a signal shipped
    # rendering as the literal string "S13" in five places.
    ("UI a drift code ships with no plain word, rendering as raw jargon",
     "miw/api/static/index.html",
     ("           S14: 'a promise nothing covers'},", "           },"),
     "pytest:tests/test_ui_contract.py"),
    ("UI a probe outcome that becomes a finding has no plain word",
     "miw/api/static/index.html",
     ("            model_rate_limit_changed: 'the usage limit changed'},",
      "            },"),
     "pytest:tests/test_ui_contract.py"),

    # --- M1: the only place generated text reaches a human ------------------
    ("M1 only invented URLs are rejected; versions, dates and prices pass",
     "miw/analyse/notes.py",
     ("    for token in _FACTUAL.findall(prose):", "    for token in []:"),
     "prose"),

    # --- the over-claim gate ------------------------------------------------
    ("reach an observation is allowed to implicate the whole footprint",
     "miw/analyse/score.py",
     ("    if sources is None:\n        return True",
      "    if True:\n        return True"),
     "reach"),

    # --- the trust boundary -------------------------------------------------
    ("trust a quote too short to verify is accepted as evidence",
     "miw/schema.py",
     ("        if len(quote) < 12:", "        if False:"),
     "trust"),
    ("trust a claim is built with no source URL at all",
     "miw/schema.py",
     ("        if not source_url:", "        if False:"),
     "trust"),
    ("trust a canonical registry settles a claim outside its remit",
     "miw/trust.py",
     ("                if kind is None or kind in kinds:\n"
      "                    return Tier.AUTHORITATIVE\n"
      "                return Tier.CORROBORATING",
      "                return Tier.AUTHORITATIVE"),
     "trust"),
    ("trust the remit is applied to a vendor's own site, not only to a lent registry",
     "miw/trust.py",
     ("    if not (own and not lent):", "    if True:"),
     "trust"),
    ("trust an invariant reads the verdict off the row instead of recomputing it",
     "main.py",
     ("        if not [c for c in (a.get(\"claims\") or []) if _claim_substantiates(c)]:",
      '        if not [c for c in (a.get("claims") or []) if c.get("substantiating")]:'),
     "trust"),
    ("trust widening to a serving provider drops the registry remit",
     "miw/trust.py",
     ("        registry_domains=subject.registry_domains,\n", ""),
     "trust"),
    ("trust a package's borrowed registry is treated as its own site",
     "miw/schema.py",
     ("            registry_domains=((reg[0],) if reg else ()),",
      "            registry_domains=(),"),
     "trust"),
]


def run(gate: str, tree: pathlib.Path) -> bool:
    """True when the gate FAILED, which is what a mutation should cause.

    A gate is normally an `eval/run_eval.py` suite. Some guarantees are pinned by the
    pytest suite instead — the UI vocabulary is one, because it compares a JavaScript
    map against the scorer and has no evidence fixtures to score. Those are named
    `pytest:<path-or-expr>` so the harness covers the whole gate surface rather than
    only the half that happens to live in the eval.
    """
    if gate.startswith("pytest:"):
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               *gate.split(":", 1)[1].split()]
    else:
        cmd = [sys.executable, "eval/run_eval.py", "--suite", gate]
    r = subprocess.run(cmd, cwd=tree, capture_output=True, text=True, timeout=600)
    return r.returncode != 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gate", help="only mutations guarded by this gate")
    args = ap.parse_args()

    wanted = [m for m in MUTATIONS if not args.gate or m[3] == args.gate]
    if not wanted:
        print(f"no mutations for gate {args.gate!r}")
        return 2

    print(f"MIW mutation check — {len(wanted)} defect(s) restored, one at a time\n")
    with tempfile.TemporaryDirectory() as tmp:
        tree = pathlib.Path(tmp) / "tree"
        shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "out", "state", "data", "*.pyc"))

        # The unmutated tree must be green, or a "CAUGHT" below proves nothing.
        for gate in sorted({m[3] for m in wanted}):
            if run(gate, tree):
                print(f"  ABORT  gate {gate!r} is already failing before any mutation")
                return 2

        missed = []
        for label, relpath, (old, new), gate in wanted:
            f = tree / relpath
            original = f.read_text()
            if old not in original:
                print(f"  STALE   [{gate}] {label}")
                print(f"          anchor no longer in {relpath} — the mutation needs "
                      f"rewriting, and until it does this gate is unproven")
                missed.append(label)
                continue
            f.write_text(original.replace(old, new, 1))
            try:
                caught = run(gate, tree)
            finally:
                f.write_text(original)
            print(f"  {'CAUGHT' if caught else 'MISSED'}  [{gate}] {label}")
            if not caught:
                missed.append(label)

    print()
    if missed:
        print(f"  {len(missed)} MUTATION(S) NOT CAUGHT — those gates assert nothing")
        return 1
    print("  every restored defect was caught by its gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
