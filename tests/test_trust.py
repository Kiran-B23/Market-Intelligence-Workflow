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
