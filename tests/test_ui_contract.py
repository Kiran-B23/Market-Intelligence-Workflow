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
    does not get Enter/Space for free.

    The class is named here as well as the attributes: the click handler is delegated
    on `[data-detail]` and picks up any new row class for free, while this one is not,
    so a new row class is reachable by mouse and invisible to the keyboard until it is
    added. That happened.
    """
    assert 'role="button" tabindex="0"' in PAGE
    assert "e.key !== 'Enter' && e.key !== ' '" in PAGE
    assert ".srow[data-detail], .frow[data-detail]" in PAGE


def test_both_finding_lists_share_one_row():
    """They had separate markup and drifted, and the copy people actually read was the
    one showing a bare name with no action."""
    assert PAGE.count("function findingRow(r)") == 1
    assert PAGE.count("standing.map(findingRow)") == 1
    assert PAGE.count("rows.map(findingRow)") == 1


def test_a_finding_row_says_what_it_is_and_what_to_do():
    """The complaint: "I could not understand what mentioned there, what suggestion
    given and where it needs to be implemented or added." A row that carries only a
    name answers none of the three."""
    body = PAGE[PAGE.index("function findingRow(r)"):]
    body = body[:body.index("\n}\n")]
    assert "r.summary" in body, "what it is"
    assert "r.action" in body, "what to do"
    assert "Do this" in body
    assert "r.sessions" in body, "where"


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
    assert "section of the" in PAGE and "sidebar, and applies to this check" in PAGE



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
    assert "official pages + Tavily" in PAGE, "the step label says so too"


def test_unchecking_research_updates_that_note():
    assert "addEventListener('change', tavilyNote)" in PAGE


# ------------------------------- the run form has to be findable

def test_the_run_form_is_in_the_sidebar_not_only_the_header_button():
    """It was filtered out on the reasoning that the header button replaced it, which
    left "Past checks" — the obvious-looking entry — as a dead end that hides the form,
    and with it the model chooser. Nothing should be reachable by one route only."""
    assert "'Run a check'" in PAGE
    assert "v !== 'run'" not in PAGE, "the sidebar must not filter the form out"


def test_the_form_and_the_history_are_named_distinguishably():
    """"Runs" vs "Run audit" asked the reader to guess which started one.

    The names changed when the page's vocabulary was rewritten for readers outside this
    repo, so this asserts the PROPERTY the old literals stood for: the entry that starts
    a check and the entry that lists past ones must not read as the same thing, and the
    verb-phrase names must be the ones on the page.
    """
    form, history = "'Run a check'", "'Past checks'"
    assert form in PAGE and history in PAGE
    assert form != history
    assert "'Run audit'" not in PAGE, "the old ambiguous label is gone"
    assert "'Run history'" not in PAGE
    # And the form's name must not read as a CONTENT category. "Check for changes" sat
    # in the nav beside "What to fix" and the two read as two kinds of finding, when
    # one of them was a button. Actions are verbs here; lists are nouns.
    assert "'Check for changes'" not in PAGE


def test_the_unscoped_form_is_linked_rather_than_only_typeable():
    """`#/global/run` was reachable but unlinked. It now shares the course form's name,
    so the assertion is that the global nav carries an entry pointing at it."""
    assert "['run','Run a check']" in PAGE


# ------------------------------- fixes and changes are different work

def test_the_two_kinds_of_finding_have_their_own_lists():
    """"Something we teach is now wrong" and "something exists we could teach" are not
    one backlog. The scorer has always recorded which is which and the digest has always
    printed them under separate headings; only the page merged them."""
    assert "['findings','Fixes']" in PAGE
    assert "['changes','Changes']" in PAGE
    assert "const VIEW_KIND = {findings: 'regression', changes: 'opportunity'}" in PAGE


def test_one_map_decides_what_belongs_in_which_list():
    """The nav count and the list it labels must not be able to disagree."""
    assert PAGE.count("VIEW_KIND[v]") >= 1, "the nav count reads it"
    assert "renderFindings(f, VIEW_KIND[view])" in PAGE, "and so does the list"


def test_each_list_says_what_it_is_for():
    """A reader who guesses wrong does the wrong work, so neither list is unlabelled."""
    assert "const KIND_INTRO = {" in PAGE
    for phrase in ("is now wrong", "could teach", "next curriculum cycle"):
        assert phrase in PAGE, phrase


def test_the_standing_list_carries_the_kind_it_is_split_on():
    """On a re-run with no news EVERY finding is standing, so that list IS the page.
    Splitting it on a field the payload does not carry empties it with no error."""
    import json
    import miw.api.app as app
    src = (app.__file__ or "")
    assert '"kind_of_signal": r.get("kind_of_signal", "regression")' in \
        open(src).read()


def test_the_history_view_offers_a_way_to_start_one():
    assert 'id="newrun"' in PAGE
    assert "$('#newrun').hidden = false" in PAGE


def test_the_active_provider_is_readable_beside_the_control():
    """It was a dead label in the meta strip, then a link to the run form. Both are
    redundant now the control itself sits in the sidebar, and two places reporting one
    fact is how they come to disagree."""
    assert "LLM.active.provider" in PAGE, "the rail names what `auto` resolves to"
    assert "Change the provider or model" not in PAGE, "the old link is gone"


# ------------------------------- a run has to show what it found

def test_the_run_view_shows_what_it_found_not_only_its_log():
    """"19 findings raised" in a log line is not something a reviewer can act on."""
    assert 'id="runfindings"' in PAGE
    assert "function renderRunFindings" in PAGE
    assert "What this run found" in PAGE


def test_each_finding_from_a_run_opens_the_detail_panel():
    body = PAGE[PAGE.index("function findingRow(r)"):]
    body = body[:body.index("\n}\n")]
    assert "data-detail=" in body, "reuses the existing slide-over"
    assert 'role="button" tabindex="0"' in body, "and stays keyboard reachable"
    assert "rows.map(findingRow)" in PAGE, "and the run view uses that row"


def test_carried_forward_findings_are_not_passed_off_as_new():
    assert "already open and\n         unchanged" in PAGE or "already open and" in PAGE
    assert "confirmed them" in PAGE


def test_a_run_without_the_analyse_stage_says_why_it_has_no_findings():
    """Empty because nothing was scored is a different fact from empty because nothing
    was wrong, and a reviewer needs to know which."""
    assert "did not include the" in PAGE and "stage, so nothing was scored" in PAGE
    assert "nothing in scope was in a state worth reporting" in PAGE


def test_the_history_row_says_what_the_run_produced():
    assert "findings_count" in PAGE and "worst_severity" in PAGE
    assert "no findings recorded" in PAGE


# ------------------------------- the page reads without a glossary
#
# The complaint that produced these: "the naming in the UI is a bit confusing and I'm
# not able to make others understand". The page's labels and the system's internal
# names had been the same vocabulary, so reading it required knowing the codebase.
# These assert the separation holds — plain words in the labels, real names in the API
# calls — because the pull back towards the internal name is constant.

def test_internal_names_are_translated_through_one_map():
    """One place to edit, and one place to look when a label reads wrong."""
    assert "const WORDS = {" in PAGE
    assert "const word = (group, key) =>" in PAGE
    for group in ("tier:", "diff:", "kind:", "authority:", "signal:", "stage:"):
        assert group in PAGE, group


def test_jargon_is_not_shown_raw():
    """Each of these was on screen with no explanation anywhere on the page."""
    for jargon in ("blast ${", "esc(f.kind_of_signal)", "esc(r.diff_class)",
                   "esc(r.watch_tier)", "esc(String(c.tier || '').toLowerCase())"):
        assert jargon not in PAGE, jargon


def test_the_drift_codes_carry_their_meaning():
    """"S7" is precise and tells a reader nothing. It stays, with the words beside it."""
    assert "word('signal', " in PAGE
    assert "S11: \"topic we don't teach yet\"" in PAGE


def test_api_values_were_not_renamed_with_the_labels():
    """A label is safe to edit; a value is a wire contract.

    The watch tiers reach `Scope.to_cli_args()`, the database and every saved scope, so
    the checkbox VALUES and the filter options must still be the internal names even
    though their text is now plain English.
    """
    assert 'class="tier" value="${esc(t)}"' in PAGE
    for value in ('value="critical"', 'value="standard"', 'value="mention-only"'):
        assert value in PAGE, value
    for stage in ("ingest", "extract", "probe", "research", "analyse", "gaps", "report"):
        assert f'value="{stage}"' in PAGE, stage


def test_the_gaps_step_is_offered_in_the_form():
    """It is the only stage that can produce an S11, and it is off by default because
    it fetches vendor documentation rather than reading the inventory."""
    assert 'value="gaps"' in PAGE
    assert "Look for topics we don't teach yet" in PAGE
    assert 'value="gaps" checked' not in PAGE


def test_a_topic_gap_is_not_described_as_a_stale_dependency():
    """Its dependency is absent from the inventory BY DESIGN, so the "re-run extract"
    warning would be advice that changes nothing."""
    assert "f.projection === 'topic'" in PAGE
    assert "Where it belongs" in PAGE
