# PRD — Market Intelligence & Curriculum Gap Analyser (MIW)

Owner: gen-ai-content · Repo: `/home/nxtwave/MIW` · Branch: `feat/agent-workflow-and-ui`
Current state as of 11 Sep 2026 · Build record from 7 Sep 2026

This document has two parts. **Part I** is what the system is now: the use case it
serves, how it is built, and what it actually produces, with every number measured off
the artifacts on disk rather than asserted. **Part II** (§1–§34) is the chronological
record of how it got there — the original plan, and every place reality differed from
it. Part I is the one to read first, and the one to hand to someone new; Part II is
where to look when you want to know *why* a rule is the way it is, because almost every
rule in Part I exists because something specific went wrong.

---

# Part I — The system as built

## A. The use case

**Who it is for.** The NxtWave curriculum team, who own four Gen-AI courses carrying
9,998 references to things they do not control. Three of the four have a workbook and
contribute 71 indexed sessions; PSE does not, and the consequences of that are stated in
§C.1 and §G rather than glossed.

**The problem, stated once.** Courses teach through external dependencies — tools,
hosted services, SDKs, model ids, docs pages, n8n nodes. Those dependencies change
constantly. The curriculum does not. The founding incident: the Gen AI course taught
`codetotutorial` as the way to read a GitHub repo, the tool died, DeepWiki already
existed as a replacement, and nobody knew until students hit a dead link. Every part of
that failure is structural rather than unlucky — detection was reactive, knowledge of
what a session depends on was tribal, and nothing could answer "which sessions break if
X dies?" without reading every session by hand.

**The two questions.** They sound similar and they are different work, so the system,
the digest and the UI all split on them:

| | Question | Signals | What it means for the reader |
|---|---|---|---|
| **Fixes** | Is what we teach still *true*? | S1–S9 | Published material is now wrong. A student is hitting it today. |
| **Changes** | Is what we teach still *complete*? | S10–S11 | Nothing is broken. A decision for the next curriculum cycle. |

The second question is the one the architecture makes hard, and §32 is about why: the
dependency inventory is extracted *from the course content*, so every stage that reads
it is confined to what is already taught. Its answer to "is our prompting session
complete?" is structurally always yes. That is why `gaps` exists as a stage that reads
neither the inventory nor the probe.

**The eleven signals.** Taken from `miw/analyse/score.py`; the last column is what is
open on today's artifact, so a signal with no producer is visible as one rather than
implied to work.

| | Signal | Means | Default severity | Open now |
|---|---|---|---|---|
| **S1** | Dead / moved URL | regression → *Fix* | critical | 3 |
| **S2** | Login- or paywall added | regression → *Fix* | high | — |
| **S3** | Free tier cut or now paid | regression → *Fix* | high | — |
| **S4** | Deprecated / abandoned | regression → *Fix* | high | — |
| **S5** | Implementation or UX change | regression → *Fix* | medium | 3 |
| **S6** | Package version drift | regression → *Fix* | medium | 6 |
| **S7** | Model deprecated or superseded | regression → *Fix* | high | 5 |
| **S8** | Docs rewritten | regression → *Fix* | low | 3 |
| **S9** | n8n node / version update | regression → *Fix* | medium | 14 |
| **S10** | Better alternative available | opportunity → *Change* | low | 2 |
| **S11** | Curriculum topic gap | opportunity → *Change* | low | 5 |

Four have no open finding today, for three different reasons, and the difference
matters. **S4** is wired and has fired (one open on 9 Sep); nothing is deprecated this
cycle. **S2** and **S3** depend on a vendor changing its access or pricing *wording*,
which none has this cycle — these are the quietest signals by design, because a pricing
page that merely lists prices is not a finding. **S10** is the rarest signal and fires
only from the rotation path; §G explains why, and why its count moves so slowly.

**The scope boundary, which is not negotiable.** MIW **never writes to course content or
the CMS**. It has no production access and is not asking for any. It identifies changes
and proposes fixes; a human applies them. Every output is a finding with evidence
attached, pointed at a named session.

## B. What it produces

For each finding, five things, and none of them optional:

1. **What changed**, in the vendor's own words — a quoted sentence, never a paraphrase.
2. **Where it is**, down to the course, the session number and the exact cell or field
   the text lives in, with the surrounding excerpt resolved so a reviewer can read it
   without opening the workbook.
3. **What to do** — one sentence naming the session and the edit.
4. **Why it matters** — the consequence, scoped to what the curriculum actually *does*
   with the thing (a model id executed in a coding question is a live outage; the same
   id named in a paragraph is not).
5. **When**, as prose and as a sortable `due_by` date derived from severity by one
   mapping, so the two cannot disagree.

Measured on the current artifact — 39 open findings:

```
by kind      regression 34 · opportunity 5
by signal    S1 3 · S5 3 · S6 6 · S7 5 · S8 3 · S9 14 · S11 5
by severity  critical 8 · high 17 · medium 10 · low 3 · info 1
evidence     17 carry an authoritative citation · 22 rest on our own probe observation
notes        39/39 carry what_to_act, why_to_act, when_to_act and due_by
```

### B.1 The outcome, measured against the founding failure

The founding incident was: a taught tool died, a replacement already existed, and nobody
knew until a student hit the dead link. The honest test of this system is whether that
exact shape is now caught before a student sees it. It is — three times over, on the
current artifact:

```
CRITICAL  Composio      https://mcp.composio.dev/dashboard returns 404/410
          Building LLM Applications + Intro to Gen AI, sessions 22, 24, 25, 26
          6 linked places · candidate replacements: Nango, Qveris (both verified
          against their own official domains)

CRITICAL  Stability AI  https://api.stability.ai/v2beta/stable-image/... returns 404/410
          Intro to Gen AI, sessions 2, 13, 15, 17 · 6 linked places
          candidates: DigitalOcean, MindStudio
```

Dead tool, named sessions, a count of affected places, and verified alternatives — the
codetotutorial/DeepWiki shape, caught by a scheduled run instead of a support ticket.
Every element the original incident lacked is present, and every claim carries the URL
it came from and the date it was fetched.

**And the third one is a false positive**, which belongs here rather than in a footnote.
The Ngrok finding fires on `https://abc123.ngrok.io` — a *placeholder* subdomain from a
tutorial, not a link any student is meant to click. It is correctly reported as dead
because it is dead; it is wrong as a curriculum finding because the URL was never real.
That is a live precision bug in link extraction, not a rounding error, and one
false CRITICAL in three is a rate that will cost the digest its authority if it is not
fixed. It is listed in §G.

Three delivery surfaces, one source of truth (`out/findings_<date>.json`):

* **`out/digest_<date>.md`** plus one per course under `out/courses/<slug>/` — the
  weekly read, split under `## Fixes` and `## Changes`.
* **The local UI** (`python3 main.py serve`) — one page per course, Fixes and Changes as
  separate lists with their own counts, a detail panel showing every occurrence grouped
  by session, and inline accept/reject that feeds the next run.
* **`registry/tools.yaml`** — the queryable dependency inventory that did not exist
  before, which answers "which sessions break if X dies?" directly.

## C. Architecture

### C.1 Two inputs per course, and which half is load-bearing

```
  Course JSON export  ──►  what was PUBLISHED: units, questions, links, code
  Course workbook     ──►  what was AUTHORED: the per-session deck outline,
  (.xlsx)                  Key Takeaways, tool pins, and the authoritative
                           session numbering
```

The export is downstream of the workbook, and the workbook is the only bridge back. Two
consequences the system depends on:

* **Session numbering comes from the workbook**, not from counting units. Inferring it
  positionally counted `Common Mistakes` as a session and mis-numbered 81 of 104 units
  in one course — and the integrity check passed, because its expected value had been
  calibrated to the defect (§31). An integrity check's expected value must come from a
  different source than the value it checks.
* **The deck outlines exist nowhere else.** All 135 slide-outline records, covering 68
  distinct `Session PPT` decks, come from the workbook's `Course Outline` sheet; **zero**
  come from the JSON export. They are the only description of what a session covers, so
  the entire gap-analysis half of the product is impossible without the workbook. PSE has
  no workbook and therefore can never receive a gap finding — a limit of the input, not
  of the code.

### C.2 The pipeline

```
 ┌──────────── INSIDE-OUT: is what we teach still true? ─────────────┐
 │                                                                   │
 │  ingest ──► extract ──► probe ──► research ──► analyse ──┐        │
 │    │           │          │          │           │       │        │
 │  course     inventory   HTTP,      official     score,   │        │
 │  JSON +     460 deps    registry,  pages,       diff,    │        │
 │  workbook   9,998 refs  vendor     Tavily       triage   │        │
 │                         catalogues                       │        │
 └──────────────────────────────────────────────────────────┼────────┘
                                                            ▼
                                              out/findings_<date>.json ──► report
                                                            ▲
 ┌──────────── OUTSIDE-IN: is what we teach still complete? ┼────────┐
 │                                                          │        │
 │  workbook outlines ──► curriculum index (71 sessions) ──► gaps    │
 │  registry/topics.yaml ─► official docs read as ──────────┘        │
 │  (10 areas, 20 sources)  enumerations, corroborated               │
 └───────────────────────────────────────────────────────────────────┘
```

Seven stages, each a CLI subcommand and each independently runnable:

| Stage | Reads | Writes | Scoped by |
|---|---|---|---|
| `ingest` | course JSON + workbooks | `out/content_records.jsonl` | never — rebuilds everything |
| `extract` | content records | `out/inventory.json`, `registry/tools.yaml` | never — same reason |
| `probe` | inventory | `out/probe_<date>.json` | course, session, tier, kind, dep-id |
| `research` | probe + inventory | `out/research_<date>.json` | same |
| `analyse` | probe + research | `out/findings_<date>.json` | same |
| `gaps` | workbooks + `registry/topics.yaml` | `out/gaps_<date>.json` + findings | course only |
| `report` | findings | `out/digest_<date>.md` + per course | course only |

`ingest` and `extract` are deliberately outside the scope system: they rebuild the whole
inventory, and scoping them would silently shrink it and break every other course's
findings. `report` and `gaps` accept only `--course`, because the other flags describe
*dependencies* and those two stages are scoped in what they **write**, not what they
read — sending `report` a flag it did not declare is what once made every UI-initiated
run die at its last stage with exit 2 (§23).

### C.3 The trust layer, which is the spine

Ground truth is enforced **structurally, not by prompt**. There is exactly one way to
create an assertion the system will act on:

```python
Claim.build(kind=…, statement=…, source_url=…, quote=…, subject=…)
```

It fetches nothing and believes nothing. It classifies the URL against the subject's
declared authority set and refuses on an empty statement, a missing source, or a quote
too short to verify. There is deliberately **no way to construct a Claim from model
recall**. Four tiers:

| Tier | What it is | What it may settle |
|---|---|---|
| `AUTHORITATIVE` | the vendor's own domain, or a canonical registry within its remit | anything |
| `CORROBORATING` | independent but reputable (arXiv, MDN, major press) | non-strict kinds only |
| `LEAD_ONLY` | directories, forums, any unrecognised host | may *nominate*, never prove |
| `EXCLUDED` | known-bad | nothing |

Five claim kinds are **strict** — `existence`, `deprecation`, `pricing`, `version`,
`implementation` — and may rest on nothing below `AUTHORITATIVE`, because they are the
ones that make us change published curriculum. Two rules fall out and are load-bearing
everywhere:

* **News may only nominate; the vendor's own pages must confirm.** Search discovery runs
  unfiltered so a tool nobody has heard of can enter the picture, and every candidate is
  then re-verified against its own official domains.
* **A model may emit only a name, or a URL.** It never emits a fact. Our code fetches,
  and `Claim.build` gates. A model's fit judgement is recorded as `opinion`, is never a
  Claim, never enters the evidence block, and is never fed back into a later prompt.

`python3 main.py verify` re-checks all of this **from outside**, against the artifacts on
disk, re-classifying every citation rather than trusting the tier the artifact records —
so a tightened trust rule retroactively invalidates old findings instead of leaving them
standing. It is the check that catches the failures no test anticipates; it has caught
three in the last week.

### C.4 Two extractors, because vendors say things two ways

* **Prose quoting** (`research/official.py`) — pulls the sentence that states a fact.
* **Column-role table reading** (`probe/catalogue.py`) — vendors publish retirements as
  *table rows*, and reading those as text is wrong in a specific way: on Groq's
  deprecations page a single model id appears eight times, once in `Deprecated Model` and
  seven times in `Recommended Replacement`. So the module never looks at a character
  offset — it segments `<table>`→`<tr>`→`<td>`, binds each column to a role from its
  header, and reads a status only from the row the id occupies. If no table can be
  role-typed the answer is `supported=False`: an explicit "this vendor publishes no such
  thing here", never a fallback to text search.
* **Enumeration reading** (`probe/frontier.py`, added for `gaps`) — the same discipline
  in a third shape: furniture removed by *element* (`<nav>`, `<footer>`, ARIA roles),
  items bound to headings, code and sub-headings stripped from quotes, and
  `supported=False` when nothing binds.

That last rule is the one that keeps *we could not parse it* from becoming *it is gone*.

### C.5 How `gaps` avoids manufacturing work

Read on their own, four vendor documentation pages produced **32 findings**, most of them
API mechanics rather than teachable topics — "Batch embeddings", "Migration from
gemini-embedding-001". Two rules, both added after measuring the alternative, cut that to
5:

* **Corroboration.** A topic must be named by two sources whose `official_domains` are
  **disjoint**, enforced structurally so two pages from the same vendor cannot
  corroborate each other. What distinguishes an industry topic from one vendor's
  implementation detail is that a competitor documents it too.
* **Taught anywhere is not a gap**, checked against the workbook's own wording and across
  every vendor's name for the topic — Google writes "Zero-shot vs few-shot prompts" where
  the workbook writes "Prompting Techniques (Zero-shot, One-shot, Few-shot, CoT)".

Placement is IDF-weighted term overlap over the 71-session corpus, weighted by how much
a session is *about* the area — not an embedding, because the score has to be explainable
in the finding ("matched on: prompt, chain-of-thought, thought") and a reviewer settling
a placement in five seconds needs to see why. Below a floor it declines to name a session
and lists the candidates instead; a confidently wrong session number is worse than an
honest shrug.

### C.6 The LLM, and why testing needs no API key

The model is an **enhancement to deterministic prose, never a source of fact**. Every
digest renders complete with no key and no model. When one is available it may rewrite
the what/why/when triad, handed only facts already verified and forbidden from adding
any — a rewrite that introduces a URL not already in the evidence is rejected whole.

Provider order is `claude_code` → OpenRouter → none, chosen per run from the sidebar.
The CLI runs in print mode with 16 tools denied, a daily call cap and a spend cap.
`eval/parity.py` proves every provider receives identical prompt bytes, so "is the output
the same with an API key" is answerable by measurement rather than assertion.

### C.7 Agents, and where they are allowed to be

Two LangGraph graphs (`miw/agents/`): a **signal agent** (plan → read → replan → done)
that hunts for a vendor's own announcement across candidate pages, and an **impact
agent** (classify → artifacts → score → review gate) that uses a real `interrupt()` so a
human decision is part of the graph rather than bolted beside it.

**LangGraph orchestrates; it never calls the model.** Every node goes through
`miw/llm.py`, so provider choice, the denied-tool list, the daily caps and prompt parity
all still apply. The graphs are opt-in (`main.py agent`) and sit *beside* the pipeline —
the weekly run does not depend on them, because a deterministic stage that always works
is worth more to this team than an agentic one that usually does.

### C.8 The UI

One self-contained HTML file, served by FastAPI, making **no external request of any
kind** — the font is an embedded subsetted woff2, not a font host — so it works on a
laptop with no network, which is often exactly when someone is reading last week's
digest. A background job runner executes stages as subprocesses, streams their logs, and
records what each run found so a run page shows findings rather than only a log.

The page's labels are deliberately **not** the names in the code. `watch_tier`,
`blast_radius`, `diff_class` and `S7` are precise, and they are also words nobody outside
this repo has heard, so every one renders through a single `WORDS` map while the API, the
CLI flags and every saved scope keep the internal names unchanged (§34). A label is safe
to edit; a wire value is not.

## D. What it deliberately does not do

* **No writes to course content or the CMS.** No production access, none requested.
* **No claim without a source.** A finding that cannot be evidenced is dropped and the
  refusal is recorded, because "we looked and could not cite it" is a result and silence
  is indistinguishable from never having looked.
* **No temporal staleness signal (S12).** "This page has not changed in 18 months" is as
  often a sign of stability as of abandonment; it was designed and deliberately not built
  (§15).
* **No bare version bumps as news.** 91 taught packages release constantly. What makes a
  release curriculum-relevant is that it moved past what the course *pins*. State is
  recorded either way; only news is reported.
* **No self-marking.** Half of all triage decisions are held out of learning and used
  only to score precision.

## E. Measured state

```
Courses               4 declared · 3 with workbooks · 71 sessions indexed
Inventory             460 dependencies · 9,998 references
  by kind             tool 203 · package 91 · service 83 · model 42 · n8n node 41
  authority           257 can be spoken for officially; 203 cannot, and are listed
  watch tier          critical 194 · standard 58 · mention-only 208
Findings              39 open · 34 fixes · 5 changes
Gap analysis          10 areas · 20 official sources · 65/71 sessions inside an area
                      95 items enumerated → 12 corroborated → 7 already taught → 5 raised
Code                  ~15,000 lines of Python · 2,800-line single-file UI
Gates                 546 tests · eval 4 suites at 100% (trust 13, extraction 10,
                      findings 22, discovery 17) · `main.py verify` clean
Cost of a run         probe / analyse / report / gaps: zero. research spends Tavily
                      searches (~73 for a scoped Intro to Gen AI run). Note refinement
                      is opt-in, capped at 120 calls and $2.00/day.
```

## F. Running it

```bash
pip install -r requirements.txt            # core: needs no API key at all
python3 main.py ingest && python3 main.py extract
python3 main.py probe --course "Intro to Gen AI"
python3 main.py research && python3 main.py analyse
python3 main.py gaps --dry-run             # see what it would raise; write nothing
python3 main.py gaps && python3 main.py report
python3 main.py verify                     # audit the trust invariants
python3 main.py serve                      # the UI, http://127.0.0.1:8000
python3 main.py run-weekly                 # all seven stages, unattended
```

### B.2 Coverage now reads the curriculum, not a summary of it

The gap check answers "do we already teach this?" It used to answer it from the
workbook's four summary fields — **39,144 characters across all 71 sessions** — while
**9,226,771 characters** of course content sat on disk, already parsed, already carrying a
session number on every record. It was deciding from **0.4% of the evidence**.

`analyse/curriculum.py` now joins each session's own prose from
`out/content_records.jsonl`: reading material, question text, worked explanations. Prose
only — `solution_code` and bare URLs are excluded, because `import langchain` says the
session *uses* a library (which the dependency inventory records far more precisely) and
mostly contributes identifiers that collide with topic names.

Two rules keep this honest, and the second was only discovered by measuring:

* **Coverage reads everything; placement still ranks on the summary.** `place()`
  normalises by the query's weight, not the document's length, so a session carrying
  300KB of reading material would out-hit one with a 550-character outline on surface
  area alone — the failure that once put "Tree of Thoughts" in a session about n8n merge
  nodes. The outline says what a session is *about* (right for placement); the body is
  evidence of what it *contains* (right for coverage).
* **In the body, terms must appear together, not merely appear.** Applying the summary's
  "every distinctive word is present" test to 400KB declared *"Start with clear
  instructions"* taught in **36 sessions**, because `start`, `clear` and `instruction`
  each occur somewhere in almost any large body of teaching prose. The body is indexed as
  overlapping ~60-term windows and the terms must share one. That is what the summary rule
  always meant; it was implicit only because 550 characters is one breath.

Measured, and the number is the point:

```
coverage reads     39,144 chars  ->  7,559,206 chars   (193x)
S11 findings            5        ->        3
already taught          7        ->        9
```

**Two of the five gap findings were false positives** — *"Start with clear instructions"*
and *"Break the task down"* are both taught in Building LLM Applications session 11
(*Effective Prompting Techniques*), and the check could not see it. The three survivors
(parallel function calling, compositional function calling, JSON schema support) are
genuinely absent.

A vector database was considered for this and is not warranted: the corpus is ~7.5MB and
indexes in under five seconds, and `curriculum.py` uses IDF term overlap rather than
embeddings *because the score has to be explainable in the finding* — a reviewer settling
a placement needs "matched on: prompt, chain-of-thought", not a cosine distance.

### B.2a The decks, read at last

§31 established the deck as the authoring source — everything else is downstream of it —
and it was the least observed input in the system: the workbook records a `Session PPT`
URL per session and nothing ever opened it. A deck deleted, unshared or emptied was
invisible until someone opened it by hand. The founding codetotutorial failure, pointed
inward.

It turned out to cost almost nothing. The decks are **published to web**, so an
anonymous fetch works and no Drive API or credential is involved, and the parse was
already written: `extract_deck.py` in the sibling project reads exactly this markup, and
PRD §5 listed it as reusable prior art and then never reused it. It is vendored into
`miw/ingest/decks.py` the way `ingest/portal.py` vendored `build_course_sheet.py`.

**The load-bearing rule is the refusal.** Google answers a request for a deck you may not
open with **HTTP 200** and a sign-in shell. Measured on the workbook's own URLs: 17 are
published and yield 2,700–11,500 characters each; **51 return 200 with an identical
108-character shell**. Treating those as content would stamp the same boilerplate onto 51
sessions of the coverage index — worse than no deck text, because it would make them all
look alike. The discriminator is structural, not a keyword search for "Sign in": a
published deck embeds its slide model as `[objectId, index, title]` triples and a gated
one embeds none — 17–53 versus exactly 0. Same shape as "did a table role-type".

**Which link to use turned out to matter more than the parsing.** Two inputs carry deck
links for the same decks and they disagree: the workbook's `Session PPT` column is mostly
the editor form (51 of 68), while the course export carries the published form.
Preferring the published one wherever either source has it took readable decks from 17 to
**85 — including all 13 PSE sessions, which have no workbook at all** and therefore get
no curriculum text from any other route.

```
85 deck URLs · 85 read · 0 gone · 0 restricted · 0 unreachable
833,316 chars of slide text
  Building LLM Applications 261,217 · AI for Finance 245,896
  Intro to Gen AI           173,413 · PSE            152,790
```

`main.py decks` is its own command, not a step inside `gaps`: 85 fetches of 0.6–14MB
against a throttling host is a curriculum-revision cadence, not a weekly one. It writes
`out/decks_<date>.json`, caches the *extracted slides* rather than the HTML (14MB of
markup becomes ~8KB of text), and `gaps` reads the artifact and never fetches a deck
itself — so the weekly run stays fast and works offline. Health comes free: we had to
open the deck to read it, so a 404 or a lost share is observed on the way past.

Coverage after decks: **8,232,739 characters** — 39,144 of deck summary, 7,520,131 of
course content, 673,464 of slide text. The three surviving gap findings survive this too;
checked directly against the slide corpus, "parallel function" and "compositional
function" appear nowhere in 833,316 characters of slides.

Two things were tried here and reverted, both recorded because the measurement is the
useful part. Measuring term distinctiveness over the **full** corpus instead of the
summaries looked more principled and was worse: across 8.2M characters almost every
ordinary word appears in more than half the sessions, so topics were left with no
distinctive terms and `teaches` — which returns False on an empty list — stopped
recognising coverage it had been getting right. And the same run exposed a latent
duplicate: when one heading corroborates two on the other page, the cluster found from
its side has three members and the ones found from the others' have two, so member-set
keying let the same topic ship four times under one name. Deduping by name, widest
cluster winning, closes it.

### B.3 The auditor was rebuilding the wrong subject

Surfaced by the first S10 findings reaching an artifact. A finding carries claims about
its candidate **replacements** so the digest can cite them, and those are about a
different subject: the S10 on Murf.AI carries a pricing claim from `vozo.ai`, which is
authoritative about **Vozo** and LEAD_ONLY about Murf.AI. `cmd_verify` re-classified every
claim against the *dependency*, so it reported a violation that was not one.

`Claim.subject_name` has recorded the true subject since the beginning, so the fix reads
data that was already there. This is the same mistake `with_provider` exists to prevent
one level up — the auditor reconstructing authority differently from the code it audits —
and it had been silently wrong for every S10; there simply had not been one until now.

## G. What is not covered yet, stated plainly

* **A newly released model or tool does not become a Change.** A new model is not a
  topic, so `gaps` will not see it; it is not a replacement for something broken, so S10
  will not either. The mechanism exists — `probe/catalogue.py` already reads vendor model
  tables as enumerations, which is the same shape `probe/frontier.py` reads documentation
  headings in — so this is the `gaps` stage pointed at a model catalogue. Not built.
* **S10 is the rarest signal, and its two paths are easy to confuse.** When a dependency
  is already broken (`discovery_reason: breakage`) its verified replacements attach to the
  existing S1 or S4 — "this is dead, here is a replacement" is one finding, not two — so
  those never surface as S10. A standalone S10 comes only from the **rotation** path,
  discovery on a *healthy* dependency, which is deliberately a small slice per run
  (`ROTATION_SLICE`, `DISCOVERY_SLICE`). Both routes are live on the current artifact:
  three dependencies carry `breakage` alternatives, two carry `rotation` ones, and those
  two (Lovable, Murf.AI) are the S10 findings on file. I twice reported that S10 had never
  fired; that was wrong, and the artifact says so.
* **n8n is a deliberate hole in gap analysis.** Nine Intro to Gen AI sessions build n8n
  workflows, `docs.n8n.io` yields zero headings to a plain fetch, and no *independent*
  vendor documents n8n's node set, so no area can corroborate it. n8n drift is caught
  instead by `probe/n8n_upstream.py` reading n8n's own declared breaking changes — a
  regression check, not a gap check.
* **PSE gets no gap findings**, having no workbook.
* **`inventory._links` attributes by domain only**, which overstates the n8n S4's blast
  radius. Known, reported, not fixed.
* **Placeholder URLs are extracted as real links.** `https://abc123.ngrok.io` is an
  example subdomain in a tutorial; it produces a live CRITICAL finding. One false
  CRITICAL in three is the single highest-value precision fix outstanding — a digest
  that cries wolf once is read with suspicion afterwards. The fix is a structural one in
  `extract/links.py` (an example-host pattern is not a dependency reference), not a
  blocklist.
* **The 68 `Session PPT` decks are never checked** — 0 dependencies carry a
  `docs.google.com` URL, so a dead deck link is invisible.
* **Two items from the approved UI plan are unbuilt**: the run-progress stage stepper
  (`stage_now` is returned and unused) and the queue-depth guard (`queue.Queue()` is
  unbounded and `queued_behind` only ever reports 0 or 1).

---

# Part II — The build record (§1–§34)

What follows is chronological and unedited: §1–§12 are the original plan, written when
the target directory was empty, and §13–§34 are what actually happened — including every
place the plan was wrong. Where Part I states a rule, Part II is where the incident that
produced it is written down.

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

**Phase 1 course scope (decided):** Intro to Gen AI (25 sessions — see §31; the
figure was 26 until the workbook's own numbering was read), Building LLM
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

~~Sheets are a parallel input, not a second source of truth~~ — **corrected in §31.**
The workbooks are the bridge back to the *authoring* source: the slide deck is what
MCQs, coding questions and reading materials are written from, and the export is the
published artifact downstream of it. The workbook is therefore the **authority** for
session numbering (the export's positional inference was wrong for 19 of 26 sessions in
Intro to Gen AI) and the **only** source for slide content. It contributes four things:
the `Tools` columns, hand-recorded version pins, the `Course Outline` sheet's per-session
outline and deck link, and the `Session-Practice Content Linked` sheet's
`Session ID -> Unit ID` lineage — joined to the JSON on **`unit_id`, exactly**
(104 of 104 rows), not on normalised title.

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

### [4] Research — targeted, Tavily (no LLM: see §21)

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
| M1 | **Ingest** — schema adapter + traversal reuse | `content_records.jsonl` for all 4 courses; session counts match **25/29/17/13** (the curriculum's own counts, per §31 — this milestone read 26/29/18/13 while `expect_sessions` was calibrated to the export's positional count); pooled exams traversed; skip reasons reported |
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
themselves. `miw/ingest/sheets.py` recovers **1,740 pin declarations across 3
workbooks**, giving **86** dependencies a `taught_version` where the JSON alone yielded
only n8n `typeVersion`s. (This paragraph read "50 pins / 42 dependencies" when first
written, before the tool sheets were read in full.)
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

---

## 19. Phase 2 · P0 + P1 — release-triggered model verification

Built, and it found four live problems the sweep could not see.

### What the curriculum is teaching right now

| Model | Provider says | Locations | Graded items | Vendor's replacement |
|---|---|---|---|---|
| `llama-3.3-70b-versatile` | Groq: deprecated, shutdown **08/16/26** | 18 | 15 (5 execute it) | `openai/gpt-oss-120b`, `qwen/qwen3.6-27b` |
| `gemini-2.0-flash` | Google: **(Shut down)** | 16 | 16 | — |
| `llama-3.1-8b-instant` | Groq: deprecated, shutdown **08/16/26** | 4 | — | `openai/gpt-oss-20b` |
| `gemini-3-pro-preview` | Google: **(Shut down)** | 1 | — | — |

All four are **critical**: the announced dates have passed. Each carries an
AUTHORITATIVE citation to the provider's own page with the verbatim table row as the
quote, and where the vendor named a successor it is attached as the alternative — after
being checked back against the same catalogue to confirm the provider still lists it.
No API key was used at any point.

### P0 — provider authority

`llama-3.3-70b-versatile` was attributed to **Meta** (the extractor's `MODEL_VENDORS`
matches the `llama` prefix), but **Groq** serves it and Groq retired it. So Groq's
deprecation page classified as `LEAD_ONLY` against a Meta subject, the claim was built
non-substantiating, and the finding was dropped — for a model in 15 graded items.

`trust.with_provider` + `Dependency.subject_with_provider` fold the serving provider's
domains into the authority set. This is the one deliberate loosening of the trust layer
in the project, so it is fenced three ways: **models only**; only for a provider whose
**own catalogue names the exact id** (self-verifying — if Groq lists it, Groq serves
it); and asserted in `main.py verify`, which now refuses an S7 finding that has no
authoritative claim behind it.

### P1 — column-role table reading

`miw/probe/catalogue.py` never looks at a character offset in page text. It segments
`<table>` → `<tr>` → `<td>` and binds each column to a role read from its header.

**Why that is load-bearing, measured on Groq's page:** `llama-3.3-70b-versatile`
appears **eight times** — once in the `Deprecated Model` column and **seven times** in
`Recommended Replacement Model ID`, because it was the successor to seven older models.
"Is this id on the deprecations page?" therefore fires eight times and is wrong seven,
and before August it would have fired purely as a replacement while the model was
perfectly healthy. Column-role binding is the only thing that separates the one true
row from the seven false ones.

Two vendor-shape details cost real debugging and are worth recording:

* **Google states the status in the *name* cell** ("Gemini 2.0 Flash (Shut down)") while
  the exact id sits in a `<code>` span in that row's Endpoint cell. Both columns map to
  the `id` role, so `Table.identifier_cell` prefers the coded one for matching and
  `status_in_row` reads the parenthetical from either.
* **Groq's models table writes display name and id in one cell** —
  "GPT OSS 120B openai/gpt-oss-120b" — with no code span. An id-shaped token rule
  (lowercase, carrying a hyphen or a namespace slash) lifts the id out; requiring both
  properties is what stops "Gemini 2.0 Flash" or "Shut down" being read as an id.

### A correction to the plan

The plan's golden case said `gemini-2.0-flash` must **not** be reported as shut down,
on the basis that the status belonged to Flash-Lite's row. Reading the table
structurally shows that was a misreading of the proximity evidence: **each row carries
its own `(Shut down)`**, and `gemini-2.0-flash` genuinely is retired. The real
must-not-fire case is the substring collision — `gemini-2.0-flash` must not implicate
`-lite`, `-001` or `-exp`, all of which are separate inventory entries — and that is
what the suite now pins.

### Two-run confirmation, correctly scoped

The flap-protection rule that requires two agreeing runs before reporting `broken`
exists to stop a transient network failure reading as a dead tool. It does not apply to
a vendor's own published declaration: re-reading Groq's table tomorrow adds no
information, and delaying a critical finding by a day buys no safety. Absence
observations ("not in the source tree") still need confirming; a dated row in a
vendor's table is a document, not an observation.

### Three defects the first end-to-end run exposed

* A model retired by its provider was **also** reported as S1 "dead URL", because the
  generic broken-status fallback fired alongside S7 — 8 findings where there were 4.
  The fallback now only fires when nothing more specific did.
* The note read "**may** be retired" for a model whose shutdown date had already passed.
  Definite where the vendor was definite.
* All 15 graded items were counted as "may need rewording". A model id inside a coding
  question is **passed to an API at run time**, so a retired id fails at call time —
  `Dependency._executes` now treats it as execution, splitting the 15 into 5 that break
  and 10 that merely name it.

### State

`miw/probe/catalogue.py`, `miw/probe/models.py`, `miw/vendors/{base,groq,google_ai}.py`,
`Scope.dep_ids` + `--dep-id`, plus `trust.with_provider`. **88 tests** (19 new),
**42 golden cases** across three suites, all at 100%. Vendor pages cached 12 hours.

---

## 20. The two entry points: one manual, one daily

The requirement: *"The workflow needs to work in both scenarios. One while running
manually and the other to check the news updates daily. How can we check the curriculum
across different courses, because sending all the course data will make the context grow
and grow and make the agent hallucinate."*

The two modes are the same six stages entered at different points, not two pipelines.

```
MANUAL                                  DAILY
main.py <stage> --course X --session 6   main.py watch [--investigate]
Run tab in the UI                        (cron, unattended)
        │                                       │
        │                              poll_all() — watermarks only
        │                                       │
        │                              Signal(vendor, trigger, refs=[ids])
        │                                       │
        │                              resolve_signal() — dict lookup
        │                                       ▼
        └──────────────► Scope ◄────────────────┘
                           │
              probe → analyse → report (scoped)
                           │
                  merge_by_dep: this slice refreshed,
                  every other vendor carried forward
```

### Why the context never grows

This was the stated worry, so it is worth stating as a measurement rather than a claim:

| | |
|---|---|
| course text across four exports | **9.2 M chars** (~2.3 M tokens) |
| the one LLM prompt the pipeline sends | **3,510 chars** (~877 tokens) |
| ratio | **2,618 : 1** |
| course body text inside that prompt | **0 chars** |
| LLM calls during signal resolution | **0** |

The reason is structural, not a budget or a truncation rule. A signal names
**identifiers** (`llama-3.3-70b-versatile`), and the inventory is an index **keyed by
identifiers** — 464 dependencies, each already carrying its 10,612 locations with
course, session, unit and content_id. So "which of our sessions does this touch" is a
dict lookup:

```
signal ref  llama-3.3-70b-versatile
  → index lookup            O(1), 18 locations
  → courses                 ['Building LLM Applications', 'Intro to Gen AI']
  → sessions                [6, 8, 10, 11, 12]
  → 15 graded items, 5 of which execute the id
```

Course content is read exactly once, at ingest, to build that index. Nothing downstream
re-reads it — which is also why cross-course checking costs nothing extra: a fifth course
adds rows to the index, not tokens to a prompt.

### The watermark, and why a first sighting reports nothing

`miw/watch/signal.py` records one watermark per source. For a package it is the
published version; for a vendor catalogue it is `row_set_hash` — a hash of the table's
*meaning* (sorted `(id, status, replacement)` triples), so a redesign, a reordering or
new marketing prose around the table is not an event, while a status cell changing is.

Three outcomes, and only one of them is news:

* **baseline** — first observation of a source. Recorded, reported as a baseline, never
  investigated. Without this, first deploy would emit every historical deprecation as
  though it had just happened.
* **unchanged** — silent.
* **changed** — one signal, carrying only the rows behind the move.

A signal's id is derived from `(vendor, source, trigger, from, to)`, so re-observing the
same state is an idempotent upsert and the ledger reports it as new exactly once.

### Notify on findings, never on releases

A vendor event is recorded silently and only reaches a person once it has resolved
against the inventory *and* produced a finding. Two rules keep that honest:

* A signal naming ids we do not teach resolves to an **empty scope** and is marked
  investigated without running a stage. This is what keeps a busy vendor quiet.
* A **bare registry bump raises no finding.** 48 of the 71 taught packages pin no
  version at all, so under daily polling every patch release of those would have raised
  an S6 that no reviewer could act on — the session never named a version, so nothing in
  it went stale. A release is curriculum news only when it leaves the taught pin behind
  (`major_behind_taught_pin`), which is the `langchain 1.3.1` vs `1.4.0` case. The new
  version is still written to the artifact: **state is recorded, only news is reported.**

### Verified progression

```
poll 1   baseline groq:catalogue 50 rows · baseline google_ai:catalogue 42 rows
         → "no vendor moved since the last poll"
poll 2   0 changed · 0 baseline · 2 unchanged        → silent
poll 3   1 changed  (watermark rewound to simulate a vendor edit)
         → resolved to 4 of 464 taught dependencies:
           llama-3.1-8b-instant, llama-3.3-70b-versatile,
           whisper-large-v3, whisper-large-v3-turbo
  --investigate
         → probe: 4 probed {broken: 2, ok: 2}
         → merged: 4 refreshed, 38 carried forward   (Google's findings untouched)
         → 2 findings raised {critical: 2} → out/digest_2026-09-09.md
```

The `38 carried forward` is the artifact-merge guarantee under a scoped run: a Groq
signal refreshes Groq's slice and leaves every other vendor's findings exactly as they
were. The periodic full sweep stays as the safety net, because **silent death emits no
release event** — codetotutorial never announced itself, it just stopped answering.

---

## 21. Suggestion quality, and why it is now the whole product

The team confirmed the boundary: **MIW never writes to course content or the CMS.**

> "Currently we are not fixing anything here, because we dont have the access to Prod to
> fix from here. I will check that feasibility and add later. For now lets focus on
> identifying the chnages and suggesting fixes."

That raises the stakes rather than lowering them. With no write-back, the suggestion *is*
the deliverable, so a finding that is technically correct but wrong about urgency, blast
radius or cause is not a near-miss — it is the whole output being wrong. Triaging the
watcher's first four real findings proved the point: **all four were true positives, and
three were described wrongly.**

### What was wrong

Every one came out `critical`, every one read *"A retired model id fails at call time, so
every example in the session stops working"*, and every one said *"This sprint"*:

| Finding | Locations | Executes? | Reality |
|---|---|---|---|
| `llama-3.3-70b-versatile` | 18 (5 coding, 10 MCQ, 2 reading, 1 sheet) | **5** | correctly described |
| `gemini-2.0-flash` | 16, **all MCQs** | 0 | nothing fails at call time |
| `llama-3.1-8b-instant` | 4 (3 reading, 1 sheet) | 0 | reading material, not an outage |
| `gemini-3-pro-preview` | **1** reading resource | 0 | `critical` indefensible |

### Severity now follows what the curriculum *does* with the id

`score.py` hard-set `critical` for `model_shutdown_passed`. It now reads the fields the
finding already carried: **executes → `critical`; graded but not executed → `high`;
named in prose only → one step below the base.** `Dependency._executes` already knew the
difference — a model id inside a coding question is passed to an API at run time.

This repaired urgency for free, because `WHEN_BY_SEVERITY` derives from severity. The
"This sprint" prefix was also being stamped on every dated finding regardless; now the
urgency follows severity while still naming the earliest affected session, which is the
genuinely useful half.

A fourth defect surfaced while fixing these: `miw/probe/models.py` was **fabricating a
vendor severity** (`"critical" if date_passed else "high"`) which `score.py` then
honoured as though the vendor had declared it, overriding the ladder. For n8n that field
really is vendor-declared; for a model provider it was our own inference wearing the
vendor's authority. Removed — the date already reaches scoring as the choice between
`model_shutdown_passed` and `model_deprecation_declared`.

### The reconcile defect: a vendor can say two true things at once

`build_catalogue()` folded all of a vendor's pages into one dict keyed by identifier and
resolved collisions with "a retired row wins", discarding the second sighting. So MIW
read one of Groq's pages and ignored the other:

* `console.groq.com/docs/deprecations` — `llama-3.3-70b-versatile | 08/16/26 | openai/gpt-oss-120b`
* `console.groq.com/docs/models` — **still lists that exact id**, as `Llama 3.3 70B Enterprise`, price and rate limits `Contact Sales`

Both official. The id did not disappear; it **left the developer plan**. A reviewer who
opens the models page sees the id listed and concludes the digest is wrong.

The discriminator is structural, not textual, and it needed a new column role: every
other model in that table carries a real per-token price. `ROLE_LEXICON` gained `price`
and `rate_limit` — verified purely additive, since every header on both vendors' pages
previously resolved to `None` for those. `CatalogueEntry` now keeps the availability
sighting alongside the retirement row, and the pair yields a distinct outcome:
`model_tier_restricted`.

**The conjunction is what decides.** `minimaxai/minimax-m2.7` is also listed at
`Contact Sales` and appears in no deprecation table — that is a pricing tier, not a
retirement, and raises nothing. Only *retired **and** still listed* is tier-restriction.
This is the same distinction `Catalogue` already draws between `ok` and `supported`
("collapsing them is how a monitoring system starts inventing outages"), one level down.

The finding now cites **both halves of the vendor's own contradiction**, each
AUTHORITATIVE with its verbatim row — including the one a reviewer would otherwise have
used to disprove us.

### The four findings, after

```
CRITICAL  llama-3.3-70b-versatile  "still served, but no longer on the provider's
                                    developer plan, so the 5 graded items that run it
                                    fail on a student's free key"   · this sprint
HIGH      gemini-2.0-flash         "16 graded questions ask students about a model id
                                    its provider no longer serves, so the answer keyed
                                    as correct is now wrong"        · within two weeks
MEDIUM    llama-3.1-8b-instant     prose only                       · next cycle
MEDIUM    gemini-3-pro-preview     one reading resource             · next cycle
```

Which is exactly what the reviewer concluded independently at triage.

---

## 22. One workflow, either provider

> "the agentic workflow should be designed to work as it is with API key. So make sure
> the prompts and tools are attached how the current mode using cluade code is working."

### Why this was impossible until now

`requirements.txt` ended with `; extra == "search"` / `; extra == "openrouter"` markers.
Those are pyproject metadata; in a plain requirements file they evaluate **False**, so
`pip install -r requirements.txt` **silently skipped `openai` and `tavily-python`**.
After a clean install the API path could not even import — which is why *"does the output
come similar with the API key approach?"* stayed unanswered. Core and optional
dependencies are now separate files, so "works with no key" holds by construction rather
than by an inert marker.

### Parity is achieved by omission, not translation

The whole LLM surface is one 279-line module with **one** semantic caller
(`notes.refine`), **one** prompt file, and **zero tool schemas**. Isolation is
*subtractive*: the CLI has 16 ambient tools that must be denied; the HTTP APIs grant
none. So the API paths honour "tools attached the same way" by sending **no** `tools`,
`tool_choice`, `system` or `response_format` — and `DENY_TOOLS` becomes an invariant to
**assert** rather than a flag to translate.

What is checkable, and now asserted in `tests/test_provider_parity.py`:

* `_claude_code_argv()` is extracted and pure, so the isolation flags are inspectable
  with no `claude` binary and no spend — including that `--allowedTools` is still never
  used, and that no `--mcp-config` / `--permission-mode` / `--append-system-prompt`
  widens the surface.
* every `ProviderSpec.grants_tools` is `False`, and the denylist is a **superset** of the
  16 known-dangerous tools (so a newly shipped CLI tool passes, a removed deny fails).
* one rendered prompt reaches all three transports **byte-identical**.
* no provider adds a system prompt.

**The one asymmetry, stated rather than papered over:** the CLI still injects its own
reduced system prompt and runs an agent loop up to `--max-turns`; the HTTP paths are a
single turn with nothing in reach. The API paths are therefore *strictly more* isolated,
not identically isolated. `LLMResult.isolation` records which mechanism held
(`denylist:16` vs `no-tools`), because "no tool attempts because the denylist worked" and
"no tool attempts because nothing was on offer" are different facts.

### Other fixes on the same surface

* **A typo used to change backend.** `available_provider()` fell through a ternary, so
  any unrecognised `MIW_LLM_PROVIDER` landed on OpenRouter. It now returns `none` loudly.
* **`auto` prefers the CLI even when `ANTHROPIC_API_KEY` is set.** Testing runs on a
  Claude Code entitlement precisely to avoid metered spend; a key exported for an
  unrelated tool must not silently start charging. Spending is opt-in.
* **One logical model name** (`haiku`/`sonnet`/`opus`) resolved per provider across three
  namespaces, with unknown names passed through so an exact snapshot can be pinned.
  `haiku` resolves to the literal `haiku` for the CLI, so **the 15 cached entries keep
  their keys** — pinned by a test.
* **Cost is real on the API paths.** A per-model price table plus a three-valued
  `cost_basis` (`reported`/`estimated`/`unpriced`). Without it `MAX_SPEND_USD` silently
  degraded into a call cap, which is what OpenRouter had been doing.
* **Two capability channels disagreed about the same feature.**
  `settings.LLM_ENABLED = False` was hardcoded and read by nothing, while
  `capability_note()` claimed "no LLM stage in Phase 1" — and `--refine` existed, worked,
  and reported its provider through a different channel entirely. One source of truth
  now: `llm.provider_status()`.

### Answering the question honestly

`eval/parity.py` is opt-in and kept out of `run_eval.py`, whose contract is "no network,
no LLM, no API key". Its default `--dry-run` renders every prompt, asserts byte-identity
and replays the existing cache for free; a live run needs `--yes` and respects the
budget caps.

It prints **within-provider variance first**, because `temperature=0` is not determinism:
if a provider disagrees with itself as much as it disagrees with another, the comparison
is noise. It also reports that only **2 of 9** current golden prompts carry a
substantiating claim, so the `<untrusted>` block — the part most likely to make providers
diverge — is barely exercised, and says so rather than quoting a flattering number.

**What it can prove:** identical prompt bytes; comparable gate accept/reject rates; that
no provider invents sources more often; measured cost per call.
**What it cannot:** that the prose is *better*. There is no reference triad and no human
in the loop. That answer comes from `Finding.note_provider` accumulating in
`review_decisions` — refined notes are now attributed in the digest and in the UI chip —
and breaking reviewer precision out by provider. Until that has a sample, the honest
statement is that the workflow is provider-portable, not that the providers are equal.

---

## 23. One page per course, and runs that stay put

> "UI also a bit querky - Make sure each course has its own page and each run should be
> mixed with other." — confirmed to mean runs should **not** be mixed.

### Why findings are projected, not partitioned

Per-course artifacts are the obvious reading and the wrong answer. A finding's identity
is `(dependency, signal)` — `finding_id = _id(dep_id, signal)`, no course in it — and
**133 of 464 dependencies are referenced by more than one course** (per-course counts
255 + 184 + 155 + 59 = 653 against 464 actual).

Partitioning would mean probing the same vendor page up to four times against a 1.5 s
per-domain courtesy gap; letting `probe_state.consecutive_failures` — global by design
for flap protection — disagree with itself, so one 502 counts four times toward the
two-run confirmation threshold; and re-keying `finding_state`, `review_decisions` and
every triage fingerprint, which would ask a reviewer to reject the same event up to four
times and split `triage.split_of` so one event lands in `learn` for one course and
hold-out for another, contaminating the very precision measurement it feeds.

**So: evidence stays global, findings are projected onto a course, run history becomes
per-course.** `state/miw.db` needed no schema change at all.

### The distortion, measured

| finding | global blast / graded | honest per course |
|---|---|---|
| `llama-3.3-70b-versatile` | 65 / 15 | LLM Apps 53/12 · **Intro 12/3** |
| `gemini-2.0-flash` | 64 / 16 | LLM Apps 44/11 · **Intro 20/5** |
| `llama-3.1-8b-instant` | 7 / 0 | AI for Finance 5/0 · LLM Apps 2/0 |

An Intro to Gen AI page copying the stored `blast_radius` claims **65** where the honest
figure is **12** — a 5.4× overstatement, on the page whose only job is telling that
course's owner how much work they have. That is the "quirky".

**A second trap, found while building it:** `score.py:130` stores
`locations=dep.locations[:12]`. `llama-3.3-70b-versatile` has 18, so projecting off the
*finding* shows Building LLM Applications with 9 instead of 15 — it understates the
busiest course by a third. `miw/analyse/project.py` therefore joins back to
`out/inventory.json` by `dep_id` and never reads `Finding.locations`.

**The invariant that makes it testable**, verified on every live finding:
`sum(per-course blast_radius) == global blast_radius` (65 = 53+12, 64 = 44+20, 7 = 5+2,
508 = 508), because the weights are additive over locations and locations partition
cleanly by course. One assertion catches double-counting, truncation and any attempt to
apportion a global figure.

### Severity, twice, and why

The **global** severity is the headline: it is what `finding_state` stores, what
`diff_class` was computed against and what `precision_stats()` counts, so a page-local
figure disagreeing with the database and the digest would be its own lie. But `critical`
above "three prose mentions in this course" is also a lie, so the local reading sits
beside it whenever the two differ — `critical` · *in this course: high*.

That exposed one more inconsistency worth recording: `notes.compose()` reads `severity`
to choose the urgency line, so the first version rendered *"in this course: high"* next
to *"This sprint"* — the critical wording — leaving the reader to reconcile two of our
own statements. `compose()` now sees the local severity while the stored field stays
global.

### Routing

Hash routing inside the existing single `index.html`. Multiple files would duplicate
~200 lines of CSS and the whole renderer four times, or force a shared `app.js`, trading
away the deliberate single-file / no-external-request property; server rendering would
need a template engine, contradicting no-build. Hash routing replaced the 11-line tab
handler, needs **zero** server change (the fragment is never sent), and delivers the
three properties that were missing: linkable, bookmarkable, reload-safe. Tabs are real
`<a href>` elements, so middle-click and browser-back work.

```
#/                    course overview, one row per course
#/c/<slug>/<view>      findings | runs | inventory | digest | run
#/watch                the vendor-signal timeline
#/global/<view>        runs | trust | digest
```

Slugs come from `config/constants.py`, which was their only home and never exposed them
past `cmd_ingest`. Resolution is **lenient** — `?course=pse` and `?course=PSE` both work
— so a URL can carry the slug while the existing scope-preview widget keeps sending
titles unchanged, and `Scope.courses` stays a set of titles because that is what
`Location.course` holds.

### Runs

A `job_courses(run_id, course_slug)` side table, not a `course` column: `scope.courses`
is a list and a run may legitimately span courses, so a scalar column could only
represent that by lying. It lives inside the `SCHEMA` string `jobs.py` already
`executescript`s, so there is **no migration script**; a `_backfill_job_courses()`
beside `recover_interrupted()` gave the four existing runs their rows (all
`intro_to_gen_ai`).

`course_slug = '*'` means unscoped, which is the honest semantic: an all-courses sweep
really did audit PSE and belongs in PSE's history, while a run scoped to one course must
not appear elsewhere. For a **watch-triggered** run — where `Scope.dep_ids` wins outright
and names no courses at all — the ids are resolved through the inventory; if the
inventory cannot be read the answer is `'*'`, because "may be relevant to any course"
hides nothing whereas "relevant to none" would drop the run out of every history.

Watch runs get **both** views, as asked: `#/watch` keeps the vendor-signal timeline (a
signal is about a vendor, not a course) and each affected course page shows the run in
its own history.

### Digests: per course, plus the roll-up

```
out/digest_<date>.md                    # roll-up, meaning unchanged
out/courses/<slug>/digest_<date>.md     # new
```

The **subdirectory is load-bearing**. `_latest()` is `sorted(OUT.glob(...))[-1]`,
lexicographic — a top-level `digest_pse_2026-09-09.md` sorts *after*
`digest_2026-09-09.md` (`p` > `2`) and would silently become "the" digest for everyone.
`OUT.glob("digest_*.md")` does not descend, so the hijack is structurally impossible,
and a test asserts it.

`report` joined `SCOPED`, with the qualification that matters most here: **scoping
`report` changes which per-course digest it _writes_, never which findings it _reads_.**
It always loads the whole merged `findings_<date>.json` and always re-renders the
roll-up, so the roll-up cannot go stale behind a scoped run and no slice is ever
presented as the week's state. Anything else reinvents the bug that put 91 Gen-AI-only
probe results in place of a sweep's 229 — the bug `miw/artifacts.py` exists because of.
A test asserts the ordering in `cmd_report` so the guarantee cannot be quietly dropped.

**`probe_<date>.json` and `findings_<date>.json` stay per-date and global, untouched.**
They are *state of the world* keyed on `dep_id`; `merge_by_dep`, `coverage`,
`carried_forward` and `resolve_absent(examined_dep_ids=)` all keep working unchanged.
Mixing and blending are different problems at different layers, and conflating them is
how this gets built wrong.

### What each page admits about itself

* **"3 of 18 references are in this course; the numbers below are this course's share.
  Also taught in Building LLM Applications (15 refs, blast 53)"** — with a link. Without
  it a course page silos, and 133 of 464 dependencies are shared.
* **A triage warning at the point of the click**: a decision is recorded against a
  `finding_id`, which has no course in it, so accepting here also applies to the other
  courses that share the finding. Named, not footnoted.
* **`precision (all courses)`** in the header, because reviewer precision has no course
  dimension. `/api/summary` returns a `global_only` list so the UI can label rather than
  imply a filter.
* **A course declared in `config/constants.py` but absent from the inventory** renders
  "has not been ingested yet — this is not the same as no problems found", never an
  innocuous empty page.
* **`probe_counts` is now recounted from the rows** rather than read from
  `probe["counts"]`, a meta dict written by whichever run touched the file last. It was
  reporting a 4-dependency run's counts over a 42-row artifact; the global figure was
  `{'broken': 4}` where the truth is `{'ok': 38, 'broken': 4}`.
* **`suppressed_unchanged` is recounted per course**, because a page listing 3 standing
  findings that claimed 5 were suppressed was a contradiction the reader had to resolve.

---

## 24. The discovery layer, phase D0–D1: the founding story, finally working

The team looked at the finished system and said *"the architecture is not looks like an
agentic or multi agent system. Let me know if I'm wrong."* They were not wrong: one LLM
call site in 12,434 lines, zero findings whose text came from a model, and every finding
a regression. **MIW solved detection and never solved discovery** — a probe can tell you
`codetotutorial` returns 404; it cannot tell you `deepwiki` is the replacement.

### S10 could not fire, and no agent would have fixed that

Four defects, all keyless, all independent of any model. Each was verified by
construction rather than read off the source.

**D-1 · The successor sentence never reached the scanner.** Vendors write the
replacement in a *separate* sentence that does not repeat the subject — *"X is
deprecated. Migrate to Y."* — and `_relevant` keeps only sentences naming the subject.
Worse, `_is_prose("Please migrate to DeepWiki.")` is **False**: 2 of its 4 tokens are
capitalised, over the 0.45 cap. So the commonest form of the notice was discarded twice
over. The lead scanner now reads the neighbouring sentence from the **raw** split, with
`_MACHINE` and the length floor still applied.

This is the one place in the codebase where proximity is used, and it is deliberate.
Elsewhere nearby text is banned as evidence, because a status sitting near an identifier
says nothing about it. Here the output is a **nomination**: the quote carries both
sentences so a reviewer sees what produced it, and the name must still survive
verification before anything is claimed about the tool itself. *A lead may be proximate;
a fact may not.*

**D-2 · The cue was case-sensitive**, so every sentence-initial form missed —
`Superseded by…`, `Migrate to…`. Naive `re.I` is worse: the name group then swallows
lowercase words (`"migrate to DeepWiki now"` captured `"DeepWiki now"`). Fixed with
scoped inline flags — case-insensitive cue, case-sensitive name — plus the missing
`we recommend` / `we suggest` cues, and trailing punctuation stripped (`[\w.+-]` admits
dots for `Node.js`, so it also swallowed the full stop).

**D-3 · The alternative carried no citation.** `official.py` built
`Alternative(claims=[])`, so `.verified` was False and `score.findings_for` filtered it
out. The one producer that needs no API key was silently discarding every result it
found — even though the quote naming the successor is already on the **old** vendor's own
authoritative page. Measured after the fix:

```
claim tier      AUTHORITATIVE          (the old vendor's own domain)
substantiates   True                   (ALTERNATIVE is not in STRICT_KINDS)
alt.verified    True                   <- was False
blog-sourced:   LEAD_ONLY, False       <- the guard still holds
```

The statement is *"CodeToTutorial's own documentation names DeepWiki as the successor"* —
a fact about the old vendor's **recommendation**, which its page is authoritative for.
Not *"DeepWiki does the job"*, which that page cannot establish and which stays unmade.

**D-4 · `is_substantiated` silently ate every S10.** This is the one that mattered most,
and it would have made all the other fixes worthless. `Finding.is_substantiated` reads
`probe_signals` and `self.claims`; an S10 has no probe signals, and its evidence lives on
`alternatives[*].claims`. So even with the `else: ensure("S10")` branch executing and a
genuinely verified alternative, `findings_for` returned `[]`:

```
alt.verified: True
findings with probe=None: []      <- dropped at `if not f.is_substantiated`
```

The S10 now lifts its alternatives' substantiating claims onto `f.claims`. They are
already properly built with the right subject and tier, so this needs no new trust
machinery, and it simultaneously fixes `evidence_urls`, the digest's evidence block, the
UI's claim filter and `cmd_verify`.

### A regression the citation fix introduced, and caught

Attaching a citation made an existing false positive *reportable*: *"the legacy endpoint
is retired, please use HTTPS for all requests"* nominated **`HTTPS`** — and it now
arrived AUTHORITATIVE, looking verified. A `GENERIC_TOKENS` stop-list (protocols,
formats, languages, platform nouns) plus a self-reference guard closes it. Must-not-fire
cases now pin all of it: a generic protocol, a tool nominated as its own replacement, and
a migration notice two sentences away all yield nothing.

### The result, with no API key

```
#### HIGH · S1 Dead / moved URL — CodeToTutorial
- What to do: … Candidate replacement: DeepWiki (homepage not yet verified)
- Alternative: DeepWiki — named as the successor by the vendor itself
```

The replacement rides on the S1, which is the right presentation for a dead tool — S10 is
reserved for *"still works, no longer best"*, which needs the opportunity rotation (D6).
Note the honest `homepage not yet verified`: a vendor-named successor is a **name** until
it has been checked against its own domain, and rendering `DeepWiki ()` advertised a
missing field.

### Consistency fixes shipped alongside (D1)

* The digest rendered **every** alternative while the refinement prompt filtered on
  `.verified` — two surfaces disagreeing about what "verified" means. Both filter now.
* `research` was **unchecked by default in the UI**, so the stage that produces citations
  and replacements never ran outside `run-weekly`.
* `ResearchResult.dropped` conflated rejected evidence with unreadable pages — 169 of 203
  entries in one real run were speculative well-known-path 404s, which made the genuine
  rejections invisible. Split into `dropped` (evidence we refused), `unreadable` (pages
  we could not read) and `refuted` (nominations actively disproven, for D3). The stage
  now says so: `rejected= 1 unreadable=13` rather than `dropped=14`.
* `ClaimKind.ALTERNATIVE`'s own comment claimed *"a replacement exists **and does the
  taught job**"* — overstating what any citation can carry. Corrected.

### What the 2026 literature contributed

The pattern MIW already implements has a name — **verifier-first grounded citation
retrieval** — and MIW enforces it more strictly than the pattern requires. The useful
part was the measured failure data: **3–13% of URLs cited by deep-research agents are
fabricated**, citation accuracy runs **40–80%**, hallucination **11–57%**, and larger
models hallucinate *more* confidently at synthesis. All of it is answered by one rule,
which is now the spine of the design: **the model may emit only a name and a candidate
domain; our code fetches it, and a claim exists only if we read the page ourselves.**
A fabricated URL cannot survive DNS; a fabricated tool cannot survive a probe of its own
domain.

Deliberately not adopted: fan-out, debate, swarm and supervisor orchestration. The
bottleneck is that nothing proposes candidates at all, not reasoning throughput — and a
swarm of unverified nominators multiplies exactly the failure modes above.

---

## 25. D2: the registry triage, and the treadmill behind it

Before building a domain-finding agent, the population it would work on was measured.
**227 dependencies could not be spoken for officially.** The honest read was that most of
that number was not an agent problem.

### What it actually was

| slice | n | fix |
|---|---|---|
| PyPI distributions mis-typed as `tool` | **20** | registry data, verified against PyPI |
| duplicate spellings of an already-resolved entry | **6** | one alias line each |
| ordinary English words matched from prose | ~15 | `PROSE_STOP` |
| library class names with no vendor page | ~9 | `PROSE_STOP` (they are classes *inside* langgraph/langchain/trl) |

`Email` contributed 358 locations, `http-request` 336, `Fetch` 237, `webhook` 234 — the
same class of false positive as "Python" once contributing 1206, just with lower-profile
names. The sheet declaration survives; only the prose matching stops.

The 6 alias merges were found by normalising: `HuggingFace` ↔ `Hugging Face`,
`Scraper API` ↔ `Scraperapi`, `TwelveData` ↔ `Twelve Data`, `Serp API` ↔ `SerpAPI`,
`Prompt Base` ↔ `Promptbase`, `suno-api` ↔ `Sunoapi`. In each case one spelling already
carried the domain and the other carried nothing.

### The result

```
cannot be spoken for   227 -> 203
  of which critical     10 -> 0        <- every critical dependency is now speakable-for
packages                 71 -> 91
review_status                          20 approved (the first non-derived entries)
```

The 203 that remain are **entirely `mention-only` tier**, which the default scope
(`critical,standard`) excludes from probe and research anyway. That is the honest state:
the gap is no longer blocking anything.

### Four findings that were structurally invisible

Retyping is not bookkeeping. A `tool` with no domain has no authority set, so it can
never produce a version finding. Once these became `package`, the registry became their
authority via `Dependency._REGISTRY_HOME`, and the probe immediately found:

```
sentence-transformers  taught 5.2.0    latest 6.0.1     HIGH   S6  blast 39
protobuf               taught 6.33.6   latest 7.36.1    HIGH   S6  blast 29
sentry-sdk             taught 1.27.0   latest 2.69.1    MEDIUM S6  blast  3
pyngrok                taught 7.5.0    latest 8.1.2     MEDIUM S6  blast  2
```

And the noise rule held: `pydantic` (2.11.10 → 2.13.5) and `tiktoken` (0.8.0 → 0.14.0)
produced **nothing**, because neither crosses the taught major.

### `resolve-packages`, because hand-fixing ten was a treadmill

Ten entries were corrected by hand. Re-extracting surfaced **ten more** — `Flask`,
`pydantic`, `tiktoken`, `sentry-sdk`, `pyngrok`, `tokenizers`, `crewai-tools`,
`lm-eval`, `murf`, `pygbag` — because every new sheet declaration arrives domainless
forever. That is the recurring-coverage argument, and it does not need an agent either.

`python3 main.py resolve-packages` retypes a sheet-declared name **only if a registry
actually serves a project under it**, then records `review_status: approved` with the
reason. Evidence-based, not a guess: a wrong `registry_id` would hand a dependency an
authority set it had not earned, so all 20 were confirmed present on PyPI before being
retyped.

It defaults to names carrying a **version pin**, which is the strong signal — nobody
writes `pydantic@2.11.10` about a SaaS product, whereas `Telegram` is sheet-declared and
is not a distribution. `--include-unpinned` widens it at the cost of a registry round
trip per name. Names that resolve nowhere are listed by name rather than silently
skipped, because that remainder is the real backlog.

### One planned item deliberately dropped

The plan proposed harvesting the vendor URLs that `miw/ingest/sheets.py:44,81` discards
from tool cells. Measured: 10 such URLs, of which **9 point at domains already known**,
and the 3 unknown ones are an AI-tool aggregator (which the exclusion list should reject
anyway), an arXiv citation, and one real product. Not worth a code change. Recorded here
so the option is not re-proposed as though it were untried.

### The research artifact now merges

`probe` and `analyse` have gone through `artifacts.merge_by_dep` ever since a scoped run
overwrote the day's probe file with its own slice. `research` was still a plain `dump()`,
so a course-scoped research run erased every other course's citations and nominations for
the day — the same bug, one stage later. Verified after the fix: two sequential
single-dependency runs leave both results in the artifact, the second reporting
`merged: 1 refreshed, 1 carried forward`.

### A phantom-course bug this exposed

Re-extracting surfaced something that had been wrong for a while: `WORKBOOK_COURSES`
in `cmd_ingest` was keyed on `gen_ai_contents.xlsx`, while the workbooks on disk are
named `Intro to Generative AI - Course Contents.xlsx`. **The keys never matched.** And
because `feed_sheets` fell back to `workbook_courses.get(t.workbook, t.workbook)`, every
unmapped workbook became its own course:

```
before:  'Building LLM Applications - Course Contents.xlsx'  1113 locations
         'AI for Finance - Course Contents.xlsx'             1069
         'Intro to Generative AI - Course Contents.xlsx'      333
after:   0 phantom courses
```

3,633 sheet locations — every hand-recorded version pin among them — were attributed to
three courses that do not exist, which corrupted every per-course figure the new UI
computes. Two fixes: the mapping now matches a **normalised stem**, so a rename does not
silently re-break it; and `feed_sheets` **skips** an unmapped workbook with a loud
`sheet PROBLEM` rather than inventing a course, because skipping is recoverable and a
phantom course is not. A test asserts the live artifact contains no course outside
`config/constants.py`.

---

## 26. D3–D8: the nomination ladder

The nominate/verify split was already in the schema — `Alternative`'s docstring reads
*"Nominated anywhere, verified officially"*, with `nominated_by` for the LEAD_ONLY source
and tri-state judgement fields. It had no producer. This is that producer, and the
adjudicator that decides what may be said about its output.

### The rule everything follows from

**A model may emit a name and at most a bare domain. Never a fact, never a quote, never
a URL it claims to have read.** Our code fetches the domain; a `Claim` exists only if we
read the page ourselves. `miw/research/nominate.py` is the ladder, cheapest rung first,
each terminal:

```
R0  policy      excluded host · the vendor's own domain · a generic token
R1  DNS         a fabricated domain dies here, before any HTTP
R2  no domain   resolve by registry, or report a name-only nomination.
                NEVER guess https://<name>.com
R3  liveness    404/410 refutes. 401/403/429 does NOT. A redirect is a correction.
R4  evidence    `official.gather` on the candidate's OWN domain, AUTHORITATIVE only
```

R3's distinction is the highest-value line in the file, and it cuts both ways:
reporting our own blocked request as a dead tool is the fastest way to lose a reviewer's
trust, and reporting a hallucinated tool as real is the fastest way to lose it
permanently. `net.Fetch` already separated `gone` from `blocked`; the ladder inherits it.

R2's refusal to guess is the subtle one. A guessed `https://<name>.com` that happens to
return 200 is **invented evidence** — precisely how a hallucinated tool acquires a
citation. A name-only nomination is reportable with nothing claimed about it.

### `net.url_safety`, split out of a boolean

`is_safe_public_url` returned `False` for both "this host is not in DNS" and "this host
resolves somewhere we refuse to go", and `fetch` collapsed both into
`unsafe_or_unresolvable_url`. Those are different facts, and discovery needs the first:
a candidate that does not resolve is a **refuted** nomination, which is reportable,
whereas a private address is a policy refusal on our side. Now
`url_safety() -> ok | bad_scheme | unresolvable | private`, with the boolean as a
one-line wrapper so no caller changed.

### The body cache, filling a seam declared and never used

`Fetch.from_cache` had existed since the beginning and was never assigned, and the only
other cache was a dict local to one call of `official.gather` — so the same vendor page
was refetched once per claim kind, per dependency, each time paying the 1.5 s per-host
courtesy gap. Discovery walks several candidate domains per dependency, which turns that
from wasteful into slow. Measured:

```
first  fetch   687 ms   from_cache=False
second fetch     0 ms   from_cache=True    (identical bytes)
```

Scoped to the process on purpose, and **answers only**: a transport error or a 5xx is
not an answer about the world, and caching one would make a blip look like a settled
fact for the rest of the run — the same conflation `reachable`/`ok` exists to prevent.

### Refutations are reported, never dropped

`ResearchResult` gained `nominations` (every candidate with the rung it reached) and
`refuted`. The digest carries a per-run tally:

> _Discovery: 7 replacement candidate(s) considered · 2 verified · 3 refuted · 1
> unverifiable (host alive but would not serve us — not treated as absent) · 1 rejected
> on policy._

**A refutation rate that falls to zero is a suspicious signal, not a good one** — it
means the verifier stopped running. Without this the digest cannot tell "we looked and
found nothing" from "nothing looked", which is exactly the 2026 complaint about
citation-support metrics: they measure what survived and never what was rejected.

`dropped` was also split three ways, because it was doing three jobs: `dropped`
(evidence we refused), `unreadable` (pages we could not read — 169 of 203 entries in one
real run), and `refuted` (nominations disproven).

### The opportunity rotation, and why S10 could not fire

`NEEDS_ALTERNATIVES` fires only on breakage, but S10 means *"still works, no longer
best"* — the case you want **before** the 404. `alternatives_reason()` now records
`breakage` | `rotation` | `""` explicitly rather than letting scoring infer it, which
also closes a latent bug: `_load_probes` and `_load_research` fall back to the newest
file independently, so a dependency with last week's alternatives and this week's clean
probe would have produced MIW's first S10 as a **stale-artifact accident**.

Selection is a sub-slice of the existing rotation, not a second clock — a second clock
would fight the first, because `mark_researched` stamps everything the stage touched.
Eligible means a curriculum decision is actually possible: a `tool` or `service` with an
authority set, still healthy. Measured against the live inventory:

```
critical                          194
  tool/service with authority      35
  and healthy                      35
DISCOVERY_SLICE=4  ->  a full cycle in ~9 weeks
```

Packages are excluded because "an alternative to `requests`" is not a curriculum
question; n8n nodes because that is an n8n-internal choice already covered by S9; models
because the same-vendor catalogue path is strictly better and already live.

### Two bugs found by running it

**The vendor's citation was lost between `gather` and the ladder.** The
`ClaimKind.ALTERNATIVE` claim lives on the `Alternative` that `gather` built, not on
`res.claims` — so looking for it there returned the successor *unverified*, and it was
filtered out again one layer further on. Exactly the same failure as D-3, one layer up.

**A redirect correction was overwritten.** `deepwiki.io redirects to deepwiki.com` is the
candidate telling us its real home, and a reviewer needs to see that the domain verified
is not the one nominated. It was being clobbered by the final verdict detail.

### `suite_discovery` — claim-level auditing inside the existing gate

A fourth suite in `eval/run_eval.py` at threshold 1.0, offline and keyless: DNS is a
set, HTTP a dict, and **a fabricated domain is tested by simply being absent from the
set**. 17 cases, over half of them must-not-fire:

* a fabricated domain is refuted at DNS **and never fetched**
* a bare name is never turned into a guessed URL, and nothing is fetched
* a model-supplied full URL is rejected, not repaired
* a dead candidate is refuted; **a 403 is not**
* a candidate whose own site says nothing checkable is refuted
* a generic protocol, an excluded source, and the vendor's own domain all raise nothing

One test-hygiene note worth recording: once a `TAVILY_API_KEY` appeared in `.env`, three
of these tests silently began reaching the live network — 8.8 s and non-deterministic.
`search.verify_on_official` and `discover_alternatives` are now stubbed in every test.
The suite's offline/keyless contract has to be enforced, not assumed.

### The research artifact merges, and its nominations round-trip

`research_<date>.json` went through `merge_by_dep`, so a course-scoped run no longer
erases the day's other courses. And `nominations` are rehydrated explicitly in
`_load_research`: `ResearchResult(**r)` would have left them as raw dicts and `analyse`
would have crashed on the system's own artifact.

---

## 27. What enabling search discovery exposed

A `TAVILY_API_KEY` arrived mid-build, so D5 came earlier than planned — and the first
live run turned **4 findings into 18, sixteen of them critical.** Every one of the new
criticals was false, and the causes were four separate holes that only open when open
search is on. This section is the interesting part of the phase, because none of them
were in the plan.

### Hole 1 · A vendor's forum was AUTHORITATIVE about that vendor

`community.n8n.io` is a subdomain of `n8n.io`, and `_host_matches` accepts subdomains —
so a thread written by any passing user classified as **AUTHORITATIVE** about every n8n
node. The evidence behind those criticals included other users' questions and pasted
JSON workflow dumps.

**A vendor hosting a forum is not the vendor speaking on it.** `trust.is_user_generated`
now demotes forum and Q&A URLs — by host prefix (`community.`, `forum.`, `discuss.`,
`answers.`) and by path (`/t/`, `/questions/`, `/threads/`) — to **LEAD_ONLY**, checked
*before* the authority test, because the whole problem is that they pass it. Demoted
rather than excluded: a forum thread is a fine pointer to something worth checking on the
real docs. It simply cannot settle anything.

### Hole 2 · The search engine's snippet was being quoted as our evidence

This was the big one. `verify_on_official` ran a domain-restricted Tavily search and
built a `Claim` straight from `Hit.snippet` — **no `_is_prose` check, no subject-term
check, and AUTHORITATIVE because the search was domain-restricted.** A 600-character
snippet of n8n's generic "Deprecated nodes" index page, nav chrome included
(``n8n Docs ⌘Ctrl k ForumChangelog…``), became a critical deprecation finding against
seven nodes that are not deprecated.

It also violated the project's own principle, which is stated everywhere else and was
quietly broken here: **search points, it does not testify.** A hit now yields only a
URL; `official.gather_url` fetches it and lifts prose under exactly the same rules as any
other page, so a claim still rests only on text we read ourselves. Asserted against the
source: nothing may build a `Claim` out of `Hit.snippet`.

The honest consequence is a large drop in yield. Across ten dependencies the stage went
from 22 "substantiated claims" to **0**, while pages actually read went *up* (6–9 per
dependency, from 3–4). Those 22 were an artifact of quoting snippets.

### Hole 3 · A page title passed every prose test

The one alternative a live run verified rested on
`Pricing | Zite - The AI builder that means business` — 0.40 caps ratio, ten words, a
lowercase bigram, so it satisfied `_is_prose`, and it matched the PRICING keyword using
the word "Pricing" **from its own title**. It says nothing about pricing.
`_TITLEISH` now rejects pipe- and bullet-delimited fragments, and a short fragment with
no terminal punctuation is treated as a heading. Long text is exempt, because a genuine
sentence truncated by the quote cap has no full stop either.

`_MACHINE` was also evaded by **smart quotes**: a forum post pasting a workflow renders
`"id":` as `“id”:`, which walked past the straight-quote patterns and got quoted as
deprecation evidence.

### Hole 4 · `verify` was checking the artifact against itself

`cmd_verify` exists so the guarantees are *"checkable after the fact, by someone who did
not write the code"*. It was reading the tier each claim **records**, not re-deriving it
from the URL. So it reported "all trust invariants hold" over fourteen findings whose
evidence was forum posts — and, worse, a tightened trust rule silently left every old
finding standing with its stale stamp.

It now re-classifies every claim's `source_url` and reports a disagreement explicitly:

> `chainLlm / S4: claim records AUTHORITATIVE but https://community.n8n.io/t/… classifies
> as LEAD_ONLY today — the finding predates a trust rule change and must be re-analysed`

That immediately caught all fourteen. It also caught **a bug in itself**: rebuilding the
subject without the serving-provider widening made Groq's own deprecation table read as
LEAD_ONLY against a model attributed to Meta — the exact bug `with_provider` exists to
prevent, reintroduced by the auditor rather than the analyser. `verify` now rebuilds
provider authority from the probe artifact.

### What this says about the discovery layer

The mechanism is sound and the verifier does its job: on live data, **6 nominations
produced 1 verified, 4 refuted and 1 unverifiable-blocked** — and after the page-title
fix, 0 verified. A fabricated domain dies at DNS, a page title is not evidence, and a
403 is never read as absence.

But the yield is currently **zero**, and that is a recall problem, not a correctness one —
the documented ceiling of the verifier-first pattern. Two causes, both worth stating
plainly rather than dressing up:

* **Open-web nomination quality is poor.** Tavily suggested `Youtube` as an alternative
  to Lovable, and `Crewai` for SerpAPI. They were refuted, but on "no checkable
  evidence" grounds rather than "irrelevant" — the verifier caught them for the wrong
  reason, which is luck.
* **Vendor pricing pages rarely carry liftable prose** matching our keywords. The
  candidates were real products with real sites; none of them said anything about
  pricing in a sentence we would accept.

So the layer is safe to run and does not yet produce opportunities. The `refuted` list is
what makes that visible instead of looking like silence — and a refutation rate that
falls to zero would be the suspicious signal, not a good one.

---

## 28. The alternatives researcher, and the recall wall it hit

The discovery layer had a ladder, an audit trail and two deterministic nominators — and
no model in it. The agent the whole phase was named after was still missing.

### The model's entire output surface

`prompts/nominate_alternatives_v1.txt` asks for a **name and, at most, a bare domain**.
Nothing else it writes is read as fact: no URL, no price, no version, no quote. It is
opt-in (`research --nominate`), runs last, and is strictly additive — a parse failure,
a refusal, or no provider at all leaves the deterministic nominators working exactly as
they did. `parse_nominations` rejects rather than repairs: `https://bolt.new` in the
domain field is dropped, not stripped, because choosing what we fetch is the one thing
the model may never do.

One flaw the tests caught immediately: the cap was applied to the **input rows**, so
three malformed entries could starve a good fourth. A sloppy reply lost its own best
candidate. The cap is now on accepted nominations.

### It works, and the improvement is not subtle

Same two dependencies, same search results, nominators side by side:

| taught tool | open search proposed | the model proposed |
|---|---|---|
| Lovable (AI web-app builder) | `Youtube`, `Zite` | **Bubble**, **WeWeb** |
| Murf.AI (AI voice) | `Audeus`, `Speaktor` | **ElevenLabs**, **Google Cloud TTS**, **Amazon Polly** |

`candidate_domains()` ranks by frequency across hits, which is why `Youtube` was offered
as a replacement for a web-app builder — it appears in every result set. Reading the
taught job alongside the same snippets is a judgement, and it is the only one asked.
Three LLM calls, $0.128.

### Then the bottleneck moved, and the diagnosis was exact

Nomination quality was fixed and **every candidate still refuted** —
`refuted_no_evidence` on ElevenLabs, Bubble, Amazon Polly. Real products with real
pricing pages. So the verifier, not the nominator, was now wrong.

`elevenlabs.io/pricing` returns 200 with 6,865 characters and **21 sentences matching
the pricing keywords**, including:

> Monthly price and included credits per plan: Free $0 (10,000 credits); Starter $6
> (30,000 credits); Creator $22 (121,000 credits…

**Zero survived** `_relevant`, because it also required the sentence to name the
subject — and a vendor's own pricing page does not repeat its own name in every
sentence. Why would it?

The subject-term check is right for a **multi-subject** page: a changelog lists every
release, a deprecations index lists every retired node, and there a sentence must name
its subject or you attribute one product's retirement to another. That check is exactly
what stopped the seven false n8n criticals. It is wrong for a single-product page
reached at the subject's own well-known path, where the page *is* the subject by
construction. So it is now scoped to `MULTI_SUBJECT_KINDS` — DEPRECATION, VERSION,
IMPLEMENTATION — and relaxed for PRICING and AVAILABILITY.

**And the first version of that fix was wrong, in a way worth recording.** Scoping the
relaxation by *claim kind* — "PRICING pages are about the site's own product" — held for
`elevenlabs.io` and broke immediately for platforms. Within one run it attributed
*"5,000 free search requests per month (shared across all **Gemini 3.x** models)"* to
`gemini-2.0-flash`, and HuggingFace's per-TB **storage** pricing to a Meta model served
by Groq. Both cited authoritatively, both nonsense.

The discriminator is not the claim kind; it is whether **the subject IS the site**. A
name matching the domain stem owns everything on that domain (`ElevenLabs` ↔
`elevenlabs.io`); a product hosted on somebody's platform does not
(`gemini-2.0-flash` on `ai.google.dev`). `_site_is_the_product` makes that the test, and
it happens to cover the case that needed fixing exactly — a nominated alternative is
always verified against a domain derived from its own name.

After the fix, on the same dependency:

```
ALTERNATIVE Speaktor    verified=True  free=True
  https://speaktor.com/#pricing
  > Convert text to speech for free with no credit card and no signup wall.
ALTERNATIVE ElevenLabs  verified=True  free=True
  https://elevenlabs.io/pricing
  > Monthly price and included credits per plan: Free $0 (10,000 credits)…
== verified: 3 ==
```

Two more evidence-quality rules came out of reading those quotes: a **page title** is
not evidence (`Pricing | Zite - The AI builder that means business` matched the pricing
keyword using the word "Pricing" from its own title), and a **question** is not an
assertion (`How do text characters and credits work?` was quoted as pricing evidence —
an FAQ heading matches topic keywords perfectly and states nothing).

### The fit judgement, and the wall around it

`miw/research/fit.py` is the only judgement in the system. The model emits bounded
per-factor estimates; **Python owns the arithmetic and the thresholds**. That split is
the one genuinely good idea in the prior Curriculum Gap Analyzer, with two of its
mistakes deliberately not copied:

* it substitutes **0.5** for a factor it could not parse, which makes a real 0.5 and a
  crash identical in the database — here an unparseable factor is `None`, `None` is
  excluded from the mean with the remaining weights **renormalised**, and too few known
  factors means `fit_score` is `None` rather than a plausible number;
* it never **clamps**, so a model returning 1.5 inflates the total unchallenged.

`does_taught_job` is treated as not optional: a score computed without it measures
everything except the question that was asked.

The output is an `AlternativeOpinion`, not a `Claim`, and it cannot become one — it has
no `source_url` and no `quote`, so `Claim.build` could not accept it. It carries its own
provenance (`provider`, `model`, `assessed_at`, `basis_urls`) so a reviewer can check it
against the same quotes we showed the model, and it renders on its own line:

> _Model opinion (not evidence · claude_code/haiku · 2026-09-10): fit 0.98 — likely does
> the taught job — Free and frictionless access confirmed, but API and coding
> integration for practical sessions remain undocumented. Unknown: coverage_of_steps,
> maturity._

And when the model does not give enough to score, it says so rather than producing a
number:

> _Model opinion (not evidence · claude_code/haiku): not assessed — the candidate's own
> pages did not say enough._

Three routes by which an opinion could launder into a fact are closed: it never enters
`Finding.claims`; it is never passed to the note-refinement prompt (feeding a model its
own prior opinion back as input is how a hypothesis becomes a "fact" over three weekly
runs); and `cmd_verify` asserts that an opinion never travels without a substantiating
claim, is always labelled `source: llm`, and never leaks a fit score into a claim.

`Alternative.evidence` was added alongside, as a closed vocabulary
(`self` | `vendor_named` | `self+vendor`), because "its own pages say so" and "the tool
it replaces says so" are different strengths that were being rendered identically.

---

## 29. Audit: everything built, checked against what was planned

Every planned item was re-checked mechanically against the code rather than from
memory. Workstreams A, B and C (PRD §21–23) come out **22/22**. Phase 4 came out
**26/33**, and the seven gaps are worth listing individually because two of them are
not "not done yet".

### Closed after the audit

| gap | what was wrong |
|---|---|
| `_preflight_failed` was process-wide | One transient 429 silently degraded every *later* dependency in the same run to "no search", and the API's long-lived job worker never recovered at all. Now reset per run. |
| `DISCOVER_TEMPLATES[:2]` | The slice dropped `"tools like {name}"` — the phrasing that finds a functional peer rather than a comparison listicle. |
| `purpose=` never passed | The parameter existed and no caller used it, so an open search for `"Murf.AI alternative"` had nothing to distinguish a voice tool from anything else. Now narrowed by unit names: `Murf.AI` → *"Mastering Audio Generation"*, `Alpaca` → *"Trading Agent Stock"*. Still no course body text, so the stage's context cost does not move. |
| `SEVERITY_OFFSETS` not adopted | Now a **`Finding.due_by`** date derived from severity — `critical +7d`, `high +30d`, `medium +90d`. Derived, not a second guess: `WHEN_BY_SEVERITY` stays the only prose, so the digest and the field cannot disagree about the same deadline, and the execution-aware severity ladder reaches the deadline for free. |

### Deliberately not built, with the evidence

**Harvesting the vendor URLs `sheets.py` discards.** Measured: 10 such URLs, 9 pointing
at domains already known, and the three unknowns are an AI-tool aggregator (which the
exclusion list rejects anyway), an arXiv citation, and one real product. Not worth a
code change. Already recorded in §25.

**A keyless nominator from our own registry.** The plan asked for one; the data refuses
it. Three rankings were measured against the live inventory:

```
same-kind, alphabetical   -> a stock-trading API proposed for a text-to-speech tool
co-occurrence in a unit    -> ElevenLabs top for Murf.AI, Tavily top for SerpAPI
                              (right!) but GitHub / OpenAI / n8n co-occur with
                              everything, being infrastructure rather than peers
...divided by ubiquity     -> over-rewards anything in exactly one unit, so
                              AssemblyAI and Discord outranked ElevenLabs
```

No ranking works because **co-occurrence conflates substitutes with complements, and
those are opposites**: Murf.AI and ElevenLabs are alternatives, Murf.AI and Lovable are
co-taught in one project. Nothing in the inventory distinguishes them and the registry
has no capability field to lean on. Shipping it would spend fetches on irrelevant
candidates and pad the audit trail with refutations that teach nobody anything — the
failure the plan's own risk section warns about. The gap is covered where it can be
answered: the model nominator does this judgement well from the same inputs. The
measurements are recorded in `miw/research/nominate.py` where the function would have
gone, so the option is not re-proposed as untried.

### Changes made that were never planned

Twelve, all discovered by running the thing rather than by design, and all documented
above in §26–28: the forum-authority hole, snippets quoted as evidence, page titles and
questions accepted as evidence, smart-quoted JSON, the subject-check relaxation and its
wrong first version, `verify` checking the artifact against itself, `verify` losing
provider authority, the phantom `.xlsx` courses, `resolve-packages`, the research
artifact not merging, and the `url_safety` split.

That ratio — 12 unplanned fixes against 33 planned items — is the honest signal from
this phase: **turning search on is what exposed them**, and every one was a defect that
had been sitting in a path nothing exercised.

---

## 30. The UI, restyled

The team's read was *"a bit flat and old modeled rather than attractive"*, which was
fair and specific enough to act on. The screenshots said why: six identical tiles, so
"open findings 7" carried the same visual weight as "unmonitorable 61"; severity as pale
washes where `high` and `medium` were nearly indistinguishable; one type size; borders
on the tiles *and* the container that held them, which flattens everything it touches.

References taken from current practice rather than taste: Linear's density (~36–40px
rows, minimal chrome), Vercel's progressive disclosure (summary up top, detail one click
deep), and — the load-bearing one — the accessibility guidance that status must be
carried by **text, colour and shape together**, on a temperature scale, most severe
first.

### What changed

* **One lead figure, then the supporting cast.** The strip is no longer six equal tiles:
  open findings gets a 38px figure on an accent wash, the rest stay compact. The label
  reserves two lines so a wrapped one ("unchanged, suppressed") does not push its own
  number out of line with the row.
* **Severity three ways.** The word, a colour (slate → blue → amber → red), and a dot —
  square for critical, so the most urgent state differs in *shape* too. Every finding
  card gets a 3px severity rail; so does every course row on the overview, which is what
  turns "which course needs me" into a glance.
* **Dense rows.** The standing-findings list went from wrapping three-line blocks to
  single 38px rows with the summary truncated, dividers instead of a border per row.
  Stacked bordered rows double every line and read as a pile of unrelated boxes.
* **The inventory is readable again.** 200 rows rendered a **9,604px** page you had to
  scroll past to reach anything. A capped scroller reusing the sticky header that
  already existed brings it to **953px**.
* **"Nothing to report" takes two lines, not a 120px dashed box** — it is good news, and
  it now says so in the ok colour.
* Sticky header and nav with an underline active state, a real type scale, tabular
  numerals on every figure, and `prefers-reduced-motion` honoured.

### Contrast checked, not assumed

Every meaning-carrying pairing was measured. Two failed and were fixed: `--muted`
carries 12px hint text and sat at **4.14:1** on white — below AA for text that size — so
it was darkened to clear 4.5 on *both* grounds it appears against (5.34 on white, 4.73
on the sunk fill). And the dark theme's primary button had white text on a **light**
teal accent; dark ink on that fill reads at 6.46:1. Inverting a palette is not the same
as designing the second theme.

### What was not allowed to change

The offline guarantee — no font host, no CDN, no external request — which is why this
uses a system stack, as Linear, Vercel and GitHub all do. Verified live: loading twelve
routes issued **zero** requests off the origin.

And every affordance that exists because the page was misleading without it: the
projection banner ("16 of 20 references are in this course"), the cross-course triage
warning, the local-vs-global severity chip, the coverage line, and the *"not evidence"*
label on a model opinion. `tests/test_ui_contract.py` now pins all of them, plus the
offline promise, so the next restyle cannot quietly drop one.

Functionally re-verified after the change: 12 routes each showing exactly one section
with the right tab active, reload keeping its route, and the triage panel still toggling
— with no JS errors.

## 31. The PPT is the source, and the session numbers were wrong

The curriculum team corrected a premise the whole ingest layer rested on:

> "the JSON will provide all the details, but the content inside the PPT is our main
> stream because based on that only we will create mcqs, coding questions and RMs.
> That's why I provided the sheet data where you can find the outline that what we
> mentioned in each PPT."

Everything through Phase 4 treated the JSON export as primary and the workbooks as
supplementary tool lists — §7 said so explicitly, and `miw/ingest/sheets.py` read only
the tool columns. The real order of authorship is the other way round:

```
slide deck  ->  the session is taught  ->  MCQs, coding questions, RMs are written
   (source)                                  (the JSON export: the PUBLISHED artifact)
```

The export is downstream. The workbook is the only bridge back to the source, and three
of the four things it carries were being discarded.

### What the workbook carries, measured

All three workbooks share an identical `Course Outline` sheet, skipped in full because
it has no tool column, plus a `Session-Practice Content Linked` sheet.

| Column | Filled (Intro to Gen AI) | In the JSON export? | Verdict |
|---|---|---|---|
| `Outline` — the PPT outline | 24/25, avg 382 chars | **0 of 24** | unique; the only record of the decks |
| `Key Takeaways` | 24/25 | 3 of 24 | mostly unique |
| `Session PPT` — deck link | 25/25 | **0 of 25** | unique, and still unused |
| `Session No.` / `Session ID` | 25/25 | not expressible | **authoritative numbering** |
| `Reading Material Content` | 25/25, avg 8.8 KB | **24 of 25** | redundant — not read |
| `Recorded Session Transcript` | 17/25 | 17 of 17 | redundant — not read |

A method note worth keeping, because the first measurement was wrong: comparing
normalised sheet text against the *raw* JSON file made the reading-material text look
22/25 absent, because the file's escape sequences do not match normalised prose. Against
the parsed string corpus it is 24/25 present. "Is this text in the export" must be asked
of parsed strings, never of file bytes.

### The lineage the export cannot express

```
Course Outline                   Session ID -> Session No., outline, deck link
Session-Practice Content Linked  Session ID -> Unit ID + artifact type
the JSON export                  Unit ID
```

104 of 104 rows join on Intro to Gen AI, 71 of 71 on each of the others. This records
**which MCQ and coding units were authored from which session's deck** — a relation the
export has no field for.

### The bug: 81 of 104 units carried the wrong session number

`portal.is_session()` infers a session by position — a LEARNING_SET unit carrying an
INTERACTIVE_VIDEO. Intro to Gen AI has a unit of exactly that shape, `Common Mistakes`,
which is not a numbered session:

```
Intro to Gen AI   export 26 "sessions", workbook numbers 25
                  diverges at position 8; every later session reported ONE TOO HIGH
                  81 of 104 units disagreed
AI for Finance    export 18, workbook 17 (`AI Finance Add-On Session`, last -> no shift)
LLM Applications  export 29, workbook 29 — agrees exactly
PSE               no workbook; positional is all there is
```

It reached the reviewer. `gemini-2.0-flash` was reported at sessions 11/12/17/21; the
true sessions are **10/11/16/20**, and a digest reading "the earliest affected session is
session 11" pointed at the deck for session 10.

**Why no guard caught it, and the rule that follows.** `expect_sessions` was 26 and 18 —
calibrated to the export's positional count, i.e. to the defect. The check existed and
was tuned to the thing it was checking, so it could not fail. *An integrity check's
expected value must come from a different source than the value being checked.* It now
comes from the workbook, and `ingest` prints which source it used.

### What was built

* **`miw/ingest/outline.py`** — reads both PPT sheets; returns numbered sessions, the
  `unit_id -> session_no` map, and the slide text as `ContentRecord`s. Headers resolved
  by alias, as everywhere else in this codebase; a workbook with no `Session No.` column
  reports that and numbers nothing rather than guessing.
* **`portal.read_course(..., session_of_unit)`** — the workbook wins where it speaks;
  the positional walk stays as the fallback for units it does not cover and for PSE.
  Disagreements are **counted and printed with examples**, never silently resolved:
  changing 81 session numbers without saying so is how a reviewer stops trusting a
  digest.
* **`feed_sheets(..., session_of_name)`** — tool-sheet declarations now carry a session
  number, resolved from the corrected records by majority vote (a name can appear under
  two sessions where a "Part - 2" unit reuses its parent's title; the most-referenced
  session is the honest answer). **2,515 sheet locations placed, 0 unplaced** — every one
  was `None` before, which is why 43 dependencies in Intro to Gen AI could be reported as
  "in this course" with no way to say where.
* **`sheets.course_for_workbook`** — the workbook->course matcher moved out of `main.py`
  so `ingest` and `extract` share one copy. Two copies of that map is how the phantom-
  course bug of §25 would return.
* **`locate.py`** resolves a workbook **cell** (`Book.xlsx::Course Outline::Outline::row7`),
  so slide text appears in the detail panel with the matched term highlighted — 186 of
  186 slide-outline locations resolve. A tool-sheet path names no cell and correctly
  stays unresolvable.
* `expect_sessions` -> 25/29/17/13, with the reason recorded beside it.

Effect: every S6 finding now names its sessions — all four were empty, because all four
rest only on workbook pins. Every location in every course now carries a session number
(49/49, 46/46, 59/59). 91 dependencies in Intro to Gen AI gained a location traced to
slide text, `DeepWiki` and `Gamma AI` among them.

Gates: 368 tests (21 new, including the `Common Mistakes` shape reproduced from a
synthetic export), eval 4/4 suites at 100%, `verify` clean, `ingest` exit 0 on all four
courses.

### Measured and not built: the outlines as an alternatives nominator

Recorded because it looked strong. Phase 4's discovery layer is bounded by nomination
recall, and one outline line is the curriculum's own nomination, better sourced than any
search hit:

```
- Similar Tools: Beautiful.ai,Sendsteps,ai,Canva
```

Across all three workbooks' 135 outline and takeaway cells there is **exactly one** such
line. A wider `vs`/`alternatives` sweep returns 34, but they are conceptual comparisons —
"Stock vs Share", "AI Ethics vs Responsible AI", "Full Fine-Tuning vs PEFT" — not tool
substitutions. One instance is not a signal. Dropped with the measurement, as the
registry-neighbour nominator was in §26.

### Still open

* **The 68 decks are never checked.** `Session PPT` records 68 Google Slides URLs; 0
  dependencies carry a `docs.google.com` referenced URL, because `INFRA_HOSTS` excludes
  the host and `extract._links` skips it. A deleted or unshared deck is invisible — the
  codetotutorial failure applied to our own authoring source. §7's reason for the
  exclusion ("a single dead Google Slides deck is a content bug, not tool drift") holds
  for a deck linked from prose, not for the 68 the workbook names as each session's
  source. Complicated by permissions: Slides returns 200 for a deck a student cannot
  open, so an unauthenticated probe would report false health. A decision, not an
  implementation.
* **Lineage is loaded but unused for blast radius.** We know unit -> session but do not
  yet flag the MCQs authored from a deck that taught a now-dead tool where they never
  name it. This is the sharpest remaining use of the data; it needs a *derived* location
  class weighted below a direct mention, the additivity invariant re-asserted, and a
  before/after on all nine live findings, because blast radius feeds severity.
* **`link:a_href` attributes by domain**, so every `docs.n8n.io/...` link lands on
  whichever entry owns that host. `@n8n/n8n-nodes-langchain.agent` carries 31 referenced
  URLs including Gmail's docs page while the real `n8n-nodes-base.gmail` carries none,
  overstating that S4's 72 locations. The panel no longer amplifies it (a URL may
  highlight only if it names the dependency) but the extraction is unchanged, because
  fixing it moves severities.

## 32. S11: the topic we do not teach yet

Everything before this section runs **inside-out**. `extract/inventory.py` derives the
dependency list *from the course content*, so the probe, the research stage and the
scorer can only ever examine what is already taught. Their answer to "is our prompting
session complete?" is structurally always yes — not because it is, but because the
question cannot reach them. Across four artifacts the measurement was exactly what the
architecture predicts: `by kind_of_signal: {'regression': 34}`, `S10=0`, `S11=0`, and
S11 had **no producer at all** — it appeared twice in the codebase, in the `SIGNALS`
table and in a recommendation template, and nothing raised it.

The request that closed this was concrete: *if a new prompting technique is introduced,
the system should suggest that it can be included in the Advanced Prompt Engineering
session.* Note what that asks for beyond detection. "Course coverage has fallen behind"
is not a task. "Add self-consistency to Intro to Gen AI session 8, whose Key Takeaways
already list Zero-shot, One-shot, Few-shot and CoT" is a half-day of work with a known
owner. So the finding has two halves, and they fail in different ways.

### The placement half: the workbook is the only record of what a deck teaches

None of the 24 `Session PPT` decks' outlines appear in the JSON export. The workbook's
`Course Outline` sheet — already the authority for session *numbering* (§31) — is also
the only description of what each deck covers. `analyse/curriculum.py` reads it as a
document per session: title, `Outline`, `Key Takeaways`. All 71 sessions across the
three workbooks index; PSE has no workbook and therefore contributes none, which is a
limit of the input rather than of the code.

Placement is IDF-weighted term overlap over that 71-document corpus, multiplied by how
much the session is *about* the area. Not an embedding, and the reason is not cost: the
score has to be **explainable in the finding** ("matched on: prompt, chain-of-thought,
thought"), and a reviewer settling a placement in five seconds needs to see why it was
proposed. Three rules earned their place by being wrong first:

* **Hyphenated compounds contribute their parts.** `Chain-of-Thought` is one token, so
  session 8 shared no term with a page describing "Tree of Thoughts" — an analogy a
  reviewer sees instantly.
* **A session may only be named on the strength of the topic's own words.** Matching
  nothing but the area vocabulary gives every session in the area the same score, so
  the winner was decided by session number. That is how "Tree of Thoughts" was placed
  in a session about n8n merge nodes.
* **Below `MIN_PLACEMENT` the finding declines to name a session** and lists the
  candidates with scores instead. A confidently wrong session number is worse than an
  honest shrug.

Worked, against the real workbook: a new *technique* reaches session 8 (Advanced Prompt
Engineering), a new *framework* reaches session 5 (Prompt Engineering Fundamentals), and
an image-sampling topic reaches session 15. Nothing but the sessions' own outline text
can separate those three, which is the whole argument for reading the workbook.

### The detection half: corroboration is the precision rule

`probe/frontier.py` reads an official page as an *enumeration* — furniture removed by
element (`<nav>`, `<footer>`, `role="navigation"`), items bound to headings, and
`supported=False` when nothing can be bound, never a fallback to text search. Same
discipline as `probe/catalogue.py`, different shape.

Run on four Google documentation pages alone, that produced **32 findings**, and most of
them were not teachable topics: *Batch embeddings*, *Migration from
gemini-embedding-001*, *Start building with embeddings*, *Workarounds for pre-tool text
requirements*. Real headings, genuinely absent from the curriculum, completely useless.
The extractor was doing what it was told; the assumption that a docs page's headings
enumerate an area is about 40% true.

What separates an industry topic from one vendor's API detail is that a **competitor
documents it too**. So `MIN_CORROBORATION` requires a topic to be named by two sources
whose authority sets are **disjoint** — enforced structurally, so two Google pages
cannot corroborate each other. Measured on the Google × Microsoft prompting pair, 0.4
keeps *Start with clear instructions* ~ *Clear and specific instructions* (0.50),
*Zero-shot vs few-shot prompts* ~ *Few-shot learning* (0.43) and *Break down prompts
into components* ~ *Break the task down* (0.40), and drops *Add context* ~ *Add clear
syntax* (0.25) and everything below. 32 findings became 7.

The cost is recall: *Grounding and code execution* ~ *Provide grounding context* scores
0.20 and is missed. That is the right direction to fail in. A digest reporting three
real gaps is read; one reporting thirty of which nine are real is not read twice.

### Three things that would have destroyed trust, and what stops each

* **Reporting a technique the session already teaches.** "Zero-shot vs few-shot
  prompts" and session 8's "Prompting Techniques (Zero-shot, One-shot, Few-shot, CoT)"
  normalise to different keys, so a key-equality test called it missing. Coverage is
  therefore decided two ways — exact key, or *every* distinctive word of the name
  already present in one session — and across a corroborated cluster, so whichever
  vendor phrased it closest to the workbook settles it.
* **A source declaring its own authority.** `Source.subject_of()` deliberately does not
  call `with_domains_from_urls()`. Folding the page's own host into its authority set
  would make every entry in `registry/topics.yaml` authoritative about itself, so a
  newsletter added by mistake would substantiate an `EXISTENCE` claim — a STRICT kind.
  Caught by a test before it shipped.
* **Corroboration surviving in name only.** If one of the two citations is refused by
  the trust layer, the finding is dropped rather than reported on one. `main.py verify`
  asserts the same invariant from outside: an S11 must carry two substantiating claims
  on two different hosts.

### The mechanism is the deliverable, not the prompting case

The worked example throughout — a new prompting technique belonging in Advanced Prompt
Engineering — is an illustration of the mechanism, not its subject. Nothing in
`probe/frontier.py`, `analyse/curriculum.py` or `analyse/gaps.py` knows what a prompt
is. Adding an area is a YAML entry: an id, the terms that identify its sessions, and two
official pages on disjoint domains. Ten areas ship; the first one needed the code, the
other nine needed nine YAML blocks.

One constant was tuned to the example and has been generalised. `normalise()` folded out
"prompting, prompts, technique, method, approach, pattern" — right for the first area
and doing nothing for the rest. It now folds `_CATEGORY_WORDS`: words naming the
*category* rather than the thing, in any area (technique, method, strategy, mode,
feature, capability, pattern). The obvious next step, folding out each area's own
`scope_terms`, was implemented, measured and reverted: it reduces a name that is mostly
area vocabulary to a stub, and the stub matches the wrong thing. "Other image generation
modes" became `other`, which the coverage check then found in three unrelated sessions
and reported as already taught. Vendor padding is handled instead by the term rule in
`analyse/curriculum.py`, which asks whether every distinctive word of a name is already
in one session — a rule that cannot produce a stub, because it never shortens anything.
The capability stays on the function, unused, with the measurement recorded beside it.

Similarly, a heading that is one vendor's page furniture ("Topic-specific prompt guides")
belongs in that source's `exclude` list in the registry, where a human can see it, and
not in the extractor's global stop list where every other area would carry it.

### Generality, as a reported number rather than a claim

The code is course-agnostic — it indexes every workbook it can map and loops every
course — but it can only see areas the registry declares, so an undeclared subject
cluster is invisible in exactly the way an un-taught topic is invisible to the rest of
the pipeline. `main.py gaps` therefore prints its own blind spot:

```
71 session(s) indexed from 3 workbook(s); 10 curriculum area(s) declared
area coverage: 65/71 session(s) fall inside at least one declared area
6 session(s) are in NO declared area, so no gap can ever be reported for them:
    AI for Finance s1 — Your Learning Journey
    Building LLM Applications s1 — Your Learning Journey
    Building LLM Applications s2 — Cloud IDE Walkthrough
    Building LLM Applications s3 — Building LLM Applications Using Python | Part 1
    Building LLM Applications s5 — Building UI for LLM Applications
    Intro to Gen AI s1 — Your Learning Journey
```

Four of the six should never be covered. Two are real: the Python and UI sessions are
software-engineering topics with no Gen-AI area to sit in. n8n is the one deliberate
hole — nine Intro to Gen AI sessions build n8n workflows, `docs.n8n.io` yields zero
headings to a plain fetch, and no *independent* vendor documents n8n's node set, so no
area can corroborate it. n8n drift is caught instead by `probe/n8n_upstream.py` reading
n8n's own declared breaking changes, which is a regression check, not a gap check.

### Where it sits

A stage of its own, between `analyse` and `report`, because it is the only stage that
consults neither the inventory nor the probe. It writes its findings into the same
`findings_<date>.json` the analyser writes — so a gap sorts, diffs, triages and renders
exactly like every other finding — and its per-topic evidence into a `gaps_<date>.json`
sidecar, which `verify` reads to re-classify each citation without trusting the tier the
finding records. That is the arrangement the probe artifact already provides for
provider widening.

The synthetic `kind="topic"` dependency it builds is **never written to the inventory**.
The inventory records what the curriculum uses, and a topic we do not teach is precisely
not that; adding it would corrupt the one count the whole system reports on.

### A finding has to be readable on its own

> "I just checked the 3 new findings ... honestly I could not understand what mentioned
> there, what suggestion given and where it needs to be implemented or added."

Every part of the answer was already on the finding, and every part was in the wrong
place. The card read:

```
MEDIUM · S11 Curriculum topic gap — Break the task down
Break the task down is documented by Google Gemini API and Microsoft Azure AI
Foundry under Prompting techniques, and appears in no session's outline
```

The title is a vendor's imperative heading, which reads as advice to the reviewer
rather than the name of a missing topic. The summary is entirely provenance — it says
how we found it and never what it is. And the sentence that makes it comprehensible was
on the finding all along, as the `Claim` quote, rendered at the very bottom of the panel
under "evidence": the right place for provenance and the wrong place for a definition.
Four changes, each answering one of the three questions a reader actually asks.

**What is it.** The summary now leads with the vendor's own defining sentence, attributed.
Choosing that sentence by length does not work — it picked *"Now we demonstrate another
toy function calling example"* over the one that explains the feature. A definition
restates its subject, so candidates are ranked by how much of the topic's own name they
contain, length only breaks ties, and sentences that announce an example are refused.
Two extraction bugs surfaced doing this, both fixed at the source in `probe/frontier.py`:
code blocks and tables are stripped from a section's prose, and so are nested headings —
a page that labels each code sample with its language contributed *"Call multiple
functions at once when they are independent: Python JavaScript Java REST"*.

**Where.** The row carries the course *and the session number*. It had neither, because
`project_all` rebuilds locations from the dependency and a topic has none — right for a
stale dependency, wrong for a gap, whose locations are the whole point.

**What to do.** The action names **every** session the topic belongs in, not the first.
A finding is written once and read from any course's page, so naming one placement made
the Building LLM Applications page say "Add this to AI for Finance session 9" — true,
and not something that reader can act on. Where there is exactly one placement it also
names what that session currently teaches, which is what turns a placement from an
assertion into something checkable in five seconds.

**One row, two lists.** The run view and the "still open, unchanged" list had separate
markup and had drifted; the copy people actually read was the one showing a bare name.
They now share `findingRow()`, and the run record carries `summary`, `action` and
`sessions` (idempotent `ALTER TABLE`, same shape as the `llm` column).

Two bugs fell out of this. A catch-all heading ("Other image generation modes") is a
leftover, not a subject — there is nothing to add to an outline — and is now rejected
structurally, since every documentation set has one and they all start the same way. And
retiring it exposed that a topic which stops being a candidate for *any* reason kept its
row forever: `examined` is now the union of what this run considered and every topic row
already on file, so an unscoped run repairs orphans as well as resolving fixed gaps.

## 33. Fixes and Changes are different work

> "Why both what to fix and check for changes are showing same.
>  Fixes - Issues found in curriculum
>  Changes - New additions like recently release model which is free, tool for specific
>  purpose which can be used in the curriculum, new update on the concept etc"

The split is right, it is the one the system has always used internally, and the page
was not showing it. `kind_of_signal` has been on every finding since the scorer was
written — S1–S9 are `regression`, S10–S11 are `opportunity` — and the digest has printed
them under separate headings all along. The findings page merged them into one list.

What made it unreadable was a naming mistake of mine: the sidebar entry that *starts a
run* was called **"Check for changes"**, sitting directly beneath **"What to fix"**. One
is a button and one is a list, and named like that they read as two categories of
finding. The rule now is that actions are verbs and lists are nouns:

| | |
|---|---|
| **Fixes** | Something a course teaches is now wrong — a dead link, a retired model id, a closed free tier, a version past what we pin. Published material has to change and students are hitting it today. |
| **Changes** | Something exists that we could teach — a newly released model, a tool that fits a session, a technique the decks do not cover. Nothing is broken; it is a decision for the next cycle. |
| **Run a check** | The button. |

Each list carries its own count in the sidebar, which is the point of splitting them:
"3 fixes, 12 changes" is a plan for the week and one number over both is not. `VIEW_KIND`
is the single map deciding what belongs where, read by both the count and the list, so
they cannot disagree. The digest's two headings were renamed to the same two words —
a reader who moves between the page and the report should not have to translate.

Two counting bugs fell out. `/api/summary` was still filtering by inventory membership,
so a course page's header said **0 opportunities** while its own list showed two — the
same bug already fixed in `/api/findings` and missed here. And the `standing` payload did
not carry `kind_of_signal`, which would have emptied one of the two lists with no error
on any re-run with no news, when the standing list *is* the page.

### What is actually in each pile today, and a correction

Fixes: 34. Changes: 5, all of them topic gaps (S11).

I previously wrote that S10 has no producer because `verified_alts` is always empty.
That was wrong, and the artifact says so: six alternatives are attached and **all six
verify**. S10 has not fired for a different and better reason — discovery ran on
dependencies that were already broken (`discovery_reason: breakage`), and when a
dependency has an S1 or S4 the verified alternatives attach to *that* finding rather
than raising a separate one. "This is dead, here is a replacement" is one finding, not
two. The rotation path that would raise a standalone S10 on a *healthy* dependency is
wired (`agent.py`'s opportunity slice) and has simply not produced a verified candidate
in these runs.

So the "newly released model which is free" case is not yet covered by anything. A new
model is not a topic, so `gaps` will not see it, and it is not a replacement for
something broken, so S10 will not either. The mechanism for it already exists —
`probe/catalogue.py` reads vendor model tables as enumerations, which is the same shape
`probe/frontier.py` reads documentation headings in — so it is the gaps stage pointed at
a model catalogue rather than a docs page. That is the next thing worth building, not
something already built.

## 34. The UI reads without a glossary

> "the naming in the UI is a bit confusing and I'm not able to make others understand"

The page's labels and the system's internal names had been the same vocabulary, so
reading it required knowing the codebase. On screen, with no explanation anywhere: *blast
radius 65*, *watch tier mention-only*, *diff class worsened*, *S7*, *AUTHORITATIVE*,
*regression*, and a stage list reading `ingest / extract / probe / research / analyse /
report`. Every one of those is the right word for the code and the wrong word for a
curriculum reviewer being shown the tool for the first time.

The fix is one `WORDS` map and a `word(group, key)` helper, and it is deliberately
**one-directional**. `watch_tier` is still `watch_tier` in the database, in
`Scope.to_cli_args()` and in every URL; the checkbox `value` attributes and the filter
options are untouched. Only the text beside them changed. A label is safe to edit and a
wire value is not, and `tests/test_ui_contract.py` now asserts both halves of that: the
plain words are present, and every internal value still is.

| Was | Is |
|---|---|
| Findings · Inventory · Digest | What to fix · Tools & models used · Weekly report |
| New run · Run history · Run audit | Check for changes · Past checks · Start check |
| Trust & feedback · Vendor watch | Sources & decisions · Vendor releases |
| Stages: probe / research / analyse | Check every link and version · Read the official pages · Rank the issues and say what to do |
| blast radius 65 | impact 65, with the weighting explained on hover |
| watch tier: mention-only | only mentioned |
| diff class: worsened / unchanged | got worse / no change |
| regression / opportunity | now broken or outdated / could be better |
| AUTHORITATIVE / LEAD_ONLY | the vendor's own page / unconfirmed |
| S7 (bare, in four lists) | model retired, with `S7` on hover |

Two things kept their precise names on purpose. The drift codes stay visible — a
reviewer comparing the page against the digest or the scorer needs them — but the plain
phrase is now the label and the code is the tooltip. And "Tavily" stays spelled out on
the research step, because the search spend is the one cost a reader must see *before*
pressing the button; plainer wording must not hide a vendor that bills.

A topic gap also needed its own words, because it points the opposite way to every other
finding. Its locations are not places the curriculum *uses* something, they are sessions
that *should cover it and do not*, so the panel says "Where it belongs", the projection
note explains that there is no tool to count, and the detail block describes the topic —
area, what each vendor calls it, how strongly the two agreed — instead of a vendor and a
registry id.
