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


def _row_harness(rows: list, course: str = "") -> str:
    js = _script()
    words = re.search(r"const WORDS = \{.*?\n\};", js, re.S).group(0)
    esc = re.search(r"const esc = s =>.*?;\n", js, re.S).group(0)
    return "\n".join([
        words,
        "const word = (group, key) => (WORDS[group] || {})[key] || key || '';",
        esc,
        re.search(r"const MONTHS = \[.*?\];", js, re.S).group(0),
        _function(js, "evidenceLine"),
        # The row's own helpers. They are pure - they read their arguments and nothing
        # else - which is the property that lets the row be executed in isolation at
        # all, so they are extracted rather than stubbed.
        _function(js, "decisionChip"),
        _function(js, "vendorDate"),
        _function(js, "findingRow"),
        "const SUMMARY = {run_date:'2026-01-01'};",
        # The all-courses view, where the course name is NOT constant and so is shown.
        # A course page sets this and the row drops it; the tests below cover both.
        f"const CURRENT_COURSE = {json.dumps(course)};",
        f"const rows = {json.dumps(rows)};",
        "let n = 0; for (const r of rows) {",
        "  const h = findingRow(r);",
        "  if (!h.includes('class=\"frow ')) throw new Error('no row: ' + r.finding_id);",
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


def test_the_row_says_what_the_issue_is_and_nothing_more():
    """The row answers WHAT, and stops.

    It used to answer what, what-to-do and where, because the alternative it replaced
    was a row carrying only a name. That went too far the other way: with the same
    treatment on raised findings, a reviewer scanning 82 of these read 82 essays. So
    the instruction, the artifact breakdown and the session numbers moved into the
    panel, and `test_the_panel_says_what_to_do_and_where` is where they are now
    guaranteed. What must NOT come back is a row that says only a name.
    """
    out = json.loads(_run(_row_harness(SHAPES[:1])))
    html = out["sample"]
    assert ">Critical<" in html                           # the level, as a word
    assert 'class="chip critical"' in html                # colour, and a square dot
    assert 'class="frow critical' in html                 # and a rail down the edge
    assert "mcp.composio.dev/dashboard" in html           # the thing we fetched
    assert "404 / 410 Gone" in html                       # what came back
    assert "2 places" in html                             # how many

    # Moved to the panel. Asserted as absent, because leaving them here is the change
    # half-done and every other assertion would still pass.
    assert "Repoint it." not in html, "the fix belongs in the panel"
    assert "2 quiz questions" not in html, "the artifact breakdown belongs in the panel"
    # The severity definition stays: it is what makes the dot mean something, and
    # "A learner hits this today" is the tooltip on it, not the fallout line.
    assert "A learner hits this today" in html


def test_the_level_is_readable_without_hovering():
    """Three bands are shown, not five: `WORDS.severity` maps high and medium both to
    "Major" and low and info both to "Minor".

    The row carried severity as a bare 8px dot while every raised finding was ALSO
    drawn as a card spelling "Critical" out in full. Deleting the card took the only
    readable severity on the page with it, and left a colour whose meaning was
    available only on hover.
    """
    out = json.loads(_run(_row_harness([
        {**SHAPES[0], "severity": s, "local_severity": ""} for s in
        ("critical", "high", "medium", "low", "info")])))
    assert out["n"] == 5
    first = out["sample"]
    assert ">Critical<" in first

    # high and medium are one band and must render identically; a row claiming a
    # fourth level the filter does not offer is a row nobody can navigate by.
    two = json.loads(_run(_row_harness([
        {**SHAPES[0], "severity": "high", "local_severity": ""},
        {**SHAPES[0], "severity": "medium", "local_severity": ""}])))
    assert ">Major<" in two["sample"]


def test_a_row_drops_what_every_other_row_also_says():
    """A value identical on every row is furniture, not information.

    On a course page the course name was printed once per finding - 31 times for 31
    findings - and "Unchanged" 31 times out of 31, together about 18% of the text in
    the list. The course shows only when the list spans more than one, and the status
    only when this run actually moved the finding.
    """
    row = {**SHAPES[0], "diff_class": "unchanged", "courses": ["AI for Finance"]}

    # All-courses view: the course varies between rows, so it earns its place.
    wide = json.loads(_run(_row_harness([row])))["sample"]
    assert "AI for Finance" in wide
    assert ">Unchanged<" not in wide, "unchanged is the default state, not news"

    # Course page: constant by definition.
    scoped = json.loads(_run(_row_harness([row], course="AI for Finance")))["sample"]
    assert "AI for Finance" not in scoped

    # What this run DID move still says so - that is the whole point of the chip.
    movedrow = {**row, "diff_class": "worsened"}
    moved = json.loads(_run(_row_harness([movedrow])))["sample"]
    assert ">Worsened<" in moved


def test_a_decided_row_does_not_look_like_an_untouched_one():
    """A verdict is the one thing about a finding a reviewer cannot re-derive by eye."""
    row = {**SHAPES[0], "_decision": "accepted"}
    html = json.loads(_run(_row_harness([row])))["sample"]
    assert "Confirmed" in html
    assert "decided" in html, "no visual difference between ruled-on and untouched"


def test_only_a_vendors_own_date_reaches_a_row():
    """`due_by` is ours — today plus a severity offset — and 26 rows share one value.

    Rendering it as a date chip would be severity wearing a date's clothes. Only
    `shutdown_on`, which `parse_shutdown` produced from the vendor's own string, is
    allowed to appear, and its tense follows the date.
    """
    past = json.loads(_run(_row_harness(
        [{**SHAPES[0], "shutdown_on": "2026-08-16", "due_by": "2026-10-05"}])))["sample"]
    assert "shut down 16 Aug 2026" in past
    assert "2026-10-05" not in past, "due_by is our deadline, not a fact about the world"

    future = json.loads(_run(_row_harness(
        [{**SHAPES[0], "shutdown_on": "2099-01-02"}])))["sample"]
    assert "shuts down 2 Jan 2099" in future

    # Nothing parseable, nothing shown - never the vendor's raw unparsed string.
    bare = json.loads(_run(_row_harness(
        [{**SHAPES[0], "shutdown_date": "08/16/26", "shutdown_on": ""}])))["sample"]
    assert "08/16/26" not in bare


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


# ----------------------------------------------- the detail panel actually closes
#
# Found by driving the real UI, not by reading it. Opening a finding and then clicking
# any nav item left the panel sitting over the page you had just navigated to. Two
# independent causes, and the second is why the first was invisible:
#
#   1. `route()` never closed the panel. The detail is not part of the route -
#      `openDetail` does not touch the hash - so nothing dismissed it on navigation.
#   2. `closeDetail()` sets `el.hidden = true`, and that did NOTHING to either element.
#      The page's only `[hidden]` rule is `section[hidden]`, while the slide-over is an
#      <aside> whose `.slide` rule sets `display:flex` - which beats the user agent's
#      `[hidden]{display:none}` on specificity. Dismissal rested entirely on
#      `transform:translateX(100%)` coming back when `.open` was removed, so any path
#      that cleared `hidden` while `.open` lingered left a fully visible panel.
#
# Measured in the browser after the fix: `hidden:true, display:"none"` on both the
# panel and the scrim, even with a stale `.open` class still on the element.

def test_navigating_away_closes_the_detail_panel():
    body = _function(_script(), "route")
    assert "closeDetail()" in body, "route() must dismiss an open detail panel"
    assert "DETAIL_RETURN = null" in body, (
        "the return-focus target belongs to the list being replaced")


def test_hidden_actually_hides_the_panel_and_the_scrim():
    """`hidden` has to win, or it is a flag nothing reads."""
    assert ".slide[hidden],.scrim[hidden]{display:none!important}" in PAGE


def test_the_panel_is_not_a_section_so_the_generic_rule_does_not_cover_it():
    """This is the trap: `section[hidden]` exists and looks like it covers everything."""
    import re
    assert "section[hidden]{display:none!important}" in PAGE
    m = re.search(r'<(\w+) class="slide" id="slide"', PAGE) or \
        re.search(r'<(\w+) class="slide"[^>]*id="slide"', PAGE)
    assert m and m.group(1) != "section", (
        "the slide-over is not a <section>, so it needs its own [hidden] rule")


# ---------------------------------------------------------------------------------
# The detail panel, executed.
#
# `card` used to carry the what/why/when triad, the projection banner, the evidence,
# the alternatives and the triage controls; the list showed everything and nothing
# stood out. All of it now renders in `renderDetail`, which until this harness existed
# had no execution coverage at all - only `test_the_page_script_parses`, which proves
# the braces balance and nothing else. A substring test catches a DELETED feature and
# catches nothing else: `${esc(f.someTypo)}` renders the word "undefined" into the
# panel and every grep in test_ui_contract.py still passes.
#
# Driven by real `/api/finding/{id}` payloads rather than hand-made ones, because the
# shapes that break a template literal are the ones nobody thought to write down: an
# empty `alternatives`, a `claims` entry with no quote, a topic gap with no dependency.


def _panel_harness(payloads: list) -> str:
    js = _script()
    words = re.search(r"const WORDS = \{.*?\n\};", js, re.S).group(0)
    esc = re.search(r"const esc = s =>.*?;\n", js, re.S).group(0)
    return "\n".join([
        words,
        "const word = (group, key) => (WORDS[group] || {})[key] || key || '';",
        esc,
        re.search(r"const MONTHS = \[.*?\];", js, re.S).group(0),
        # Stubs for what the panel touches outside itself. Each records rather than
        # acts, so the assertions below can read what the panel produced.
        "const SLOTS = {};",
        "const el = id => ({",
        "  set innerHTML(v) { SLOTS[id] = (SLOTS[id] || '') + v; },",
        "  get innerHTML() { return SLOTS[id] || ''; },",
        "  querySelectorAll: () => [], querySelector: () => null,",
        "  addEventListener: () => {}, set onclick(v) {},",
        "});",
        "const $ = sel => el(sel);",
        "const wireCopy = () => {}; const wireTriage = () => {};",
        "const closeDetail = () => {}; const get = async () => ({});",
        "let DETAIL_ID = '', CURRENT_COURSE = '';",
        "const SUMMARY = {run_date:'2026-01-01'};",
        re.search(r"const NO_EXCERPT = \{.*?\n\};", js, re.S).group(0),
        _function(js, "highlight"),
        _function(js, "idChip"),
        _function(js, "locBlock"),
        _function(js, "decisionChip"),
        _function(js, "renderDetail"),
        f"const payloads = {json.dumps(payloads)};",
        "let n = 0;",
        "for (const d of payloads) {",
        "  for (const k of Object.keys(SLOTS)) delete SLOTS[k];",
        "  CURRENT_COURSE = d.course || '';",
        "  DETAIL_ID = (d.finding || {}).finding_id || '';",
        "  renderDetail(d);",
        "  const all = Object.values(SLOTS).join('');",
        "  const id = DETAIL_ID || '?';",
        "  if (!all.trim()) throw new Error('empty panel: ' + id);",
        "  if (/\\bundefined\\b/.test(all)) throw new Error('undefined in: ' + id);",
        "  if (all.includes('[object Object]')) throw new Error('object in: ' + id);",
        "  if (all.includes('NaN')) throw new Error('NaN in: ' + id);",
        "  n++; }",
        "const last = {};",
        "for (const k of Object.keys(SLOTS)) last[k] = SLOTS[k];",
        "console.log(JSON.stringify({n, slots: last}));",
    ])


def _payloads(limit: int = 0) -> list:
    """Real detail payloads, built through the API rather than hand-written."""
    arts = sorted((ROOT / "out").glob("findings_*.json"))
    if not arts:
        pytest.skip("no findings artifact in this checkout")
    rows = json.loads(arts[-1].read_text()).get("findings") or []
    if not rows:
        pytest.skip("artifact has no findings")
    from fastapi.testclient import TestClient

    from miw.api.app import app

    client = TestClient(app)
    out = []
    for r in (rows[:limit] if limit else rows):
        resp = client.get(f"/api/finding/{r['finding_id']}")
        if resp.status_code == 200:
            out.append(resp.json())
    if not out:
        pytest.skip("no detail payloads resolved")
    return out


def test_the_panel_renders_for_every_finding_in_the_live_artifact():
    """Whatever the pipeline last produced, rendered - not shapes we imagined."""
    payloads = _payloads()
    out = json.loads(_run(_panel_harness(payloads)))
    assert out["n"] == len(payloads)


def test_the_panel_says_what_to_do_and_where():
    """The three things the row stopped saying have to be somewhere, and this is it."""
    d = next((p for p in _payloads() if (p.get("change") or {}).get("what_to_act")), None)
    if d is None:
        pytest.skip("no finding in this artifact carries an action")
    slots = json.loads(_run(_panel_harness([d])))["slots"]
    body = slots.get("#slide-body", "")
    assert d["finding"]["summary"][:40] in body, "what it is"
    assert d["change"]["what_to_act"][:40] in body, "what to do"
    assert "What this affects" in body or "Where it belongs" in body, "where"


def test_the_decision_is_in_the_panel_and_reachable_without_scrolling():
    """It moved off the card, so it has to be here - and pinned, because a finding with
    776 locations would otherwise put Confirm below every one of them."""
    slots = json.loads(_run(_panel_harness(_payloads(1))))["slots"]
    foot = slots.get("#slide-foot", "")
    assert 'data-do="accept"' in foot and 'data-do="reject"' in foot
    assert "also applies to" in foot or "data-id=" in foot
    # In the footer, not at the end of the scrolling body.
    assert 'data-do="accept"' not in slots.get("#slide-body", "")
