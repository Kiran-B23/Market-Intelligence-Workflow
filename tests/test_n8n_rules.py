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
