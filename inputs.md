I reviewed the PRD as an **agentic workflow / multi-stage intelligence system**, with particular attention to **inputs, state, agent boundaries, and whether each decision has enough information to act reliably**.

Overall, the architecture is strong. The biggest opportunity is not adding more agents; it is adding a few **high-value structured inputs** that improve impact scoring, gap detection, freshness, and recommendation quality.

The current design already has a solid foundation: course JSON + workbook are treated as distinct published/authoring inputs, the inventory acts as the central index, and downstream agents work from scoped identifiers rather than sending entire course content into prompts.  

# My assessment

### Current input architecture

| Input                                   | Current     | Assessment                     |
| --------------------------------------- | ----------- | ------------------------------ |
| Course JSON                             | ✅           | Essential                      |
| Course workbook                         | ✅           | Essential                      |
| Saved deck text                         | ⚠️ Optional | Should become a stronger input |
| Tool/dependency registry                | ✅           | Essential                      |
| Topic registry                          | ✅           | Essential for gaps             |
| Vendor official sources                 | ✅           | Essential                      |
| Tavily/web research                     | ✅           | Good                           |
| Historical state / snapshots            | ✅           | Very important                 |
| Cohort timing                           | ⚠️          | Missing as a structured input  |
| Enrollment / learner impact             | ⚠️          | Missing                        |
| Assessment criticality                  | ⚠️ Partial  | Should be explicit             |
| Course/session learning outcomes        | ⚠️ Partial  | Important for gap analysis     |
| Curriculum version / effective date     | ⚠️          | Should be explicit             |
| Dependency ownership/vendor metadata    | ⚠️ Partial  | Should be normalized           |
| Business priority / curriculum priority | ❌           | Missing                        |
| Industry/job-market signals             | ❌           | Missing for S11                |
| Human feedback / reviewer decisions     | ⚠️ Partial  | Should become structured input |
| Source freshness policy                 | ⚠️          | Should be explicit             |
| Known exceptions / suppressions         | ⚠️          | Should be first-class input    |

The PRD itself already identifies four useful gaps: blast-radius data, export refresh, decks, and scheduling/triggering. 

But I would add several more.

---

# 1. Highest-priority missing input: cohort information

This is the **single most valuable input I would add**.

The current severity model already uses:

> cohort urgency — is a live cohort hitting this unit within ~2 weeks

but the PRD does not define a durable source/input for that information. 

### Add

```yaml
cohort_context:
  course_id:
  cohort_id:
  start_date:
  end_date:
  active:
  affected_sessions:
  learner_count:
```

Or, better, maintain a separate:

```text
cohorts
course → cohort → dates → session schedule → learner count
```

### Why this matters

Without this, "critical" is based largely on **content semantics and dependency usage**, not actual business urgency.

For example:

```text
Dead dependency
→ Session 12 affected
→ Session 12 is taught tomorrow
→ 2,500 learners

vs.

Dead dependency
→ Session 12 affected
→ Session 12 is retired
→ 0 active learners
```

Those should not receive the same urgency.

### Recommendation

Make `cohort_context` an input to **Analyse**, not the agent.

```text
inventory
probe
research
       ↓
cohort_context
       ↓
impact analysis
```

---

# 2. Enrollment / learner exposure should be an explicit input

The PRD already recognizes this as an open decision: without enrollment data, blast radius falls back to location count and content type. 

I would go one step further.

Instead of:

```text
blast radius = number of locations
```

use:

```text
blast radius =
    affected learners
  + affected sessions
  + execution criticality
  + assessment criticality
```

Suggested input:

```yaml
learner_impact:
  course_id:
  unit_id:
  active_enrollments:
  expected_enrollments:
  completion_rate:
  upcoming_cohort_count:
```

This makes the recommendation much more meaningful to a curriculum lead.

---

# 3. Assessment criticality needs to be explicit

You already distinguish whether something is executed versus merely mentioned. That was a very good correction in the system. The PRD shows why: a retired model used in coding execution is materially different from one merely mentioned in MCQs or reading material. 

But I would formalize this further.

Add:

```yaml
content_criticality:
  learning_resource: 1
  example: 2
  mcq: 3
  coding_practice: 4
  graded_assessment: 5
  production_workflow: 5
```

Or preferably make it **data-driven** rather than hard-coded.

Then the agent gets:

```text
dependency
    ↓
where used
    ↓
content criticality
    ↓
learner exposure
    ↓
cohort urgency
    ↓
severity
```

That is a much stronger impact model than merely counting references.

---

# 4. Add an explicit curriculum version / effective-date input

This is missing from the core input contract.

You are already relying heavily on historical snapshots and week-over-week state. 

But you also need to know:

```yaml
curriculum_version:
  course_id:
  version:
  effective_from:
  effective_to:
  status: active | archived | draft
```

Why?

Suppose:

```text
Course v2026.1
Course v2026.2
```

and a dependency exists only in v2026.1.

Your inventory should not treat the dependency as active merely because it exists in an old snapshot.

This becomes especially important once course refreshes happen frequently.

---

# 5. Add explicit dependency ownership metadata

Your current dependency schema is good:

```text
canonical_name
homepage
docs_url
aliases
taught_version
vendor
locations
first_seen
watch_tier
review_status
```

But I would add:

```yaml
ownership:
  vendor:
  product:
  parent_company:
  maintained_by:
  official_domains:
  github_org:
```

The trust system already relies heavily on official-domain classification. 

Making this metadata explicit will reduce repeated reasoning in the agents.

For example:

```text
Groq
→ vendor: Groq
→ product: Groq API
→ official domains:
   groq.com
   console.groq.com
   console.groq.com/docs
```

This makes source routing much more deterministic.

---

# 6. Add a source-freshness policy as an input/config

You have retrieval dates, hashes, watermarks and historical state. That's excellent.

But I would explicitly define **how fresh a source must be for each type of claim**.

For example:

```yaml
freshness_policy:
  pricing: 7d
  model_status: 1d
  package_version: 1d
  documentation: 14d
  curriculum_gap_source: 30d
```

This prevents the agent from treating:

```text
"pricing page fetched 3 months ago"
```

the same way as:

```text
"model catalogue checked yesterday"
```

Your current watcher correctly distinguishes baseline / unchanged / changed states, which is a strong design. 

A freshness policy would make that even stronger.

---

# 7. The biggest missing input for S11: external curriculum benchmarks

This is the **most important gap in the gap-analysis design**.

Current `gaps` uses:

```text
workbook outlines
+
registry/topics.yaml
+
official documentation
+
two-source corroboration
```

That is sensible, but it creates a conceptual limitation.

The external evidence is still largely **vendor-centric**.

The PRD itself correctly separates S11 from dependency monitoring because inventory-driven analysis cannot discover topics that aren't already taught. 

However, I would introduce a dedicated **curriculum benchmark input**.

Examples:

```text
job descriptions
industry competency frameworks
certification blueprints
official university curricula
major conference curricula
technical community trend data
employer skill requirements
```

Then:

```text
Industry signals
       ↓
topic normalization
       ↓
topic importance
       ↓
compare against curriculum
       ↓
gap candidate
       ↓
corroboration
       ↓
S11
```

Otherwise, your gap detector can answer:

> "What vendors are documenting?"

more reliably than:

> "What skills should this curriculum teach?"

Those are not the same question.

---

# 8. Add a curriculum intent input

For gap analysis, I would not rely only on session outlines.

Add structured:

```yaml
curriculum_intent:
  course:
  module:
  learning_outcomes:
  target_role:
  learner_level:
  prerequisites:
  intended_skills:
  assessment_skills:
```

For example:

```yaml
course: Intro to Gen AI
target_role:
  - AI Engineer
  - GenAI Developer

learner_level: beginner

learning_outcomes:
  - understand prompting
  - use APIs
  - build simple RAG applications
  - evaluate LLM outputs
```

Then the gap agent can distinguish:

```text
Topic exists externally
BUT
topic irrelevant to course objective
```

from:

```text
Topic exists externally
AND
directly relevant to intended learning outcomes
BUT
not taught
```

That will sharply reduce unnecessary S11 findings.

---

# 9. Add learner-level / difficulty as an input

A curriculum gap does not automatically mean:

> "Add this topic."

Suppose research discovers:

```text
advanced mixture-of-experts optimization
```

That might be a valid industry topic but completely inappropriate for an introductory course.

Add:

```yaml
curriculum_profile:
  learner_level: beginner | intermediate | advanced
  target_role:
  prerequisite_level:
  intended_depth:
```

Then the gap agent can classify:

```text
relevant + appropriate
relevant but too advanced
relevant but prerequisite missing
not relevant
```

This is especially important because your current placement mechanism is intentionally conservative. 

---

# 10. Add an explicit "business priority" input

You currently have technical severity and blast radius.

You should also have **organizational priority**.

Example:

```yaml
curriculum_priority:
  course:
  module:
  session:
  priority: P0 | P1 | P2 | P3
```

Why?

A low-traffic session may still be strategically important because:

```text
new curriculum launch
placement preparation
certification requirement
enterprise commitment
faculty dependency
upcoming recording
```

So:

```text
Technical severity
+
Learner impact
+
Business priority
=
Final priority
```

That is much closer to how a real curriculum team makes decisions.

---

# 11. Human feedback should become a formal input, not only a review gate

You already have human-in-the-loop behavior in the impact agent using `interrupt()`. 

Good.

But the result of that review should be captured structurally:

```yaml
review:
  reviewer:
  decision:
    - accept
    - reject
    - defer
    - merge
    - duplicate
  reason:
  corrected_severity:
  corrected_scope:
  corrected_recommendation:
  reviewed_at:
```

This is important because otherwise the human review is only a **control mechanism**, not a **feedback input**.

The system should progressively learn things like:

```text
"Reading-resource model mentions are usually non-critical."

"n8n documentation changes require manual verification."

"Certain vendor domains routinely generate false positives."
```

Not by blindly fine-tuning the model, but through **structured decision rules / evaluation data**.

---

# 12. Add an explicit suppression / exception input

You already need to handle things like the Ngrok placeholder false positive. The PRD identifies this as a significant precision problem. 

Instead of letting the extractor repeatedly rediscover exceptions, introduce:

```yaml
exceptions:
  - pattern: "*.ngrok.io"
    type: placeholder
    action: ignore
    reason: tutorial example domain
```

Also:

```yaml
suppression:
  dependency:
  signal:
  course:
  until:
  reason:
```

This is useful for temporary situations too.

For example:

```text
Vendor documentation temporarily inaccessible
→ don't generate a finding for 24 hours
```

That is better than contaminating the core detection logic.

---

# 13. Add an explicit "source allowlist / source strategy" input

Right now source trust is structurally strong: authoritative, corroborating, lead-only, excluded. 

I would make source strategy configurable per signal:

```yaml
source_policy:
  S7:
    authoritative:
      - vendor_model_catalog
      - vendor_deprecation_page

  S6:
    authoritative:
      - pypi
      - npm

  S11:
    required:
      - industry_source
      - independent_source
```

This makes the agents less dependent on prompt instructions and more dependent on an explicit policy contract.

---

# 14. For the agents specifically, define explicit agent input/output contracts

This is the area where I would make the biggest architectural improvement.

Currently:

```text
signal agent:
plan → read → replan → done

impact agent:
classify → artifacts → score → review gate
```

That is a good graph, but the PRD doesn't expose a sufficiently formal **state contract** for those agents in the high-level design. 

I recommend defining:

### Signal agent input

```python
SignalAgentInput = {
    vendor,
    trigger,
    candidate_sources,
    dependency_ids,
    source_policy,
    freshness_policy,
    previous_observation,
}
```

### Signal agent output

```python
SignalAgentOutput = {
    claims,
    evidence,
    source_urls,
    confidence,
    unresolved_questions,
    terminal_reason,
}
```

### Impact agent input

```python
ImpactAgentInput = {
    dependency,
    probe_result,
    research_claims,
    affected_locations,
    cohort_context,
    learner_impact,
    content_criticality,
    curriculum_priority,
    historical_findings,
}
```

### Impact agent output

```python
ImpactAgentOutput = {
    signal,
    severity,
    blast_radius,
    urgency,
    recommendation,
    evidence,
    uncertainty,
    review_required,
}
```

This makes the agentic architecture much more deterministic.

---

# 15. Add an uncertainty input/output

One thing I would definitely add because this is a **recommendation system**.

You currently have:

```text
claim
source
quote
severity
recommendation
```

Add:

```yaml
uncertainty:
  level: low | medium | high
  reasons:
  missing_inputs:
```

Example:

```text
severity: HIGH

uncertainty:
  level: MEDIUM
  reasons:
    - vendor pricing page accessible
    - actual student free-tier eligibility not verified
    - India availability unknown
```

This is much safer than forcing the agent to appear certain.

---

# 16. A major architectural improvement: separate "environment inputs" from "business inputs"

Right now several different things are mixed together.

I would reorganize the workflow inputs into five categories.

### A. Curriculum inputs

```text
course JSON
workbooks
decks
learning outcomes
course version
session metadata
assessment metadata
```

### B. Dependency inputs

```text
tool registry
vendor registry
aliases
versions
official domains
dependency relationships
```

### C. External intelligence inputs

```text
vendor docs
registries
web research
industry sources
job-market sources
```

### D. Operational context

```text
cohorts
enrollment
schedule
course status
curriculum priority
```

### E. Control-plane inputs

```text
scope
run mode
freshness policy
trust policy
severity policy
suppression rules
budget
rate limits
review rules
```

This would make the system much easier to reason about.

---

# 17. The agent should NOT receive raw course data

This part of your design is already very good and I would preserve it.

The document shows the current system reduces roughly **9.2M characters of course material to a ~3,510-character LLM prompt**, with zero course body text in the signal-resolution prompt. 

That is exactly the right direction.

I would make this an explicit architectural invariant:

```text
Agent input = scoped structured evidence
NOT
Agent input = whole course
```

In fact, I would add this to the PRD as a formal guardrail.

---

# 18. One input I would NOT add

I would **not** give the agent unrestricted access to:

```text
all course JSON
all workbooks
all external web pages
all historical findings
```

just because more context feels useful.

Your architecture already demonstrates why this is unnecessary.

The inventory/index approach is much safer and cheaper. 

---

# Recommended final input contract

If I were designing the next version, I'd make the top-level workflow input look roughly like this:

```yaml
workflow_context:

  run:
    mode: manual | scheduled | event
    scope:
      courses: []
      sessions: []
      dependencies: []
    run_id:
    triggered_at:

  curriculum:
    course:
    course_version:
    effective_from:
    learner_level:
    target_roles:
    learning_outcomes:
    sessions:
    assessments:
    decks:

  dependency:
    registry:
    vendors:
    aliases:
    versions:
    official_domains:

  learner_context:
    active_cohorts:
    enrollment:
    upcoming_sessions:
    completion_data:

  business_context:
    curriculum_priority:
    launch_date:
    strategic_topics:

  intelligence:
    vendor_sources:
    package_registries:
    web_search:
    industry_sources:
    benchmark_curricula:

  policies:
    trust_policy:
    freshness_policy:
    severity_policy:
    source_policy:
    suppression_rules:
    rate_limits:
    budget:

  state:
    previous_snapshot:
    previous_findings:
    reviewer_feedback:
```

Then the graph becomes:

```text
                    ┌───────────────┐
                    │ Workflow Input│
                    └───────┬───────┘
                            │
             ┌──────────────┼──────────────┐
             ↓              ↓              ↓
        Curriculum      Dependency      Operational
          Context         Context          Context
             │              │              │
             └──────────────┼──────────────┘
                            ↓
                     Signal Detection
                            ↓
                     Evidence Research
                            ↓
                      Impact Analysis
                            ↓
                 Cohort / Learner Impact
                            ↓
                    Recommendation
                            ↓
                       Review Gate
                            ↓
                         Finding
```

## Priority ranking

### P0 — I would add these now

**1. Cohort / schedule input**
**2. Enrollment / learner exposure**
**3. Explicit assessment criticality**
**4. Curriculum version/effective date**
**5. Structured reviewer decision input**
**6. Exception/suppression registry**

These directly improve the quality of the current S1–S9 findings.

### P1 — Next most valuable

**7. Learning outcomes / target-role profile**
**8. Learner level / intended depth**
**9. Business/curriculum priority**
**10. Source freshness policy**
**11. Explicit vendor/dependency ownership metadata**

### P2 — Important for making S11 genuinely strong

**12. Industry/job-market curriculum benchmarks**
**13. External curriculum/certification frameworks**
**14. Skill taxonomy / competency map**

This is the main area where I think the workflow can evolve from a **dependency-change detector** into a true **curriculum intelligence agent**.

The existing design is already unusually disciplined about trust, scoped context, deterministic extraction, and human approval.  The next step should therefore be **better input contracts**, rather than adding more autonomous behavior.


-----------------


# Review — MIW (Market Intelligence & Curriculum Gap Analyser)

This is one of the more disciplined agentic-system designs I've seen written up. Most of the failure modes that normally worry me in an LLM-in-the-loop production pipeline — hallucinated citations, silent scope creep, unbounded context growth, severity inflation, an audit layer that just trusts its own prior output — have already been hit, diagnosed, and fixed with a regression test each. The Part II changelog is unusually valuable *as documentation* because it records why each guardrail exists, not just that it does, which is what will keep the system from quietly re-acquiring the same bugs as it grows.

## What's genuinely strong (worth protecting as this evolves)

- **`Claim.build()` as the only path to an assertion.** Structural grounding, not prompt-based. "A model may emit only a name or a URL, never a fact" is the correct primitive against citation hallucination, and it's enforced by code path rather than instruction — which is what lets it survive prompt drift and provider swaps.
- **`main.py verify` re-derives trust tiers from raw artifacts instead of trusting stored tags.** This is the single best practice in the whole document. It's what caught the forum-authority hole and the provider-widening regression *inside the auditor itself* (§27). Most teams build the check once and never re-audit the check.
- **Execution-aware severity (§21).** Distinguishing "a retired model id is called at runtime" from "named in an MCQ stem" from "mentioned in prose" is exactly the fix most alerting systems never make, and it happened on the system's own first four real findings before it ever reached a wider audience.
- **The nomination ladder and its refutation accounting (§26).** Treating a refutation rate that falls to zero as a suspicion signal rather than a success signal is a level of self-monitoring on top of the pipeline that's rare to see designed in from the start.
- **The context-cost argument in §20.** "Won't sending all the course data make the agent hallucinate" gets answered with a structural argument (identifier-keyed index, dict lookup) plus a measurement (2,618:1, 0 chars of course body in the one prompt sent), not a promise. That's the right way to close that kind of stakeholder objection.

## Where I'd push before calling Phase 1 done

1. **The Ngrok false positive.** I'd treat this as higher priority than its own framing suggests. It isn't just a precision bug — it's sitting inside the headline "founding failure, solved" example, next to two real criticals. If a senior stakeholder's first read of this system is that example, a fabricated CRITICAL there costs more trust than the same bug anywhere else in the digest.

2. **The 68 decks are unmonitored.** Given §31 establishes the decks as the actual authoring source — everything else is downstream of them — they are currently checked *less* than any third-party dependency: zero probing, and permission-aware health-checking isn't even designed yet. That's the founding codetotutorial failure pointed inward: a deleted or unshared deck is invisible until someone opens it by hand. I'd move this from "known gap" to next milestone rather than leaving it in §G indefinitely.

3. **Student ticket ingestion is still Phase 2.** The stated north-star metric — share of changes caught before the first student report — is currently unmeasurable, because there's no feed of student reports to compare against. Everything else (precision, blast radius, severity) can be validated internally; this metric can only be validated externally. I'd pull even a narrow slice of this forward — tagging existing support tickets with course/session, no NLP required yet — purely to get the denominator this metric needs.

4. **The gap between the stated example and what ships.** §33 uses "a newly released free model" as the canonical Changes example, but nothing currently detects that — new models aren't topics for `gaps`, and they're not replacements for anything broken, so S10 doesn't fire either. The team has already diagnosed the fix correctly (point the catalogue enumeration reader at new rows the same way it's pointed at deprecated ones). I'd agree that's the next build, since it's the single largest gap between what the team was told to expect and what the system currently does.

## Additional inputs worth feeding the pipeline

- **A PSE workbook, even partial.** This is a named, well-understood input shape (§C.1) that would unlock gap analysis for a quarter of the courses in scope. Worth a direct ask to whoever owns PSE authoring rather than leaving it as permanent architecture.
- **A cohort/enrolment schedule feed** (already flagged as open in §10 D4). Without it, "cohort urgency" in the severity formula has nothing to weight against — a live cohort and a dormant one currently score the same. Even a coarse feed would sharpen "this sprint" vs. "next cycle" calls, which is the distinction the content team actually acts on.
- **Read-only Google Drive API access, matching student permission level**, for the 68 decks — this is the named fix for "Slides returns 200 for a deck a student can't open" (§31), and it doesn't touch the no-write boundary anywhere.
- **Security-advisory feeds** (PyPI/npm advisory DBs, GitHub Security Advisories) for the 91 taught packages. As built, S6 only fires on `major_behind_taught_pin`, so a patch release fixing a live CVE is currently silent — a different kind of "Fix" than version staleness, and close to free to add given the registry/PyPI-authority plumbing from §25 already exists.
- **Vendor RSS/Atom release feeds** as a secondary, cheaper watermark alongside the HTML-table scraping in `probe/catalogue.py` — not a replacement (the column-role binding needs the table), but redundancy for a vendor restructuring a page in a way that breaks the parser silently.
- **An independent index for n8n's node set** (e.g., npm packages published under `n8n-nodes-*`) as a possible second source for n8n gap analysis, which is currently a permanent hole because no independent vendor documents n8n's nodes (§32). This wouldn't be as strong as a true second vendor, so it'd probably want its own weaker tier rather than being treated as equivalent to two disjoint AUTHORITATIVE sources — but it beats the current dead end.

One thing I'd ask the team directly rather than guess at: of "monitor the 68 decks," "cover new-model announcements," and "ingest student tickets," which is actually most valuable to their week-to-week workflow right now? All three are legitimate next milestones and nothing in the doc ranks them against each other — that's a product call, not an architecture one.