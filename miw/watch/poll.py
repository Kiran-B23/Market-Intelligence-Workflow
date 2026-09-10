"""Daily polling: did a vendor move, and which taught artifacts does that touch?

This is the second of the two entry points. The manual one already exists — pick a
course or a session in the UI, or pass `--course`/`--session` on the command line. This
one runs unattended, notices that a vendor changed something, and resolves it to the
exact dependencies affected.

The resolution is why the context problem the operator raised does not arise. A signal
names identifiers; the inventory is an index keyed by those identifiers; matching is a
dict lookup over 464 entries. **No course content is read, and no model is called** —
9.2M characters of curriculum stay on disk while a signal turns into a list of
`dep_id`s. The model, if it runs at all, sees one finding's already-verified facts.
"""
from __future__ import annotations

from typing import Iterable, Optional

from miw.schema import Dependency, utcnow
from miw.scope import Scope
from miw.state import State
from miw.watch.signal import Signal, Watermark, row_set_hash


# --- watermark readers ------------------------------------------------------

def vendor_watermark(adapter) -> tuple[Optional[Watermark], list[dict], str]:
    """A vendor catalogue's meaning, hashed. Returns (watermark, rows, error)."""
    cat = adapter.catalogue()
    if not cat.usable:
        return None, [], cat.error or "catalogue unusable"
    rows = [{"id": e.entry_id, "status": e.status,
             "replacement_ids": e.replacement_ids, "shutdown_date": e.shutdown_date,
             "quote": e.quote, "doc_url": e.evidence_url}
            for e in cat.entries.values()]
    value = row_set_hash((r["id"], r["status"], ",".join(r["replacement_ids"]))
                         for r in rows)
    return (Watermark(source_key=f"{adapter.key}:catalogue", kind="row_set_hash",
                      value=value, evidence_url=(cat.sources or [""])[0]),
            rows, "")


def package_watermark(dep: Dependency) -> tuple[Optional[Watermark], list[dict], str]:
    """A package's published version, straight from its registry."""
    from miw.probe import registries as R
    if dep.registry not in ("pypi", "npm"):
        return None, [], "not a registry package"
    info = (R.pypi if dep.registry == "pypi" else R.npm)(
        dep.registry_id or dep.canonical_name)
    if not info.get("found"):
        return None, [], info.get("error") or f"http {info.get('http_status')}"
    version = info.get("latest_version") or ""
    if not version:
        return None, [], "registry returned no version"
    return (Watermark(source_key=f"{dep.registry}:{dep.registry_id or dep.canonical_name}",
                      kind="version", value=version,
                      evidence_url=info.get("evidence_url", "")),
            [{"id": dep.canonical_name, "status": "released", "version": version,
              "quote": f"{dep.canonical_name} {version} on {dep.registry}",
              "doc_url": info.get("evidence_url", "")}], "")


# --- the poll ---------------------------------------------------------------

def _compare(state: State, wm: Watermark, *, vendor_key: str, trigger: str,
             rows: list[dict], now: str) -> tuple[Optional[Signal], str]:
    """Emit a signal only if the watermark moved. First sight is a baseline."""
    prev = state.watermark(wm.source_key)
    prev_value = (prev["value"] if prev else "") or ""
    state.watermark_save(source_key=wm.source_key, kind=wm.kind, value=wm.value,
                         evidence_url=wm.evidence_url, now=now)
    if not prev_value:
        return Signal(vendor_key=vendor_key, source_key=wm.source_key,
                      trigger="baseline", from_value="", to_value=wm.value,
                      evidence_url=wm.evidence_url, observed_at=now), "baseline"
    if prev_value == wm.value:
        return None, "unchanged"
    return Signal(vendor_key=vendor_key, source_key=wm.source_key, trigger=trigger,
                  from_value=prev_value, to_value=wm.value,
                  evidence_url=wm.evidence_url, declared=rows,
                  refs=[r["id"] for r in rows], observed_at=now), "changed"


def poll_all(deps: Iterable[Dependency], state: State, *,
             vendors: bool = True, packages: bool = True,
             package_limit: Optional[int] = None,
             progress=None) -> tuple[list[Signal], dict]:
    """One pass over every watchable source derived from the inventory.

    The watchlist is derived, never curated: it comes from the inventory's own
    `official_domains` and `registry` fields, so it tracks what is taught rather than
    a list someone has to remember to update.
    """
    deps = list(deps)
    now = utcnow()
    signals: list[Signal] = []
    stats = {"vendor_sources": 0, "package_sources": 0, "baseline": 0,
             "unchanged": 0, "changed": 0, "unreadable": 0}

    if vendors:
        from miw.vendors import all_adapters
        for adapter in all_adapters():
            wm, rows, err = vendor_watermark(adapter)
            stats["vendor_sources"] += 1
            if wm is None:
                stats["unreadable"] += 1
                if progress:
                    progress(f"{adapter.key}:catalogue", "unreadable", err)
                continue
            sig, outcome = _compare(state, wm, vendor_key=adapter.key,
                                    trigger="deprecation", rows=rows, now=now)
            stats[outcome] += 1
            if progress:
                progress(wm.source_key, outcome, f"{len(rows)} rows")
            if sig:
                state.signal_save(sig, "baseline" if sig.is_baseline else "open", now)
                if not sig.is_baseline:
                    signals.append(sig)

    if packages:
        pkgs = [d for d in deps if d.registry in ("pypi", "npm")]
        if package_limit:
            pkgs = pkgs[:package_limit]
        for dep in pkgs:
            wm, rows, err = package_watermark(dep)
            stats["package_sources"] += 1
            if wm is None:
                stats["unreadable"] += 1
                continue
            sig, outcome = _compare(state, wm, vendor_key=dep.registry,
                                    trigger="new_release", rows=rows, now=now)
            stats[outcome] += 1
            if progress:
                progress(wm.source_key, outcome, wm.value)
            if sig:
                state.signal_save(sig, "baseline" if sig.is_baseline else "open", now)
                if not sig.is_baseline:
                    signals.append(sig)

    return signals, stats


# --- resolution -------------------------------------------------------------

def resolve_signal(sig: Signal, deps: Iterable[Dependency]) -> Scope:
    """Signal -> the exact dependencies it touches.

    Two deterministic steps, most specific first. Neither reads course content nor
    calls a model:

    1. **Exact identifier match** against `canonical_name`, aliases and `registry_id`.
       "Groq retired these two ids" resolves to those two dependencies and nothing else.
    2. **Vendor ownership**, only if step 1 found nothing — the dependencies whose
       authority set intersects the vendor's domains. That is the "n8n released 2.39.0,
       check the nodes we teach" case.
    """
    from miw.registry import norm
    deps = list(deps)
    wanted = {norm(r) for r in sig.refs if r}

    hit: set[str] = set()
    if wanted:
        for d in deps:
            names = {norm(d.canonical_name), norm(d.registry_id or "")}
            names |= {norm(a) for a in d.aliases}
            if names & wanted:
                hit.add(d.dep_id)
    if hit:
        return Scope(dep_ids=hit)

    from miw.vendors import all_adapters
    domains = set()
    for a in all_adapters():
        if a.key == sig.vendor_key:
            domains |= {x.lower() for x in a.official_domains}
    if domains:
        for d in deps:
            if {x.lower() for x in d.subject().official_domains} & domains:
                hit.add(d.dep_id)
    return Scope(dep_ids=hit)
