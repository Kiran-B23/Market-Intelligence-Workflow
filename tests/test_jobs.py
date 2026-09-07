"""The job runner. Its one hard requirement: never present a partial audit as done."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.api import jobs


def test_scoped_stages_are_declared():
    """ingest/extract rebuild the whole inventory; scoping them would shrink it and
    break every other course's findings."""
    assert jobs.SCOPED == {"probe", "research", "analyse"}
    assert "ingest" not in jobs.SCOPED and "extract" not in jobs.SCOPED


def test_interrupted_runs_are_never_reported_as_done(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "DB", tmp_path / "jobs.db")
    r = jobs.JobRunner.__new__(jobs.JobRunner)      # no worker thread for this test
    import sqlite3
    from contextlib import closing
    r.conn = jobs._conn()
    with closing(r.conn.cursor()) as cur:
        cur.executescript(jobs.SCHEMA)
    r.conn.commit()
    import threading
    r.lock = threading.Lock()

    r.conn.execute(
        "INSERT INTO jobs (run_id, scope, stages, status, created_at)"
        " VALUES ('abc','{}','[\"probe\"]','running','t0')")
    r.conn.commit()

    assert r.recover_interrupted() == 1
    row = r.conn.execute("SELECT status FROM jobs WHERE run_id='abc'").fetchone()
    assert row["status"] == "interrupted", "a dead run must not read as done"
    lines = [e["line"] for e in
             r.conn.execute("SELECT line FROM job_events WHERE run_id='abc'").fetchall()]
    assert any("interrupted" in l for l in lines), "the log must say what happened"
    r.conn.close()
