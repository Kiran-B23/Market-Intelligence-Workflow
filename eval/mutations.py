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
    ("trust a package's borrowed registry is treated as its own site",
     "miw/schema.py",
     ("            registry_domains=((reg[0],) if reg else ()),",
      "            registry_domains=(),"),
     "trust"),
]


def run(gate: str, tree: pathlib.Path) -> bool:
    """True when the gate FAILED, which is what a mutation should cause."""
    r = subprocess.run([sys.executable, "eval/run_eval.py", "--suite", gate],
                       cwd=tree, capture_output=True, text=True, timeout=600)
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
