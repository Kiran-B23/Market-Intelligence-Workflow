"""Each course sees its own work, and one course's run never shows up under another.

Three separate mechanisms have to hold for that to be true, and they fail in different
ways:

  * READS are projected, not filtered — a shared dependency carries per-course counts.
  * RUN HISTORY is a side table, because a run may legitimately span courses.
  * The per-course DIGEST must not be reachable by `_latest("digest_*.md")`, or it
    silently becomes "the" digest for everybody.
"""
import contextlib
import json
import sqlite3
import sys
import threading
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.api.jobs import JobRunner, _slugs_for_scope
from miw.scope import resolve_courses, slug_of, slugify, title_of

INTRO, APPS = "Intro to Gen AI", "Building LLM Applications"
FIN = "AI for Finance"


# ------------------------------------------------------------------ slugs

def test_slugs_round_trip_for_every_declared_course():
    from config.constants import COURSES
    for slug, meta in COURSES.items():
        assert slug_of(meta["title"]) == slug
        assert title_of(slug) == meta["title"]


def test_an_unknown_course_still_gets_an_addressable_slug():
    """A course renamed in an export but not in COURSES must not vanish from the UI."""
    assert slugify("Some New Course 2027") == "some_new_course_2027"
    assert slug_of("Some New Course 2027") == "some_new_course_2027"
    assert title_of("some_new_course_2027") == "", "not a declared course"


def test_course_resolution_accepts_slugs_and_titles_together():
    """So a URL can carry the slug while the existing widget keeps sending titles."""
    assert resolve_courses(["ai_for_finance"]) == {FIN}
    assert resolve_courses([FIN]) == {FIN}
    assert resolve_courses(["ai_for_finance", FIN]) == {FIN}, "must dedupe to one course"
    assert resolve_courses(["intro_to_gen_ai", APPS]) == {INTRO, APPS}
    assert resolve_courses(["", None]) == set()


# ------------------------------------------------------- run scope -> courses

def test_a_course_scoped_run_belongs_to_that_course_only():
    assert _slugs_for_scope({"courses": [INTRO]}) == ["intro_to_gen_ai"]


def test_a_multi_course_run_belongs_to_both():
    assert _slugs_for_scope({"courses": [INTRO, APPS]}) == ["intro_to_gen_ai",
                                                            "llm_applications"]


def test_an_unscoped_sweep_belongs_to_every_course():
    """It really did audit every course, so hiding it from one history would be a lie."""
    assert _slugs_for_scope({}) == ["*"]
    assert _slugs_for_scope({"courses": [], "dep_ids": []}) == ["*"]


def test_a_vendor_signal_run_resolves_its_dep_ids_to_courses():
    """`Scope.dep_ids` wins outright over course/session, so a watch-triggered run
    names dependencies and no courses at all."""
    inv = Path("out/inventory.json")
    if not inv.exists():
        return
    deps = json.loads(inv.read_text())["dependencies"]
    target = next((d for d in deps if d["canonical_name"] == "llama-3.3-70b-versatile"),
                  None)
    if target is None:
        return
    got = _slugs_for_scope({"dep_ids": [target["dep_id"]]})
    assert "*" not in got and got, got
    expected = {slug_of(l["course"]) for l in target["locations"] if l.get("course")}
    assert set(got) == expected


def test_an_unreadable_inventory_makes_a_dep_id_run_visible_everywhere():
    """"May be relevant to any course" hides nothing; "relevant to none" would drop
    the run out of every course's history."""
    import os
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)                       # no out/inventory.json here
            assert _slugs_for_scope({"dep_ids": ["deadbeef"]}) == ["*"]
        finally:
            os.chdir(cwd)


# ------------------------------------------------------ run history isolation

@contextlib.contextmanager
def _runner(tmp):
    """A JobRunner over a throwaway DB, with no worker thread.

    `jobs._conn()` reads the module-level `DB` path, so pointing the instance at a temp
    file is not enough — the module constant has to move too, or reads land on the real
    state/jobs.db.
    """
    from miw.api import jobs as J
    original = J.DB
    J.DB = Path(tmp) / "jobs.db"
    r = JobRunner.__new__(JobRunner)
    r.conn = J._conn()
    r.conn.executescript(J.SCHEMA)
    r.conn.commit()
    r.lock = threading.Lock()
    try:
        yield r
    finally:
        r.conn.close()
        J.DB = original


def _add(r, run_id, scope):
    r.conn.execute("INSERT INTO jobs (run_id, scope, stages, status, created_at) "
                   "VALUES (?,?,?, 'done', ?)",
                   (run_id, json.dumps(scope), '["probe"]', "2026-09-09T00:00:00"))
    for slug in _slugs_for_scope(scope):
        r.conn.execute("INSERT OR IGNORE INTO job_courses (run_id, course_slug) "
                       "VALUES (?,?)", (run_id, slug))
    r.conn.commit()


def test_a_run_on_one_course_does_not_appear_under_another():
    with tempfile.TemporaryDirectory() as tmp:
        with _runner(tmp) as r:
            _add(r, "aaa", {"courses": [INTRO]})
            _add(r, "bbb", {"courses": [APPS]})
            intro = [x["run_id"] for x in r.list(50, course_slug="intro_to_gen_ai")]
            apps = [x["run_id"] for x in r.list(50, course_slug="llm_applications")]
            assert intro == ["aaa"] and apps == ["bbb"]
            assert [x["run_id"] for x in r.list(50, course_slug="ai_for_finance")] == []


def test_an_unscoped_sweep_appears_in_every_course_history():
    with tempfile.TemporaryDirectory() as tmp:
        with _runner(tmp) as r:
            _add(r, "sweep", {})
            for slug in ("intro_to_gen_ai", "llm_applications", "ai_for_finance"):
                assert [x["run_id"] for x in r.list(50, course_slug=slug)] == ["sweep"]


def test_the_unfiltered_history_still_shows_everything():
    with tempfile.TemporaryDirectory() as tmp:
        with _runner(tmp) as r:
            _add(r, "aaa", {"courses": [INTRO]})
            _add(r, "bbb", {"courses": [APPS]})
            assert len(r.list(50)) == 2


def test_the_backfill_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        with _runner(tmp) as r:
            r.conn.execute(
                "INSERT INTO jobs (run_id, scope, stages, status, created_at) "
                "VALUES ('old', ?, '[\"probe\"]', 'done', '2026-01-01')",
                (json.dumps({"courses": [INTRO]}),))
            r.conn.commit()
            assert r.list(50, course_slug="intro_to_gen_ai") == []
            r._backfill_job_courses()
            r._backfill_job_courses()
            rows = r.conn.execute(
                "SELECT * FROM job_courses WHERE run_id='old'").fetchall()
            assert len(rows) == 1
            assert [x["run_id"] for x in
                    r.list(50, course_slug="intro_to_gen_ai")] == ["old"]


def test_a_legacy_scope_missing_keys_still_backfills():
    """The oldest real row's scope JSON has no `kinds` or `limit`."""
    assert _slugs_for_scope({"courses": [INTRO], "sessions": [4]}) == ["intro_to_gen_ai"]


# --------------------------------------------------------- digest isolation

def test_a_per_course_digest_can_never_become_the_roll_up():
    """`_latest()` globs lexicographically. `digest_zz_<date>.md` sorts AFTER
    `digest_<date>.md` ('p' > '2'), so a top-level per-course file would silently be
    served as "the" digest. The subdirectory makes that structurally impossible."""
    from miw.api import app
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "digest_2026-09-09.md").write_text("ROLLUP")
        cdir = out / "courses" / "ai_for_finance"
        cdir.mkdir(parents=True)
        (cdir / "digest_2026-09-09.md").write_text("COURSE ONLY")
        original = app.OUT
        try:
            app.OUT = out
            assert app._latest("digest_*.md").read_text() == "ROLLUP"
        finally:
            app.OUT = original


def test_a_missing_per_course_digest_does_not_fall_back_to_the_rollup():
    """Serving the roll-up under a per-course heading is exactly the mixing this
    workstream exists to end."""
    from miw.api import app
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "digest_2026-09-09.md").write_text("ROLLUP with every course in it")
        original = app.OUT
        try:
            app.OUT = out
            text = app.digest(course="ai_for_finance")
        finally:
            app.OUT = original
        assert "ROLLUP" not in text
        assert "No digest for AI for Finance yet" in text
        assert "report --course" in text


# ------------------------------------------------- the API, end to end

def _client():
    from fastapi.testclient import TestClient
    from miw.api.app import app
    return TestClient(app)


def test_summary_counts_are_the_course_share_not_the_global_total():
    if not Path("out/inventory.json").exists():
        return
    c = _client()
    g = c.get("/api/summary").json()
    intro = c.get("/api/summary?course=intro_to_gen_ai").json()
    assert intro["course"] == INTRO
    assert intro["dependencies"] < g["dependencies"]
    assert intro["findings_total"] <= g["findings_total"]
    # Reviewer precision has no course dimension and must be labelled, not filtered.
    assert "precision" in intro["global_only"]
    assert intro["precision"] == g["precision"]


def test_a_slug_and_a_title_reach_the_same_page():
    if not Path("out/inventory.json").exists():
        return
    c = _client()
    a = c.get("/api/summary?course=ai_for_finance").json()
    b = c.get("/api/summary?course=AI for Finance").json()
    assert a["course"] == b["course"] == "AI for Finance"
    assert a["dependencies"] == b["dependencies"]


def test_findings_are_projected_so_a_shared_one_differs_by_course():
    """The whole point: `llama-3.3-70b-versatile` is not equally severe everywhere."""
    if not Path("out/findings_2026-09-09.json").exists():
        return
    c = _client()
    seen = {}
    for slug in ("intro_to_gen_ai", "llm_applications"):
        d = c.get(f"/api/findings?course={slug}").json()
        rows = d["findings"] + d["standing"]
        for r in rows:
            if r["canonical_name"] == "llama-3.3-70b-versatile":
                seen[slug] = r["blast_radius"]
    if len(seen) == 2:
        assert seen["intro_to_gen_ai"] != seen["llm_applications"], \
            "a filtered-but-not-recomputed page would report the same number twice"


def test_every_course_page_reports_its_own_coverage():
    if not Path("out/findings_2026-09-09.json").exists():
        return
    c = _client()
    total = len(c.get("/api/findings").json()["coverage"])
    per = {slug: len(c.get(f"/api/findings?course={slug}").json()["coverage"])
           for slug in ("intro_to_gen_ai", "llm_applications", "ai_for_finance")}
    assert all(v <= total for v in per.values()), per
    assert any(v < total for v in per.values()), "coverage was not narrowed at all"


def test_the_api_says_that_triage_is_global():
    """So the UI can warn at the point of the click rather than in a footnote."""
    c = _client()
    assert c.get("/api/findings?course=ai_for_finance").json()["triage_is_global"] is True


def test_courses_expose_slugs_and_flag_the_uningested():
    c = _client()
    rows = c.get("/api/courses").json()["courses"]
    assert rows and all("slug" in r and "ingested" in r for r in rows)
    from config.constants import COURSES
    assert {r["slug"] for r in rows} >= set(COURSES)


def test_the_summary_payload_has_no_duplicate_keys():
    """A duplicate key in a dict literal is legal, silent, and last-one-wins.

    `summary()` already returned `capabilities` as a PROSE line for the digest footer.
    Adding a second `capabilities` holding the capability vocabulary silently kept the
    string, the UI called `.forEach` on it, and the boot block died taking the whole
    page with it. Python cannot warn about this, so the source has to be checked.
    """
    import ast
    import inspect
    import textwrap

    import miw.api.app as app

    tree = ast.parse(textwrap.dedent(inspect.getsource(app.summary)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        dupes = {k for k in keys if keys.count(k) > 1}
        assert not dupes, f"summary() returns duplicate key(s): {sorted(dupes)}"
