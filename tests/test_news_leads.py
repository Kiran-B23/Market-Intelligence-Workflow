"""News points; it does not testify.

`research/launch.py` reads the same feeds and deliberately stops at a browsable list,
because judging whether an unknown product belongs in a session is an opinion about a
LEAD_ONLY source. That refusal is untouched. The question here is narrower: has anything
been said about a dependency the curriculum already depends on — and if so, read that
vendor's own pages this week rather than in four, which is what the rotation would cost.

The tests that matter are the ones asserting what a headline can NOT do.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.research.news import dependencies_in_the_news
from miw.schema import Dependency


@dataclass
class _Fetch:
    body: str
    ok: bool = True
    status: int = 200


def _feed(*titles):
    import json
    hits = [{"title": t, "url": "https://news.test/x"} for t in titles]
    return lambda url, **k: _Fetch(json.dumps({"hits": hits}))


def _dep(name, **kw):
    kw.setdefault("homepage", f"https://{name.lower().replace(' ', '')}.test")
    kw.setdefault("official_domains", [f"{name.lower().replace(' ', '')}.test"])
    return Dependency(kind="service", canonical_name=name, **kw)


def test_a_headline_naming_a_tracked_tool_is_a_reason_to_look():
    deps = [_dep("Composio"), _dep("Tavily")]
    got = dependencies_in_the_news(deps, feeds=[("HN", "u")],
                                   fetcher=_feed("Composio raises a Series A"))
    assert [v["name"] for v in got.values()] == ["Composio"]
    assert got[deps[0].dep_id]["headline"].startswith("Composio")


def test_MUST_NOT_match_a_name_that_is_also_an_ordinary_word():
    """`Whisper`, `Gamma`, `Wait` and `Cursor` are all real dependency names here.

    Matching them against a news feed matches every article ever written, and the cost
    is not a bad row in a list — it is the research budget spent on the wrong vendor
    while the right one waits another four weeks.
    """
    deps = [_dep("Whisper"), _dep("Gamma"), _dep("Cursor")]
    got = dependencies_in_the_news(
        deps, feeds=[("HN", "u")],
        fetcher=_feed("A whisper of change in the cursor of history",
                      "Gamma rays and you"))
    assert got == {}


def test_MUST_NOT_match_a_name_inside_a_longer_word():
    deps = [_dep("Groq")]
    got = dependencies_in_the_news(deps, feeds=[("HN", "u")],
                                   fetcher=_feed("Groqster launches something"))
    assert got == {}


def test_MUST_NOT_point_at_a_dependency_nothing_can_be_read_for():
    """No authority set means no pages to go and read, so a mention is not actionable
    and pointing the budget at it wastes the one thing this spends."""
    deps = [Dependency(kind="tool", canonical_name="Julius AI")]   # no domains
    got = dependencies_in_the_news(deps, feeds=[("HN", "u")],
                                   fetcher=_feed("Julius AI ships a new mode"))
    assert got == {}


def test_a_dead_feed_is_not_an_error():
    deps = [_dep("Composio")]
    assert dependencies_in_the_news(
        deps, feeds=[("HN", "u")],
        fetcher=lambda url, **k: _Fetch("", ok=False, status=503)) == {}
    assert dependencies_in_the_news(
        deps, feeds=[("HN", "u")],
        fetcher=lambda url, **k: _Fetch("not json at all")) == {}


def test_the_headline_never_becomes_evidence():
    """The whole design in one assertion: a news item can move a dependency up the
    queue and can never appear in a claim. `Claim.build` requires a source URL, a
    verbatim quote and a tier computed against the SUBJECT's own domains, so a
    news.test URL cannot substantiate anything about Composio even if something tried.
    """
    from miw.trust import ClaimKind, Tier, classify

    subj = _dep("Composio").subject()
    assert classify("https://news.test/x", subj, ClaimKind.DEPRECATION) is Tier.LEAD_ONLY
    # ...and LEAD_ONLY cannot settle a strict kind.
    from miw.trust import substantiates
    assert not substantiates(Tier.LEAD_ONLY, ClaimKind.DEPRECATION)
