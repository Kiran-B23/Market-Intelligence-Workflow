"""The job runner. Its one hard requirement: never present a partial audit as done."""
import contextlib
import json
import queue
import sys
import threading
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


# ------------------------------------------- the per-run LLM choice

@contextlib.contextmanager
def _bare_runner(tmp):
    """A JobRunner over a throwaway DB with no worker thread.

    Same shape as `tests/test_course_isolation.py::_runner`: `jobs._conn()` reads the
    module-level `DB`, so the constant has to move or reads land on the real
    `state/jobs.db`.
    """
    from miw.api import jobs as J
    original = J.DB
    J.DB = Path(tmp) / "jobs.db"
    r = J.JobRunner.__new__(J.JobRunner)
    r.conn = J._conn()
    r.conn.executescript(J.SCHEMA)
    r.conn.commit()
    r.lock = threading.Lock()
    r.q = queue.Queue()
    try:
        yield r
    finally:
        r.conn.close()
        J.DB = original


def _no_stream(monkeypatch, seen):
    from miw.api.jobs import JobRunner
    monkeypatch.setattr(
        JobRunner, "_stream",
        lambda self, rid, st, cmd: (seen.update(
            env=dict(getattr(self, "_llm_env", {}))), 0)[1], raising=True)


def test_the_llm_choice_reaches_the_stage_subprocess_as_environment(tmp_path, monkeypatch):
    """`MIW_LLM_PROVIDER`/`MIW_LLM_MODEL` is the interface `miw/llm.py` already reads,
    so the choice travels as env rather than as a new CLI flag — one way to say it."""
    with _bare_runner(tmp_path) as r:
        rid = r.submit(scope={"courses": ["PSE"]}, stages=["probe"],
                       llm={"provider": "openrouter", "model": "sonnet"})
        row = r.get(rid)
        assert json.loads(row["llm"]) == {"provider": "openrouter", "model": "sonnet"}
        seen = {}
        _no_stream(monkeypatch, seen)
        r._run(rid, row)
        assert seen["env"] == {"MIW_LLM_PROVIDER": "openrouter",
                               "MIW_LLM_MODEL": "sonnet"}


def test_no_choice_means_no_env_so_the_default_is_untouched(tmp_path, monkeypatch):
    """An empty choice must not export `MIW_LLM_PROVIDER=''` — that is not the same as
    absent, and `available_provider()` would read it as a typo and use no LLM at all."""
    with _bare_runner(tmp_path) as r:
        rid = r.submit(scope={"courses": ["PSE"]}, stages=["probe"])
        assert r.get(rid)["llm"] is None
        seen = {}
        _no_stream(monkeypatch, seen)
        r._run(rid, r.get(rid))
        assert seen["env"] == {}


def test_a_partial_choice_exports_only_what_was_chosen(tmp_path, monkeypatch):
    with _bare_runner(tmp_path) as r:
        rid = r.submit(scope={"courses": ["PSE"]}, stages=["probe"],
                       llm={"provider": "", "model": "opus"})
        seen = {}
        _no_stream(monkeypatch, seen)
        r._run(rid, r.get(rid))
        assert seen["env"] == {"MIW_LLM_MODEL": "opus"}


def test_an_existing_jobs_db_gains_the_llm_column(tmp_path, monkeypatch):
    """`CREATE TABLE IF NOT EXISTS` does not add a column to a table that exists, and
    every already-deployed jobs.db predates `llm`."""
    import sqlite3

    from miw.api import jobs as J
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE jobs (run_id TEXT PRIMARY KEY, scope TEXT,
        stages TEXT, refine INTEGER, status TEXT, stage_now TEXT, created_at TEXT,
        started_at TEXT, ended_at TEXT, exit_code INTEGER, label TEXT);""")
    c.commit(); c.close()

    monkeypatch.setattr(J, "DB", db)
    monkeypatch.setattr(J.JobRunner, "_loop", lambda self: None, raising=True)
    r = J.JobRunner()                        # must migrate, not crash
    try:
        rid = r.submit(scope={"courses": ["PSE"]}, stages=["probe"],
                       llm={"provider": "anthropic"})
        assert json.loads(r.get(rid)["llm"]) == {"provider": "anthropic"}
    finally:
        r.conn.close()
