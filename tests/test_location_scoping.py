"""A finding may only claim the places its own signal can reach.

Every test here corresponds to a defect measured on the 2026-09-18 run, where a finding
inherited `dep.locations[:12]` regardless of what it was about:

    Composio     S1 "mcp.composio.dev/dashboard returns 404"   12 shown / 6 links
    Stability AI S1                                            10 shown / 6 links
    Ngrok        S1                                             9 shown / 2 links

and where the recommendation printed the location's `unit_name`, which in this
curriculum is "Coding Practice" for the unit holding the MCQ bank - so 25 of 43 findings
announced "Coding Practice" and not one of them pointed at a coding question.
"""
import pytest

from config.constants import artifact_word
from miw.analyse.score import (SIGNAL_EVIDENCE, reaching_locations, recommend,
                               scope_locations, where_line)
from miw.schema import Dependency, Finding, Location

COURSE = "Intro to Gen AI"


def loc(evidence, object_type="LEARNING_RESOURCE", session=1, url="", unit="Reading"):
    return Location(course=COURSE, topic_name="T", unit_id="u1", unit_name=unit,
                    content_id="c1", field_path="f", evidence_source=evidence,
                    object_type=object_type, session_no=session, url=url)


DEAD = "https://mcp.composio.dev/dashboard"
LIVE = "https://app.composio.dev/"


def composio():
    return Dependency(
        kind="tool", canonical_name="Composio",
        locations=[loc("link:a_href", "OBJECTIVE_QUESTIONS", 24, DEAD),
                   loc("link:a_href", "OBJECTIVE_QUESTIONS", 24, DEAD),
                   loc("link:a_href", "CODING_QUESTIONS", 25, LIVE),
                   loc("sheet_declared", "SHEET", None),
                   loc("prose_name", "OBJECTIVE_QUESTIONS", 24, unit="Coding Practice"),
                   loc("prose_name", "OBJECTIVE_QUESTIONS", 24, unit="Coding Practice")])


def finding(signal="S1", urls=()):
    f = Finding(dep_id="d", canonical_name="Composio", signal=signal,
                signal_label="Dead / moved URL", severity="high")
    f.probe_signals = ["url_gone"]
    f.affected_urls = list(urls)
    return f


def test_a_dead_url_does_not_touch_a_quiz_that_merely_names_the_tool():
    dep, f = composio(), finding(urls=[DEAD])
    scope_locations(dep, f)
    assert len(f.locations) == 2
    assert {l.url for l in f.locations} == {DEAD}
    # The prose mentions and the sheet row are kept, not deleted - they are just not
    # claimed as affected.
    assert len(f.mention_locations) == 4
    assert f.locations_scoped is True


def test_without_a_named_url_the_rule_still_stops_at_link_evidence():
    dep, f = composio(), finding()          # no affected_urls
    scope_locations(dep, f)
    assert {l.evidence_source for l in f.locations} == {"link:a_href"}
    assert len(f.locations) == 3


def test_a_signal_that_reaches_nothing_refuses_rather_than_showing_everything():
    """S9 is about n8n workflows. Composio has none, so the honest answer is 'unknown'."""
    dep = composio()
    f = finding(signal="S9")
    scope_locations(dep, f)
    assert f.locations == []
    assert f.locations_scoped is False
    assert f.mention_locations                       # still browsable
    assert "not determined" in where_line(dep, f)


def test_pricing_and_deprecation_reach_every_place_it_is_taught():
    for signal in ("S3", "S4"):
        dep, f = composio(), finding(signal=signal)
        scope_locations(dep, f)
        assert len(f.locations) == len(dep.locations), signal


def test_every_declared_signal_has_a_rule_or_is_deliberately_unscoped():
    for signal in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"):
        assert signal in SIGNAL_EVIDENCE


# --------------------------------------------------------------- the wording

def test_the_starts_at_line_names_the_artifact_not_the_unit():
    """"Coding Practice" was printed for quiz questions on 25 of 43 findings."""
    dep, f = composio(), finding(urls=[DEAD])
    scope_locations(dep, f)
    line = where_line(dep, f)
    assert "quiz question" in line
    assert "Coding Practice" not in line
    assert "Starts at" not in line


def test_the_exemplar_is_the_place_that_executes_it():
    dep = Dependency(kind="package", canonical_name="langchain", registry="pypi",
                     locations=[loc("prose_name", "OBJECTIVE_QUESTIONS", 2),
                                loc("solution_import", "CODING_QUESTIONS", 9)])
    f = finding(signal="S6")
    scope_locations(dep, f)
    # Only the runtime location survives S6's rule, and it is what the line names.
    assert "coding practice" in where_line(dep, f)


def test_the_s1_sentence_and_the_list_under_it_agree():
    dep, f = composio(), finding(urls=[DEAD])
    scope_locations(dep, f)
    text = recommend(dep, f)
    assert f"in the {len(f.locations)} place(s)" in text


def test_reaching_locations_gives_the_same_answer_for_dicts_and_objects():
    """The API reads serialised locations; the analyser reads objects."""
    dep = composio()
    as_obj, _ = reaching_locations("S1", [DEAD], dep.locations)
    as_dict, _ = reaching_locations("S1", [DEAD], [
        {"evidence_source": l.evidence_source, "object_type": l.object_type,
         "url": l.url} for l in dep.locations])
    assert len(as_obj) == len(as_dict) == 2


def test_artifact_words_cover_every_object_type_the_extractor_emits():
    for ot in ("OBJECTIVE_QUESTIONS", "CODING_QUESTIONS", "LEARNING_RESOURCE",
               "SESSION_PPT", "SHEET", "UNIT_TAG"):
        assert artifact_word(ot) != "place"


def test_a_free_probe_is_not_rationed_by_a_research_budget():
    """`watch_tier` rations research. An n8n node probe reads one cached source tree.

    Nine of the 41 taught nodes are `mention-only`, so a tiered probe skipped them
    entirely - and a reference table listing a node n8n has removed then stood for ever
    with nothing looking at it.
    """
    from miw.probe.runner import probe_all
    from miw.scope import Scope
    from miw.state import State

    mention_node = Dependency(kind="n8n_node", canonical_name="n8n-nodes-base.code",
                              registry="n8n", watch_tier="mention-only",
                              locations=[loc("n8n_mention", "OBJECTIVE_QUESTIONS")])
    mention_tool = Dependency(kind="tool", canonical_name="SomeTool",
                              watch_tier="mention-only",
                              locations=[loc("prose_name", "OBJECTIVE_QUESTIONS")])
    wired = Dependency(kind="n8n_node", canonical_name="n8n-nodes-base.gmail",
                       registry="n8n", watch_tier="critical",
                       locations=[loc("n8n_workflow", "OBJECTIVE_QUESTIONS")])

    seen = []
    state = State(":memory:") if _state_takes_path() else State()
    try:
        probe_all([mention_node, mention_tool, wired], state,
                  scope=Scope(tiers={"critical", "standard"}),
                  progress=lambda i, n, r: seen.append(r.canonical_name))
    finally:
        state.close()
    # the mention-only n8n node is probed anyway; the mention-only TOOL is not
    assert "n8n-nodes-base.code" in seen
    assert "n8n-nodes-base.gmail" in seen
    assert "SomeTool" not in seen


def test_the_tier_lift_does_not_widen_any_other_filter():
    """Only the tier constraint is lifted - course, kind and dep-id still bind."""
    import inspect

    from miw.probe import runner
    src = inspect.getsource(runner.probe_all)
    assert 'if scope.tiers and (not scope.kinds or "n8n_node" in scope.kinds):' in src
    assert 'd.kind == "n8n_node"' in src


def _state_takes_path() -> bool:
    import inspect

    from miw.state import State
    return "path" in inspect.signature(State.__init__).parameters
