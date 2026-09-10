"""Does the workflow behave the same with an API key as it does on the Claude Code CLI?

That question has been open since it was first asked, and until now it could not even
be attempted: `requirements.txt` carried `; extra == "..."` markers, which are pyproject
metadata — in a plain requirements file they evaluate False, so pip silently skipped
`openai`, and the API path could not import after a clean install.

This harness is deliberately NOT part of `eval/run_eval.py`, whose contract is "no
network, no LLM, no API key" and must stay true. It has two modes:

  --dry-run (default, free)   render every prompt, assert byte-identity across
                              providers, and replay whatever `state/llm_cache` already
                              holds. Because the provider is part of the cache key, the
                              claude_code column often comes back for nothing.
  live                        --providers anthropic --repeats 3 --yes

It calls the real `notes.refine()` with the provider forced, so the ordinary acceptance
gate is the arbiter and nothing is scored that the pipeline would not have used.

WHAT IT CAN PROVE: that every provider receives identical prompt bytes; that the gate
accepts and rejects at comparable rates on a fixed corpus; that no provider invents
sources more often; the measured cost per call.

WHAT IT CANNOT PROVE: that the prose is equally GOOD. There is no reference triad and
no human in the loop. `temperature=0` is not determinism, so within-provider variance
across --repeats is reported FIRST: if it is comparable to the spread between
providers, the honest answer is "indistinguishable at this sample size". And it says
nothing about whether a reviewer accepts — which is the only metric this project
trusts. That answer comes from `Finding.note_provider` accumulating in
`review_decisions`, not from here.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import cases                                          # noqa: E402
from miw import llm                                             # noqa: E402
from miw.analyse import notes                                   # noqa: E402
from miw.analyse.score import findings_for                      # noqa: E402

OUT = ROOT / "out"
PROVIDERS = ("claude_code", "anthropic", "openrouter")


def corpus() -> list[tuple[str, object, object, str]]:
    """(case name, dep, finding, prompt) for every golden case that raises one."""
    rows = []
    for c in cases.load("finding_cases"):
        dep = cases.dependency(dict(c["dependency"]))
        found = findings_for(dep, cases.probe_of(c, dep), cases.research_of(c, dep))
        for f in found[:1]:
            notes.compose(dep, f)
            rows.append((c["name"], dep, f, notes.build_refine_prompt(dep, f)))
    return rows


def assert_prompt_identity(rows) -> dict:
    """Every provider must receive the same bytes. This is the core parity claim."""
    mismatches = []
    for name, _dep, _f, prompt in rows:
        argv = llm._claude_code_argv(prompt, "haiku")
        via_cli = argv[argv.index("-p") + 1]
        if via_cli != prompt:
            mismatches.append(name)
    return {"cases": len(rows), "identical": not mismatches, "mismatched": mismatches}


def evidence_coverage(rows) -> dict:
    """How many prompts actually exercise the <untrusted> block.

    Worth printing loudly: if almost no case carries a substantiating claim, the part
    of the prompt most likely to make providers diverge is barely tested, and any
    agreement number is flattering.
    """
    with_claims = sum(1 for _n, _d, f, _p in rows
                      if any(c.substantiating for c in f.claims))
    return {"cases": len(rows), "with_substantiating_claims": with_claims}


def run_one(dep, f, prompt: str, provider: str, use_cache: bool) -> dict:
    """One provider's attempt at one finding, judged by the ordinary gate."""
    before = (f.what_to_act, f.why_to_act, f.when_to_act, f.note_source)
    os.environ["MIW_LLM_PROVIDER"] = provider
    started = time.time()
    res = llm.complete(prompt, use_cache=use_cache)
    elapsed_ms = int((time.time() - started) * 1000)

    row = {"provider": provider, "ok": res.ok, "error": res.error,
           "cached": res.cached, "cost_usd": res.cost_usd,
           "cost_basis": res.cost_basis, "isolation": res.isolation,
           "model": res.model, "tool_attempts": list(res.tool_attempts),
           "elapsed_ms": elapsed_ms, "accepted": False, "reason": "", "triad": None,
           "needed_fence_strip": False}
    if not res.ok:
        row["reason"] = res.error or "provider returned not-ok"
        return row

    stripped = (res.text or "").strip()
    row["needed_fence_strip"] = stripped.startswith("```")
    triad, reason = notes.judge_rewrite(dep, f, res.json())
    row["reason"] = reason
    row["accepted"] = triad is not None
    if triad:
        row["triad"] = {"what_to_act": triad[0], "why_to_act": triad[1],
                        "when_to_act": triad[2]}
    # Leave the finding exactly as composed: this is a measurement, not a run.
    f.what_to_act, f.why_to_act, f.when_to_act, f.note_source = before
    return row


def similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a or "", b or "").ratio(), 3)


def summarise(results: dict) -> dict:
    """Per provider, then the variance caveat that has to be read first."""
    out = {}
    for provider, rows in results.items():
        attempted = [r for r in rows if r["ok"] or r["error"]]
        if not attempted:
            continue
        accepted = [r for r in rows if r["accepted"]]
        costs = [r["cost_usd"] for r in rows if not r["cached"]]
        out[provider] = {
            "attempts": len(rows),
            "ok": sum(1 for r in rows if r["ok"]),
            "accepted": len(accepted),
            "accept_rate": round(len(accepted) / len(rows), 3) if rows else 0.0,
            "invented_a_source": sum(1 for r in rows
                                     if "invented a source" in r["reason"]),
            "needed_fence_strip": sum(1 for r in rows if r["needed_fence_strip"]),
            "cached": sum(1 for r in rows if r["cached"]),
            "mean_cost_usd": round(statistics.fmean(costs), 6) if costs else 0.0,
            "cost_basis": sorted({r["cost_basis"] for r in rows if r["cost_basis"]}),
            "mean_ms": int(statistics.fmean([r["elapsed_ms"] for r in rows])) if rows else 0,
            "isolation": sorted({r["isolation"] for r in rows if r["isolation"]}),
            "tool_attempts": sorted({t for r in rows for t in r["tool_attempts"]}),
        }
    return out


def within_provider_variance(results: dict) -> dict:
    """Spread between repeats of the SAME provider on the same case.

    Reported before any cross-provider number. temperature=0 is not determinism, and
    if a provider disagrees with itself as much as it disagrees with another, the
    comparison is noise.
    """
    out = {}
    for provider, rows in results.items():
        by_case: dict[str, list[str]] = {}
        for r in rows:
            if r["triad"]:
                by_case.setdefault(r["case"], []).append(r["triad"]["what_to_act"])
        ratios = [similarity(v[i], v[j])
                  for v in by_case.values() if len(v) > 1
                  for i in range(len(v)) for j in range(i + 1, len(v))]
        if ratios:
            out[provider] = {"pairs": len(ratios),
                             "mean_self_similarity": round(statistics.fmean(ratios), 3)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--providers", default="", help="comma-separated; default dry-run")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--yes", action="store_true", help="required for a live run")
    ap.add_argument("--limit", type=int, default=0, help="cap the corpus, for a smoke test")
    args = ap.parse_args()

    rows = corpus()
    if args.limit:
        rows = rows[:args.limit]
    print(f"  corpus: {len(rows)} prompt(s) from eval/golden/finding_cases.json")

    ident = assert_prompt_identity(rows)
    print(f"  prompt byte-identity across providers: "
          f"{'OK' if ident['identical'] else 'FAILED ' + str(ident['mismatched'])}")
    cov = evidence_coverage(rows)
    print(f"  prompts carrying a substantiating claim: "
          f"{cov['with_substantiating_claims']}/{cov['cases']}")
    if cov["with_substantiating_claims"] < 4:
        print("  ! the <untrusted> evidence block is barely exercised, so any agreement")
        print("    number below is flattering. Add evidence-rich golden cases first.")

    live = [p.strip() for p in args.providers.split(",") if p.strip()]
    dry = args.dry_run or not live
    if not dry and not args.yes:
        est = len(rows) * len(live) * args.repeats
        print(f"\n  would make up to {est} LLM call(s) across {live}.")
        print("  re-run with --yes to spend. Budget caps still apply "
              f"(LLM_MAX_CALLS={llm.MAX_CALLS}, LLM_MAX_SPEND_USD={llm.MAX_SPEND_USD}).")
        return 2

    targets = live or list(PROVIDERS)
    saved = os.environ.get("MIW_LLM_PROVIDER")
    results: dict[str, list] = {p: [] for p in targets}
    try:
        for provider in targets:
            for name, dep, f, prompt in rows:
                for _ in range(args.repeats if not dry else 1):
                    if dry and not llm._cache_path(provider, llm.resolve_model(
                            llm.DEFAULT_MODEL, provider), prompt).exists():
                        continue        # nothing cached: a dry run spends nothing
                    row = run_one(dep, f, prompt, provider, use_cache=True)
                    row["case"] = name
                    results[provider].append(row)
    finally:
        if saved is None:
            os.environ.pop("MIW_LLM_PROVIDER", None)
        else:
            os.environ["MIW_LLM_PROVIDER"] = saved

    print(f"\n  {'dry run (cache replay only)' if dry else 'live run'}")
    var = within_provider_variance(results)
    if var:
        print("\n  within-provider variance (read this FIRST):")
        for p, v in var.items():
            print(f"    {p:14} {v['pairs']} repeat pair(s), "
                  f"mean self-similarity {v['mean_self_similarity']}")
    else:
        print("\n  within-provider variance: not measured (need --repeats > 1)")

    summary = summarise(results)
    if summary:
        print(f"\n  {'provider':14} {'att':>4} {'acc':>4} {'rate':>6} {'inv':>4} "
              f"{'fence':>6} {'$/call':>9} {'ms':>7}  isolation")
        for p, v in summary.items():
            print(f"  {p:14} {v['attempts']:4} {v['accepted']:4} {v['accept_rate']:6} "
                  f"{v['invented_a_source']:4} {v['needed_fence_strip']:6} "
                  f"{v['mean_cost_usd']:9} {v['mean_ms']:7}  {','.join(v['isolation'])}")
    else:
        print("\n  nothing measured. In dry-run this just means the cache holds no")
        print("  entry for these prompts yet; run `analyse --refine` once, or go live.")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"parity_{time.strftime('%Y-%m-%d')}.json"
    path.write_text(json.dumps({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "dry_run": dry,
        "repeats": args.repeats, "prompt_identity": ident,
        "evidence_coverage": cov, "within_provider_variance": var,
        "summary": summary, "results": results,
    }, indent=2, default=str))
    print(f"\n  -> {path}")
    print("\n  Reads as: identical prompt bytes and comparable gate outcomes are")
    print("  demonstrable here. Whether the prose is BETTER is not — that needs")
    print("  reviewer decisions broken out by Finding.note_provider.")
    return 0 if ident["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
