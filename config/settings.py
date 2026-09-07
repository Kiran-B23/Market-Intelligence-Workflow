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
# House convention (Coding-Questions-Generator): Claude for reasoning, a cheap fast
# model for high-volume validation.
# Reserved for a future synthesis stage. NOT WIRED IN PHASE 1: recommendations are
# composed from templates in miw/analyse/score.py:recommend(). Kept here so the house
# model split (Claude to reason, a cheap model to validate) is recorded, but
# capability_note() must not claim a stage that does not exist.
LLM_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5")
VALIDATION_LLM_MODEL = os.getenv("VALIDATION_LLM_MODEL", "google/gemini-2.5-flash")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

RESEARCH_MAX_DEPS = int(os.getenv("RESEARCH_MAX_DEPS", "40"))
ROTATION_SLICE = int(os.getenv("ROTATION_SLICE", "12"))

SEARCH_ENABLED = bool(TAVILY_API_KEY)
LLM_ENABLED = False       # no synthesis stage exists yet; see LLM_MODEL above


def capability_note() -> str:
    bits = [
        f"official-page evidence: always on (no key needed)",
        f"search discovery: {'on' if SEARCH_ENABLED else 'OFF (set TAVILY_API_KEY)'}",
        "recommendations: template-composed (no LLM stage in Phase 1)",
        "robots.txt: honoured, incl. Crawl-delay",
    ]
    return " | ".join(bits)
