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

from fastapi import FastAPI, HTTPException
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


# ----------------------------------------------------------------- summary

@app.get("/api/summary")
def summary() -> dict:
    from config import settings
    from miw.llm import available_provider, budget_state
    from miw.state import State

    inv = _read("inventory.json")
    findings = _read("findings_*.json")
    probe = _read("probe_*.json")

    deps = inv.get("dependencies", [])
    with_authority = sum(1 for d in deps if d.get("official_domains") or
                         d.get("registry") in ("pypi", "npm"))
    by_sev: dict[str, int] = {}
    for f in findings.get("findings", []):
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

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
        "by_kind": inv.get("stats", {}).get("by_kind", {}),
        "watch_tiers": _tiers(deps),
        "findings_total": len(findings.get("findings", [])),
        "findings_by_severity": by_sev,
        "suppressed_unchanged": findings.get("suppressed_unchanged", 0),
        "suppressed_by_reviewer": findings.get("suppressed_by_reviewer", []),
        "resolved": findings.get("resolved", []),
        "probe_counts": probe.get("counts", {}),
        "probed": len(probe.get("results", [])),
        "precision": precision,
        "capabilities": settings.capability_note(),
        "llm_provider": available_provider(),
        "llm_budget": budget_state(),
    }


def _tiers(deps: list) -> dict:
    out: dict[str, int] = {}
    for d in deps:
        out[d.get("watch_tier", "?")] = out.get(d.get("watch_tier", "?"), 0) + 1
    return out


# ---------------------------------------------------------------- findings

@app.get("/api/findings")
def findings() -> dict:
    from miw import triage
    from miw.state import State

    data = _read("findings_*.json")
    rows = data.get("findings", [])
    standing = [r for r in rows if r.get("diff_class") == "unchanged"]
    rows = [r for r in rows if r.get("diff_class") != "unchanged"]
    state = State()
    for f in rows:
        d = state.latest_decision(f["finding_id"])
        f["_decision"] = d["verdict"] if d else None
        f["_decision_reason"] = (d["reason"] if d else "") or ""
        f["_split"] = triage.split_of(f["finding_id"])
    state.close()
    return {"run_date": data.get("analysed_at", ""), "findings": rows,
            "standing": [{"canonical_name": r["canonical_name"], "signal": r["signal"],
                          "severity": r["severity"], "summary": r.get("summary", "")}
                         for r in standing],
            "resolved": data.get("resolved", []),
            "coverage": data.get("coverage", {}),
            "suppressed_by_reviewer": data.get("suppressed_by_reviewer", [])}


@app.get("/api/digest", response_class=PlainTextResponse)
def digest() -> str:
    p = _latest("digest_*.md")
    return p.read_text() if p else "No digest yet. Run: python3 main.py report"


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
    courses = {c for c in course.split("|") if c.strip()}
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
    cmap = course_map(deps)
    kinds = sorted({d.kind for d in deps})
    return {"courses": [{"title": c, "sessions": s} for c, s in cmap.items()],
            "kinds": kinds, "tiers": ["critical", "standard", "mention-only"]}


class RunIn(BaseModel):
    courses: list[str] = []
    sessions: str = ""
    tiers: list[str] = ["critical", "standard"]
    kinds: list[str] = []
    limit: Optional[int] = None
    stages: list[str] = ["probe", "analyse", "report"]
    refine: bool = False
    label: str = ""


@app.post("/api/runs")
def start_run(body: RunIn) -> dict:
    from miw.api.jobs import runner
    from miw.scope import parse_sessions

    try:
        sessions = sorted(parse_sessions(body.sessions))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    scope = {"courses": body.courses, "sessions": sessions, "tiers": body.tiers,
             "kinds": body.kinds, "limit": body.limit}
    r = runner()
    run_id = r.submit(scope=scope, stages=body.stages, refine=body.refine,
                      label=body.label)
    return {"run_id": run_id, "queued_behind": 1 if r.active() else 0}


@app.get("/api/runs")
def list_runs(limit: int = 20) -> dict:
    from miw.api.jobs import runner
    r = runner()
    return {"active": r.active(), "runs": [dict(x) for x in r.list(limit)]}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, after: int = 0) -> dict:
    from miw.api.jobs import runner
    r = runner()
    row = r.get(run_id)
    if row is None:
        raise HTTPException(404, f"no run {run_id}")
    events = [dict(e) for e in r.events(run_id, after)]
    return {"run": dict(row), "events": events,
            "last_event_id": events[-1]["id"] if events else after}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    from miw.api.jobs import runner
    return {"cancelled": runner().cancel(run_id)}


# -------------------------------------------------------------------- html

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
