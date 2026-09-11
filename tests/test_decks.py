"""Reading the session decks — and refusing to, when what came back is not a deck.

The decisive case here is not a failure at all: Google answers a request for a deck you
may not open with **HTTP 200** and a sign-in shell. Measured on the workbook's own URLs,
51 of 68 do exactly that, returning an identical 108-character result. Treating those as
slide text would stamp the same boilerplate onto 51 sessions of the coverage index,
which is worse than having no deck text — it would make them all look alike.
"""
from miw.ingest.decks import (Deck, Slide, collapse, deck_urls, extract, published,
                              read_deck, slide_model)


class _Fetch:
    def __init__(self, body="", status=200, error=""):
        self.body, self.status, self.error = body, status, error

    @property
    def reachable(self):
        return self.status is not None


def _published_html(*slides) -> str:
    """The shape a published deck actually has: a slide-model array, then <svg> text."""
    model = ",".join(f'["g{i:02x}d_0_{i}",{i},"{t}"]' for i, (t, _b) in enumerate(slides))
    svgs = "".join(
        f'<svg width="1"><g aria-label="{t}"></g>'
        + "".join(f'<g aria-label="{line}"></g>' for line in body)
        + "</svg>"
        for t, body in slides)
    return f"<html><script>var model=[{model}];</script>{svgs}</html>"


# ------------------------------------------------------------------ the refusal

def test_a_sign_in_shell_is_not_a_deck():
    """200 with no slide model means "not published to us", not "here is the content"."""
    shell = "<html><body><h1>Sign in</h1><svg><g aria-label='Sign in'></g></svg></body></html>"
    deck = read_deck("https://docs.google.com/presentation/d/abc", cache=False,
                     fetcher=lambda u: _Fetch(shell))
    assert deck.reachable and deck.status == 200
    assert not deck.supported
    assert deck.restricted, "served, but not to us"
    assert not deck.gone, "a permission wall is not a deleted deck"
    assert deck.text == ""
    assert "not published" in deck.reason


def test_the_discriminator_is_the_slide_model_not_a_keyword():
    """Structural, like `catalogue.py` binding a column to a role.

    A published deck embeds [objectId, index, title] triples; a gated one embeds none.
    Measured on real pages: 17-53 ids versus exactly 0.
    """
    assert slide_model(_published_html(("Intro", ["a line"]))) == {0: "Intro"}
    assert slide_model("<html>Sign in to continue</html>") == {}


def test_a_deleted_deck_is_reported_as_gone_not_as_restricted():
    """Different problems, different owners. Telling a reviewer to re-share a deck that
    was deleted, or to re-create one that merely lost its sharing, wastes both."""
    deck = read_deck("https://docs.google.com/presentation/d/e/2PACX-x", cache=False,
                     fetcher=lambda u: _Fetch("", status=404))
    assert deck.gone and not deck.restricted and not deck.supported


def test_an_unreachable_host_is_neither():
    deck = read_deck("https://docs.google.com/presentation/d/e/2PACX-x", cache=False,
                     fetcher=lambda u: _Fetch("", status=None, error="timeout"))
    assert not deck.reachable and not deck.gone and not deck.restricted
    assert "not reachable" in deck.reason


# ------------------------------------------------------------------ extraction

def test_slides_keep_their_titles_and_bodies():
    html = _published_html(("What is a prompt?", ["A prompt is an instruction.",
                                                  "Prompts can be long."]),
                           ("Prompt frameworks", ["RCAFT", "STAR"]))
    deck = read_deck("u", cache=False, fetcher=lambda u: _Fetch(html))
    assert deck.supported
    assert [s.title for s in deck.slides] == ["What is a prompt?", "Prompt frameworks"]
    assert "RCAFT" in deck.text and "A prompt is an instruction." in deck.text
    # The title is not repeated inside its own body.
    assert deck.slides[0].body == ("A prompt is an instruction.", "Prompts can be long.")


def test_animated_reveals_collapse_into_one_slide():
    """Decks animate bullets in, so consecutive slides share a title and each is a
    superset of the one before. Unioning in order keeps everything exactly once."""
    slides = [Slide(0, "Steps", ("one",)), Slide(1, "Steps", ("one", "two")),
              Slide(2, "Next", ("other",))]
    got = collapse(slides)
    assert [s.title for s in got] == ["Steps", "Next"]
    assert got[0].body == ("one", "two")


def test_canvas_furniture_is_not_slide_text():
    """`Rectangle 4` and `image3.png` are drawing objects, not what the slide says."""
    html = _published_html(("Real title", ["Rectangle 4", "image3.png", "Group 12",
                                           "An actual sentence."]))
    deck = read_deck("u", cache=False, fetcher=lambda u: _Fetch(html))
    assert deck.slides[0].body == ("An actual sentence.",)


# ------------------------------------------------------------------ which URL

def test_the_published_form_is_preferred_over_the_editor_link():
    """The two inputs carry different links to the same deck, and only one is readable.

    The workbook's `Session PPT` column is mostly the editor form (51 of 68); the course
    export carries the published form. Preferring it is the difference between reading
    17 sessions' decks and reading 85 — including all 13 PSE sessions, which have no
    workbook at all.
    """
    assert published("https://docs.google.com/presentation/d/e/2PACX-1vQ11/pub")
    assert not published("https://docs.google.com/presentation/d/1GFNgel/edit")


def test_deck_urls_takes_the_export_link_when_the_workbook_has_the_editor_one(tmp_path):
    import json
    from types import SimpleNamespace

    editor = "https://docs.google.com/presentation/d/1GFNgel/edit"
    pub = "https://docs.google.com/presentation/d/e/2PACX-1vQ11/pub"
    outline = SimpleNamespace(sessions=[SimpleNamespace(session_no=3, ppt_url=editor)])
    recs = tmp_path / "r.jsonl"
    recs.write_text(json.dumps({"course": "C", "session_no": 3,
                                "body_text": f"see the deck at {pub} for details"}))
    got = deck_urls({"C": outline}, recs)
    assert got[("C", 3)] == pub


def test_a_session_with_only_an_editor_link_still_gets_one(tmp_path):
    """Recording the unreadable link is right: `read_deck` then reports *why* it could
    not be read, which is the finding. Dropping it would hide the problem."""
    from types import SimpleNamespace
    editor = "https://docs.google.com/presentation/d/1GFNgel/edit"
    outline = SimpleNamespace(sessions=[SimpleNamespace(session_no=3, ppt_url=editor)])
    got = deck_urls({"C": outline}, tmp_path / "missing.jsonl")
    assert got == {("C", 3): editor}
