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


def _site(r: ContentRecord) -> dict:
    """Enough of a record to become a `Location` later, and nothing more."""
    return {"content_id": r.content_id, "course": r.course,
            "topic_name": r.topic_name, "unit_id": r.unit_id,
            "unit_name": r.unit_name, "field_path": r.field_path,
            "object_type": r.object_type, "session_no": r.session_no}


def attach(records: Iterable[ContentRecord],
           deps: Iterable[Dependency]) -> tuple[int, dict]:
    """Record each dependency's taught fields, and return where each field is written.

    The `where` half is not decoration, and it is deliberately NOT filtered by where the
    dependency was found. Two different questions were once answered with one lookup:

    * *Whose field is this?* — settled by the vendor's own reference page, at probe time.
    * *Where must we edit?* — **every record that writes the field**, named or not.

    Filtering sites by the dependency's own locations answered the second with the first
    and under-reported badly. Measured on Murf: `multiNativeLocale` is written in ten
    records across sessions 18, 19 and 20, eight of them graded — and the finding could
    see three, because session 20's module quiz writes the key seven times without ever
    saying the word "Murf" or linking `murf.ai`.

    That is structural rather than a Murf quirk. Every evidence kind the inventory can
    emit is a NAME or a LINK, so a dependency used without being named produces no
    location at all. Payload keys are simply the first place the system looks inside the
    request and the gap becomes visible.

    Uncapped on purpose: a display cap belongs in the reporter (`MAX_LOCATIONS`), not
    here, or `affects_total` silently understates the work.

    Returns `(fields attached, {field: [site, ...]})`. The site map is shared rather
    than copied onto every dependency - see the note at the return.
    """
    records = list(records)
    deps = list(deps)
    found = taught_params(records, deps)

    # field -> every record in the curriculum that writes it, whoever is named there.
    by_field: dict[str, list[dict]] = {}
    for r in records:
        if not r.content_id:
            continue
        for k in payload_keys(r.body_text):
            rows = by_field.setdefault(k, [])
            if not any(x["content_id"] == r.content_id for x in rows):
                rows.append(_site(r))

    n = 0
    for d in deps:
        keys = [k for k, c in (found.get(d.dep_id) or {}).items()
                if c >= MIN_OCCURRENCES]
        d.taught_params = keys
        n += len(keys)
    # The site map is returned, not hung on each dependency. A record writing
    # `promptType` is the same record for every dependency that lists it as a
    # candidate, and storing it per dependency took the inventory from 6 MB to 65 MB
    # for no new information. It rides in the artifact beside `unit_images`, which is
    # the same shape of shared census.
    return n, {k: v for k, v in by_field.items()
               if any(k in (found.get(d.dep_id) or {}) for d in deps)}


def where_written(records: Iterable[ContentRecord], dep: Dependency,
                  field: str) -> list[ContentRecord]:
    """The records that write `field`, for a finding that has to name places."""
    at = {loc.content_id for loc in dep.locations if loc.content_id}
    return [r for r in records
            if r.content_id in at and field in payload_keys(r.body_text)]
