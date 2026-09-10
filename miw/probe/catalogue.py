"""HTML table reading with column roles — the structure that makes proximity bugs impossible.

Vendors publish what they serve, and what they have retired, as tables. Reading those
tables as *text* does not work, and the failure is not subtle. On Groq's deprecations
page the id `llama-3.3-70b-versatile` appears eight times: once in the
`Deprecated Model` column, and **seven times** in the
`Recommended Replacement Model ID` column, because it was the successor to seven older
models. A text search for "is this id on the deprecations page" therefore fires eight
times and is wrong seven of them — and before August 2026 it would have fired purely as
a replacement, while the model was perfectly healthy.

On Google's models page the same class of bug appears differently: `gemini-2.0-flash`
sits about ten characters before "Gemini 2.0 Flash-Lite (Shut down)", so proximity
reports the wrong model as retired.

So this module never looks at a character offset in page text. It segments
`<table>` → `<tr>` → `<td>`, binds each column to a *role* by reading the header text,
and reads a status only from the row the id actually occupies. Two consequences worth
stating:

* A cell's meaning comes from its column, so the same id can legitimately be
  "deprecated" in one row and "the replacement" in another on one page.
* If no table can be role-typed, the result is `supported=False` — an explicit "this
  vendor publishes no such thing here", never a fallback to text search. That is what
  keeps *we could not parse it* from becoming *it is gone*.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Optional

# --- markup segmentation ----------------------------------------------------

_TABLE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.S | re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_TH = re.compile(r"<th\b[^>]*>(.*?)</th>", re.S | re.I)
_TD = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
_CODE = re.compile(r"<(?:code|pre|kbd|tt)\b[^>]*>(.*?)</(?:code|pre|kbd|tt)>", re.S | re.I)
_CAPTION = re.compile(r"<caption\b[^>]*>(.*?)</caption>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(fragment: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", fragment or ""))).strip()


# A machine identifier: lowercase, and carrying a hyphen or a namespace slash. Groq's
# models table writes display name and id in one cell with no code span —
# "GPT OSS 120B openai/gpt-oss-120b" — so the id has to be picked out of the prose.
# Requiring lowercase plus a separator is what stops "Gemini 2.0 Flash" or "Shut down"
# being mistaken for an id.
_ID_TOKEN = re.compile(r"(?<![\w./-])([a-z][a-z0-9]*(?:[./-][a-z0-9][\w.]*)+)(?![\w/-])")


@dataclass(frozen=True)
class Cell:
    text: str
    # `<code>` contents kept verbatim and separately: vendors put the exact model id in
    # a code span (Google's Endpoint column), while the surrounding prose is a label.
    code: tuple[str, ...] = ()

    def ids(self) -> tuple[str, ...]:
        """Candidate identifiers in this cell.

        Code spans win outright when present — they are the vendor marking the id
        explicitly. Otherwise fall back to id-shaped tokens in the prose, and only if
        neither yields anything treat the whole cell as the identifier (which is the
        case for a plain single-id cell).
        """
        if self.code:
            return self.code
        toks = tuple(m.group(1) for m in _ID_TOKEN.finditer(self.text))
        if toks:
            return toks
        return (self.text,) if self.text else ()


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]
    headers: tuple[str, ...]
    row_text: str

    def cell_for(self, role_index: Optional[int]) -> Optional[Cell]:
        if role_index is None or role_index >= len(self.cells):
            return None
        return self.cells[role_index]


# --- column roles -----------------------------------------------------------

# Header text -> role. Verified against Groq's deprecations table
# ('Deprecated Model' / 'Shutdown Date' / 'Recommended Replacement Model ID') and
# Google's models table ('Model' / 'Description' / 'Endpoint').
ROLE_LEXICON: dict[str, tuple[str, ...]] = {
    "id": ("model id", "model", "endpoint", "deprecated model", "name", "node",
           "node type", "package", "identifier", "legacy model", "current model"),
    "status": ("status", "state", "availability", "lifecycle"),
    "replacement": ("recommended replacement model id", "recommended replacement",
                    "replacement model id", "replacement", "successor",
                    "migrate to", "recommended model", "use instead", "alternative"),
    "date": ("shutdown date", "retirement date", "deprecation date", "end of life",
             "eol", "sunset date", "removal date", "date"),
    # Price and rate-limit columns say who may still call a model. Groq lists
    # `llama-3.3-70b-versatile` in BOTH its deprecation table and its live models
    # table; what separates "gone" from "moved to enterprise" is that the live row's
    # price and rate-limit cells read `Contact Sales` where every other model carries a
    # real per-token figure. Purely additive: every header on both vendors' pages
    # previously resolved to None for these.
    "price": ("price per 1m tokens", "price", "pricing", "cost", "price per 1m"),
    "rate_limit": ("rate limits (developer plan)", "rate limits", "rate limit"),
}

# Price wordings that mean "not self-serve" - a model you cannot start calling with a
# free or developer key. Deliberately a small, closed vocabulary of things vendors
# actually print, for the same reason the free-tier check reads LOSS of known wording
# rather than trying to enumerate every paid phrasing.
QUOTED_PRICING = ("contact sales", "contact us", "talk to sales", "custom pricing",
                  "enterprise only", "on request", "request a quote")


def _role_of(header: str) -> Optional[str]:
    h = header.strip().lower()
    if not h:
        return None
    for role, names in ROLE_LEXICON.items():
        if h in names:
            return role
    # Fall back to containment, longest lexicon entry first so "deprecated model"
    # is preferred over the bare "model".
    best: Optional[tuple[int, str]] = None
    for role, names in ROLE_LEXICON.items():
        for n in names:
            if n in h and (best is None or len(n) > best[0]):
                best = (len(n), role)
    return best[1] if best else None


@dataclass
class Table:
    headers: tuple[str, ...]
    rows: tuple[Row, ...]
    roles: dict[str, int] = field(default_factory=dict)
    # Several columns can name the same thing: Google's table is
    # `Model | Description | Endpoint`, where Model holds the display name carrying the
    # status parenthetical and Endpoint holds the exact id in a <code> span. Both map to
    # the `id` role, so keep every one of them.
    id_columns: tuple[int, ...] = ()
    caption: str = ""

    def identifier_cell(self, row: Row) -> Optional[Cell]:
        """The cell holding the machine identifier, preferring a <code> span.

        A display name ("Gemini 2.0 Flash (Shut down)") is not an id, and matching
        against it would never align with what the curriculum actually writes.
        """
        cands = [row.cell_for(i) for i in (self.id_columns or (self.roles.get("id"),))]
        cands = [c for c in cands if c and (c.text or c.code)]
        if not cands:
            return None
        coded = [c for c in cands if c.code]
        return coded[-1] if coded else cands[0]

    def status_cells(self, row: Row) -> list[Cell]:
        """Every id-role cell in the row, since any of them may carry the status."""
        out = [row.cell_for(i) for i in (self.id_columns or (self.roles.get("id"),))]
        return [c for c in out if c]

    @property
    def role(self) -> str:
        """What this table is: a retirement list, an availability list, or neither."""
        id_header = (self.headers[self.roles["id"]].lower()
                     if "id" in self.roles and self.roles["id"] < len(self.headers)
                     else "")
        retiring = ("deprecat" in id_header or "legacy" in id_header
                    or "date" in self.roles or "replacement" in self.roles)
        if "id" in self.roles and retiring:
            return "deprecation"
        if "id" in self.roles:
            return "availability"
        return "unknown"


def tables(html_text: str) -> list[Table]:
    """Every role-typed table in the document, in source order."""
    out: list[Table] = []
    for body in _TABLE.findall(html_text or ""):
        headers = tuple(_text(h) for h in _TH.findall(body))
        rows: list[Row] = []
        for tr in _ROW.findall(body):
            if _TH.search(tr) and not _TD.search(re.sub(_TH.pattern, "", tr, flags=re.S | re.I)):
                continue                      # header row
            raw_cells = _TD.findall(tr)
            if not raw_cells:
                continue
            cells = tuple(Cell(text=_text(c),
                               code=tuple(_text(x) for x in _CODE.findall(c) if _text(x)))
                          for c in raw_cells)
            if not any(c.text or c.code for c in cells):
                continue
            rows.append(Row(cells=cells, headers=headers,
                            row_text=" | ".join(c.text for c in cells)))
        if not rows:
            continue
        roles: dict[str, int] = {}
        id_cols: list[int] = []
        for i, h in enumerate(headers):
            r = _role_of(h)
            if not r:
                continue
            if r == "id":
                id_cols.append(i)
            if r not in roles:
                roles[r] = i
        cap = _CAPTION.search(body)
        out.append(Table(headers=headers, rows=tuple(rows), roles=roles,
                         id_columns=tuple(id_cols),
                         caption=_text(cap.group(1)) if cap else ""))
    return out


# --- status reading ---------------------------------------------------------

# A status parenthetical must terminate the cell — Google writes
# "Gemini 2.0 Flash-Lite (Shut down)". Anchored so it cannot match a word mid-sentence.
_STATUS_PAREN = re.compile(
    r"\(\s*(shut ?down|deprecated|retired|legacy|discontinued|removed|sunset|"
    r"preview|experimental|beta)\s*\)\s*$", re.I)

RETIRED = {"shut down", "shutdown", "deprecated", "retired", "discontinued",
           "removed", "sunset"}


def _normalise_status(raw: str) -> str:
    s = raw.strip().lower().replace("-", " ")
    return "shut down" if s in ("shutdown", "shut down") else s


def status_in_row(row: Row, table: Table, id_cell: Cell) -> str:
    """The status of this row's identifier, read only from this row.

    Priority: an explicit status cell, then a parenthetical anchored to the end of any
    id-role cell in this row, then the table's own role. Never the surrounding page,
    and never a neighbouring row.
    """
    cell = row.cell_for(table.roles.get("status"))
    if cell and cell.text:
        return _normalise_status(cell.text)
    for c in table.status_cells(row):
        m = _STATUS_PAREN.search(c.text)
        if m:
            return _normalise_status(m.group(1))
    return "deprecated" if table.role == "deprecation" else "available"


# --- the extracted record ---------------------------------------------------

@dataclass
class CatalogueEntry:
    entry_id: str
    status: str                       # available | deprecated | shut down | preview | ...
    replacement_ids: list[str] = field(default_factory=list)
    shutdown_date: str = ""
    table_role: str = ""
    column_role: str = "id"           # which column this id was found in
    quote: str = ""                    # the verbatim row - this IS the evidence
    evidence_url: str = ""
    price: str = ""                   # verbatim price cell, "" when the table has none
    rate_limit: str = ""              # verbatim rate-limit cell

    # Set by `build_catalogue` when a vendor lists the same id in BOTH a retirement
    # table and a live availability table. Keeping the second sighting is the whole
    # point: collapsing them is how "Groq moved this to enterprise" became "Groq shut
    # this down", which a reviewer disproves in one click on the vendor's own page.
    still_listed: bool = False
    listed_price: str = ""
    listed_quote: str = ""
    listed_url: str = ""

    @property
    def retired(self) -> bool:
        return self.status in RETIRED

    @property
    def quoted_only(self) -> bool:
        """Is the listed price a "come and talk to us" rather than a number?"""
        blob = f"{self.listed_price or self.price} {self.rate_limit}".casefold()
        return any(p in blob for p in QUOTED_PRICING)

    @property
    def tier_restricted(self) -> bool:
        """Retired on the developer plan, yet still served under a quote-only tier.

        Both halves are required. Groq lists `minimaxai/minimax-m2.7` at `Contact
        Sales` too, and it appears in no deprecation table - that is a pricing tier,
        not a retirement, and must raise nothing.
        """
        return self.retired and self.still_listed and self.quoted_only


_SPLIT_REPL = re.compile(r"\s+or\s+|\s*,\s*|\s*/\s(?=[a-z])|\s{2,}", re.I)


def split_replacements(text: str) -> list[str]:
    """"openai/gpt-oss-120b or qwen/qwen3.6-27b" -> both ids.

    Deliberately does not split on a bare "/" — provider-namespaced ids such as
    `openai/gpt-oss-120b` contain one.
    """
    out = []
    for part in _SPLIT_REPL.split(text or ""):
        p = part.strip().strip(".,;")
        if p and len(p) > 2 and " " not in p:
            out.append(p)
    return out


def entries(html_text: str, *, evidence_url: str = "") -> list[CatalogueEntry]:
    """Every identifier the document lists, tagged with the column role it appeared in.

    Ids found in a `replacement` column are returned with `column_role="replacement"`
    so a caller can never mistake "this was the successor to something" for "this is
    retired". That distinction is the entire point of the module.
    """
    out: list[CatalogueEntry] = []
    for table in tables(html_text):
        if table.role == "unknown":
            continue
        repl_i = table.roles.get("replacement")
        date_i = table.roles.get("date")
        price_i = table.roles.get("price")
        rate_i = table.roles.get("rate_limit")
        for row in table.rows:
            id_cell = table.identifier_cell(row)
            repl_cell = row.cell_for(repl_i)
            date_cell = row.cell_for(date_i)
            price_cell = row.cell_for(price_i)
            rate_cell = row.cell_for(rate_i)
            replacements = split_replacements(repl_cell.text) if repl_cell else []
            date = date_cell.text if date_cell else ""
            price = price_cell.text if price_cell else ""
            rate = rate_cell.text if rate_cell else ""

            if id_cell:
                status = status_in_row(row, table, id_cell)
                for ident in id_cell.ids():
                    ident = _STATUS_PAREN.sub("", ident).strip()
                    if not ident:
                        continue
                    out.append(CatalogueEntry(
                        entry_id=ident, status=status, replacement_ids=replacements,
                        shutdown_date=date, table_role=table.role, column_role="id",
                        quote=row.row_text, evidence_url=evidence_url,
                        price=price, rate_limit=rate))
            # The replacement column's own ids, explicitly marked as such.
            for ident in replacements:
                out.append(CatalogueEntry(
                    entry_id=ident, status="available", table_role=table.role,
                    column_role="replacement", quote=row.row_text,
                    evidence_url=evidence_url))
    return out
