"""Column-role table reading — the structure that makes proximity bugs impossible.

Every case here corresponds to a real vendor page. The must-not-fire cases matter most:
a false retirement on a taught model would send someone to rewrite working sessions.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIX = Path(__file__).parent / "fixtures"

from miw.probe.catalogue import (Cell, entries, split_replacements, tables)


def _groq():
    return (FIX / "groq_deprecations.html").read_text()


def _google():
    return (FIX / "google_models.html").read_text()


# --------------------------------------------------------------- roles

def test_groq_columns_resolve_to_roles():
    t = tables(_groq())[0]
    assert t.headers == ("Deprecated Model", "Shutdown Date",
                         "Recommended Replacement Model ID")
    assert t.roles == {"id": 0, "date": 1, "replacement": 2}
    assert t.role == "deprecation"


def test_google_keeps_both_id_columns_and_matches_on_the_coded_one():
    """The name cell carries the status, the Endpoint cell carries the exact id."""
    t = tables(_google())[0]
    assert t.headers == ("Model", "Description", "Endpoint")
    assert t.id_columns == (0, 2)
    row = t.rows[0]
    assert t.identifier_cell(row).text == "gemini-2.0-flash"


# ------------------------------------------- the dual-role trap (the whole point)

def test_the_same_id_is_deprecated_in_one_row_and_a_replacement_in_another():
    """On Groq's page `llama-3.3-70b-versatile` sits in the Deprecated Model column of
    one row and the Recommended Replacement column of others. A text search fires on
    all of them; only the first is a finding."""
    got = entries(_groq(), evidence_url="u")
    target = "llama-3.3-70b-versatile"
    as_id = [e for e in got if e.entry_id == target and e.column_role == "id"]
    as_repl = [e for e in got if e.entry_id == target and e.column_role == "replacement"]
    assert len(as_id) == 1, "exactly one row deprecates it"
    assert as_id[0].retired and as_id[0].shutdown_date == "08/16/26"
    assert as_id[0].replacement_ids == ["openai/gpt-oss-120b", "qwen/qwen3.6-27b"]
    assert as_repl, "and it is named as the successor to older models"
    assert all(not e.retired for e in as_repl), \
        "an id in a replacement column must never be marked retired"


def test_status_comes_from_the_row_not_the_neighbour():
    """Google lists several models each with its own `(Shut down)`. The status must bind
    to the id in the same row, never to the one above or below."""
    got = {e.entry_id: e for e in entries(_google(), evidence_url="u")
           if e.column_role == "id"}
    assert got["gemini-2.0-flash"].status == "shut down"
    assert got["gemini-2.0-flash-lite"].status == "shut down"
    # each row's quote names only its own model
    assert "Flash-Lite" not in got["gemini-2.0-flash"].quote


def test_an_id_never_implicates_its_suffixed_siblings():
    """`gemini-2.0-flash`, `-lite`, `-001` and `-exp` are all real inventory entries; a
    substring rule would retire four models from one row."""
    got = {e.entry_id for e in entries(_google()) if e.column_role == "id"}
    assert "gemini-2.0-flash" in got and "gemini-2.0-flash-lite" in got
    # distinct keys, not one prefix swallowing the other
    assert len({"gemini-2.0-flash", "gemini-2.0-flash-lite"} & got) == 2


# --------------------------------------------------------------- parsing details

def test_replacement_splitting_keeps_namespaced_ids_intact():
    assert split_replacements("openai/gpt-oss-120b or qwen/qwen3.6-27b") == \
        ["openai/gpt-oss-120b", "qwen/qwen3.6-27b"]
    assert split_replacements("llama-3.3-70b-versatile") == ["llama-3.3-70b-versatile"]
    assert split_replacements("") == []


def test_id_shaped_tokens_are_lifted_out_of_a_prose_cell():
    """Groq's models table writes "GPT OSS 120B openai/gpt-oss-120b" in one cell."""
    assert "openai/gpt-oss-120b" in Cell(text="GPT OSS 120B openai/gpt-oss-120b").ids()
    # a display name with no id shape is not an id
    assert Cell(text="Gemini 2.0 Flash (Shut down)").ids() == \
        ("Gemini 2.0 Flash (Shut down)",)


def test_a_document_with_no_role_typed_table_yields_nothing():
    """Never fall back to text search — that is how "we could not parse it" becomes
    "it is gone"."""
    assert entries("<p>Some models were deprecated. gemini-2.5-flash</p>") == []
    assert entries("<table><tr><th>Colour</th></tr><tr><td>blue</td></tr></table>") == []


# ------------------------------------------------------- the OpenAI adapter's real shape

def test_the_openai_adapter_is_a_retirement_watch_not_a_live_catalogue():
    """Its page is `docs/deprecations`, and that is not an accident.

    `platform.openai.com/docs/models` is client-rendered and a plain fetch returns a
    378KB shell with zero tables. The deprecations page role-types cleanly — but every
    identifier-column row on it is, by construction, retired. So this adapter makes S7
    possible for OpenAI and contributes nothing to S12, and the docstring has to say so
    rather than imply coverage we do not have.
    """
    from miw.vendors.openai import DEPRECATIONS_URL, OpenAIAdapter
    import miw.vendors.openai as mod

    assert DEPRECATIONS_URL.endswith("/deprecations")
    assert OpenAIAdapter.kinds == ("model",)
    assert "platform.openai.com" in OpenAIAdapter.official_domains
    doc = mod.__doc__ or ""
    # The rejected pages are recorded so nobody re-tests them by hand.
    for rejected in ("docs/models", "docs.anthropic.com", "huggingface.co/models"):
        assert rejected in doc, f"{rejected} is not recorded as tested-and-rejected"
    assert "S12 gains nothing from OpenAI" in doc


def test_every_adapter_is_registered_once():
    from miw.vendors.base import all_adapters
    keys = [a.key for a in all_adapters()]
    assert keys == sorted(set(keys), key=keys.index), "a duplicate adapter key"
    assert {"groq", "google_ai", "openai"} <= set(keys)
