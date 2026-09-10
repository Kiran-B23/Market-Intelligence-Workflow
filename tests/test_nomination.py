"""A nomination is believed only as far as we can check it ourselves.

The 2026 measurements on deep-research agents are the design brief: 3-13% of cited URLs
are fabricated, citation accuracy runs 40-80%, and larger models hallucinate more
confidently at synthesis. The answer is structural rather than a better prompt — the
model may emit a name and at most a bare domain, our code fetches it, and a claim exists
only if we read the page.

Everything here is offline: `resolver` is a dict standing in for DNS, and `fetcher`
returns canned responses. A fabricated domain is tested by simply not being in the dict.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.research import nominate
from miw.research.nominate import adjudicate, from_search_hit, from_vendor_name
from miw.schema import AlternativeNomination, Dependency, Location

DEP = Dependency(kind="service", canonical_name="CodeToTutorial",
                 official_domains=["codetotutorial.com"],
                 homepage="https://codetotutorial.com",
                 locations=[Location(course="Intro to Gen AI", topic_name="t",
                                     unit_id="u", unit_name="n", content_id="q1",
                                     field_path="f", evidence_source="markdown",
                                     object_type="LEARNING_RESOURCE", session_no=4)])

# A realistic pricing page: `official._relevant` requires a KIND keyword, so wording
# like "free for public repos" alone establishes nothing. Vendors write "free tier".
PRICING_PAGE = (
    "<html><body><p>DeepWiki has a generous free tier for public repositories, and "
    "you can start reading any project with no credit card required.</p>"
    "<p>To index a private repository you will need to sign up and create an account "
    "with your GitHub identity first.</p></body></html>")


def _resolver(known):
    """Stand-in for DNS. Absence from `known` is a fabricated domain."""
    def go(url):
        from miw.net import domain
        return "ok" if domain(url) in known else "unresolvable"
    return go


def _fetcher(pages, status=200, final=None):
    class F:
        def __init__(self, url):
            from miw.net import domain
            self.url = url
            self.status = status
            self.body = pages.get(domain(url), PRICING_PAGE) if status == 200 else ""
            self.final_url = final or url
            self.error = ""
            self.redirects = []
        reachable = property(lambda s: s.status is not None)
        ok = property(lambda s: 200 <= (s.status or 0) < 300)
        gone = property(lambda s: s.status in (404, 410))
        blocked = property(lambda s: s.status in (401, 403, 429))
    return lambda url, **k: F(url)


# ------------------------------------------------------- the model may not cite

def test_a_fabricated_domain_is_refuted_at_dns_before_anything_is_fetched():
    """The 3-13% fabrication defence, and it costs one DNS lookup."""
    calls = []
    def spy(url, **k):
        calls.append(url)
        raise AssertionError("must not fetch a domain that does not resolve")
    nom = AlternativeNomination(name="DeepWikiPro",
                                candidate_domain="deepwikipro.invalid",
                                source="model")
    got, alt = adjudicate(nom, DEP, fetcher=spy, resolver=_resolver(set()))
    assert got.verdict == "refuted_no_such_domain"
    assert got.refuted and alt is None
    assert calls == [], "nothing should have been fetched"


def test_a_name_with_no_domain_is_never_turned_into_a_guessed_url():
    """A guessed `https://<name>.com` that happens to answer is invented evidence —
    it is exactly how a hallucinated tool acquires a citation."""
    calls = []
    nom = AlternativeNomination(name="DeepWikiPro", candidate_domain="", source="model")
    got, alt = adjudicate(nom, DEP,
                          fetcher=lambda url, **k: calls.append(url),
                          resolver=lambda url: "ok")
    assert got.verdict == "unresolved_name_only" and alt is None
    assert calls == [], f"guessed a host: {calls}"


def test_a_full_url_in_the_domain_field_is_rejected_not_repaired():
    for bad in ("https://deepwiki.com", "deepwiki.com/docs", "deep wiki.com",
                "deepwiki.com?q=1"):
        nom = AlternativeNomination(name="DeepWiki", candidate_domain=bad,
                                    source="model")
        got, alt = adjudicate(nom, DEP, fetcher=_fetcher({}),
                              resolver=_resolver({"deepwiki.com"}))
        assert got.verdict == "rejected_malformed_domain", bad
        assert alt is None


# ------------------------------------------- refuted vs blocked vs redirected

def test_a_dead_candidate_is_refuted():
    nom = from_search_hit("https://deepwiki.com/blog", "DeepWiki")
    got, alt = adjudicate(nom, DEP, fetcher=_fetcher({}, status=404),
                          resolver=_resolver({"deepwiki.com"}))
    assert got.verdict == "refuted_dead" and alt is None


def test_a_blocked_candidate_is_not_refuted():
    """Our own blocked request is not evidence of absence. This is the single most
    important line in the ladder: an anti-bot 403 proves the host is alive."""
    nom = from_search_hit("https://deepwiki.com/blog", "DeepWiki")
    got, alt = adjudicate(nom, DEP, fetcher=_fetcher({}, status=403),
                          resolver=_resolver({"deepwiki.com"}))
    assert got.verdict == "unverifiable_blocked"
    assert not got.refuted, "403 must never be reported as refuted"
    assert alt is None


def test_a_redirect_is_a_correction_not_a_failure():
    """A candidate that 301s elsewhere has just told us its real domain."""
    nom = AlternativeNomination(name="DeepWiki", candidate_domain="deepwiki.io",
                                source="search", nominated_by="https://x.example/p")
    got, alt = adjudicate(
        nom, DEP,
        fetcher=_fetcher({"deepwiki.com": PRICING_PAGE},
                         final="https://deepwiki.com/"),
        resolver=_resolver({"deepwiki.io", "deepwiki.com"}))
    assert "redirects to deepwiki.com" in got.verdict_detail
    assert got.verdict == "verified"
    assert alt is not None and alt.homepage == "https://deepwiki.com"


# --------------------------------------------------------------- policy rungs

def test_the_vendors_own_domain_is_never_its_own_replacement():
    nom = AlternativeNomination(name="CodeToTutorial Docs",
                                candidate_domain="codetotutorial.com", source="search")
    got, alt = adjudicate(nom, DEP, fetcher=_fetcher({}),
                          resolver=_resolver({"codetotutorial.com"}))
    assert got.verdict == "rejected_same_vendor" and alt is None


def test_a_generic_protocol_is_never_a_replacement():
    got, alt = adjudicate(from_vendor_name("HTTPS", "https://codetotutorial.com/docs"),
                          DEP, fetcher=_fetcher({}), resolver=_resolver(set()))
    assert got.verdict == "rejected_generic_token" and alt is None


def test_an_excluded_host_is_never_fetched():
    calls = []
    nom = AlternativeNomination(name="Best AI Tools",
                                candidate_domain="geeksforgeeks.org", source="search")
    got, alt = adjudicate(nom, DEP, fetcher=lambda u, **k: calls.append(u),
                          resolver=lambda u: "ok")
    assert got.verdict == "rejected_excluded" and alt is None and calls == []


# --------------------------------------------------------- what it establishes

def test_a_verified_candidate_carries_claims_from_its_own_domain():
    nom = from_search_hit("https://deepwiki.com/blog", "DeepWiki")
    got, alt = adjudicate(nom, DEP, fetcher=_fetcher({"deepwiki.com": PRICING_PAGE}),
                          resolver=_resolver({"deepwiki.com"}))
    assert got.verdict == "verified" and got.usable
    assert alt is not None and alt.verified
    assert all("deepwiki.com" in c.source_url for c in alt.claims)
    assert alt.free_student_path is True, "its own page says free"


def test_a_candidate_whose_site_says_nothing_checkable_is_refuted():
    blank = "<html><body><p>Welcome to our website. Home About Contact</p></body></html>"
    nom = from_search_hit("https://deepwiki.com/blog", "DeepWiki")
    got, alt = adjudicate(nom, DEP, fetcher=_fetcher({"deepwiki.com": blank}),
                          resolver=_resolver({"deepwiki.com"}))
    assert got.verdict == "refuted_no_evidence" and alt is None


def test_a_vendor_named_successor_with_no_domain_is_reportable_but_claims_nothing():
    got, alt = adjudicate(from_vendor_name("DeepWiki", "https://codetotutorial.com/docs"),
                          DEP, fetcher=_fetcher({}), resolver=_resolver(set()))
    assert got.verdict == "vendor_named" and got.usable
    assert alt is not None
    assert alt.homepage == "", "no homepage has been established"
    assert alt.free_student_path is None and alt.signup_required is None


def test_the_nomination_records_which_rung_it_reached():
    """The audit trail: a refutation must be reportable, not a silent drop."""
    nom = AlternativeNomination(name="Ghost", candidate_domain="ghost.invalid",
                                source="model")
    got, _alt = adjudicate(nom, DEP, fetcher=_fetcher({}), resolver=_resolver(set()))
    assert got.verdict_detail and got.checked_at
    assert got.refuted and not got.usable


# ------------------------------------------- the rotation, and where S10 lives

def _healthy(name="Legacy Tool", dom="legacytool.com"):
    from miw.schema import ProbeResult
    dep = Dependency(kind="service", canonical_name=name, official_domains=[dom],
                     homepage=f"https://{dom}", watch_tier="critical",
                     locations=[Location(course="Intro to Gen AI", topic_name="t",
                                         unit_id="u", unit_name="n", content_id="q1",
                                         field_path="f", evidence_source="markdown",
                                         object_type="LEARNING_RESOURCE",
                                         session_no=4)])
    return dep, ProbeResult(dep_id=dep.dep_id, canonical_name=name, status="ok")


def test_the_reason_distinguishes_breakage_from_rotation():
    """S10 means "still works, no longer best", so it is only honest when discovery ran
    on a HEALTHY dependency. Inferring that from which findings exist let a stale
    research artifact - last week's alternatives against this week's clean probe - look
    like a discovery."""
    from miw.research.agent import alternatives_reason
    from miw.schema import ProbeResult

    broken = ProbeResult(dep_id="x", canonical_name="x", status="broken")
    broken.flag("url_gone")
    assert alternatives_reason(broken, False) == "breakage"
    assert alternatives_reason(broken, True) == "breakage", "breakage outranks rotation"

    ok = ProbeResult(dep_id="x", canonical_name="x", status="ok")
    assert alternatives_reason(ok, True) == "rotation"
    assert alternatives_reason(ok, False) == "", "not in the slice: do not go looking"
    assert alternatives_reason(None, True) == "rotation"


def test_a_dependency_outside_the_slice_costs_nothing():
    """The budget guard: 464 dependencies at 1.5s per host is not a weekly run."""
    from unittest.mock import patch
    from miw.research import agent, official, search
    dep, probe = _healthy()
    calls = []
    page = "<html><body><p>Legacy Tool renders documents for your team quickly.</p></body></html>"

    class F:
        ok, final_url, error, status, redirects = True, "", "", 200, []
        reachable, gone, blocked = True, False, False
        def __init__(self, url):
            self.url, self.body = url, page

    from miw.research.search import SearchOutcome
    with patch.object(official, "fetch", lambda url, **k: F(url)), \
         patch.object(search, "verify_on_official",
                      lambda *a, **k: SearchOutcome(disabled=True)), \
         patch.object(search, "discover_alternatives",
                      side_effect=lambda *a, **k: calls.append(a)):
        res = agent.research_dependency(dep, probe, in_discovery_slice=False)
    assert res.discovery_reason == ""
    assert calls == [], "searched for alternatives outside the discovery slice"
    assert res.nominations == []


def test_a_vendor_named_successor_found_on_the_rotation_keeps_its_citation():
    """The claim lives on the Alternative that `gather` built, not on `res.claims`.
    Looking for it in the wrong place returned it unverified, so it was filtered out
    again one layer further on."""
    from unittest.mock import patch
    from miw.research import agent, official, search
    from miw.research.search import SearchOutcome
    dep, probe = _healthy()
    page = ("<html><body><p>Legacy Tool still works for existing projects, but it is "
            "deprecated for new work and we no longer add features. "
            "We recommend BetterTool for anything new.</p></body></html>")

    class F:
        ok, final_url, error, status, redirects = True, "", "", 200, []
        reachable, gone, blocked = True, False, False
        def __init__(self, url):
            self.url, self.body = url, page

    with patch.object(official, "fetch", lambda url, **k: F(url)), \
         patch.object(search, "verify_on_official",
                      lambda *a, **k: SearchOutcome(disabled=True)), \
         patch.object(search, "discover_alternatives",
                      lambda *a, **k: SearchOutcome(disabled=True)):
        res = agent.research_dependency(dep, probe, in_discovery_slice=True)

    assert res.discovery_reason == "rotation"
    assert [(n.name, n.verdict) for n in res.nominations] == [("BetterTool",
                                                               "vendor_named")]
    alt = res.alternatives[0]
    assert alt.verified, "the vendor's citation was lost between gather and the ladder"
    assert alt.claims and "codetotutorial" not in alt.claims[0].source_url


# ------------------------------------------- evidence must be a statement

def test_a_page_title_is_not_evidence():
    """The one alternative a live run verified rested on
    `Pricing | Zite - The AI builder that means business` — 0.40 caps ratio, 10 words,
    a lowercase bigram, so it passed every prose test, and it matched the PRICING
    keyword using the word "Pricing" from its own title. It says nothing about pricing.
    """
    from miw.research.official import _is_prose
    for title in ("Pricing | Zite - The AI builder that means business",
                  "Home » Pricing » Enterprise plans for teams",
                  "Zite — The AI builder that means business",
                  "Start for free"):
        assert not _is_prose(title), title


def test_real_prose_still_passes():
    from miw.research.official import _is_prose
    for prose in (
        "DeepWiki has a generous free tier for public repositories and needs no "
        "credit card.",
        "We are deprecating the v1 endpoint and recommend migrating to v2 before June.",
        # A genuine sentence truncated by the quote cap has no full stop, so length
        # exempts it - otherwise the cap itself would destroy the evidence.
        "Our free plan includes 100 requests per month, and no credit card is required "
        "to begin using the service today",
    ):
        assert _is_prose(prose), prose


# ------------------------------------- search points, it does not testify

def test_a_search_snippet_is_never_quoted_as_evidence():
    """A Tavily snippet was being quoted verbatim as the claim, bypassing `_is_prose`
    and the subject-term check, and a domain-restricted search made it AUTHORITATIVE.
    n8n's generic "Deprecated nodes" index page - nav chrome and all - became a
    critical deprecation finding against seven nodes that are not deprecated.

    Asserted against the source: nothing may build a Claim out of `Hit.snippet`.
    """
    import inspect
    from miw.research import agent
    src = inspect.getsource(agent.research_dependency)
    assert "h.snippet" not in src, "a search snippet is being used as a quote"
    assert "gather_url" in src, "search hits must be fetched and read"


def test_gather_url_reads_the_page_itself_and_applies_the_prose_rules():
    from miw.research import official
    from miw.trust import ClaimKind

    dep = Dependency(kind="service", canonical_name="Thing",
                     official_domains=["thing.com"], homepage="https://thing.com")

    class F:
        ok, final_url, error, status = True, "", "", 200
        def __init__(self, url, body):
            self.url, self.body = url, body

    chrome = ("<html><body><p>Thing Docs</p><p>ForumChangelog</p>"
              "<h1>Deprecated nodes</h1>"
              "<p>This page lists removed nodes and versioned nodes.</p>"
              "</body></html>")
    res = official.gather_url(dep, "https://thing.com/deprecated",
                              ClaimKind.DEPRECATION,
                              fetcher=lambda u, **k: F(u, chrome))
    assert res.claims == [], "nav chrome and a generic index page are not evidence"

    real = ("<html><body><p>Thing v1 is deprecated and will be removed in the next "
            "major release, so migrate your workflows now.</p></body></html>")
    res = official.gather_url(dep, "https://thing.com/deprecated",
                              ClaimKind.DEPRECATION,
                              fetcher=lambda u, **k: F(u, real))
    assert len(res.claims) == 1
    assert "deprecated" in res.claims[0].quote.lower()
    assert res.claims[0].source_url == "https://thing.com/deprecated"


def test_a_question_is_not_an_assertion():
    """FAQ headings match topic keywords perfectly and state nothing.
    `How do text characters and credits work?` was quoted as pricing evidence."""
    from miw.research.official import _is_prose
    for q in ("How do text characters and credits work?",
              "Do you offer volume-based pricing?",
              "What happens when I run out of credits?"):
        assert not _is_prose(q), q
    # The answer to the question is fine.
    assert _is_prose("Yes, we offer volume-based pricing for teams and schools.")


# --------------------------- when the subject term may be relaxed, and when not

def test_the_subject_check_is_relaxed_only_when_the_site_is_the_product():
    """`elevenlabs.io/pricing` is ElevenLabs' pricing — every sentence is about
    ElevenLabs whether or not it repeats the name, and requiring the name gave 21
    keyword matches and zero survivors.

    But `ai.google.dev/pricing` is a PLATFORM page listing many products. Relaxing it
    there immediately attributed "5,000 free search requests (shared across all Gemini
    3.x models)" to `gemini-2.0-flash`, and HuggingFace's per-TB storage pricing to a
    Meta model served by Groq.
    """
    from miw.research.official import _site_is_the_product

    own = [("ElevenLabs", "https://elevenlabs.io/pricing"),
           ("Speaktor", "https://speaktor.com/#pricing"),
           ("Murf.AI", "https://murf.ai/pricing")]
    for name, url in own:
        assert _site_is_the_product(
            Dependency(kind="service", canonical_name=name), url), (name, url)

    hosted = [("gemini-2.0-flash", "https://ai.google.dev/pricing"),
              ("llama-3.3-70b-versatile", "https://huggingface.co/pricing"),
              ("llama-3.3-70b-versatile", "https://console.groq.com/docs/models")]
    for name, url in hosted:
        assert not _site_is_the_product(
            Dependency(kind="model", canonical_name=name), url), (name, url)


def test_a_platform_pricing_page_cannot_speak_for_one_product_on_it():
    """The regression this guards: a sentence about Gemini 3.x must not become
    evidence about gemini-2.0-flash just because both live on ai.google.dev."""
    from miw.research import official
    from miw.trust import ClaimKind

    dep = Dependency(kind="model", canonical_name="gemini-2.0-flash",
                     official_domains=["ai.google.dev"],
                     homepage="https://ai.google.dev")

    class F:
        ok, final_url, error, status = True, "", "", 200
        def __init__(self, url):
            self.url = url
            self.body = ("<html><body><p>5,000 free search requests per month "
                         "(shared across all Gemini 3.x models), then $14 per 1,000 "
                         "requests.</p></body></html>")

    res = official.gather_url(dep, "https://ai.google.dev/pricing",
                              ClaimKind.PRICING, fetcher=lambda u, **k: F(u))
    assert res.claims == [], \
        "a platform page spoke for a product it does not name"


# ------------------------------------------------ search hygiene (D5)

def test_a_preflight_failure_does_not_outlive_the_run():
    """It was process-wide, so one transient 429 silently degraded every later
    dependency in the same run to "no search", and the API's long-lived job worker
    never recovered at all."""
    from miw.research import search
    search._preflight_failed = True
    search.reset_preflight()
    assert search._preflight_failed is False


def test_all_three_discovery_templates_are_used():
    """The `[:2]` slice dropped "tools like {name}" - the phrasing that finds a
    functional peer rather than a comparison listicle."""
    import inspect
    from miw.research import search
    assert len(search.DISCOVER_TEMPLATES) == 3
    src = inspect.getsource(search.discover_alternatives)
    assert "DISCOVER_TEMPLATES[:2]" not in src
    assert "for tpl in DISCOVER_TEMPLATES:" in src


def test_the_search_query_is_narrowed_by_what_the_session_does():
    """`purpose` existed and was never passed, so an open search for "Murf.AI
    alternative" had nothing to distinguish a voice tool from anything else."""
    from miw.research.agent import _search_purpose
    dep = Dependency(kind="service", canonical_name="Murf.AI",
                     locations=[Location(course="AI for Finance", topic_name="t",
                                         unit_id="u",
                                         unit_name="Mastering Audio Generation",
                                         content_id="q", field_path="f",
                                         evidence_source="markdown",
                                         object_type="LEARNING_RESOURCE",
                                         session_no=9)])
    purpose = _search_purpose(dep)
    assert "Audio" in purpose and "Generation" in purpose
    assert "Murf" not in purpose, "the name is already in the query"
    assert "session" not in purpose.lower(), "structural words say nothing"
