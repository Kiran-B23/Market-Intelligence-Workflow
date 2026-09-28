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


def test_every_finding_list_renders_through_the_one_row():
    """They had separate markup and drifted, and the copy people actually read was the
    one showing a bare name with no action.

    The raised list was the last holdout: it drew through `card`, a 115-line article
    that put everything the system knew on the list, so nothing stood out. `card` is
    gone and all four lists draw through `findingRow`.
    """
    assert PAGE.count("function findingRow(r)") == 1
    # ONE call site now, inside `pagedGrid`. Raised, settled and the run view all reach
    # the row through it, so the three lists cannot page, grid or render differently
    # from one another by accident.
    assert PAGE.count("map(findingRow)") == 1
    assert PAGE.count("function pagedGrid(") == 1
    for call in ("pagedGrid(FINDINGS, 'raised')", "pagedGrid(settled, 'settled')",
                 "pagedGrid(rows, 'run')"):
        assert PAGE.count(call) == 1, call
    # Not "unused": a second renderer is how the two lists diverged the first time.
    assert "function card(" not in PAGE
    assert "function locLine" not in PAGE, "only card called it"
    # `#held` still renders `.card`, so the class and its severity rails stay - but no
    # findings list may scroll to one.
    assert "#findings .card" not in PAGE
    assert "#findings .frow" in PAGE


def test_a_finding_row_says_what_it_is_and_the_panel_says_what_to_do():
    """The row answers WHAT, the panel answers what-to-do and where.

    The earlier complaint - "I could not understand what mentioned there, what
    suggestion given and where it needs to be implemented" - was about a row carrying
    only a name, and the fix put all three on the row. With the same treatment applied
    to raised findings that became 82 essays in a column, so the fix and the location
    moved into the panel. The guarantee is unchanged: a row still may not be a bare
    name. Only its address moved.
    """
    body = PAGE[PAGE.index("function findingRow(r)"):]
    body = body[:body.index("\n}\n")]
    assert "r.summary" in body, "what it is"
    assert "evidenceLine(r)" in body, "what we observed"
    assert "r.action" not in body, "the fix belongs in the panel"
    assert "Do this" not in body
    assert "r.sessions" not in body, "sessions belong in the panel"

    panel = PAGE[PAGE.index("function renderDetail(d)"):]
    panel = panel[:panel.index("\nfunction ")]
    assert "ch.what_to_act" in panel, "what to do"
    assert "f.summary" in panel, "what it is, for a reviewer who opened it cold"
    assert "What this affects" in panel, "where"
    assert 'data-do="accept"' in panel, "and the decision itself"


def test_an_unresolvable_location_explains_itself_rather_than_rendering_blank():
    """A blank excerpt could mean "nothing there" or "we could not look". Each distinct
    reason the resolver returns must have wording of its own."""
    for reason in ("sheet_declaration", "course_export_missing", "path_not_found",
                   "term_not_in_field", "not_a_text_field", "unparsable_path"):
        assert reason + ":" in PAGE, f"no wording for {reason}"


def test_a_probe_only_finding_is_labelled_not_left_looking_unsourced():
    assert "d.probe_only" in PAGE
    # The wording is now a labelled Probe / Response / Checked block plus the sentence
    # that explains why nothing external is quoted. What the test guards is that a
    # probe-only finding SAYS where it came from rather than rendering as unsourced.
    assert "observation is ours" in PAGE
    assert '<div class="lab">Probe</div>' in PAGE
    assert '<div class="lab">Response</div>' in PAGE


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
    """Naming the course on the button is the guard against starting a run for someone
    else's course from a page that looks like yours."""
    assert 'id="hrun"' in PAGE
    assert "Start check · ${COURSE_TITLE" in PAGE, "the button says which course"
    # The old label must be gone from everything RENDERED. It still appears in two
    # comments that record why it changed, and deleting those would throw away the
    # reason — so this asserts on the markup and the template, not the whole file.
    assert ">Run audit<" not in PAGE, "the button's default label is stale"
    assert "`Run audit" not in PAGE, "a template still builds the old label"


def test_every_page_says_what_it_is_for():
    """A title alone assumes the reader already knows why they are on the page."""
    assert "const PAGE_DESC = {" in PAGE
    for scope in ("overview:", "watch:", "add:", "'global:run'", "'global:runs'"):
        assert scope in PAGE, scope


def test_the_sidebar_has_an_icon_per_entry_and_fetches_none_of_them():
    """Icons are what make a sidebar scannable rather than a list of similar words — and
    they have to be inline, because the page may make no external request."""
    assert "const VIEW_ICON = {" in PAGE
    assert 'class="ic" viewBox="0 0 24 24"' in PAGE
    for sym in ("i-fix", "i-change", "i-inventory", "i-run", "i-history", "i-report",
                "i-home", "i-vendor", "i-trust", "i-course", "i-add", "i-mark"):
        assert f'id="{sym}"' in PAGE, f"sprite is missing {sym}"


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
    # The handler also keeps the three job buttons in step with the boxes, so it is a
    # small arrow function rather than a bare reference - what matters is that moving a
    # stage box recomputes the note.
    assert "tavilyNote(); syncJob();" in PAGE
    assert "document.querySelectorAll('#stages input').forEach(" in PAGE


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
    assert "pagedGrid(rows, 'run')" in PAGE, "and the run view uses that row"


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
    # Keyed on `d.projection`, the value the detail endpoint computes, NOT the
    # finding's own field - that one is empty on the detail payload, so keying on it
    # would leave this branch permanently unreachable with nothing to show for it.
    assert "d.projection === 'topic'" in PAGE
    assert "Where it belongs" in PAGE


def test_the_never_covered_view_is_not_a_findings_list():
    """A long-standing coverage hole did not HAPPEN, so it is not news.

    n8n has always shipped `chainSummarization`; not teaching it is a curriculum
    decision, not a change. It is browsable — 600 rows of it — and must carry no
    severity, no due date and no place in the digest, or the digest cries wolf 600 times.
    """
    assert "['uncovered','Never covered']" in PAGE
    body = PAGE[PAGE.index("async function loadUncovered()"):]
    body = body[:body.index("\n}\n")]
    for word in ("severity", "due_by", "diff_class"):
        assert word not in body, f"the browsable list must not carry {word}"
    assert "not issues" in PAGE, "the page says so in words too"


def test_the_never_covered_table_says_how_a_row_is_related():
    """Every n8n row's sibling is a package-mate, not a newer version of it. A column
    headed "closest thing we teach" implied a closeness that was not there."""
    assert "related to what we teach" in PAGE
    assert "r.relation" in PAGE


def test_every_evidence_source_has_a_plain_word():
    """The chip shows how the tool was found. `n8n_workflow` vs `n8n_mention` is the
    distinction that separates a node a workflow builds with from one a reference table
    merely names, and nine of the 41 taught nodes are the second kind."""
    import json
    import pathlib
    import re

    block = PAGE[PAGE.index("  evidence: {"):PAGE.index("  // Pipeline stages")]
    keys = set(re.findall(r"(?:^|[{,\s])'?([\w:]+)'?\s*:\s*'", block))
    inv = pathlib.Path(__file__).resolve().parents[1] / "out" / "inventory.json"
    if not inv.exists():                      # artifact-free checkout
        emitted = {"n8n_workflow", "n8n_mention", "prose_name", "link:a_href"}
    else:
        emitted = {l["evidence_source"]
                   for d in json.loads(inv.read_text())["dependencies"]
                   for l in d["locations"]}
    assert not (emitted - keys), sorted(emitted - keys)
    # the raw name stays reachable, the same rule the drift codes follow
    assert 'title="${esc(w.evidence_source || \'\')}"' in PAGE


def test_every_drift_code_the_scorer_can_emit_has_a_plain_word():
    """The loop `test_the_drift_codes_carry_their_meaning` left open.

    That test asserts `word('signal', ` exists and spot-checks S11. It does not
    enumerate, so S13 shipped with no entry and `word()`'s fallback rendered the chip as
    the literal string "S13" in five places — the exact jargon the WORDS map exists to
    keep off the page. Nothing failed, because nothing was comparing the map to the
    scorer.

    Modelled on `test_every_evidence_source_has_a_plain_word` below, which closes the
    same loop for evidence kinds against the live inventory.
    """
    import re

    from miw.analyse.score import SIGNALS

    block = PAGE[PAGE.index("  signal: {"):PAGE.index("  /* How the tool was found")]
    have = set(re.findall(r"(?:^|[{,\s])'?(S\d+)'?\s*:", block))
    missing = set(SIGNALS) - have
    assert not missing, f"no plain word for {sorted(missing)}"


def test_every_probe_outcome_that_becomes_a_finding_has_a_plain_word():
    """Same loop for the observation vocabulary.

    `WORDS.outcome` drives the one-line "→ what came back" beside the evidence URL, and
    the Response row of a probe-only detail panel. A probe signal that routes to a
    finding but has no word renders as a bare URL with nothing next to it — which is how
    `taught_field_deprecated` shipped.

    Only signals that can actually reach a reader are required: `PROBE_TO_SIGNAL` is the
    set that becomes a finding. Diagnostic flags (`model_provider_unknown`,
    `n8n_rule_unparsed:*`) never surface and need no prose.
    """
    import re

    from miw.analyse.score import PROBE_TO_SIGNAL

    block = PAGE[PAGE.index("  outcome: {"):PAGE.index("  // How a finding moved")]
    have = set(re.findall(r"(?:^|[{,\s])'?([a-z0-9_]+)'?\s*:\s*'", block))
    missing = set(PROBE_TO_SIGNAL) - have
    assert not missing, f"no plain word for {sorted(missing)}"


def test_the_digest_says_what_it_could_not_check_at_all():
    """Two different silences, and the reader has to be able to tell them apart.

    "Checked and came back uninformative" is a coverage number. "No official domain, so
    nothing can aim anywhere" is a worklist — 161 taught names on the live inventory,
    including `RSS Feed` at 68 locations and `Julius AI` at 39. The second was visible
    only in a summary line inside `verify`, which nobody reads weekly, so a reference-only
    tool could sit unverifiable for months and the digest would look calm.
    """
    from miw.reporters.markdown import render

    out = render([], run_date="2026-09-23", inventory_size=436, probed=277,
                 coverage={"checked": 227, "unchecked": 40, "inconclusive": 10,
                           "no_authority": 161})
    assert "227 were checked against a source" in out
    assert "161 taught name(s) carry no official domain" in out
    assert "needs_domain.yaml" in out
    # ...and it stays quiet when there is nothing to say.
    clean = render([], run_date="2026-09-23", inventory_size=436, probed=277,
                   coverage={"checked": 277, "unchecked": 0, "inconclusive": 0,
                             "no_authority": 0})
    assert "no official domain" not in clean


def test_the_triage_box_is_wired_by_element_not_by_scanning_the_page():
    """`renderAgentRuns` renders a SECOND `.triage` box, for the agent review gate, and
    it has no `[data-do=toggle]`.

    `wireTriage` used to do `document.querySelectorAll('.triage')`, so it reached that
    box too and threw on the next line. Nothing had ever surfaced it because
    `renderFindings` happened to run first. Passing the one element does not narrow the
    selector, it removes it - there is no longer a selector that could match the wrong
    thing. `wireAgentReview` keeps its own loop because it legitimately renders N boxes
    and discriminates by class.
    """
    assert "function wireTriage(box)" in PAGE, (
        "a zero-argument signature is what reintroduces the document-wide scan")
    assert "document.querySelectorAll('.triage')" not in PAGE


def test_a_verdict_reaches_the_list_behind_the_panel():
    """The decision is made in the panel now, and every list behind it goes stale.

    The old marker was `$('#c-' + id)`, the id of a card element that no longer exists -
    it would have thrown on every successful triage. Both halves are required: the DOM
    patch for immediacy, and the DATA patch because the severity filter re-renders both
    lists from arrays captured by `renderFindings`, so a DOM-only fix vanishes the
    moment someone narrows to a band.
    """
    assert "$('#c-'+id)" not in PAGE and "$('#c-' + id)" not in PAGE
    body = PAGE[PAGE.index("function markDecided("):]
    body = body[:body.index("\n}\n")]
    assert "r._decision = verdict" in body, "the data, or a re-render undoes it"
    assert "[data-detail=" in body, "the DOM, or the list only updates on a reload"
    assert "STANDING" in body, "the settled list holds most of the page"


def test_the_panel_head_is_cleared_when_the_next_finding_loads():
    """It used to keep the previous finding's chips while the next one fetched.

    Cosmetic until the footer held a Confirm button: a failed load would then leave a
    live verdict control for finding A under a panel saying finding B could not be
    loaded, and the handler's captured id is A's.
    """
    body = PAGE[PAGE.index("async function openDetail("):]
    body = body[:body.index("\n}\n")]
    assert "$('#slide-head').innerHTML" in body
    assert "$('#slide-foot').innerHTML" in body


def test_our_own_deadline_is_never_drawn_as_a_vendors_date():
    """`due_by` is `today + SEVERITY_OFFSETS[severity]`, so 26 findings share one value.

    On a row it would read as a fact about the world. Only `shutdown_on` - which
    `parse_shutdown` produced from the vendor's own published string - may appear, and
    the parse happens server-side so the browser never becomes a second, disagreeing
    reader of an ambiguous format.
    """
    body = PAGE[PAGE.index("function findingRow(r)"):]
    body = body[:body.index("\n}\n")]
    assert "vendorDate(r.shutdown_on)" in body
    assert "due_by" not in body


def test_the_list_can_be_narrowed_to_one_severity_band():
    """82 findings with no way to ask "what is on fire" means reading all 82."""
    assert "class=\"sevfilter\"" in PAGE
    assert "data-sev=" in PAGE
    # Pressed state is never colour alone.
    assert "aria-pressed=" in PAGE
    body = PAGE[PAGE.index("function renderFindings(data, kind)"):]
    body = body[:body.index("\n}\n")]
    assert "SEV_FILTER" in body
    # Both lists, or the filter lies about what it hid.
    assert "FINDINGS = all.filter(keep)" in body
    assert "STANDING = allSettled.filter(keep)" in body


def test_the_severity_counts_are_of_everything_not_of_what_survived_the_filter():
    """A count that changes when you press it is a count you cannot navigate by."""
    body = PAGE[PAGE.index("function renderFindings(data, kind)"):]
    body = body[:body.index("\n}\n")]
    assert "for (const r of all.concat(allSettled)) counts[band(r)]" in body


def test_the_filter_offers_exactly_the_bands_the_rows_show():
    """Severity is five-valued in the store and three-banded on the page: `high` and
    `medium` both read "Major", `low` and `info` both read "Minor".

    A filter offering a band the rows do not distinguish is a filter that returns a
    surprising set, so one function decides the band for the chip, the rail and the
    control alike.
    """
    assert "function sevBand(sev)" in PAGE
    assert "const SEV_BANDS = ['critical', 'high', 'low'];" in PAGE
    css = PAGE[PAGE.index("<style>"):PAGE.index("</style>")]
    # The rail must group the same way. A `--med` rail under an amber chip is how the
    # two vocabularies drifted apart the first time.
    assert ".frow.high::before,.frow.medium::before{background:var(--high)}" in css
    assert ".frow.medium::before{background:var(--med)}" not in css
    assert ".card.high::before,.card.medium::before{background:var(--high)}" in css


def test_an_empty_list_says_whether_the_filter_emptied_it():
    """"No new or changed ones this week" is false when a filter hid them."""
    body = PAGE[PAGE.index("function renderFindings(data, kind)"):]
    body = body[:body.index("\n}\n")]
    # ...and only when the filter is what emptied it. On most runs nothing new broke,
    # so this list is empty regardless, and blaming the filter there is a plain untruth
    # with the matching findings sitting in the settled list just below.
    assert "SEV_FILTER && all.length" in body
    assert "are at other levels" in body


def test_one_severity_vocabulary_across_the_whole_page():
    """The urgency strip used to print the stored keys - "critical, high, medium, low" -
    beside a list whose rows read "Critical, Major, Minor", and coloured medium blue
    against the list's amber. Two schemes, neither labelled, on one screen.

    Everything a reader sees now goes through `sevBand` and `WORDS.severity`.
    """
    assert "SEV_ORDER.map(k => [k," not in PAGE, "the strip must not label with stored keys"
    assert "SEV_BANDS.map(b => [word('severity', b)" in PAGE
    assert "const SEV_VAR = {critical: 'crit', high: 'high', low: 'low'};" in PAGE
    assert "medium: 'med'" not in PAGE, "medium is amber like high, never its own blue"
    # The headline tile, too. It printed the raw keys in dictionary order.
    assert "Object.entries(sev).map(([k,v]) => `${v} ${k}`)" not in PAGE


def test_the_teaching_prose_is_dismissable_and_shown_by_default():
    """Sentences that teach the page rather than report on it.

    Measured on the AI for Finance findings page: its default state carried 1,412
    characters of surrounding material and not one finding, because the raised list is
    empty on a quiet week and a quiet week is the normal case for a drift watcher.

    They are dismissed, not deleted - several of those sentences exist because a
    reviewer misread something, and the comments record which. Default is SHOWN, so a
    first-time reader gets the explanation without having to discover a control.
    """
    assert 'id="explainbtn"' in PAGE
    assert "html:not(.explain) .teach{display:none !important}" in PAGE
    body = PAGE[PAGE.index("function initExplain()"):]
    body = body[:body.index("\n}\n")]
    # The INITIAL read specifically. The click handler also defaults to '1', so a
    # loose substring check passes with the initial read flipped to '0' - which is the
    # whole bug: a first-time reader never sees the explanation at all.
    assert body.count("?? '1'") == 2, "both reads default to shown"
    assert "let on = '1';" in body
    assert "catch (e)" in body, "private mode throws on localStorage read"
    assert 'aria-pressed' in body, "the state is not carried by colour alone"


def test_the_numbers_are_never_hidden_behind_the_explanation_toggle():
    """Only the sentences go. How fresh the oldest check is decides whether this page
    can be trusted today, and that is not something to make a reader opt into."""
    cov = PAGE[PAGE.index("$('#coverage').innerHTML"):]
    cov = cov[:cov.index(": '';")]
    assert "dependencies tracked" in cov and '<span class="teach">' in cov
    assert cov.index("dependencies tracked") < cov.index('<span class="teach">')
    # The finding count's own breakdown stays: it is the count, not a gloss on it.
    assert "TEACH_SUB = new Set(" in PAGE
    assert "'open findings'" not in PAGE[PAGE.index("TEACH_SUB = new Set("):
                                          PAGE.index("TEACH_SUB = new Set(") + 200]


def test_the_run_stats_are_off_the_triage_path():
    """Six tiles and four charts stood between opening the page and reading a finding.

    Three of those tiles and all four charts describe the RUN - what was probed, what
    came back, how much of the inventory is monitorable at all - not the findings a
    reviewer came to act on.
    """
    assert 'id="rundetail"' in PAGE
    assert 'id="runstrip"' in PAGE
    rd = PAGE[PAGE.index('<details class="rundetail"'):]
    rd = rd[:rd.index("</details>")]
    assert 'id="charts"' in rd, "the charts belong inside the disclosure"
    assert 'id="runstrip"' in rd


def test_a_finding_is_its_own_surface():
    """Card treatment, row layout. A card GRID at 52 findings reads as a zigzag - is the
    one on the right worse than the one below? - which is the wrong question to make a
    reviewer answer about a severity-ordered list."""
    css = PAGE[PAGE.index("<style>"):PAGE.index("</style>")]
    frow = css[css.index("  .frow{"):]
    frow = frow[:frow.index("\n  }")]
    assert "background:var(--surface)" in frow and "border-radius:var(--r)" in frow
    assert "margin-bottom" in frow, "cards need to be separated, not stacked flush"
    # ...and not wrapped in a second box. The settled list used to sit in a `.panel`,
    # which since the rows became surfaces is a box inside a box. (`#runs` still uses
    # that wrapper and should: its contents are not cards.)
    assert '<div class="flist">' in PAGE
    assert ".settled .flist{" in css and ".settled .panel{" not in css


def test_an_unchanged_finding_is_one_click_away_not_two():
    """It used to be two: open the collapsed group, then "Show 27 more".

    Both tiers were built against the same incident - 37 settled findings drowning the
    6 that had just moved - and the collapse alone already solves it, because a closed
    group puts no settled rows above the fold at all. The inner cap only fired after a
    reviewer had explicitly asked to see them.
    """
    body = PAGE[PAGE.index("function renderFindings(data, kind)"):]
    body = body[:body.index("\n}\n")]
    assert "pagedGrid(settled, 'settled')" in body
    assert "settled.slice(" not in body, "no second cap inside the group"
    assert "more unchanged findings" not in PAGE


def test_the_settled_group_opens_when_nothing_was_raised():
    """A quiet week is the normal week for a drift watcher - every course on this run
    raised nothing - and then this group IS the page. Collapsing it hides all of the
    content behind a click; collapsing it when something DID move is the case it was
    built for."""
    body = PAGE[PAGE.index("function renderFindings(data, kind)"):]
    body = body[:body.index("\n}\n")]
    assert "const openSettled = FINDINGS.length === 0;" in body
    assert "${openSettled ? 'open' : ''}" in body


def test_findings_are_a_three_across_grid_of_nine():
    """Three per row, nine to a view, then a numbered pager.

    Every list pages independently, so the key matters: leaving the raised and settled
    lists sharing one page number means opening page 3 of one silently moves the other.
    """
    assert "const PAGE_SIZE = 9;" in PAGE
    css = PAGE[PAGE.index("<style>"):PAGE.index("</style>")]
    assert ".fgrid{" in css
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in css
    # A third of a phone screen is a word per line, so it steps down to 2 then 1.
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in css
    assert "grid-template-columns:minmax(0,1fr)" in css
    body = PAGE[PAGE.index("function pagedGrid(items, key)"):]
    body = body[:body.index("\n}\n")]
    assert "PAGES[key]" in body, "each list holds its own page number"
    assert "Math.min(Math.max(PAGES[key] || 0, 0), pages - 1)" in body, (
        "a stale page number from a longer list must clamp, not render blank")


def test_narrowing_the_list_returns_you_to_its_first_page():
    """Filtering to Critical while on page 4 of 9 would otherwise land on a page that
    no longer exists. The clamp in `pagedGrid` catches it; this resets it properly."""
    body = PAGE[PAGE.index("function renderFindings(data, kind)"):]
    body = body[:body.index("\n}\n")]
    assert body.count("PAGES = {}") >= 2, "reset on both a kind change and a filter"


def test_a_card_does_not_stretch_to_its_longest_neighbour():
    """Cards in a grid row are equal height, so one long line sets all three.

    The S5 summaries carry a redirect target with a query string - 297 characters on
    one finding - and the whole value is in the panel anyway.
    """
    css = PAGE[PAGE.index("<style>"):PAGE.index("</style>")]
    for sel in (".fev{", ".fwhat{"):
        block = css[css.index(sel):]
        block = block[:block.index("\n  }")]
        assert "-webkit-line-clamp:3" in block, sel


def test_the_course_is_chosen_in_one_place():
    """The header carried a course dropdown listing exactly what the sidebar already
    lists as links. Two controls for one choice is one of them going stale - the select
    had to be re-rendered on every route just to keep `selected` honest."""
    assert "renderSwitcher" not in PAGE
    assert 'id="courseSel"' not in PAGE
    assert 'id="switcher"' not in PAGE
    # The sidebar still offers every course and the all-courses view.
    assert '#/c/${esc(c.slug)}/findings' in PAGE or "#/c/" in PAGE
