# Market Intelligence Agent

_Curriculum drift watch · short name `MIW`, which is still the package name, the repo directory and every environment variable._

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
  someone who did not write the code can check them. On the current run: 257 of 460
  dependencies can be spoken for officially; the other 203 are workbook-declared names
  with no authority set, and are therefore incapable of producing a deprecation,
  pricing or version finding until a human adds their domains.

**No API keys are required for any of this.** `miw/research/official.py` fetches each
tool's own changelog / pricing / deprecation / status pages and quotes them verbatim.
Search only *discovers* candidates; the LLM only *writes prose*. Neither supplies facts.

## The inputs: two files per course, and which parts are load-bearing

MIW reads a course from **two** places, and they are not interchangeable — the slide
deck is what MCQs, coding questions and reading materials are *written from*, and the
JSON export is the **published** artifact downstream of it:

```
data/courses/<slug>.json                     the portal export      MANDATORY
config/constants.py  COURSES[<slug>]         the roster entry       MANDATORY
data/sheets/<Course> - Course Contents.xlsx  the workbook           optional, load-bearing
```

**The export** — a list containing one course object, `topics[] -> units[] -> contents[]`.
Field names must match the portal's exactly; a plausible file using `content_markdown`
instead of `content` yields **0 records and no error at all**. A session is counted from
a `LEARNING_SET` unit containing an `INTERACTIVE_VIDEO` content.

**The roster entry** is the real gate: `cmd_ingest` iterates `COURSES.items()`, so a JSON
dropped into `data/courses/` with no entry is never opened. It needs `title` (the
course's identity everywhere downstream — it is what every `Location.course` stores) and
`expect_sessions`, which is read directly and so cannot be omitted.

**The workbook** is optional — a course without one inventories fine — but it is not
supplementary. It is the only bridge back to the authoring source, and it carries four
things the export cannot:

| Sheet / column | What it gives MIW |
|---|---|
| `Course Outline` -> `Session No.`, `Session ID` | **the authoritative session numbering** |
| `Course Outline` -> `Outline`, `Key Takeaways` | the slide content — 0 of 24 outlines appear anywhere in the export |
| `Course Outline` -> `Session PPT` | the deck link (recorded; not yet checked) |
| `Session-Practice Content Linked` -> `Unit ID` | which MCQ / coding / RM unit came from which session's deck |
| `Entity Ids - Tools & Versions U` -> `Tool@version` | hand-recorded version pins — the **only** source for these, and what makes S6 real |

Its `Reading Material Content` and `Recorded Session Transcript` columns are *not* read:
measured, they are already in the export (24 of 25 and 17 of 17).

Two consequences worth knowing before you drop a course in:

* **Session numbers come from the workbook where there is one.** The export's positional
  count disagreed for 19 of 26 sessions in Intro to Gen AI, because a unit called
  `Common Mistakes` looks like a session and is not numbered as one. `ingest` prints
  which source it used — `(workbook)` or `(position)` — and how many numbers it
  corrected. Set `expect_sessions` to the **curriculum's** count (the row count of
  `Course Outline`), never the export's.
* **Every S6 version-drift finding currently rests only on workbook pins.** Drop the
  workbooks and you lose four of the nine live findings.

Adding a course is three manual edits today — there is no CLI command and no upload. A
workbook whose filename maps to no course in `sheets.WORKBOOK_COURSES` is reported and
**skipped**, never guessed at: the fallback to its own filename once invented three
phantom courses and misattributed 3,633 locations.

## Run it

```bash
pip install -r requirements.txt              # core: needs no API key at all
pip install -r requirements-optional.txt     # only if you want search or LLM refinement
python3 main.py ingest       # course JSON + workbook -> out/content_records.jsonl
python3 main.py extract      # records      -> out/inventory.json + registry/tools.yaml
python3 main.py probe        # inventory    -> out/probe_<date>.json
python3 main.py probe --course "Intro to Gen AI" --session 4-6 --tiers critical
python3 main.py research     # flagged deps -> out/research_<date>.json
python3 main.py research --nominate   # ...and ask a model for replacement candidates
python3 main.py analyse      # everything   -> out/findings_<date>.json
python3 main.py decks        # read the 85 session decks: slide text + reachability
python3 main.py gaps         # workbooks + official docs -> topics we do not teach yet
python3 main.py gaps --dry-run   # print what it would raise; write nothing
python3 main.py report       # findings     -> out/digest_<date>.md + out/courses/<slug>/
python3 main.py report --course ai_for_finance   # one course's digest (reads them all)
python3 main.py verify       # audit the trust invariants on those artifacts
python3 main.py resolve-packages --dry-run   # sheet-declared names that are really packages
python3 main.py triage --list # reviewer decisions; teaches the next run
python3 eval/run_eval.py     # scored regression harness (offline, no API key)
python3 main.py serve        # local web UI -> http://127.0.0.1:8000
python3 main.py run-weekly   # all of it, unattended (see .github/workflows/weekly.yml)
python3 main.py watch        # daily: did a vendor move? (poll only, reports nothing)
python3 main.py watch --investigate   # ...and run the scoped stages for what it touched
python3 -m pytest tests/ -q
python3 eval/parity.py --dry-run   # do both providers behave the same? (free)
```

Optional keys live in `.env` (see `.env.example`); every one is optional.

## Two directions of inquiry: is it still true, and is it still complete

Stages `probe`, `research` and `analyse` run **inside-out**: they start from the
dependency list, which `extract` derives from the course content, and ask whether what
we teach is still true. That is a regression check, and it is most of the system.

It cannot, even in principle, notice that a session is *incomplete*. A technique the
courses have never mentioned has no dependency row, so it is never probed, never
researched, and can never become a finding — the inventory's universe is exactly what is
already taught.

`gaps` runs the other way. It reads the workbook to learn what each session covers, reads
official vendor documentation as an enumeration of an area, and reports the part of that
enumeration that appears in no session's outline — naming the session it belongs in:

```
Add Parallel function calling to Building LLM Applications session 10
(Tool Use & Function Calling in LLMs) — extend that deck's outline and its Key Takeaways.
```

Two rules keep it honest, and both were added after measuring what happens without them:

* **Two independent vendors, or it is not reported.** Read on their own, four Google
  documentation pages produced 32 findings, most of them API mechanics rather than
  teachable topics ("Batch embeddings", "Migration from gemini-embedding-001"). What
  separates an industry topic from one vendor's implementation detail is that a
  competitor documents it too, so a topic must be named by two sources whose
  `official_domains` are **disjoint**. 32 became 7. Recall suffers and that is the right
  direction: a digest reporting three real gaps gets read, one reporting thirty of which
  nine are real does not get read twice.
* **Already taught anywhere is not a gap.** Checked against the workbook's own wording
  and across both vendors' names for the topic, because Google writes "Zero-shot vs
  few-shot prompts" where the workbook writes "Prompting Techniques (Zero-shot,
  One-shot, Few-shot, CoT)".

Areas live in `registry/topics.yaml`, hand-owned, two sources each. The stage prints its
own blind spot — how many indexed sessions fall inside a declared area and which do not —
so an undeclared subject cluster is a reported number rather than something you have to
infer. It needs the course **workbook**: a course registered without one never receives a gap
finding.

## Two ways in: a manual run, and a daily watch

Both modes run the same stages; they differ only in how the **scope** is decided.

*Manual* — a reviewer picks it: `--course`, `--session`, `--tiers`, `--dep-id`, or the
Run tab.

*Daily* — a vendor event picks it. `main.py watch` polls one watermark per source (a
package's published version; for a vendor catalogue, a hash of the table's *meaning* —
sorted `(id, status, replacement)` triples, so a redesign is not an event but a status
change is). If nothing moved, it prints one line and exits. If something moved, it emits
a signal naming the affected **identifiers** and resolves them against the inventory.

That resolution is why the daily mode does not need to send course content anywhere:

```
signal ref  llama-3.3-70b-versatile
  → index lookup   O(1), 18 locations
  → courses        ['Building LLM Applications', 'Intro to Gen AI']
  → sessions       [6, 8, 10, 11, 12]
  → 4 of 464 taught dependencies in scope
LLM calls          0
```

Course text is read once, at ingest, to build an index keyed by identifier. Nothing
downstream re-reads it — 9.2 M characters of curriculum against a 3,510-character
prompt that contains **zero** characters of course body text. Adding a fifth course adds
rows to the index, not tokens to a prompt.

Three deliberate silences keep an unattended daily run quiet enough to trust:

* **A first sighting is a baseline** — recorded, never investigated. Otherwise day one
  emits every historical deprecation as though it just happened.
* **A signal naming ids we do not teach resolves to an empty scope** and is recorded
  without running a stage.
* **A bare registry bump raises no finding.** 48 of 71 taught packages pin no version, so
  every patch release would otherwise raise a finding no reviewer could act on. A release
  is news only once it leaves the taught pin behind. The version is still written to the
  artifact — state is recorded, only news is reported.

A scoped run **merges**: a Groq signal refreshed 4 findings and carried 38 forward
untouched. And the periodic full sweep stays, because silent death emits no release
event — codetotutorial never announced itself, it just stopped answering.

## The UI: one page per course

Styled entirely locally — **no font host, no CDN, no external request of any kind** —
because the app has to work on a laptop with no network, which is also when someone is
most likely to be reading last week's digest. A test asserts it.

Severity is encoded three ways at once: the **word**, a **colour** on a temperature
scale (slate → blue → amber → red), and a **shape** (a dot, square for critical). Colour
alone fails a colour-blind reader, and fails again in a screenshot pasted into a chat.

**Opening a finding** slides over a panel answering the three questions the card cannot:
*where is this*, *what proves it*, and *what do I change*. Per location it shows the
session, topic, unit, the enclosing item's own title, the object type, copyable unit and
content ids, and the **actual excerpt resolved from `field_path`** with the matched term
highlighted. Locations that cannot resolve say which reason applies — a workbook tool
declaration has no path into the export at all, a slide outline resolves to its workbook
cell, a stale path says the content moved — because a blank panel could mean either
"nothing there" or "we could not look". A finding resting only on our own probe says so
rather than showing an empty evidence list.

Set `MIW_PLATFORM_UNIT_URL` (placeholders `{course_slug} {unit_id} {content_id}
{session_no} {topic_name}`) to turn every location into a direct link into the learning
platform. Left empty, ids are copyable instead: a guessed URL that 404s cannot be told
apart from a unit that moved.
Each finding carries a severity rail down its left edge, and each course row on the
overview carries the worst severity in that course — so "which course needs me" is a
glance rather than a read.

`python3 main.py serve` then open http://127.0.0.1:8000. Navigation is by URL, so a
course page can be linked, bookmarked and reloaded:

```
#/                     every course, with its own finding counts
#/c/intro_to_gen_ai/findings | /runs | /inventory | /digest | /run
#/watch                vendor-signal timeline (a signal is about a vendor, not a course)
#/global/runs | /trust | /digest
```

**A course page shows that course's share, recomputed — not the global total filtered.**
This matters more than it sounds: 129 of the 460 dependencies are referenced by more
than one course, and `llama-3.3-70b-versatile` carries a blast radius of 67 across the
curriculum but **13** inside Intro to Gen AI. Copying the stored number would overstate
that page by 5.2×, so every count is re-derived against the inventory and each card says
what it is looking at:

> 4 of 20 references are in this course; the numbers below are this course's share.
> Also taught in **Building LLM Applications** (16 refs, blast 54).

Severity is shown twice when the two disagree — `critical` · *in this course: high* —
because the global figure is what the store and the precision metric key on, while
`critical` above three prose mentions would be its own exaggeration. The urgency line
follows the local one.

**Runs stay put.** A run scoped to one course never appears in another's history, while
an unscoped sweep appears in all of them, because it really did audit them all. Digests
work the same way: each course gets `out/courses/<slug>/digest_<date>.md` and the
all-courses roll-up stays at `out/digest_<date>.md`. A missing per-course digest says so
rather than falling back to the roll-up, which would put other courses' findings under a
per-course heading.

Three things a course page deliberately refuses to pretend:

* a **triage decision is global** — it is recorded against a finding, which has no
  course in it — so the warning appears at the point of the click, not in a footnote;
* **reviewer precision has no course dimension**, so it is labelled `all courses`;
* a course declared in `config/constants.py` but never ingested says **"has not been
  ingested yet"**, because an empty page reads as a healthy course.

## Running the UI, and what each tab does

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

Then **What to fix** (urgency-ranked cards with evidence, affected sessions and the
what/why/when note, each with inline accept/reject), **Tools & models used** (all 460,
filterable — including `no official source` to find the unwatched ones), **Sources &
decisions** (runs the source check, and shows what the system has learned from your
accept/reject decisions), and **Weekly report** (the raw markdown).

The labels on the page are deliberately not the names in the code. `watch_tier`,
`blast_radius`, `diff_class` and `S7` are precise and they are also words nobody outside
this repo has heard, so the page renders every one of them through a single `WORDS` map
(`impact`, `only mentioned`, `no change`, `model retired`) while the API, the CLI flags
and every saved scope keep the internal names unchanged. The real name is on hover
wherever losing it would stop you cross-checking the page against the digest.

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

## Setting up Claude Code as the LLM provider

**Read this first if you are testing this repo.** Everything deterministic runs with no
key and no model at all — ingest, extract, probe, official-page evidence, findings, the
digest and the whole UI. You only need this section for the three optional model-facing
features: `analyse --refine`, `research --nominate`, and `main.py agent`.

MIW calls the **`claude` CLI in print mode** rather than a metered API, which is what
lets it be tested on a Claude Code entitlement instead of per-token spend.

### 1. Install the CLI and sign in

```bash
npm install -g @anthropic-ai/claude-code     # or the installer for your platform
claude                                       # sign in once, interactively, then quit
```

Availability is literally `shutil.which("claude")` — if the binary is on `PATH`, MIW
will use it. Nothing else needs configuring.

### 2. Confirm MIW can see it

```bash
python3 -c "from miw.llm import provider_status; print(provider_status())"
```

```
{'provider': 'claude_code', 'forced': '', 'model_logical': 'haiku',
 'model_resolved': 'haiku',
 'candidates': [{'name': 'claude_code', 'available': True, 'why': ''},
                {'name': 'anthropic', 'available': False, 'why': 'ANTHROPIC_API_KEY is not set'},
                {'name': 'openrouter', 'available': False, 'why': 'OPENROUTER_API_KEY is not set'}]}
```

`provider: none` means it could not find one, and `candidates[].why` says why for each.
The same line appears at the bottom of every digest, so a run always records which
provider produced its prose.

### 3. Run something that uses it

```bash
python3 main.py agent --dep "gemini-2.0-flash" --kinds DEPRECATION
python3 main.py analyse --course "Intro to Gen AI" --refine
```

### Provider order, and why the CLI is first

`auto` tries **`claude_code` → `anthropic` → `openrouter`**, and the CLI is deliberately
first: an `ANTHROPIC_API_KEY` exported for some unrelated tool must not silently start
charging on the next `--refine`. Metered spend is opt-in:

```bash
MIW_LLM_PROVIDER=anthropic python3 main.py analyse --refine   # explicit, and billable
MIW_LLM_PROVIDER=none      python3 main.py analyse            # force the template path
```

All three providers receive **byte-identical prompts** — `eval/parity.py` exists to prove
it — so switching provider cannot change a finding.

### What the CLI is and is not allowed to do

Worth knowing before you run it against your own machine, because the CLI has ambient
tools that an API key does not:

| Guard | Value |
|---|---|
| Tools denied by name | **16** — `Read`, `Write`, `Edit`, `Bash`, `WebFetch`, `WebSearch`, `Task`, … |
| Max turns | `4` |
| Working directory | a fresh empty temp dir, never the repo and never `/tmp` |
| Daily ceilings | `120` calls, `$2.00`, tracked in `state/llm_budget.json` |

Isolation is **subtractive** on this path (`--disallowedTools`), where the HTTP providers
grant nothing to begin with — `LLMResult.isolation` records which mechanism held
(`denylist:16` vs `no-tools`), because those are different facts.

Two caps to know about: they are per **day**, on disk, and shared by every stage. If a
run reports `no LLM provider available` after a lot of agent work, check
`state/llm_budget.json` before assuming the CLI broke.

```bash
LLM_MAX_CALLS=200 LLM_MAX_SPEND_USD=5.00 python3 main.py agent --limit 10
```

### The other two optional keys

```bash
# .env — see .env.example for the full annotated list
TAVILY_API_KEY=...        # search discovery: lets MIW look for replacements a vendor
                          # never names. Without it, vendors' own pages still work.
ANTHROPIC_API_KEY=...     # only if you deliberately want metered API refinement
```

Neither is needed to test the system. `python3 main.py verify` and
`python3 eval/run_eval.py` are both fully offline and keyless, and are the fastest way
to confirm a fresh clone is sound.

## Setting up OpenRouter as the LLM provider

OpenRouter is the third provider, behind the Claude CLI and the Anthropic API. Use it
when you want one key to reach many models, or when the `claude` CLI is not available on
the machine.

### 1. Install the client and set the key

```bash
pip install -r requirements-optional.txt      # brings openai>=2.0, which this path uses
```

```bash
# .env
OPENROUTER_API_KEY=sk-or-v1-...
LLM_BASE_URL=https://openrouter.ai/api/v1     # already the default
LLM_MODEL=anthropic/claude-haiku-4-5          # OpenRouter-style id, vendor-prefixed
```

### 2. Select it explicitly — `auto` will not choose it

```bash
MIW_LLM_PROVIDER=openrouter python3 -c \
  "from miw.llm import provider_status; print(provider_status())"
```

`auto` tries **`claude_code` → `anthropic` → `openrouter`** and stops at the first one
available, so with the `claude` CLI on `PATH` OpenRouter is never reached. That order is
deliberate: a key exported for some other tool must not silently start charging. Set the
variable per command, or put `MIW_LLM_PROVIDER=openrouter` in `.env` to make it the
default.

### 3. Run something that uses it

```bash
MIW_LLM_PROVIDER=openrouter python3 main.py analyse --course "Intro to Gen AI" --refine
MIW_LLM_PROVIDER=openrouter python3 main.py agent --dep "gemini-2.0-flash"
```

Every digest records the provider and model that produced its prose, so a run is always
attributable.

### Read this before you spend anything

**The `$2.00` daily spend cap only works for models MIW knows the price of.**
`PRICE_USD_PER_MTOK` in `miw/llm.py` covers exactly three:

| Model | Priced |
|---|---|
| `anthropic/claude-haiku-4-5` | yes |
| `anthropic/claude-sonnet-5` | yes |
| `anthropic/claude-opus-5` | yes |
| anything else (`google/…`, `openai/…`, `meta-llama/…`) | **no** |

For an unpriced model the call is recorded at `$0.00` with
`cost_basis: "unpriced"`, it prints

```
WARNING: no price known for 'google/gemini-2.5-flash'; the spend cap is
acting as a call cap for this model
```

and the run continues. Your only remaining guard is `LLM_MAX_CALLS` (120/day). So if you
point this at a model outside that table, **set a call budget you are happy to pay for**
and treat the dollar cap as absent:

```bash
LLM_MAX_CALLS=25 MIW_LLM_PROVIDER=openrouter python3 main.py agent --limit 5
```

Adding a model to `PRICE_USD_PER_MTOK` is a two-line change and makes the dollar cap real
again. The prices are not guessed at on purpose — a wrong number is worse than a
declared absence.

### Two knobs that interact

`MIW_LLM_MODEL` takes a *logical* name (`haiku` / `sonnet` / `opus`) and is translated per
provider. `LLM_MODEL` is an **OpenRouter-only override that wins over it**:

```bash
# LLM_MODEL is still anthropic/claude-haiku-4-5, so this does NOT get you Sonnet
MIW_LLM_MODEL=sonnet MIW_LLM_PROVIDER=openrouter python3 main.py analyse --refine

# Change the OpenRouter id itself
LLM_MODEL=anthropic/claude-sonnet-5 MIW_LLM_PROVIDER=openrouter python3 main.py analyse --refine
```

Clear `LLM_MODEL` if you would rather drive everything with the logical names.

### What is the same, and what differs

Same across all three providers, and asserted by `eval/parity.py`: the prompt bytes, so
switching provider cannot change a finding; `temperature=0`; and the fact that the model
is handed **no tools at all**.

Different, and recorded on every result so the two are never confused:

| | Claude CLI | OpenRouter / Anthropic API |
|---|---|---|
| `isolation` | `denylist:16` — 16 ambient tools denied by name | `no-tools` — no tools array is sent |
| Output limit | the CLI has no equivalent knob | `max_tokens=1500` (`LLM_MAX_OUTPUT_TOKENS`) |
| Cost | not metered | `cost_basis` is `estimated` or `unpriced` |

Two more things worth knowing while testing:

* **Replies are cached** in `state/llm_cache/`, keyed by provider, model and prompt — so
  re-running the same stage costs nothing. Delete the directory to force real calls.
* **A typo in `MIW_LLM_PROVIDER` fails loudly** and uses no LLM at all. It used to fall
  through to OpenRouter, which meant a misspelling silently changed backend and started
  charging.

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

## The same workflow, either provider

The LLM is optional and never a source of facts — it may only reword what the
deterministic pipeline already established, and a rewrite naming a URL that is not
already in the evidence is rejected whole. But it should not matter *which* backend does
the rewording, so all three receive the same prompt and face the same gate:

| | `claude_code` (default) | `anthropic` | `openrouter` |
|---|---|---|---|
| needs a key | no — uses your Claude Code entitlement | `ANTHROPIC_API_KEY` | `OPENROUTER_API_KEY` |
| tools in reach | none, via a 16-name **denylist** | none, by sending no tools | none, by sending no tools |
| prompt | identical bytes | identical bytes | identical bytes |
| cost | reported by the CLI | estimated from tokens | estimated from tokens |

`auto` prefers the CLI whenever `claude` is on PATH, *even if* an API key is set —
testing runs on the entitlement precisely to avoid metered spend, so a key exported for
some unrelated tool must never silently start charging. To spend, ask for it:
`MIW_LLM_PROVIDER=anthropic`.

Isolation is **subtractive**, which is why "attach the tools the same way" means sending
*fewer* parameters, not translating a list: the CLI has ambient tools that must be
denied, the HTTP APIs have none. One asymmetry remains and is documented rather than
glossed: the CLI still injects its own reduced system prompt and runs an agent loop up
to `--max-turns`, so the API paths are *strictly more* isolated, not identically so.

`python3 eval/parity.py --dry-run` proves the prompt byte-identity for free and replays
whatever the cache holds. It reports within-provider variance **first**, because
`temperature=0` is not determinism — if a provider disagrees with itself as much as it
disagrees with another, the comparison is noise. It can show that the gate accepts at
comparable rates; it cannot show the prose is *better*. That needs reviewer decisions
broken out by `Finding.note_provider`, which the digest and UI now record.

## Discovery: the one place a model proposes something

Everything else in MIW is deterministic. Discovery is the exception, and it is built so
that being wrong is cheap:

```
the model emits   a name, and at most a bare domain          <- nothing else is read
our code does     DNS -> liveness -> fetch -> lift prose      <- every fact comes from here
a claim exists    only if we read the page ourselves
```

A fabricated domain dies at DNS before anything is fetched. A fabricated tool dies when
its own site is fetched. A model-supplied URL is rejected rather than repaired, because
choosing what we fetch is the one thing it may never do. The 2026 measurements on
research agents — 3–13% of cited URLs fabricated, citation accuracy 40–80% — are about
citations the agent supplies; here there is no citation for it to supply.

**Refutations are reported, not dropped.** Each run's digest carries a tally, and a
refutation rate that falls to zero is a suspicious signal rather than a good one:

> _Discovery: 9 candidates considered · 3 verified · 5 refuted · 1 unverifiable (host
> alive but would not serve us — not treated as absent)._

The one judgement — *would this actually do the job the session uses the old tool for?*
— cannot be settled by any page, so it never becomes a claim. The model emits bounded
factors, Python does the arithmetic, and it renders as clearly labelled opinion:

> _Model opinion (not evidence · claude_code/haiku): fit 0.98 — Free and frictionless
> access confirmed, but API integration for practical sessions remains undocumented.
> Unknown: coverage_of_steps, maturity._

When it cannot judge, it says *not assessed* rather than producing a number — a real 0.5
and a parse failure must never look the same.

Discovery runs on a small rotation (`DISCOVERY_SLICE`, default 4/week over the ~35
eligible dependencies) and is opt-in for the model half. The deterministic nominators —
a successor the vendor names in its own deprecation notice, and open search — run
regardless.

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
miw/llm.py              provider resolution: claude CLI | Anthropic | OpenRouter | none
miw/ingest/portal.py    course JSON -> addressable records       (vendored traversal)
miw/ingest/sheets.py    workbook tool columns + hand-recorded version pins
miw/ingest/outline.py   the PPT stream: slide outlines, and the session numbering
                          the export gets wrong  <- the workbook is the authority here
miw/extract/            7 extractors: tags, imports, n8n, links, hosts, packages, prose
miw/extract/locate.py   field_path -> the actual excerpt, for the finding detail panel
miw/probe/              deterministic health: HTTP, PyPI, npm, GitHub
miw/probe/n8n_upstream.py  n8n node index + n8n's own breaking-change rules (cached 7d)
miw/probe/pricing.py    free-tier erosion, detected as the loss of free wording
miw/probe/catalogue.py  column-role HTML table reading (makes proximity bugs impossible)
miw/probe/models.py     is a taught model id still served, and what does its provider say to use
miw/vendors/            vendor adapters: Groq, Google AI (n8n still in probe/n8n_upstream.py)
miw/research/           official-first evidence, then constrained search, then prose
miw/analyse/score.py    severity, weighted blast radius, signal mapping
miw/analyse/project.py  a finding as it applies to ONE course (projected, not filtered)
miw/watch/              the daily vendor-signal watcher
miw/reporters/          weekly markdown digest
miw/scope.py            run scoping: course / session / tier / kind selection
miw/api/                FastAPI app, job runner, single-file frontend
registry/tools.yaml     human-owned: aliases + official_domains (the authority set)
registry/review_queue.yaml  unresolved tag candidates - NOT monitored until promoted
eval/                   the gate: 4 deterministic suites, offline, no API key
```

Reusable logic is **vendored, not imported**: the prior-art scripts live in a path with
spaces and carry stale hardcodes. Each copy names its source file in a comment.

## What it does not do

Recommends only. It never edits course JSON, sheets, or the CMS — it writes to `out/`
and `state/` and produces a digest. Probing is read-only, rate-limited, and never
authenticated against a third party.
