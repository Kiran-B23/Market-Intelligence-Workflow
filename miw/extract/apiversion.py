"""Which API version the curriculum CALLS, and whose API it is.

`params.py` asks what the course puts inside the request. This asks the question one
level out: **which version of the API does the request address at all?** A vendor can
retire a whole API version without retiring anything else the system watches — the tool
is live, the pricing page is up, the fields are current, and every code sample in the
course points at an endpoint that is scheduled to stop answering.

Two refusals decide what counts, and both were measured rather than assumed:

* **A version is only pinned where the course CALLS the URL.** The curriculum writes
  `https://developers.google.com/youtube/v3/docs/search/list` — a link to documentation
  that happens to carry `v3` in its path — beside
  `https://www.googleapis.com/youtube/v3/search`, which is the endpoint the solution
  actually posts to. Reading the first as a taught API version would report the course
  for linking to a manual. The test is whether the URL addresses an API surface: an
  `api`-ish host, or an `/api/` segment in the path, and never a documentation path.
* **A version is a bounded path segment.** `/v1/` is a version; the `v1` inside `v10`,
  `rev1` or `Nov1` is not. Substring matching here is how a version check turns into a
  generator of nonsense, and `research/news.py` already learned the same lesson about
  matching a tool's name inside ordinary words.

**Binding is by domain, and that is what makes this stricter than the field check.**
A payload key has to be offered to every dependency in the record and settled later by
the vendor's own page, because a lesson names a dozen tools at once. A URL does not need
that: `api.murf.ai` falls under Murf's authority set, so the API being addressed is a
matter of record rather than inference. A host no tracked dependency can speak for —
`www.googleapis.com` and `api.freepik.com` on today's inventory — produces nothing,
which is the same refusal `params.taught_params` makes when a dependency has no
official domain to check against.

Measured on the live curriculum: 46 sightings, 11 host/version pairs, 9 of them owned by
a tracked dependency. `global.api.murf.ai/v1` (14), `backend.composio.dev/v3` (9),
`api.stability.ai/v2beta` (4), `api.groq.com/v1` (4).
"""
from __future__ import annotations

import re
from typing import Iterable

from miw.schema import ContentRecord, Dependency

_URL = re.compile(r"https?://[A-Za-z0-9.\-]+/[^\s\"'<>)\]]*")

# A version segment: `v1`, `v2beta`, `v1alpha2`, or a dated version like `2024-02-01`,
# which is how Azure and a few others spell the same thing.
_VERSION_SEG = re.compile(r"/(v\d+(?:[a-z]+\d*)?|\d{4}-\d{2}-\d{2})(?=/|$)", re.I)

# An `api` label in the host. The boundary allows a hyphen as well as a dot because
# `open.er-api.com` is a real endpoint the course calls, and requiring a dot dropped it
# while keeping every other host. It does not match `therapist.com`, where `api` is not
# followed by a boundary.
_API_HOST = re.compile(r"(^|[.\-])api([.\-]|$)|^backend\.|googleapis\.com$", re.I)
_API_PATH = re.compile(r"/api(/|$)", re.I)

# A documentation path. `developers.google.com/youtube/v3/docs/search/list` is the case
# this exists for: the host is not an API host and the path says so twice.
_DOCS_PATH = re.compile(
    r"/(docs?|documentation|reference|guides?|blog|help|support|tutorials?)(/|$)", re.I)


def _split(url: str) -> tuple[str, str]:
    """`(host, path)` for a URL that already matched `_URL`."""
    parts = url.split("/")
    return parts[2].lower(), "/" + "/".join(parts[3:])


def is_api_call(url: str) -> bool:
    """Does this URL address an API surface rather than a page about one?"""
    host, path = _split(url)
    if _DOCS_PATH.search(path):
        return False
    return bool(_API_HOST.search(host)) or bool(_API_PATH.search(path))


def api_versions(text: str) -> set[tuple[str, str]]:
    """`{(base, version)}` — every versioned API endpoint this text calls.

    `base` is the URL truncated at the version segment, so
    `https://api.murf.ai/v1/speech/generate` and `https://api.murf.ai/v1/voices` are one
    place to edit rather than two.
    """
    out: set[tuple[str, str]] = set()
    for url in _URL.findall(text or ""):
        host, path = _split(url)
        m = _VERSION_SEG.search(path)
        if not m or not is_api_call(url):
            continue
        base = f"{url.split('://')[0]}://{host}{path[:m.end()]}"
        out.add((base, m.group(1).lower()))
    return out


def owner(host: str, deps: Iterable[Dependency]):
    """The tracked dependency whose authority set covers this host, if any.

    Longest declared domain wins, so `global.api.murf.ai` binds to the entry that
    declares it rather than to a broader one that merely ends the same way.
    """
    best, best_len = None, -1
    for d in deps:
        for dom in d.subject().official_domains:
            dom = (dom or "").lower().lstrip(".")
            if not dom:
                continue
            if (host == dom or host.endswith("." + dom)) and len(dom) > best_len:
                best, best_len = d, len(dom)
    return best


def _site(r: ContentRecord) -> dict:
    """Enough of a record to become a `Location` later, and nothing more."""
    return {"content_id": r.content_id, "course": r.course,
            "topic_name": r.topic_name, "unit_id": r.unit_id,
            "unit_name": r.unit_name, "field_path": r.field_path,
            "object_type": r.object_type, "session_no": r.session_no}


def attach(records: Iterable[ContentRecord],
           deps: Iterable[Dependency]) -> tuple[int, dict]:
    """Record each dependency's taught API versions, and where each is called.

    Returns `(rows attached, {base: [site, ...]})`. The site map is shared rather than
    copied onto every dependency, for the reason `params.attach` gives: the same record
    is the same record for every dependency that reads it.

    Uncapped, deliberately. `params.py` carried a 40-candidate cap that silently evicted
    the very field it was built for, and there is no reason to repeat it on a list this
    size.
    """
    records, deps = list(records), list(deps)
    by_dep: dict[str, dict[str, str]] = {}
    sites: dict[str, list[dict]] = {}

    for r in records:
        if not r.content_id:
            continue
        for base, version in api_versions(r.body_text):
            host, _ = _split(base)
            d = owner(host, deps)
            if d is None:
                continue        # nobody can speak for this host; nothing to check
            by_dep.setdefault(d.dep_id, {})[base] = version
            rows = sites.setdefault(base, [])
            if not any(x["content_id"] == r.content_id for x in rows):
                rows.append(_site(r))

    n = 0
    for d in deps:
        found = by_dep.get(d.dep_id) or {}
        d.taught_api = [{"base": b, "version": v} for b, v in sorted(found.items())]
        n += len(d.taught_api)
    return n, {b: v for b, v in sites.items()
               if any(b in (by_dep.get(d.dep_id) or {}) for d in deps)}
