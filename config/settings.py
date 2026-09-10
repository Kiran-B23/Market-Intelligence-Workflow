"""Configuration. Everything optional; the ground-truth path needs none of it.

MIW is built so that the evidence layer is deterministic: authoritative claims come
from fetching a vendor's own pages (`miw/research/official.py`), which needs no API
key at all. Search and the LLM are enrichment — search *discovers* candidate
replacements, the LLM *writes* the recommendation prose. If neither key is present the
weekly run still produces cited, authoritative findings; it just discovers fewer
alternatives and writes plainer prose.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(".env")


def _load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
# OpenRouter-specific model override, used only when that provider is selected. The
# general model choice is a LOGICAL name (haiku|sonnet|opus) in MIW_LLM_MODEL, resolved
# per provider by miw.llm.resolve_model.
LLM_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

RESEARCH_MAX_DEPS = int(os.getenv("RESEARCH_MAX_DEPS", "40"))
ROTATION_SLICE = int(os.getenv("ROTATION_SLICE", "12"))
# How many HEALTHY critical dependencies each run asks "is there something better?".
# A sub-slice of the rotation above, kept small on purpose: the aim is a steady trickle
# that surfaces one real opportunity a week, not coverage of the whole inventory. At 4
# a week over the ~35 eligible dependencies, a full cycle takes about nine weeks.
DISCOVERY_SLICE = int(os.getenv("DISCOVERY_SLICE", "4"))

SEARCH_ENABLED = bool(TAVILY_API_KEY)

# --- deep links into the learning platform ------------------------------------------
# One place to teach MIW how to build a link from a location back to the live unit, so
# a reviewer can jump from a finding to the page a student sees. Left EMPTY on purpose:
# an invented URL pattern that happens to 404 is worse than no link, because the
# reviewer cannot tell "this unit moved" from "MIW guessed the URL". While it is empty
# the UI shows copyable ids instead.
#
# Available placeholders, all taken from the Location we already record:
#   {course_slug} {unit_id} {content_id} {session_no} {topic_name}
# Example shape (NOT a real NxtWave URL - replace it with yours):
#   PLATFORM_UNIT_URL = "https://learning.example.com/courses/{course_slug}/units/{unit_id}"
PLATFORM_UNIT_URL = os.getenv("MIW_PLATFORM_UNIT_URL", "")



def _refinement_note() -> str:
    """How notes are being written, asked of the code that actually decides it.

    This used to be a hardcoded "no LLM stage in Phase 1" sitting beside a hardcoded
    `LLM_ENABLED = False` that nothing read — while `analyse --refine` existed and
    worked, and reported its provider through a completely separate channel. Two
    capability channels disagreeing about the same feature is the same class of untruth
    the trust layer exists to prevent, so there is now one source of truth.
    """
    try:                      # local import: miw.llm imports this module at load time
        from miw.llm import provider_status
    except ImportError:       # settings must never become un-importable
        return "note refinement: unknown"
    st = provider_status()
    if st["provider"] == "none":
        # Off by choice and off for want of a credential are different facts, and
        # blaming a missing key when the operator asked for none is its own small lie.
        if st["forced"]:
            return f"note refinement: OFF (MIW_LLM_PROVIDER={st['forced']})"
        reasons = [c["why"] for c in st["candidates"] if c["why"]]
        return f"note refinement: OFF ({'; '.join(reasons) or 'no provider'})"
    return (f"note refinement: available via {st['provider']} "
            f"({st['model_logical']} -> {st['model_resolved']}), opt in with --refine")


def capability_note() -> str:
    bits = [
        "official-page evidence: always on (no key needed)",
        f"search discovery: {'on' if SEARCH_ENABLED else 'OFF (set TAVILY_API_KEY)'}",
        _refinement_note(),
        "robots.txt: honoured, incl. Crawl-delay",
    ]
    return " | ".join(bits)
