"""S11 — curriculum topic gaps.

The two halves are tested separately because they fail separately: a topic can be
correctly detected as absent and then placed in the wrong session, and the second
mistake is the one that loses a reviewer's trust. The fixtures are the real Google and
Microsoft documentation pages as fetched on 11 Sep 2026, so the parsing tests pin
behaviour against markup we actually saw rather than markup we imagined.
"""
from pathlib import Path

import pytest

from miw.analyse.curriculum import CurriculumIndex, SessionDoc
from miw.analyse.gaps import (Area, MIN_CORROBORATION, Source, agreement,
                              corroborate, find_gaps, independent, load_areas,
                              severity_for_coverage, topic_dependency)
from miw.probe.frontier import enumerate_page, normalise
from miw.trust import Tier

FIX = Path(__file__).parent / "fixtures"
GOOGLE = "https://ai.google.dev/gemini-api/docs/prompting-strategies"
MSFT = ("https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/"
        "prompt-engineering")


def _fixture(name: str) -> str:
    return (FIX / name).read_text()


# --------------------------------------------------------------------- the extractor

def test_reads_a_real_docs_page_as_an_enumeration():
    got = enumerate_page(_fixture("google_prompting_strategies.html"),
                         source_url=GOOGLE)
    assert got.supported
    names = [i.name for i in got.items]
    assert "Zero-shot vs few-shot prompts" in names
    assert "Break down prompts into components" in names
    # Every item must be citeable: a bare heading is a label, not evidence.
    assert all(len(i.context) >= 40 for i in got.items)


def test_page_furniture_is_never_an_item():
    """A consent banner rendered as a real heading must not become a curriculum gap.

    It sits in a plain `<div>`, so no tag and no ARIA role removes it — which is why
    `STOP_ITEMS` exists alongside the structural filtering rather than instead of it.
    """
    page = _fixture("anthropic_prompt_engineering.html")
    # Guard against the assertion becoming vacuous if the fixture is ever trimmed
    # again: the banner has to still be IN the page for its absence to mean anything.
    assert "<h3" in page and "Cookie settings" in page
    got = enumerate_page(page, levels=(2, 3))
    names = {i.name.lower() for i in got.items}
    assert "cookie settings" not in names
    assert got.items, "and a real heading on the same page still comes through"


def test_unparseable_page_is_explicit_not_empty():
    """`supported=False` is "this page publishes no enumeration", never a text search.

    The alternative - falling back to searching the page text - is how "we could not
    parse it" silently becomes "the whole area is missing".
    """
    got = enumerate_page("<p>Lots of words about prompting techniques.</p>")
    assert got.supported is False
    assert got.items == []
    assert "no headings" in got.reason


def test_list_items_are_off_by_default():
    """Measured, not assumed: <li> on real docs pages yields "topK" and "Temperature"."""
    html = ("<h2>Techniques</h2><p>" + "x" * 60 + "</p>"
            "<ul><li>Temperature: controls randomness of the sampled output</li></ul>")
    assert "Temperature" not in [i.name for i in enumerate_page(html).items]
    assert "Temperature" in [i.name for i in enumerate_page(html, list_items=True).items]


@pytest.mark.parametrize("a,b", [
    ("Chain-of-Thought", "chain of thought"),
    ("Few-shot prompting", "few shot"),
    ("Self-Consistency (majority vote)", "self consistency"),
])
def test_naming_variants_fold_together(a, b):
    assert normalise(a) == normalise(b)


# --------------------------------------------------------------------- the curriculum

def _session_8() -> SessionDoc:
    """Intro to Gen AI session 8, verbatim from the workbook's Course Outline sheet."""
    return SessionDoc(
        course="Intro to Gen AI", session_no=8,
        session_name="Advanced Prompt Engineering", row=9,
        workbook="Intro to Generative AI - Course Contents.xlsx",
        outline=("Prompting Techniques - Zero-shot, One-shot, Few-shot, "
                 "Chain-of-Thought\nLLM Limitations - Knowledge Cutoff, "
                 "Hallucination, Passive\nReady-to-Use Prompts - Natural Language "
                 "Writer, Anti-Hallucination Prompts"),
        key_takeaways=("Prompting Techniques (Zero-shot, One-shot, Few-shot, CoT)\n"
                       "LLM Limitations\nReady-to-use prompts"))


def _session_5() -> SessionDoc:
    return SessionDoc(
        course="Intro to Gen AI", session_no=5,
        session_name="Prompt Engineering Fundamentals", row=6,
        workbook="Intro to Generative AI - Course Contents.xlsx",
        outline=("What is Prompt?\nPrompt Engineering?\nPrompting Frameworks - RCAFT, "
                 "STAR, RISEN\nRole, Context, Action/Task, Format, Tone\n"
                 "Prompt Templates (separating data from instructions)"),
        key_takeaways="Prompt Engineering\nPrompt Structure\nPrompt Template")


def _session_13() -> SessionDoc:
    return SessionDoc(
        course="Intro to Gen AI", session_no=13, session_name="Mastering Image Generation",
        row=14, workbook="Intro to Generative AI - Course Contents.xlsx",
        outline=("AI Image Generation - definition, applications\nText-to-Image, "
                 "Image-to-Text, Image-to-Image\nDiffusion, GAN, Transformer, VAE"),
        key_takeaways="AI Image Generation\nUnderstanding Working of Diffusion Models")


def _index() -> CurriculumIndex:
    return CurriculumIndex([_session_5(), _session_8(), _session_13()])


def test_a_technique_the_session_already_teaches_is_not_a_gap():
    """The regression that mattered most.

    "Zero-shot vs few-shot prompts" and session 8's "Prompting Techniques (Zero-shot,
    One-shot, Few-shot, CoT)" normalise to DIFFERENT keys, so the key rule alone
    reported a technique the session has taught since day one as missing. Reporting
    that once would cost the digest its authority permanently.
    """
    ix = _index()
    for name in ("Zero-shot vs few-shot prompts", "Chain of thought prompting"):
        hits = ix.teaches_anywhere(normalise(name), ix.topic_terms(name))
        assert [d.session_no for d in hits] == [8], name


def test_coverage_is_decided_across_a_cluster_not_one_name():
    """Whichever vendor phrased it closest to the workbook settles the question.

    Microsoft calls it "Few-shot learning"; that name shares no distinctive word with
    session 8 ("learning" is not in its outline), so on its own it reads as a gap.
    Google calls the same technique "Zero-shot vs few-shot prompts", which session 8
    plainly teaches. Because corroboration has already established that the two names
    are one topic, either name being taught settles it - and that is what stops the
    system reporting few-shot prompting as missing from the session that invented it.
    """
    g, m = _sources()
    rep = find_gaps([_area([g, m])], _index(), fetcher=_fetcher({
        GOOGLE: _Fetch(_fixture("google_prompting_strategies.html")),
        MSFT: _Fetch(_fixture("msft_prompt_engineering.html")),
    }))
    names = {f.canonical_name for f in rep.findings}
    assert not any("shot" in n.lower() for n in names), names
    assert rep.stats.already_taught >= 1


def test_a_genuinely_new_technique_is_absent():
    ix = _index()
    for name in ("Self-consistency", "Break down prompts into components"):
        assert ix.teaches_anywhere(normalise(name), ix.topic_terms(name)) == []


def test_placement_distinguishes_techniques_from_frameworks():
    """Session 5 teaches prompt FRAMEWORKS, session 8 teaches prompt TECHNIQUES.

    Nothing but the sessions' own outline text can tell those apart, which is why the
    workbook is read at all - none of the 24 decks' outlines appear in the JSON export.
    """
    ix = _index()
    terms = ("prompt", "prompting", "prompt engineering", "prompting technique")
    pool = ix.area_sessions(terms, course="Intro to Gen AI")

    technique = ix.place(
        "Self-consistency",
        "Sample several chain-of-thought reasoning paths for the same prompt and take "
        "the majority answer.", pool, area_terms=terms)
    assert technique.session is not None
    assert technique.session.session_no == 8

    framework = ix.place(
        "RISEN framework",
        "A prompt structure giving the model a role, instructions, steps, end goal and "
        "narrowing constraints.", pool, area_terms=terms)
    assert framework.session is not None
    assert framework.session.session_no == 5


def test_placement_declines_rather_than_guess():
    """A topic sharing nothing but the area's vocabulary must not pick a session.

    Without this the winner was decided by session number, which put "Tree of Thoughts"
    in a session about n8n merge nodes.
    """
    ix = _index()
    terms = ("prompt", "prompting")
    p = ix.place("Kubernetes ingress tuning", "Configure a cluster load balancer.",
                 ix.area_sessions(terms), area_terms=terms)
    assert p.session is None
    assert "judgement call" in p.reason


def test_placement_points_at_a_real_workbook_cell():
    """The finding has to be checkable: `field_path` is resolved by extract/locate."""
    doc = _session_8()
    assert doc.field_path == (
        "Intro to Generative AI - Course Contents.xlsx::Course Outline::Outline::row9")


# -------------------------------------------------------------------- corroboration

def _sources():
    g = Source(url=GOOGLE, subject="Google Gemini API",
               official_domains=("ai.google.dev",), exclude=frozenset())
    m = Source(url=MSFT, subject="Microsoft Azure AI Foundry",
               official_domains=("learn.microsoft.com",), exclude=frozenset())
    return g, m


def test_independence_is_measured_on_authority_not_urls():
    g, m = _sources()
    assert independent(g, m)
    other_google = Source(url="https://ai.google.dev/gemini-api/docs/embeddings",
                          subject="Google Gemini API",
                          official_domains=("ai.google.dev",))
    # Two pages of the same vendor are the vendor agreeing with itself.
    assert not independent(g, other_google)


def test_two_real_vendor_pages_corroborate_real_techniques():
    g, m = _sources()
    gl = enumerate_page(_fixture("google_prompting_strategies.html"), source_url=GOOGLE)
    ml = enumerate_page(_fixture("msft_prompt_engineering.html"), source_url=MSFT)
    clusters = corroborate([(g, gl.items), (m, ml.items)])
    names = {c.name for c in clusters}
    # Pairs measured at or above the threshold on the real pages.
    assert "Start with clear instructions" in names
    assert "Break the task down" in names
    # And the API mechanics that a single vendor documents alone are not candidates.
    assert not any("iteration" in n.lower() for n in names)
    # Every cluster spans two independent sources.
    assert all(len({s.subject for s, _i in c.members}) >= 2 for c in clusters)


def test_a_pair_is_not_reported_twice():
    """Discovered once from each side; keyed on the member set so it collapses."""
    g, m = _sources()
    gl = enumerate_page(_fixture("google_prompting_strategies.html"), source_url=GOOGLE)
    ml = enumerate_page(_fixture("msft_prompt_engineering.html"), source_url=MSFT)
    clusters = corroborate([(g, gl.items), (m, ml.items)])
    assert len(clusters) == len({c.key_set() for c in clusters})


def test_agreement_threshold_is_the_precision_rule():
    assert agreement("Start with clear instructions",
                     "Clear and specific instructions") >= MIN_CORROBORATION
    assert agreement("Add context", "Add clear syntax") < MIN_CORROBORATION


# ------------------------------------------------------------------------ end to end

class _Fetch:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    @property
    def ok(self):
        return 200 <= self.status < 300


def _fetcher(mapping):
    return lambda url, **kw: mapping.get(url) or _Fetch("", status=404)


def _area(sources):
    return Area(area_id="prompting-techniques", title="Prompting techniques",
                scope_terms=("prompt", "prompting", "prompt engineering",
                             "prompting technique"),
                sources=tuple(sources))


def test_end_to_end_raises_a_placed_and_cited_gap():
    g, m = _sources()
    rep = find_gaps([_area([g, m])], _index(), fetcher=_fetcher({
        GOOGLE: _Fetch(_fixture("google_prompting_strategies.html")),
        MSFT: _Fetch(_fixture("msft_prompt_engineering.html")),
    }))
    assert rep.findings
    for f in rep.findings:
        assert f.signal == "S11"
        assert f.kind_of_signal == "opportunity"
        assert f.is_substantiated
        # Two citations, on two different hosts — the invariant `main.py verify` asserts.
        hosts = {c.source_url.split("/")[2] for c in f.claims}
        assert len(hosts) >= 2, f.canonical_name
        assert all(c.tier is Tier.AUTHORITATIVE for c in f.claims)
        # The action names a session, not "review course coverage".
        assert "session" in f.recommendation.lower()
        assert f.what_to_act and f.why_to_act and f.when_to_act and f.due_by


def test_one_source_reports_nothing_and_says_why():
    """"No second opinion" and "the area is complete" are different answers."""
    g, _m = _sources()
    rep = find_gaps([_area([g])], _index(), fetcher=_fetcher({
        GOOGLE: _Fetch(_fixture("google_prompting_strategies.html")),
    }))
    assert rep.findings == []
    assert any("two INDEPENDENT" in r for r in rep.stats.uncorroborated_areas)


def test_an_unreadable_source_is_not_a_gap():
    g, m = _sources()
    rep = find_gaps([_area([g, m])], _index(), fetcher=_fetcher({}))
    assert rep.findings == []
    assert rep.stats.sources_read == 0
    assert all("NOT a gap" in r for r in rep.stats.sources_unsupported)


def test_a_lead_only_source_can_never_raise_a_gap():
    """EXISTENCE is a STRICT claim kind; a non-official page cannot substantiate it."""
    g, _m = _sources()
    blog = Source(url="https://example-newsletter.example/techniques",
                  subject="Some Newsletter", official_domains=())
    rep = find_gaps([_area([g, blog])], _index(), fetcher=_fetcher({
        GOOGLE: _Fetch(_fixture("google_prompting_strategies.html")),
        blog.url: _Fetch(_fixture("msft_prompt_engineering.html")),
    }))
    assert rep.findings == []
    assert any("AUTHORITATIVE" in r for r in rep.stats.uncitable)


def test_a_topic_is_never_written_into_the_inventory():
    """The inventory records what the curriculum USES. A topic we do not teach is not.

    Adding one would corrupt the dependency count the whole system reports on, so the
    synthetic dependency exists only in memory and carries a `topic:` id that cannot
    collide with a real one.
    """
    g, _m = _sources()
    dep = topic_dependency(_area([g]), "Self-consistency", g)
    assert dep.kind == "topic"
    assert dep.dep_id == "topic:prompting-techniques:self-consistency"
    assert dep.official_domains == ["ai.google.dev"]


@pytest.mark.parametrize("missing,total,expect", [
    (9, 10, "medium"), (4, 10, "low"), (1, 10, "info"), (0, 0, "info"),
])
def test_severity_follows_how_much_of_the_area_is_missing(missing, total, expect):
    assert severity_for_coverage(missing, total) == expect


def test_shipped_registry_declares_two_sources_per_area():
    """An area with one source can report nothing, so shipping one is a dead entry."""
    for area in load_areas():
        assert len(area.sources) >= 2, area.area_id
        domains = [set(s.official_domains) for s in area.sources]
        assert any(not (a & b) for i, a in enumerate(domains)
                   for b in domains[i + 1:]), area.area_id


# ------------------------------------------------------------------------- the digest

def _gap_finding() -> "object":
    from miw.schema import Claim, Finding, Location
    from miw.trust import ClaimKind, Tier
    f = Finding(dep_id="topic:prompting-techniques:self-consistency",
                canonical_name="Self-consistency", signal="S11",
                signal_label="Curriculum topic gap", severity="medium",
                kind_of_signal="opportunity", diff_class="new",
                summary="Self-consistency is documented by two vendors and appears in "
                        "no session's outline",
                courses=["Intro to Gen AI"])
    f.locations = [Location(course="Intro to Gen AI", topic_name="Prompting techniques",
                            unit_id="", unit_name="Advanced Prompt Engineering",
                            content_id="session-8",
                            field_path="book.xlsx::Course Outline::Outline::row9",
                            evidence_source="markdown", object_type="SESSION_PPT",
                            session_no=8)]
    f.claims = [
        Claim(kind=ClaimKind.EXISTENCE, statement="documented by A",
              source_url="https://ai.google.dev/x", quote="a" * 40, tier=Tier.AUTHORITATIVE),
        Claim(kind=ClaimKind.EXISTENCE, statement="documented by B",
              source_url="https://learn.microsoft.com/y", quote="b" * 40,
              tier=Tier.AUTHORITATIVE),
    ]
    from miw.analyse import notes
    from miw.schema import Dependency
    notes.compose(Dependency(kind="topic", canonical_name="Self-consistency"), f)
    f.recommendation = ("Add Self-consistency to Intro to Gen AI session 8 "
                        "(Advanced Prompt Engineering).")
    f.what_to_act = f.recommendation
    return f


def test_the_digest_describes_a_gap_as_a_gap():
    """S10 and S11 are both "opportunities" and are not the same claim.

    "Still right, no longer best" is true of a tool that has been overtaken and says
    nothing at all about a topic missing from the outline, so the two get their own
    sentences in the header.
    """
    from miw.reporters.markdown import render
    md = render([_gap_finding()], run_date="2026-09-11", inventory_size=1,
                probed=1, researched=1)
    assert "topic gap(s)" in md
    assert "no longer best" not in md


def test_the_digest_omits_blast_radius_for_a_gap():
    """It is a weighted count of where a dependency is USED, so a gap's is always 0."""
    from miw.reporters.markdown import render
    md = render([_gap_finding()], run_date="2026-09-11", inventory_size=1,
                probed=1, researched=1)
    assert "blast radius" not in md


def test_the_reason_claims_only_what_the_evidence_shows():
    """The old wording asserted what employers expect, which no claim substantiates."""
    from miw.analyse.notes import WHY
    assert "employers" not in WHY["S11"]
    assert "outline" in WHY["S11"]


def test_a_scoped_run_must_not_narrow_a_topic_that_spans_courses():
    """Scope chooses what to look at; it never changes what a finding is allowed to say.

    A topic gap's identity is the topic, and one row carries the sessions it belongs in
    across every course. Placing only within the scoped course produced a narrower row
    that `merge_by_dep` then wrote over the global one, silently deleting the other
    courses' placements — the same shape as the bug where a course page repainted with
    whole-curriculum numbers, pointing the other way.

    So `cmd_gaps` always places across every course and filters the RESULT. This pins
    the property that makes that safe: the placements a topic gets do not depend on
    which course asked.
    """
    g, m = _sources()
    # A second course whose prompting session is as strong an area match as the
    # first's, so a topic genuinely belongs in both. A weaker one is excluded by
    # `RELATIVE_AREA_FLOOR` and the test would pass for the wrong reason.
    other = _session_5()
    other.course, other.workbook, other.row = "Other Course", "other.xlsx", 4
    ix = CurriculumIndex([_session_5(), _session_8(), _session_13(), other])
    pages = {GOOGLE: _Fetch(_fixture("google_prompting_strategies.html")),
             MSFT: _Fetch(_fixture("msft_prompt_engineering.html"))}
    everywhere = find_gaps([_area([g, m])], ix, fetcher=_fetcher(pages))
    placed = {f.canonical_name: sorted(f.courses) for f in everywhere.findings}
    assert placed, "the fixture pages must produce at least one gap"
    assert any(len(v) > 1 for v in placed.values()), \
        "at least one topic must span both courses for this test to mean anything"

    scoped = find_gaps([_area([g, m])], ix, fetcher=_fetcher(pages),
                       courses=["Intro to Gen AI"])
    kept = {f.canonical_name: sorted(f.courses) for f in scoped.findings
            if "Intro to Gen AI" in f.courses}
    for name, courses in kept.items():
        # `find_gaps(courses=…)` narrows the pool, which is exactly why `cmd_gaps` does
        # not pass a scope to it. Assert the difference is real, so the day someone
        # "simplifies" `cmd_gaps` by passing the scope through, this fails loudly.
        assert set(courses) <= set(placed[name])
    assert any(set(kept.get(n, [])) != set(v) for n, v in placed.items() if len(v) > 1), \
        ("scoping find_gaps DOES narrow placements — which is why cmd_gaps places "
         "globally and filters afterwards")
