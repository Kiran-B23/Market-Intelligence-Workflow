"""LLM access, with the Claude Code CLI as the default provider.

Testing runs under a cost constraint: no metered API key. So the default backend
shells out to the `claude` CLI in print mode, which draws on the operator's existing
Claude Code entitlement instead of a separate billed key. It is **not free** — each
call reports a `total_cost_usd`, mostly fixed cache overhead (~$0.01/call measured) —
so three protections are built in rather than bolted on:

* **A disk cache keyed by (provider, model, prompt).** Re-running an eval suite after
  a code change costs nothing for the cases whose prompts did not change. This is what
  makes an eval harness affordable to run often, which is the only way it gets run.
* **A per-process call budget.** `MAX_CALLS` and `MAX_SPEND_USD` stop a runaway loop
  from quietly spending the operator's allowance.
* **A hard rule about what the LLM is for.** It refines wording and classifies; it
  never supplies facts. Callers pass already-verified claims. Nothing here can create
  a `Claim`, because `Claim.build()` requires a fetched source and a quote.

Providers, in the order `auto` tries them: `claude_code` (the CLI), `openrouter` (only
if `OPENROUTER_API_KEY` is set), then `none` — which returns a miss so every caller
must already have a deterministic fallback path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import settings

CACHE_DIR = Path("state/llm_cache")
MAX_CALLS = int(os.getenv("LLM_MAX_CALLS", "120"))
MAX_SPEND_USD = float(os.getenv("LLM_MAX_SPEND_USD", "2.00"))
TIMEOUT_S = int(os.getenv("LLM_TIMEOUT_S", "180"))

# Claude Code model aliases. Haiku is the default: these are short wording and
# classification tasks, not reasoning tasks.
DEFAULT_MODEL = os.getenv("MIW_LLM_MODEL", "haiku")

# `--allowedTools ""` does NOT mean "no tools" - the CLI reads an empty allow list as
# "no restriction". Verified: with only that flag, the model attempted `Read` and then
# `Bash` over 3 turns, and was stopped only by an unrelated user setting
# (blockReadsOutsideWorkingDirectories) that the operator is free to switch off.
#
# That matters here beyond tidiness. This call is handed verbatim text fetched from
# third-party vendor pages, so a compromised or hostile page could carry instructions;
# a model with Bash in reach turns prompt injection into command execution. It could
# also read MIW's own `out/findings_*.json` and "confirm" a claim from the system's own
# output - the circular evidence the trust layer exists to prevent.
#
# An explicit denylist does work (verified: zero tool attempts, model reports no file
# or shell tools). Belt and braces: a turn bound, and a fresh empty working directory.
DENY_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash",
              "BashOutput", "KillShell", "Glob", "Grep", "WebFetch", "WebSearch",
              "Task", "TodoWrite", "SlashCommand", "Skill")
MAX_TURNS = int(os.getenv("LLM_MAX_TURNS", "4"))

BUDGET_FILE = Path("state/llm_budget.json")

# The budget lives on disk, keyed by date. Every pipeline stage is a separate
# subprocess, so module globals reset at each stage boundary: MAX_SPEND_USD read as a
# per-stage cap, and a six-stage run could spend six times the number the operator set.
_spent = 0.0
_calls = 0


def _budget_today() -> dict:
    from datetime import date
    today = date.today().isoformat()
    try:
        d = json.loads(BUDGET_FILE.read_text())
    except (json.JSONDecodeError, OSError, ValueError):
        d = {}
    return d if d.get("date") == today else {"date": today, "calls": 0, "spent": 0.0}


def _budget_add(calls: int, spent: float) -> dict:
    d = _budget_today()
    d["calls"] += calls
    d["spent"] = round(d["spent"] + spent, 6)
    BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        BUDGET_FILE.write_text(json.dumps(d))
    except OSError:
        pass
    return d


@dataclass
class LLMResult:
    text: str = ""
    ok: bool = False
    error: str = ""
    provider: str = ""
    model: str = ""
    cost_usd: float = 0.0
    cached: bool = False
    raw: dict = field(default_factory=dict)
    tool_attempts: list = field(default_factory=list)

    def json(self) -> Optional[object]:
        """Parse the reply as JSON, tolerating code fences and surrounding prose."""
        if not self.ok or not self.text:
            return None
        return extract_json(self.text)


_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")


def extract_json(text: str):
    body = _FENCE.sub("", (text or "").strip())
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        pass
    # Fall back to the outermost bracketed span — models sometimes add a preamble.
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = body.find(opener), body.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(body[i:j + 1])
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def available_provider() -> str:
    forced = os.getenv("MIW_LLM_PROVIDER", "auto").strip().lower()
    if forced != "auto":
        return forced
    if shutil.which("claude"):
        return "claude_code"
    if settings.OPENROUTER_API_KEY:
        return "openrouter"
    return "none"


def budget_state() -> dict:
    """Today's spend across every stage, not just this process."""
    d = _budget_today()
    return {"calls_today": d["calls"], "max_calls": MAX_CALLS,
            "spent_usd_today": round(d["spent"], 4), "max_spend_usd": MAX_SPEND_USD,
            "this_process_calls": _calls}


def _cache_path(provider: str, model: str, prompt: str) -> Path:
    key = hashlib.sha256(f"{provider}\x1f{model}\x1f{prompt}".encode()).hexdigest()[:32]
    return CACHE_DIR / f"{key}.json"


def _claude_code(prompt: str, model: str) -> LLMResult:
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
           "--disallowedTools", *DENY_TOOLS,
           "--max-turns", str(MAX_TURNS),
           "--exclude-dynamic-system-prompt-sections"]
    # A fresh empty directory, not /tmp: /tmp is shared and world-writable, so it is
    # not a sandbox. This is the last line of defence, not the first.
    workdir = tempfile.mkdtemp(prefix="miw-llm-")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S,
                              cwd=workdir)
    except subprocess.TimeoutExpired:
        return LLMResult(error=f"timeout after {TIMEOUT_S}s", provider="claude_code",
                         model=model)
    except OSError as exc:
        return LLMResult(error=f"{type(exc).__name__}: {exc}", provider="claude_code",
                         model=model)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if proc.returncode != 0:
        return LLMResult(error=f"exit {proc.returncode}: {proc.stderr.strip()[:300]}",
                         provider="claude_code", model=model)
    try:
        env = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return LLMResult(error="unparseable CLI envelope", provider="claude_code",
                         model=model)
    if env.get("is_error"):
        return LLMResult(error=str(env.get("result"))[:300], provider="claude_code",
                         model=model, raw=env)
    attempted = [d.get("tool_name") for d in (env.get("permission_denials") or [])]
    if attempted:
        # Not fatal, but it means the isolation flags stopped working. Loud, not silent.
        print(f"  WARNING: LLM call attempted blocked tool(s) {attempted}; "
              f"isolation held but check miw/llm.py DENY_TOOLS", file=sys.stderr)
    return LLMResult(text=str(env.get("result") or ""), ok=True, provider="claude_code",
                     model=model, cost_usd=float(env.get("total_cost_usd") or 0.0),
                     raw={k: env.get(k) for k in ("duration_ms", "num_turns", "usage")},
                     tool_attempts=attempted)


def _openrouter(prompt: str, model: str) -> LLMResult:
    try:
        from openai import OpenAI
    except ImportError:
        return LLMResult(error="openai package not installed", provider="openrouter")
    client = OpenAI(api_key=settings.OPENROUTER_API_KEY,
                    base_url=settings.LLM_BASE_URL)
    try:
        r = client.chat.completions.create(
            model=model, temperature=0, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}])
    except Exception as exc:
        return LLMResult(error=f"{type(exc).__name__}: {exc}"[:300], provider="openrouter",
                         model=model)
    return LLMResult(text=r.choices[0].message.content or "", ok=True,
                     provider="openrouter", model=model)


def complete(prompt: str, *, model: str = "", use_cache: bool = True) -> LLMResult:
    """One LLM call. Never raises: failures come back on `LLMResult.error`.

    Every caller must behave correctly when `ok` is False — the deterministic path is
    the product, and the model is an enhancement to it.
    """
    global _spent, _calls
    provider = available_provider()
    model = model or (DEFAULT_MODEL if provider == "claude_code" else settings.LLM_MODEL)

    if provider == "none":
        return LLMResult(error="no LLM provider available", provider="none")

    path = _cache_path(provider, model, prompt)
    if use_cache and path.exists():
        try:
            d = json.loads(path.read_text())
            return LLMResult(text=d["text"], ok=True, provider=provider, model=model,
                             cost_usd=0.0, cached=True)
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    day = _budget_today()
    if day["calls"] >= MAX_CALLS:
        return LLMResult(error=f"call budget exhausted ({MAX_CALLS} today); "
                               f"raise LLM_MAX_CALLS to continue", provider=provider)
    if day["spent"] >= MAX_SPEND_USD:
        return LLMResult(error=f"spend budget exhausted (${MAX_SPEND_USD} today); "
                               f"raise LLM_MAX_SPEND_USD to continue", provider=provider)

    res = _claude_code(prompt, model) if provider == "claude_code" \
        else _openrouter(prompt, model)
    _calls += 1
    _spent += res.cost_usd
    _budget_add(1, res.cost_usd)

    if res.ok and use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps({"text": res.text, "model": model,
                                        "provider": provider, "at": time.time()}))
        except OSError:
            pass
    return res


PROMPT_DIR = Path("prompts")


def load_prompt(name: str, **fields) -> str:
    """Load `prompts/<name>.txt` and fill `{placeholders}` via str.format().

    Prompt bodies live in versioned plain-text files so an eval suite can point at the
    same file the pipeline uses — the convention in
    `Market-Intelligence-Gathering-Curriculum-Gap-Analyzer/backend/prompts`. Renaming a
    placeholder therefore breaks the prompt and its eval together, which is the point.
    Literal braces in a prompt file must be doubled for `.format()`.
    """
    body = (PROMPT_DIR / f"{name}.txt").read_text()
    return body.format(**fields) if fields else body
