"""Launch nominations — and the reason they stop at nomination.

The pipeline was built to the point of counting what it would report against the real
feed: 42 findings a run, filed as "Hebbian Robotics — scalable robotics data pipelines"
under `market-data` and twenty-two more under `agent-framework` solely because the word
"agent" appears in an AI launch feed. That is the wall PRD §29 already recorded —
relevance cannot be inferred from co-occurrence, and a keyword is co-occurrence with
extra steps — so these are browsable and never scored.
"""
import json

import pytest

from miw.research.launch import (CAPABILITY_HINTS, Launch, capability_of,
                                 product_name, read_launches)


class _Fetch:
    def __init__(self, payload, ok=True, status=200):
        self.body = json.dumps(payload) if isinstance(payload, dict) else payload
        self.status, self._ok = status, ok

    @property
    def ok(self):
        return self._ok


def _feed(*hits):
    return _Fetch({"hits": [{"title": t, "url": u, "points": p} for t, u, p in hits]})


def _run(hits, *, caps=("agent-framework", "voice-synthesis"), taught=()):
    return read_launches(capabilities=caps, taught=taught,
                         feeds=[("Launch HN", "u")], fetcher=lambda u: _feed(*hits))


@pytest.mark.parametrize("title,name", [
    ("Launch HN: Speko (YC S26) – OpenRouter for Voice AI", "Speko"),
    ("Show HN: Bitroad – Infra for Agent-to-Agent Services", "Bitroad"),
    ("Launch HN: Agnost AI (YC S26) – Extract feedback from agent chats", "Agnost AI"),
])
def test_the_product_name_is_pulled_out_of_the_headline(title, name):
    assert product_name(title) == name


def test_a_capability_is_only_claimed_for_something_we_teach():
    """A launch in a category no session touches is not a curriculum question at all."""
    title = "Launch HN: Foo – a vector database for embeddings"
    assert capability_of(title, ["vector-db"])[0] == "vector-db"
    assert capability_of(title, ["voice-synthesis"])[0] == ""


def test_the_matched_phrase_is_kept_so_a_wrong_guess_is_visible():
    """"IDE for the agents era" really is an IDE; "AI agents to discover new materials"
    really is not an agent framework. The reader can only tell if we show our working."""
    cap, hint = capability_of("Launch HN: Superset – IDE for the agents era", ["ide"])
    assert (cap, hint) == ("ide", "ide")


def test_a_self_post_with_no_product_url_is_dropped():
    """Nobody can check a name with nothing behind it."""
    rep = _run([("Launch HN: Ghost – an agent framework", "", 50)])
    assert rep.launches == [] and rep.no_url == 1


def test_something_we_already_teach_is_not_a_nomination():
    rep = _run([("Launch HN: Speko – a voice cloning tool", "https://speko.ai", 10)],
               taught=["Speko"])
    assert rep.launches == [] and rep.already_taught == 1


def test_off_topic_launches_are_counted_not_reported():
    """55 of 100 real feed items are in no category we teach. Counting them is how the
    stage can say what it filtered rather than just what survived."""
    rep = _run([("Launch HN: RonanRX – Personalized Peptides and GLP-1s",
                 "https://ronanrx.com", 119)])
    assert rep.launches == [] and rep.off_topic == 1 and rep.fetched == 1


def test_nominations_are_ordered_by_interest():
    rep = _run([("Launch HN: Quiet – an agent runtime", "https://a.test", 3),
                ("Launch HN: Loud – an agent runtime", "https://b.test", 300)])
    assert [l.name for l in rep.launches] == ["Loud", "Quiet"]


def test_a_dead_feed_is_recorded_and_does_not_raise():
    rep = read_launches(capabilities=["agent-framework"], taught=[],
                        feeds=[("Launch HN", "u")],
                        fetcher=lambda u: _Fetch("", ok=False, status=503))
    assert rep.launches == [] and rep.errors and "503" in rep.errors[0]


def test_a_nomination_carries_no_severity_and_no_deadline():
    """It is not a finding and must not be able to become one by accident."""
    rep = _run([("Launch HN: Foo – an agent framework", "https://foo.test", 9)])
    l = rep.launches[0]
    assert isinstance(l, Launch)
    for attr in ("severity", "due_by", "signal", "claims"):
        assert not hasattr(l, attr), f"a Launch must not carry {attr}"


def test_every_hinted_capability_is_a_real_one():
    from config.constants import CAPABILITIES
    assert set(CAPABILITY_HINTS) <= set(CAPABILITIES)
