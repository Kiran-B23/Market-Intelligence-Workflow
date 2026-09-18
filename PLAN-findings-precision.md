# Plan — say the true thing about *where* and *what*

Status: **implemented**, 18 Sep 2026 · responds to five complaints raised against the
`findings_2026-09-18` run (43 findings) · outcomes recorded in `PRD.md` §35

---

## 0. What I checked, before proposing anything

Each complaint was tested against the artifact rather than the code's intent. Four are
confirmed with harder evidence than the complaint claimed; one is confirmed as stated.

| # | Complaint | Verdict |
|---|---|---|
| 1 | No separate way to run gaps / issues / new-concepts | **Confirmed** — one checkbox runs three signals |
| 2 | Composio's *name* is blamed for a *URL* problem | **Confirmed** — and it is the root cause of #3 and #5 |
| 3 | Hard to see where the issue is; MCQ/coding not separated | **Confirmed** — the data exists and is discarded |
| 4 | Too many n8n findings; no version check | **Confirmed** — three separate defects, one a false positive |
| 5 | Everything says "Coding Practice" | **Confirmed, and worse** — 25 of 43, none of them coding |

---

## 1. The single root cause behind #2, #3 and #5

`miw/analyse/score.py:154`

```python
courses=dep.courses, locations=dep.locations[:12],
```

A finding inherits **the dependency's entire location set**, then truncates it to 12. It
is never filtered to the locations the *signal* actually affects.

The asymmetry is visible in the same file. `recommend()` at line 467 already gets this
right:

```python
# Scope the action to the links actually affected, not to every mention of the
# dependency: "repoint the dead link in 845 locations" is false when one URL broke.
"S1": f"Repoint or replace the dead link in the {dep.link_locations} place(s) ..."
```

The *sentence* was scoped. The *location list* underneath it never was. So the Composio
finding reads:

> `https://mcp.composio.dev/dashboard` returns 404/410 — repoint the dead link in the
> **6** place(s) Composio is linked

…directly above a list of **12** locations, of which 6 are `link:a_href` and the other 6
are four `prose_name` hits in *Classroom Quiz A* (s24) and two `sheet_declared` rows. A
dead dashboard URL cannot make an MCQ that merely names Composio wrong. Same shape for
Stability AI (10 shown / 6 links) and **Ngrok (9 shown / only 2 links)** — the worst ratio.

This is precisely the user's point: *"the system will need to be designed to identify the
issue related to a particular problem rather than generalized."*

### Fix — an affected-location predicate per signal

Add to `miw/analyse/score.py` a table mapping signal → which locations it can touch, and
run `dep.locations` through it before the `[:12]`:

| Signal | Affected locations |
|---|---|
| S1 dead link, S2 signup wall | `evidence_source.startswith("link:")` **and** the URL is in `f.affected_urls` |
| S3 version drift, S4 install | `install_command`, `solution_import`, `sheet_pin` |
| S7 model retired / renamed | `model_id`, plus `prose_name` **only where the exact model id appears** |
| S9 n8n breaking change | `n8n_workflow` (the node is actually wired), and per-node |
| S5/S6 screenshot & UI drift | `SESSION_PPT`, `LEARNING_RESOURCE` |
| S8 pricing | any — pricing changes the instruction wherever it is taught |
| S10/S11/S12 | already location-free by design; unchanged |

Two guard rails, both learned the hard way on this codebase:

* **Never silently empty a finding.** If the predicate yields zero locations, that is not
  "no locations" — it is a finding whose blast radius we cannot substantiate. Raise it
  with `supported=False` semantics (the same structural refusal used for decks and
  catalogues) and say *"affected places not determined"* rather than showing the
  dependency's whole footprint and hoping.
* **Keep the unfiltered set.** `total_locations` already records the true count. Add
  `mention_locations` alongside `locations` so the panel can still say "Composio is named
  in 6 other places we did not flag" — informative, and clearly separated from the 6 that
  are broken.

`recommend()` then drops `dep.link_locations` and uses `len(f.locations)`, because the two
finally agree.

---

## 2. #5 — "Coding Practice" is a unit name, not an artifact type

`miw/analyse/score.py:442`

```python
first = f.locations[0]
sess = f"session {first.session_no}" if first.session_no else first.unit_name
where = f" Starts at {first.course} / {sess} / {first.unit_name[:50]}."
```

Measured across the 43 findings:

```
"Starts at … / Coding Practice"                          25
first location's object_type == OBJECTIVE_QUESTIONS      26
first location's object_type == CODING_QUESTIONS          0
findings labelled "Coding Practice" that point at a
  coding question                                         0
```

**Every one of the 25 is wrong.** "Coding Practice" is the `unit_name` of the unit that
happens to hold the MCQ bank; the artifact is an MCQ. The user's instinct — *"verify once
whether it is true or not"* — was right, and the true figure is worse than "all the
findings": it is *all of them, and it is never once correct*.

Two further defects in the same three lines:

* When `session_no` is absent the unit name is printed **twice** — "… / Coding Practice /
  Coding Practice".
* `locations[0]` is the extract order, not a ranked first. The "start" is arbitrary.

### Fix

* Say the artifact type, from the `object_type` we already store, through the existing
  `WORDS`/`word()` vocabulary: `OBJECTIVE_QUESTIONS` → *quiz question*, `CODING_QUESTIONS`
  → *coding practice*, `LEARNING_RESOURCE` → *reading material*, `SESSION_PPT` → *slide
  deck*, `SHEET` → *tracking sheet*, `UNIT_TAG` → *unit tag*.
* Rank before taking the first: prefer a location that **executes** the dependency over
  one that merely names it. `Dependency._executes()` and `RUNTIME_EVIDENCE_SOURCES`
  already encode exactly this distinction — reuse them rather than inventing a second
  rule.
* Replace "Starts at" with a count-led phrase, because a single location is the common
  case and "starts at" implies a sequence: *"Affects the reading material in session 12
  (Intro to Gen AI), and 5 more places."*
* Drop the duplicated unit name.

---

## 3. #3 — group the occurrences by what the reader has to edit

The complaint is the presentation half of #1's root cause: *"if the issue persists among
the session, MCQs and coding practices, then those also need to be categorized."*

The data is already on every `Location` — `object_type`, `evidence_source`, `session_no`,
`content_id` — and the detail panel throws all of it away except the session.

### Fix, in `miw/api/static/index.html` and `/api/findings`

Group locations **by artifact type first, session second**, and label each group with the
work it implies:

```
Broken links          2   reading material · s07 Integrating MCP, s12 Tool Use
Quiz questions        4   s24 Classroom Quiz A            → wording only
Slide decks           1   s07 deck 3                      → screenshot re-shoot
Tracking sheet        2   declared dependency             → update the pin
```

Each row carries the `content_id` so the row is a usable address, not a description. The
counts are the filtered set from §1; anything excluded appears under a collapsed *"also
named in N places, not affected"*.

This also makes the finding readable from the list without opening it — the same standard
the user set earlier for gap findings — because the one-line row can now say *"6 broken
links, 0 quiz questions"* instead of *"12 locations"*.

---

## 4. #4 — the n8n findings, which are three separate defects

14 S9 findings. Three are *"no longer in n8n's shipped node set"* (`googleCalendarTool`,
`googleDocsTool`, `httpRequestTool`) and are sound. The other **11 come from 7 rules**:

| Count | Rule |
|---|---|
| **4** | Sub-workflow waiting node output behavior change |
| 2 | `${removedNodeName}` node removed |
| 1 each | Pyodide Python removed · `process.env` blocked · in-memory binary storage removed · embedded chat WebSocket format · AI Agent versions below 2 removed |

### (a) Fan-out: one rule, four findings

`wait-node-subworkflow` lists many `node_types`; the curriculum teaches four of them, so
the reader gets the same paragraph four times. **One rule must produce one finding**, with
the affected nodes as its location list — which is exactly the §1 change applied to S9.
11 findings become 7, and the 7 are distinct pieces of information.

### (b) A template literal reached the report

Two findings read *"`${removedNodeName}` node removed"*. `parse_rule` lifted a TypeScript
template literal out of the upstream source and never interpolated it. A finding whose
title contains `${` is not a finding — it is a parse failure wearing one.

Fix: interpolate where the binding is available; where it is not, **refuse to emit the
rule** and count it in the run's diagnostics. Add a test asserting no finding field
contains `${`.

### (c) No version gating — the false positive the user predicted

This is the substance of *"check the versions and identify what changes were made and are
those essential to fail n8n execution."*

```json
{"rule_id": "agent-node-version.rule", "n8n_version": "v3",
 "title": "AI Agent versions below 2 are removed",
 "node_types": [], "actions": ["Move AI Agent nodes on versions below 2 to the latest"]}
```

The course teaches `@n8n/n8n-nodes-langchain.agent` at **typeVersion 2.2**. 2.2 is not
below 2. **The finding is false.** And `node_types` is empty, so the rule matched by *name
mention* and rode the `breaking_change_possible` path — a rule that names no node was
allowed to accuse a node.

`BreakingRule` carries `rule_id, n8n_version, title, description, severity, doc_url,
node_types, actions, source_path` — **no affected version range**. The range exists only
as prose inside `title`/`actions`.

Fix, in order of confidence:

1. **Extract the range.** Parse `below N`, `version N and earlier`, `v1 only` from
   `title` + `actions` into `affected_typeversion: str` on `BreakingRule`. Compare against
   the `typeVersion` the workflow JSON actually declares — we already read it, it is how
   S3 works for n8n nodes. Suppress when the taught version is outside the range.
2. **Where the range cannot be parsed, do not guess.** Emit at `info` with an explicit
   *"version range not stated in the rule — confirm manually"*, never at `high`. Silence
   is wrong here too; the honest output is a low-confidence flag.
3. **A rule with empty `node_types` may not name a node.** Either it applies to the
   instance (report once, unattached to any dependency) or it is unusable. The current
   `breaking_change_possible` middle ground produced this false positive.
4. **Drop `latest_version` from S9 rendering.** It reads `2.38.7` on all 14 — n8n's app
   version, identical everywhere and uninformative. The useful version is the node's
   `typeVersion` versus the rule's range.

Expected after the fix: **14 → about 5–7**, every one of them version-checked.

---

## 5. #1 — one checkbox runs three different jobs

The run form (`index.html`, *Steps to run*) offers seven pipeline stages. One of them,
`gaps`, silently runs **three independent signals**:

```python
rep      = find_gaps(areas, index)     # S11  topics we don't teach
newer    = find_newer(inventory, …)    # S12  what a vendor added since last look
launches = read_launches(…)            # nominations — new tools
```

There is no way to ask for gaps alone, changes alone, or nominations alone. And the form
is expressed as *pipeline stages* — engineering vocabulary — when the question the
curriculum team actually asks is *"what is broken?"* / *"what is missing?"* / *"what is
new?"*

### Fix — three named jobs above the stage list

Keep the stage checkboxes (they are the honest mechanism, and an advanced user wants
them), but put three one-click jobs above, in the vocabulary the rest of the UI now uses:

| Job | Stages | Signals |
|---|---|---|
| **Find what's broken** | probe · research · analyse · report | S1–S9 |
| **Find what's missing** | gaps · report | S11 only |
| **Find what's new** | gaps · report | S12 + launches |

This needs `cmd_gaps` split into three independently callable pieces and the runner's
stage list widened — the functions are already separate, only the entry point bundles
them. Selecting a job ticks the corresponding boxes, so the two views stay consistent and
nothing is hidden.

---

## 6. Order of work

1. **§1 affected-location predicate** — one change, fixes #2, unblocks #3 and #5.
2. **§2 artifact-type wording** — small, and the most visible wrong statement in the product.
3. **§4 n8n** — (b) the `${` leak first (it is a one-line credibility problem), then (c)
   version gating, then (a) the fan-out, which is §1 applied to S9.
4. **§3 grouped detail panel** — depends on 1 and 2.
5. **§5 three jobs** — independent; can land any time.

## 7. How each fix is verified

* **#2** — for every finding, every location in `f.locations` is one the signal can reach.
  A test asserts it per signal; Composio drops 12 → 6, Ngrok 9 → 2.
* **#5** — zero findings say "Coding Practice" unless `object_type == CODING_QUESTIONS`.
  Currently that count is 25 wrong out of 25; the target is 0.
* **#4** — no finding field contains `${`; S9 count falls to one per rule; the
  `agent` typeVersion 2.2 finding disappears; a rule with no parseable range renders at
  `info` with the manual-check note.
* **#3** — each detail panel row carries a `content_id` a reviewer can open.
* **#1** — each of the three jobs runs alone and produces only its own signals.
* Throughout: `pytest tests/ -q` (612 now, plus the new assertions), `eval/run_eval.py`
  4/4, `main.py verify` clean, and the total finding count must **fall** — every change
  here removes false statements, so any increase is a regression.

## 8. Not in this plan

The deferred backlog is unchanged: security advisories for the 91 packages · the
revision-cycle calendar for due dates · `resolve_absent` conflating curriculum removal
with a vendor fix · freshness enforcement · a pricing feed as a trigger only · student
ticket tagging · cohort/enrolment as a sort axis, never a severity input.
