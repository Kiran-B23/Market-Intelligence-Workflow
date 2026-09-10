"""The job runner. Its one hard requirement: never present a partial audit as done."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.api import jobs


def test_scoped_stages_are_declared():
    """ingest/extract rebuild the whole inventory; scoping them would shrink it and
    break every other course's findings.

    `report` IS scoped, but only in which per-course digest it writes — see the test
    below. That is the load-bearing distinction.
    """
    assert jobs.SCOPED == {"probe", "research", "analyse", "report"}
    assert "ingest" not in jobs.SCOPED and "extract" not in jobs.SCOPED


def test_report_narrows_what_it_writes_never_what_it_reads():
    """A scoped `report` must still load the whole merged findings artifact.

    Reading a slice and rendering it as the week's state is the bug that once put 91
    Gen-AI-only probe results in place of the sweep's 229 — `miw/artifacts.py` exists
    because of it. Asserted against the source so the guarantee cannot be quietly
    dropped: the roll-up is written unconditionally, before any --course branch.
    """
    import inspect
    import main
    src = inspect.getsource(main.cmd_report)
    rollup = src.index('OUT / f"digest_{_today()}.md"')
    per_course = src.index('OUT / "courses"')
    assert rollup < per_course, "the roll-up must be rendered from the whole artifact"
    assert "args.course" not in src[:rollup], \
        "the findings load and roll-up must not depend on --course"


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


# ----------------------------------------- the argv a run builds must actually parse

def test_every_scoped_stage_accepts_the_argv_the_runner_builds():
    """The guard for a bug that made every UI run fail at its last stage.

    `report` was added to `jobs.SCOPED`, so `Scope.to_cli_args()` started sending it
    `--tiers` — which its subparser does not declare, because `_add_scope_args` is
    applied to probe/research/analyse/run-weekly and deliberately not to report. argparse
    exited 2, `_run` treats anything outside (0,1) as fatal, and the run was marked
    failed. Nothing caught it because report joined SCOPED after the last green run.

    So: for every stage the runner will scope, assert the exact argv parses.
    """
    import main
    from miw.api.jobs import SCOPED, _stage_args
    from miw.scope import Scope

    parser = main.build_parser()
    # A maximal scope, so every flag `to_cli_args` can emit is exercised.
    sc = Scope(courses={"Intro to Gen AI", "PSE"}, sessions={4, 5, 6},
               tiers=("critical", "standard"), kinds=("model",),
               dep_ids={"a1b2c3"}, limit=10)
    for stage in sorted(SCOPED):
        argv = [stage, *_stage_args(stage, sc)]
        ns = parser.parse_args(argv)      # raises SystemExit(2) on an unknown flag
        assert ns.cmd == stage, argv


def test_report_is_scoped_only_by_course():
    """It chooses which per-course digest to WRITE; it never filters its input."""
    from miw.api.jobs import _stage_args
    from miw.scope import Scope
    sc = Scope(courses={"PSE"}, sessions={1, 2}, tiers=("critical",),
               kinds=("model",), dep_ids={"x1"}, limit=5)
    assert _stage_args("report", sc) == ["--course", "PSE"]


def test_repeated_flags_survive_the_course_only_filter():
    """`--course` and `--dep-id` are `action="append"`, so the filter must not eat one."""
    from miw.api.jobs import _stage_args
    from miw.scope import Scope
    sc = Scope(courses={"PSE", "Intro to Gen AI"}, dep_ids={"a", "b"},
               tiers=("critical",))
    assert _stage_args("report", sc) == ["--course", "Intro to Gen AI",
                                         "--course", "PSE"]


def test_unscoped_stages_get_no_flags_at_all():
    """`ingest`/`extract` rebuild the whole inventory; scoping them would shrink it."""
    from miw.api.jobs import _stage_args
    from miw.scope import Scope
    sc = Scope(courses={"PSE"}, tiers=("critical",))
    assert _stage_args("ingest", sc) == []
    assert _stage_args("extract", sc) == []
