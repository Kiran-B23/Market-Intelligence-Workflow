"""Authoritative n8n checks: does the taught node still exist, and has n8n itself
declared a breaking change that touches it?

The previous S9 detector grepped only the newest GitHub release body for the node's
name. That could not work: a change from a year ago is hundreds of releases back, and
it never asked the one question that matters - is this node still there at all. Probed
against `@n8n/n8n-nodes-langchain.memoryBufferWindow`, the node the course teaches at
typeVersion 1.3, it returned `ok`.

n8n publishes two things that answer this properly:

* **The repository tree**, from which every shipped node type can be derived - so node
  removal is a membership test against the vendor's own source, not an inference.
* **A breaking-changes rules module** (`packages/cli/src/modules/breaking-changes/`),
  where each rule carries the affected node types, a severity, a title, and a
  `documentationUrl` on docs.n8n.io. That is n8n stating its own breaking changes in
  machine-readable form - including `pyodide-removed` (Python in the Code node) and
  `removed-nodes`. It is a far better source than release-note prose, and because the
  doc URLs are on an official n8n domain they pass the trust gate as AUTHORITATIVE.

Both are cached to disk with a TTL: the first run costs ~40 fetches, later runs cost
nothing, which is what makes this affordable in a weekly job.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from miw.net import fetch

CACHE = Path("state/n8n_upstream.json")
CACHE_TTL_S = 7 * 24 * 3600

TREE_URL = "https://api.github.com/repos/n8n-io/n8n/git/trees/master?recursive=1"
RAW = "https://raw.githubusercontent.com/n8n-io/n8n/master/"
RULES_DIR = "packages/cli/src/modules/breaking-changes/rules/"

# Path prefix -> node-type package prefix.
PACKAGE_PREFIXES = {
    "packages/nodes-base/nodes/": "n8n-nodes-base",
    "packages/@n8n/nodes-langchain/nodes/": "@n8n/n8n-nodes-langchain",
}

_NODE_FILE = re.compile(r"/([A-Za-z][\w]*)\.node\.ts$")


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def node_paths_from_tree(paths: list[str]) -> dict:
    """`{node type: the repo path it was derived from}`.

    The path is kept, not just the type, because it is the only quotable evidence that
    n8n ships a node. The type is *our* derivation from a filename; the path is a line
    n8n's own repository tree contains, which is what `Claim.build` can be handed for an
    EXISTENCE claim about a node we do not teach yet.
    """
    out: dict = {}
    for p in paths:
        m = _NODE_FILE.search(p)
        if not m:
            continue
        for prefix, pkg in PACKAGE_PREFIXES.items():
            if p.startswith(prefix):
                out.setdefault(f"{pkg}.{_lower_first(m.group(1))}", p)
                break
    return out


def node_types_from_tree(paths: list[str]) -> set[str]:
    """Derive every shipped node type from the repo's file paths.

    `packages/nodes-base/nodes/Schedule/ScheduleTrigger.node.ts`
        -> `n8n-nodes-base.scheduleTrigger`
    """
    return set(node_paths_from_tree(paths))


# --- rule parsing -----------------------------------------------------------

def _ts_string(body: str, key: str) -> str:
    """Value of `key: '...'` in a TypeScript object, tolerating a line break and
    adjacent-literal continuation."""
    m = re.search(rf"\b{key}\s*:\s*\n?\s*((?:'[^']*'|\"[^\"]*\"|`[^`]*`)(?:\s*\+?\s*(?:'[^']*'|\"[^\"]*\"|`[^`]*`))*)",
                  body)
    if not m:
        return ""
    parts = re.findall(r"'([^']*)'|\"([^\"]*)\"|`([^`]*)`", m.group(1))
    return " ".join(a or b or c for a, b, c in parts).strip()


_RULE_VERSION = re.compile(r"@BreakingChangeRule\(\{\s*version:\s*'([^']+)'")
# A class field is written `id: string = 'removed-nodes-v2'` - a typed assignment, not
# an object entry, so the object-literal reader above cannot see it.
_TS_FIELD = re.compile(r"\b(\w+)\s*:\s*\w+\s*=\s*'([^']+)'")


def _ts_field(body: str, key: str) -> str:
    for name, value in _TS_FIELD.findall(body):
        if name == key:
            return value
    return ""
_NODE_LITERAL = re.compile(r"'((?:@[\w.\-]+/)?n8n-nodes[\w.\-]*\.[\w.]+)'")


@dataclass
class BreakingRule:
    rule_id: str
    n8n_version: str            # 'v2', 'v3', ...
    title: str
    description: str
    severity: str
    doc_url: str
    node_types: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    source_path: str = ""

    @property
    def major(self) -> Optional[int]:
        m = re.search(r"(\d+)", self.n8n_version or "")
        return int(m.group(1)) if m else None

    @property
    def targets_specific_nodes(self) -> bool:
        return bool(self.node_types)


def parse_rule(body: str, path: str) -> Optional[BreakingRule]:
    ver = _RULE_VERSION.search(body)
    title = _ts_string(body, "title")
    if not (ver or title):
        return None
    return BreakingRule(
        rule_id=_ts_field(body, "id") or _ts_string(body, "id") or Path(path).stem,
        n8n_version=ver.group(1) if ver else "",
        title=title,
        description=_ts_string(body, "description"),
        severity=(_ts_string(body, "severity") or "medium").lower(),
        doc_url=_ts_string(body, "documentationUrl"),
        node_types=sorted(set(_NODE_LITERAL.findall(body))),
        actions=[a for a in re.findall(r"\baction:\s*'([^']+)'", body)],
        source_path=path,
    )


# --- fetch + cache ----------------------------------------------------------

def _load_cache() -> dict:
    if not CACHE.exists():
        return {}
    try:
        d = json.loads(CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return d if time.time() - d.get("fetched_at", 0) < CACHE_TTL_S else {}


def _save_cache(d: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    d["fetched_at"] = time.time()
    try:
        CACHE.write_text(json.dumps(d))
    except OSError:
        pass


def upstream(refresh: bool = False, max_rules: int = 60) -> dict:
    """Node index + breaking-change rules, cached for a week.

    Returns `{"nodes": [...], "rules": [...], "ok": bool, "error": str}`. On any
    fetch failure the caller gets `ok: False` and must treat the result as unknown -
    an unreachable GitHub must never read as "the node was removed".
    """
    if not refresh:
        cached = _load_cache()
        if cached.get("nodes"):
            return {**cached, "ok": True, "cached": True}

    f = fetch(TREE_URL, timeout=45)
    if not f.ok:
        return {"ok": False, "error": f"tree: http {f.status or f.error}",
                "nodes": [], "rules": []}
    try:
        tree = json.loads(f.body)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": "tree: unparseable", "nodes": [], "rules": []}

    paths = [e.get("path", "") for e in tree.get("tree", [])]
    node_paths = node_paths_from_tree(paths)
    nodes = sorted(node_paths)

    rule_paths = [p for p in paths
                  if p.startswith(RULES_DIR) and p.endswith(".ts")
                  and "__tests__" not in p and not p.endswith("index.ts")]
    rules, errors = [], []
    for p in sorted(rule_paths)[:max_rules]:
        rf = fetch(RAW + p, timeout=30)
        if not rf.ok:
            errors.append(f"{p}: http {rf.status or rf.error}")
            continue
        r = parse_rule(rf.body, p)
        if r:
            rules.append(r.__dict__)

    out = {"nodes": nodes, "node_paths": node_paths, "rules": rules,
           "truncated": bool(tree.get("truncated")), "errors": errors}
    _save_cache(out)
    return {**out, "ok": True, "cached": False}


def rules_for_node(rules: list[dict], node_type: str) -> list[BreakingRule]:
    """Rules that name this exact node type."""
    return [BreakingRule(**r) for r in rules if node_type in (r.get("node_types") or [])]


def rules_mentioning(rules: list[dict], terms: list[str]) -> list[BreakingRule]:
    """Rules whose title or description mentions one of these terms but which declare
    no node list - capability changes such as Python in the Code node.

    Reported as needing a human look, never as a confirmed break: MIW knows the course
    uses the Code node, not whether it uses the Python parameter the rule is about.
    """
    out = []
    for r in rules:
        if r.get("node_types"):
            continue
        blob = f"{r.get('title', '')} {r.get('description', '')}".lower()
        if any(t.lower() in blob for t in terms if len(t) > 3):
            out.append(BreakingRule(**r))
    return out
