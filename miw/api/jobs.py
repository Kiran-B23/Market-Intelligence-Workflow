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

STAGES = ("ingest", "extract", "probe", "research", "analyse", "report")
# Which stages accept scope flags. `ingest`/`extract` rebuild the whole inventory:
# scoping them would silently shrink it and break every other course's findings.
SCOPED = {"probe", "research", "analyse"}

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
    label      TEXT
);
CREATE TABLE IF NOT EXISTS job_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT,
    at      TEXT,
    stage   TEXT,
    line    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON job_events(run_id, id);
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


class JobRunner:
    def __init__(self) -> None:
        self.q: "queue.Queue[str]" = queue.Queue()
        self.lock = threading.Lock()
        self.conn = _conn()
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self.conn.commit()
        self._current: Optional[subprocess.Popen] = None
        self._current_run: Optional[str] = None
        self.recover_interrupted()
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
               label: str = "") -> str:
        stages = [s for s in stages if s in STAGES] or ["probe", "analyse", "report"]
        run_id = uuid.uuid4().hex[:12]
        self._exec(
            "INSERT INTO jobs (run_id, scope, stages, refine, status, created_at, label)"
            " VALUES (?,?,?,?, 'queued', ?, ?)",
            (run_id, json.dumps(scope), json.dumps(stages), int(refine), _now(), label))
        self.q.put(run_id)
        return run_id

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

    def list(self, limit: int = 25) -> list[sqlite3.Row]:
        return self._read(
            "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))

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
        self._current_run = run_id
        self._exec("UPDATE jobs SET status='running', started_at=? WHERE run_id=?",
                   (_now(), run_id))

        from miw.scope import Scope
        sc = Scope.from_dict(scope)
        self.event(run_id, "", f"scope: {sc.describe()}")
        self.event(run_id, "", f"stages: {', '.join(stages)}"
                               f"{' (+refine)' if refine else ''}")

        code = 0
        try:
            for stage in stages:
                self._exec("UPDATE jobs SET stage_now=? WHERE run_id=?", (stage, run_id))
                cmd = [sys.executable, "-u", "main.py", stage]
                if stage in SCOPED:
                    cmd += sc.to_cli_args()
                if stage == "analyse" and refine:
                    cmd.append("--refine")
                self.event(run_id, stage, f"$ {' '.join(cmd[2:])}")
                code = self._stream(run_id, stage, cmd)
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
        env = dict(os.environ, PYTHONUNBUFFERED="1")
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
