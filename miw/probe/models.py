"""Is a taught model id still served — and if not, what does its provider say to use?

The verification ladder, most specific first. What it deliberately does *not* do:

* **No inference call.** There is no key, and a 401 from an inference endpoint is a
  fact about our credentials, not about the model.
* **No proximity search.** Statuses come from `probe/catalogue.py`, bound to the row
  and column the id actually occupies.
* **No model.** Every step here is a membership test or a date comparison.

The provider is *discovered*, not assumed. `MODEL_VENDORS` in the extractor attributes
`llama-3.3-70b-versatile` to Meta on a prefix match, but Groq is who serves and retires
it. So each adapter is asked "does your own catalogue name this exact id" and the first
that says yes is the serving provider — self-verifying, and the basis on which
`subject_with_provider` widens authority.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from miw.probe.catalogue import parse_shutdown
from miw.schema import Dependency, ProbeResult, utcnow
from miw.vendors import Catalogue, adapters_for

# `parse_shutdown` moved to `probe/catalogue.py`, which now needs it to tell a
# retirement row from a still-current one. Re-exported here because it was part of this
# module's surface and the tests import it from both places.
__all__ = ["parse_shutdown", "probe_model_dependency"]


def _declared(adapter, entry, provider: str) -> dict:
    """The vendor's own statement about this id, in the shape `score.py` reads.

    Shared by the retirement path and the advance-warning path so the two cannot drift
    apart in what they hand downstream.
    """
    return {
        "rule_id": f"{adapter.key}:{entry.entry_id}",
        "n8n_version": "",                       # shared shape with the n8n path
        "title": f"{entry.entry_id} is {entry.status} on {provider}",
        "description": entry.quote,
        # No `severity` key, deliberately. n8n states a severity on each of its
        # own breaking-change rules and `score.py` rightly honours it; a model
        # provider states no such thing, so anything here would be OUR inference
        # wearing the vendor's authority - and it overrode the execution-aware
        # severity, putting one line of reading material at `critical`. The date
        # is the fact, and it already reaches scoring as the choice between
        # `model_shutdown_passed` and `model_deprecation_declared`.
        "doc_url": entry.evidence_url,
        "node_types": [entry.entry_id],
        "actions": [f"Replace with {r}" for r in entry.replacement_ids],
        "replacement_ids": entry.replacement_ids,
        "shutdown_date": entry.shutdown_date,
        "still_listed": entry.still_listed,
        "listed_price": entry.listed_price,
        "listed_quote": entry.listed_quote,
        "listed_url": entry.listed_url,
    }


_TERMS_SIGNALS = frozenset({"model_price_changed", "model_rate_limit_changed"})


def _terms_drift(dep: Dependency, adapter, entry, res: ProbeResult, state) -> None:
    """Did what this model COSTS, or how much of it you may use, change since last week?

    Both cells have been parsed since `probe/catalogue.py` was written and nothing ever
    compared them to the last run — they fed `quoted_only` and the agent path and were
    otherwise discarded. So a halved free quota was a fact the system fetched, parsed
    and threw away, every week, on the one signal class a student notices first.

    Quoted verbatim in both directions. `$0.04 per hour` -> `$0.08 per hour` is the
    evidence; a parsed number would be our arithmetic standing in for the vendor's page.
    """
    if state is None:
        return
    price = (entry.listed_price or entry.price or "").strip()
    rate = (entry.rate_limit or "").strip()
    if not (price or rate):
        return
    key = f"catalogue:{adapter.key}"
    prev = state.terms_prev(key, entry.entry_id)
    state.terms_save(source_key=key, entry_id=entry.entry_id, price=price,
                     rate_limit=rate, now=utcnow())
    if prev is None:
        return                 # first sight is a baseline, the rule every diff here uses
    was_price = (prev["price"] or "").strip()
    was_rate = (prev["rate_limit"] or "").strip()
    moved = []
    if price and was_price and price != was_price:
        res.flag("model_price_changed")
        moved.append(f"price {was_price!r} -> {price!r}")
    if rate and was_rate and rate != was_rate:
        res.flag("model_rate_limit_changed")
        moved.append(f"rate limit {was_rate!r} -> {rate!r}")
    if moved:
        if res.status == "ok":
            res.status = "changed"
        res.detail = (f"{res.provider or adapter.key} changed the terms on "
                      f"{entry.entry_id}: " + "; ".join(moved))


def probe_model_dependency(dep: Dependency, *, today: Optional[date] = None,
                           adapters=None, state=None) -> ProbeResult:
    """One deterministic observation of a taught model id."""
    today = today or date.today()
    res = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name)
    unreadable: list[str] = []

    for adapter in (adapters if adapters is not None else adapters_for("model")):
        cat: Catalogue = adapter.catalogue()
        if not cat.ok:
            unreadable.append(f"{adapter.key}: {cat.error}")
            continue
        if not cat.supported:
            # Fetched fine, parsed to nothing we can role-type - a page restructure.
            # Recorded, not skipped: this is the ONE failure mode that looks like
            # success. A 500 or a timeout already lands in `unreadable` and produces
            # `inconclusive`, but a 200 whose table has moved used to `continue`
            # silently, so every model from that vendor flipped to
            # `model_provider_unknown` and kept `status="ok"` - the same reading as a
            # healthy check, reported for as long as the page stayed restructured.
            unreadable.append(f"{adapter.key}: {cat.error or 'catalogue not readable'}")
            continue
        entry = cat.get(dep.canonical_name)
        if entry is None:
            continue                     # this provider does not serve this id

        # Found the serving provider, by its own catalogue naming the exact id.
        res.provider = getattr(adapter, "vendor", adapter.key)
        res.provider_domains = list(adapter.official_domains)
        res.evidence_url = entry.evidence_url
        res.checked_at = utcnow()

        when = parse_shutdown(entry.shutdown_date)
        # Terms drift is orthogonal to retirement: a model can be perfectly alive and
        # twice the price, and the early return below would have skipped the check.
        _terms_drift(dep, adapter, entry, res, state)

        if not entry.retired:
            # Still listed - but a vendor can announce a shutdown date for a model it
            # goes on serving, which is the whole of the advance warning this system
            # exists to give. Returning "ok" here the moment the status column looked
            # healthy is why `model_deprecation_declared` had never once fired in
            # production: the date was fetched, parsed, and then dropped one line
            # before it was read.
            if when and when > today:
                res.status = "changed"
                res.flag("model_deprecation_declared")
                res.detail = (f"{res.provider} still lists {entry.entry_id}, and has "
                              f"announced its shutdown for {entry.shutdown_date} "
                              f"({(when - today).days} days away)")
                res.declared_changes = [_declared(adapter, entry, res.provider)]
                if entry.replacement_ids:
                    res.flag("vendor_named_replacement")
                return res
            # `_terms_drift` above may already have set `changed`, and a model that is
            # still listed but twice the price is not `ok`. The flag is the finding;
            # this only stops the status contradicting it.
            res.status = "changed" if _TERMS_SIGNALS & set(res.signals) else "ok"
            res.flag("model_listed_available")
            return res

        res.declared_changes = [_declared(adapter, entry, res.provider)]

        if entry.tier_restricted:
            # The vendor says both things, and both are true: retired on the developer
            # plan, yet still served under a quote-only tier. Taught examples still
            # fail for a student on a free key, so this stays `broken` - but the REASON
            # is different, and saying "shut down" of an id the vendor still lists is
            # how a reviewer catches the digest being wrong.
            res.status = "broken"
            res.flag("model_tier_restricted")
            gone = f" on {entry.shutdown_date}" if entry.shutdown_date else ""
            res.detail = (
                f"{res.provider} lists {entry.entry_id} as {entry.status}{gone}, but "
                f"still offers it at \u201c{entry.listed_price or 'quote only'}\u201d — it "
                f"has left the developer plan rather than shut down")
        elif when and when <= today:
            # The date has passed: this is not a warning, it is a live outage.
            res.status = "broken"
            res.flag("model_shutdown_passed")
            res.detail = (f"{res.provider} lists {entry.entry_id} as {entry.status} with "
                          f"shutdown {entry.shutdown_date} — {(today - when).days} days ago")
        elif when:
            res.status = "changed"
            res.flag("model_deprecation_declared")
            res.detail = (f"{res.provider} lists {entry.entry_id} as {entry.status}, "
                          f"shutdown {entry.shutdown_date} "
                          f"({(when - today).days} days away)")
        else:
            res.status = "broken" if entry.status in ("shut down", "removed") else "changed"
            res.flag("model_shutdown_passed" if res.status == "broken"
                     else "model_deprecation_declared")
            res.detail = (f"{res.provider}'s own catalogue lists {entry.entry_id} as "
                          f"{entry.status}")
        if entry.replacement_ids:
            res.flag("vendor_named_replacement")
        return res

    # Nobody claimed it.
    res.checked_at = utcnow()
    if unreadable:
        # A provider page we could not read is never evidence that a model is gone.
        res.status = "inconclusive"
        res.flag("model_catalogue_unreadable")
        res.detail = "; ".join(unreadable)[:200]
    else:
        res.status = "ok"
        res.flag("model_provider_unknown")
        res.detail = ("no configured provider lists this id; its serving provider has "
                      "no adapter yet, so nothing can be asserted about it")
    return res


def verified_replacements(res: ProbeResult, adapters=None) -> list[dict]:
    """Replacements the same provider still lists as available.

    A successor the vendor names is only useful if the vendor is actually serving it, so
    each candidate is checked back against the same catalogue. This is why the Groq case
    needs no search key: the fix is one row away from the problem.
    """
    out: list[dict] = []
    for change in res.declared_changes:
        for rid in change.get("replacement_ids") or []:
            for adapter in (adapters if adapters is not None else adapters_for("model")):
                if getattr(adapter, "vendor", adapter.key) != res.provider:
                    continue
                cat = adapter.catalogue()
                entry = cat.get(rid)
                if entry and not entry.retired:
                    out.append({"name": rid, "homepage": entry.evidence_url,
                                "quote": entry.quote, "status": entry.status})
                break
    return out
