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


# One coherent observation per signal. A finding carries the probe signals that
# produced it, and since `evidence_reach` keys on those rather than on the signal
# letter, a fixture that pairs "S3 pricing" with "a URL 404'd" is not a small
# inaccuracy - it is the exact confusion the rule exists to catch.
OBSERVED = {"S1": "url_gone", "S2": "access_wall_language",
            "S3": "free_tier_language_lost", "S4": "sunset_language_about_subject",
            "S5": "redirected_off_path", "S6": "major_behind_taught_pin",
            "S7": "model_shutdown_passed", "S8": "page_text_changed",
            "S9": "breaking_change_declared"}


def finding(signal="S1", urls=(), observed=None):
    f = Finding(dep_id="d", canonical_name="Composio", signal=signal,
                signal_label="Dead / moved URL", severity="high")
    f.probe_signals = [observed or OBSERVED.get(signal, "url_gone")]
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
    """Earned, not assumed: `free_tier_language_lost` and
    `sunset_language_about_subject` are the vendor changing what the course should
    TEACH, so they reach everywhere it is taught."""
    for signal in ("S3", "S4"):
        dep, f = composio(), finding(signal=signal)
        scope_locations(dep, f)
        assert len(f.locations) == len(dep.locations), signal


def test_the_same_signal_reaches_differently_when_the_observation_differs():
    """S4 is a bucket. `sunset_language_about_subject` is the vendor saying it is
    winding down and reaches everywhere; `registry_missing` is a package that left
    PyPI and reaches where the package is used. Declaring the reach on the bucket made
    the second one claim the first one's blast radius."""
    dep = composio()
    everywhere = finding(signal="S4", observed="sunset_language_about_subject")
    scope_locations(dep, everywhere)
    assert len(everywhere.locations) == len(dep.locations)

    package = finding(signal="S4", observed="registry_missing")
    scope_locations(dep, package)
    assert len(package.locations) < len(dep.locations)
    assert all(l.evidence_source != "prose_name" for l in package.locations)


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
    f = finding(signal="S6", observed="major_behind_taught_pin")
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


def test_the_probe_is_not_rationed_by_a_research_budget(monkeypatch):
    """`watch_tier` rations RESEARCH - searches, model calls, the many fetches
    `official.gather` makes. A probe is one request per referenced URL.

    Measured the moment the mute dependencies were given vendors: 48 had an
    authoritative domain and were never probed because nothing executes them, Hugging
    Face at blast radius 56 among them. A dependency with nothing to probe against is
    still skipped - that is what `_has_something_to_probe` is for.
    """
    from miw.probe.runner import probe_all
    from miw.scope import Scope
    from miw.state import State

    mention_node = Dependency(kind="n8n_node", canonical_name="n8n-nodes-base.code",
                              registry="n8n", watch_tier="mention-only",
                              locations=[loc("n8n_mention", "OBJECTIVE_QUESTIONS")])
    # Spoken for, but nothing executes it: probed anyway, because there is an authority
    # to check it against. This is Telegram, Hugging Face and Google Colab.
    mention_tool = Dependency(kind="tool", canonical_name="SomeTool",
                              homepage="https://sometool.example",
                              official_domains=["sometool.example"],
                              watch_tier="mention-only",
                              locations=[loc("prose_name", "OBJECTIVE_QUESTIONS")])
    # Nothing to probe against at all: still skipped, because a slot spent here only
    # records "no URL known", which the inventory already says.
    voiceless = Dependency(kind="tool", canonical_name="QLoRA",
                           watch_tier="mention-only",
                           locations=[loc("prose_name", "OBJECTIVE_QUESTIONS")])
    wired = Dependency(kind="n8n_node", canonical_name="n8n-nodes-base.gmail",
                       registry="n8n", watch_tier="critical",
                       locations=[loc("n8n_workflow", "OBJECTIVE_QUESTIONS")])

    # n8n's node index and npm's release list are stubbed, not fetched. This test is
    # about which dependencies the TIER lets through, and nothing else; reaching out
    # for the real answers made it depend on a seven-day disk cache, and the week the
    # cache expired it stopped failing and started hanging instead.
    # `fetch` consults `url_safety` before it opens anything, so one stub makes the
    # whole probe offline: a host that does not resolve is returned as unreachable
    # without a request. These two tests are about which dependencies the SCOPE lets
    # through, and a real DNS lookup on a `.example` host answers nothing about that.
    import miw.net as net
    monkeypatch.setattr(net, "url_safety", lambda url: "unresolvable")

    import miw.probe.n8n_upstream as up
    import miw.probe.registries as reg
    monkeypatch.setattr(up, "upstream", lambda *a, **k: {
        "ok": True, "cached": True, "rules": [],
        "nodes": ["n8n-nodes-base.code", "n8n-nodes-base.gmail"]})
    monkeypatch.setattr(reg, "n8n_latest", lambda *a, **k: {
        "latest_version": "1.100.0", "released_at": "2026-09-01", "found": True,
        "reachable": True})

    seen = []
    state = State(":memory:") if _state_takes_path() else State()
    try:
        probe_all([mention_node, mention_tool, wired, voiceless], state,
                  scope=Scope(tiers={"critical", "standard"}),
                  progress=lambda i, n, r: seen.append(r.canonical_name))
    finally:
        state.close()
    assert "n8n-nodes-base.code" in seen      # mention-only node, probe is free
    assert "n8n-nodes-base.gmail" in seen
    assert "SomeTool" in seen                 # mention-only, but it has an authority
    assert "QLoRA" not in seen                # nothing to probe it against


def test_the_tier_lift_does_not_widen_any_other_filter(monkeypatch):
    """Only the tier constraint is lifted - course, kind, dep-id and limit still bind.

    Asserted by RUNNING the selection, not by reading the source of it. The previous
    version of this test matched on the text of the `dataclasses.replace` call, which
    meant it passed while `limit=None` in that very call threw the limit away: an
    operator asking for a bounded spot-check got the whole inventory - ~464
    dependencies, politeness-throttled - and the test agreed that nothing was widened.
    """
    from miw.probe.runner import probe_all
    from miw.scope import Scope
    from miw.state import State

    deps = [Dependency(kind="tool", canonical_name=f"Tool{i}",
                       homepage=f"https://tool{i}.example",
                       official_domains=[f"tool{i}.example"],
                       watch_tier="mention-only",
                       locations=[loc("prose_name", "OBJECTIVE_QUESTIONS")])
            for i in range(6)]
    # Critical, so it is picked by the tiered pass; the rest can only arrive through
    # the widening, which is the path that used to ignore the limit.
    deps[0].watch_tier = "critical"

    # `fetch` consults `url_safety` before it opens anything, so one stub makes the
    # whole probe offline: a host that does not resolve is returned as unreachable
    # without a request. These two tests are about which dependencies the SCOPE lets
    # through, and a real DNS lookup on a `.example` host answers nothing about that.
    import miw.net as net
    monkeypatch.setattr(net, "url_safety", lambda url: "unresolvable")

    seen = []
    state = State(":memory:") if _state_takes_path() else State()
    try:
        probe_all(deps, state, scope=Scope(tiers={"critical", "standard"}, limit=2),
                  progress=lambda i, n, r: seen.append(r.canonical_name))
    finally:
        state.close()
    assert len(seen) == 2, seen
    assert "Tool0" in seen                   # the tiered pass still comes first


def _state_takes_path() -> bool:
    import inspect

    from miw.state import State
    return "path" in inspect.signature(State.__init__).parameters


# ------------------------------------------- the list row is not the detail panel

def test_the_list_row_drops_the_clause_about_where_it_lands():
    """A row is read to decide what to open next; the panel says where, and better."""
    from miw.analyse.score import action_only
    dep, f = composio(), finding(urls=[DEAD])
    scope_locations(dep, f)
    f.recommendation = recommend(dep, f)

    assert f.affects_line and f.affects_line in f.recommendation
    row = action_only({"recommendation": f.recommendation,
                       "affects_line": f.affects_line})
    assert f.affects_line not in row
    assert "quiz question" not in row
    # what remains is still the instruction, not a fragment
    assert row.startswith("Repoint or replace the dead link")
    assert "  " not in row                     # no seam where the clause was


def test_a_model_refined_action_is_returned_untouched():
    from miw.analyse.score import action_only
    refined = "Swap the dashboard link for the new console URL in session 25."
    assert action_only({"what_to_act": refined,
                        "recommendation": "deterministic text",
                        "affects_line": "Affects 2 quiz questions."}) == refined


def test_a_finding_whose_recommendation_was_not_built_by_recommend_is_left_alone():
    """S11 and S12 write their own line, and for a topic gap the session IS the point."""
    from miw.analyse.score import action_only
    gap = "Add “Parallel function calling” to AI for Finance session 9."
    assert action_only({"recommendation": gap, "affects_line": ""}) == gap


def test_the_counts_are_taken_before_the_display_cap():
    """`locations` stops at 12. Counting it made a 32-place finding say 12."""
    dep = Dependency(
        kind="n8n_node", canonical_name="n8n-nodes-base.code", registry="n8n",
        locations=[loc("n8n_mention", "OBJECTIVE_QUESTIONS", session=i)
                   for i in range(1, 33)])
    f = finding(signal="S9")
    scope_locations(dep, f)
    assert len(f.locations) == 12                    # the display cap still applies
    assert f.affects_total == 32
    assert f.affects_counts == {"OBJECTIVE_QUESTIONS": 32}
    assert "32 quiz questions" in where_line(dep, f)


def test_every_surface_reads_the_same_recorded_count():
    """A row, the digest and the panel must not disagree about one finding."""
    from miw.reporters.markdown import _loc_line
    dep = Dependency(
        kind="tool", canonical_name="Murf.AI",
        locations=[loc("prose_name", "OBJECTIVE_QUESTIONS", session=1) for _ in range(40)]
                  + [loc("prose_name", "LEARNING_RESOURCE", session=2) for _ in range(5)])
    f = finding(signal="S4")
    scope_locations(dep, f)
    assert f.affects_counts == {"OBJECTIVE_QUESTIONS": 40, "LEARNING_RESOURCE": 5}
    assert "40 quiz questions" in _loc_line(f)        # digest
    assert "40 quiz questions" in where_line(dep, f)  # the panel's action sentence


def test_a_projection_recounts_for_its_own_course():
    from miw.analyse.project import project_finding
    other = "Building LLM Applications"
    dep = Dependency(
        kind="tool", canonical_name="Murf.AI",
        locations=[loc("prose_name", "OBJECTIVE_QUESTIONS", session=1) for _ in range(3)]
                  + [Location(course=other, topic_name="T", unit_id="u", unit_name="U",
                              content_id="c", field_path="f",
                              evidence_source="prose_name",
                              object_type="LEARNING_RESOURCE", session_no=4)])
    f = finding(signal="S4")
    f.courses = [COURSE, other]
    scope_locations(dep, f)
    assert f.affects_total == 4
    local = project_finding(f, dep, COURSE)
    assert local.affects_total == 3
    assert local.affects_counts == {"OBJECTIVE_QUESTIONS": 3}


def test_scoring_is_not_rationed_by_the_research_budget_either():
    """`probe` widened past the tier filter; `analyse` did not, and that gap was the
    whole of the reference-only requirement.

    The tier rations RESEARCH — searches, model calls, the many fetches
    `official.gather` makes. Scoring costs nothing. The UI (`app.py` `RunIn.tiers`) and
    `run-weekly` both send `--tiers critical,standard`, so on every run the team
    actually starts, the 209 `mention-only` dependencies were probed and then dropped
    before they could become findings — 48% of the inventory observed and discarded,
    and precisely the half the curriculum names so students are aware a tool exists,
    where the only question is whether it still works.

    Course, session, kind and dep-id must still bind: scoring one course must never
    quietly score another.
    """
    import main as cli
    from miw.scope import Scope

    # Through the real transform, not a local copy of it: a gate that reimplements the
    # thing it guards passes while the call site rots.
    sc = Scope(courses={"Intro to Gen AI"}, tiers={"critical", "standard"},
               kinds={"tool"}, sessions={4})
    scored = cli._scoring_scope(sc)
    assert not scored.tiers, "the tier must be lifted for scoring"
    assert scored.courses == {"Intro to Gen AI"}, "the course must still bind"
    assert scored.sessions == {4}, "the session must still bind"
    assert scored.kinds == {"tool"}, "the kind must still bind"

    def loc(course):
        return Location(course=course, topic_name="t", unit_id="u", unit_name="un",
                        content_id="c", field_path="f", evidence_source="prose_name",
                        object_type="LEARNING_RESOURCE", session_no=4)

    mention = Dependency(kind="tool", canonical_name="Julius AI",
                         watch_tier="mention-only",
                         locations=[loc("Intro to Gen AI")])
    other = Dependency(kind="tool", canonical_name="Elsewhere",
                       watch_tier="mention-only", locations=[loc("AI for Finance")])
    assert mention in scored.select([mention, other])   # reference-only, now scored
    assert other not in scored.select([mention, other])  # another course, still out
    assert mention not in sc.select([mention, other])    # and the old rule dropped it


def test_a_field_finding_reaches_every_record_that_writes_it():
    """The scope of a field finding is the content, not the places the tool was named.

    Measured on the live Murf case before this was fixed: `multiNativeLocale` is written
    in ten records across sessions 18, 19 and 20, eight of them graded — and the finding
    named three, because session 20's module quiz writes the key seven times without
    ever saying the word "Murf" or linking `murf.ai`, so Murf had no `Location` there.
    A reviewer was told "1 quiz question"; the work was eight.

    Structural rather than a Murf quirk: every evidence kind the inventory emits is a
    NAME or a LINK, so a dependency used without being named produces no location at
    all. Payload keys are the first place the system looks inside the request.
    """
    from miw.analyse import score
    from miw.schema import Finding

    dep = Dependency(kind="service", canonical_name="Acme",
                     homepage="https://acme.test", official_domains=["acme.test"],
                     watch_tier="critical",
                     # Named in ONE record. The other four write the field and never
                     # name the vendor — the shape that was invisible.
                     locations=[Location(
                         course="C1", topic_name="t", unit_id="u1", unit_name="U1",
                         content_id="r1", field_path="body",
                         evidence_source="link:a_href",
                         object_type="LEARNING_RESOURCE", session_no=18)])
    sites = [{"content_id": f"r{i}", "course": "C1", "topic_name": "t",
              "unit_id": f"u{i}", "unit_name": f"U{i}", "field_path": "body",
              "object_type": "OBJECTIVE_QUESTIONS" if i > 1 else "LEARNING_RESOURCE",
              "session_no": 18 + (i % 3)} for i in range(1, 6)]
    score.set_param_sites({"legacyLocale": sites})
    try:
        f = Finding(dep_id=dep.dep_id, canonical_name="Acme", signal="S13",
                    signal_label="Taught API field deprecated",
                    kind_of_signal="regression", severity="high",
                    probe_signals=["taught_field_deprecated"],
                    deprecated_fields=[{"field": "legacyLocale", "successor": "locale",
                                        "quote": "legacyLocale string Optional "
                                                 "deprecated use locale instead",
                                        "evidence_url": "https://acme.test/ref"}])
        score.scope_locations(dep, f)
        score._recount_from_locations(dep, f)
    finally:
        score.set_param_sites({})

    assert f.affects_total == 5, "every writing record, not only the one that names it"
    assert {l.content_id for l in f.locations} == {f"r{i}" for i in range(1, 6)}
    assert all(l.evidence_source == score.PAYLOAD_KEY for l in f.locations)
    # The counts follow the finding: four graded records write the field, and a payload
    # key is runtime evidence, so they EXECUTE it rather than mention it.
    assert f.graded_locations == 4
    assert f.questions_executing == 4
    assert f.courses == ["C1"]
    assert sorted({l.session_no for l in f.locations}) == [18, 19, 20]


def test_the_field_check_runs_for_every_kind_not_only_url_backed_ones(monkeypatch):
    """It lived inside the `else` arm of `probe_dependency`'s switch on kind.

    Measured on the live inventory: of 170 dependencies carrying candidate fields, 70
    could reach the check and 100 could not — every package, every model, every n8n
    node. The 100 were the most-taught things in the curriculum: `langchain` at 215
    locations, `gemini-2.5-flash` at 213, `google-genai` at 108. An SDK's request fields
    and a model's request fields are exactly this class of problem.
    """
    import miw.probe.registries as reg
    import miw.probe.runner as runner
    from miw.probe.http_probe import UrlObservation
    from miw.state import State

    def fake_observe(url, terms=()):
        o = UrlObservation(url=url)
        o.reachable = True
        o.deprecated_fields = [{"field": "legacyLocale", "successor": "locale",
                                "quote": "legacyLocale string Optional deprecated "
                                         "use locale instead"}]
        return o

    monkeypatch.setattr(runner, "observe", fake_observe)
    monkeypatch.setattr(runner, "_reference_pages", lambda *a, **k: [])
    # The pricing probe fetches on its own account and is not what this test is about.
    monkeypatch.setattr(runner, "_probe_pricing", lambda *a, **k: None)
    monkeypatch.setattr(reg, "pypi", lambda *a, **k: {
        "found": True, "reachable": True, "latest_version": "2.0.0",
        "released_at": "2026-09-01", "evidence_url": "https://pypi.org/project/x/"})

    st = State(":memory:")
    try:
        for kind, extra in (("package", {"registry": "pypi", "registry_id": "x"}),
                            ("service", {})):
            dep = Dependency(kind=kind, canonical_name="Acme",
                             homepage="https://acme.test",
                             docs_url="https://acme.test/docs",
                             official_domains=["acme.test"], watch_tier="critical",
                             taught_params=["legacyLocale"], **extra)
            res = runner.probe_dependency(dep, st)
            assert "taught_field_deprecated" in res.signals, (
                f"a {kind} never reached the field check")
            assert res.deprecated_fields[0]["successor"] == "locale"

        # ...and a tool named only so students are aware it exists is NOT deep-checked.
        aware = Dependency(kind="service", canonical_name="Acme",
                           homepage="https://acme.test",
                           official_domains=["acme.test"], watch_tier="mention-only",
                           taught_params=["legacyLocale"])
        assert "taught_field_deprecated" not in runner.probe_dependency(aware, st).signals
    finally:
        st.close()
