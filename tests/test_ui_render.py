"""Actually run the page's render functions, rather than grepping for strings.

Every other UI test in this repo asserts that some substring is present in
`index.html`. That catches a deleted feature and catches nothing else: a template
literal with an unbalanced brace, a call to a helper that was renamed, a `.map` on a
value the API sends as a string - all of them pass a substring check and all of them
blank the page. One of them did exactly that (`capabilities` became a string and the
whole boot died), which is why this file exists.

So: extract the pure render helpers, run them under node against the real artifact, and
require that every finding in it renders. Skipped, not failed, where node is absent -
this is a stronger check layered on top of the substring ones, not a new dependency.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = (ROOT / "miw" / "api" / "static" / "index.html").read_text()
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def _script() -> str:
    return re.search(r"<script>(.*?)</script>", PAGE, re.S).group(1)


def _function(js: str, name: str) -> str:
    """One top-level `function name(...) {...}`, by brace matching."""
    i = js.index(f"function {name}(")
    k = js.index("{", i)
    depth = 0
    while True:
        if js[k] == "{":
            depth += 1
        elif js[k] == "}":
            depth -= 1
            if depth == 0:
                return js[i:k + 1]
        k += 1


def _run(body: str) -> str:
    """Through a temp file, not `-e`: the live artifact is 1MB of JSON and an argv that
    size is `OSError: Argument list too long`."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True, timeout=120)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr[-2500:]
    return out.stdout


def test_the_page_script_parses():
    """A syntax error in one template literal blanks the entire application."""
    out = subprocess.run([NODE, "--check", "-"], input=_script(),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2500:]


def _row_harness(rows: list) -> str:
    js = _script()
    words = re.search(r"const WORDS = \{.*?\n\};", js, re.S).group(0)
    esc = re.search(r"const esc = s =>.*?;\n", js, re.S).group(0)
    return "\n".join([
        words,
        "const word = (group, key) => (WORDS[group] || {})[key] || key || '';",
        esc,
        _function(js, "evidenceLine"),
        _function(js, "findingRow"),
        "const SUMMARY = {run_date:'2026-01-01'};",
        f"const rows = {json.dumps(rows)};",
        "let n = 0; for (const r of rows) {",
        "  const h = findingRow(r);",
        "  if (!h.includes('class=\"frow\"')) throw new Error('no row: ' + r.finding_id);",
        "  if (h.includes('undefined')) throw new Error('undefined in: ' + r.finding_id);",
        "  if (h.includes('[object Object]')) throw new Error('object in: ' + r.finding_id);",
        "  n++; }",
        "console.log(JSON.stringify({n, sample: findingRow(rows[0])}));",
    ])


SHAPES = [
    # A dead link with everything populated.
    {"finding_id": "a", "canonical_name": "Composio", "signal": "S1",
     "severity": "critical", "diff_class": "unchanged", "courses": ["A", "B"],
     "sessions": [25], "affects": [{"count": 2, "label": "quiz questions"}],
     "affected_urls": ["https://mcp.composio.dev/dashboard"],
     "probe_signals": ["url_gone"], "summary": "returns 404", "action": "Repoint it."},
    # No URL, but a recorded outcome.
    {"finding_id": "b", "canonical_name": "google-genai", "signal": "S6",
     "severity": "high", "diff_class": "new", "courses": ["A"], "sessions": [3, 4],
     "affects": [{"count": 22, "label": "coding practices"}],
     "probe_signals": ["major_behind_taught_pin"], "action": "Bump it."},
    # Neither: the summary has to carry "what it is".
    {"finding_id": "c", "canonical_name": "A topic", "signal": "S11",
     "severity": "low", "diff_class": "new", "courses": ["A"], "sessions": [9],
     "affects": [], "summary": "No session covers this.", "action": "Add it."},
    # The empty finding. Nothing may throw, and nothing may print `undefined`.
    {"finding_id": "d", "canonical_name": "", "signal": "S4", "severity": "info"},
]


def test_every_row_shape_renders():
    out = json.loads(_run(_row_harness(SHAPES)))
    assert out["n"] == len(SHAPES)


def test_the_row_says_severity_evidence_scope_and_status():
    out = json.loads(_run(_row_harness(SHAPES[:1])))
    html = out["sample"]
    assert 'class="sev critical"' in html                 # severity, as a dot
    assert "mcp.composio.dev/dashboard" in html           # the thing we fetched
    assert "404 / 410 Gone" in html                       # what came back
    assert "2 quiz questions" in html                     # what has to be opened
    assert "2 places" in html                             # how many
    assert ">Unchanged<" in html                          # what this run did to it
    assert "Repoint it." in html                          # the one instruction
    # Severity is a level with its definition on hover, never the raw word alone.
    assert "A learner hits this today" in html


def test_a_row_renders_for_every_finding_in_the_live_artifact():
    """The shapes above are hand-made; this is whatever the pipeline last produced."""
    arts = sorted((ROOT / "out").glob("findings_*.json"))
    if not arts:
        pytest.skip("no findings artifact in this checkout")
    rows = json.loads(arts[-1].read_text()).get("findings") or []
    if not rows:
        pytest.skip("artifact has no findings")
    out = json.loads(_run(_row_harness(rows)))
    assert out["n"] == len(rows)


# --------------------------------------------------------------------- contrast
#
# The palette is warm paper now, and every text token moved with it. A ratio that held
# on the old cool neutrals says nothing about the new ones, and `faint` in particular -
# which carries the 10.5-11px counts and labels - came out of the design kit at 3.13 on
# white, below AA for text that size.

def _luminance(hexcolour: str) -> float:
    h = hexcolour.lstrip("#")
    if len(h) == 3:                        # `--surface:#fff`
        h = "".join(c * 2 for c in h)
    chans = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _tokens() -> dict:
    """The light palette, read off `:root` rather than duplicated here."""
    block = PAGE[PAGE.index("  :root {"):PAGE.index("@media (prefers-color-scheme:dark)")]
    return dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{3,6})\b", block))


# (text token, ground token, what it carries)
SMALL_TEXT = [
    ("ink", "surface", "body"), ("ink", "ground", "body on the page ground"),
    ("soft", "surface", "secondary body"),
    ("muted", "surface", "12px hints"), ("muted", "sunk", "12px hints on a fill"),
    ("faint", "surface", "10.5-11px counts"), ("faint", "sunk", "10.5-11px counts"),
    ("crit-ink", "crit-wash", "critical chips"),
    ("high-ink", "high-wash", "changed chips"),
    ("low-ink", "low-wash", "minor chips"),
    ("accent-ink", "accent-wash", "the candidate block"),
    ("rail-ink", "rail", "the rail"), ("rail-muted", "rail", "rail labels and counts"),
]


@pytest.mark.parametrize("fg,bg,what", SMALL_TEXT)
def test_small_text_clears_aa(fg, bg, what):
    t = _tokens()
    assert fg in t and bg in t, (fg, bg)
    r = _ratio(t[fg], t[bg])
    assert r >= 4.5, f"{what}: --{fg} on --{bg} is {r:.2f}:1"


def test_white_on_the_action_fill_clears_aa():
    """The one filled button on a screen, so it must not be the least readable thing."""
    assert _ratio("#ffffff", _tokens()["accent"]) >= 4.5
