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
