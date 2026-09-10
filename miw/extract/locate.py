"""Resolve a `Location.field_path` back to the course content it came from.

The inventory records *where* a dependency was seen — `[0].topics[4].units[9].
contents[0].solutions[0].solution_answer` — but a reviewer opening a finding needs the
*text*: which question, which line, and what to change. This module is the inverse of
`ingest/portal._texts_from_content`, which built those paths on the way in.

Three things it must get right, because each one was a way of misleading a reader:

1. **Not every location is resolvable, and the unresolvable ones must say so.**
   Sheet-derived locations carry `field_path = "Workbook.xlsx::Sheet name"` and no
   session number — they assert *that* a session uses a tool, never where in the export
   it appears. Rendering a blank excerpt for those reads as "we looked and found
   nothing"; `reason` distinguishes it from a path that genuinely failed to resolve.
2. **The excerpt must be a window, not the field.** The measured `solution_answer` for
   `gemini-2.0-flash` in Intro to Gen AI session 11 is 5,149 characters of embedded n8n
   workflow JSON. Pasting that into a side panel buries the one line that matters.
3. **The match has to be located, not asserted.** We return byte spans into the
   excerpt so the UI highlights exactly what the extractor matched. If we cannot find
   the term in the resolved text, we say the path resolved but the term did not appear
   rather than highlighting something arbitrary — that mismatch is a real signal that
   the export has moved on since the last `ingest`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
COURSES_DIR = ROOT / "data" / "courses"

# `[0]`, `.topics`, `[4]` ... Anything else in a path is a parse failure, not something
# to skip: a silently ignored segment resolves to the wrong node.
_TOKEN = re.compile(r"\[(\d+)\]|\.?([A-Za-z_][A-Za-z0-9_]*)")

# How much text to show around the match, and the cap when there is no match to centre
# on. Chosen so the n8n-workflow case shows the surrounding node rather than the file.
WINDOW = 420
NO_MATCH_HEAD = 600


def parse_path(field_path: str) -> Optional[list]:
    """`"[0].topics[4].units[9].contents[0].content"` -> `[0,'topics',4,...]`.

    Returns None if the string is not a JSON path at all — which is how a sheet
    location (`"Book.xlsx::Sheet"`) is detected without special-casing its format here.
    """
    if not field_path or "::" in field_path:
        return None
    out, pos = [], 0
    for m in _TOKEN.finditer(field_path):
        if m.start() != pos:            # a gap means an unparsed character
            return None
        pos = m.end()
        idx, key = m.group(1), m.group(2)
        out.append(int(idx) if idx is not None else key)
    return out if pos == len(field_path) and out else None


def _walk(doc: Any, parts: list) -> tuple[Any, bool]:
    cur = doc
    for p in parts:
        try:
            cur = cur[p]
        except (KeyError, IndexError, TypeError):
            return None, False
    return cur, True


SHEETS_DIR = ROOT / "data" / "sheets"

# `Book.xlsx::Course Outline::Outline::row7` - written by `ingest.outline`, and unlike a
# tool-sheet path it names a specific cell, so it CAN be resolved. That matters: the
# slide outline is the only textual record of a session's deck we have (the deck itself
# is a Google Slides link, absent from the export), so a reviewer opening a finding
# whose evidence is "taught on the slides" would otherwise be shown nothing at all.
_CELL_PATH = re.compile(r"^(?P<book>[^:]+\.xlsx)::(?P<sheet>[^:]+)::(?P<col>[^:]+)::row(?P<row>\d+)$")

_WB_CACHE: dict[str, tuple[float, Any]] = {}


def _workbook_cell(book: str, sheet: str, column: str, row: int) -> Optional[str]:
    """One cell, addressed by header name rather than column index.

    By header, because the workbooks disagree on column order - the same reason
    `ingest.outline` resolves its headers by alias.
    """
    try:
        import openpyxl
    except ImportError:                                    # pragma: no cover
        return None
    path = SHEETS_DIR / book
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _WB_CACHE.get(book)
    if hit and hit[0] == mtime:
        wb = hit[1]
    else:
        try:
            wb = openpyxl.load_workbook(str(path), data_only=True)
        except Exception:
            return None
        _WB_CACHE[book] = (mtime, wb)
    if sheet not in wb.sheetnames:
        return None
    ws = wb[sheet]
    rows = ws.iter_rows(values_only=True)
    try:
        header = [re.sub(r"\s+", " ", str(h or "").strip().lower()) for h in next(rows)]
    except StopIteration:
        return None
    want = re.sub(r"\s+", " ", column.strip().lower())
    try:
        ci = header.index(want)
    except ValueError:
        return None
    # `row` is 1-based including the header, as `ingest.outline` records it.
    cell = ws.cell(row=row, column=ci + 1).value
    return None if cell is None else str(cell)


_CACHE: dict[str, tuple[float, Any]] = {}


def load_course(slug: str) -> Optional[Any]:
    """The raw export for a slug, memoised on mtime.

    The exports are 6-10 MB each; re-reading one per location would make a side panel
    with 20 locations unusable. Keyed on mtime so a fresh export is picked up without
    restarting the server.
    """
    path = COURSES_DIR / f"{slug}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _CACHE.get(slug)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        doc = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    _CACHE[slug] = (mtime, doc)
    return doc


def clear_cache() -> None:
    _CACHE.clear()
    _WB_CACHE.clear()


def _spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    """Every case-insensitive occurrence of any term, longest term first.

    Longest-first matters: for a dependency whose aliases include both `gemini` and
    `gemini-2.0-flash`, matching the short alias first would highlight three characters
    of the very id the reviewer is looking for.
    """
    found: list[tuple[int, int]] = []
    for t in sorted({t for t in terms if t and len(t) >= 2}, key=len, reverse=True):
        for m in re.finditer(re.escape(t), text, re.I):
            s, e = m.start(), m.end()
            if any(s < os + ol and os < e for os, ol in found):
                continue                       # already covered by a longer term
            found.append((s, e - s))
    return sorted(found)


def _window(text: str, spans: list[tuple[int, int]]) -> tuple[str, int, bool]:
    """A readable slice around the first match. Returns (excerpt, offset, truncated)."""
    if not spans:
        return text[:NO_MATCH_HEAD], 0, len(text) > NO_MATCH_HEAD
    first = spans[0][0]
    start = max(0, first - WINDOW // 3)
    end = min(len(text), first + spans[0][1] + WINDOW)
    # Snap to a line boundary so the excerpt does not begin mid-token.
    nl = text.rfind("\n", start, first)
    if nl != -1 and first - nl < WINDOW:
        start = nl + 1
    return text[start:end], start, (start > 0 or end < len(text))


# Path prefixes that identify the enclosing content object, so the panel can name the
# question rather than only the field inside it.
_CONTAINER_KEYS = ("contents", "question_details")


def _container(doc: Any, parts: list) -> dict:
    """Title / type of the content object enclosing the field, for context."""
    for i in range(len(parts) - 1, 0, -1):
        if parts[i - 1] in _CONTAINER_KEYS and isinstance(parts[i], int):
            node, ok = _walk(doc, parts[:i + 1])
            if ok and isinstance(node, dict):
                return {
                    "title": str(node.get("title") or node.get("short_text") or ""),
                    "object_type": str(node.get("object_type") or ""),
                    "content_type": str(node.get("content_type") or ""),
                }
            break
    return {}


def _resolve_cell(m, out: dict, terms: list[str],
                  fallback_terms: list[str] = ()) -> dict:
    """Resolve a workbook cell path - a session's slide outline or key takeaways."""
    text = _workbook_cell(m.group("book"), m.group("sheet"), m.group("col"),
                          int(m.group("row")))
    if text is None:
        out["reason"] = "workbook_cell_missing"
        return out
    spans = _spans(text, terms)
    matched_by = "name" if spans else ""
    if not spans and fallback_terms:
        spans = _spans(text, list(fallback_terms))
        matched_by = "referenced_url" if spans else ""
    excerpt, offset, truncated = _window(text, spans)
    out.update(resolved=True, text_len=len(text), excerpt=excerpt, offset=offset,
               truncated=truncated, matched_by=matched_by,
               # Named so the panel can say this is the slide outline, not course content.
               container={"title": f"{m.group('col')} — {m.group('sheet')}",
                          "object_type": "SESSION_PPT", "content_type": "WORKBOOK"},
               spans=[[s - offset, l] for s, l in spans
                      if offset <= s < offset + len(excerpt)])
    if not spans:
        out["reason"] = "term_not_in_field"
    return out


def resolve(slug: str, field_path: str, terms: list[str],
            fallback_terms: list[str] = ()) -> dict:
    """Resolve one location to an excerpt with the match located.

    `resolved` is False for every failure, and `reason` always says which — a reviewer
    must never be shown an empty panel that could mean either "nothing there" or "we
    could not look".

    `fallback_terms` are tried ONLY when no primary term appears, and `matched_by` says
    which set won. The two are not interchangeable: the primary terms are the
    dependency's own names, while the fallback is the set of URLs it references, and a
    URL match is weaker evidence that *this* line is the thing to edit. Mixing them
    into one list let a URL match win on length alone and highlighted a link to another
    tool's docs page as though it were this dependency.
    """
    out = {"resolved": False, "reason": "", "field_path": field_path,
           "excerpt": "", "spans": [], "offset": 0, "text_len": 0,
           "truncated": False, "container": {}, "matched_by": ""}

    parts = parse_path(field_path)
    if parts is None:
        cell = _CELL_PATH.match(field_path or "")
        if cell:
            return _resolve_cell(cell, out, terms, fallback_terms)
        out["reason"] = ("sheet_declaration" if "::" in (field_path or "")
                         else "unparsable_path")
        return out

    doc = load_course(slug)
    if doc is None:
        out["reason"] = "course_export_missing"
        return out

    node, ok = _walk(doc, parts)
    if not ok:
        out["reason"] = "path_not_found"
        return out
    if not isinstance(node, (str, int, float)):
        out["reason"] = "not_a_text_field"
        return out

    text = str(node)
    spans = _spans(text, terms)
    matched_by = "name" if spans else ""
    if not spans and fallback_terms:
        spans = _spans(text, list(fallback_terms))
        matched_by = "referenced_url" if spans else ""
    excerpt, offset, truncated = _window(text, spans)
    out.update(resolved=True, text_len=len(text), excerpt=excerpt, offset=offset,
               truncated=truncated, container=_container(doc, parts),
               matched_by=matched_by,
               spans=[[s - offset, l] for s, l in spans
                      if offset <= s < offset + len(excerpt)])
    if not spans:
        out["reason"] = "term_not_in_field"
    return out
