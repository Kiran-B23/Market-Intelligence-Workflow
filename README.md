# MIW — Market Intelligence & Curriculum Gap Analyser

A weekly watch on everything the curriculum depends on: third-party tools, hosted
services, packages, models, and n8n nodes. It exists because the content team's
detector of record used to be a student complaint — the Gen AI course taught
**codetotutorial** as the way to read a GitHub repo, it died, and nobody knew until
students reported the dead URL, even though **deepwiki** had existed for a while.

Full requirements and rationale: [`PRD.md`](PRD.md).

## Ground truth is official sources, and that is enforced in code

The rule: **a claim about a tool is only ever substantiated by that tool's own
official pages, or by a canonical registry.** It is enforced structurally, not by
prompt.

- `miw/trust.py` grades every source into four roles. Authority is **relative to the
  subject**: `docs.n8n.io` is ground truth about n8n and merely a lead about Groq.
  - `AUTHORITATIVE` — the vendor's own domains, or a canonical registry *within its
    remit* (PyPI settles a version, never a price). May substantiate any claim.
  - `CORROBORATING` — reputable independents. May support, never decide.
  - `LEAD_ONLY` — directories, forums, unknown hosts. May only *nominate* something to
    verify. This is how a replacement nobody configured gets discovered.
  - `EXCLUDED` — content farms and SEO listicles. Never fetched, never shown to the model.
- `Claim.build()` in `miw/schema.py` will not construct a claim without a source URL, a
  retrieval date, and a verbatim quote, and it computes its own tier. There is no code
  path from model recall to a finding.
- Strict claim kinds (existence, deprecation, pricing, version, implementation) require
  `AUTHORITATIVE`. Anything less is dropped, not hedged.
- Official domains are **derived from the curriculum's own links**. A content author
  writing `<a href="https://console.groq.com/keys">` *is* the assertion that `groq.com`
  speaks for Groq — safer than recalling vendor domains from memory.
- A dependency with no known official domain simply produces no strict claims. Missing
  registry data costs recall, never correctness.
- `python3 main.py verify` audits those guarantees against the artifacts on disk, so
  someone who did not write the code can check them. On the current run: 230 of 448
  dependencies can be spoken for officially; the other 218 are workbook-declared names
  with no authority set, and are therefore incapable of producing a deprecation,
  pricing or version finding until a human adds their domains.

**No API keys are required for any of this.** `miw/research/official.py` fetches each
tool's own changelog / pricing / deprecation / status pages and quotes them verbatim.
Search only *discovers* candidates; the LLM only *writes prose*. Neither supplies facts.

## Run it

```bash
pip install -r requirements.txt
python3 main.py ingest       # course JSON  -> out/content_records.jsonl
python3 main.py extract      # records      -> out/inventory.json + registry/tools.yaml
python3 main.py probe        # inventory    -> out/probe_<date>.json
python3 main.py probe --course "Intro to Gen AI" --session 4-6 --tiers critical
python3 main.py research     # flagged deps -> out/research_<date>.json
python3 main.py analyse      # everything   -> out/findings_<date>.json
python3 main.py report       # findings     -> out/digest_<date>.md
python3 main.py verify       # audit the trust invariants on those artifacts
python3 main.py triage --list # reviewer decisions; teaches the next run
python3 eval/run_eval.py     # scored regression harness (offline, no API key)
python3 main.py serve        # local web UI -> http://127.0.0.1:8000
python3 main.py run-weekly   # all of it, unattended (see .github/workflows/weekly.yml)
python3 -m pytest tests/ -q
```

Optional keys live in `.env` (see `.env.example`); every one is optional.

## The UI

```bash
python3 main.py serve          # http://127.0.0.1:8000   (API docs at /api/docs)
```

FastAPI serving one self-contained HTML file — no npm, no build step, and **no external
request of any kind** (system fonts, inline favicon), so it works on a laptop with no
network, which is often exactly when someone is reading last week's digest.

**Run audit** is the default tab, and the reason the UI exists rather than just the
digest. Pick a scope — any combination of courses, a session spec (`12`, `3-7`,
`3,5,9-11`), watch tiers, dependency kinds, a cap — choose which stages to run, and
press Run. Before starting, the panel tells you what you are about to do: how many
dependencies are in scope, how many URLs they reference, and a rough duration. That
matters because probing is throttled to 1.5s per domain: the whole inventory is ~38
minutes, one course's sessions 4-6 is ~3. The log streams live and is persisted, so
reloading mid-run shows it from the beginning.

Then **Findings** (severity-ranked cards with evidence, affected sessions and the
act/why/when note, each with inline triage), **Inventory** (all 448 dependencies,
filterable — including `no authority` to find the unmonitorable ones), **Trust** (runs
the invariant audit, shows what the system has learned from reviewers), and **Digest**
(the raw markdown).

### How runs are executed

Runs are queued **one at a time** — two concurrent runs would double our request rate
against the same vendors, which defeats the politeness budget. Each stage runs as
`python3 main.py <stage> --course ...`: the same command cron runs, so there is one
execution path rather than two, and a stage that crashes takes down a subprocess rather
than the server. `ingest` and `extract` are deliberately **not** scopable — they rebuild
the whole inventory, and scoping them would silently shrink it.

Jobs live in `state/jobs.db`, and anything left `running` when the process dies is
marked **interrupted** at the next startup — never `done`. The prior Curriculum Gap
Analyzer's own notes record the trap: agent runs on FastAPI `BackgroundTasks` with no
queue, where "a server restart loses in-flight runs". Showing a half-finished audit as
complete is the failure that would actually mislead someone.

Triage from the browser goes through the same `miw.triage` path as the CLI, so a
decision made here suppresses and teaches identically. It binds to 127.0.0.1 and has
**no authentication** — it reads and writes your local state. Don't expose it.

## The LLM, and why testing needs no API key

The evidence path is deterministic, so the model is never load-bearing. `miw/llm.py`
defaults to shelling out to the **Claude Code CLI** (`claude -p --output-format json`),
which draws on an existing Claude Code entitlement rather than a metered API key. It is
not free — roughly $0.01 per call, mostly fixed cache overhead — so there is a disk
cache keyed on the prompt (re-running an eval after a code change is free for unchanged
cases), a per-process call and spend budget, and no tools are granted to the call.

The model does exactly one job: rewriting the action note. `miw/analyse/notes.py`
composes `what_to_act` / `why_to_act` / `when_to_act` deterministically first, then
`refine()` optionally asks the model to rewrite those three parts from
already-verified facts. A rewrite is **rejected wholesale** if it drops a part or
introduces a URL not already in the finding's evidence. Run it with
`python3 main.py analyse --refine`; without the flag, notes are template-composed and
the digest is complete.

## Triage: how precision becomes measurable

```bash
python3 main.py triage --list
python3 main.py triage <id> --accept
python3 main.py triage <id> --reject --reason "we cite this blog, it is not a taught step"
python3 main.py triage <id> --accept --when "next cycle - session 3 is not running until March"
```

Three things follow from a decision. **Precision** becomes computable
(`accepted ÷ triaged`) — the PRD's supporting metric, previously not measurable at all.
**Rejections suppress recurrences**, but only while the finding's fingerprint is
unchanged: if the evidence moves, the finding returns, so "stop showing me this" can
never hide a tool going from redirected to dead. And **per-dimension corrections become
guidance** in `registry/learned_feedback.md`, injected into the refinement prompt —
correcting "when" is a different lesson from correcting "what".

Half of all decisions are held out from that learning
(`miw/triage.split_of`) and used only for scoring. Without the split, precision decays
into self-assessment the moment the system starts learning from feedback — the failure
documented in `agentic-interview-question-generator/eval/run_eval.py`, where runs scored
0.9 on their own confidence while reviewers rejected most of the set.

## Evals

`python3 eval/run_eval.py` — 30 golden cases across three offline suites, plus the
held-out precision metric. **Half the cases are must-not-fire**, because the failure
that kills this system is a phantom finding, not a missed one: a healthy tool, our own
network failing, an anti-bot 403, a robots refusal, docs churn on a passing mention,
and a deprecation claim sourced to a blog must each produce *nothing*.

Suites are held to 100% — every case encodes a rule the code is meant to enforce, so
one failure is a regression. Precision is advisory until there is real triage volume,
and it never gates on MIW's own severity score.

## Two distinctions that keep the report trustworthy

**`unreachable` is not `broken`.** A 401/403/429 proves the host is alive and serving,
so it reports as `inconclusive`. A transport error with no HTTP status is `unreachable`
— our problem. Only a 404/410 on a URL the curriculum links to becomes `broken`, and
only after two consecutive runs agree. Reporting our own blocked request as a dead tool
is the fastest way to lose the content team's trust.

**Change, not state.** Page comparison uses a 64-bit simhash with a Hamming threshold,
not an exact hash. Measured on real vendor pages minutes apart, exact hashing called 16
of 30 pages "changed"; the similarity threshold called 1 — the one that had genuinely
started redirecting.

## Layout

```
main.py                 stage CLI; run-weekly is the unattended entry point
config/                 settings (all optional), course roster, internal hosts
miw/trust.py            source authority policy  <- the load-bearing module
miw/schema.py           data model; the citation gate lives in Claim.build()
miw/net.py              SSRF guard, politeness, simhash          (vendored + extended)
miw/state.py            SQLite: week-over-week diffing, flap protection
miw/ingest/portal.py    course JSON -> addressable records       (vendored traversal)
miw/extract/            7 extractors: tags, imports, n8n, links, hosts, packages, prose
miw/probe/              deterministic health: HTTP, PyPI, npm, GitHub
miw/probe/n8n_upstream.py  n8n node index + n8n's own breaking-change rules (cached 7d)
miw/probe/pricing.py    free-tier erosion, detected as the loss of free wording
miw/research/           official-first evidence, then constrained search, then prose
miw/analyse/score.py    severity, weighted blast radius, signal mapping
miw/reporters/          weekly markdown digest
miw/scope.py            run scoping: course / session / tier / kind selection
miw/api/                FastAPI app, job runner, single-file frontend
registry/tools.yaml     human-owned: aliases + official_domains (the authority set)
registry/review_queue.yaml  unresolved tag candidates - NOT monitored until promoted
```

Reusable logic is **vendored, not imported**: the prior-art scripts live in a path with
spaces and carry stale hardcodes. Each copy names its source file in a comment.

## What it does not do

Recommends only. It never edits course JSON, sheets, or the CMS — it writes to `out/`
and `state/` and produces a digest. Probing is read-only, rate-limited, and never
authenticated against a third party.
