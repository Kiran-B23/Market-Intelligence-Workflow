"""The trust policy is the load-bearing part of this system: it decides what may be
treated as ground truth. These tests pin the behaviour that must not regress.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.schema import Claim, UncitedClaim
from miw.trust import (ClaimKind, Subject, Tier, classify, substantiates)

N8N = Subject("n8n", homepage="https://n8n.io",
              docs_url="https://docs.n8n.io/").with_domains_from_urls()
GROQ = Subject("Groq", homepage="https://groq.com").with_domains_from_urls()


def test_authority_is_relative_to_the_subject():
    """docs.n8n.io is ground truth about n8n and merely a lead about Groq."""
    assert classify("https://docs.n8n.io/x", N8N, ClaimKind.DEPRECATION) is Tier.AUTHORITATIVE
    assert classify("https://docs.n8n.io/x", GROQ, ClaimKind.DEPRECATION) is Tier.LEAD_ONLY


def test_registry_authority_is_limited_to_its_remit():
    assert classify("https://pypi.org/project/gradio/", GROQ, ClaimKind.VERSION) is Tier.AUTHORITATIVE
    assert classify("https://pypi.org/project/gradio/", GROQ, ClaimKind.PRICING) is Tier.CORROBORATING


def test_content_farms_are_excluded_outright():
    for url in ("https://theresanaiforthat.com/x", "https://futurepedia.io/y",
                "https://example.com/best-10-alternatives-2026"):
        assert classify(url, N8N, ClaimKind.EXISTENCE) is Tier.EXCLUDED


def test_unknown_domain_is_a_lead_not_evidence():
    """An unrecognised host is where SEO spam lives; it must never count as evidence."""
    assert classify("https://some-blog.dev/n8n-is-dead", N8N, ClaimKind.EXISTENCE) is Tier.LEAD_ONLY


def test_strict_kinds_require_authoritative_sources():
    for kind in (ClaimKind.EXISTENCE, ClaimKind.DEPRECATION, ClaimKind.PRICING,
                 ClaimKind.VERSION, ClaimKind.IMPLEMENTATION):
        assert substantiates(Tier.AUTHORITATIVE, kind)
        assert not substantiates(Tier.CORROBORATING, kind)
        assert not substantiates(Tier.LEAD_ONLY, kind)


def test_claim_cannot_be_built_without_evidence():
    base = dict(kind=ClaimKind.EXISTENCE, statement="n8n is dead",
                source_url="https://n8n.io/blog", quote="a long enough quote here",
                subject=N8N)
    Claim.build(**base)                                   # the happy path works
    for bad in ({"source_url": ""}, {"quote": "yes"}, {"statement": ""},
                {"source_url": "https://theresanaiforthat.com/n8n"}):
        try:
            Claim.build(**{**base, **bad})
        except UncitedClaim:
            continue
        raise AssertionError(f"expected UncitedClaim for {bad}")


def test_lead_only_claim_never_substantiates():
    c = Claim.build(kind=ClaimKind.DEPRECATION, statement="n8n deprecated node X",
                    source_url="https://random-blog.example/post",
                    quote="they took the node away last month", subject=N8N)
    assert c.tier is Tier.LEAD_ONLY
    assert not c.substantiating


# ------------------------------- user-generated content on an official host

def test_a_forum_on_the_vendors_own_domain_is_not_the_vendor_speaking():
    """`community.n8n.io` is a subdomain of `n8n.io`, so a thread written by any
    passing user classified as AUTHORITATIVE about every n8n node. Once open search
    was enabled that produced 14 critical "deprecated" findings whose evidence
    included other users' questions and pasted JSON workflow dumps.

    Demoted to LEAD_ONLY rather than excluded: a forum thread is a fine pointer to
    something worth checking on the real docs. It just cannot settle anything.
    """
    from miw.schema import Dependency
    from miw.trust import ClaimKind, Tier, classify

    subj = Dependency(kind="n8n_node", canonical_name="n8n-nodes-langchain.agent",
                      official_domains=["n8n.io", "docs.n8n.io"]).subject()

    forum = "https://community.n8n.io/t/deprecated-packages-in-n8n-update/72380"
    assert classify(forum, subj, ClaimKind.DEPRECATION) is Tier.LEAD_ONLY
    # LEAD_ONLY cannot settle a strict kind, so no claim can rest on it.
    from miw.trust import substantiates
    assert not substantiates(Tier.LEAD_ONLY, ClaimKind.DEPRECATION)


def test_the_vendors_actual_docs_are_still_authoritative():
    """The fix must not cost the real evidence."""
    from miw.schema import Dependency
    from miw.trust import ClaimKind, Tier, classify
    subj = Dependency(kind="n8n_node", canonical_name="x",
                      official_domains=["n8n.io", "docs.n8n.io"]).subject()
    for url in ("https://docs.n8n.io/integrations/builtin/deprecated-and-versioned-nodes",
                "https://docs.n8n.io/changelog/release-notes",
                "https://n8n.io/pricing"):
        assert classify(url, subj, ClaimKind.DEPRECATION) is Tier.AUTHORITATIVE, url


def test_forum_shaped_urls_are_caught_by_host_and_by_path():
    from miw.trust import is_user_generated
    for url in ("https://community.example.com/t/thing/1",
                "https://forum.example.com/x",
                "https://discuss.example.com/x",
                "https://example.com/questions/12345/how-do-i",
                "https://example.com/community/threads/abc"):
        assert is_user_generated(url), url
    for url in ("https://docs.example.com/pricing",
                "https://example.com/changelog",
                "https://example.com/docs/deprecations"):
        assert not is_user_generated(url), url


def test_verify_reclassifies_rather_than_trusting_the_stored_tier():
    """`cmd_verify` exists so the guarantee is checkable by someone who did not write
    the code. Reading the tier the artifact records makes it check the artifact against
    itself — and it means a tightened trust rule silently leaves old findings standing.
    That is exactly what happened: 14 stale criticals kept an AUTHORITATIVE stamp on
    forum URLs after forum pages stopped being authoritative.
    """
    import inspect
    import main
    src = inspect.getsource(main.cmd_verify)
    assert 'classify(c["source_url"]' in src, \
        "verify must re-derive the tier from the URL"
    assert "predates a trust rule" in src, "and say so when they disagree"
