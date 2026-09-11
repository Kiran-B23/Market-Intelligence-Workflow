"""The UI's own promises, asserted so a redesign cannot quietly break them.

Two kinds of promise. The first is the offline guarantee the file states about itself:
no font host, no CDN, no external request of any kind, because the app has to work on a
laptop with no network — which is also when a curriculum lead is most likely to be
reading last week's digest.

The second is the set of affordances that keep the page honest. Those were each added
because a specific reading was misleading without them, so they are not decoration and
a restyle must not drop them.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGE = (Path(__file__).resolve().parents[1] / "miw" / "api" / "static"
        / "index.html").read_text()


# ------------------------------------------------------- offline guarantee

def test_the_page_requests_nothing_from_the_network():
    """A webfont, a CDN script or a remote image would all break a no-network run."""
    urls = re.findall(r'''(?:src|href)\s*=\s*["']([^"']+)["']''', PAGE)
    remote = [u for u in urls
              if u.startswith(("http://", "https://", "//"))
              and not u.startswith("http://www.w3.org")]      # SVG namespace only
    assert remote == [], f"external reference(s): {remote}"


def test_no_font_or_stylesheet_is_imported():
    assert "@import" not in PAGE
    assert "fonts.googleapis" not in PAGE and "fonts.gstatic" not in PAGE
    # The only <link> may be the inline data: favicon.
    for href in re.findall(r"<link[^>]*href=[\"']([^\"']+)", PAGE):
        assert href.startswith("data:"), href


def test_styles_and_script_are_inline():
    assert "<style>" in PAGE and "<script>" in PAGE
    assert not re.search(r"<script[^>]+src=", PAGE), "an external script was added"


# ------------------------------------------- the affordances that must survive

def test_severity_is_encoded_by_more_than_colour():
    """Colour alone fails a colour-blind reader, and fails again in a screenshot
    pasted into a chat. Each chip carries the word, a colour and a dot; critical gets
    a square dot so the most urgent state differs in shape too."""
    css = PAGE.split("<style>")[1].split("</style>")[0]
    assert ".chip::before" in css, "no shape marker on severity chips"
    assert ".chip.critical::before" in css, "critical must differ in shape"
    for sev in ("critical", "high", "medium", "low"):
        assert f".chip.{sev}" in css, sev
        assert f".card.{sev}::before" in css, f"no severity rail for {sev}"


def test_the_projection_banner_and_triage_warning_are_still_styled():
    """A course page shows one course's SHARE, and a triage decision applies to every
    course sharing the finding. Both were added because the page was misleading
    without them."""
    css = PAGE.split("<style>")[1].split("</style>")[0]
    assert ".proj{" in css and ".warn{" in css
    assert "16 of" not in css                      # content lives in the JS, not CSS
    assert 'class="proj"' in PAGE or "class=\"proj\"" in PAGE
    assert "also applies to" in PAGE, "the cross-course triage warning is gone"


def test_the_model_opinion_is_still_labelled_as_not_evidence():
    """The one thing in the UI a model asserted rather than a page stated."""
    assert "note_source === 'llm'" in PAGE, "the refined-note chip is gone"
    assert "note refined" in PAGE


def test_every_route_still_has_a_section_to_show():
    for view in ("overview", "watch", "run", "findings", "inventory", "trust", "digest"):
        assert f'id="tab-{view}"' in PAGE, view
    assert "hashchange" in PAGE, "hash routing is what makes a course page linkable"


def test_reduced_motion_is_respected():
    css = PAGE.split("<style>")[1].split("</style>")[0]
    assert "prefers-reduced-motion" in css


def test_both_themes_are_defined_at_token_level():
    """A colour whose only definition sits inside the dark block renders one theme's
    text on the other theme's ground."""
    css = PAGE.split("<style>")[1].split("</style>")[0]
    light = css.split("@media")[0]
    dark = css.split("prefers-color-scheme:dark")[1].split("}}")[0]
    for tok in re.findall(r"(--[a-z0-9-]+):", dark):
        assert f"{tok}:" in light, f"{tok} is only defined in dark mode"


# ------------------------------------------- the detail slide-over's promises

def test_the_detail_panel_is_a_real_dialog_and_can_be_closed_three_ways():
    """A modal that traps a keyboard reader on the list behind it is not usable.

    Escape, the backdrop and an explicit button all close it, and the panel carries
    dialog semantics rather than being a styled div.
    """
    assert 'role="dialog"' in PAGE and 'aria-modal="true"' in PAGE
    assert "aria-labelledby=" in PAGE
    assert "$('#scrim').onclick = closeDetail" in PAGE, "backdrop must close it"
    assert "e.key === 'Escape'" in PAGE, "Escape must close it"
    assert 'aria-label="Close details"' in PAGE, "an explicit close button"


def test_the_standing_list_is_keyboard_reachable():
    """Standing rows are the only list on a no-news re-run, and a div with role=button
    does not get Enter/Space for free."""
    assert 'role="button" tabindex="0"' in PAGE
    assert "e.key !== 'Enter' && e.key !== ' '" in PAGE


def test_an_unresolvable_location_explains_itself_rather_than_rendering_blank():
    """A blank excerpt could mean "nothing there" or "we could not look". Each distinct
    reason the resolver returns must have wording of its own."""
    for reason in ("sheet_declaration", "course_export_missing", "path_not_found",
                   "term_not_in_field", "not_a_text_field", "unparsable_path"):
        assert reason + ":" in PAGE, f"no wording for {reason}"


def test_a_probe_only_finding_is_labelled_not_left_looking_unsourced():
    assert "d.probe_only" in PAGE
    assert "Observed directly by our own probe" in PAGE


def test_the_excerpt_is_escaped_per_segment_so_offsets_stay_valid():
    """Escaping the joined result would shift every span; escaping first would too.

    Course content is not trusted input - it is authored text that can contain angle
    brackets - so the highlighter must escape each slice it emits.
    """
    assert "esc(t.slice(at, a)) + '<mark>' + esc(t.slice(a, a + l))" in PAGE


def test_the_excerpt_scrolls_inside_itself_rather_than_widening_the_page():
    """An embedded n8n workflow is one very long line."""
    m = re.search(r"\.excerpt\{[^}]*\}", PAGE, re.S)
    assert m and "overflow:auto" in m.group(0) and "max-height" in m.group(0)


def test_a_missing_platform_url_pattern_is_explained_not_silently_linkless():
    assert "deep_links_configured" in PAGE
    assert "MIW_PLATFORM_UNIT_URL" in PAGE


# ------------------------------- occurrences are grouped, not a flat wall

def test_occurrences_render_as_collapsible_session_groups():
    """72 occurrences across 12 sessions used to be one flat scroll of 40 cards."""
    assert '<details class="grp"' in PAGE
    assert "d.groups" in PAGE, "groups come from the API, over the full location list"
    assert ".grp > summary" in PAGE, "the group header is styled as a control"


def test_groups_are_collapsed_until_asked_for():
    """Opening a finding must not resolve every excerpt it has."""
    m = re.search(r'<details class="grp"[^>]*>', PAGE)
    assert m and " open" not in m.group(0)
    assert "Loading occurrences…" in PAGE, "the body is a placeholder until expanded"


def test_expanding_a_group_fetches_only_that_session():
    assert "addEventListener('toggle'" in PAGE
    assert "session: el.dataset.group" in PAGE
    assert "el.dataset.loaded" in PAGE, "each group loads at most once"


def test_a_group_that_can_never_show_an_excerpt_says_so_once():
    assert "no excerpt available" in PAGE


def test_the_post_run_refresh_stays_in_course_scope():
    """These three calls dropped `qs()`, so a finished run repainted a course page with
    whole-curriculum numbers."""
    tail = PAGE.split("if (st === 'done')")[1][:600]
    for call in ("/api/summary' + qs()", "/api/findings' + qs()",
                 "/api/agent-findings' + qs()"):
        assert call in tail, call


# ------------------------------- the app shell, charts and theme control

def test_the_shell_is_a_sidebar_plus_a_content_column():
    """It was a sticky header over a sticky tab bar over one centred column, which put
    the per-course run action fifth in a row of five tabs."""
    assert 'class="shell"' in PAGE and 'class="side"' in PAGE
    assert re.search(r"\.shell\{[^}]*grid-template-columns", PAGE)
    assert 'id="sidecourses"' in PAGE and 'id="sideglobal"' in PAGE


def test_the_primary_run_action_lives_in_the_content_header_and_names_the_course():
    assert 'id="hrun"' in PAGE
    assert "Run audit · ${COURSE_TITLE" in PAGE, "the button says which course"


def test_the_sidebar_collapses_on_a_narrow_screen():
    assert re.search(r"@media \(max-width:900px\)\{[\s\S]{0,400}?\.sidetoggle\{display:block\}",
                     PAGE)


def test_a_typeface_is_embedded_rather_than_fetched():
    """A `data:` URI issues no request, so the offline guarantee is unchanged."""
    assert PAGE.count("@font-face") == 3
    assert PAGE.count("data:font/woff2;base64,") == 3
    assert "tools/build_font.py" in PAGE, "the build step is named, so it is reproducible"


def test_the_inert_font_features_are_gone():
    """`cv05`/`ss01` are Inter-specific and did nothing on the system stack."""
    assert '"cv05"' not in PAGE and '"ss01"' not in PAGE
    assert "tabular-nums" in PAGE


def test_severity_distributions_are_drawn_not_concatenated():
    assert 'class="bar"' in PAGE and "function bar(" in PAGE
    assert 'class="chart"' in PAGE and "renderCharts" in PAGE
    assert 'role="img" aria-label=' in PAGE, "a chart states its values for a reader"


def test_there_is_an_elevation_ladder_not_two_shadows():
    for t in ("--e1:", "--e2:", "--e3:", "--e4:"):
        assert t in PAGE, t


def test_the_theme_has_all_three_states_and_a_control():
    """An explicit choice stamps data-theme; "system" stamps nothing, so the media query
    has to be guarded or an explicit light choice loses to a dark OS."""
    assert ':root:not([data-theme="light"])' in PAGE
    assert ':root[data-theme="dark"]{' in PAGE
    assert 'id="themebtn"' in PAGE and "THEME_KEY" in PAGE


def test_adding_a_course_is_reachable_and_explains_the_slug():
    assert 'href="#/add"' in PAGE and 'id="tab-add"' in PAGE
    assert "slug will be" in PAGE, "the derived slug is shown before committing"
    assert "/api/courses/validate" in PAGE, "it checks before it writes"


# ------------------------------- the model chooser

def test_the_model_chooser_lives_in_the_sidebar():
    """It applies to the next run from anywhere, not to one form — and the sidebar was
    already reporting the active provider, so the readout and the control belong
    together rather than in two places that can disagree."""
    assert 'id="sidellm"' in PAGE
    side = PAGE[PAGE.index('<aside class="side"'):PAGE.index("</aside>")]
    assert 'id="llmprov"' in side and 'id="llmmodel"' in side
    assert "/api/llm" in PAGE
    assert "llm_provider:" in PAGE and "llm_model:" in PAGE, "and sends them with the run"


def test_the_chooser_is_not_duplicated():
    """Two controls for one setting is how they come to disagree."""
    assert PAGE.count('id="llmprov"') == 1 and PAGE.count('id="llmmodel"') == 1
    assert 'id="llmbox"' not in PAGE, "the run-form copy is gone"


def test_the_choice_survives_a_reload():
    """The rail survives navigation but not a reload, and a setting that silently
    reverted to `auto` between page loads would be worse than no control."""
    assert "LLM_KEY" in PAGE and "localStorage" in PAGE
    assert "function llmSave()" in PAGE


def test_the_chooser_is_populated_at_startup_not_on_one_view():
    """The rail is always visible, so it cannot wait for the run view."""
    assert "initSidebar();" in PAGE
    i = PAGE.index("initSidebar();")
    assert "loadLLM();" in PAGE[i:i + 200]


def test_the_run_form_says_where_the_control_is():
    assert "section of the" in PAGE and "sidebar, and apply to this run" in PAGE



def test_an_unavailable_provider_is_disabled_and_says_why():
    """`auto` will never reach OpenRouter while the CLI is on PATH, and a key that is
    simply absent should say so rather than fail at run time."""
    assert "disabled" in PAGE and "esc(p.why)" in PAGE


def test_choosing_none_is_offered_as_a_real_option():
    """Template prose is the product; the model is an enhancement to it."""
    assert "template prose only, no calls" in PAGE


def test_the_chooser_states_the_cost_of_the_choice():
    assert "per refined note" in PAGE
    assert "spent_usd_today" in PAGE and "calls_today" in PAGE


def test_an_unpriced_model_warns_that_the_spend_cap_stops_applying():
    """This is the one a bill would otherwise teach you."""
    assert "no price in the table" in PAGE
    assert "only the" in PAGE and "call limit does" in PAGE


def test_it_says_which_stages_make_model_calls():
    """Worded as "no MODEL calls", not "cost nothing".

    The looser wording was wrong by omission: `research` is checked by default and
    spends Tavily searches with no model involved, so a run with every model call
    disabled is still not free. `test_the_form_says_research_spends_tavily_searches`
    covers the other half.
    """
    assert "probe, analyse and report make no model calls at all" in PAGE


def test_the_form_says_research_spends_tavily_searches():
    """`research` is checked by default and calls Tavily even with no model involved —
    `verify_on_official` runs per unanswered claim kind and is not gated behind
    `--nominate`. Measured: ~73 searches for a scoped Intro to Gen AI run."""
    assert "tavilyNote" in PAGE
    assert "spends <b>Tavily</b> searches" in PAGE
    assert "official pages + Tavily" in PAGE, "the stage label says so too"


def test_unchecking_research_updates_that_note():
    assert "addEventListener('change', tavilyNote)" in PAGE


# ------------------------------- the run form has to be findable

def test_the_run_form_is_in_the_sidebar_not_only_the_header_button():
    """It was filtered out on the reasoning that the header button replaced it, which
    left "Run history" — the obvious-looking entry — as a dead end that hides the form,
    and with it the model chooser. Nothing should be reachable by one route only."""
    assert "'New run'" in PAGE
    assert "v !== 'run'" not in PAGE, "the sidebar must not filter the form out"


def test_the_form_and_the_history_are_named_distinguishably():
    """"Runs" vs "Run audit" asked the reader to guess which started one."""
    assert "'Run history'" in PAGE
    assert "'Run audit'" not in PAGE


def test_the_unscoped_form_is_linked_rather_than_only_typeable():
    assert "'New run (any scope)'" in PAGE


def test_the_history_view_offers_a_way_to_start_one():
    assert 'id="newrun"' in PAGE
    assert "$('#newrun').hidden = false" in PAGE


def test_the_active_provider_is_readable_beside_the_control():
    """It was a dead label in the meta strip, then a link to the run form. Both are
    redundant now the control itself sits in the sidebar, and two places reporting one
    fact is how they come to disagree."""
    assert "LLM.active.provider" in PAGE, "the rail names what `auto` resolves to"
    assert "Change the provider or model" not in PAGE, "the old link is gone"
