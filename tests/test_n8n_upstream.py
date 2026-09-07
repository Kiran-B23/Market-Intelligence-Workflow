"""n8n's own breaking-change rules as the S9 source of truth.

The previous detector grepped the newest release body for the node's name. Probed
against the Simple Memory node the course teaches, it returned `ok`. These tests pin
the replacement: node existence checked against n8n's source tree, and breaking
changes read from the rules module n8n publishes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIX = Path(__file__).parent / "fixtures"

from miw.probe.n8n_upstream import (node_types_from_tree, parse_rule,
                                    rules_for_node, rules_mentioning)


def test_node_types_derived_from_repo_paths():
    """Node existence must be a membership test against the vendor's source, not an
    inference from release prose."""
    paths = [
        "packages/nodes-base/nodes/Schedule/ScheduleTrigger.node.ts",
        "packages/@n8n/nodes-langchain/nodes/memory/MemoryBufferWindow/MemoryBufferWindow.node.ts",
        "packages/nodes-base/nodes/Code/Code.node.ts",
        "packages/cli/src/something/NotANode.ts",
        "packages/nodes-base/nodes/Schedule/ScheduleTrigger.node.json",
    ]
    got = node_types_from_tree(paths)
    assert "n8n-nodes-base.scheduleTrigger" in got
    assert "@n8n/n8n-nodes-langchain.memoryBufferWindow" in got
    assert "n8n-nodes-base.code" in got
    assert not any("NotANode" in g for g in got)


def test_parses_a_rule_that_names_node_types():
    r = parse_rule((FIX / "removed_nodes_rule.ts").read_text(), "v2/removed-nodes.rule.ts")
    assert r.rule_id == "removed-nodes-v2"
    assert r.n8n_version == "v2" and r.major == 2
    assert r.title == "Removed Deprecated Nodes"
    assert r.severity == "low"
    assert r.doc_url.startswith("https://docs.n8n.io/")
    assert "n8n-nodes-base.spontit" in r.node_types
    assert r.targets_specific_nodes
    assert "Update affected workflows" in r.actions


def test_parses_a_capability_rule_with_a_wrapped_description():
    """The description sits on the next line; a naive same-line regex misses it."""
    r = parse_rule((FIX / "pyodide_rule.ts").read_text(), "v2/pyodide-removed.rule.ts")
    assert r.rule_id == "pyodide-removed-v2"
    assert "Pyodide" in r.title
    assert "Code node has been removed" in r.description
    assert r.severity == "medium"
    assert not r.targets_specific_nodes, "this rule names a capability, not node types"


def _rules():
    return [parse_rule((FIX / f).read_text(), f).__dict__
            for f in ("removed_nodes_rule.ts", "pyodide_rule.ts")]


def test_exact_node_match_finds_the_declaring_rule():
    hits = rules_for_node(_rules(), "n8n-nodes-base.spontit")
    assert [h.rule_id for h in hits] == ["removed-nodes-v2"]
    assert rules_for_node(_rules(), "n8n-nodes-base.gmail") == []


def test_capability_rules_match_by_mention_and_never_by_node_list():
    """"Python in the Code node" is a capability change: MIW knows the course uses the
    Code node, not whether it uses the Python parameter, so this is a look-here signal
    rather than a confirmed break."""
    hits = rules_mentioning(_rules(), ["code", "python"])
    assert [h.rule_id for h in hits] == ["pyodide-removed-v2"]
    # A rule that declares node types is never returned by the mention path -
    # that would double-report it.
    assert all(not h.targets_specific_nodes for h in hits)


def test_short_terms_cannot_trigger_a_mention_match():
    assert rules_mentioning(_rules(), ["a", "de", "n8n"]) == []
