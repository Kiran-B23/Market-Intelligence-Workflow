"""A method the curriculum calls is the same class of problem as a field it sends.

S13 was built for `multiNativeLocale` — a field Murf retired while every other signal
read the dependency as healthy. A method a vendor renames is the same thing one step
along: `murf.ai` still answers 200, the docs are up, and the call in the notebook is
wrong. The system had no way to see it.

The alternative was a rename detector built from a phrase list, and there is already
one: `research/official.py`'s IMPLEMENTATION cues (`"renamed"`, `"replaced"`,
`"removed the"`). Measured over the whole live inventory it produced three claims, none
substantiating, two of them Wikipedia prose about OpenAI removing a chief executive. A
phrase list finds sentences containing a word. So this reuses the machinery that works
instead: the curriculum proposes a symbol, and the vendor's own reference page disposes.

What the two halves share, and where they differ, is one rule each:

* **extraction** — a key is only a key when it is written in a payload (`"locale":`),
  and a method is only a method when it is called ON something (`client.generate(`).
  A bare `process(` is a helper the course defined three cells earlier.
* **the page** — a field declares a TYPE, a method declares a SIGNATURE. Both are the
  same proof that the name is a member the vendor documents rather than a word in a
  sentence.

**Evidence, stated honestly.** The no-false-positives half is proven on real pages: the
eight held-out `fields` fixtures still pass with their `max_fields` caps, and a live
LangChain API index that lists `Class BaseChatMessageHistory` / `deprecated` on separate
lines yields nothing, which is correct — a navigation listing is not a declaration. The
detection half is proven here on constructed pages only. No live SDK reference I could
fetch declares a deprecated method in server-rendered HTML, because those references are
client-rendered, and that is a real limit on this check rather than a gap in the tests.
"""
from __future__ import annotations

from miw.extract.params import method_calls, payload_keys, taught_params, taught_symbols
from miw.probe.http_probe import deprecated_fields
from miw.schema import ContentRecord, Dependency, Location


def rec(content_id: str, body: str) -> ContentRecord:
    return ContentRecord(
        course="Building LLM Applications", topic_name="T", unit_id="u",
        unit_name="Unit", unit_type="LEARNING", content_id=content_id,
        object_type="LEARNING_RESOURCE", content_type="MARKDOWN", title="t",
        body_text=body, field_path="content", evidence_source="markdown",
        source_file="f.json", session_no=18)


def dep(name: str, *content_ids: str) -> Dependency:
    return Dependency(
        kind="package", canonical_name=name, homepage=f"https://{name}.example",
        official_domains=[f"{name}.example"],
        locations=[Location(course="Building LLM Applications", topic_name="T",
                            unit_id="u", unit_name="Unit", content_id=c,
                            field_path="content", evidence_source="prose_name",
                            object_type="LEARNING_RESOURCE") for c in content_ids])


# --------------------------------------------------------------- extraction: the dot

def test_a_method_is_only_a_method_when_called_on_something():
    """The rule that makes the method half tractable at all.

    Without it every `print(`, every `range(` and every helper the course defined three
    cells earlier becomes a candidate SDK symbol. With it, what survives on the live
    curriculum is 80 distinct names led by `invoke`, `generate_content`,
    `from_pretrained` and `similarity_search` — real vendor surface.
    """
    body = "result = client.generate_content(prompt)\nvalue = process(raw)\n"
    assert "generate_content" in method_calls(body)
    assert "process" not in method_calls(body), "a bare call is not a vendor's method"


def test_the_language_and_its_standard_library_are_not_a_vendors_api():
    body = "text.strip().lower()\nrows.append(x)\nos.unlink(p)\njson.dumps(d)\n"
    assert method_calls(body) == set()


def test_our_own_grading_schema_is_not_a_vendors_api():
    """The largest single source of candidates on the live inventory, and it is ours.

    `test_case_enum` was being offered to 62 of 195 dependencies. No vendor documents
    it, so it could never become a finding — but candidates are ordered by how many
    records write them, and these outnumber every real payload key in the curriculum.
    """
    body = '{"test_case_enum": "x", "must_contain": "y", "voiceId": "z"}'
    assert payload_keys(body) == {"voiceId"}


def test_a_payload_key_and_a_method_land_in_one_set():
    body = 'client.generate_content({"multiNativeLocale": "en-US"})'
    assert taught_symbols(body) == {"generate_content", "multiNativeLocale"}


# ------------------------------------------------------------- extraction: no eviction

def test_candidates_are_not_truncated():
    """The regression that nearly ate the case this whole check was built for.

    Candidates used to be capped at the 40 most-written per dependency. Because the
    most-written symbols in this curriculum are the grading schema and the n8n workflow
    file format — ambient scaffolding that co-occurs with almost every dependency —
    `multiNativeLocale` survived at position 30 of 40 on the live inventory, and 93 of
    195 dependencies sat exactly at the cap. One more quiz format key and Murf's finding
    would have vanished with every test still green.
    """
    keys = [f'"vendorField{i:03d}": {i}' for i in range(60)]
    body = "{" + ", ".join(keys) + ', "multiNativeLocale": "en-US"}'
    d = dep("murf", "c1")
    got = taught_params([rec("c1", body)], [d])[d.dep_id]
    assert len(got) == 61, f"truncated to {len(got)}"
    assert "multiNativeLocale" in got


# ------------------------------------------------------------------ the page: shapes

def test_a_method_is_recognised_by_its_signature():
    page = ("generate_content(contents, *, config=None)  Deprecated\n"
            "  Use generate_content_async instead.\n")
    got = {d["field"]: d for d in deprecated_fields(page)}
    assert got["generate_content"]["successor"] == "generate_content_async"
    assert got["generate_content"]["shape"] == "method"


def test_a_field_is_still_recognised_by_its_type():
    page = ("multiNativeLocale  string  Optional  Deprecated\n"
            "  This field is superseded by locale field.\n")
    got = {d["field"]: d for d in deprecated_fields(page)}
    assert got["multiNativeLocale"]["successor"] == "locale"
    assert got["multiNativeLocale"]["shape"] == "field"


def test_a_navigation_listing_is_not_a_declaration():
    """The real shape of LangChain's API index, fetched live while building this.

    It prints `Class BaseChatMessageHistory` and the word `deprecated` on the next line,
    for dozens of classes, as a browsable table of contents. Nothing there states a type
    or a signature, so nothing is a declaration, and reporting these would attach a
    finding to every class a vendor has ever retired regardless of what we teach.
    """
    page = ("Class BaseChatMessageHistory\ndeprecated\n"
            "Abstract base class for storing chat message history.\n")
    assert deprecated_fields(page) == []


def test_prose_with_a_bracket_in_it_is_not_a_signature():
    """The space is the whole distinction, and the first rule written here missed it.

    `_SIGNATURE` began as `^\\s*\\(` and this sentence defeated it: `voice` was reported
    as a renamed METHOD, which reaches a reviewer as a call to go and edit. A
    declaration is written `generate_content(contents)` and never
    `generate_content (contents)`.

    Worded so the sentence starts with the candidate. The first draft of this test
    opened with "The voice (and its locale)...", passed against the broken rule, and
    proved nothing — `the` is in `_NOT_A_FIELD`, so the match was discarded a step
    earlier and the signature rule never ran.
    """
    page = "voice (and its locale) are deprecated in v2 now.\n"
    assert [d["field"] for d in deprecated_fields(page)] == []


def test_a_signature_may_take_no_arguments():
    page = "create()  Deprecated\n  Use create_async instead.\n"
    got = {d["field"]: d["shape"] for d in deprecated_fields(page)}
    assert got.get("create") == "method"


# ------------------------------------------------------- what the reviewer is told

def _probe(rows):
    from miw.schema import ProbeResult
    return ProbeResult(dep_id="d", canonical_name="x", status="changed",
                       deprecated_fields=rows)


def test_a_renamed_method_is_not_described_as_a_field_we_send():
    """The wrong noun here is a wrong instruction, not a vague one.

    A field is edited inside a request payload; a method is edited at every call site.
    Telling a reviewer that `generate_content` is "a field we send" sends them looking
    for a JSON key that does not exist.
    """
    from miw.analyse.score import _field_summary

    d = dep("google-genai", "c1")
    row = {"field": "generate_content", "successor": "generate_content_async",
           "quote": "generate_content(contents) deprecated", "shape": "method"}
    said = _field_summary(d, _probe([row]))
    assert "method `generate_content`" in said
    assert "calls it" in said
    assert "field" not in said


def test_a_deprecated_field_still_reads_as_a_field():
    from miw.analyse.score import _field_summary

    d = dep("murf", "c1")
    row = {"field": "multiNativeLocale", "successor": "locale",
           "quote": "multiNativeLocale string deprecated", "shape": "field"}
    said = _field_summary(d, _probe([row]))
    assert "field `multiNativeLocale`" in said and "writes it" in said


def test_the_fix_for_a_method_points_at_call_sites():
    from miw.analyse.score import recommend
    from miw.schema import Finding

    d = dep("google-genai", "c1")
    f = Finding(dep_id=d.dep_id, canonical_name="google-genai", signal="S13",
                signal_label="x", kind_of_signal="regression", severity="high",
                summary="s")
    f.deprecated_fields = [{"field": "generate_content",
                            "successor": "generate_content_async",
                            "quote": "generate_content(contents) deprecated",
                            "shape": "method"}]
    said = recommend(d, f)
    assert "call site" in said and "request payloads" not in said


def test_the_shape_survives_the_hop_from_page_to_finding():
    """The row is rebuilt in the probe, not copied, so a new key is dropped by default.

    `_match_taught_fields` constructs a fresh dict — it has to, because it adds
    `evidence_url`, which the detector has no way to know. That is exactly how `shape`
    went missing on the first live run: Murf's `multiNativeLocale` came back with the
    field, the successor and the quote, and no shape at all, so every method would have
    been described to a reviewer as a field.
    """
    from miw.probe.runner import _match_taught_fields
    from miw.probe.http_probe import UrlObservation
    from miw.schema import ProbeResult

    d = dep("google-genai", "c1")
    d.taught_params = ["generate_content", "multiNativeLocale"]
    o = UrlObservation(url="https://ai.example/reference")
    o.deprecated_fields = [
        {"field": "generate_content", "successor": "generate_content_async",
         "quote": "generate_content(contents) deprecated", "shape": "method"},
        {"field": "multiNativeLocale", "successor": "locale",
         "quote": "multiNativeLocale string deprecated", "shape": "field"}]
    res = ProbeResult(dep_id=d.dep_id, canonical_name="google-genai", status="changed")
    _match_taught_fields(d, [o], res)

    got = {r["field"]: r["shape"] for r in res.deprecated_fields}
    assert got == {"generate_content": "method", "multiNativeLocale": "field"}
