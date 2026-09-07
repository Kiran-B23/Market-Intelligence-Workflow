# PRD — Market Intelligence & Curriculum Gap Analyser (MIW), Phase 1

Status: draft for review · Owner: gen-ai-content · Date: 2026-09-07 · Phase 1
Target dir: `/home/nxtwave/MIW` (currently empty)

---

## 1. Context — why we are building this

NxtWave courses teach through external dependencies: third-party tools, hosted
services, SDKs, models, docs pages, and n8n nodes. Those dependencies drift
constantly; the curriculum does not.

The triggering incident: the Gen AI curriculum taught **codetotutorial** as the
way to understand a GitHub repo. The tool stopped working. Nobody on the content
team knew until **students complained the URL was dead**. A near-equivalent
replacement (**deepwiki**) already existed, and had for a while — we simply had
no mechanism that was looking.

That failure mode is structural, not a one-off:

- discovery is **reactive** — the detector of record is a student complaint
- detection is **late** — a live cohort is already blocked when we find out
- knowledge is **tribal** — whoever authored a session is the only person who
  knows what it depends on
- there is **no queryable dependency inventory** — no one can answer "which
  sessions break if tool X dies?" without reading every session by hand

Drift is also broader than dead links. Classes the team has already hit or
expects: free tier → paid; an API or UI changes so the taught steps no longer
match; a taught package version goes stale or breaks; a model ID is retired;
vendor docs are rewritten so our screenshots and step lists are wrong; n8n bumps
a node version and the taught node behaves differently; a materially better
alternative appears.

**Intended outcome:** the content team learns of every curriculum-affecting
change from this system, weekly, with evidence and a ranked recommendation
attached — *before* a student does.

---

## 2. Goals & non-goals

### Goals (Phase 1)

- **G1 — Canonical dependency inventory.** What the curriculum actually depends
  on, extracted from course JSON + sheets, addressable back to the exact
  course/unit/topic/field it came from.
- **G2 — Weekly change detection** across the drift classes in §4 —
  deterministic where possible, targeted research where not.
- **G3 — Recommendation with evidence** for every problem: what changed, how bad
  it is for which sessions, what to do instead, ranked alternatives.
- **G4 — Suppress repeats.** Week N reports what changed since week N−1, not the
  same standing facts forever.
- **G5 — Human in the loop.** The system recommends only (§9).

### Non-goals (explicitly out of Phase 1)

- Editing course content, course JSON, sheets, or the CMS — not even a staged patch.
- Auto-filing tickets or messaging students.
- Competitor curriculum scraping.
- Ingesting student support tickets as a signal (Phase 2 — closes the loop on the
  very complaints that motivated this).
- Any claim about a tool that is not backed by a fetched, dated citation.

### North-star metric

**Share of curriculum-affecting changes caught by MIW before the first student
report.** Today that is, by construction, near zero.

Supporting metrics: median lead time (detection → first student report or cohort
start); **precision** — findings the team marks actionable ÷ findings raised,
target ≥ 70% by week 4, because a noisy weekly report gets ignored and the system
dies; inventory recall vs. a hand audit of one course.

---

## 3. Users & scope

| User | Job | What MIW gives them |
|---|---|---|
| Content author / SME | Keep my sessions correct | "These 3 of your sessions are affected — evidence, and the suggested replacement" |
| Curriculum lead | Decide what gets revised this sprint | Severity- and blast-radius-ranked list across all 4 courses |
| Delivery / support | Stop being surprised by a live cohort | Early warning on anything a running cohort touches this week |

**Phase 1 course scope (decided):** Intro to Gen AI (26 sessions), Building LLM
Applications (29), AI for Finance (18), PSE (13) — the four already exported as
JSON + xlsx in `/home/nxtwave/Market Intelligence Workflow/`.

---

## 4. Signal taxonomy — what counts as a finding

| # | Drift class | Example | Detector |
|---|---|---|---|
| S1 | Dead / moved URL | codetotutorial 404 | deterministic HTTP |
| S2 | Login- or paywall added | tool now demands signup for the taught step | fetch heuristic + research |
| S3 | Free → paid / free tier cut | free quota drops below what a session needs | pricing-page diff + research |
| S4 | Deprecated / sunset / abandoned | repo archived, sunset notice, no commits in N months | registry + repo probe |
| S5 | Implementation / UX change | taught click-path or API call no longer matches | page diff + research |
| S6 | Package version drift | taught pin far behind latest, or breaking major | PyPI / npm registry |
| S7 | Model change | taught model ID deprecated, retired, superseded | provider model & deprecation pages |
| S8 | Docs rewritten | vendor docs restructured; our steps/screenshots wrong | content-hash + section diff |
| S9 | n8n node / version update | node renamed or version bumped (we teach `n8n@2.17.8`) | n8n releases + node docs |
| S10 | Better alternative appeared | deepwiki vs codetotutorial | research, rotating watch |
| S11 | Curriculum topic gap | industry-expected topic absent or outdated | research vs course outline |

S1–S9 are **regression** signals (what we teach is now wrong). S10–S11 are
**opportunity** signals (still right, no longer best). They are scored and
reported in separate sections — mixing them buries the urgent under the
interesting.

---

## 5. What already exists — reuse map

Phase 1 is not greenfield. `/home/nxtwave/Market Intelligence Workflow/` already
turns course JSON into a per-session tool sheet, and three in-house agent repos
set the house conventions. **Take these rather than rewriting:**

**JSON traversal layer** — `Market Intelligence Workflow/build_course_sheet.py`:
`is_session()`, `ordered_units()` (stable sort — two units share `order == 5` in
Gen AI's AI Ethics topic), `markdown_content()`, `reading_material()`,
`unit_label()` (QUIZ/ASSESSMENT units have no `unit_name`; the title lives in
`exam_details.title`), `transcript()`, `slide_url()`, `normalise()`.

**Tool extractors** — `Market Intelligence Workflow/extract_tools.py`:
`packages()` (the `PIP` regex + `NOT_A_PACKAGE` stopwords), `models()` (the
`MODEL` regex), `tools_for()` (the tier-ordered composer), the `BOLD` regex, and
`ENUM_MAP` over `test_cases[].test_case_enum`.

**Deck text** — all of `extract_deck.py` (`extract()`, `collapse()`), which
parses saved Google Slides HTML. Many no-code/SaaS tools are named *only* on
slides. `extract_tools.deck_text()` correctly skips recap slides.

**Grounding checks** — the anti-hallucination patterns are the most transferable
prior art: `verify_build.py`'s `flat()` corpus-flattening + its every-tool-is-in-
the-corpus and no-invented-version-pin assertions, and `verify_outlines.py`'s
`support()` / `words()` / `stem()` word-overlap scoring with a 0.6 threshold.

**Existing (flat) inventory** — the `Entity Ids - Tools & Versions U` sheet in
each `* Course Contents.xlsx`, and `ai4f_authored.py:TOOLS`. Today a tool mention
is a **flat string**, `Name[@version] [(qualifier)]`, tiered by newline
(services → packages → models → URLs) and comma-separated within a tier. There is
no per-tool row, no URL, no category, no first-seen date. Normalising this into
real records is exactly stage [2].

**Additional inventory seeds** — `Gen_AI_Keytakeaways.xlsx:Sheet1` and
`agentic-mcq-generation-workflow/docs/Gen-AI-Course-Curriculum.xlsx:courses-curriculum`
both carry an explicit per-session **`Tools`** column. Together with the Entity
sheets and `ai4f_authored.TOOLS`, that is enough to hand-seed
`registry/tools.yaml` without an LLM.

**Export parsers already written** — `agentic-ai-content-workflows/workflows/
genai_coding_question_generation/curriculum/portal_export.py:read_export()`
handles the single-element-list wrapper, walks `topics → units → contents`,
branches on `unit_type`, and — valuably — records *why* each item was skipped in
`ExportContents.skipped`. It has tests. For sheets, header aliasing has been
written twice already: `workflows_v2/topic_mastery/tools/import_curriculum.py`
(`rows_from_csv`, `rows_from_xlsx`, `_norm_header`, `_HEADER_ALIASES`) and
`workflows/research_agent/services/curriculum_sync.py` (`ColumnMap`,
`_resolve_columns`). Reuse one; the workbook headers vary enough that alias
resolution is not optional.

**The closest existing thing to MIW** — the `revamping-session` skill at
`gen-ai-courseware/introduction-to-genai-revamping/.claude/skills/revamping-session/`
already does, manually and per-session, what MIW automates: Step 1 inventories
every tool/model/platform/API/SDK a session mentions, Step 2 web-verifies
currency against official docs via a **doc-source routing table**, Step 3 writes
a `_research/<slug>.research.md` evidence file. Live evidence files already exist
under `revamped-reading-materials/.../_research/`. That routing table is exactly
what stage [4] needs, and those evidence files are a ready-made ground truth set
for evaluating it.

**House agent conventions** — from `Coding-Questions-Generator` (layout:
`pipeline/{graph,state,nodes/}`, `prompts/*.md`, `config/{settings,llm}.py`,
`pyproject.toml` + mirrored `requirements.txt`, argparse `main.py` with a headless
`run-all`) and `agentic-interview-question-generator` (`src/sources/base.py` —
the `Record` dataclass, `is_safe_public_url()` SSRF guard, and the shared
`USER_AGENT`; `src/sources/tavily_search.py` — `TavilyConnector.health_check()`
and the `_tavily_preflight` degrade-instead-of-fail pattern; `src/memory.py` —
SQLite run history, `normalize_content()`, canonical/superseded runs;
`src/llm_client.py` — `chat_completion_json()`, `_call_with_retry()`).

**Known gaps to fill (not reusable as-is):**
- `extract_tools.CATALOG` is a hand-curated ~50-name allowlist tuned to AI for
  Finance. Running `collect()` on `PSE.json` returns several sessions with an
  **empty** tool list. This allowlist is the single biggest correctness gap and
  must become a maintained registry.
- The three richest signals in the exports are **entirely unmined**:
  - `units[].question_tags[].tag_name_enum` — 2419 distinct tags in the Gen AI
    export, and many *are* tool names: `n8n Platform`, `Gamma AI`, `Napkin AI`,
    `Perplexity AI`, `Hugging Face Platform`, `SerpAPI Tool Node`,
    `Murf.ai Platform`, `Groq API Audio Support`. This is the highest-recall
    **structured** tool signal available and the best replacement for `CATALOG`.
  - `solutions[].solution_answer` — full reference source code (real imports), the
    highest-precision evidence available; and for `tool_type: "N8N"` items (32 in
    Gen AI) it is a complete n8n workflow JSON containing node type strings
    (`@n8n/n8n-nodes-langchain.*`) and their `typeVersion` — a precise, structured
    S9 signal.
  - **URL hosts** — AI4F alone has 544 URLs (top hosts `api.twelvedata.com` 103,
    `python.langchain.com` 82, `ai.google.dev` 53); Gen AI's top hosts are
    `docs.n8n.io` 144, `console.groq.com` 18, `murf.ai` 12, `serpapi.com` 10.
- **Two incompatible export schemas exist.** Schema B ("portal export", the
  canonical one, used by all four Phase 1 courses): a single-element list wrapping
  `course → topics[] → units[] → contents[]`. Schema A (legacy pre-revamp): a flat
  list of `{unit_id, content_data}` with **no course/topic nesting at all**. The
  two share **zero `unit_id`s** — anything joining them must join on normalised
  title, never on ID (documented in
  `gen-ai-courseware/introduction-to-genai-revamping/CLAUDE.md`).
- **Pooled exams hide content in a second location.** Questions live either
  inline under `unit.contents[]` *or* under `exam_sections[i].contents[]` /
  `exam_details[i].question_details[]` (9 pooling units in the Gen AI export). A
  walker that reads only the inline path **silently skips them** — a known trap
  recorded in project memory at
  `.claude/projects/-home-nxtwave-Downloads-course-data/memory/exam-json-two-structures.md`.
- **External tool links are `<a href="…" target="_blank">`, not markdown.**
  Markdown `[](…)` / `![](…)` is used almost entirely for S3-hosted media. The
  quotes are escaped inside the JSON strings, so a naive regex silently returns
  zero. Bodies are markdown with embedded raw HTML, including `<details>` /
  `<summary>` blocks (428 in Gen AI) where prerequisite tool links cluster.
- **No change detection exists anywhere in-house.** Closest primitives:
  `agentic-ai-content-workflows/platform_core/cache/content_cache.py:content_hash()`.
- **No scheduler exists in-house.** The only cron precedent on disk is a
  third-party clone's GitHub Actions `schedule:` block.
- Hardcodes to fix on reuse: the `n <= 17` deck cutoff in
  `extract_tools.collect()`, and the stale scratchpad deck paths at
  `verify_outlines.py:11` and `verify_build.py:172`.

---

## 6. System design

Six stages, each writing a durable artifact so any stage can be rerun or audited
alone. The stage boundaries matter more than the code inside them: **the
inventory is the product; the rest is refresh.**

```
course JSON + xlsx sheets + saved decks
        │
  [1] Ingest ──────────► content_records.jsonl   (normalised, addressable)
        │
  [2] Extract ─────────► inventory.json          (THE SPINE: dependencies × locations)
        │
  [3] Probe ───────────► probe_<date>.json       (deterministic, whole inventory)
        │
  [4] Research ────────► research_<date>.json    (LLM + Tavily, flagged items only)
        │
  [5] Analyse ─────────► findings_<date>.json    (severity, blast radius, recommendation)
        │
  [6] Report ──────────► weekly digest           (delivery surface TBD — §10 D1)
        │
     state.db ◄───────── snapshots for week-over-week diffing (G4)
```

### [1] Ingest

Normalise all four exports into one addressable record stream, so no later stage
knows a course's quirks. Reuse the traversal layer from §5 verbatim.

`content_record = { course, topic_name, unit_id, unit_name, unit_type,
content_id, object_type, content_type, title, body_text, field_path,
source_file, ingested_at }`

`field_path` is the JSON pointer the text came from — the addressing idea already
used by `.claude/skills/course-media-audit/scripts/scan_media.py`, which walks
course JSON carrying `topic_name / unit_name / title / question_id / option_id /
question_type` as context. Reuse that walk; do not write a second one.

Body text is pulled from all signal sources, ranked by evidence quality:
`solutions[].solution_answer` (code) > `question_tags[].tag_name_enum` >
`test_cases[].test_case_enum` > reading-material markdown > deck text > URL hosts.

Two things the ingest layer must get right, because both fail *silently*:

- **A schema adapter per export format.** Phase 1 targets Schema B (all four
  courses), but the adapter boundary is cheap now and expensive to retrofit; a
  Schema A adapter is a later drop-in. Build on
  `portal_export.py:read_export()` and keep its skip-reason tracking — an
  ingest that cannot say what it ignored cannot be trusted to have covered a
  course.
- **Both question locations.** Walk inline `unit.contents[]` *and* pooled
  `exam_sections[i].contents[]` / `exam_details[i].question_details[]`. An
  ingest-time assertion should reconcile the count of units carrying
  `exam_details`/`exam_sections` against the number actually traversed.

Sheets are a parallel input, not a second source of truth: the workbooks
contribute the `Tools` columns and session ordering
(`topic_number_in_course`, `unit_number_in_course`), joined to the JSON on
normalised title.

### [2] Extract — the dependency inventory

The hard, high-value stage. Seven extractors over the record stream, ordered by
how much they can be trusted:

1. **Question tags** — `question_tags[].tag_name_enum`, filtered through the
   registry. Highest-recall structured signal; also the best bootstrap for the
   registry itself.
2. **Code imports** — mine `solutions[].solution_answer` for actual import /
   client-construction statements. Highest precision in the entire export.
3. **n8n artifacts** — parse the `tool_type: "N8N"` workflow JSON out of
   `solution_answer` and collect node `type` + `typeVersion` per node. This is
   what makes S9 detectable rather than guessed at.
4. **Links** — every external link with its `field_path`, extracted from
   `<a href>` **and** markdown **and** `<iframe src>`, plus the structured URL
   fields (`slides[].slide_url`, `multimedia_url`). Exclude internal asset hosts
   (`nkb-backend-*.s3.amazonaws.com`, `media-content.ccbp.in`).
5. **URL hosts → services** — map hosts to canonical services via the registry
   (`api.twelvedata.com → Twelve Data`, `console.groq.com → Groq`).
6. **Packages + versions + model IDs** — reuse `packages()` and `models()`.
7. **Named tools in prose** — the residual, and the only one needing an LLM: a
   curated **registry** (`registry/tools.yaml`, hand-seeded from the Entity
   sheets, `ai4f_authored.TOOLS`, the workbook `Tools` columns, and the question
   tags — mapping `codetotutorial → {homepage, docs_url, aliases, category}`),
   plus an LLM pass for names the registry does not know. **LLM-proposed names
   land in a review queue, not the inventory** — an unreviewed extractor feeding a
   monitor produces confident nonsense. This replaces `extract_tools.CATALOG`.

`dependency = { dep_id, kind: url|service|tool|package|model|n8n_node,
canonical_name, homepage, docs_url, aliases[], taught_version?, vendor?,
locations[ {course, topic, unit_id, unit_name, content_id, field_path,
evidence_source} ], first_seen, watch_tier, review_status }`

Deduping across courses is **required**: codetotutorial in 6 sessions is one
dependency with 6 locations, not 6 findings.

`watch_tier` controls research cost — `critical` (a student performs the taught
step live; a block becomes a support ticket), `standard`, `mention-only` (named in
prose, nothing depends on it).

### [3] Probe — deterministic, whole inventory, weekly

Cheap, no LLM, reproducible:

- HTTP status, full redirect chain, final URL, TLS validity, latency
- **content hash + extracted main-text hash** of the landing/doc page → drives S8
- login-wall / paywall heuristics (auth redirect, "sign in to continue", a price
  table appearing where none was last week)
- registry lookups: PyPI / npm latest version, release date, yanked flags (S6)
- GitHub: archived flag, last commit date, open-issue spike (S4)
- n8n releases feed (S9)

Politeness and reliability are requirements, not polish: per-domain rate limit,
conditional requests, backoff, the shared `USER_AGENT` and `is_safe_public_url()`
guard from `src/sources/base.py`, and a `probe_status` of
`ok | changed | broken | unreachable | inconclusive`.

**`unreachable` is not `broken`.** Our own network flaking must never be reported
as a dead tool: a finding requires N consecutive failing runs or corroboration
from a second vantage point.

### [4] Research — targeted, Tavily + LLM

Runs **only** for: anything the probe marked `changed`/`broken`; every
`critical`-tier dependency on a rotating schedule (so S3/S5/S10 are caught even
while the URL still returns 200); and course-level gap analysis (S11).

Per dependency the researcher answers a fixed question set — alive? pricing
changed? taught flow changed? sunset notice? credible alternatives? — and must
return for every claim a `{ claim, source_url, retrieved_at, quote }`.

**Claims without citations are dropped, not downgraded.** This is the most
important quality rule in the system. Model knowledge is a hypothesis generator;
the citation is the evidence. It is the same discipline as `verify_outlines.py`
scoring authored text against the deck corpus, and the media-audit rule of naming
an image from its pixels rather than the prose around it.

Alternatives are scored on what matters in a classroom: does it do the taught
job, is there a genuinely free student path, signup friction, India availability,
maturity / abandonment risk.

### [5] Analyse

`severity = f(breakage_class, blast_radius, cohort_urgency)`

- **breakage class** — S1/S3 (student hard-blocked) outrank S8 (stale screenshots)
- **blast radius** — location count, weighted up when the dependency sits in an
  assignment/assessment rather than prose (`object_type` is
  `CODING_QUESTIONS`/`OBJECTIVE_QUESTIONS`, not `LEARNING_RESOURCE`)
- **cohort urgency** — is a live cohort hitting this unit within ~2 weeks

Then diff against `state.db` and classify each finding `new`, `worsened`,
`unchanged` (suppressed from the digest, retained in the store), or `resolved`
(explicitly reported — the team needs to know a fire went out).

### [6] Report

Each finding carries: what changed, evidence with retrieval dates, affected
sessions as concrete locations, severity, and a **recommended action** an author
can act on — "replace the codetotutorial step in Gen AI / Unit 4 / Topic 3 with
deepwiki; free, no signup for public repos; taught click-path differs at step 2".

---

## 7. Data model

Four files plus one database, all under `out/` and `state/`:

| Artifact | Grain | Purpose |
|---|---|---|
| `content_records.jsonl` | one content item | addressable normalised text |
| `inventory.json` | one dependency | the spine; dedup + locations + watch tier |
| `probe_<date>.json` | one dependency | deterministic weekly observation |
| `research_<date>.json` | one dependency | cited claims + ranked alternatives |
| `findings_<date>.json` | one finding | severity, blast radius, recommendation, diff class |
| `state.db` (SQLite) | run history | snapshots for week-over-week diffing, repeat suppression |
| `registry/tools.yaml` | one tool | **human-owned**: canonical names, aliases, homepages |
| `review_queue.yaml` | one candidate | **human-owned**: LLM-proposed names awaiting approval |

---

## 8. Stack & layout

Aligned with `Coding-Questions-Generator`, the closest house template, and with
what is already installed on this machine (`tavily-python`, `playwright`,
`gspread`, `google-auth`, `openpyxl`, `openai`; note **pandas is not installed**,
and `openpyxl` must read the hand-built workbooks with `data_only=True` and
**not** `read_only=True`, which raises on their unsized worksheets).

```
MIW/
  main.py                 argparse: ingest | extract | probe | research | analyse | report | run-weekly
  config/{settings.py,llm.py,constants.py}
  prompts/*.md            one file per LLM node (house convention)
  ingest/                 reuses Market Intelligence Workflow traversal
  extract/                extractors + registry loader
  probe/                  http, pypi, npm, github, n8n
  research/               tavily connector + researcher nodes
  analyse/                severity, blast radius, diff
  reporters/              markdown (Phase 1) + pluggable surfaces (D1)
  registry/tools.yaml
  data/                   dropped-in course JSON, xlsx, saved decks
  out/, state/
  tests/
  pyproject.toml + requirements.txt + Makefile
```

**Orchestration:** stages [1]–[3] are pure deterministic Python CLIs — no LLM, no
graph framework, because a framework there would only hide the stage boundaries
that make this auditable. Stages [4]–[5] are a small **LangGraph** graph, matching
the house pattern, with the fan-out over flagged dependencies.

**LLM:** OpenRouter via `langchain-openai.ChatOpenAI`, reusing the house two-tier
split — `anthropic/claude-haiku-4-5` for research/analysis, a cheap model for
high-volume validation. Do not build on
`platform_core/tools/search/web_search_tool.py`; it is a stub returning
placeholder results.

**Schedule:** weekly cron. GitHub Actions `schedule` + `workflow_dispatch` is the
only precedent on disk; a local crontab entry calling `main.py run-weekly` works
equally well for Phase 1. See D2.

Secrets in `.env` (`OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `GITHUB_TOKEN`); none
in the repo.

**Reuse mechanics — vendor, don't import.** The prior-art scripts live in
`/home/nxtwave/Market Intelligence Workflow/`, a path with spaces that cannot be
imported as a Python package without hackery, and they carry stale hardcodes
(`n <= 17` deck cutoff, dead scratchpad deck paths). So the reusable functions get
**copied into `MIW/ingest/` and `MIW/extract/` with a provenance comment naming
the source file**, and the hardcodes are fixed on the way in. The four course
JSON exports and workbooks are likewise **copied** into `MIW/data/` — they are
dated snapshots, and MIW's week-over-week diffing depends on knowing exactly which
snapshot a finding was computed against.

---

## 9. Autonomy & trust boundary

**Recommend only** (decided). MIW never edits course JSON, sheets, or the CMS. It
writes only to its own `out/` and `state/` and produces a digest. This matches
the house convention in the media-audit skill, where swapping URLs inside the
course JSON is a separate, explicitly-requested step, and `gsheet_publish.py`,
which is dry-run unless `confirm=True`.

Guardrails:
- Every finding is falsifiable: cited source, retrieval date, exact curriculum
  locations affected.
- Findings are **never** raised from LLM prior knowledge alone.
- `registry/tools.yaml` and `review_queue.yaml` are human-owned.
- Read-only, rate-limited, robots-respecting probing; no authenticated probing of
  third-party services; no scraping behind logins.

---

## 10. Open decisions

- **D1 — Delivery surface** (user: "will decide later"). Phase 1 writes canonical
  `findings_<date>.json` + a Markdown digest to disk, and keeps delivery behind a
  thin `reporters/` interface so a Sheets appender, Slack digest, or Artifact
  dashboard drops in later without touching the pipeline. Note two conflicting
  house Sheets auth conventions exist — service account
  (`Coding-Questions-Generator/scripts/gsheet_publish.py`) vs. OAuth
  (`agentic-interview-question-generator/src/sheets_writer.py`); pick service
  account for an unattended weekly job.
- **D2 — Cadence & trigger.** Weekly assumed; day/hour TBD, and whether a cohort
  start should trigger an off-cycle run for that course.
- **D3 — Export refresh.** Phase 1 assumes course JSON/xlsx are dropped into
  `data/` by hand. Whether MIW should pull fresh exports itself is deferred.
- **D4 — Blast-radius inputs.** Per-unit enrolment data may not be available;
  without it blast radius falls back to location count + content type.
- **D5 — Decks.** Deck text materially improves tool recall for no-code/SaaS
  tools, but decks must be saved to `data/decks/` by hand (`extract_deck.py` has
  no network access) and the 17 previously-downloaded AI4F decks live in a stale
  scratchpad. Phase 1 treats decks as optional input.

---

## 11. Phase 1 build plan

| M | Milestone | Done when |
|---|---|---|
| M0 | This PRD committed as `MIW/PRD.md`; repo scaffold, settings, `data/` populated with 4 JSON + 4 xlsx | `main.py --help` lists all stages |
| M1 | **Ingest** — schema adapter + traversal reuse | `content_records.jsonl` for all 4 courses; session counts match 26/29/18/13; pooled exams traversed; skip reasons reported |
| M2 | **Extract** — registry + 7 extractors + review queue | `inventory.json`; PSE no longer returns empty tool lists; question tags, `solution_answer` imports, n8n node versions, and URL hosts all mined |
| M3 | **Probe** — http/pypi/npm/github/n8n + `state.db` snapshots | two consecutive runs produce identical `probe_status` for unchanged deps |
| M4 | **Research** — Tavily + LLM, citation-enforced | every claim carries `{source_url, retrieved_at, quote}`; uncited claims dropped |
| M5 | **Analyse** — severity, blast radius, week-over-week diff | second run's digest contains no `unchanged` findings |
| M6 | **Report** — Markdown digest + `reporters/` interface | a digest a content author can act on without opening the JSON |
| M7 | **Schedule** — `run-weekly` under cron / Actions | one unattended end-to-end run completes and writes a digest |
| M8 | **Backtest** — the codetotutorial acceptance test | see §12 |

---

## 12. Verification

**The acceptance test (M8): replay the incident.** Point MIW at the Gen AI export
that still teaches codetotutorial. It must, without any hand-holding:
1. list codetotutorial in `inventory.json` with every session that references it,
2. mark it broken from a deterministic probe (not from model knowledge),
3. surface deepwiki as a ranked alternative with a fetched, dated citation.

If it cannot reproduce the case that motivated the project, it is not done.

Other checks, each mapping to a stated requirement:

- **Inventory recall (G1)** — hand-audit one course (PSE, the current weak spot,
  ~13 sessions) and compare against `inventory.json`. Report recall and every
  miss. This is the honest measure of stage [2].
- **Inventory recall against existing ground truth (G1)** — the
  `revamping-session` skill has already produced per-session tool inventories and
  `_research/<slug>.research.md` evidence files for Gen AI sessions. Score
  `inventory.json` against those by hand; they are free labelled data and a
  stronger check than any self-consistency test.
- **Pooled-exam coverage (G1)** — assert every unit carrying `exam_details` or
  `exam_sections` contributed records. This trap has already bitten a previous
  scanner, so it gets an assertion, not a code comment.
- **Dedup (G1)** — a tool in N sessions yields exactly one dependency with N
  locations, and one finding.
- **Probe determinism (G2)** — run stage [3] twice back-to-back; unchanged
  dependencies must yield identical status. Any flapping dep is a bug in the
  `unreachable` vs `broken` distinction.
- **Citation enforcement (§9)** — feed the researcher a dependency whose evidence
  is unfetchable and confirm the resulting claim is dropped, not reported with a
  hedge. Reuse `verify_build.flat()` corpus grounding for the extraction side.
- **Repeat suppression (G4)** — run the full pipeline twice with no external
  change; the second digest must be empty except a "no change" line.
- **Blast-radius sanity (G3)** — a dependency inside a `CODING_QUESTIONS` unit
  must outrank the same dependency mentioned only in prose.
- **No invented versions** — port `verify_build.py`'s assertion that no
  `name@version` pin appears that is not present in the source corpus. The
  existing sheets' pins (`n8n@2.17.8`, `gradio@6.6.0`) were recorded by hand at
  recording time and are unrecoverable from the export; MIW must not fabricate
  replacements for them.
- **Politeness** — no domain receives more than the configured rate; a run
  against a blocked/erroring host degrades (as `_tavily_preflight` does) rather
  than failing the pipeline.

---

## 13. Implementation notes — where reality differed from this plan

Recorded during the Phase 1 build, because each of these changed a decision above.

**The codetotutorial backtest cannot be run against live data.** §12 assumed the Gen AI
export "still teaches codetotutorial". It does not: `codetotutorial` appears **0 times**
across all four exports, and **deepwiki** has already replaced it (30 mentions, Gen AI
session 4, *Productivity Power-Up with AI Tools*). The content team fixed this before
MIW existed. The acceptance test is therefore reconstructed as a deterministic offline
fixture in `tests/test_dead_tool_backtest.py`, asserting the four behaviours the real
case needed: a dead referenced URL becomes `broken`; only after two consecutive runs
agree; the finding is S1, substantiated by our own probe with no citation required, and
lists every session that referenced the tool; and a nominated replacement is reported
only once verified against its own official domain. DeepWiki is now itself in the
inventory (`standard` tier, `deepwiki.com`) and is monitored.

**The workbooks *do* carry recoverable version pins.** §5 stated the sheets' pins
(`n8n@2.17.8`, `gradio@6.6.0`) "were recorded by hand at recording time and cannot be
recovered from the export" — true of the JSON, but they are readable from the workbooks
themselves. `miw/ingest/sheets.py` recovers **50 pins across 3 workbooks**, giving 42
dependencies a `taught_version` where the JSON alone yielded only n8n `typeVersion`s.
That makes S6 version-drift detection real rather than aspirational. The no-invented-pin
rule in §12 still stands: pins are read, never inferred.

**Exact content hashing is unusable for S8.** Measured on real vendor pages minutes
apart, an exact prose hash reported **16 of 30** pages as "changed" — rotating banners,
counters and randomised ids. Replaced with a 64-bit simhash over word shingles and a
Hamming threshold (`miw/net.py`), which reported **1** on the same set: the page that had
genuinely started redirecting. Docs-prose drift on a `mention-only` dependency is
suppressed entirely.

**Citations had to be separated from dependencies.** Reading materials link heavily to
news, press and academic sources. Treated as dependencies, these put "Bbc", "Nytimes"
and "Teslarati" into the tool inventory. `miw/extract/links.py:is_citation()` reuses the
trust layer's own source lists — anything the policy already classes as a *source* rather
than a *subject* is a citation — plus a news/press set and placeholder-host patterns.
This cut services from 132 to 86 and made the list read like an actual tool inventory.

**Authority for a package is its registry, not a website.** §6 gave every dependency an
`official_domains` set, which left all 71 package dependencies unable to substantiate
anything. `Dependency.subject()` now folds in the PyPI/npm project page, and
`REGISTRY_AUTHORITY` in `miw/trust.py` limits each registry to the claim kinds it
actually governs — PyPI settles a version and says nothing about pricing.

**Watch tiers needed a stricter definition of "graded".** Counting any mention inside a
graded question as runtime evidence put **221 of 281** dependencies into `critical`,
making the tier meaningless. A tool named in an MCQ stem is prose that happens to be
graded; `critical` now requires runtime evidence or a link a student must open to answer.

**Prose matching had to exclude package-kind names.** Matching the English words
"requests", "datasets", "application" and "Python" produced ~9,400 phantom locations and
swamped real evidence. Prose matching now applies only to service/tool kinds, filtered
by a stop list; packages are established by imports and install commands only.

**A `verify` stage was added** (not in the original plan): `python3 main.py verify`
audits the trust invariants against the artifacts on disk, so the guarantees are
checkable by someone who did not write the code.

---

## 14. S9 rebuilt on n8n's own breaking-change declarations

The n8n signal was not merely incomplete, it was non-functional, and the review that
found it is worth recording because the failure was silent in three separate ways.

**It never asked the question that matters.** The detector fetched the newest GitHub
release and grepped its body for the node's name. A change from a year ago is hundreds
of releases back and therefore invisible, and nothing ever checked whether the taught
node still exists. Probed against
`@n8n/n8n-nodes-langchain.memoryBufferWindow` — the Simple Memory node the course
teaches at typeVersion 1.3 — it returned **`ok`**.

**The version it compared was garbage.** `re.search(r"(\d+[\w.]*)", tag)` on the tag
`n8n@2.38.4` matched **`8n`**, from the letters of "n8n". Every version comparison
downstream was comparing that string, so the `n8n_new_release` branch could never fire
correctly either. Now anchored on the `@` or a `v` prefix.

**A third of the taught nodes were never inventoried.** The extractor only recognised
node types in `"type": "..."` JSON form. The Code node — the one n8n's Pyodide removal
is about — appears 34 times in a markdown *reference table* (`| n8n-nodes-base.code |
Code |`) and so was absent from the inventory entirely. n8n's declared Python removal
could never have matched anything. `BARE_NODE` now catches prose and table mentions,
and `PLACEHOLDERS` drops documentation examples such as `someNode`, which alone had
contributed 32 locations.

### What replaced it

n8n publishes `packages/cli/src/modules/breaking-changes/rules/` — its own breaking
changes as machine-readable rules, each carrying the affected node types, a severity,
a title, and a `documentationUrl` on docs.n8n.io. Among them are exactly the two
changes that bit us: `removed-nodes.rule.ts` and `pyodide-removed.rule.ts` ("The
Pyodide-based Python implementation in the Code node has been removed").

`miw/probe/n8n_upstream.py` asks three questions in priority order:

1. **Is the node still in n8n's source tree?** Node types are derived from the repo's
   file paths (`.../ScheduleTrigger.node.ts` → `n8n-nodes-base.scheduleTrigger`), so
   removal is a membership test against the vendor's own source rather than an
   inference from prose.
2. **Does a rule name this exact node type?** That is n8n stating the break itself. The
   rule's severity is honoured over ours — it is their product — and its
   `documentationUrl` becomes an AUTHORITATIVE citation, since docs.n8n.io is already
   in n8n's authority set.
3. **Does a capability rule mention it without naming node types?** Python in the Code
   node is this shape. Reported as *needs a human check*, one severity step down,
   because MIW knows the course uses the node and not whether it uses the affected
   parameter. Claiming otherwise would be the overstatement this system exists to avoid.

GitHub being unreachable returns `inconclusive` with `n8n_upstream_unreachable` — never
"the node was removed". Both the node index and the rules are cached for seven days, so
the ~40 fetches are paid once rather than every run.

Three golden cases pin this: a removed node fires S9 at critical, a declared change is
cited from the vendor's own docs, and an unreachable GitHub fires nothing.

## 15. Temporal staleness (S12) — deliberately not built

Dated references that are no longer valid — a 2024 tax reckoner, an FY 2023-24
worksheet — are a real missing signal class. It is **not implemented**, and the reason
is that there is nothing in these four courses to validate it against.

Measured across all 58k content records: the only year tokens inside external URLs are
news citations (`techcrunch.com/2025/...`, `axios.com/2025/...`), which are already
correctly excluded as citations rather than dependencies, plus one instance of
`amazon.in/s?k=running+shoes+under+2000` — where "2000" is a **price**, and a naive year
regex reads it as a year. There are **zero** occurrences of "as of <year>",
"FY <year>", "effective <year>" or similar in the body text.

So the false-positive rate of the obvious implementation is already demonstrable while
the true-positive rate is unmeasurable. Building it now would add an unvalidated signal
to a system whose stated failure mode is noise. It should be built when a course that
actually cites dated statutory documents is in scope — a real example URL is enough to
start — so it can be scored against reality rather than against a guess.

## 16. Free-tier erosion (S3), rebuilt as the loss of free wording

The old S3 could only fire if one of ten hardcoded paid-sounding phrases happened to
appear on a page the probe happened to fetch. No pricing page was tracked as its own
artifact and nothing compared one week to the next, so a vendor moving Spaces to paid
for new accounts was caught only by luck — and then as an access-wall signal rather
than a pricing one.

`miw/probe/pricing.py` inverts the test. Enumerating how a vendor can announce a charge
is open-ended — "requires a Pro subscription", "new accounts need a paid plan",
"included with Team" — and every phrasing not anticipated is a miss. The *free*
vocabulary is small, stable, and advertised loudly while it is true. So the signal is
**wording that used to be present and no longer is**: `free_tier_language_lost` is the
strongest S3 evidence available, it needs no model, and it is specific enough to quote
back to a reviewer ("'free tier', 'no credit card' no longer present on
https://.../pricing").

Supporting mechanics: the working pricing URL is discovered once and remembered in
`pricing_state`, so the `/pricing`, `/plans`, `/#pricing` guessing is paid once per
vendor; the pricing page gets its own simhash separate from the docs page, so a pricing
rewrite is visible even when the free vocabulary survives; a rewrite *alone* is set to
`low` and routed to research rather than reported as a finding about money; and a first
observation records a baseline and reports nothing, because with no prior snapshot
there is no change to report.

Restricted to `critical` dependencies — up to six extra fetches on first discovery, one
thereafter — since a tool a student only reads about does not justify the cost.

## 17. Screenshot exposure on a changed flow

A rewritten third-party flow invalidates every capture of it. The Gen AI OAuth unit
carries **34 images**, and a finding that said only "the docs moved" hid all of that
work. Images are deliberately *not* inventoried as dependencies — a course-hosted
screenshot's own health is not the signal — instead `extract` keeps a per-unit image
census (49 units, 230 images across the four courses) and
`score.screenshots_at_risk()` attaches it to S2/S5/S8 findings only, deduplicated by
unit so a dependency referenced eight times in one unit does not multiply that unit's
count by eight. A dead package reports zero, because there is no flow to re-capture.

## 18. Other fixes from the same review

* **Cross-process LLM budget.** `MAX_SPEND_USD` was a module global, but every stage is
  a subprocess — so it read as a per-stage cap and a six-stage run could spend six
  times the configured limit. The budget now lives in `state/llm_budget.json`, keyed by
  date.
* **Diff direction.** Any fingerprint change was classified `worsened`, so a tool going
  from broken back to merely redirected was reported as a deterioration. Now
  `worsened` / `improved` / `changed` / `unchanged`, and the digest has an "Improved but
  still open" section.
* **`jobs.db` durability.** WAL, a busy timeout, and — the real bug — reads no longer
  share the worker's write connection from API request threads, which a sqlite3
  connection does not support.
* **`merge_location` was O(n²).** A linear `in list` scan over 845 locations for
  LangChain alone. Set-backed now; `extract` runs in **2.8s**.
* **The eval's precision suite no longer gates CI.** It reads live triage state and so
  is not reproducible; it is now opt-in behind `--with-precision`.
* **Scope-mismatch note.** The probe artifact is selected by date, not by scope, so
  `analyse` now says out loud how many in-scope dependencies have no fresh data this
  cycle rather than quietly scoring stale entries.
* **The OpenRouter path is now exercised.** It had never executed; a mocked test pins
  its response parsing, its failure handling, and `temperature=0` — the last of which
  matters because a provider comparison is meaningless without it.

**One operational caveat worth knowing:** the n8n checks make 39 GitHub API calls per
refresh, and unauthenticated GitHub allows 60 per hour. The seven-day cache is what
makes this safe; set `GITHUB_TOKEN` (5,000/hour) before lowering the TTL. Measured cold
refresh: 406s for 692 node types and 36 rules, 0 errors.
