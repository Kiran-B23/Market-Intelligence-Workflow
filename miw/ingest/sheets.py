"""Workbook input: the hand-maintained per-session tool lists.

The course workbooks carry columns the JSON export does not — `Tool@version` on the
`Entity Ids - Tools & Versions U` sheets, and a `Tools` column on the key-takeaways
and curriculum sheets. Those are a curriculum engineer's own statement of what a
session uses, including no-code and SaaS tools that appear only on slides and so are
invisible to the JSON export.

Two things this module must respect:

* **openpyxl must not use `read_only=True`.** The hand-built workbooks have unsized
  worksheets, and read-only mode raises on `calculate_dimension()` for them. Use
  `data_only=True` so formula cells yield values.
* **Header positions vary** between hand-built and generated workbooks (`Topic Name`
  vs `Module Name`, a trailing space on `Reading Material `). Headers are resolved by
  alias, never by index — the same approach already taken twice in
  `agentic-ai-content-workflows` (`import_curriculum.py`, `curriculum_sync.py`).

Names arrive as a flat, tiered string — services, then packages, then models, `\\n`
between tiers and `, ` within one. They are emitted as *names only*: a sheet says a
session uses a tool, not where that tool's official pages live, so a sheet-derived
entry gets no authority set and therefore cannot substantiate a strict claim until a
human adds one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

try:
    import openpyxl
except ImportError:                                        # pragma: no cover
    openpyxl = None

TOOL_HEADERS = ("tool@version", "tools", "tools used", "tool", "entity name")
SESSION_HEADERS = ("session name", "session title", "entity name", "unit title",
                   "unit name", "session")

# Qualifiers and pins carried inside a cell: `Google Colab (T4 GPU)`, `n8n@2.17.8`.
_QUALIFIER = re.compile(r"\s*\([^)]*\)\s*$")
_PIN = re.compile(r"@([\w.\-]+)$")
# Cells sometimes carry trailing reference URLs on their own lines.
_URL_LINE = re.compile(r"^https?://", re.I)

# Values that are not tool names. `Session Mapping` and `Tools Used` are documented as
# dirty in the Gen AI workbook - they hold leftovers from an unrelated React course.
NOT_A_TOOL = {"", "-", "n/a", "na", "none", "nil", "tbd", "todo", "react", "reactdom",
              "babel", "no tools", "not applicable"}


# Workbook filename -> course title. Matched on a NORMALISED STEM, never the exact
# filename: an exact-match version silently broke when the workbooks were renamed, and
# because the lookup fell back to the filename itself, 3,633 sheet locations were
# attributed to three phantom courses called "AI for Finance - Course Contents.xlsx" and
# friends. This lives here rather than in `main.py` because BOTH `ingest` (for the
# authoritative session numbering) and `extract` (for the tool columns) need it, and two
# copies of this map is how the phantom-course bug would come back.
# Filename fragment -> course title, for workbooks whose name predates the derivation
# rule below. These are ALIASES and they are load-bearing: "Intro to Generative AI -
# Course Contents.xlsx" normalises to `introtogenerativeai`, while the course's slug and
# title both normalise to `introtogenai` — neither is a substring of the other. Derive
# only, and the biggest course silently loses its workbook: session numbering falls back
# to positional (the 81-unit off-by-one of PRD §31 returning) and `ingest` exits 1.
LEGACY_WORKBOOK_KEYS = {
    "genai": "Intro to Gen AI", "generativeai": "Intro to Gen AI",
    "llmapps": "Building LLM Applications",
    "llmapplications": "Building LLM Applications",
    "aiforfinance": "AI for Finance",
    "pse": "PSE",
}
# Kept as the old name so nothing that imported it breaks.
WORKBOOK_COURSES = LEGACY_WORKBOOK_KEYS

# Below this length a key matches by EQUALITY only. Substring matching on a short key is
# how a course slugged `ai` would steal `AI for Finance - Course Contents.xlsx`.
_MIN_SUBSTRING_KEY = 5


def norm_stem(workbook: str) -> str:
    return re.sub(r"[^a-z0-9]", "",
                  str(workbook).lower().replace("course contents", "")
                  .replace("contents", "").replace(".xlsx", ""))


def workbook_keys() -> dict:
    """key -> course title, built from the roster plus the legacy aliases.

    Composed so earlier sources win: an entry's explicit `workbook_keys`, then the
    legacy aliases (filtered to courses still in the roster), then keys derived from
    each course's slug and title. A course registered through the UI needs no alias,
    because the API names its uploaded workbook `<title> - Course Contents.xlsx`, whose
    stem IS the derived title key.
    """
    from config.constants import COURSES

    out: dict = {}
    roster = dict(COURSES.items())
    titles = {m["title"] for m in roster.values()}
    for meta in roster.values():
        for k in (meta.get("workbook_keys") or []):
            out.setdefault(norm_stem(k), meta["title"])
    for k, title in LEGACY_WORKBOOK_KEYS.items():
        if title in titles:
            out.setdefault(k, title)
    for slug, meta in roster.items():
        out.setdefault(norm_stem(slug), meta["title"])
        out.setdefault(norm_stem(meta["title"]), meta["title"])
    return {k: v for k, v in out.items() if k}


def course_for_workbook_detail(workbook: str) -> tuple:
    """(title, reason). An empty title is always a problem the caller must report.

    Ambiguity resolves to "" as well: two courses matching one filename is exactly the
    phantom-course risk that returning "" exists to prevent, and guessing between them
    would attribute a whole course's tool declarations to the wrong place.
    """
    stem = norm_stem(workbook)
    if not stem:
        return "", "filename normalises to nothing"
    keys = workbook_keys()
    if stem in keys:
        return keys[stem], ""
    hits = set()
    for key in sorted(keys, key=len, reverse=True):
        if len(key) < _MIN_SUBSTRING_KEY:
            continue                      # short keys match by equality only
        if key in stem or stem in key:
            hits.add(keys[key])
    if not hits:
        return "", "maps to no course in the roster"
    if len(hits) > 1:
        return "", f"ambiguous: matches {' and '.join(sorted(hits))}"
    return hits.pop(), ""


def course_for_workbook(workbook: str) -> str:
    """The course a workbook belongs to, or "" when it maps to none.

    Returning "" is deliberate and callers must treat it as a problem to report: a
    fallback to the workbook's own name is what invented the phantom courses.
    """
    return course_for_workbook_detail(workbook)[0]


def _norm_header(v) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


@dataclass
class SheetTool:
    name: str
    taught_version: str = ""
    qualifier: str = ""
    session: str = ""
    sheet: str = ""
    workbook: str = ""


@dataclass
class SheetStats:
    workbooks: int = 0
    sheets_scanned: int = 0
    sheets_with_tools: int = 0
    rows: int = 0
    tools: int = 0
    pins: int = 0
    errors: list[str] = field(default_factory=list)


def parse_tool_cell(cell: str) -> Iterator[tuple[str, str, str]]:
    """Yield (name, version, qualifier) from a flat tiered tool cell."""
    for line in str(cell or "").splitlines():
        line = line.strip()
        if not line or _URL_LINE.match(line):
            continue
        for raw in line.split(","):
            item = raw.strip()
            if not item:
                continue
            qual = ""
            m = _QUALIFIER.search(item)
            if m:
                qual = m.group(0).strip().strip("()")
                item = _QUALIFIER.sub("", item).strip()
            ver = ""
            m = _PIN.search(item)
            if m:
                ver = m.group(1)
                item = _PIN.sub("", item).strip()
            if item.lower() in NOT_A_TOOL or len(item) < 2:
                continue
            yield item, ver, qual


def read_workbook(path: str | Path, stats: SheetStats) -> list[SheetTool]:
    if openpyxl is None:
        stats.errors.append("openpyxl not installed; sheet input skipped")
        return []
    try:
        # NOT read_only: the hand-built workbooks have unsized sheets and read-only
        # mode raises on calculate_dimension() for them.
        wb = openpyxl.load_workbook(str(path), data_only=True)
    except Exception as exc:
        stats.errors.append(f"{Path(path).name}: {type(exc).__name__}: {exc}")
        return []

    stats.workbooks += 1
    out: list[SheetTool] = []
    for ws in wb.worksheets:
        stats.sheets_scanned += 1
        rows = ws.iter_rows(values_only=True)
        try:
            header = [_norm_header(c) for c in next(rows)]
        except StopIteration:
            continue
        tool_cols = [i for i, h in enumerate(header) if h in TOOL_HEADERS]
        if not tool_cols:
            continue
        # "Entity Name" is a tool column only when there is no better one; on the
        # entity sheets it holds the session name instead.
        if len(tool_cols) > 1:
            tool_cols = [i for i in tool_cols if header[i] != "entity name"] or tool_cols
        sess_col = next((i for i, h in enumerate(header)
                         if h in SESSION_HEADERS and i not in tool_cols), None)
        stats.sheets_with_tools += 1

        for row in rows:
            stats.rows += 1
            session = str(row[sess_col]).strip() if (
                sess_col is not None and sess_col < len(row) and row[sess_col]) else ""
            for ci in tool_cols:
                if ci >= len(row) or not row[ci]:
                    continue
                for name, ver, qual in parse_tool_cell(row[ci]):
                    out.append(SheetTool(name=name, taught_version=ver, qualifier=qual,
                                         session=session, sheet=ws.title,
                                         workbook=Path(path).name))
                    stats.tools += 1
                    if ver:
                        stats.pins += 1
    return out


def read_all(paths: Iterable[str | Path]) -> tuple[list[SheetTool], SheetStats]:
    stats = SheetStats()
    tools: list[SheetTool] = []
    for p in paths:
        if Path(p).exists():
            tools += read_workbook(p, stats)
        else:
            stats.errors.append(f"missing workbook: {p}")
    return tools, stats
