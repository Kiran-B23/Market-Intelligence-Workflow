"""Resolving a `field_path` back to the content it came from, and failing honestly.

The panel this feeds exists to answer "where is this and what do I change", so the
tests that matter most are the ones about *not* answering: a location that cannot
resolve has to say which of several quite different reasons applies. A blank excerpt
that could mean either "nothing there" or "we could not look" is the failure mode.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.extract import locate


# A course shaped like a real portal export, small enough to assert against exactly.
COURSE = [{
    "course_title": "Demo",
    "topics": [{
        "topic_name": "Workflows",
        "order": 1,
        "units": [{
            "unit_id": "u-1", "unit_name": "Session 1", "unit_type": "LEARNING_SET",
            "order": 1,
            "contents": [
                {"learning_resource_type": "INTERACTIVE_VIDEO"},
                {"question_id": "q-1", "object_type": "OBJECTIVE_QUESTIONS",
                 "content_type": "MARKDOWN", "title": "AI News Summarizer",
                 "content": "Pick the model.\nWe call gemini-2.0-flash in the node.\n",
                 "solutions": [{"solution_answer":
                                "prefix " * 200 + '"modelName": "models/gemini-2.0-flash",'
                                + " suffix" * 200}]},
            ]}]}]}]


@pytest.fixture()
def course_dir(tmp_path, monkeypatch):
    (tmp_path / "demo.json").write_text(json.dumps(COURSE))
    monkeypatch.setattr(locate, "COURSES_DIR", tmp_path)
    locate.clear_cache()
    yield tmp_path
    locate.clear_cache()


# ------------------------------------------------------------------ path parsing

def test_a_real_export_path_parses_to_alternating_keys_and_indices():
    assert locate.parse_path(
        "[0].topics[4].units[9].contents[0].solutions[0].solution_answer"
    ) == [0, "topics", 4, "units", 9, "contents", 0, "solutions", 0, "solution_answer"]


def test_a_workbook_location_is_not_a_path_at_all():
    """Sheet locations carry `Book.xlsx::Sheet`, which must not parse as a path."""
    assert locate.parse_path("Intro.xlsx::Entity Ids - Tools") is None


@pytest.mark.parametrize("bad", ["", "!!!", "[0].topics[4]!", "topics..units",
                                 "[0].topics[x]"])
def test_a_malformed_path_is_rejected_rather_than_partially_walked(bad):
    """A silently skipped segment resolves to the WRONG node, which is worse than none."""
    assert locate.parse_path(bad) is None


# ------------------------------------------------------------------ resolution

def test_a_resolvable_location_returns_the_text_and_locates_the_match(course_dir):
    r = locate.resolve("demo", "[0].topics[0].units[0].contents[1].content",
                       ["gemini-2.0-flash"])
    assert r["resolved"] and r["reason"] == ""
    assert r["spans"], "the matched term must be located, not merely asserted"
    s, ln = r["spans"][0]
    assert r["excerpt"][s:s + ln] == "gemini-2.0-flash"


def test_every_span_indexes_into_the_excerpt_not_the_whole_field(course_dir):
    """The offsets are what the UI highlights with, so they are relative to the slice.

    The field here is ~2.8kB and the excerpt a small window inside it; spans returned
    against the full field would highlight the wrong characters, or none.
    """
    r = locate.resolve("demo",
                       "[0].topics[0].units[0].contents[1].solutions[0].solution_answer",
                       ["gemini-2.0-flash"])
    assert r["resolved"] and r["truncated"], "a long field must be windowed"
    assert len(r["excerpt"]) < r["text_len"]
    for s, ln in r["spans"]:
        assert 0 <= s and s + ln <= len(r["excerpt"])
        assert r["excerpt"][s:s + ln].lower() == "gemini-2.0-flash"


def test_the_longest_term_wins_so_an_alias_cannot_shadow_the_id(course_dir):
    """With both `gemini` and `gemini-2.0-flash` as terms, the id must be highlighted.

    Matching the short alias first would highlight six characters of the very id the
    reviewer opened the panel to find.
    """
    r = locate.resolve("demo", "[0].topics[0].units[0].contents[1].content",
                       ["gemini", "gemini-2.0-flash"])
    s, ln = r["spans"][0]
    assert r["excerpt"][s:s + ln] == "gemini-2.0-flash"


def test_the_enclosing_question_is_named_so_the_panel_can_say_which_item(course_dir):
    r = locate.resolve("demo", "[0].topics[0].units[0].contents[1].content",
                       ["gemini-2.0-flash"])
    assert r["container"]["title"] == "AI News Summarizer"
    assert r["container"]["object_type"] == "OBJECTIVE_QUESTIONS"


# ------------------------------------------------- the failures, each distinguished

def test_a_workbook_declaration_says_so_rather_than_looking_empty(course_dir):
    r = locate.resolve("demo", "Intro.xlsx::Tools", ["x"])
    assert not r["resolved"] and r["reason"] == "sheet_declaration"


def test_a_stale_path_is_not_found_rather_than_silently_blank(course_dir):
    r = locate.resolve("demo", "[0].topics[9].units[0].contents[0].content", ["x"])
    assert not r["resolved"] and r["reason"] == "path_not_found"


def test_a_missing_export_is_distinguished_from_a_missing_path(course_dir):
    r = locate.resolve("no_such_course", "[0].topics[0]", ["x"])
    assert not r["resolved"] and r["reason"] == "course_export_missing"


def test_a_path_to_a_structure_is_not_treated_as_text(course_dir):
    r = locate.resolve("demo", "[0].topics[0].units[0]", ["x"])
    assert not r["resolved"] and r["reason"] == "not_a_text_field"


def test_a_resolved_field_that_lacks_the_term_is_flagged_not_highlighted(course_dir):
    """The path resolved, so `resolved` is True - but nothing may be highlighted.

    This is a real signal (the content was edited, or the location was matched by a
    broader rule), so it must not be dressed up as a successful match.
    """
    r = locate.resolve("demo", "[0].topics[0].units[0].contents[1].content",
                       ["not-in-there"])
    assert r["resolved"] and r["reason"] == "term_not_in_field"
    assert r["spans"] == []


def test_a_one_character_term_cannot_highlight_the_whole_field(course_dir):
    """Guards against a junk registry name turning the excerpt into confetti."""
    r = locate.resolve("demo", "[0].topics[0].units[0].contents[1].content", ["e"])
    assert r["spans"] == []


# ------------------------------------------------------------------ caching

def test_a_rewritten_export_is_picked_up_without_a_restart(course_dir):
    import os
    p = course_dir / "demo.json"
    first = locate.resolve("demo", "[0].topics[0].units[0].contents[1].content",
                           ["gemini-2.0-flash"])
    assert first["resolved"]

    edited = json.loads(json.dumps(COURSE))
    edited[0]["topics"][0]["units"][0]["contents"][1]["content"] = "now uses gemini-3-pro"
    p.write_text(json.dumps(edited))
    os.utime(p, (0, 0))                        # force a distinct mtime

    again = locate.resolve("demo", "[0].topics[0].units[0].contents[1].content",
                           ["gemini-3-pro"])
    assert again["resolved"] and again["spans"], "the cache must key on mtime"


# ------------------------------------------- name matches beat URL matches

URLDOC = [{
    "course_title": "Demo",
    "topics": [{"topic_name": "T", "order": 1, "units": [{
        "unit_id": "u-1", "unit_name": "S1", "unit_type": "LEARNING_SET", "order": 1,
        "contents": [
            {"question_id": "q-1", "title": "Both",
             "content": "See https://docs.x.io/nodes/thing and call thing-v2 in code."},
            {"question_id": "q-2", "title": "URL only",
             "content": "See https://docs.x.io/nodes/thing for details."},
        ]}]}]}]


@pytest.fixture()
def urldir(tmp_path, monkeypatch):
    (tmp_path / "u.json").write_text(json.dumps(URLDOC))
    monkeypatch.setattr(locate, "COURSES_DIR", tmp_path)
    locate.clear_cache()
    yield
    locate.clear_cache()


def test_the_name_wins_when_both_a_name_and_a_url_are_present(urldir):
    """A URL is weaker evidence that THIS line is the thing to edit.

    Passed as one list, the long URL would win on length alone and the reviewer would
    be pointed at a link instead of the call.
    """
    r = locate.resolve("u", "[0].topics[0].units[0].contents[0].content",
                       ["thing-v2"], ["https://docs.x.io/nodes/thing"])
    assert r["matched_by"] == "name"
    s, ln = r["spans"][0]
    assert r["excerpt"][s:s + ln] == "thing-v2"


def test_a_url_match_is_used_only_when_the_name_is_absent_and_is_labelled(urldir):
    r = locate.resolve("u", "[0].topics[0].units[0].contents[1].content",
                       ["thing-v2"], ["https://docs.x.io/nodes/thing"])
    assert r["matched_by"] == "referenced_url"
    s, ln = r["spans"][0]
    assert r["excerpt"][s:s + ln] == "https://docs.x.io/nodes/thing"


def test_no_fallback_leaves_the_location_honestly_unmatched(urldir):
    r = locate.resolve("u", "[0].topics[0].units[0].contents[1].content", ["thing-v2"])
    assert r["resolved"] and r["reason"] == "term_not_in_field"
    assert r["matched_by"] == "" and r["spans"] == []


# ---------------------------------------- slide outline: a workbook CELL resolves

def test_a_slide_outline_path_resolves_to_the_workbook_cell(tmp_path, monkeypatch):
    """The slide outline is the only textual record of a session's deck.

    `ingest.outline` writes `Book.xlsx::Course Outline::Outline::row7`, which unlike a
    tool-sheet path names a specific cell — so it can and must be resolved. Showing
    nothing here would hide the very stream the MCQs were written from.
    """
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Course Outline"
    # Deliberately not the first column: the resolver addresses by header, not index.
    ws.append(["Session No.", "Session Name", "Key Takeaways", "Outline"])
    ws.append([1, "S One", "Learn it", "- Gamma AI turns ideas into slides\n- DeepWiki"])
    book = "Demo - Course Contents.xlsx"
    wb.save(tmp_path / book)
    monkeypatch.setattr(locate, "SHEETS_DIR", tmp_path)
    locate.clear_cache()

    r = locate.resolve("anything", f"{book}::Course Outline::Outline::row2", ["Gamma AI"])
    assert r["resolved"] and r["reason"] == ""
    s, ln = r["spans"][0]
    assert r["excerpt"][s:s + ln] == "Gamma AI"
    assert r["container"]["object_type"] == "SESSION_PPT", \
        "the panel must be able to say this is slide text, not course content"
    locate.clear_cache()


def test_a_tool_sheet_path_is_still_not_resolvable(tmp_path, monkeypatch):
    """`Book.xlsx::Sheet` names no cell, so it must stay a sheet_declaration."""
    monkeypatch.setattr(locate, "SHEETS_DIR", tmp_path)
    r = locate.resolve("x", "Book.xlsx::Entity Ids - Tools & Versions U", ["y"])
    assert not r["resolved"] and r["reason"] == "sheet_declaration"


def test_a_vanished_workbook_cell_is_reported_distinctly(tmp_path, monkeypatch):
    monkeypatch.setattr(locate, "SHEETS_DIR", tmp_path)
    locate.clear_cache()
    r = locate.resolve("x", "Gone.xlsx::Course Outline::Outline::row2", ["y"])
    assert not r["resolved"] and r["reason"] == "workbook_cell_missing"
    locate.clear_cache()
