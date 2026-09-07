"""The weekly digest.

Written for someone deciding what to fix this sprint, so: regressions before
opportunities, severity before volume, and every claim shown with the URL and date it
came from. Unchanged findings are deliberately absent — they stay in the store. A
report that repeats last week's list stops being read by week three.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

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
           f"- **Affects:** {_loc_line(f)} · blast radius {f.blast_radius}"
           f"{f' · {f.graded_locations} graded item(s)' if f.graded_locations else ''}",
           f"- **What to do:** {f.what_to_act or f.recommendation}",
           f"- **Why it matters:** {f.why_to_act}",
           f"- **When:** {f.when_to_act}"]
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
        bits = [a.homepage or "no homepage"]
        if a.free_student_path:
            bits.append("free path confirmed")
        if a.signup_required:
            bits.append("signup required")
        if a.maturity_note:
            bits.append(a.maturity_note)
        out.append(f"- **Alternative:** {a.name} — {', '.join(bits)}")
    out.append("")
    return out


@register("markdown")
def render(findings: Iterable[Finding], *, resolved: list[dict] | None = None,
           run_date: str = "", capability_note: str = "",
           inventory_size: int = 0, probed: int = 0,
           researched: int = 0, suppressed: int = 0) -> str:
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
    L = [f"# Curriculum drift digest — {run_date}", ""]
    if not findings:
        L += ["**No new or worsened findings this week.**", ""]
    else:
        L += [f"**{len(regressions)} regression(s)** — something we teach is now wrong. "
              f"**{len(opportunities)} opportunity(ies)** — still right, no longer best.",
              ""]
    L += [f"_{inventory_size} dependencies inventoried · {probed} probed · "
          f"{researched} researched · {max(standing, suppressed)} unchanged finding(s) "
          f"still open and not repeated here_", ""]
    if capability_note:
        L += [f"_Capabilities: {capability_note}_", ""]
    L += ["---", ""]

    if regressions:
        L += ["## Regressions", ""]
        for f in regressions:
            L += _finding_block(f)
    if opportunities:
        L += ["## Opportunities", ""]
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
