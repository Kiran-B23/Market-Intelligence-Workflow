"""n8n workflow extraction.

`solutions[].solution_answer` on an `INTERACTIVE_BUILDER_TEXTUAL` question is the
entire n8n workflow as a JSON string. Each node carries a `type` and a `typeVersion`,
which is what turns "n8n changed" from a guess into a checkable claim: we know the
exact node and the exact version the course was authored against.
"""
from __future__ import annotations

import json
import re
from typing import Iterator

NODE_TYPE = re.compile(r"\"type\"\s*:\s*\"((?:@[\w./-]+/)?n8n-nodes[\w.-]*\.[\w.]+)\"")
TYPE_VERSION = re.compile(r"\"typeVersion\"\s*:\s*([\d.]+)")

# Node types named in prose or a markdown reference table rather than a workflow -
# e.g. "| n8n-nodes-base.code | Code |". The Code node was taught 34 times in exactly
# this form and was invisible to the JSON-shaped pattern above, which meant n8n's own
# breaking change about Python in the Code node could never have matched anything.
BARE_NODE = re.compile(r"(?<![\w.\-/\"])((?:@[\w.\-]+/)?n8n-nodes[\w.\-]*\.[a-zA-Z][\w.]*)")

# Placeholders from documentation examples. `someNode` alone accounted for 32
# locations and is not a node anyone can install.
PLACEHOLDERS = {"somenode", "yournode", "mynode", "examplenode", "nodename",
                "nodetype", "thenode", "anynode", "customnode", "test", "example"}


def _is_placeholder(node_type: str) -> bool:
    return node_type.rsplit(".", 1)[-1].lower() in PLACEHOLDERS


def _walk_nodes(obj) -> Iterator[dict]:
    if isinstance(obj, dict):
        if "type" in obj and isinstance(obj.get("type"), str) and "n8n-nodes" in obj["type"]:
            yield obj
        for v in obj.values():
            yield from _walk_nodes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_nodes(v)


def nodes(payload: str) -> list[tuple[str, str]]:
    """(node_type, type_version) pairs found in `payload`.

    Three passes, strongest first: a real JSON parse (so `typeVersion` binds to the
    right node), the JSON-shaped `"type": "..."` regex for truncated or embedded
    blobs, and finally bare mentions in prose or a markdown table. Versions come only
    from the JSON parse - a mention carries none, and inventing one would be worse
    than leaving it blank.
    """
    if not payload or "n8n-nodes" not in payload:
        return []
    out: list[tuple[str, str]] = []
    try:
        data = json.loads(payload) if payload.lstrip()[:1] in "{[" else None
    except (json.JSONDecodeError, ValueError):
        data = None

    if data is not None:
        for node in _walk_nodes(data):
            tv = node.get("typeVersion")
            out.append((node["type"], str(tv) if tv is not None else ""))
    else:
        for t in NODE_TYPE.findall(payload):
            out.append((t, ""))

    known = {t for t, _ in out}
    for t in BARE_NODE.findall(payload):
        if t not in known:
            out.append((t, ""))

    seen, uniq = set(), []
    for t, v in out:
        if _is_placeholder(t) or (t, v) in seen:
            continue
        seen.add((t, v))
        uniq.append((t, v))
    return uniq


def package_of(node_type: str) -> str:
    """npm package that ships a node type. `@n8n/n8n-nodes-langchain.agent` -> that pkg."""
    head = node_type.rsplit(".", 1)[0]
    return head if head.startswith("@") or head.startswith("n8n-nodes") else ""
