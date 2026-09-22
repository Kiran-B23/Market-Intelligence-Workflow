"""Which API fields the curriculum actually writes, and whose API they belong to.

Every other extractor answers "is this tool named here". This one answers a narrower
question the system had no way to ask: **what does the course put inside the request**.
That matters because a vendor can retire a field without retiring anything the rest of
the system watches. Murf is live proof — `murf.ai` answers 200, its pricing is up, its
docs are up, and its own reference says:

    multiNativeLocale  string  Optional  Deprecated
      This field is superseded by locale field. Please migrate to locale field.

while the curriculum sends `"multiNativeLocale": "en-US"` in three sessions of Building
LLM Applications, eight of those places graded. Nothing in the inventory could see it,
because the inventory's unit is the dependency and the dependency is perfectly healthy.

Two rules decide what gets recorded, and both are refusals:

* **A key is only a key in a payload.** `"multiNativeLocale":` is a field being set;
  `p.function_call` is an attribute being read off a response. The distinction is not
  pedantic — the curriculum contains both, and reading the second as the first is the
  exact mistake that makes a field watch cry wolf.
* **The vendor's own page decides whose field it is.** Curriculum text cannot settle
  it: a single lesson record references langchain, gemini, python-dotenv, Murf,
  langgraph, Whisper and flask-cors together, so "the tool named nearest the key" is a
  guess, and dropping every ambiguous record loses the Murf case entirely — measured,
  9 of its 10 records name more than one tool. So this module proposes and the probe
  disposes: a key recorded here is a CANDIDATE, and it becomes a finding only when the
  dependency's own reference page marks that exact field deprecated. A page that
  documents a field is the proof that the field is that vendor's, and it arrives as a
  verbatim quote rather than as an inference.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from miw.schema import ContentRecord, Dependency

# A key being SET inside an object literal: quoted name, colon, value. Deliberately not
# `foo.bar` or `foo=bar` — an attribute read and a kwarg are not the request body, and
# `p.function_call` off a Gemini response is the case that proves it.
_PAYLOAD_KEY = re.compile(r"""["']([A-Za-z_][A-Za-z0-9_]{2,40})["']\s*:""")

# Keys that say nothing about a vendor's API surface: language keywords, generic JSON
# scaffolding, and the words every payload in every SDK contains. Watching these would
# attach a finding to half the curriculum the first time any vendor deprecated a
# `content` field.
GENERIC_KEYS = frozenset({
    "type", "name", "id", "key", "value", "data", "text", "content", "role", "input",
    "output", "result", "results", "items", "list", "dict", "str", "int", "bool",
    "true", "false", "null", "none", "url", "uri", "path", "file", "files", "body",
    "headers", "header", "params", "query", "method", "status", "code", "error",
    "errors", "message", "messages", "response", "request", "options", "config",
    "settings", "properties", "required", "description", "title", "format", "schema",
    "object", "array", "string", "number", "integer", "boolean", "default", "example",
    "position", "parameters", "nodes", "connections", "main", "index", "json",
})

# One sighting is enough to watch, because watching is free: the key is only ever
# reported if the vendor's own reference marks it deprecated, and a field a vendor has
# retired is worth a line in the digest however few times the course writes it. The
# Murf case would be lost at a threshold of two — of its ten records, only one names
# Murf alone, and the count that survives ambiguity is one.
MIN_OCCURRENCES = 1
MAX_KEYS_PER_DEP = 40


def payload_keys(text: str) -> set[str]:
    """Field names set inside an object literal in this text."""
    return {k for k in _PAYLOAD_KEY.findall(text or "")
            if k.lower() not in GENERIC_KEYS}


def taught_params(records: Iterable[ContentRecord],
                  deps: Iterable[Dependency]) -> dict[str, dict[str, int]]:
    """`{dep_id: {field: how many records write it}}` — candidates, not conclusions.

    A key is offered to every dependency located in the same record that could be
    checked against its own pages. That over-offers on purpose: `voiceId` reaches
    langchain as well as Murf, and only Murf's reference documents it. The probe
    resolves it, and a dependency whose page never mentions a key simply never produces
    a finding about it.
    """
    deps = list(deps)
    # content_id -> the dependencies located there that we could actually check.
    speakable: dict[str, list[Dependency]] = {}
    for d in deps:
        if not d.subject().official_domains:
            continue            # nothing to check a field against; do not record one
        for loc in d.locations:
            if loc.content_id:
                speakable.setdefault(loc.content_id, []).append(d)

    counts: dict[str, dict[str, int]] = {}
    for r in records:
        here = speakable.get(r.content_id or "")
        if not here:
            continue
        keys = payload_keys(r.body_text)
        if not keys:
            continue
        for d in here:
            bucket = counts.setdefault(d.dep_id, {})
            for k in keys:
                bucket[k] = bucket.get(k, 0) + 1
    return {dep_id: dict(sorted(b.items(), key=lambda kv: (-kv[1], kv[0]))
                         [:MAX_KEYS_PER_DEP])
            for dep_id, b in counts.items()}


# How many writing records to remember per field. The finding needs to NAME places,
# and `MAX_LOCATIONS` caps what a finding displays at 12 anyway.
MAX_SITES_PER_FIELD = 12


def attach(records: Iterable[ContentRecord], deps: Iterable[Dependency]) -> int:
    """Record each dependency's taught fields, and where each one is written.

    The `where` half is not decoration. A finding about a field has to scope to the
    records that WRITE it, and `evidence_source` cannot answer that: it records how the
    DEPENDENCY was found in a record, not what the record contains. Measured on Murf,
    the four records writing `multiNativeLocale` carry `link:a_href`, `link:markdown`
    and `prose_name` — the code block sits in a reading material that also links to
    murf.ai — so any reach rule phrased in evidence kinds scopes this finding to
    nothing at all.
    """
    records = list(records)
    deps = list(deps)
    found = taught_params(records, deps)
    at: dict[str, set[str]] = {}
    for d in deps:
        at[d.dep_id] = {l.content_id for l in d.locations if l.content_id}

    sites: dict[str, dict[str, list[str]]] = {}
    for r in records:
        keys = payload_keys(r.body_text)
        if not keys or not r.content_id:
            continue
        for dep_id, mine in at.items():
            if r.content_id not in mine:
                continue
            bucket = sites.setdefault(dep_id, {})
            for k in keys & set(found.get(dep_id) or {}):
                ids = bucket.setdefault(k, [])
                if r.content_id not in ids and len(ids) < MAX_SITES_PER_FIELD:
                    ids.append(r.content_id)

    n = 0
    for d in deps:
        keys = [k for k, c in (found.get(d.dep_id) or {}).items()
                if c >= MIN_OCCURRENCES]
        d.taught_params = keys
        d.taught_param_at = {k: v for k, v in (sites.get(d.dep_id) or {}).items()
                             if k in set(keys)}
        n += len(keys)
    return n


def where_written(records: Iterable[ContentRecord], dep: Dependency,
                  field: str) -> list[ContentRecord]:
    """The records that write `field`, for a finding that has to name places."""
    at = {loc.content_id for loc in dep.locations if loc.content_id}
    return [r for r in records
            if r.content_id in at and field in payload_keys(r.body_text)]
