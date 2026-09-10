"""Read the planned pages. Fully deterministic — this is where facts come from.

Every page is put through **both** of MIW's extractors, because they read different
shapes and the important pages are often the shape the prose reader refuses:

* `research.official.gather_url` quotes PROSE — a sentence that names the subject and
  carries a deprecation or pricing marker.
* `probe.catalogue.entries` reads TABLES by column role, and each row's verbatim text
  *is* the evidence.

Wiring only the first was a measured mistake. Groq states its retirements as
`August 16, 2026: llama-3.1-8b-instant and llama-3.3-70b-versatile`, which
`official._is_prose` correctly refuses, so a live run against exactly the right page
returned 0 claims *and* 0 rejections — the line never became a candidate. The scheduled
pipeline gets that fact through `vendors/groq.py` and the column-role parser, so the
agent had to as well or it could only ever find the easy half.

Two rules the table path inherits rather than reinvents:

* **Exact, case-folded id match, never substring.** The inventory holds
  `gemini-2.0-flash`, `gemini-2.0-flash-lite` and `gemini-3.1-flash-lite-preview`; a
  substring rule would implicate all three from one row. This mirrors
  `vendors.Catalogue.get`.
* **`Claim.build` still decides.** A row becomes evidence only if it survives the
  citation gate and `trust.classify` rates the page authoritative for this subject, so
  a table claim is no weaker than a prose one.

The agent chose the URL. It did not read the page, and it cannot influence what this
node extracts.
"""
from __future__ import annotations

from typing import Optional

from langchain_core.runnables import RunnableConfig

from miw.schema import ClaimKind, to_jsonable

# Which claim kind a catalogue row can settle, given what the row actually carries.
_RETIRED_KIND = ClaimKind.DEPRECATION
_PRICED_KIND = ClaimKind.PRICING
_LISTED_KIND = ClaimKind.AVAILABILITY


def _table_claims(dep, url: str, html: str, wanted: set) -> tuple[list, list[str]]:
    """Claims from any catalogue row naming this exact id. Returns (claims, notes)."""
    from miw.probe.catalogue import entries as catalogue_entries
    from miw.schema import Claim, UncitedClaim

    try:
        rows = catalogue_entries(html, evidence_url=url)
    except Exception as exc:                      # a malformed table is not a crash
        return [], [f"{url}: table parse failed ({type(exc).__name__})"]
    if not rows:
        return [], []

    ids = {t.casefold() for t in
           {dep.canonical_name, dep.registry_id, *dep.aliases} if t}
    subject = dep.subject()
    out, notes = [], []
    for e in rows:
        if (e.entry_id or "").casefold() not in ids:
            continue                              # exact match only
        if e.retired:
            kind, said = _RETIRED_KIND, f"is listed as {e.status}"
        elif e.price or e.rate_limit:
            kind, said = _PRICED_KIND, "appears in a table with plan or quota columns"
        else:
            kind, said = _LISTED_KIND, f"is listed as {e.status}"
        if kind not in wanted:
            continue
        when = f", shutdown {e.shutdown_date}" if e.shutdown_date else ""
        try:
            out.append(Claim.build(
                kind=kind, subject=subject, source_url=url, quote=e.quote,
                statement=(f"{dep.canonical_name} {said} in a table on the vendor's "
                           f"own page{when}")))
        except UncitedClaim as exc:
            # The commonest cause is a one-word row: `Claim.build` refuses a quote
            # under 12 characters, and a label is not evidence of anything.
            notes.append(f"{url}: table row for {e.entry_id} not citable ({exc})")
    return out, notes


def read_pages_node(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """`dep` and `fetcher` arrive through `config["configurable"]`, not on the state.

    They cannot live on the state: LangGraph serialises state into the checkpoint, and a
    `Dependency` object and an injected fetcher function are not msgpack-serialisable —
    the checkpointer test failed loudly on exactly that. Config is the idiomatic place
    for a runtime dependency, and it keeps the checkpointed state to plain data, which
    is also what makes a resumed run auditable.
    """
    from miw.net import fetch as net_fetch
    from miw.research.official import gather_url
    from miw.schema import ResearchResult

    cfg = (config or {}).get("configurable") or {}
    dep = cfg.get("dep")
    if dep is None:
        return {"status": "blocked", "errors": ["read: no dependency in config"]}

    # Injectable for the same reason `official.gather` takes one: without it the whole
    # evidence path needs the network, which means it does not get tested.
    fetcher = cfg.get("fetcher") or net_fetch
    urls = state.get("planned_urls") or []
    wanted = {getattr(ClaimKind, k) for k in (state.get("kinds") or ["DEPRECATION"])
              if hasattr(ClaimKind, k)} or {ClaimKind.DEPRECATION}

    res = ResearchResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name)
    table_claims, notes, read_ok = [], [], []
    for url in urls:
        got = fetcher(url)
        html = got.body if (got.ok and got.body) else ""
        if not html:
            res.unreadable.append(f"{url}: not readable (http {got.status or got.error})")
            continue
        read_ok.append(url)
        # One fetch, two readers. The prose reader is handed the response we already
        # have so the page is not requested twice.
        once = lambda u, _f=got, **kw: _f
        for kind in wanted:
            gather_url(dep, url, kind, result=res, fetcher=once)
        tc, tn = _table_claims(dep, url, html, wanted)
        table_claims += tc
        notes += tn

    prose = [c for c in res.claims if c.substantiating]
    tabled = [c for c in table_claims if c.substantiating]
    # Same page, same id, same kind read by both readers: keep one.
    seen, merged = set(), []
    for c in [*tabled, *prose]:
        key = (c.source_url, c.kind, c.quote[:80])
        if key in seen:
            continue
        seen.add(key)
        merged.append(c)

    return {
        "claims": [to_jsonable(c) for c in merged],
        "unreadable": list(res.unreadable),
        "refuted": list(res.refuted),
        "dropped": list(res.dropped) + notes,
        "pages_read": read_ok,
        "visited": urls,
        "planned_urls": [],
        "status": "verified" if merged else "reading",
        "trajectory": [f"read {len(urls)} page(s): {len(merged)} claim(s) "
                       f"({len(tabled)} from tables, {len(prose)} from prose), "
                       f"{len(res.unreadable)} unreadable"],
    }
