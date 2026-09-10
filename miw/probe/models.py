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

from miw.schema import Dependency, ProbeResult, utcnow
from miw.vendors import Catalogue, adapters_for

# Groq writes 08/16/26; others use ISO or a spelled month.
_DATE_FORMS = ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y")


def parse_shutdown(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _DATE_FORMS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return date(*(int(x) for x in m.groups()))
        except ValueError:
            return None
    return None


def probe_model_dependency(dep: Dependency, *, today: Optional[date] = None,
                           adapters=None) -> ProbeResult:
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
            continue
        entry = cat.get(dep.canonical_name)
        if entry is None:
            continue                     # this provider does not serve this id

        # Found the serving provider, by its own catalogue naming the exact id.
        res.provider = getattr(adapter, "vendor", adapter.key)
        res.provider_domains = list(adapter.official_domains)
        res.evidence_url = entry.evidence_url
        res.checked_at = utcnow()

        if not entry.retired:
            res.status = "ok"
            res.flag("model_listed_available")
            return res

        when = parse_shutdown(entry.shutdown_date)
        res.declared_changes = [{
            "rule_id": f"{adapter.key}:{entry.entry_id}",
            "n8n_version": "",                       # shared shape with the n8n path
            "title": f"{entry.entry_id} is {entry.status} on {res.provider}",
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
        }]

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
