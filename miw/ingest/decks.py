"""Slide decks — the authoring source, read at last.

§31 established that the deck is where a session is written and everything else is
downstream of it. It was also, until now, the least observed input in the system: the
workbook records a `Session PPT` URL per session and nothing ever opened it, so a deck
that had been deleted, unshared or emptied was invisible until a human opened it by
hand. That is the founding codetotutorial failure pointed inward.

Two things make this cheap. The decks are **published to web**, so an anonymous fetch
works and no Drive API, credential or permission negotiation is involved. And the parse
was already written: `/home/nxtwave/Market Intelligence/extract_deck.py` reads exactly
this markup, and PRD §5 listed it as reusable prior art. It is vendored here rather than
imported, the same way `ingest/portal.py` vendored `build_course_sheet.py`.

The rule that carries this module is the one `probe/catalogue.py` established: **when the
structure cannot be bound, say so — never guess.** It matters more here than anywhere
else, because Google answers a request for a deck you may not open with **HTTP 200** and
a sign-in shell. Measured across the workbook's own URLs: 17 decks are published and
yield 2,700-11,500 characters of real slide text; 51 return 200 with an identical
108-character shell. Treating those as content would stamp the same boilerplate onto 51
sessions of the coverage index, which is worse than having no deck text at all.

The discriminator is structural rather than a keyword search for "Sign in". A published
deck embeds its slide model as a JS array of `[objectId, index, title]` triples; a
permission-gated one embeds none. 17-53 ids versus exactly 0 — so the test is "did the
slide model parse", which is the same shape as "did a table role-type".
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

CACHE_DIR = Path("state/deck_cache")
# Decks change on a curriculum cycle, not a news cycle, and each fetch is 0.6-14MB.
CACHE_TTL_S = 7 * 24 * 3600

# --- vendored from `Market Intelligence/extract_deck.py` ---------------------
# Published decks embed the slide model as a JS array (ordered ids + titles) and render
# each slide as an <svg> whose text lives in aria-label attributes. Splitting on the
# <svg boundary groups body text to its slide exactly.

_MODEL = re.compile(r'\["(g[0-9a-f]+_\d+_\d+)",(\d+),"((?:[^"\\]|\\.)*)"')
_ARIA = re.compile(r'aria-label="(.*?)"', re.S)
_IMG_NOISE = re.compile(r"\.(png|jpg|jpeg|svg|gif|webp)$", re.I)
_BARE_SHAPE = re.compile(r"(?i)^(group|rectangle|frame|ellipse|arrow|line|image)\s*[\d()\s.\-]*$")


def _unescape(s: str) -> str:
    """Undo the \\xNN / \\uNNNN escaping Google applies to embedded markup."""
    s = s.replace("\\/", "/")
    s = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return _html.unescape(s)


def _clean(line: str) -> Optional[str]:
    """Drop shape names and image filenames — they are canvas furniture, not content."""
    line = " ".join(line.split())
    if not line or _IMG_NOISE.search(line) or _BARE_SHAPE.match(line):
        return None
    return line


@dataclass(frozen=True)
class Slide:
    index: int
    title: str = ""
    body: tuple = ()

    @property
    def text(self) -> str:
        return "\n".join([self.title, *self.body]).strip()


def slide_model(raw: str) -> dict:
    """`{slide index: title}` from the embedded model array.

    Empty for a deck we are not allowed to read, which is the whole point — see the
    module docstring. First sighting of an object id wins: Google repeats the array with
    per-revision variants and the first is the one the renderer uses.
    """
    titles: dict = {}
    seen: set = set()
    for m in _MODEL.finditer(raw or ""):
        obj_id, idx, title = m.group(1), int(m.group(2)), _unescape(m.group(3))
        if obj_id in seen:
            continue
        seen.add(obj_id)
        titles.setdefault(idx, " ".join(title.split()))
    return titles


def extract(raw: str) -> list:
    """Ordered slides with their body text. Assumes `slide_model` found something."""
    titles = slide_model(raw)
    page = _unescape(raw or "")
    out = []
    for i, chunk in enumerate(page.split("<svg ")[1:]):
        body, seen_lines = [], set()
        for m in _ARIA.finditer(chunk):
            for line in m.group(1).replace("&#xa;", "\n").splitlines():
                line = _clean(line)
                if line and line not in seen_lines:
                    seen_lines.add(line)
                    body.append(line)
        title = titles.get(i, "")
        out.append(Slide(index=i, title=title,
                         body=tuple(b for b in body if b != title)))
    return out


def collapse(slides: Iterable[Slide]) -> list:
    """Merge consecutive slides sharing a title.

    Decks both animate bullets in (a later slide is a superset of the one before) and
    reuse one title across genuinely different bullet sets. Unioning the body lines in
    order handles both without dropping anything.
    """
    out: list = []
    for s in slides:
        if out and s.title and s.title == out[-1].title:
            prev = out[-1]
            merged = list(prev.body) + [b for b in s.body if b not in prev.body]
            out[-1] = Slide(index=prev.index, title=prev.title, body=tuple(merged))
            continue
        out.append(s)
    return out


# --- fetching, with the refusal that matters --------------------------------

@dataclass
class Deck:
    """One deck as we found it — including the cases where we found nothing."""
    url: str
    status: Optional[int] = None
    reachable: bool = False
    supported: bool = False        # the slide model bound; this really is a deck
    reason: str = ""
    slides: list = field(default_factory=list)
    fetched_at: str = ""
    from_cache: bool = False

    @property
    def gone(self) -> bool:
        """The deck is not there. 403/401 are NOT gone — those are permission."""
        return self.status in (404, 410)

    @property
    def restricted(self) -> bool:
        """Served, but not to us. The 200-with-a-sign-in-shell case lives here.

        Kept apart from `gone` on purpose: "deleted" and "no longer shared" are
        different problems with different owners, and reporting one as the other sends a
        reviewer to re-create a deck that exists.
        """
        return self.reachable and not self.supported and self.status not in (404, 410)

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.slides if s.text).strip()


def _cache_path(url: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.json"


def read_deck(url: str, *, fetcher: Optional[Callable] = None,
              ttl: int = CACHE_TTL_S, cache: bool = True) -> Deck:
    """Fetch and parse one deck. Never raises; failure comes back on the Deck.

    The disk cache stores the EXTRACTED slides, not the HTML: a deck is 0.6-14MB of
    markup and about 8KB of text, so caching the parse keeps `state/` small and makes a
    re-run cheap. It also means a Google restyle invalidates by TTL rather than leaving
    us unable to re-read what we already understood.
    """
    from miw import net
    from miw.schema import utcnow

    path = _cache_path(url)
    if cache and path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            d = json.loads(path.read_text())
            return Deck(url=url, status=d.get("status"), reachable=d.get("reachable", False),
                        supported=d.get("supported", False), reason=d.get("reason", ""),
                        slides=[Slide(index=s["index"], title=s.get("title", ""),
                                      body=tuple(s.get("body") or []))
                                for s in (d.get("slides") or [])],
                        fetched_at=d.get("fetched_at", ""), from_cache=True)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass                       # a corrupt cache entry is not a dead deck

    got = (fetcher or net.fetch)(url)
    deck = Deck(url=url, status=getattr(got, "status", None),
                reachable=bool(getattr(got, "reachable", False)), fetched_at=utcnow())
    body = getattr(got, "body", "") or ""
    if not deck.reachable:
        deck.reason = f"not reachable: {getattr(got, 'error', '') or 'no response'}"
    elif deck.gone:
        deck.reason = f"deck returns {deck.status}"
    elif not slide_model(body):
        # The load-bearing refusal. See the module docstring: 200 + a sign-in shell is
        # how Google answers for a deck we may not open, and it is indistinguishable
        # from a real deck by status code alone.
        deck.reason = ("no slide model in the page — the deck is not published to web, "
                       "or we are not allowed to read it")
    else:
        deck.slides = collapse(extract(body))
        deck.supported = bool(deck.slides)
        if not deck.supported:
            deck.reason = "slide model parsed but no slide carried any text"

    if cache:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "url": url, "status": deck.status, "reachable": deck.reachable,
                "supported": deck.supported, "reason": deck.reason,
                "fetched_at": deck.fetched_at,
                "slides": [{"index": s.index, "title": s.title, "body": list(s.body)}
                           for s in deck.slides]}))
        except OSError:
            pass                       # an unwritable cache must not fail the run
    return deck


# --- which URL to use for which session -------------------------------------

_PRESENTATION = re.compile(r"https://docs\.google\.com/presentation/[^\s\"'\\<>)]+")


def published(url: str) -> bool:
    """Is this the publish-to-web form, the only one an anonymous fetch can read?

    `/presentation/d/e/2PACX-…` is published; `/presentation/d/<docId>` is the editor
    link, which answers 200 with a sign-in shell. Both appear in our inputs for the same
    deck, so preferring the first is what turns 17 readable decks into 85.
    """
    return "/presentation/d/e/" in (url or "")


def deck_urls(outlines: dict, records_path: str | Path = "out/content_records.jsonl"
              ) -> dict:
    """`{(course, session_no): url}`, preferring the form we can actually read.

    Two sources carry deck links and they disagree in a way that matters: the workbook's
    `Session PPT` column is mostly the editor form (51 of 68), while the course export
    carries the published form. Same decks, different links. Taking the published one
    wherever either source has it is the difference between reading 17 sessions' decks
    and reading 85 — including the sessions of a course with no workbook at all.
    """
    out: dict = {}
    for course, outline in (outlines or {}).items():
        for s in getattr(outline, "sessions", []):
            u = (getattr(s, "ppt_url", "") or "").strip()
            if u:
                out[(course, s.session_no)] = u

    path = Path(records_path)
    if not path.exists():
        return out
    with path.open() as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not r.get("session_no"):
                continue
            m = _PRESENTATION.search(r.get("body_text") or "")
            if not m:
                continue
            key = (r.get("course") or "", int(r["session_no"]))
            url = m.group(0)
            if published(url) and not published(out.get(key, "")):
                out[key] = url
            else:
                out.setdefault(key, url)
    return out
