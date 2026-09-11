"""The weekly digest.

Written for someone deciding what to fix this sprint, so: regressions before
opportunities, severity before volume, and every claim shown with the URL and date it
came from. Unchanged findings are deliberately absent — they stay in the store. A
report that repeats last week's list stops being read by week three.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from miw.reporters import register
from miw.schema import Finding

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _loc_line(f: Finding) -> str:
    by_course: dict[str, list[str]] = {}
    for l in f.locations:
        # Workbook-declared locations carry no session number; label them by source
        # rather than crashing on a field Location does not have.
        tag = (f"s{l.session_no}" if l.session_no
               else ("workbook" if l.evidence_source.startswith("sheet") else "unit"))
        by_course.setdefault(l.course, [])
        if tag not in by_course[l.course]:
            by_course[l.course].append(tag)
    return "; ".join(f"**{c}** ({', '.join(t[:6])})" for c, t in by_course.items())


def _finding_block(f: Finding) -> list[str]:
    out = [f"#### {f.severity.upper()} · {f.signal} {f.signal_label} — {f.canonical_name}",
           "",
           f"{f.summary}",
           "",
           # Blast radius is a weighted count of where a dependency is USED, so a
           # topic gap has one of exactly zero and printing it says only "this number
           # does not apply here". Omitted rather than shown as 0.
           f"- **Affects:** {_loc_line(f)}"
           f"{f' · blast radius {f.blast_radius}' if f.blast_radius else ''}"
           f"{f' · {f.graded_locations} graded item(s)' if f.graded_locations else ''}",
           f"- **What to do:** {f.what_to_act or f.recommendation}",
           f"- **Why it matters:** {f.why_to_act}",
           f"- **When:** {f.when_to_act}"
           + (f" _(by {f.due_by})_" if f.due_by else "")]
    if f.note_source == "llm":
        # Say so, and say by what. A reviewer weighing a recommendation should know
        # whether the wording came from the deterministic template or from a model,
        # and reviewer precision is later broken out on exactly this field.
        via = f" via {f.note_provider}" if f.note_provider else ""
        model = f" ({f.note_model})" if f.note_model else ""
        out.append(f"- _Wording refined by a model{via}{model}; every fact above still "
                   f"comes from the cited evidence._")
    if f.screenshots_at_risk:
        out.append(f"- **Screenshots to re-check:** {f.screenshots_at_risk} image(s) in "
                   f"the affected unit(s) document this flow")
    if f.questions_executing or (f.questions_mentioning and f.signal in ("S4", "S7")):
        bits = []
        if f.questions_executing:
            bits.append(f"{f.questions_executing} that execute it (will break)")
        if f.questions_mentioning and f.signal in ("S4", "S7"):
            bits.append(f"{f.questions_mentioning} that only mention it")
        out.append(f"- **Assessment fallout:** {'; '.join(bits)}"
                   + (f" · e.g. `{'`, `'.join(f.question_ids[:3])}`"
                      if f.question_ids else ""))
    if f.probe_signals:
        out.append(f"- **Observed directly:** {', '.join(sorted(set(f.probe_signals)))}")
    for c in f.claims:
        if c.substantiating:
            out.append(f"- **Evidence** ({c.tier.name.lower()}, retrieved "
                       f"{c.retrieved_at[:10]}): [{c.source_url}]({c.source_url})")
            out.append(f"  > {c.quote[:280]}")
    for a in f.alternatives:
        # Filter on `.verified` to match `notes._fmt_alternatives`. The two surfaces
        # disagreed: the digest rendered every nomination while the LLM prompt saw only
        # verified ones, so a reviewer could read a candidate in the digest that the
        # system did not consider substantiated.
        if not a.verified:
            continue
        bits = [a.homepage or "homepage not yet verified"]
        if a.free_student_path:
            bits.append("free path confirmed")
        if a.signup_required:
            bits.append("signup required")
        if a.maturity_note:
            bits.append(a.maturity_note)
        how = {"vendor_named": "named by the vendor it replaces; not verified on its "
                               "own site",
               "self": "verified on its own site",
               "self+vendor": "verified on its own site and named by the vendor"}
        if a.evidence in how:
            bits.append(how[a.evidence])
        out.append(f"- **Alternative:** {a.name} — {', '.join(bits)}")
        # On its own line and explicitly labelled, because this is the one thing in
        # the digest a model asserted rather than a page stated.
        if a.opinion is not None:
            from miw.research.fit import render as _render_opinion
            line = _render_opinion(a.opinion)
            if line:
                out.append(f"  - {line}")
    out.append("")
    return out


@register("markdown")
def render(findings: Iterable[Finding], *, resolved: list[dict] | None = None,
           run_date: str = "", capability_note: str = "",
           inventory_size: int = 0, probed: int = 0,
           researched: int = 0, suppressed: int = 0, course: str = "",
           nominations: Optional[dict] = None) -> str:
    """The weekly digest. With `course`, the numbers are that course's share.

    A per-course digest is rendered from findings already PROJECTED onto that course,
    so every count is local. `_loc_line` already groups by course and degrades to a
    single group, so nothing there needs changing.
    """
    # The store holds every open finding; a digest reports what changed. An unchanged
    # finding stays on file and out of the report - a weekly report that repeats last
    # week's list stops being read by week three.
    all_open = list(findings)
    findings = sorted((f for f in all_open if f.diff_class != "unchanged"),
                      key=lambda f: (SEV_ORDER.get(f.severity, 9), -f.blast_radius))
    standing = len(all_open) - len(findings)
    regressions = [f for f in findings if f.kind_of_signal == "regression"]
    opportunities = [f for f in findings if f.kind_of_signal == "opportunity"]
    run_date = run_date or date.today().isoformat()

    improved = [f for f in findings if f.diff_class == "improved"]
    L = [f"# Curriculum drift digest — {course} — {run_date}" if course
         else f"# Curriculum drift digest — {run_date}", ""]
    if course:
        L += [f"_Scoped to **{course}**. Every count below is this course's share; a "
              f"dependency shared with another course is reported there too, with its "
              f"own numbers. The all-courses roll-up is `digest_{run_date}.md`._", ""]
    if not findings:
        L += ["**No new or worsened findings this week.**", ""]
    else:
        # The three kinds of opportunity are three different claims and must not share
        # a sentence. S10 says a tool we teach has been overtaken — a fit judgement.
        # S11 says a topic is missing from the outline altogether. S12 says only that a
        # vendor we already use has added something; it deliberately never claims the
        # new thing is better, because nothing in it measures that.
        #
        # Counting S12 under S10's wording was a real defect: the header asserted "still
        # right, no longer best" about a finding whose own recommendation says "consider
        # whether". A summary line must not claim more than the findings it summarises.
        by_signal = {"S10": [], "S11": [], "S12": []}
        for f in opportunities:
            by_signal.setdefault(f.signal, []).append(f)
        bits = [f"**{len(regressions)} fix(es)** — something we teach is now wrong."]
        if by_signal["S10"]:
            bits.append(f"**{len(by_signal['S10'])} better option(s)** — still right, "
                        f"no longer best.")
        if by_signal["S11"]:
            bits.append(f"**{len(by_signal['S11'])} topic gap(s)** — documented by two "
                        f"independent vendors and in no session's outline.")
        if by_signal["S12"]:
            bits.append(f"**{len(by_signal['S12'])} newer option(s)** — a vendor we "
                        f"already use has added something since the last check.")
        if opportunities:
            bits.append("Those are decisions for the next cycle; nobody is blocked by "
                        "them.")
        L += [" ".join(bits), ""]
    L += [f"_{inventory_size} dependencies{' in this course' if course else ''} "
          f"inventoried · {probed} probed · "
          f"{researched} researched · {max(standing, suppressed)} unchanged finding(s) "
          f"still open and not repeated here_", ""]
    if nominations and nominations.get("total"):
        # Refutations are reported, never dropped. A nomination that failed is the only
        # way to tell "we looked and found nothing" apart from "nothing looked", and a
        # refutation rate that falls to zero is a suspicious signal, not a good one.
        n = nominations
        bits = [f"{n['total']} replacement candidate(s) considered",
                f"{n.get('verified', 0)} verified",
                f"{n.get('refuted', 0)} refuted"]
        if n.get("blocked"):
            bits.append(f"{n['blocked']} unverifiable (host alive but would not serve "
                        f"us — not treated as absent)")
        if n.get("rejected"):
            bits.append(f"{n['rejected']} rejected on policy")
        L += [f"_Discovery: {' · '.join(bits)}._", ""]
        for line in (n.get("examples") or [])[:4]:
            L += [f"_  refuted — {line}_"]
        if n.get("examples"):
            L += [""]
    if capability_note:
        L += [f"_Capabilities: {capability_note}_", ""]
    L += ["---", ""]

    if regressions:
        L += ["## Fixes — something we teach is now wrong", ""]
        for f in regressions:
            L += _finding_block(f)
    if opportunities:
        L += ["## Changes — something exists that we could teach", ""]
        for f in opportunities:
            L += _finding_block(f)
    if improved:
        L += ["## Improved but still open", "",
              "_Severity fell since the last run - worth knowing, still unresolved._", ""]
        for f in improved:
            L.append(f"- {f.signal} {f.canonical_name}: now {f.severity}")
        L.append("")
    if resolved:
        L += ["## Resolved since last run", ""]
        for r in resolved:
            L.append(f"- {r.get('signal')} on dependency `{r.get('dep_id')}` no longer "
                     f"reproduces (first raised {str(r.get('first_raised'))[:10]})")
        L.append("")

    L += ["---", "",
          "Every claim above carries the official URL and the date it was fetched. "
          "Claims that could not be sourced to a tool's own pages, or to a canonical "
          "registry, were dropped rather than reported.", ""]
    return "\n".join(L)
