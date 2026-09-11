"""Local UI for MIW: run scoped audits, read the findings, triage them.

Three things a browser is genuinely better at than a terminal:

* **Run an audit against a chosen scope** — a course, a session range, a watch tier.
  A full sweep takes about an hour because probing is throttled to 1.5s per domain; one
  course's critical dependencies take a minute or two, which is what makes an
  on-demand run worth having. Runs go through `miw.api.jobs`, which queues them one at
  a time (two concurrent runs would double our request rate against the same vendors)
  and executes each stage as `python3 main.py <stage> --course ...` — the same command
  cron runs, so there is one execution path rather than two.
* **Read the week's findings** with evidence, affected sessions and the act/why/when
  note side by side.
* **Triage them** — accept, reject with a reason, or correct any one of the three note
  dimensions. Decisions go through `miw.triage`, the same path as the CLI, so a
  decision made here suppresses and teaches exactly as one made on the command line.

The prior Curriculum Gap Analyzer's own notes record the trap here: agent runs
dispatched with FastAPI `BackgroundTasks` and no queue, where "a server restart loses
in-flight runs". Jobs here live in SQLite and anything left `running` at startup is
marked **interrupted**, never `done` — presenting a partial audit as a complete one is
the failure that would actually mislead someone.

It binds to 127.0.0.1 by default and has **no authentication**, because it reads and
writes the operator's own local state. Do not expose it to a network.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="MIW — Curriculum Drift Watch", docs_url="/api/docs")


@app.on_event("startup")
def _startup() -> None:
    from miw.api.jobs import runner
    n = runner().recover_interrupted()
    if n:
        print(f"  marked {n} run(s) from a previous process as interrupted")


def _latest(pattern: str) -> Optional[Path]:
    files = sorted(OUT.glob(pattern))
    return files[-1] if files else None


def _read(pattern: str) -> dict:
    p = _latest(pattern)
    if not p:
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# ------------------------------------------------------- per-course projection

def _inventory_deps() -> dict:
    """The inventory as Dependency objects, keyed by dep_id.

    The projection MUST join back to these rather than read `Finding.locations`, which
    `score.py` truncates to 12 - projecting off the finding understates the busiest
    course (measured: 9 locations instead of 15).
    """
    from miw.schema import Dependency, Location
    out = {}
    for d in _read("inventory.json").get("dependencies", []):
        locs = [Location(**{k: v for k, v in l.items()
                            if k in Location.__dataclass_fields__})
                for l in d.get("locations", [])]
        out[d["dep_id"]] = Dependency(
            **{k: v for k, v in d.items()
               if k in Dependency.__dataclass_fields__ and k != "locations"},
            locations=locs)
    return out


def _resolve_course(course: str) -> str:
    """A slug or a title -> the title. "" means every course."""
    from miw.scope import resolve_courses
    got = resolve_courses([course]) if course else set()
    return next(iter(got), "") if got else ""


def _course_dep_ids(course: str, deps: dict) -> set:
    return {i for i, d in deps.items() if any(l.course == course for l in d.locations)}


def _touches_course(row: dict, course: str, ids: set) -> bool:
    """Does this finding belong on `course`'s page?

    Normally the answer comes from the inventory: a dependency's locations say which
    courses reference it. A TOPIC GAP (S11) has no inventory entry by design - the
    inventory records what the curriculum uses, and a topic we do not teach is
    precisely not that - so filtering on inventory membership alone silently dropped
    every gap finding from every course page while leaving it on the roll-up. A gap
    carries its own `courses`, populated from the sessions it was placed in, so that
    is what answers the question for it.
    """
    return row.get("dep_id") in ids or course in (row.get("courses") or [])


def _project_rows(rows: list, course: str) -> list:
    """Project raw finding dicts onto one course, recomputing every count."""
    from miw.analyse.project import project_all
    from miw.schema import Finding, to_jsonable
    deps = _inventory_deps()
    objs = []
    for r in rows:
        f = Finding(**{k: v for k, v in r.items()
                       if k in Finding.__dataclass_fields__
                       and k not in ("locations", "claims", "alternatives")})
        f.locations, f.claims, f.alternatives = [], [], []
        f._raw = r                                    # carry the original for the UI
        objs.append(f)
    out = []
    for p in project_all(objs, deps, course):
        raw = dict(next((o._raw for o in objs if o.finding_id == p.finding_id), {}))
        raw.update(to_jsonable(p))
        # `locations`/`claims`/`alternatives` were emptied to rebuild cheaply, so take
        # the projected locations from the projection and keep the original evidence.
        raw["locations"] = to_jsonable(p.locations)
        out.append(raw)
    return out


# ----------------------------------------------------------------- summary

@app.get("/api/summary")
def summary(course: str = "") -> dict:
    """The header tiles. With `course` (slug or title) every count is that course's.

    Anything that genuinely has no course dimension - reviewer precision, the LLM
    provider, capabilities - stays global and is labelled `global_only` so the UI can
    say so rather than implying it was filtered.
    """
    from config import settings
    from miw.llm import available_provider, budget_state, provider_status
    from miw.state import State

    inv = _read("inventory.json")
    findings = _read("findings_*.json")
    probe = _read("probe_*.json")

    title = _resolve_course(course)
    deps = inv.get("dependencies", [])
    rows = findings.get("findings", [])
    coverage = findings.get("coverage", {})
    probe_results = probe.get("results", [])
    # Recounted from the rows rather than read from `probe["counts"]`, which is a meta
    # dict written by whichever run touched the file last (`out = {**prev, **meta}`) -
    # so a 4-dependency run's counts were labelling a 42-row artifact.
    probe_counts: dict[str, int] = {}
    for r in probe_results:
        probe_counts[r.get("status", "?")] = probe_counts.get(r.get("status", "?"), 0) + 1

    if title:
        deps = [d for d in deps
                if any(l.get("course") == title for l in d.get("locations", []))]
        ids = {d["dep_id"] for d in deps}
        rows = _project_rows([r for r in rows if r.get("dep_id") in ids], title)
        # `coverage` must be narrowed BEFORE any freshness figure is derived, or a PSE
        # page reports Intro's staleness.
        coverage = {k: v for k, v in coverage.items() if k in ids}
        probe_results = [r for r in probe_results if r.get("dep_id") in ids]
        probe_counts = {}
        for r in probe_results:
            probe_counts[r.get("status", "?")] = probe_counts.get(r.get("status", "?"), 0) + 1

    with_authority = sum(1 for d in deps if d.get("official_domains") or
                         d.get("registry") in ("pypi", "npm"))
    by_sev: dict[str, int] = {}
    for f in rows:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    by_kind: dict[str, int] = {}
    for d in deps:
        by_kind[d.get("kind", "?")] = by_kind.get(d.get("kind", "?"), 0) + 1

    state = State()
    precision = state.precision_stats()
    state.close()

    fp = _latest("findings_*.json")
    return {
        "run_date": findings.get("analysed_at") or inv.get("generated_at") or "",
        "findings_file": fp.name if fp else "",
        "dependencies": len(deps),
        "with_authority": with_authority,
        "without_authority": len(deps) - with_authority,
        "by_kind": by_kind if title else inv.get("stats", {}).get("by_kind", {}),
        "watch_tiers": _tiers(deps),
        "findings_total": len(rows),
        "findings_by_severity": by_sev,
        # Per course this must be recounted: the artifact's own figure is the global
        # one, and a page listing 3 standing findings that claims 5 were suppressed is
        # a contradiction the reader has to resolve for us.
        "suppressed_unchanged": (
            sum(1 for r in rows if r.get("diff_class") == "unchanged") if title
            else findings.get("suppressed_unchanged", 0)),
        "suppressed_by_reviewer": findings.get("suppressed_by_reviewer", []),
        "resolved": findings.get("resolved", []),
        "probe_counts": probe_counts,
        "probed": len(probe_results),
        "coverage_tracked": len(coverage),
        "course": title,
        "course_slug": (__import__("miw.scope", fromlist=["slug_of"]).slug_of(title)
                        if title else ""),
        # Genuinely course-less, and said so rather than implied to be filtered.
        "global_only": ["precision", "llm_provider", "llm_budget", "llm",
                        "capabilities", "resolved"],
        "precision": precision,
        "capabilities": settings.capability_note(),
        # Kept exactly as they are: the UI reads both keys today. `llm` is the richer
        # channel (which providers are configured, and why the others are not).
        "llm_provider": available_provider(),
        "llm_budget": budget_state(),
        "llm": provider_status(),
    }


def _tiers(deps: list) -> dict:
    out: dict[str, int] = {}
    for d in deps:
        out[d.get("watch_tier", "?")] = out.get(d.get("watch_tier", "?"), 0) + 1
    return out


# ---------------------------------------------------------------- findings

@app.get("/api/findings")
def findings(course: str = "") -> dict:
    """Findings, optionally projected onto one course.

    Projected, not filtered: a finding shared by two courses carries its own counts per
    course. `llama-3.3-70b-versatile` is blast radius 65 globally and 12 in Intro to
    Gen AI, so a page that filtered without recomputing would overstate by 5.4x.
    """
    from miw import triage
    from miw.state import State

    data = _read("findings_*.json")
    rows = data.get("findings", [])
    title = _resolve_course(course)
    coverage = data.get("coverage", {})
    if title:
        deps = _inventory_deps()
        ids = _course_dep_ids(title, deps)
        rows = [r for r in rows if _touches_course(r, title, ids)]
        coverage = {k: v for k, v in coverage.items() if k in ids}
    standing_raw = [r for r in rows if r.get("diff_class") == "unchanged"]
    rows = [r for r in rows if r.get("diff_class") != "unchanged"]
    if title:
        rows = _project_rows(rows, title)
        standing_raw = _project_rows(standing_raw, title)
    standing = standing_raw
    state = State()
    for f in rows:
        d = state.latest_decision(f["finding_id"])
        f["_decision"] = d["verdict"] if d else None
        f["_decision_reason"] = (d["reason"] if d else "") or ""
        f["_split"] = triage.split_of(f["finding_id"])
    state.close()
    return {"run_date": data.get("analysed_at", ""), "findings": rows,
            "course": title,
            # `finding_id` is carried because a standing finding is still openable:
            # on a re-run with no news EVERY finding is `unchanged`, so a standing list
            # without ids makes the detail panel unreachable exactly when it is the
            # only list on the page.
            "standing": [{"finding_id": r["finding_id"], "dep_id": r.get("dep_id", ""),
                          "canonical_name": r["canonical_name"], "signal": r["signal"],
                          "severity": r["severity"], "summary": r.get("summary", ""),
                          "local_severity": r.get("local_severity", ""),
                          "signal_label": r.get("signal_label", ""),
                          "graded_locations": r.get("graded_locations", 0),
                          "questions_executing": r.get("questions_executing", 0),
                          "local_locations": r.get("local_locations", 0),
                          "blast_radius": r.get("blast_radius", 0)}
                         for r in standing],
            "resolved": data.get("resolved", []),
            "coverage": coverage,
            # A triage decision is recorded against a finding_id, which has no course
            # in it - so accepting or rejecting from a course page affects every course
            # that shares the finding. The UI must say this at the point of the click.
            "triage_is_global": True,
            "suppressed_by_reviewer": data.get("suppressed_by_reviewer", [])}


# ------------------------------------------------------ one finding, in full

def _deep_link(loc: dict, slug: str) -> str:
    """A link back to the live unit, or "" when no pattern is configured.

    Returns "" rather than a best-effort URL: a guessed link that 404s cannot be told
    apart from a unit that moved, and the reviewer would blame the course rather than
    the guess. `config.settings.PLATFORM_UNIT_URL` is the single place to add it.
    """
    from config.settings import PLATFORM_UNIT_URL
    if not PLATFORM_UNIT_URL:
        return ""
    try:
        return PLATFORM_UNIT_URL.format(
            course_slug=slug, unit_id=loc.get("unit_id", ""),
            content_id=loc.get("content_id", ""),
            session_no=loc.get("session_no") or "",
            topic_name=loc.get("topic_name", ""))
    except (KeyError, IndexError):
        # A pattern naming a placeholder we do not have is a configuration error, not a
        # reason to render a half-built URL.
        return ""


def _self_referring_urls(dep) -> list:
    """This dependency's referenced URLs that are actually ABOUT this dependency.

    Measured on the live inventory: `@n8n/n8n-nodes-langchain.agent` carries 31
    referenced URLs, 30 of them unique to it - including
    `.../app-nodes/n8n-nodes-base.gmail`, while the real `n8n-nodes-base.gmail`
    dependency carries none. Whichever node the extractor recorded first in a unit
    collected every docs link in that content.

    That is an extraction defect, not a display one, but the panel must not amplify it:
    highlighting a Gmail docs link under an Agent finding sends the reviewer to the
    wrong line. So a URL earns the right to highlight only when the dependency's own
    name appears in it - the same "is this page about the subject" test
    `research.official._site_is_the_product` applies to authority.
    """
    from urllib.parse import urlparse
    # `@n8n/n8n-nodes-langchain.agent` -> `n8n-nodes-langchain.agent`, which is what
    # the docs URL actually contains.
    name = dep.canonical_name.lower()
    tail = name.split("/")[-1] if name.startswith("@") else name
    out = []
    for u in dep.referenced_urls:
        low = u.lower()
        if not urlparse(u).path.strip("/"):
            continue                       # a root URL discriminates nothing
        if tail and tail in low:
            out.append(u.rstrip("/"))
    return out


def _as_int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _session_groups(locs: list, graded: tuple) -> list:
    """Occurrences per session, over the whole list, with no excerpt resolved.

    A session is the unit of curriculum work - you fix a deck and its questions
    together - so it is the grouping a reviewer acts on. Workbook declarations are their
    own group because they carry no path into the export and so can never show an
    excerpt; saying that once per group beats repeating it on every row.
    """
    order: list = []
    by: dict = {}
    for l in locs:
        key = l.get("session_no")
        if key not in by:
            by[key] = {"session_no": key,
                       "label": (f"Session {key}" if key else "Workbook declarations"),
                       "count": 0, "graded": 0, "resolvable": 0,
                       "object_types": {}, "units": []}
            order.append(key)
        g = by[key]
        g["count"] += 1
        if l.get("object_type") in graded:
            g["graded"] += 1
        # A `Book.xlsx::Sheet` path names no cell, so it has no excerpt to show.
        if "::" not in (l.get("field_path") or "") or (l.get("field_path") or "").count("::") >= 2:
            g["resolvable"] += 1
        ot = l.get("object_type") or "UNKNOWN"
        g["object_types"][ot] = g["object_types"].get(ot, 0) + 1
        un = l.get("unit_name") or ""
        if un and un not in g["units"]:
            g["units"].append(un)
    # Numbered sessions first, in order; the unnumbered group last.
    return [by[k] for k in sorted(order, key=lambda k: (k is None, k or 0))]


# Locations shown in the detail panel. Higher than the 12 `score.py` stores because
# this view exists precisely to answer "where else", but still bounded: one dependency
# reaches 776 n8n-workflow locations and resolving every excerpt would stall the panel.
DETAIL_LOCATION_CAP = 40


def _topic_block(row: dict) -> dict:
    """What to show in place of the dependency panel for a topic gap.

    Read from the `gaps_*.json` sidecar rather than the finding, because that is where
    the corroborating sources and their authority sets live - the same arrangement
    `main.py verify` uses to re-classify a topic claim without trusting the tier the
    finding records.
    """
    base = {"canonical_name": row.get("canonical_name", "")}
    if not str(row.get("dep_id", "")).startswith("topic:"):
        return {**base, "stale": True}
    side = _read("gaps_*.json")
    topic = next((t for t in (side.get("topics") or [])
                  if t.get("dep_id") == row.get("dep_id")), {})
    srcs = topic.get("sources") or []
    return {**base, "kind": "topic",
            "dep_id": row.get("dep_id", ""),
            "area_id": topic.get("area_id", ""),
            "area_title": topic.get("area_title", ""),
            "also_documented_as": topic.get("also_documented_as") or [],
            "corroboration": topic.get("corroboration"),
            "vendor": ", ".join(s.get("subject", "") for s in srcs),
            "docs_url": (srcs[0] or {}).get("url", "") if srcs else "",
            "homepage": "",
            "official_domains": sorted({d for s in srcs
                                        for d in (s.get("official_domains") or [])}),
            "referenced_urls": [s.get("url", "") for s in srcs],
            "placements": topic.get("placements") or []}


@app.get("/api/finding/{finding_id}")
def finding_detail(finding_id: str, course: str = "", session: str = "",
                   offset: int = 0) -> dict:
    """Everything needed to act on one finding: where, what source, what to change.

    The card answers "what broke". This answers the three questions a reviewer asks
    next, and each comes from a different place, so each is assembled separately:

    * **where** - every location for this course, joined back to the INVENTORY (the
      finding truncates at 12) and each resolved through `field_path` to the actual
      excerpt, with the matched term located so the UI can highlight it.
    * **what the source is** - the claims, with tier, retrieval date and the verbatim
      quote. Findings resting only on our own probe say so explicitly rather than
      showing an empty evidence list, which reads as unsourced.
    * **what to change** - the act/why/when triad plus the taught-vs-latest version
      pair, re-derived for this course by the projection.
    """
    from miw.schema import to_jsonable
    from miw.scope import slug_of

    data = _read("findings_*.json")
    rows = data.get("findings", [])
    row = next((r for r in rows if r.get("finding_id") == finding_id), None)
    if row is None:
        raise HTTPException(404, f"no finding {finding_id} in the latest run")

    deps = _inventory_deps()
    dep = deps.get(row.get("dep_id"))
    title = _resolve_course(course)

    # The finding as it applies here. Falls back to the global row when no course is
    # asked for, or when the dependency has been dropped by a later `extract`.
    projection = "global"
    shown = row
    if title and dep is not None:
        from miw.analyse.project import project_finding
        from miw.schema import Finding
        f = Finding(**{k: v for k, v in row.items()
                       if k in Finding.__dataclass_fields__
                       and k not in ("locations", "claims", "alternatives")})
        f.locations, f.claims, f.alternatives = [], [], []
        p = project_finding(f, dep, title)
        if p is not None:
            shown = {**row, **to_jsonable(p)}
            projection = "ok"
        else:
            projection = "not_in_course"
    elif title and dep is None:
        # Two different situations wearing one label. A dependency dropped by a later
        # `extract` really is unavailable and its figures really are global. A topic gap
        # has no inventory entry by construction, and its per-course figures are exact -
        # its locations ARE the sessions - so calling it stale would tell the reviewer
        # to re-run `extract`, which would change nothing.
        projection = "topic" if str(row.get("dep_id", "")).startswith("topic:") \
            else "unavailable"

    # --- where ---------------------------------------------------------------
    from miw.extract import locate
    if dep is not None:
        locs = [to_jsonable(l) for l in dep.locations
                if not title or l.course == title]
    else:
        locs = row.get("locations", [])
    # The name is not always what was matched: a `link:a_href` location was attributed
    # because a URL in the text matched one of this dependency's referenced URLs, and
    # searching for the name alone leaves 20 of this S4's 40 locations unhighlighted.
    # But a URL is WEAKER evidence that this line is the thing to edit, so it is a
    # fallback rather than a peer - see `locate.resolve`.
    #
    # Two kinds of term are excluded outright because they are shared, and a shared
    # term points the reviewer at the wrong line in the right file:
    #   * `registry_id` - for an n8n node it is the package `@n8n/n8n-nodes-langchain`,
    #     carried by 20 dependencies, so it highlighted the package prefix of a
    #     DIFFERENT node's identifier;
    #   * a referenced URL that is not about this dependency - the agent node's
    #     `referenced_urls` contains every docs link in its workflows, Gmail's
    #     included, so `_self_referring_urls` keeps only the ones naming it.
    terms = [row.get("canonical_name", "")]
    url_terms: list[str] = []
    if dep is not None:
        terms = [dep.canonical_name, *dep.aliases]
        url_terms = _self_referring_urls(dep)
    # Graded first, then by session: the graded ones are where a student actually
    # executes the taught step, so they are the ones a reviewer must see without
    # scrolling.
    graded = ("CODING_QUESTIONS", "OBJECTIVE_QUESTIONS")
    locs.sort(key=lambda l: (l.get("object_type") not in graded,
                             l.get("session_no") or 999, l.get("unit_name") or ""))
    total_locations = len(locs)

    # Grouping is computed over the FULL location list and costs nothing, because a
    # group needs no excerpt - only a session number and an object type. That is what
    # makes every occurrence reachable without ever resolving all of them: this S4 has
    # 776 locations, and resolving 776 excerpts would stall the panel, which is why
    # `DETAIL_LOCATION_CAP` exists. Grouped, the reviewer picks a session and only that
    # session's excerpts are read.
    groups = _session_groups(locs, graded)

    # `session` selects one group to resolve; otherwise the first page. Both are capped.
    if session:
        want = None if session in ("none", "workbook") else _as_int(session)
        chosen = [l for l in locs if l.get("session_no") == want]
    else:
        chosen = locs[max(offset, 0):]
    # NB: not `shown` — that name already holds the projected finding dict earlier in
    # this function, and reusing it made the response 500 on `shown.get(...)`.
    page = chosen[:DETAIL_LOCATION_CAP]

    where = []
    for l in page:
        cslug = slug_of(l.get("course", "")) or (slug_of(title) if title else "")
        where.append({**l,
                      "course_slug": cslug,
                      "deep_link": _deep_link(l, cslug),
                      "excerpt": locate.resolve(cslug, l.get("field_path", ""),
                                               terms, url_terms)})

    # --- what the source is --------------------------------------------------
    claims = row.get("claims", []) or []
    for a in row.get("alternatives", []) or []:
        for c in a.get("claims", []) or []:
            claims.append({**c, "about_alternative": a.get("name", "")})

    return {
        "finding": shown,
        "projection": projection,
        "course": title,
        "course_slug": slug_of(title) if title else "",
        "dependency": ({
            "canonical_name": dep.canonical_name, "kind": dep.kind,
            "dep_id": dep.dep_id, "homepage": dep.homepage, "docs_url": dep.docs_url,
            "vendor": dep.vendor, "registry": dep.registry,
            "registry_id": dep.registry_id, "watch_tier": dep.watch_tier,
            "taught_version": dep.taught_version or "",
            "official_domains": dep.official_domains,
            "referenced_urls": dep.referenced_urls[:8],
        } if dep is not None else _topic_block(row)),
        "where": where,
        "groups": groups,
        "locations_total": total_locations,
        "locations_shown": len(where),
        "session": session,
        "offset": max(offset, 0),
        "has_more": (not session) and (max(offset, 0) + len(where)) < total_locations,
        "page_size": DETAIL_LOCATION_CAP,
        "evidence": claims,
        # Said explicitly, because an empty evidence list looks like an unsourced
        # finding when in fact our own probe is the observation.
        "probe_only": not claims and bool(row.get("probe_signals")),
        "probe_signals": row.get("probe_signals", []),
        "change": {
            "what_to_act": shown.get("what_to_act", ""),
            "why_to_act": shown.get("why_to_act", ""),
            "when_to_act": shown.get("when_to_act", ""),
            "due_by": shown.get("due_by", ""),
            "recommendation": shown.get("recommendation", ""),
            "note_source": shown.get("note_source", ""),
            "taught_version": (dep.taught_version or "") if dep is not None else "",
            "latest_version": row.get("latest_version", ""),
        },
        "alternatives": row.get("alternatives", []) or [],
        "also_in": shown.get("also_in", []),
        "deep_links_configured": bool(_deep_link({"unit_id": "x"}, "y")),
    }


@app.get("/api/agent-findings")
def agent_findings(course: str = "") -> dict:
    """Findings produced by an agent run and suspended at its review gate.

    Deliberately a SEPARATE endpoint from `/api/findings`. Pipeline findings have been
    through `analyse`: two-run confirmation, week-over-week diffing, reviewer
    suppression. An agent finding has been through none of that — it is one run's
    observation awaiting a human — so presenting the two in one list would overstate the
    agent's and understate the pipeline's.

    Without this the agent's output was invisible: it lived only in
    `state/agent_checkpoints.db`, and every UI surface reads `out/`.
    """
    data = _read("agent_findings_*.json")
    runs = data.get("runs", [])
    title = _resolve_course(course)
    if title:
        # An agent run scores every course the dependency touches; a course page shows
        # only its own row, with that course's numbers.
        scoped = []
        for r in runs:
            mine = [f for f in r.get("findings", []) if f.get("course") == title]
            if mine:
                scoped.append({**r, "findings": mine})
        runs = scoped
    pending = [r for r in runs if r.get("awaiting_review")]
    return {"written_at": data.get("written_at", ""), "runs": runs,
            "pending": len(pending), "course": title}


class AgentReviewIn(BaseModel):
    verdict: str = Field(pattern="^(accepted|rejected)$")
    reason: str = ""
    reviewer: str = "gen-ai-content"


@app.post("/api/agent-runs/{thread_id}/review")
def review_agent_run(thread_id: str, body: AgentReviewIn) -> dict:
    """Resume a suspended agent run with a reviewer's verdict.

    This is a real `Command(resume=...)` against the run's durable checkpoint, so the
    graph continues from its review gate rather than re-reading the vendor's pages. The
    review node then rewrites `out/agent_findings_<date>.json` with the verdict, which
    is what makes the decision visible to the UI.

    A fresh SQLite connection per request: the checkpointer is opened with
    `check_same_thread=False` and FastAPI serves on a threadpool, so sharing one
    connection across requests is how you get intermittent "objects created in a
    thread can only be used in that same thread" failures.

    **Deliberately NOT wired into `miw.triage`.** Triage decisions drive the precision
    metric and reviewer suppression, and both are statements about the *pipeline*, whose
    findings carry two-run confirmation and week-over-week diffing. An agent run has
    neither. Feeding these in would quietly change what `precision_stats()` measures.
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.types import Command

        from miw.agents.impact_agent import CHECKPOINT_DB, build_impact_graph
    except ImportError:
        raise HTTPException(503, "the agent graphs need langgraph: "
                                 "pip install -r requirements-optional.txt")

    if body.verdict == "rejected" and not body.reason.strip():
        raise HTTPException(400, "a rejection needs a reason — the reason is what a "
                                 "later reader needs to know why this was not acted on")
    if not CHECKPOINT_DB.exists():
        raise HTTPException(404, "no agent runs have been recorded yet")

    import sqlite3
    conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    try:
        app_g = build_impact_graph().compile(checkpointer=SqliteSaver(conn))
        cfg = {"configurable": {"thread_id": thread_id}}
        snap = app_g.get_state(cfg)
        # `get_state` echoes the config back for a thread that does not exist, so
        # `snap.config` is truthy either way — checking it reported an unknown thread as
        # "already reviewed", which is a different and misleading thing. Stored VALUES
        # are what distinguish a real run from a typo'd id.
        if not snap.values:
            raise HTTPException(404, f"no agent run on thread {thread_id}")
        if not snap.next:
            # Already resumed. Re-resuming would run the gate again and overwrite a
            # decision someone already made.
            raise HTTPException(409, "this run has already been reviewed")
        out = app_g.invoke(
            Command(resume={"decision": body.verdict, "reason": body.reason.strip(),
                            "reviewer": body.reviewer}), config=cfg)
    finally:
        conn.close()

    return {"ok": True, "thread_id": thread_id, "verdict": out.get("review", ""),
            "feeds_triage": False,
            "note": "recorded on the agent artifact; the pipeline precision metric is "
                    "unaffected"}


@app.get("/api/digest", response_class=PlainTextResponse)
def digest(course: str = "") -> str:
    """The roll-up, or one course's digest.

    A missing per-course digest says so. Falling back to the roll-up would put other
    courses' findings under a per-course heading, which is the mixing this is meant to
    end. Per-course digests live in `out/courses/<slug>/` rather than `out/` because
    `_latest()` sorts lexicographically: a top-level `digest_pse_<date>.md` sorts AFTER
    `digest_<date>.md` and would silently become "the" digest.
    """
    from miw.scope import slug_of
    title = _resolve_course(course)
    if not title:
        p = _latest("digest_*.md")
        return p.read_text() if p else "No digest yet. Run: python3 main.py report"
    files = sorted((OUT / "courses" / slug_of(title)).glob("digest_*.md"))
    if files:
        return files[-1].read_text()
    return (f"No digest for {title} yet.\n\n"
            f"Run:  python3 main.py report --course \"{title}\"\n\n"
            f"(The all-courses roll-up is not shown here on purpose: it contains other "
            f"courses' findings, and this page is about {title}.)")


# --------------------------------------------------------------- inventory

@app.get("/api/inventory")
def inventory(q: str = "", kind: str = "", tier: str = "", authority: str = "",
              course: str = "", sessions: str = "", tiers: str = "",
              limit: int = 100, offset: int = 0, count_only: bool = False) -> dict:
    """Browse the inventory, and preview what a run scope would select.

    `course`/`sessions`/`tiers` mirror the run-scope flags so the picker can tell
    someone how many dependencies they are about to probe *before* they start a run
    that is throttled to 1.5s per domain.
    """
    from miw.scope import parse_sessions

    deps = _read("inventory.json").get("dependencies", [])
    ql = q.strip().lower()
    from miw.scope import resolve_courses
    # Lenient: `?course=pse` and `?course=PSE` both work, so a URL can carry the slug
    # while the existing scope-preview widget keeps sending titles unchanged.
    courses = resolve_courses(course.split("|"))
    tierset = {t.strip() for t in tiers.split(",") if t.strip()}
    try:
        sessionset = parse_sessions(sessions)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    def in_scope(d: dict) -> bool:
        if tierset and d.get("watch_tier") not in tierset:
            return False
        if not (courses or sessionset):
            return True
        for l in d.get("locations") or []:
            if courses and l.get("course") not in courses:
                continue
            if sessionset and l.get("session_no") not in sessionset:
                continue
            return True
        return False

    def keep(d: dict) -> bool:
        if not in_scope(d):
            return False
        if kind and d.get("kind") != kind:
            return False
        if tier and d.get("watch_tier") != tier:
            return False
        has_auth = bool(d.get("official_domains")) or d.get("registry") in ("pypi", "npm")
        if authority == "yes" and not has_auth:
            return False
        if authority == "no" and has_auth:
            return False
        if ql:
            blob = " ".join([d.get("canonical_name", ""), *(d.get("aliases") or []),
                             *(d.get("official_domains") or [])]).lower()
            if ql not in blob:
                return False
        return True

    rows = [d for d in deps if keep(d)]
    if count_only:
        by_tier: dict[str, int] = {}
        for d in rows:
            by_tier[d.get("watch_tier", "?")] = by_tier.get(d.get("watch_tier", "?"), 0) + 1
        urls = sum(len(d.get("referenced_urls") or []) for d in rows)
        return {"total": len(rows), "by_tier": by_tier, "referenced_urls": urls,
                "rows": []}
    slim = []
    for d in rows[offset:offset + limit]:
        locs = d.get("locations") or []
        slim.append({
            "dep_id": d["dep_id"], "kind": d["kind"],
            "canonical_name": d["canonical_name"],
            "watch_tier": d.get("watch_tier"),
            "taught_version": d.get("taught_version"),
            "official_domains": d.get("official_domains") or [],
            "registry": d.get("registry") or "",
            "locations": len(locs),
            "courses": sorted({l["course"] for l in locs}),
            "referenced_urls": (d.get("referenced_urls") or [])[:3],
            "notes": d.get("notes") or "",
        })
    return {"total": len(rows), "offset": offset, "limit": limit, "rows": slim}


# ------------------------------------------------------------------ triage

class TriageIn(BaseModel):
    finding_id: str
    verdict: str = Field(pattern="^(accepted|rejected)$")
    reason: str = ""
    corrected_what: str = ""
    corrected_why: str = ""
    corrected_when: str = ""
    reviewer: str = "gen-ai-content"


@app.post("/api/triage")
def triage_finding(body: TriageIn) -> dict:
    from miw import triage
    from miw.schema import utcnow
    from miw.state import State

    rows = _read("findings_*.json").get("findings", [])
    match = next((f for f in rows if f["finding_id"] == body.finding_id), None)
    if match is None:
        raise HTTPException(404, f"no finding {body.finding_id} in the current run")
    if body.verdict == "rejected" and not body.reason.strip():
        raise HTTPException(400, "a rejection needs a reason — the reason is what the "
                                 "system learns from")

    state = State()
    triage.record(state, finding_id=match["finding_id"], dep_id=match["dep_id"],
                  signal=match["signal"], verdict=body.verdict,
                  fingerprint=triage.fingerprint_of_raw(match),
                  reason=body.reason.strip(),
                  corrected_what=body.corrected_what.strip(),
                  corrected_why=body.corrected_why.strip(),
                  corrected_when=body.corrected_when.strip(),
                  reviewer=body.reviewer, now=utcnow())
    stats = state.precision_stats()
    state.close()
    return {"ok": True, "verdict": body.verdict, "precision": stats,
            "learning": triage.split_of(match["finding_id"]) == "learn",
            "suppresses_recurrence": body.verdict == "rejected"}


@app.get("/api/feedback", response_class=PlainTextResponse)
def feedback() -> str:
    from miw import triage
    p = triage.FEEDBACK_FILE
    return p.read_text() if p.exists() else "No reviewer corrections recorded yet."


# ------------------------------------------------------------------ verify

@app.get("/api/llm")
def llm_options() -> dict:
    """What the run form's model chooser can offer, and what each choice costs.

    The price table is deliberately short — three Anthropic ids — and anything outside
    it reports `priced: false`, because for an unpriced model the daily SPEND cap stops
    applying and only the call cap remains. The UI says that at the point of choosing
    rather than leaving it to be discovered in a bill.
    """
    from miw.llm import (AUTO_ORDER, LOGICAL_MODELS, MAX_CALLS, MAX_SPEND_USD,
                         PRICE_USD_PER_MTOK, budget_state, provider_status,
                         resolve_model)

    st = provider_status()
    avail = {c["name"]: c for c in st.get("candidates", [])}
    providers = [{"name": n,
                  "available": bool(avail.get(n, {}).get("available")),
                  "why": avail.get(n, {}).get("why", "")}
                 for n in AUTO_ORDER]
    models = []
    for logical in LOGICAL_MODELS:
        ids = {p: resolve_model(logical, p) for p in AUTO_ORDER}
        # Priced per logical name: the three logical names map onto the three priced
        # ids, so "priced" is a property of the choice a user can actually make here.
        anth = ids["anthropic"]
        price = PRICE_USD_PER_MTOK.get(anth)
        models.append({"logical": logical, "ids": ids,
                       "priced": price is not None,
                       "usd_per_mtok_in": price[0] if price else None,
                       "usd_per_mtok_out": price[1] if price else None})
    return {"providers": providers, "models": models,
            "active": {"provider": st["provider"], "forced": st["forced"],
                       "model": st.get("model_logical", "")},
            "caps": {"calls": MAX_CALLS, "usd": MAX_SPEND_USD},
            "budget": budget_state()}


@app.get("/api/verify", response_class=PlainTextResponse)
def verify() -> str:
    """Run the trust-invariant audit and stream back exactly what the CLI prints."""
    proc = subprocess.run([sys.executable, "main.py", "verify"], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=120)
    return (proc.stdout + proc.stderr) or "(no output)"


# -------------------------------------------------------------------- runs

@app.get("/api/courses")
def courses() -> dict:
    """Courses and the session numbers seen in each, for the scope picker."""
    from miw.schema import Dependency, Location
    from miw.scope import course_map

    deps = []
    for d in _read("inventory.json").get("dependencies", []):
        locs = [Location(**l) for l in d.get("locations", [])]
        deps.append(Dependency(kind=d["kind"], canonical_name=d["canonical_name"],
                               watch_tier=d.get("watch_tier", "standard"),
                               locations=locs))
    from config.constants import COURSES
    from miw.scope import slug_of

    cmap = course_map(deps)
    kinds = sorted({d.kind for d in deps})

    # Badge counts so the overview page and the course switcher render in one request.
    findings_raw = _read("findings_*.json").get("findings", [])
    by_course: dict[str, list] = {}
    for f in findings_raw:
        for c in f.get("courses", []):
            by_course.setdefault(c, []).append(f)

    order = ["critical", "high", "medium", "low", "info"]
    rows = []
    for c, sessions in cmap.items():
        fs = by_course.get(c, [])
        sev: dict[str, int] = {}
        for f in fs:
            sev[f["severity"]] = sev.get(f["severity"], 0) + 1
        worst = next((s2 for s2 in order if sev.get(s2)), "")
        rows.append({
            "title": c, "slug": slug_of(c), "sessions": sessions,
            "dependencies": sum(1 for d in deps
                                if any(l.course == c for l in d.locations)),
            "findings_total": len(fs), "findings_by_severity": sev,
            "worst_severity": worst, "ingested": True,
        })

    # A course declared in config/constants.py but absent from the inventory has not
    # been ingested. It must render as "not ingested yet", never as an innocuous empty
    # page that reads as a healthy course.
    for slug, meta in COURSES.items():
        if meta["title"] not in cmap:
            rows.append({"title": meta["title"], "slug": slug, "sessions": [],
                         "dependencies": 0, "findings_total": 0,
                         "findings_by_severity": {}, "worst_severity": "",
                         "ingested": False})

    return {"courses": rows, "kinds": kinds,
            "tiers": ["critical", "standard", "mention-only"]}


# --------------------------------------------------------------- adding a course

STAGING = ROOT / "data" / ".staging"
MAX_UPLOAD = 64 * 1024 * 1024          # course exports run 5.8-9.5 MB


class AddCourseIn(BaseModel):
    title: str
    expect_sessions: Optional[int] = None
    export_token: str = ""
    workbook_token: str = ""


def _staged(token: str, kind: str) -> Optional[Path]:
    """Resolve a staging token, refusing anything that escapes the staging dir."""
    if not token or "/" in token or "\\" in token or token.startswith("."):
        return None
    p = (STAGING / token / kind).resolve()
    try:
        p.relative_to(STAGING.resolve())
    except ValueError:
        return None
    return p if p.exists() else None


@app.post("/api/courses/stage")
async def stage_upload(request: Request, kind: str = "export") -> dict:
    """Take one uploaded file as a RAW request body, streamed to disk.

    Deliberately not `UploadFile`: that needs `python-multipart`, which is absent, and
    FastAPI raises at *route-definition* time without it — so adding a multipart route
    would break importing the app entirely. This repo already has a scar from a silently
    skipped requirement, so the upload takes no new dependency at all.

    Streamed rather than `await request.body()` because an export is 6-10 MB and the cap
    has to be enforced as the bytes arrive, not after they are all in memory. A declared
    `Content-Length` is not trusted for the same reason.
    """
    if kind not in ("export", "workbook"):
        raise HTTPException(400, "kind must be 'export' or 'workbook'")
    import secrets
    token = secrets.token_hex(8)
    d = STAGING / token
    d.mkdir(parents=True, exist_ok=True)
    dest = d / kind
    size = 0
    try:
        with open(dest, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(
                        413, f"file exceeds {MAX_UPLOAD // (1024 * 1024)} MB")
                fh.write(chunk)
    except HTTPException:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        raise
    if not size:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, "empty upload")
    return {"token": token, "kind": kind, "bytes": size}


def _candidate(body: "AddCourseIn"):
    from miw.courses import Candidate
    export = _staged(body.export_token, "export")
    if export is None:
        raise HTTPException(400, "the course export was not staged — upload it first")
    book = _staged(body.workbook_token, "workbook") if body.workbook_token else None
    return Candidate(title=body.title, export_path=export,
                     expect_sessions=body.expect_sessions, workbook_path=book)


@app.post("/api/courses/validate")
def validate_course(body: AddCourseIn) -> dict:
    """Dry run: what would be registered, and what the export actually parsed to.

    The stats matter more than they look. `portal.read_course` never raises on shape
    surprises, so an export with the wrong field names yields 0 records and no error —
    returning `units`/`records`/`sessions` is the only way the UI can show that.
    """
    from miw.courses import validate
    v = validate(_candidate(body))
    return {"ok": v.ok, "slug": v.slug, "reasons": v.reasons, "warnings": v.warnings,
            "counted": v.counted, "source": v.source, "positional": v.positional,
            "stats": v.stats, "stopped_at": v.stopped_at}


@app.post("/api/courses")
def add_course(body: AddCourseIn) -> dict:
    """Validate then register. Nothing is written unless every check passes."""
    from miw.courses import commit, validate
    c = _candidate(body)
    v = validate(c)
    if not v.ok:
        raise HTTPException(400, {"reasons": v.reasons, "stopped_at": v.stopped_at,
                                  "stats": v.stats})
    row = commit(c, v)
    return {"ok": True, **row, "warnings": v.warnings,
            "next": "run ingest + extract to build its inventory"}


@app.get("/api/courses/registry")
def course_registry() -> dict:
    """The writable overlay, plus anything the loader refused and why."""
    from config.constants import COURSES
    from miw.courses import REGISTRY_PATH, load_overlay
    entries, reasons = load_overlay()
    return {"path": str(REGISTRY_PATH.relative_to(ROOT)),
            "runtime": entries,
            "declared": sorted(COURSES.declared()),
            "conflicts": COURSES.conflicts(),
            "rejected": reasons}


@app.delete("/api/courses/{slug}")
def remove_course(slug: str, purge: bool = False) -> dict:
    from miw.courses import unregister
    ok, why = unregister(slug, purge=purge)
    if not ok:
        raise HTTPException(400, why)
    return {"ok": True, "slug": slug, "purged": purge}


class RunIn(BaseModel):
    courses: list[str] = []
    sessions: str = ""
    tiers: list[str] = ["critical", "standard"]
    kinds: list[str] = []
    limit: Optional[int] = None
    # `research` included: it is what produces citations and replacement candidates,
    # and leaving it out of the default meant a UI run never triggered it at all - only
    # `run-weekly` did.
    stages: list[str] = ["probe", "research", "analyse", "report"]
    refine: bool = False
    label: str = ""
    # Per-run LLM choice. Empty means "whatever the environment already says", which is
    # `auto` unless MIW_LLM_PROVIDER is set — so the default behaviour is unchanged.
    llm_provider: str = ""
    llm_model: str = ""


@app.post("/api/runs")
def start_run(body: RunIn) -> dict:
    from miw.api.jobs import runner
    from miw.scope import parse_sessions, resolve_courses

    try:
        sessions = sorted(parse_sessions(body.sessions))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    # Slugs or titles, both accepted; `Scope.courses` stays a set of TITLES because
    # that is what `Location.course` holds and what `to_cli_args()` emits as --course.
    scope = {"courses": sorted(resolve_courses(body.courses)), "sessions": sessions,
             "tiers": body.tiers, "kinds": body.kinds, "limit": body.limit}
    r = runner()
    llm = ({"provider": body.llm_provider, "model": body.llm_model}
           if (body.llm_provider or body.llm_model) else None)
    run_id = r.submit(scope=scope, stages=body.stages, refine=body.refine,
                      label=body.label, llm=llm)
    return {"run_id": run_id, "queued_behind": 1 if r.active() else 0}


@app.get("/api/runs")
def list_runs(limit: int = 20, course: str = "") -> dict:
    """Run history, optionally only the runs that touched one course.

    An UNSCOPED sweep appears in every course's history, because it really did audit
    every course. A run scoped to one course appears only there.
    """
    from miw.api.jobs import runner
    from miw.scope import slug_of
    title = _resolve_course(course)
    r = runner()
    rows = r.list(limit, course_slug=slug_of(title) if title else "")
    out = []
    for x in rows:
        row = dict(x)
        # So a history row can say what it produced, not just that it finished.
        found = r.findings_for(row["run_id"])
        row["findings_count"] = len(found)
        row["worst_severity"] = found[0]["severity"] if found else ""
        out.append(row)
    return {"active": r.active(), "runs": out, "course": title}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, after: int = 0) -> dict:
    from miw.api.jobs import runner
    r = runner()
    row = r.get(run_id)
    if row is None:
        raise HTTPException(404, f"no run {run_id}")
    events = [dict(e) for e in r.events(run_id, after)]
    # What the run FOUND, not only what it logged. "19 findings raised" in a log line
    # is not something a reviewer can act on; each row here carries its finding_id, so
    # the UI can open it in the detail panel.
    return {"run": dict(row), "events": events,
            "last_event_id": events[-1]["id"] if events else after,
            "findings": r.findings_for(run_id)}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    from miw.api.jobs import runner
    return {"cancelled": runner().cancel(run_id)}


# -------------------------------------------------------------------- html

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
