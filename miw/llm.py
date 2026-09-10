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

Providers, in the order `auto` tries them: `claude_code` (the CLI), `anthropic` (only
if `ANTHROPIC_API_KEY` is set), `openrouter` (only if `OPENROUTER_API_KEY` is set), then
`none` — which returns a miss so every caller must already have a deterministic fallback
path. The CLI comes first on purpose: a key exported for some unrelated tool must not
silently start metered spend, so spending is opt-in via `MIW_LLM_PROVIDER=anthropic`.

Parity across providers, and the one asymmetry that cannot be closed
--------------------------------------------------------------------
The same prompt bytes reach every provider, and the same acceptance gate
(`miw.analyse.notes.judge_rewrite`) decides whether a rewrite is used, whichever backend
produced it. Tool isolation is **subtractive**: the CLI has ambient tools that must be
DENIED (see `DENY_TOOLS`), while the Messages API and OpenRouter grant none, so the HTTP
paths achieve the same guarantee by sending no `tools`, no `tool_choice`, no `system`
and no `response_format` at all. `tests/test_provider_parity.py` asserts both halves.

What is NOT identical, and should not be claimed as such: the CLI still injects its own
reduced system prompt and runs an agent loop up to `--max-turns`, whereas the HTTP paths
are a single turn with no system prompt and no tools in reach. So the API paths are
*strictly more* isolated than the CLI, not identically isolated — parity here is a
one-directional guarantee. `LLMResult.isolation` records which mechanism held
(`denylist:N` vs `no-tools`), because "no tool attempts because the denylist worked" and
"no tool attempts because nothing was on offer" are different facts.
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

# The reply is a three-field JSON object. Shared by both HTTP providers so they cannot
# drift; the CLI has no equivalent knob, which is one of the documented asymmetries.
MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1500"))

# USD per million tokens, (input, output). Without this the API paths report zero cost
# and MAX_SPEND_USD silently degrades into a call cap - which is what OpenRouter has
# been doing. An unpriced model is reported as such rather than guessed at.
PRICE_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (15.00, 75.00),
}


def _price_for(model: str) -> Optional[tuple[float, float]]:
    """Look up a model's price, tolerating an OpenRouter vendor prefix."""
    key = (model or "").strip().lower()
    if key in PRICE_USD_PER_MTOK:
        return PRICE_USD_PER_MTOK[key]
    return PRICE_USD_PER_MTOK.get(key.split("/", 1)[-1])


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> tuple[float, str]:
    """(usd, basis). `basis` is what a reader needs to trust the number."""
    price = _price_for(model)
    if price is None:
        return 0.0, "unpriced"
    return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000, "estimated"

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
    # Tools the model *tried* to use, harvested from the CLI's permission denials. It
    # is permanently empty on the HTTP paths, and that is not the same fact as the
    # denylist having held - `isolation` is what distinguishes them.
    tool_attempts: list = field(default_factory=list)
    isolation: str = ""          # "denylist:16" | "no-tools"
    cost_basis: str = ""         # reported | estimated | unpriced
    logical_model: str = ""

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


# --- the provider registry --------------------------------------------------

# One logical model name, resolved per provider. The three namespaces are genuinely
# different: the CLI takes its own short aliases, the Anthropic API takes bare model
# ids, and OpenRouter namespaces them by vendor. Keeping "haiku" as the LOGICAL name is
# deliberate - it resolves to the literal "haiku" for claude_code, so the cache key for
# every existing entry on disk is unchanged.
LOGICAL_MODELS: dict[str, dict[str, str]] = {
    "haiku":  {"claude_code": "haiku",
               "anthropic": "claude-haiku-4-5",
               "openrouter": "anthropic/claude-haiku-4-5"},
    "sonnet": {"claude_code": "sonnet",
               "anthropic": "claude-sonnet-5",
               "openrouter": "anthropic/claude-sonnet-5"},
    "opus":   {"claude_code": "opus",
               "anthropic": "claude-opus-5",
               "openrouter": "anthropic/claude-opus-5"},
}


def resolve_model(name: str, provider: str) -> str:
    """A logical name -> this provider's id for it. Unknown names pass through.

    Passing an unrecognised name straight to the provider is on purpose: it keeps the
    ability to pin an exact snapshot id. Cross-provider comparability is then the
    operator's problem, not ours to guess at.
    """
    entry = LOGICAL_MODELS.get((name or "").strip().lower())
    return entry.get(provider, name) if entry else name


@dataclass(frozen=True)
class ProviderSpec:
    """How to reach one backend, and what it is allowed to hand the model.

    `grants_tools` is False for every provider and is asserted in the test suite. The
    isolation model here is subtractive: the CLI has 16 ambient tools that must be
    denied, and the HTTP APIs have none, so parity is achieved by the APIs sending no
    tools array at all rather than by translating the denylist.
    """
    name: str
    call: object                       # (prompt, model) -> LLMResult
    available: object                  # () -> bool
    why_unavailable: str
    grants_tools: bool = False


def _claude_code_available() -> bool:
    return bool(shutil.which("claude"))


def _anthropic_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def _openrouter_available() -> bool:
    return bool(settings.OPENROUTER_API_KEY)


def _providers() -> dict:
    # Built lazily so the provider functions below are already defined.
    return {p.name: p for p in (
        ProviderSpec("claude_code", _claude_code, _claude_code_available,
                     "the `claude` CLI is not on PATH"),
        ProviderSpec("anthropic", _anthropic, _anthropic_available,
                     "ANTHROPIC_API_KEY is not set"),
        ProviderSpec("openrouter", _openrouter, _openrouter_available,
                     "OPENROUTER_API_KEY is not set"),
    )}


# `claude_code` first, on purpose. Testing runs on a Claude Code entitlement precisely
# to avoid metered spend, so an ANTHROPIC_API_KEY exported for some unrelated tool must
# not silently start charging on the next `analyse --refine`. Spending is opt-in:
# MIW_LLM_PROVIDER=anthropic.
AUTO_ORDER = ("claude_code", "anthropic", "openrouter")


def available_provider() -> str:
    forced = os.getenv("MIW_LLM_PROVIDER", "auto").strip().lower()
    specs = _providers()
    if forced and forced != "auto":
        if forced == "none":
            return "none"
        if forced not in specs:
            # Previously an unrecognised name fell through a ternary straight to
            # OpenRouter, so a typo silently changed backend. Fail loudly instead.
            print(f"  WARNING: MIW_LLM_PROVIDER={forced!r} is not a known provider "
                  f"({', '.join(specs)}); no LLM will be used", file=sys.stderr)
            return "none"
        return forced
    for name in AUTO_ORDER:
        if specs[name].available():
            return name
    return "none"


def provider_status() -> dict:
    """The single source of truth for "is refinement on, and through what".

    Reported to both the CLI banner and the UI, replacing the hardcoded
    `settings.LLM_ENABLED` that claimed no LLM stage existed.
    """
    forced = os.getenv("MIW_LLM_PROVIDER", "auto").strip().lower()
    chosen = available_provider()
    logical = DEFAULT_MODEL
    return {
        "provider": chosen,
        "forced": forced if forced and forced != "auto" else "",
        "model_logical": logical,
        "model_resolved": resolve_model(logical, chosen) if chosen != "none" else "",
        "candidates": [{"name": s.name, "available": bool(s.available()),
                        "why": "" if s.available() else s.why_unavailable}
                       for s in _providers().values()],
    }


def budget_state() -> dict:
    """Today's spend across every stage, not just this process."""
    d = _budget_today()
    return {"calls_today": d["calls"], "max_calls": MAX_CALLS,
            "spent_usd_today": round(d["spent"], 4), "max_spend_usd": MAX_SPEND_USD,
            "this_process_calls": _calls}


def _cache_path(provider: str, model: str, prompt: str) -> Path:
    key = hashlib.sha256(f"{provider}\x1f{model}\x1f{prompt}".encode()).hexdigest()[:32]
    return CACHE_DIR / f"{key}.json"


def _claude_code_argv(prompt: str, model: str) -> list[str]:
    """The exact argv the CLI provider runs. Pure, so the isolation flags are
    inspectable without a `claude` binary on the box - which is what lets a test assert
    that no provider grants a capability another denies.
    """
    return ["claude", "-p", prompt, "--output-format", "json", "--model", model,
            "--disallowedTools", *DENY_TOOLS,
            "--max-turns", str(MAX_TURNS),
            "--exclude-dynamic-system-prompt-sections"]


def _claude_code(prompt: str, model: str) -> LLMResult:
    cmd = _claude_code_argv(prompt, model)
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
                     cost_basis="reported", isolation=f"denylist:{len(DENY_TOOLS)}",
                     raw={k: env.get(k) for k in ("duration_ms", "num_turns", "usage")},
                     tool_attempts=attempted)


def _anthropic(prompt: str, model: str) -> LLMResult:
    """The Messages API path. Every omission below is the parity guarantee.

    No `tools`, no `tool_choice`, no `system`, no `response_format`, no `thinking`, no
    `mcp_servers` - one user message whose content is the prompt byte-for-byte, exactly
    what the CLI receives on `-p`. The API grants no ambient tools, so sending nothing
    is the equivalent of the CLI's 16-name denylist. That makes this path *strictly
    more* isolated than the CLI, not identically isolated: the CLI still injects its own
    reduced system prompt and runs an agent loop up to --max-turns.
    """
    try:
        import anthropic
    except ImportError:
        return LLMResult(error="anthropic package not installed "
                               "(pip install -r requirements-optional.txt)",
                         provider="anthropic", model=model)
    try:
        client = anthropic.Anthropic()
        r = client.messages.create(
            model=model, max_tokens=MAX_OUTPUT_TOKENS, temperature=0,
            messages=[{"role": "user", "content": prompt}])
    except Exception as exc:
        # Never raises: the deterministic note is the product and must still render.
        return LLMResult(error=f"{type(exc).__name__}: {exc}"[:300],
                         provider="anthropic", model=model)

    stop = getattr(r, "stop_reason", "") or ""
    if stop == "refusal":
        return LLMResult(error="model declined to answer", provider="anthropic",
                         model=model)

    blocks = list(getattr(r, "content", None) or [])
    # With no tools declared this cannot happen; if it ever does, the isolation
    # assumption is wrong and that must be loud rather than silently ignored.
    foreign = [b for b in blocks if getattr(b, "type", "text") != "text"]
    if foreign:
        kinds = [getattr(b, "type", "?") for b in foreign]
        print(f"  WARNING: anthropic reply contained non-text block(s) {kinds} though "
              f"no tools were declared; check miw/llm.py _anthropic", file=sys.stderr)

    text = "".join(getattr(b, "text", "") for b in blocks
                   if getattr(b, "type", "text") == "text")
    usage = getattr(r, "usage", None)
    tin = int(getattr(usage, "input_tokens", 0) or 0)
    tout = int(getattr(usage, "output_tokens", 0) or 0)
    cost, basis = _estimate_cost(model, tin, tout)
    res = LLMResult(text=text, ok=bool(text), provider="anthropic", model=model,
                    cost_usd=cost, cost_basis=basis, isolation="no-tools",
                    tool_attempts=[getattr(b, "type", "?") for b in foreign],
                    raw={"stop_reason": stop, "usage": {"input_tokens": tin,
                                                        "output_tokens": tout}})
    if not text:
        res.error = f"empty reply (stop_reason={stop or 'unknown'})"
    elif stop == "max_tokens":
        # Truncated JSON fails the gate and drops silently to the template, so say why.
        print(f"  WARNING: anthropic reply hit max_tokens ({MAX_OUTPUT_TOKENS}); "
              f"the rewrite will likely be rejected as malformed", file=sys.stderr)
    return res


def _openrouter(prompt: str, model: str) -> LLMResult:
    try:
        from openai import OpenAI
    except ImportError:
        return LLMResult(error="openai package not installed", provider="openrouter")
    client = OpenAI(api_key=settings.OPENROUTER_API_KEY,
                    base_url=settings.LLM_BASE_URL)
    try:
        r = client.chat.completions.create(
            model=model, temperature=0, max_tokens=MAX_OUTPUT_TOKENS,
            messages=[{"role": "user", "content": prompt}])
    except Exception as exc:
        return LLMResult(error=f"{type(exc).__name__}: {exc}"[:300], provider="openrouter",
                         model=model)
    usage = getattr(r, "usage", None)
    tin = int(getattr(usage, "prompt_tokens", 0) or 0)
    tout = int(getattr(usage, "completion_tokens", 0) or 0)
    cost, basis = _estimate_cost(model, tin, tout)
    if basis == "unpriced":
        print(f"  WARNING: no price known for {model!r}; the spend cap is acting as a "
              f"call cap for this model", file=sys.stderr)
    return LLMResult(text=r.choices[0].message.content or "", ok=True,
                     provider="openrouter", model=model, cost_usd=cost,
                     cost_basis=basis, isolation="no-tools",
                     raw={"usage": {"input_tokens": tin, "output_tokens": tout}})


def complete(prompt: str, *, model: str = "", use_cache: bool = True) -> LLMResult:
    """One LLM call. Never raises: failures come back on `LLMResult.error`.

    Every caller must behave correctly when `ok` is False — the deterministic path is
    the product, and the model is an enhancement to it.
    """
    global _spent, _calls
    provider = available_provider()
    if provider == "none":
        return LLMResult(error="no LLM provider available", provider="none")

    # One logical name in, this provider's id out. `settings.LLM_MODEL` stays honoured
    # as the OpenRouter-specific override it has always been in practice.
    logical = model or DEFAULT_MODEL
    if not model and provider == "openrouter" and settings.LLM_MODEL:
        model = settings.LLM_MODEL
    else:
        model = resolve_model(logical, provider)

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

    res = _providers()[provider].call(prompt, model)
    res.logical_model = logical
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
