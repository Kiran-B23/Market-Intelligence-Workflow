"""n8n breaking-change rules: version-gated, interpolated, and one finding per rule.

Three defects, all measured on the 2026-09-18 run's 14 S9 findings:

  * 11 of them came from 7 rules. `wait-node-subworkflow-v2` produced 4, because n8n
    names 16 node types on it and the curriculum teaches 4 of them.
  * 2 read "${removedNodeName} node removed" - `_ts_string` lifts a TypeScript template
    literal and nothing interpolated it.
  * `agent-node-version.rule` ("AI Agent versions below 2 are removed") was raised
    against a node the curriculum teaches at typeVersion **2.2**. 2.2 is not below 2.
"""
from miw.analyse.merge import merge_n8n_breaks
from miw.probe.n8n_upstream import BreakingRule, display_name
from miw.schema import Dependency, Finding, Location


def rule(**kw):
    base = dict(rule_id="r", n8n_version="v3", title="t", description="",
                severity="medium", doc_url="https://docs.n8n.io/x")
    base.update(kw)
    return BreakingRule(**base)


# --------------------------------------------------------------- version gating

def test_a_node_above_the_stated_range_is_not_affected():
    r = rule(title="AI Agent versions below 2 are removed",
             actions=["Move AI Agent nodes on versions below 2 to the latest version"])
    assert r.version_bound == ("<", "2")
    assert r.affects_version("2.2") is False
    assert r.affects_version("1.9") is True


def test_a_minor_bound_compares_on_both_parts():
    r = rule(title="Gmail Trigger versions below 1.4 are removed")
    assert r.affects_version("1.3") is True
    assert r.affects_version("1.4") is False
    assert r.affects_version("1.10") is False


def test_and_earlier_is_inclusive():
    r = rule(title="Nodes on version 1 and earlier stop working")
    assert r.affects_version("1") is True
    assert r.affects_version("2") is False


def test_no_stated_range_is_not_a_synonym_for_not_affected():
    """Unknown must never be silently read as safe, nor as broken."""
    r = rule(title="Sub-workflow waiting node output behavior change")
    assert r.version_bound is None
    assert r.affects_version("1.1") is None
    assert r.affects_version(None) is None


def test_an_unparseable_taught_version_does_not_raise():
    r = rule(title="versions below 2 are removed")
    assert r.affects_version("latest") is None


# --------------------------------------------------------------- interpolation

def test_a_template_literal_is_filled_in_from_the_node_that_matched():
    r = rule(title="${removedNodeName} node removed",
             description="The ${removedNodeName} node is no longer supported.")
    assert r.has_placeholder
    title, desc = r.render("n8n-nodes-base.readPDF")
    assert title == "Read PDF node removed"
    assert "${" not in title + desc


def test_an_expression_with_no_binding_is_stripped_not_printed():
    r = rule(title="x", description="Do this. ${recommendations.map(d => d).join(' ')}")
    _, desc = r.render("n8n-nodes-base.code")
    assert "${" not in desc
    assert desc == "Do this."


def test_display_name_does_not_explode_capital_runs():
    assert display_name("n8n-nodes-base.readPDF") == "Read PDF"
    assert display_name("@n8n/n8n-nodes-langchain.chatTrigger") == "Chat Trigger"


# --------------------------------------------------------------- the fan-out

def _f(name, radius, rule_id="wait-node-subworkflow-v2", sev="high"):
    f = Finding(dep_id=f"d:{name}", canonical_name=name, signal="S9",
                signal_label="n8n node / version update", severity=sev)
    f.blast_radius = radius
    f.courses = ["Intro to Gen AI"]
    f.probe_signals = ["breaking_change_declared", f"n8n_rule:{rule_id}"]
    f.summary = "n8n declares a breaking change affecting this node"
    f.locations = [Location(course="Intro to Gen AI", topic_name="T", unit_id="u",
                            unit_name="S", content_id=f"c{name}", field_path="f",
                            evidence_source="n8n_workflow", object_type="SHEET")]
    return f


def test_one_rule_becomes_one_finding():
    members = [_f("n8n-nodes-base.gmail", 10), _f("n8n-nodes-base.telegram", 30),
               _f("n8n-nodes-base.wait", 5), _f("n8n-nodes-base.respondToWebhook", 8)]
    out, absorbed = merge_n8n_breaks(list(members), {})
    assert absorbed == 3
    assert len(out) == 1
    carrier = out[0]
    assert carrier.canonical_name == "n8n-nodes-base.telegram"   # widest footprint
    assert len(carrier.also_affects) == 3
    assert carrier.blast_radius == 53
    assert len(carrier.locations) == 4
    assert "4 taught nodes" in carrier.summary


def test_findings_from_different_rules_are_left_alone():
    out, absorbed = merge_n8n_breaks(
        [_f("a", 1, "rule-one"), _f("b", 1, "rule-two")], {})
    assert absorbed == 0 and len(out) == 2


def test_the_merged_severity_is_the_worst_of_the_group():
    out, _ = merge_n8n_breaks(
        [_f("a", 9, sev="low"), _f("b", 1, sev="critical")], {})
    assert out[0].severity == "critical"


def test_nothing_else_is_touched():
    other = Finding(dep_id="x", canonical_name="Composio", signal="S1",
                    signal_label="Dead / moved URL", severity="high")
    out, absorbed = merge_n8n_breaks([other, _f("a", 1), _f("b", 2)], {})
    assert absorbed == 1
    assert out[0] is other


# ------------------------------------------- naming a node is not building with it
#
# `@n8n/n8n-nodes-langchain.lmOpenAi` carried 32 locations, all recorded as
# `n8n_workflow` - the top blast-radius weight, and counted as EXECUTING the node. Every
# one of the 32 was the same "Technical Type | Display Name" reference table repeated
# across 32 quiz questions, and the node is wired into a workflow exactly zero times. It
# shipped a `high` S9. Nine of the 41 taught nodes were in that position.

from miw.extract import n8n as N
from miw.extract.inventory import RUNTIME_EVIDENCE, VISITED_EVIDENCE, WEAK_EVIDENCE
from miw.analyse.score import EVIDENCE_WEIGHT, SIGNAL_EVIDENCE, findings_for

WORKFLOW = ('{"nodes":[{"type":"@n8n/n8n-nodes-langchain.agent","typeVersion":2.2},'
            '{"type":"n8n-nodes-base.gmail","typeVersion":2.1}]}')
TABLE = ("| Technical Type | Display Name |\n"
         "| @n8n/n8n-nodes-langchain.lmOpenAi | OpenAI Model |\n"
         "| n8n-nodes-base.code | Code |\n")


def test_a_wired_node_and_a_table_row_are_told_apart():
    wired = N.nodes(WORKFLOW)
    assert {(t, v, how) for t, v, how in wired} == {
        ("@n8n/n8n-nodes-langchain.agent", "2.2", N.WIRED),
        ("n8n-nodes-base.gmail", "2.1", N.WIRED)}
    named = N.nodes(TABLE)
    assert {how for _, _, how in named} == {N.MENTIONED}
    assert {t for t, _, _ in named} == {"@n8n/n8n-nodes-langchain.lmOpenAi",
                                        "n8n-nodes-base.code"}


def test_a_node_found_both_ways_keeps_the_stronger_reading():
    """Reading material that shows a workflow AND the display-name table.

    Not valid JSON as a whole, so the typeVersion is unavailable - that is the
    documented cost of the regex pass. What must not happen is the node being demoted
    to a mention because the table also names it.
    """
    body = ("Here is the workflow:\n\n" + WORKFLOW + "\n\n" + TABLE
            + "| @n8n/n8n-nodes-langchain.agent | AI Agent |\n")
    out = N.nodes(body)
    by_type = {t: how for t, _, how in out}
    assert by_type["@n8n/n8n-nodes-langchain.agent"] == N.WIRED
    assert by_type["n8n-nodes-base.gmail"] == N.WIRED
    assert by_type["@n8n/n8n-nodes-langchain.lmOpenAi"] == N.MENTIONED
    assert by_type["n8n-nodes-base.code"] == N.MENTIONED


def test_a_table_row_is_weak_evidence_everywhere_it_is_classified():
    assert N.MENTIONED in WEAK_EVIDENCE
    assert N.MENTIONED not in RUNTIME_EVIDENCE and N.MENTIONED not in VISITED_EVIDENCE
    # and worth what a prose mention is worth, not what a wired node is worth
    assert EVIDENCE_WEIGHT[N.MENTIONED] == EVIDENCE_WEIGHT["prose_name"]
    assert EVIDENCE_WEIGHT[N.WIRED] > EVIDENCE_WEIGHT[N.MENTIONED] * 10


def test_a_mention_does_not_count_as_executing_the_node():
    dep = Dependency(kind="n8n_node", canonical_name="@n8n/n8n-nodes-langchain.lmOpenAi",
                     registry="n8n",
                     locations=[Location(course="Intro to Gen AI", topic_name="T",
                                         unit_id="u", unit_name="Coding Practice",
                                         content_id=f"q{i}", field_path="f",
                                         evidence_source=N.MENTIONED,
                                         object_type="OBJECTIVE_QUESTIONS",
                                         session_no=i) for i in range(1, 33)])
    assert dep.wired_locations == 0
    assert dep.questions_that_execute_it == []
    assert len(dep.questions_that_mention_it) == 32


def _n8n_probe(dep, rule_title):
    from miw.schema import ProbeResult
    p = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                    status="changed")
    p.signals = ["breaking_change_declared"]
    p.evidence_url = "https://docs.n8n.io/x"
    p.declared_changes = [{"rule_id": "r", "n8n_version": "v3", "title": rule_title,
                           "description": "The node is no longer supported.",
                           "severity": "medium",
                           "doc_url": "https://docs.n8n.io/x", "node_types": [dep.canonical_name]}]
    p.detail = f"n8n declares a breaking change affecting this node: {rule_title}"
    return p


def _node(name, evidence, n):
    return Dependency(
        kind="n8n_node", canonical_name=name, registry="n8n",
        official_domains=["n8n.io"],
        locations=[Location(course="Intro to Gen AI", topic_name="T", unit_id="u",
                            unit_name="Coding Practice", content_id=f"q{i}",
                            field_path="f", evidence_source=evidence,
                            object_type="OBJECTIVE_QUESTIONS", session_no=i)
                   for i in range(1, n + 1)])


def test_a_node_the_course_only_names_is_capped_at_low():
    dep = _node("@n8n/n8n-nodes-langchain.lmOpenAi", N.MENTIONED, 32)
    f = [x for x in findings_for(dep, _n8n_probe(dep, "OpenAI Model node removed"), None)
         if x.signal == "S9"][0]
    assert f.severity == "low"
    assert "never builds a workflow with it" in f.summary
    assert "correct or drop the reference-table row" in f.recommendation
    assert "re-import the workflow" not in f.recommendation


def test_a_node_the_course_builds_with_is_not_capped():
    dep = _node("n8n-nodes-base.gmail", N.WIRED, 7)
    f = [x for x in findings_for(dep, _n8n_probe(dep, "Something changed"), None)
         if x.signal == "S9"][0]
    assert f.severity in ("medium", "high", "critical")
    assert "re-import the workflow" in f.recommendation


def test_s9_still_reaches_the_table_so_a_wrong_row_is_not_left_standing():
    """Scoping it to wired evidence only would silently drop the glossary finding."""
    assert set(SIGNAL_EVIDENCE["S9"]) == {N.WIRED, N.MENTIONED}
    dep = _node("n8n-nodes-base.code", N.MENTIONED, 32)
    f = [x for x in findings_for(dep, _n8n_probe(dep, "process.env blocked"), None)
         if x.signal == "S9"][0]
    assert f.locations and f.locations_scoped is True
