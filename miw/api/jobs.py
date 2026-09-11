"""A small, honest job runner for on-demand audits.

Design constraints, and why:

* **One run at a time.** Probing is rate-limited per domain and the whole point of the
  politeness budget is that two concurrent runs would double our request rate against
  the same vendors. A single worker thread with a queue enforces that structurally.

* **Stages run as subprocesses of the same CLI.** `python3 main.py probe --course ...`
  is exactly what cron runs, so there is one execution path, not two. It also means a
  stage that crashes takes down a subprocess, not the web server.

* **A run that dies is reported as interrupted, never as finished.** The prior
  Curriculum Gap Analyzer dispatched agent runs with FastAPI `BackgroundTasks` and no
  queue, and its own notes record the consequence: "a server restart loses in-flight
  runs". Here, jobs live in SQLite and `recover_interrupted()` runs at startup to mark
  anything left `running` as `interrupted`. Silently showing a half-finished audit as
  complete is the failure that matters — someone would trust a digest built from a
  partial probe.

* **Output is persisted, not just streamed.** Events go to a table, so reloading the
  page mid-run shows the log from the beginning rather than from whenever the browser
  reconnected.
"""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "state" / "jobs.db"

# `gaps` sits between `analyse` and `report` because it writes into the same findings
# artifact the analyser writes and the reporter reads. It is the only stage that does
# not consult the dependency inventory at all - it reads the workbooks and official
# documentation directly, which is what lets it notice a topic the courses never
# mention (see `miw/analyse/gaps.py`).
STAGES = ("ingest", "extract", "probe", "research", "analyse", "gaps", "report")
# Which stages accept scope flags. `ingest`/`extract` rebuild the whole inventory:
# scoping them would silently shrink it and break every other course's findings.
# `report` is scoped only in what it WRITES - it always reads the whole merged findings
# artifact and always re-renders the roll-up, then writes the named course's digest.
SCOPED = {"probe", "research", "analyse", "gaps", "report"}

# Which scope flags each stage's argparse actually declares. `report` takes ONLY
# `--course` (main.py applies `_add_scope_args` to probe/research/analyse/run-weekly,
# deliberately not to report), so sending it the full scope made every UI-initiated run
# die at its last stage:
#
#   main.py report --course "Intro to Gen AI" --tiers critical,standard
#   main.py: error: unrecognized arguments: --tiers critical,standard   -> exit 2
#
# and `_run` treats any code outside (0,1) as fatal, so the run was marked FAILED. It
# went unnoticed because `report` joined SCOPED after the last successful run.
# `gaps` is course-scoped in the same narrow sense: tier, kind and dep-id describe
# dependencies, and a topic gap is about a SESSION, so those flags would parse and then
# silently do nothing.
COURSE_ONLY = {"report", "gaps"}


def _stage_args(stage: str, sc) -> list:
    """The scope flags this stage can actually parse.

    Filtering here rather than widening `report`'s CLI keeps the CLI honest: report does
    not filter its input by tier or session, it only chooses which per-course digest to
    write, so `--tiers` would be a flag that silently did nothing.
    """
    if stage not in SCOPED:
        return []
    args = sc.to_cli_args()
    if stage not in COURSE_ONLY:
        return args
    out, i = [], 0
    while i < len(args):
        if args[i] == "--course":
            out += args[i:i + 2]
            i += 2
        else:
            i += 2 if i + 1 < len(args) and not args[i + 1].startswith("--") else 1
    return out

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    run_id     TEXT PRIMARY KEY,
    scope      TEXT,
    stages     TEXT,
    refine     INTEGER DEFAULT 0,
    status     TEXT,       -- queued | running | done | failed | interrupted | cancelled
    stage_now  TEXT,
    created_at TEXT,
    started_at TEXT,
    ended_at   TEXT,
    exit_code  INTEGER,
    label      TEXT,
    llm        TEXT        -- {"provider": ..., "model": ...}, chosen per run
);
CREATE TABLE IF NOT EXISTS job_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT,
    at      TEXT,
    stage   TEXT,
    line    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON job_events(run_id, id);

-- Which courses a run touched. A side table rather than a `course` column on `jobs`,
-- because `scope.courses` is a LIST and a run may legitimately span courses - a scalar
-- column could only represent that by lying. `course_slug = '*'` means unscoped, which
-- is the honest semantic: an all-courses sweep really did audit PSE and belongs in
-- PSE's history, while a run scoped to one course must not appear elsewhere.
-- What a run FOUND, captured the moment its `analyse` stage exits.
--
-- It has to be recorded rather than inferred. The findings artifact carries
-- `examined_this_run` and a `coverage` map of dep_id -> stamp, but both describe the
-- LAST analyse to touch the file: one later run, or one manual `main.py analyse`, and
-- an earlier run's findings can no longer be told apart. A log line saying "19 findings
-- raised" is not something a reviewer can click.
CREATE TABLE IF NOT EXISTS job_findings (
    run_id     TEXT,
    finding_id TEXT,
    dep_id     TEXT,
    signal     TEXT,
    severity   TEXT,
    name       TEXT,
    diff_class TEXT,
    courses    TEXT,
    PRIMARY KEY (run_id, finding_id)
);

CREATE TABLE IF NOT EXISTS job_courses (
    run_id      TEXT,
    course_slug TEXT,
    PRIMARY KEY (run_id, course_slug)
);
CREATE INDEX IF NOT EXISTS idx_job_courses_slug ON job_courses(course_slug, run_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), check_same_thread=False, timeout=20.0)
    c.row_factory = sqlite3.Row
    # WAL so the API thread can read while the worker writes; a busy timeout so the
    # loser of a race waits instead of raising "database is locked".
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=20000")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def _slugs_for_scope(scope: dict) -> list[str]:
    """Which course slugs a scope touches. `['*']` means every course.

    Three cases, and the third is the one that needs care. `Scope.dep_ids` "wins
    outright" over course/session, so a vendor-signal run names dependencies and no
    courses at all - those ids are resolved through the inventory. If the inventory
    cannot be read, the answer is `'*'`: "may be relevant to any course" hides nothing,
    whereas "relevant to none" would drop the run out of every course's history.
    """
    from miw.scope import slug_of
    courses = [c for c in (scope.get("courses") or []) if c]
    if courses:
        return sorted({slug_of(c) for c in courses})

    dep_ids = set(scope.get("dep_ids") or [])
    if not dep_ids:
        return ["*"]                     # an unscoped sweep audits everything
    try:
        import json as _json
        from pathlib import Path as _Path
        raw = _json.loads((_Path("out") / "inventory.json").read_text())
        found = set()
        for d in raw.get("dependencies", []):
            if d.get("dep_id") in dep_ids:
                found |= {l.get("course") for l in d.get("locations", []) if l.get("course")}
        return sorted({slug_of(c) for c in found}) or ["*"]
    except (OSError, ValueError, KeyError):
        return ["*"]


class JobRunner:
    def __init__(self) -> None:
        self.q: "queue.Queue[str]" = queue.Queue()
        self.lock = threading.Lock()
        self.conn = _conn()
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
            # `CREATE TABLE IF NOT EXISTS` does not add a column to a table that
            # already exists, and this DB predates `llm`. Same shape as
            # `_backfill_job_courses`: idempotent, no migration script.
            cols = {r[1] for r in cur.execute("PRAGMA table_info(jobs)")}
            if "llm" not in cols:
                cur.execute("ALTER TABLE jobs ADD COLUMN llm TEXT")
        self.conn.commit()
        self._current: Optional[subprocess.Popen] = None
        self._current_run: Optional[str] = None
        self.recover_interrupted()
        self._backfill_job_courses()
        self.worker = threading.Thread(target=self._loop, daemon=True, name="miw-jobs")
        self.worker.start()

    # --- persistence --------------------------------------------------------

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self.lock:
            self.conn.execute(sql, args)
            self.conn.commit()

    def _read(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        """Run a query on a short-lived connection of its own.

        The worker thread writes on `self.conn`; API request threads used to read on
        the same handle with no lock held. A sqlite3 connection is not safe for
        concurrent use, and under WAL a separate reader is cheap.
        """
        c = _conn()
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()

    def recover_interrupted(self) -> int:
        """Mark anything still `running` or `queued` from a previous process.

        Called at startup. A run whose process is gone cannot be resumed, and calling
        it `done` would present a partial audit as a complete one.
        """
        rows = self._read(
            "SELECT run_id, status FROM jobs WHERE status IN ('running','queued')")
        for r in rows:
            self._exec("UPDATE jobs SET status='interrupted', ended_at=? WHERE run_id=?",
                       (_now(), r["run_id"]))
            self.event(r["run_id"], "", "-- server restarted; this run was "
                                       "interrupted and its output may be partial --")
        return len(rows)

    def event(self, run_id: str, stage: str, line: str) -> None:
        self._exec("INSERT INTO job_events (run_id, at, stage, line) VALUES (?,?,?,?)",
                   (run_id, _now(), stage, line.rstrip("\n")[:2000]))

    # --- public API ---------------------------------------------------------

    def submit(self, *, scope: dict, stages: list[str], refine: bool = False,
               label: str = "", llm: Optional[dict] = None) -> str:
        stages = [s for s in stages if s in STAGES] or ["probe", "analyse", "report"]
        run_id = uuid.uuid4().hex[:12]
        self._exec(
            "INSERT INTO jobs (run_id, scope, stages, refine, status, created_at, "
            "label, llm) VALUES (?,?,?,?, 'queued', ?, ?, ?)",
            (run_id, json.dumps(scope), json.dumps(stages), int(refine), _now(), label,
             json.dumps(llm) if llm else None))
        for slug in _slugs_for_scope(scope):
            self._exec("INSERT OR IGNORE INTO job_courses (run_id, course_slug) "
                       "VALUES (?,?)", (run_id, slug))
        self.q.put(run_id)
        return run_id

    def _record_findings(self, run_id: str, stage: str = "analyse") -> None:
        """Snapshot the findings this run's `analyse` just produced.

        Keyed on `examined_this_run`, which is the set of dependencies the stage
        actually looked at — a finding carried forward from another scope was not found
        by this run and must not be claimed as such.
        """
        import glob

        try:
            files = sorted(glob.glob(str(ROOT / "out" / "findings_*.json")))
            if not files:
                return
            data = json.loads(Path(files[-1]).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            self.event(run_id, stage, f"-- could not record findings: {exc} --")
            return

        examined = set(data.get("examined_this_run") or [])
        rows = [f for f in (data.get("findings") or [])
                if not examined or f.get("dep_id") in examined]
        with self.lock:
            for f in rows:
                self.conn.execute(
                    "INSERT OR REPLACE INTO job_findings (run_id, finding_id, dep_id,"
                    " signal, severity, name, diff_class, courses)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (run_id, f.get("finding_id", ""), f.get("dep_id", ""),
                     f.get("signal", ""), f.get("severity", ""),
                     f.get("canonical_name", ""), f.get("diff_class", ""),
                     json.dumps(f.get("courses") or [])))
            self.conn.commit()
        self.event(run_id, "analyse",
                   f"recorded {len(rows)} finding(s) against this run")

    def findings_for(self, run_id: str) -> list:
        """What this run found, newest-severity first."""
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        rows = [dict(r) for r in self._read(
            "SELECT * FROM job_findings WHERE run_id=?", (run_id,))]
        for r in rows:
            try:
                r["courses"] = json.loads(r["courses"] or "[]")
            except json.JSONDecodeError:
                r["courses"] = []
        return sorted(rows, key=lambda r: (order.get(r["severity"], 9), r["name"]))

    def _backfill_job_courses(self) -> None:
        """Give pre-existing runs their course rows. Idempotent, one pass.

        No migration script is needed: `job_courses` is CREATE TABLE IF NOT EXISTS
        inside the SCHEMA this class already executescripts on construction.
        """
        try:
            missing = self._read(
                "SELECT j.run_id, j.scope FROM jobs j LEFT JOIN job_courses c "
                "ON c.run_id = j.run_id WHERE c.run_id IS NULL")
        except sqlite3.Error:
            return
        for row in missing:
            try:
                scope = json.loads(row["scope"] or "{}")
            except (json.JSONDecodeError, TypeError):
                scope = {}
            for slug in _slugs_for_scope(scope):
                self._exec("INSERT OR IGNORE INTO job_courses (run_id, course_slug) "
                           "VALUES (?,?)", (row["run_id"], slug))

    def cancel(self, run_id: str) -> bool:
        row = self.get(run_id)
        if not row:
            return False
        if row["status"] == "queued":
            self._exec("UPDATE jobs SET status='cancelled', ended_at=? WHERE run_id=?",
                       (_now(), run_id))
            return True
        if row["status"] == "running" and self._current_run == run_id and self._current:
            self._current.terminate()
            self.event(run_id, "", "-- cancelled by request --")
            return True
        return False

    def get(self, run_id: str) -> Optional[sqlite3.Row]:
        rows = self._read("SELECT * FROM jobs WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

    def list(self, limit: int = 25, course_slug: str = "") -> list[sqlite3.Row]:
        """Recent runs, newest first. With `course_slug`, only runs that touched it.

        `'*'` rows are included: an unscoped sweep audited this course too.
        """
        if not course_slug:
            return self._read(
                "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,))
        return self._read(
            "SELECT j.* FROM jobs j JOIN job_courses c ON c.run_id = j.run_id "
            "WHERE c.course_slug IN (?, '*') "
            "ORDER BY j.created_at DESC, j.rowid DESC LIMIT ?", (course_slug, limit))

    def events(self, run_id: str, after: int = 0) -> list[sqlite3.Row]:
        return self._read(
            "SELECT id, at, stage, line FROM job_events WHERE run_id=? AND id>? "
            "ORDER BY id", (run_id, after))

    def active(self) -> Optional[str]:
        return self._current_run

    # --- worker -------------------------------------------------------------

    def _loop(self) -> None:
        while True:
            run_id = self.q.get()
            row = self.get(run_id)
            if row is None or row["status"] != "queued":
                continue                      # cancelled before it started
            self._run(run_id, row)

    def _run(self, run_id: str, row: sqlite3.Row) -> None:
        scope = json.loads(row["scope"] or "{}")
        stages = json.loads(row["stages"] or "[]")
        refine = bool(row["refine"])
        # The provider/model choice reaches the stages as environment, because that is
        # the interface `miw/llm.py` already reads — there is no CLI flag for it, and
        # inventing one would give two ways to say the same thing.
        llm = {}
        try:
            llm = json.loads(row["llm"] or "{}") or {}
        except (json.JSONDecodeError, IndexError, KeyError):
            llm = {}
        self._llm_env = {k: v for k, v in (
            ("MIW_LLM_PROVIDER", llm.get("provider")),
            ("MIW_LLM_MODEL", llm.get("model"))) if v}
        self._current_run = run_id
        self._exec("UPDATE jobs SET status='running', started_at=? WHERE run_id=?",
                   (_now(), run_id))

        from miw.scope import Scope
        sc = Scope.from_dict(scope)
        self.event(run_id, "", f"scope: {sc.describe()}")
        self.event(run_id, "", f"stages: {', '.join(stages)}"
                               f"{' (+refine)' if refine else ''}")
        if self._llm_env:
            self.event(run_id, "", "llm: " + ", ".join(
                f"{k.replace('MIW_LLM_', '').lower()}={v}"
                for k, v in sorted(self._llm_env.items())))

        code = 0
        try:
            for stage in stages:
                self._exec("UPDATE jobs SET stage_now=? WHERE run_id=?", (stage, run_id))
                cmd = [sys.executable, "-u", "main.py", stage]
                cmd += _stage_args(stage, sc)
                if stage == "analyse" and refine:
                    cmd.append("--refine")
                self.event(run_id, stage, f"$ {' '.join(cmd[2:])}")
                code = self._stream(run_id, stage, cmd)
                if stage in ("analyse", "gaps") and code in (0, 1):
                    # Immediately after, while `examined_this_run` in the artifact is
                    # still this stage's. Any later analyse overwrites it.
                    #
                    # Called after `gaps` as well, because `gaps` merges its own
                    # findings into the same artifact and so replaces
                    # `examined_this_run` with its topic ids. The insert is keyed on
                    # (run_id, finding_id), so this adds the gaps without dropping what
                    # `analyse` recorded a moment earlier - which is exactly what the
                    # run page needs to show both kinds together.
                    self._record_findings(run_id, stage=stage)
                # `ingest` exits 1 on data problems it has already reported; that is a
                # warning, not a stage failure.
                if code not in (0, 1):
                    self.event(run_id, stage, f"-- stage exited {code}; stopping --")
                    break
                if code == 1 and stage != "ingest":
                    self.event(run_id, stage, f"-- stage exited 1; stopping --")
                    break
        except Exception as exc:                                  # pragma: no cover
            self.event(run_id, "", f"-- runner error: {type(exc).__name__}: {exc} --")
            code = 99

        status = "done" if code in (0, 1) else "failed"
        if (self.get(run_id) or {})["status"] == "running":
            self._exec("UPDATE jobs SET status=?, ended_at=?, exit_code=?, stage_now=NULL"
                       " WHERE run_id=?", (status, _now(), code, run_id))
        self.event(run_id, "", f"-- {status} --")
        self._current_run, self._current = None, None

    def _stream(self, run_id: str, stage: str, cmd: list[str]) -> int:
        env = dict(os.environ, PYTHONUNBUFFERED="1",
                   **getattr(self, "_llm_env", {}) or {})
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
        self._current = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.strip():
                self.event(run_id, stage, line)
        proc.wait()
        return proc.returncode


_runner: Optional[JobRunner] = None


def runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner
