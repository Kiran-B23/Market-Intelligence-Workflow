"""The detail panel's contract: where it is, what proves it, what to change.

The card answers "what broke". This endpoint answers the three questions asked next,
and each has its own way of going wrong:

  * WHERE must join back to the inventory. `score.py` stores `dep.locations[:12]`, so
    an endpoint that read `Finding.locations` would under-report the busiest course and
    look correct while doing it.
  * WHAT PROVES IT must distinguish "no citation" from "our own probe is the
    observation" - an empty evidence list reads as an unsourced finding.
  * WHAT TO CHANGE must be the projected triad, because the global one names other
    courses' sessions.

Plus the reachability trap that made the panel necessary to test at all: on a re-run
with no news every finding is `unchanged`, so the standing list is the only list on the
page and its rows must carry `finding_id`.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.api import app as api
from miw.extract import locate
from miw.schema import Claim, Dependency, Location, to_jsonable

INTRO, APPS = "Intro to Gen AI", "Building LLM Applications"

COURSE = [{
    "course_title": INTRO,
    "topics": [{"topic_name": "Workflows", "order": 1, "units": [{
        "unit_id": "u-1", "unit_name": "Coding Practice", "unit_type": "LEARNING_SET",
        "order": 1,
        "contents": [
            {"learning_resource_type": "INTERACTIVE_VIDEO"},
            {"question_id": "q-1", "object_type": "OBJECTIVE_QUESTIONS",
             "title": "AI News Summarizer",
             "content": "The node calls gemini-2.0-flash today."},
        ]}]}]}]

PATH = "[0].topics[0].units[0].contents[1].content"


def _dep() -> Dependency:
    """13 locations in this course, so the 12-location truncation would show."""
    locs = [Location(course=INTRO, topic_name="Workflows", unit_id="u-1",
                     unit_name="Coding Practice", content_id=f"q-{i}",
                     field_path=PATH, evidence_source="model_id",
                     object_type="OBJECTIVE_QUESTIONS", session_no=11)
            for i in range(13)]
    locs.append(Location(course=INTRO, topic_name="(from workbook)", unit_id="",
                         unit_name="Session 4", content_id="",
                         field_path="Intro.xlsx::Entity Ids", evidence_source="sheet_declared",
                         object_type="SHEET"))
    locs.append(Location(course=APPS, topic_name="Apps", unit_id="u-9",
                         unit_name="Elsewhere", content_id="q-x",
                         field_path=PATH, evidence_source="model_id",
                         object_type="CODING_QUESTIONS", session_no=3))
    return Dependency(kind="model", canonical_name="gemini-2.0-flash",
                      homepage="https://ai.google.dev/gemini-api/docs/models",
                      official_domains=["ai.google.dev"], vendor="Google",
                      watch_tier="critical", locations=locs)


DEP = _dep()

FINDING = {
    "finding_id": "f-1", "dep_id": DEP.dep_id, "canonical_name": DEP.canonical_name,
    "signal": "S7", "signal_label": "taught model id retired", "severity": "high",
    "kind_of_signal": "regression", "diff_class": "unchanged",
    "summary": "gemini-2.0-flash is shut down at Google",
    "probe_signals": ["model_shutdown_passed"], "blast_radius": 60,
    "graded_locations": 14, "questions_executing": 0, "courses": [INTRO, APPS],
    "locations": [], "claims": [], "alternatives": [],
    "what_to_act": "global wording", "why_to_act": "global why",
    "when_to_act": "global when", "recommendation": "global rec",
}


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """Point the API at a synthetic run and a synthetic export."""
    out = tmp_path / "out"
    out.mkdir()
    # `to_jsonable` is the project's own serialiser; `Dependency` carries a non-field
    # set (`_loc_keys`) that a naive `__dict__` dump chokes on.
    (out / "inventory.json").write_text(json.dumps(
        {"dependencies": [to_jsonable(DEP)]}))
    (out / "findings_2026-01-01.json").write_text(json.dumps(
        {"analysed_at": "2026-01-01", "findings": [FINDING], "coverage": {}}))
    monkeypatch.setattr(api, "OUT", out)

    courses = tmp_path / "courses"
    courses.mkdir()
    (courses / "intro_to_gen_ai.json").write_text(json.dumps(COURSE))
    monkeypatch.setattr(locate, "COURSES_DIR", courses)
    locate.clear_cache()
    yield
    locate.clear_cache()


# ------------------------------------------------------------------ where

def test_where_shows_every_course_location_not_the_twelve_the_finding_stores(wired):
    """The whole reason the endpoint joins back to the inventory."""
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert d["projection"] == "ok"
    assert d["locations_total"] == 14, "13 export + 1 workbook, in this course only"
    assert d["locations_shown"] == 14, "well under the cap, so all of them"
    assert all(w["course"] == INTRO for w in d["where"]), "no other course's locations"


def test_a_global_view_spans_courses(wired):
    d = api.finding_detail("f-1")
    assert d["projection"] == "global"
    assert d["locations_total"] == 15
    assert {w["course"] for w in d["where"]} == {INTRO, APPS}


def test_graded_locations_are_ordered_first(wired):
    d = api.finding_detail("f-1")
    types = [w["object_type"] for w in d["where"]]
    assert types[0] in ("CODING_QUESTIONS", "OBJECTIVE_QUESTIONS")
    assert types[-1] == "SHEET", "the unresolvable workbook row sorts last"


def test_each_export_location_carries_a_real_excerpt_with_the_match_located(wired):
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    ex = next(w["excerpt"] for w in d["where"] if w["evidence_source"] == "model_id")
    assert ex["resolved"] and ex["spans"]
    s, ln = ex["spans"][0]
    assert ex["excerpt"][s:s + ln] == "gemini-2.0-flash"
    assert ex["container"]["title"] == "AI News Summarizer"


def test_a_workbook_location_reports_why_it_has_no_excerpt(wired):
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    ex = next(w["excerpt"] for w in d["where"] if w["evidence_source"] == "sheet_declared")
    assert not ex["resolved"] and ex["reason"] == "sheet_declaration"


def test_the_locations_cap_is_reported_rather_than_silently_applied(wired, monkeypatch):
    monkeypatch.setattr(api, "DETAIL_LOCATION_CAP", 3)
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert d["locations_shown"] == 3 and d["locations_total"] == 14


# ------------------------------------------------------------------ what proves it

def test_a_probe_only_finding_says_so_instead_of_looking_unsourced(wired):
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert d["evidence"] == []
    assert d["probe_only"] is True
    assert d["probe_signals"] == ["model_shutdown_passed"]


def test_a_cited_finding_carries_the_quote_tier_and_retrieval_date(wired, tmp_path):
    # Built through `Claim.build`, not hand-assembled: the tier is DERIVED by
    # `trust.classify` from the source URL and the subject's authority set, and a test
    # that stamped AUTHORITATIVE itself would be asserting past the one check that
    # makes evidence trustworthy.
    from miw.schema import ClaimKind
    from miw.trust import Subject
    subject = Subject(name="gemini-2.0-flash",
                      official_domains=("ai.google.dev",))
    c = Claim.build(kind=ClaimKind.DEPRECATION, subject=subject,
                    statement="shut down", source_url="https://ai.google.dev/x",
                    quote="gemini-2.0-flash is shut down and no longer available")
    assert c.tier.name == "AUTHORITATIVE", "the fixture must be genuinely authoritative"
    row = {**FINDING, "claims": [to_jsonable(c)]}
    (api.OUT / "findings_2026-01-01.json").write_text(json.dumps(
        {"analysed_at": "2026-01-01", "findings": [row], "coverage": {}}))
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert d["probe_only"] is False
    assert d["evidence"][0]["source_url"] == "https://ai.google.dev/x"
    assert "shut down" in d["evidence"][0]["quote"]


# ------------------------------------------------------------------ what to change

def test_the_triad_is_the_projected_one_not_the_global_wording(wired):
    """The global triad names other courses' sessions, so it must be re-derived."""
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert d["change"]["what_to_act"] != "global wording"
    assert INTRO in d["change"]["what_to_act"] or "session" in d["change"]["what_to_act"]
    assert d["change"]["due_by"], "a sortable deadline comes with the triad"


def test_the_other_courses_are_named_so_a_course_page_does_not_silo(wired):
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert [a["course"] for a in d["also_in"]] == [APPS]


# ------------------------------------------------------------------ failure paths

def test_an_unknown_finding_is_a_404(wired):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        api.finding_detail("nope", course="intro_to_gen_ai")
    assert e.value.status_code == 404


def test_a_finding_that_does_not_touch_the_course_says_so(wired):
    d = api.finding_detail("f-1", course="ai_for_finance")
    assert d["projection"] == "not_in_course"
    assert d["locations_total"] == 0


# ------------------------------------------------------------------ deep links

def test_no_configured_pattern_means_no_link_rather_than_a_guessed_one(wired):
    """A guessed URL that 404s cannot be told apart from a unit that moved."""
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert d["deep_links_configured"] is False
    assert all(w["deep_link"] == "" for w in d["where"])


def test_a_configured_pattern_produces_a_link_per_location(wired, monkeypatch):
    import config.settings as settings
    monkeypatch.setattr(settings, "PLATFORM_UNIT_URL",
                        "https://learn.example.com/{course_slug}/u/{unit_id}")
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert d["deep_links_configured"] is True
    link = next(w["deep_link"] for w in d["where"] if w["unit_id"])
    assert link == "https://learn.example.com/intro_to_gen_ai/u/u-1"


def test_a_pattern_naming_an_unknown_placeholder_yields_no_link(wired, monkeypatch):
    """A configuration error must not render a half-built URL."""
    import config.settings as settings
    monkeypatch.setattr(settings, "PLATFORM_UNIT_URL", "https://x/{not_a_field}")
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert all(w["deep_link"] == "" for w in d["where"])


# ------------------------------------------------- reachability from the list

def test_standing_rows_carry_the_id_so_the_panel_is_reachable(wired):
    """On a re-run with no news EVERY finding is `unchanged` and standing is all there is."""
    r = api.findings(course="intro_to_gen_ai")
    assert r["findings"] == [], "the fixture finding is unchanged"
    assert len(r["standing"]) == 1
    row = r["standing"][0]
    assert row["finding_id"] == "f-1"
    assert api.finding_detail(row["finding_id"], course="intro_to_gen_ai")["projection"] == "ok"


# ------------------------------------- which URLs may highlight anything

def test_only_urls_naming_the_dependency_may_highlight_it():
    """Measured on the live inventory: `@n8n/n8n-nodes-langchain.agent` carries 31
    referenced URLs, Gmail's docs page among them, because `inventory._links` attributes
    a link by DOMAIN - every `docs.n8n.io/...` link lands on whichever entry owns that
    host. Highlighting Gmail's link under an Agent finding sends the reviewer to the
    wrong line, so a URL must name the dependency to be used.
    """
    dep = Dependency(kind="n8n_node", canonical_name="@n8n/n8n-nodes-langchain.agent",
                     registry="n8n", registry_id="@n8n/n8n-nodes-langchain",
                     referenced_urls=[
                         "https://docs.n8n.io/",                       # root: no path
                         "https://docs.n8n.io/x/n8n-nodes-langchain.agent/",   # its own
                         "https://docs.n8n.io/x/n8n-nodes-base.gmail/",        # another's
                     ])
    kept = api._self_referring_urls(dep)
    assert kept == ["https://docs.n8n.io/x/n8n-nodes-langchain.agent"]


def test_the_shared_package_id_is_never_a_highlight_term(tmp_path, monkeypatch):
    """`registry_id` is `@n8n/n8n-nodes-langchain`, carried by 20 dependencies, so
    highlighting it marks the package prefix of a DIFFERENT node's identifier.

    Asserted behaviourally: the content below names the package (inside another node's
    type) but never this node, so nothing may be highlighted.
    """
    node_dep = Dependency(
        kind="n8n_node", canonical_name="@n8n/n8n-nodes-langchain.agent",
        registry="n8n", registry_id="@n8n/n8n-nodes-langchain",
        locations=[Location(course=INTRO, topic_name="T", unit_id="u-1",
                            unit_name="S1", content_id="q-1", field_path=PATH,
                            evidence_source="link:a_href",
                            object_type="OBJECTIVE_QUESTIONS", session_no=1)])
    doc = [{"course_title": INTRO, "topics": [{"topic_name": "T", "order": 1, "units": [{
        "unit_id": "u-1", "unit_name": "S1", "unit_type": "LEARNING_SET", "order": 1,
        "contents": [{"learning_resource_type": "INTERACTIVE_VIDEO"},
                     {"question_id": "q-1", "title": "Other node",
                      "content": 'type: "@n8n/n8n-nodes-langchain.chatTrigger"'}]}]}]}]

    out = tmp_path / "out"; out.mkdir()
    (out / "inventory.json").write_text(json.dumps({"dependencies": [to_jsonable(node_dep)]}))
    (out / "findings_2026-01-01.json").write_text(json.dumps({
        "analysed_at": "2026-01-01", "coverage": {},
        "findings": [{**FINDING, "dep_id": node_dep.dep_id,
                      "canonical_name": node_dep.canonical_name}]}))
    monkeypatch.setattr(api, "OUT", out)
    courses = tmp_path / "courses"; courses.mkdir()
    (courses / "intro_to_gen_ai.json").write_text(json.dumps(doc))
    monkeypatch.setattr(locate, "COURSES_DIR", courses)
    locate.clear_cache()

    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    ex = d["where"][0]["excerpt"]
    assert ex["resolved"], "the path itself is fine"
    assert ex["spans"] == [], "the shared package prefix must not be highlighted"
    assert ex["reason"] == "term_not_in_field"
    locate.clear_cache()


# ------------------------------- occurrences grouped by session, and reachable

def test_occurrences_are_grouped_by_session_over_the_full_list(wired):
    """Grouping needs no excerpt, so it covers every occurrence, not just the page.

    The flat list it replaces rendered up to 40 cards of 230px excerpt in one scroll.
    """
    d = api.finding_detail("f-1", course="intro_to_gen_ai")
    labels = [g["label"] for g in d["groups"]]
    assert labels == ["Session 11", "Workbook declarations"]
    assert sum(g["count"] for g in d["groups"]) == d["locations_total"] == 14


def test_a_group_reports_what_a_reviewer_needs_to_choose_with(wired):
    g = next(g for g in api.finding_detail("f-1", course="intro_to_gen_ai")["groups"]
             if g["session_no"] == 11)
    assert g["count"] == 13 and g["graded"] == 13
    assert g["object_types"] == {"OBJECTIVE_QUESTIONS": 13}
    assert "Coding Practice" in g["units"]


def test_the_workbook_group_is_marked_as_having_no_excerpt(wired):
    """A `Book.xlsx::Sheet` path names no cell, so it can never resolve one. Saying so
    once per group beats repeating it on every row."""
    g = next(g for g in api.finding_detail("f-1", course="intro_to_gen_ai")["groups"]
             if g["session_no"] is None)
    assert g["count"] == 1 and g["resolvable"] == 0
    assert g["label"] == "Workbook declarations"


def test_numbered_sessions_sort_before_the_unnumbered_group(wired):
    groups = api.finding_detail("f-1", course="intro_to_gen_ai")["groups"]
    assert [g["session_no"] for g in groups] == [11, None]


def test_expanding_one_session_resolves_only_that_sessions_excerpts(wired):
    """This is what makes every occurrence reachable without resolving all of them."""
    d = api.finding_detail("f-1", course="intro_to_gen_ai", session="11")
    assert d["locations_shown"] == 13
    assert {w["session_no"] for w in d["where"]} == {11}
    assert d["session"] == "11"


def test_the_workbook_group_can_be_expanded_by_name(wired):
    d = api.finding_detail("f-1", course="intro_to_gen_ai", session="workbook")
    assert d["locations_shown"] == 1
    assert d["where"][0]["evidence_source"] == "sheet_declared"


def test_offset_pages_through_the_full_list_so_nothing_is_unreachable(wired, monkeypatch):
    """The flat panel showed 40 of 776 and offered no control to see the rest."""
    monkeypatch.setattr(api, "DETAIL_LOCATION_CAP", 5)
    first = api.finding_detail("f-1", course="intro_to_gen_ai")
    assert first["locations_shown"] == 5 and first["has_more"] is True
    seen = [w["content_id"] for w in first["where"]]
    off = 5
    while True:
        page = api.finding_detail("f-1", course="intro_to_gen_ai", offset=off)
        seen += [w["content_id"] for w in page["where"]]
        if not page["has_more"]:
            break
        off += page["locations_shown"]
    assert len(seen) == first["locations_total"] == 14, "every occurrence was reachable"


def test_a_negative_offset_is_clamped_rather_than_wrapping(wired):
    d = api.finding_detail("f-1", course="intro_to_gen_ai", offset=-5)
    assert d["offset"] == 0 and d["locations_shown"] == 14


def test_an_unknown_session_returns_an_empty_page_not_an_error(wired):
    d = api.finding_detail("f-1", course="intro_to_gen_ai", session="999")
    assert d["locations_shown"] == 0 and d["where"] == []
