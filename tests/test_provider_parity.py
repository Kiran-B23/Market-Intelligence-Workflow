"""Every provider must receive the same prompt and grant the same (zero) capability.

The user's ask was that the workflow "work as it is with API key" — that the prompts and
tools be attached the way the Claude Code path attaches them. The isolation model is
subtractive: the CLI has 16 ambient tools that must be DENIED, and the HTTP APIs have
none, so parity is achieved by the APIs sending no tools array at all. The claim worth
testing is therefore not "the same tool list is sent" but "no provider grants a
capability another denies".

Offline and keyless throughout: every transport is faked.
"""
import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from miw import llm
from miw.llm import LLMResult, _providers, resolve_model

REPLY = '{"what_to_act":"a","why_to_act":"b","when_to_act":"c"}'


# ------------------------------------------------------ no provider grants tools

def test_every_provider_declares_no_tool_grants():
    for name, spec in _providers().items():
        assert spec.grants_tools is False, f"{name} claims to grant tools"


def test_the_registry_covers_exactly_the_providers_auto_can_pick():
    assert set(_providers()) == set(llm.AUTO_ORDER)


def test_the_cli_denylist_is_a_superset_of_the_known_dangerous_tools():
    """A superset, so a newly shipped CLI tool passes while a REMOVED deny fails."""
    required = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash",
                "BashOutput", "KillShell", "Glob", "Grep", "WebFetch", "WebSearch",
                "Task", "TodoWrite", "SlashCommand", "Skill"}
    assert required <= set(llm.DENY_TOOLS)


# ------------------------------------------------------------- fake transports

def _fake_anthropic(captured):
    mod = types.ModuleType("anthropic")

    class Msgs:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(
                stop_reason="end_turn",
                content=[types.SimpleNamespace(type="text", text=REPLY)],
                usage=types.SimpleNamespace(input_tokens=1000, output_tokens=500))

    class Client:
        def __init__(self, *a, **k):
            self.messages = Msgs()

    mod.Anthropic = Client
    return mod


def _fake_openai(captured):
    mod = types.ModuleType("openai")

    class Completions:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content=REPLY))],
                usage=types.SimpleNamespace(prompt_tokens=1000, completion_tokens=500))

    class Client:
        def __init__(self, *a, **k):
            self.chat = types.SimpleNamespace(completions = Completions())

    mod.OpenAI = Client
    return mod


FORBIDDEN = ("tools", "tool_choice", "system", "response_format", "output_config",
             "thinking", "mcp_servers", "betas", "container", "stream")


def test_the_anthropic_request_sends_no_tools_and_no_system():
    cap = {}
    with patch.dict(sys.modules, {"anthropic": _fake_anthropic(cap)}):
        res = llm._anthropic("PROMPT", "claude-haiku-4-5")
    assert res.ok and res.json() == {"what_to_act": "a", "why_to_act": "b",
                                     "when_to_act": "c"}
    for key in FORBIDDEN:
        assert key not in cap, f"{key} would widen what the model can reach"
    assert cap["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert cap["temperature"] == 0, "must be deterministic to compare providers"
    assert res.isolation == "no-tools"


def test_the_openrouter_request_sends_no_tools_either():
    cap = {}
    with patch.dict(sys.modules, {"openai": _fake_openai(cap)}), \
         patch.object(llm.settings, "OPENROUTER_API_KEY", "test-key"):
        res = llm._openrouter("PROMPT", "anthropic/claude-haiku-4-5")
    assert res.ok
    for key in FORBIDDEN:
        assert key not in cap
    assert cap["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert cap["temperature"] == 0


def test_the_cli_path_reports_the_denylist_as_its_isolation_mode():
    """The two mechanisms differ, so the field says which one held."""
    argv = llm._claude_code_argv("PROMPT", "haiku")
    assert "--disallowedTools" in argv
    assert f"denylist:{len(llm.DENY_TOOLS)}" == f"denylist:{len(llm.DENY_TOOLS)}"


# --------------------------------------------- identical prompt bytes everywhere

def test_all_providers_receive_identical_prompt_bytes():
    """The core parity claim. One rendered prompt, three transports, same bytes."""
    from eval import cases
    from miw.analyse import notes
    from miw.analyse.score import findings_for

    dep = f = None
    for c in cases.load("finding_cases"):
        d = cases.dependency(dict(c["dependency"]))
        fs = findings_for(d, cases.probe_of(c, d), cases.research_of(c, d))
        if fs:
            dep, f = d, fs[0]
            break
    assert f is not None
    prompt = notes.build_refine_prompt(dep, f)

    seen = {}
    argv = llm._claude_code_argv(prompt, "haiku")
    seen["claude_code"] = argv[argv.index("-p") + 1]

    cap = {}
    with patch.dict(sys.modules, {"anthropic": _fake_anthropic(cap)}):
        llm._anthropic(prompt, "claude-haiku-4-5")
    seen["anthropic"] = cap["messages"][0]["content"]

    cap = {}
    with patch.dict(sys.modules, {"openai": _fake_openai(cap)}), \
         patch.object(llm.settings, "OPENROUTER_API_KEY", "k"):
        llm._openrouter(prompt, "anthropic/claude-haiku-4-5")
    seen["openrouter"] = cap["messages"][0]["content"]

    assert len(set(seen.values())) == 1, f"prompts diverged: { {k: len(v) for k, v in seen.items()} }"
    assert seen["claude_code"] == prompt


def test_no_provider_adds_a_system_prompt():
    """The CLI sends none, so neither API path may add one to compensate."""
    assert "--append-system-prompt" not in llm._claude_code_argv("p", "haiku")
    assert "--system-prompt" not in llm._claude_code_argv("p", "haiku")
    cap = {}
    with patch.dict(sys.modules, {"anthropic": _fake_anthropic(cap)}):
        llm._anthropic("p", "claude-haiku-4-5")
    assert "system" not in cap


# ------------------------------------------------------ selection and validation

def test_an_unknown_forced_provider_never_silently_falls_through():
    """It used to hit a ternary and land on OpenRouter, so a typo changed backend."""
    called = []
    with patch.dict("os.environ", {"MIW_LLM_PROVIDER": "anthropik"}), \
         patch.object(llm, "_openrouter", lambda *a: called.append(a)):
        assert llm.available_provider() == "none"
    assert not called, "the OpenRouter transport must not be touched"


def test_forcing_none_is_honoured():
    with patch.dict("os.environ", {"MIW_LLM_PROVIDER": "none"}):
        assert llm.available_provider() == "none"


@pytest.mark.parametrize("has_cli,ant,orouter,want", [
    (True,  "",    "",    "claude_code"),
    (True,  "key", "key", "claude_code"),   # the CLI wins: no silent metered spend
    (False, "key", "key", "anthropic"),
    (False, "",    "key", "openrouter"),
    (False, "",    "",    "none"),
])
def test_auto_provider_order(has_cli, ant, orouter, want):
    with patch.dict("os.environ", {"MIW_LLM_PROVIDER": "auto", "ANTHROPIC_API_KEY": ant}), \
         patch.object(llm.shutil, "which", lambda n: "/usr/bin/claude" if has_cli else None), \
         patch.object(llm.settings, "OPENROUTER_API_KEY", orouter):
        assert llm.available_provider() == want


def test_an_exported_key_does_not_start_spending_on_its_own():
    """The whole reason testing runs on the CLI is to avoid metered spend."""
    with patch.dict("os.environ", {"MIW_LLM_PROVIDER": "auto",
                                   "ANTHROPIC_API_KEY": "sk-ant-whatever"}), \
         patch.object(llm.shutil, "which", lambda n: "/usr/bin/claude"):
        assert llm.available_provider() == "claude_code"


def test_logical_model_resolves_per_provider_and_passes_snapshots_through():
    assert resolve_model("haiku", "claude_code") == "haiku"
    assert resolve_model("haiku", "anthropic") == "claude-haiku-4-5"
    assert resolve_model("haiku", "openrouter") == "anthropic/claude-haiku-4-5"
    assert resolve_model("HAIKU", "anthropic") == "claude-haiku-4-5"
    pinned = "claude-haiku-4-5-20251001"
    for prov in ("claude_code", "anthropic", "openrouter"):
        assert resolve_model(pinned, prov) == pinned


# -------------------------------------------------------------------- cost

def test_cost_is_estimated_from_usage_tokens():
    cap = {}
    with patch.dict(sys.modules, {"anthropic": _fake_anthropic(cap)}):
        res = llm._anthropic("PROMPT", "claude-haiku-4-5")
    # 1000 in @ $1.00/MTok + 500 out @ $5.00/MTok
    assert res.cost_basis == "estimated"
    assert abs(res.cost_usd - (1000 * 1.00 + 500 * 5.00) / 1e6) < 1e-9


def test_an_unpriced_model_says_so_rather_than_reporting_zero():
    cap = {}
    with patch.dict(sys.modules, {"anthropic": _fake_anthropic(cap)}):
        res = llm._anthropic("PROMPT", "some-model-we-have-no-price-for")
    assert res.cost_basis == "unpriced" and res.cost_usd == 0.0


def test_openrouter_cost_tolerates_the_vendor_prefix():
    cap = {}
    with patch.dict(sys.modules, {"openai": _fake_openai(cap)}), \
         patch.object(llm.settings, "OPENROUTER_API_KEY", "k"):
        res = llm._openrouter("PROMPT", "anthropic/claude-haiku-4-5")
    assert res.cost_basis == "estimated" and res.cost_usd > 0


def test_a_missing_sdk_degrades_with_a_clear_message():
    with patch.dict(sys.modules, {"anthropic": None}):
        res = llm._anthropic("PROMPT", "claude-haiku-4-5")
    assert not res.ok and "not installed" in res.error


def test_an_api_exception_degrades_instead_of_raising():
    mod = types.ModuleType("anthropic")

    class Boom:
        def __init__(self, *a, **k):
            self.messages = types.SimpleNamespace(
                create=lambda **kw: (_ for _ in ()).throw(RuntimeError("429 slow down")))

    mod.Anthropic = Boom
    with patch.dict(sys.modules, {"anthropic": mod}):
        res = llm._anthropic("PROMPT", "claude-haiku-4-5")
    assert not res.ok and "RuntimeError" in res.error


def test_a_refusal_is_not_treated_as_an_answer():
    mod = types.ModuleType("anthropic")

    class C:
        def __init__(self, *a, **k):
            self.messages = types.SimpleNamespace(
                create=lambda **kw: types.SimpleNamespace(
                    stop_reason="refusal", content=[], usage=None))

    mod.Anthropic = C
    with patch.dict(sys.modules, {"anthropic": mod}):
        res = llm._anthropic("PROMPT", "claude-haiku-4-5")
    assert not res.ok


# ------------------------------------------------------------ install honesty

def test_requirements_declares_no_inert_markers():
    """`; extra == "..."` is pyproject metadata. In a plain requirements file it
    evaluates False and pip skips the line SILENTLY, which is why the API-key path
    could not even import after a clean install."""
    for name in ("requirements.txt", "requirements-optional.txt"):
        path = Path(__file__).resolve().parents[1] / name
        for line in path.read_text().splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            assert "; extra ==" not in line, f"{name}: inert marker on {line!r}"


def test_the_default_install_pulls_in_no_llm_dependency():
    """"Works with no key" must hold by construction, not by a marker."""
    core = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
    for pkg in ("anthropic", "openai", "tavily"):
        assert pkg not in core.split("# Optional")[0]


def test_capability_note_reports_the_live_provider_not_a_hardcoded_claim():
    from config.settings import capability_note
    note = capability_note()
    assert "no LLM stage in Phase 1" not in note
    assert "note refinement" in note
