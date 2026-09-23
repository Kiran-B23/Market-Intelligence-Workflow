"""The API version the course CALLS, and the vendor's own notice that it is closing.

S13 looks inside the request — which fields it sends, which methods it calls. This asks
the question one level out: **which version of the API does the request address?** A
vendor can retire an entire API version while everything else the system watches stays
green. The tool is live, the pricing page is up, the fields are current, and every code
sample in the course posts to an endpoint with a shutdown date on it.

Three refusals carry this, and each was measured on the live curriculum rather than
assumed:

* **A version is only pinned where the course CALLS the URL.** The curriculum writes
  `developers.google.com/youtube/v3/docs/search/list` — a link to a manual that happens
  to carry `v3` — beside `www.googleapis.com/youtube/v3/search`, which is the endpoint
  the solution posts to. Reading the first as a taught API version reports the course
  for linking to documentation.
* **A version is a bounded token.** `v1` occurs inside `v10`, `rev1` and `Nov1`. A
  substring match reports a vendor announcing its tenth version as retiring its first.
* **The sentence must be about the interface.** A sunset phrase plus a bounded `v1`
  still matches "Whisper v1 will be removed" — a model's version, not the endpoint's.

**Binding is by domain, which makes this stricter than the field check rather than
looser.** A payload key has to be offered to every dependency in the record and settled
later by the vendor's own page, because one lesson names a dozen tools. A host does not:
`api.murf.ai` falls under Murf's authority set, so whose API it is is a matter of record.
A host no tracked dependency can speak for produces nothing at all.
"""
from __future__ import annotations

from miw.extract.apiversion import api_versions, attach, is_api_call, owner
from miw.probe.http_probe import api_version_notices, api_version_sunset
from miw.schema import ContentRecord, Dependency, Location


def rec(content_id: str, body: str) -> ContentRecord:
    return ContentRecord(
        course="Building LLM Applications", topic_name="T", unit_id="u",
        unit_name="Unit", unit_type="LEARNING", content_id=content_id,
        object_type="LEARNING_RESOURCE", content_type="MARKDOWN", title="t",
        body_text=body, field_path="content", evidence_source="markdown",
        source_file="f.json", session_no=18)


def dep(name: str, *domains: str, tier: str = "critical") -> Dependency:
    return Dependency(
        kind="tool", canonical_name=name, homepage=f"https://{domains[0]}",
        official_domains=list(domains), watch_tier=tier,
        locations=[Location(course="Building LLM Applications", topic_name="T",
                            unit_id="u", unit_name="Unit", content_id="named",
                            field_path="content", evidence_source="prose_name",
                            object_type="LEARNING_RESOURCE")])


# ------------------------------------------------- refusal 1: a call, not a manual

def test_a_documentation_link_is_not_a_taught_api_version():
    """The live case this rule exists for, both halves of it in one record.

    Both URLs carry `v3`. One is the endpoint the solution posts to; the other is the
    page a student reads about it. Only the first is a version the course depends on.
    """
    assert not is_api_call("https://developers.google.com/youtube/v3/docs/search/list")
    assert is_api_call("https://www.googleapis.com/youtube/v3/search")


def test_an_api_label_may_be_hyphenated():
    """`open.er-api.com/v6/latest/USD` is a real endpoint the course calls.

    The first version of the host rule required the `api` label to follow a dot, and it
    dropped this one while keeping every other host on the inventory.
    """
    assert is_api_call("https://open.er-api.com/v6/latest/USD")
    assert not is_api_call("https://therapist.example/v1/notes")


def test_an_api_path_counts_when_the_host_does_not():
    assert is_api_call("https://openrouter.ai/api/v1/chat/completions")


def test_one_base_rather_than_one_row_per_endpoint():
    """Two calls to the same versioned API are one place to edit, not two."""
    body = ('requests.post("https://api.murf.ai/v1/speech/generate")\n'
            'requests.get("https://api.murf.ai/v1/voices")\n')
    assert api_versions(body) == {("https://api.murf.ai/v1", "v1")}


# ----------------------------------------------------- binding: by domain, not guess

def test_the_longest_declared_domain_owns_the_host():
    murf = dep("Murf.AI", "murf.ai")
    murf_global = dep("Murf Global", "global.api.murf.ai")
    got = owner("global.api.murf.ai", [murf, murf_global])
    assert got is murf_global


def test_a_host_nobody_can_speak_for_produces_nothing():
    """`www.googleapis.com` and `api.freepik.com` on today's inventory.

    The same refusal `params.taught_params` makes when a dependency has no official
    domain: with nothing authoritative to check against, a candidate is not worth
    recording.
    """
    d = dep("Murf.AI", "murf.ai")
    n, sites = attach([rec("c1", 'post("https://api.freepik.com/v1/ai/text-to-image")')],
                      [d])
    assert (n, sites, d.taught_api) == (0, {}, [])


def test_the_sites_are_the_records_that_call_it():
    """Not the records where the dependency was NAMED — the ones that call the URL.

    Measured on Murf: `global.api.murf.ai/v1` is called in ten records, and the finding
    has to name all ten however many of them say the word "Murf".
    """
    d = dep("Murf.AI", "murf.ai")
    records = [rec("c1", 'post("https://api.murf.ai/v1/speech/generate")'),
               rec("c2", 'r = requests.post("https://api.murf.ai/v1/voices")'),
               rec("c3", "nothing here")]
    n, sites = attach(records, [d])
    assert n == 1
    assert d.taught_api == [{"base": "https://api.murf.ai/v1", "version": "v1"}]
    assert {s["content_id"] for s in sites["https://api.murf.ai/v1"]} == {"c1", "c2"}


# ------------------------------------------- refusal 2 and 3: what the page has to say

def test_a_version_is_not_lifted_out_of_a_longer_token():
    for text in ("Our API v10 will be shut down on June 1, 2026.",
                 "The rev1 API will be shut down on June 1, 2026."):
        assert api_version_sunset(text, ("v1",)) == []


def test_the_sentence_must_be_about_the_interface():
    """A model's version is not the endpoint's version, on a page that serves both."""
    assert api_version_sunset(
        "Whisper v1 will be removed from the catalogue in June.", ("v1",)) == []


def test_a_vendor_retiring_a_version_we_never_taught_is_not_our_problem():
    text = "The v1 API will be shut down on June 1, 2026."
    assert api_version_notices(text)          # the page does say it
    assert api_version_sunset(text, ("v3",)) == []


def test_the_notice_is_quoted_not_inferred():
    text = "The v2beta API will be retired on March 1. Migrate to v2."
    got = api_version_sunset(text, ("v2beta",))
    assert got == [{"version": "v2beta",
                    "quote": "The v2beta API will be retired on March 1."}]


def test_a_current_version_says_nothing():
    assert api_version_sunset("The v1 API is the current version.", ("v1",)) == []


# --------------------------------------------------- what reaches the reviewer

def test_a_reference_only_tool_is_still_checked():
    """The one place this deliberately differs from the field check.

    `_taught_field_pass` skips `mention-only`, because a tool named so students are
    aware of it writes no payload and any key it inherits from a co-located record
    would be a false attribution. A versioned endpoint is bound by DOMAIN, so if the
    course calls `api.example/v1` it calls it whatever the watch tier says. The tier
    governs how loudly it is reported, not whether the fact is true.
    """
    from miw.probe.runner import _taught_api_pass
    from miw.probe.http_probe import UrlObservation
    from miw.schema import ProbeResult

    d = dep("Sidekick", "sidekick.example", tier="mention-only")
    d.taught_api = [{"base": "https://api.sidekick.example/v1", "version": "v1"}]
    o = UrlObservation(url="https://sidekick.example/changelog")
    o.api_version_notices = [{"version": "v1",
                              "quote": "The v1 API will be shut down on June 1, 2026."}]
    res = ProbeResult(dep_id=d.dep_id, canonical_name="Sidekick", status="changed")
    _taught_api_pass(d, res, [o])

    assert "taught_api_version_sunset" in res.signals
    assert res.api_version_sunset[0]["version"] == "v1"
    assert res.api_version_sunset[0]["bases"] == ["https://api.sidekick.example/v1"]


def test_the_finding_names_every_record_that_calls_the_endpoint():
    """Scoped to the records that CALL it, not to where the dependency was named.

    The `named` location below is the only place this dependency was recognised; `c1`
    and `c2` post to the endpoint without saying its name. A finding that intersected
    with `dep.locations` would report one place and miss both real ones.
    """
    from miw.analyse.score import scope_locations, set_api_sites
    from miw.schema import Finding

    d = dep("Murf.AI", "murf.ai")
    set_api_sites({"https://api.murf.ai/v1": [
        {"content_id": "c1", "course": "Building LLM Applications", "topic_name": "T",
         "unit_id": "u", "unit_name": "Unit", "field_path": "content",
         "object_type": "OBJECTIVE_QUESTIONS", "session_no": 18},
        {"content_id": "c2", "course": "Building LLM Applications", "topic_name": "T",
         "unit_id": "u", "unit_name": "Unit", "field_path": "content",
         "object_type": "LEARNING_RESOURCE", "session_no": 19}]})
    try:
        f = Finding(dep_id=d.dep_id, canonical_name="Murf.AI", signal="S16",
                    signal_label="x", kind_of_signal="regression", severity="high",
                    summary="s")
        f.api_version_sunset = [{"version": "v1", "quote": "q",
                                 "evidence_url": "https://murf.ai/docs",
                                 "bases": ["https://api.murf.ai/v1"]}]
        scope_locations(d, f)
        assert {l.content_id for l in f.locations} == {"c1", "c2"}
        assert all(l.evidence_source == "api_call" for l in f.locations)
        assert f.affects_total == 2
    finally:
        set_api_sites({})


def test_the_fix_names_the_base_url_to_repoint():
    from miw.analyse.score import recommend
    from miw.schema import Finding

    d = dep("Murf.AI", "murf.ai")
    f = Finding(dep_id=d.dep_id, canonical_name="Murf.AI", signal="S16",
                signal_label="x", kind_of_signal="regression", severity="high",
                summary="s")
    f.api_version_sunset = [{"version": "v1", "quote": "q",
                             "evidence_url": "https://murf.ai/docs",
                             "bases": ["https://api.murf.ai/v1"]}]
    said = recommend(d, f)
    assert "https://api.murf.ai/v1" in said and "v1" in said


# ---------------------------------------- held out: real sentences from real vendors

def test_a_page_explaining_versioning_is_not_an_announcement():
    """Verbatim from GitHub's own `about-the-rest-api/api-versions` page, fetched live.

    The page is full of retirement vocabulary — five sentences of it — and announces
    nothing, because it is documentation ABOUT the versioning scheme. Not one sentence
    names a version. Reporting these would tell a reviewer their endpoint is closing on
    the strength of a page explaining what the `Sunset` header means.
    """
    real = ("If you specify an API version that is no longer supported, you will "
            "receive a 410 Gone response.\n"
            "Sunset - The date when the API version will be completely removed "
            "(retired), after which requests will return a 410 Gone response.\n"
            "If you rely on unversioned requests, you may observe behavioral changes "
            "as older versions are removed from support.\n")
    assert api_version_notices(real) == []


def test_no_longer_a_required_parameter_is_not_a_retirement():
    """Verbatim from Microsoft's `api-version-deprecation` page, fetched live.

    This one clears two of the three rules on its own: it names `v1` as a bounded token
    and it names the API. Only the phrase list stops it, and it is the reason bare
    "no longer" is not in `_VERSION_SUNSET_PHRASES` while "will no longer be supported"
    is. A page titled *api-version-deprecation* saying `v1` and "no longer" in one
    sentence is exactly the shape a looser rule would fire on.
    """
    real = "api-version is no longer a required parameter with the v1 GA API."
    assert api_version_notices(real) == []
    # And the rule it is distinguished from still works.
    assert api_version_notices(
        "The v1 API will no longer be supported after June 1, 2026.")
