"""Adding a course at runtime: the writable overlay, and refusing rather than half-doing.

`config/constants.py` is a hand-aligned literal under an eleven-line comment that is the
reviewed justification for each `expect_sessions` value, and nothing wrote it. These
tests cover the overlay that now sits beside it, and the rule that decides conflicts.

The refusals carry as much weight as the happy path. A roster entry is the switch that
makes `cmd_ingest` open an export at all, so a half-registered course does not merely
fail for itself — it breaks the run for every other course.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw import courses as C

DECLARED = {"alpha": {"title": "Alpha", "expect_sessions": 5}}


# ------------------------------------------------------------------ the overlay

def test_an_absent_overlay_leaves_only_the_declared_courses(tmp_path):
    r = C.Roster(DECLARED, path=tmp_path / "none.json")
    assert dict(r) == {"alpha": {"title": "Alpha", "expect_sessions": 5,
                                 "origin": "declared"}}


def test_an_overlay_entry_is_merged_and_marked_as_runtime(tmp_path):
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"courses": [
        {"slug": "beta", "title": "Beta", "expect_sessions": 7}]}))
    r = C.Roster(DECLARED, path=p)
    assert sorted(r) == ["alpha", "beta"]
    assert r.origin_of("beta") == "runtime" and r.origin_of("alpha") == "declared"


def test_the_declared_literal_wins_and_the_shadowing_is_reported(tmp_path):
    """An untracked file must not override a tracked number whose reason is documented
    three lines above it."""
    p = tmp_path / "reg.json"
    # 99, not 999: the loader's own range check would reject 999 first, and then the
    # merge would never see the entry at all.
    p.write_text(json.dumps({"courses": [
        {"slug": "alpha", "title": "Alpha", "expect_sessions": 99}]}))
    r = C.Roster(DECLARED, path=p)
    assert r["alpha"]["expect_sessions"] == 5
    assert any("shadowed" in c for c in r.conflicts())


def test_a_title_that_only_differs_in_case_is_refused(tmp_path):
    """Case-only twins collide in scope's title->slug table and one becomes
    unaddressable."""
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"courses": [
        {"slug": "alpha2", "title": "ALPHA", "expect_sessions": 3}]}))
    r = C.Roster(DECLARED, path=p)
    assert "alpha2" not in r
    assert any("already declared" in c for c in r.conflicts())


@pytest.mark.parametrize("bad,why", [
    ({"title": "X", "expect_sessions": 3}, "missing slug"),
    ({"slug": "Not A Slug", "title": "X", "expect_sessions": 3}, "normalised slug"),
    ({"slug": "x", "expect_sessions": 3}, "missing title"),
    ({"slug": "x", "title": "X"}, "expect_sessions"),
    ({"slug": "x", "title": "X", "expect_sessions": 0}, "expect_sessions"),
    ({"slug": "x", "title": "X", "expect_sessions": True}, "expect_sessions"),
])
def test_a_malformed_entry_is_dropped_with_a_reason(tmp_path, bad, why):
    """A roster entry with no `expect_sessions` would make cmd_ingest raise KeyError and
    abort ingest for EVERY course, so it must never reach the roster."""
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"courses": [bad]}))
    entries, reasons = C.load_overlay(p)
    assert entries == []
    assert any(why in r for r in reasons), reasons


def test_a_corrupt_overlay_degrades_to_the_declared_roster_and_never_raises(tmp_path):
    """`config.constants` is imported at module scope elsewhere; it must stay
    importable whatever is in this file."""
    p = tmp_path / "reg.json"
    p.write_text("{ not json")
    r = C.Roster(DECLARED, path=p)
    assert sorted(r) == ["alpha"]
    assert r.load_reasons()

    p.write_text(json.dumps(["a", "list", "not", "an", "object"]))
    r2 = C.Roster(DECLARED, path=p)
    assert sorted(r2) == ["alpha"] and r2.load_reasons()


def test_a_duplicate_slug_inside_the_file_is_visible_not_last_wins(tmp_path):
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"courses": [
        {"slug": "b", "title": "B1", "expect_sessions": 2},
        {"slug": "b", "title": "B2", "expect_sessions": 3}]}))
    entries, reasons = C.load_overlay(p)
    assert [e["title"] for e in entries] == ["B1"]
    assert any("duplicate slug" in r for r in reasons)


# --------------------------------------------------- visible without a restart

def test_a_written_overlay_is_seen_by_an_already_built_roster(tmp_path):
    """The direct test of the module-caching problem: a long-running uvicorn holds the
    imported `COURSES` object, so it has to notice the file changing."""
    p = tmp_path / "reg.json"
    r = C.Roster(DECLARED, path=p)
    assert sorted(r) == ["alpha"]
    p.write_text(json.dumps({"courses": [
        {"slug": "gamma", "title": "Gamma", "expect_sessions": 4}]}))
    r.invalidate()
    assert sorted(r) == ["alpha", "gamma"]


def test_items_returns_a_snapshot_so_a_refresh_cannot_break_an_open_iteration(tmp_path):
    """`cmd_ingest` holds one `COURSES.items()` open for minutes while parsing 9 MB
    exports; a mutation mid-iteration would raise RuntimeError and kill the whole run."""
    p = tmp_path / "reg.json"
    r = C.Roster(DECLARED, path=p)
    it = r.items()
    p.write_text(json.dumps({"courses": [
        {"slug": "d", "title": "D", "expect_sessions": 1}]}))
    r.invalidate()
    assert isinstance(it, list)
    for _slug, _meta in it:          # must not raise
        pass


# ------------------------------------------------------------------ validation

@pytest.fixture()
def staged(tmp_path):
    """A real export, copied so the test can move it without touching the repo's."""
    src = Path(__file__).resolve().parents[1] / "data" / "courses" / "ai_for_finance.json"
    if not src.exists():
        pytest.skip("no course export available")
    dest = tmp_path / "up.json"
    shutil.copy(src, dest)
    return dest


def test_a_good_candidate_passes_and_reports_what_it_parsed(staged):
    v = C.validate(C.Candidate(title="Zeta Workshop", export_path=staged))
    assert v.ok and v.slug == "zeta_workshop"
    assert v.stats["records"] > 0 and v.stats["units"] > 0
    assert v.counted == v.stats["sessions"] and v.source == "position"
    assert any("blank" in w for w in v.warnings), "adopting a count is said out loud"


def test_a_duplicate_slug_or_title_is_refused(staged):
    v = C.validate(C.Candidate(title="AI for Finance", export_path=staged))
    assert not v.ok and v.stopped_at == "metadata"
    assert any("already exists" in r for r in v.reasons)


def test_a_title_that_makes_no_usable_slug_is_refused(staged):
    v = C.validate(C.Candidate(title="!!", export_path=staged))
    assert not v.ok and any("usable slug" in r for r in v.reasons)


def test_malformed_json_is_refused_with_a_position(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{ oops" + " " * 2000)
    v = C.validate(C.Candidate(title="Zeta Workshop", export_path=f))
    assert not v.ok and v.stopped_at == "json"
    assert any("line" in r and "column" in r for r in v.reasons)


def test_an_export_with_the_wrong_field_names_is_caught_by_the_record_count(tmp_path):
    """`portal.read_course` never raises on shape surprises: the documented failure is
    that `content_markdown` instead of `content` yields 0 records and no error."""
    f = tmp_path / "plausible.json"
    f.write_text(json.dumps([{
        "course_title": "Zeta", "topics": [{"topic_name": "T", "order": 1, "units": [
            {"unit_id": "u1", "unit_name": "S1", "unit_type": "LEARNING_SET",
             "order": 1, "contents": [
                 {"learning_resource_type": "INTERACTIVE_VIDEO"},
                 {"content_markdown": "wrong field name" * 200}]}]}]}]))
    v = C.validate(C.Candidate(title="Zeta Workshop", export_path=f))
    assert not v.ok and v.stopped_at == "records"
    assert any("0 records" in r for r in v.reasons)
    assert v.stats["units"] > 0, "the units parsed, which is what makes it plausible"


def test_a_session_count_mismatch_is_refused_and_shows_what_it_found(staged):
    v = C.validate(C.Candidate(title="Zeta Workshop", export_path=staged,
                               expect_sessions=99))
    assert not v.ok and v.stopped_at == "sessions"
    # 18 is what the fixture export actually contains; the point is that the refusal
    # SHOWS both numbers rather than just saying no.
    assert any("99" in r and "18" in r for r in v.reasons)


def test_a_non_course_json_is_refused_on_shape(tmp_path):
    f = tmp_path / "notacourse.json"
    f.write_text(json.dumps({"hello": "world", "pad": "x" * 2000}))
    v = C.validate(C.Candidate(title="Zeta Workshop", export_path=f))
    assert not v.ok and v.stopped_at == "shape"
    assert any("topics" in r for r in v.reasons)


# ------------------------------------------- workbook keys stay roster-driven

def test_the_three_shipped_workbook_filenames_still_resolve():
    """The legacy aliases are load-bearing: "Intro to Generative AI" normalises to
    `introtogenerativeai`, while the slug and title both give `introtogenai` — neither
    is a substring of the other, so derivation alone loses the biggest course."""
    from miw.ingest.sheets import course_for_workbook_detail as d
    assert d("Intro to Generative AI - Course Contents.xlsx")[0] == "Intro to Gen AI"
    assert d("AI for Finance - Course Contents.xlsx")[0] == "AI for Finance"
    assert d("Building LLM Applications - Course Contents.xlsx")[0] \
        == "Building LLM Applications"


def test_an_unknown_workbook_still_maps_to_nothing():
    """Returning "" is the phantom-course protection; a filename fallback invented
    three phantom courses once."""
    from miw.ingest.sheets import course_for_workbook_detail as d
    title, why = d("Some Other Book.xlsx")
    assert title == "" and "no course" in why


def test_a_short_roster_key_cannot_steal_another_courses_workbook():
    """A course slugged `ai` would match `aiforfinance` under naive substring matching."""
    from miw.ingest import sheets
    assert sheets._MIN_SUBSTRING_KEY >= 5
    # A 4-char stem is still below the floor and must match its own file by equality.
    assert sheets.norm_stem("Zeta - Course Contents.xlsx") == "zeta"
