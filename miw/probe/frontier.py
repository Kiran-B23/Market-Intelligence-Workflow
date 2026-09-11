"""Reading an official page as an ENUMERATION — the outside-in half of gap analysis.

Every other extractor in MIW starts from something the curriculum already teaches and
asks whether it is still true. This one runs the other way: it reads a vendor's own
page as a *list of the things that exist in an area*, so the set of items the
curriculum does NOT teach can be computed. Without it S11 has no input at all — the
dependency inventory is extracted from the course content, so by construction it can
only contain what is already taught.

The hazard is obvious and is the same one `catalogue.py` exists to defeat, wearing a
different hat: a page's text contains far more headings than it contains teachable
topics. "Get started", "Was this page helpful?", a language picker and a footer sitemap
all read as list items. Scraping text and calling the result "the state of the art"
would manufacture a curriculum gap out of a cookie banner.

So this module never treats page text as a flat string:

* Page furniture is removed by ELEMENT, not by keyword — `<nav>`, `<header>`,
  `<footer>`, `<aside>`, `<script>`, `<style>`, `<form>` and anything whose role is
  `navigation` go before a single heading is read. A keyword blocklist would need to
  know every vendor's wording; an element blocklist needs to know HTML.
* An item exists only if it is a **heading** or a **list item inside the section that
  heading opens**. A sentence that merely mentions a technique is not an enumeration of
  it, and is not read as one.
* `supported=False` when no heading structure can be bound. That is an explicit "this
  page publishes no such enumeration here" and never degrades to a text search — the
  same rule that keeps *we could not parse it* from becoming *it is gone*.

Each item carries the prose that follows it, because that prose is what becomes the
`Claim.build()` quote. An item whose own page says nothing about it cannot be cited and
is therefore dropped: a bare heading is a label, and a label is not evidence.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# --- structural removal of page furniture -----------------------------------

# Whole elements that are never content. Removed by tag, so no vendor-specific wording
# is involved: this is the difference between knowing HTML and guessing at copy.
_FURNITURE_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form",
                   "svg", "noscript", "template", "button", "select")
_FURNITURE = [re.compile(rf"<{t}\b[^>]*>.*?</{t}>", re.S | re.I) for t in _FURNITURE_TAGS]
# ARIA says what an element is for even when the tag does not.
_ROLE_FURNITURE = re.compile(
    r"<(\w+)\b[^>]*role=[\"'](?:navigation|banner|contentinfo|search|menu|menubar)"
    r"[\"'][^>]*>.*?</\1>", re.S | re.I)

_HEADING = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.S | re.I)
_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I)
# Code samples and tables are not prose. Stripped before a quote is taken, because a
# section's explanatory sentence often runs straight into its example and the flattened
# result reads as "Call multiple functions at once when they are independent: Python
# power_disco_ball = { "type" : "function" , ..." — true, cited, and unreadable.
_NOT_PROSE = re.compile(r"<(pre|code|table|script|style)\b[^>]*>.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _text(fragment: str) -> str:
    return _WS.sub(" ", _html.unescape(_TAG.sub(" ", fragment or ""))).strip()


def strip_furniture(html_text: str) -> str:
    """Remove the elements that are never an enumeration of anything."""
    out = html_text or ""
    for pat in _FURNITURE:
        out = pat.sub(" ", out)
    return _ROLE_FURNITURE.sub(" ", out)


# --- what may count as an item ----------------------------------------------

# Page furniture that survives element removal because it is written as a real heading.
# Matched on the WHOLE normalised name, never as a substring: "Overview" is furniture,
# "Overview of retrieval augmentation" is a topic, and a substring test cannot tell
# them apart. This is the same exact-match rule `vendors.Catalogue.get` follows.
STOP_ITEMS = frozenset({
    "overview", "introduction", "intro", "get started", "getting started",
    "quickstart", "quick start", "next steps", "next step", "see also", "related",
    "further reading", "resources", "summary", "conclusion", "faq", "faqs",
    "examples", "example", "reference", "api reference", "guides", "guide",
    "documentation", "docs", "changelog", "pricing", "contents", "table of contents",
    "on this page", "in this article", "was this page helpful", "feedback",
    "prerequisites", "requirements", "installation", "install", "setup",
    "limitations", "notes", "note", "tips", "best practices", "troubleshooting",
    "support", "contact us", "sign up", "log in", "login", "search", "menu",
    "home", "blog", "news", "about", "legal", "privacy", "terms", "cookies",
    # Consent banners are rendered as real headings inside plain <div>s, so no tag or
    # role removes them. Anthropic's prompt-engineering page contributes
    # "Cookie settings" as an h3 and it would otherwise be reported as a curriculum gap.
    "cookie settings", "cookies settings", "manage cookies", "privacy settings",
    # Generic calls to action. A heading naming ONE area's furniture
    # ("Topic-specific prompt guides") belongs in that source's `exclude` list in
    # `registry/topics.yaml`, where a human can see it, not in a global list that every
    # other area then carries around.
    "what's next", "whats next", "start building",
})

# An item name has to look like the name of a thing: long enough to be more than a
# label, short enough not to be a paragraph, and containing a letter.
_MIN_NAME, _MAX_NAME = 3, 72
_HAS_LETTER = re.compile(r"[A-Za-z]")
# A quote must clear `Claim.build`'s 12-character floor with room to be meaningful.
_MIN_CONTEXT = 40


# Words that name the CATEGORY rather than the thing, in any area. "Chain-of-thought
# technique" and "chain of thought" are one topic wherever you are.
_CATEGORY_WORDS = ("technique", "techniques", "method", "methods", "approach",
                   "approaches", "pattern", "patterns", "strategy", "strategies",
                   "mode", "modes", "feature", "features", "capability", "capabilities")


def normalise(name: str, drop: Iterable[str] = ()) -> str:
    """Fold a topic name to a comparison key.

    Hyphens, spaces and case are noise in this domain and nothing else: the workbook
    writes "Chain-of-Thought", a vendor writes "Chain of thought prompting", and a
    paper writes "chain-of-thought". All three are the same technique, and any
    comparison that treats them as three distinct items reports two false gaps.

    What is folded out is `_CATEGORY_WORDS`: words that name the category rather than
    the thing, in ANY area. This list used to read "prompting, prompts, technique,
    method, approach, pattern" - tuned to the first area anyone looked at, and doing
    nothing at all for the other nine.

    `drop` exists for a caller that wants to fold out more, and production deliberately
    does not use it. Passing each area's own `scope_terms` was tried and measured: it
    reduces a name that is MOSTLY area vocabulary to a stub, and the stub then matches
    the wrong thing. "Other image generation modes" became `other`, which the coverage
    check found in three unrelated sessions and reported as already taught; "Parallel
    function calling" became `parallel` and matched a session about multi-agent systems.
    Vendor padding is handled instead by the term rule in `analyse/curriculum.py`, which
    asks whether every distinctive word of the name is already in one session - and
    that rule cannot produce a stub, because it never shortens anything.
    """
    s = _WS.sub(" ", (name or "").lower()).strip()
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    s = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", s)          # "Few-shot (in-context)"
    words = [re.escape(w) for w in {*_CATEGORY_WORDS,
                                    *(w.lower() for t in drop
                                      for w in re.findall(r"[a-z]+", t.lower()))}]
    if words:
        s = re.sub(r"\b(?:" + "|".join(sorted(words, key=len, reverse=True))
                   + r")s?\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


# A heading that exists to hold whatever did not fit the other headings. It names a
# leftover, not a subject, so "Other image generation modes" cannot be added to a
# session outline - there is nothing to add. Structural rather than a per-page
# blocklist: every documentation set has one of these and they all start the same way.
_CATCH_ALL = re.compile(r"^(?:other|more|additional|misc\.?|miscellaneous|advanced)\b"
                        r".*\b(?:modes?|options?|topics?|features?|settings?|"
                        r"resources?|scenarios?|capabilities|considerations?)$", re.I)


def _is_item(name: str) -> bool:
    if not (_MIN_NAME <= len(name) <= _MAX_NAME):
        return False
    if _CATCH_ALL.match(name.strip()):
        return False
    if not _HAS_LETTER.search(name):
        return False
    if name.endswith("?"):
        return False            # a question is a section title, not a named thing
    flat = _WS.sub(" ", name.lower()).strip(" .:·—-")
    return flat not in STOP_ITEMS


@dataclass(frozen=True)
class FrontierItem:
    """One thing an official page enumerates, with the prose that describes it."""
    name: str
    context: str                # becomes the Claim quote; never empty
    source_url: str = ""
    found_as: str = ""          # heading:h2 | list_item — how it was bound
    # The area's own vocabulary, folded out of `key`. Carried on the item rather than
    # applied by the caller so that an item's key means the same thing everywhere it is
    # compared — coverage, corroboration and the registry's `exclude` list.
    drop: tuple = ()

    @property
    def key(self) -> str:
        return normalise(self.name, self.drop)


@dataclass
class FrontierList:
    """The enumeration read off one page, or an explicit refusal to guess."""
    source_url: str = ""
    supported: bool = False     # False = the page publishes no enumeration we can bind
    reason: str = ""
    items: list[FrontierItem] = field(default_factory=list)
    headings_seen: int = 0

    def keys(self) -> set[str]:
        return {i.key for i in self.items if i.key}


def _spans(html_text: str) -> list[tuple[int, int, str, int, int]]:
    """Every heading as (level, start_of_heading, text, body_start, body_end)."""
    found = [(int(m.group(1)), m.start(), _text(m.group(2)), m.end())
             for m in _HEADING.finditer(html_text)]
    out = []
    for i, (level, start, text, body_start) in enumerate(found):
        # A section ends at the next heading of the SAME OR HIGHER rank. Ending it at
        # the next heading of any rank would truncate every section that has
        # sub-headings, which is most of them.
        end = len(html_text)
        for level2, start2, _t, _b in found[i + 1:]:
            if level2 <= level:
                end = start2
                break
        out.append((level, start, text, body_start, end))
    return out


def _prose(body: str, limit: int = 320) -> str:
    """The first sentences of a section's own prose, list markup excluded.

    Taken from the section body rather than the whole page so the quote genuinely
    describes the item it is attached to. `<li>` content is removed first: a quote made
    of the section's sub-list is a list of other items, not evidence about this one.
    Code, tables and their markup go the same way, for the same reason — and so do
    nested HEADINGS, which are labels for sub-sections rather than sentences in this
    one. Without that, a documentation page that presents each code sample under its
    own language heading contributed "Call multiple functions at once when they are
    independent: Python JavaScript Java REST" as its definition: the sentence is real,
    and the tab labels after it are not part of it.
    """
    text = _text(_HEADING.sub(" ", _NOT_PROSE.sub(" ", _LI.sub(" ", body))))
    if len(text) <= limit:
        return text
    cut = text[:limit]
    parts = _SENT.split(cut)
    return (" ".join(parts[:-1]) if len(parts) > 1 else cut).strip()


def enumerate_page(html_text: str, *, source_url: str = "", under: str = "",
                   levels: Iterable[int] = (2,), list_items: bool = False,
                   drop: Iterable[str] = ()) -> FrontierList:
    """Read `html_text` as a list of named things.

    `under` binds the enumeration to one section by its heading text — the structural
    equivalent of `catalogue.py` binding a column to a role. When it is given, items
    are the sub-headings and list items *inside that section only*, so a page that
    enumerates six areas does not contribute all six areas' contents to one of them.
    When it is empty the page's own headings at `levels` are the enumeration.

    `list_items` is off by default, and that default was measured rather than guessed.
    On four real vendor documentation pages, headings enumerated the area cleanly while
    `<li>` contributed "topK", "Temperature", "biography Response" and
    "Generated by Nano Banana 2 Prompt" - parameter tables and image captions, not
    topics. A source whose enumeration genuinely IS a list turns it on in
    `registry/topics.yaml`, per source, where a human can see the choice.
    """
    res = FrontierList(source_url=source_url)
    drop = tuple(drop)
    clean = strip_furniture(html_text or "")
    spans = _spans(clean)
    res.headings_seen = len(spans)
    if not spans:
        res.reason = "no headings: page publishes no enumeration here"
        return res

    want = set(levels)
    scope_body, scope_level = clean, 0
    if under:
        key = normalise(under, drop)
        hit = next((s for s in spans if key and key in normalise(s[2], drop)), None)
        if hit is None:
            res.reason = f"section {under!r} not found on page"
            return res
        scope_level = hit[0]
        scope_body = clean[hit[3]:hit[4]]
        # Inside a bound section, its sub-headings are the items regardless of the
        # caller's `levels` — the section's own rank decides what "one level down" is.
        want = {scope_level + 1, scope_level + 2}
        spans = _spans(scope_body)

    seen: set[str] = set()

    def add(name: str, context: str, found_as: str) -> None:
        name = name.strip(" .:·—-")
        if not _is_item(name):
            return
        key = normalise(name, drop)
        # A bare label cannot be cited, so it cannot become a finding. Dropping it here
        # rather than at `Claim.build` keeps the count honest: `items` is what we could
        # actually evidence, not what we could see.
        if not key or key in seen or len(context) < _MIN_CONTEXT:
            return
        seen.add(key)
        res.items.append(FrontierItem(name=name, context=context,
                                      source_url=source_url, found_as=found_as,
                                      drop=drop))

    for level, _start, text, body_start, body_end in spans:
        if level not in want:
            continue
        body = scope_body[body_start:body_end]
        add(text, _prose(body), f"heading:h{level}")

    # List items inside the bound section (or the whole page when unbound). Their
    # context is the item's own text beyond its name — "Few-shot: give the model two
    # or three worked examples" carries both in one <li>.
    for m in (_LI.finditer(scope_body) if list_items else ()):
        raw = _text(m.group(1))
        name, _, rest = raw.partition(":")
        if not rest:
            name, _, rest = raw.partition(" — ")
        if not rest:
            name, _, rest = raw.partition(" - ")
        if rest.strip():
            add(name, raw, "list_item")

    res.supported = bool(res.items)
    if not res.supported:
        res.reason = (f"{len(spans)} heading(s) found but none is a citeable named "
                      f"topic")
    return res
