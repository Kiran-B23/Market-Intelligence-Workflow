"""The seams a second LLM provider slots into, and the invariants it must not break.

Everything here is offline and keyless: no `claude` binary is spawned, no API is
reached. That is the point - the isolation flags and the prompt bytes have to be
inspectable without spending anything, or nobody checks them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw import llm
from miw.analyse import notes
from miw.analyse.score import findings_for
from miw.llm import DENY_TOOLS, _cache_path, _claude_code_argv

# The 14 cache entries on disk were written under this key format. Changing it orphans
# them, and they are the free half of any provider comparison.
PINNED_PROMPT = "PINNED PROMPT FOR THE CACHE-KEY TEST"
PINNED_KEY = "e60c193a63554a319dd7ece099662d0c.json"


def test_claude_code_cache_key_is_unchanged():
    assert _cache_path("claude_code", "haiku", PINNED_PROMPT).name == PINNED_KEY


def test_the_provider_is_part_of_the_cache_key():
    """So switching providers re-fetches instead of replaying the other's answer -
    which is exactly what a comparison needs."""
    a = _cache_path("claude_code", "haiku", PINNED_PROMPT)
    b = _cache_path("anthropic", "haiku", PINNED_PROMPT)
    assert a != b


# ------------------------------------------------------- the isolation argv

def test_claude_code_argv_pins_the_isolation_flags():
    argv = _claude_code_argv("hello", "haiku")
    assert argv[0] == "claude"
    assert "--disallowedTools" in argv
    for tool in ("Read", "Write", "Edit", "Bash", "WebFetch", "Glob", "Grep", "Task"):
        assert tool in argv, f"{tool} must stay denied"
    assert "--max-turns" in argv
    assert "--exclude-dynamic-system-prompt-sections" in argv


def test_claude_code_argv_never_uses_an_allow_list():
    """Verified once and recorded in miw/llm.py: `--allowedTools ""` reads as NO
    restriction, and the model went on to attempt Read and then Bash."""
    assert "--allowedTools" not in _claude_code_argv("hello", "haiku")


def test_claude_code_argv_grants_no_extra_surface():
    argv = _claude_code_argv("hello", "haiku")
    for flag in ("--mcp-config", "--permission-mode", "--settings",
                 "--append-system-prompt", "--system-prompt", "--resume"):
        assert flag not in argv, f"{flag} would widen what the model can reach"


def test_the_prompt_is_passed_as_one_argument_not_interpolated():
    """A prompt carrying vendor text must never be shell-assembled."""
    prompt = 'quote " and $HOME and `id`'
    argv = _claude_code_argv(prompt, "haiku")
    assert prompt in argv, "the prompt must arrive as a single argv element"


def test_deny_list_covers_filesystem_shell_and_network():
    """Grouped by what a hostile vendor page could reach for. The prompt carries
    verbatim third-party text, so Bash in reach turns injection into execution."""
    for group in (("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"),
                  ("Bash", "BashOutput", "KillShell"),
                  ("Glob", "Grep"),
                  ("WebFetch", "WebSearch"),
                  ("Task", "TodoWrite", "SlashCommand", "Skill")):
        for tool in group:
            assert tool in DENY_TOOLS, f"{tool} left the denylist"


# ------------------------------------------------- the extracted prompt/gate

def _case():
    from eval import cases
    for c in cases.load("finding_cases"):
        dep = cases.dependency(dict(c["dependency"]))
        fs = findings_for(dep, cases.probe_of(c, dep), cases.research_of(c, dep))
        if fs:
            return dep, fs[0]
    raise AssertionError("no golden case produced a finding")


def test_build_refine_prompt_is_pure_and_repeatable():
    """Same inputs, same bytes - the precondition for comparing providers at all."""
    dep, f = _case()
    a = notes.build_refine_prompt(dep, f)
    b = notes.build_refine_prompt(dep, f)
    assert a == b and len(a) > 500


def test_the_prompt_labels_fetched_content_as_data():
    dep, f = _case()
    prompt = notes.build_refine_prompt(dep, f)
    assert "<untrusted>" in prompt


def test_the_gate_rejects_an_invented_source_and_says_so():
    dep, f = _case()
    triad, reason = notes.judge_rewrite(dep, f, {
        "what_to_act": "Swap it out, see https://totally-made-up.example/docs",
        "why_to_act": "because", "when_to_act": "now"})
    assert triad is None
    assert "invented a source" in reason


def test_the_gate_rejects_a_missing_part_and_names_it():
    dep, f = _case()
    triad, reason = notes.judge_rewrite(dep, f, {
        "what_to_act": "Do the thing", "why_to_act": "", "when_to_act": "now"})
    assert triad is None and "why_to_act" in reason


def test_the_gate_rejects_a_non_object_reply():
    dep, f = _case()
    assert notes.judge_rewrite(dep, f, ["not", "a", "dict"])[0] is None


def test_the_gate_accepts_a_clean_rewrite_with_no_urls():
    dep, f = _case()
    triad, reason = notes.judge_rewrite(dep, f, {
        "what_to_act": "Replace the taught step in session 4.",
        "why_to_act": "Students hit a dead end.",
        "when_to_act": "Before the next cohort."})
    assert triad is not None and reason == "accepted"
    assert triad[0].startswith("Replace the taught step")


def test_the_gate_does_not_know_which_provider_produced_the_text():
    """It is the single arbiter, and it must stay provider-blind.

    Checks the code, not the prose - the docstring is free to discuss providers.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(notes.judge_rewrite).strip())
    fn = tree.body[0]
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    code = "\n".join(ast.unparse(node) for node in body)
    for word in ("provider", "claude_code", "anthropic", "openrouter"):
        assert word not in code, f"the gate must not branch on {word}"

    # And it takes no provider argument it could branch on.
    assert set(inspect.signature(notes.judge_rewrite).parameters) == {"dep", "f", "data"}


def test_a_rejected_rewrite_leaves_the_deterministic_triad_intact():
    from unittest.mock import patch
    from miw.llm import LLMResult
    dep, f = _case()
    notes.compose(dep, f)
    before = (f.what_to_act, f.why_to_act, f.when_to_act)
    bad = LLMResult(text='{"what_to_act":"see https://evil.example","why_to_act":"x",'
                         '"when_to_act":"y"}', ok=True)
    with patch("miw.analyse.notes.complete", return_value=bad):
        assert notes.refine(dep, f) is False
    assert (f.what_to_act, f.why_to_act, f.when_to_act) == before
    assert f.note_source == "template"
