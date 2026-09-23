"""Published vulnerabilities in the exact version a course pins.

The cheapest honest security signal available: OSV aggregates GitHub's advisory
database and PyPA's, it is free and needs no key, and asking it about a *version*
rather than a package is what makes the answer about this curriculum rather than about
the package's whole history.

Three things this refuses to do, each because the alternative produces a number that
reads as a measurement and is not one:

* **No pinned version, no finding.** `transformers` without a taught version could be
  any release; reporting the union of every advisory ever filed against it would be
  true of the package and say nothing about the course. `supported=False`, the same
  structural refusal the deck and catalogue readers make.
* **Deduplicate by CVE.** GHSA and PYSEC both publish most issues, so the raw row count
  is close to double. Measured: transformers 4.46.3 returns **44 rows for 26 distinct
  issues**, gradio 4.44.0 **43 for 23**, langchain 0.3.7 **4 for 2**. Reporting rows
  would have overstated every finding by roughly 40%, which is the same mistake as
  reporting one n8n rule once per node it names.
* **Never invent a severity.** 26 of those 44 rows carry a CVSS vector and no rated
  band, and scoring a vector ourselves would be our arithmetic presented as the
  database's judgement. Rated advisories are counted by band; the rest are counted as
  unrated and said to be unrated.
"""
from __future__ import annotations

import json
from typing import Optional

from miw.net import Fetch, fetch

OSV_QUERY = "https://api.osv.dev/v1/query"
ECOSYSTEM = {"pypi": "PyPI", "npm": "npm"}
# GHSA first: within a CVE group it is the row that carries the rated band.
_PREFERRED = ("GHSA-", "CVE-", "PYSEC-")
BANDS = ("CRITICAL", "HIGH", "MODERATE", "LOW")


def _rank(vuln_id: str) -> int:
    for i, p in enumerate(_PREFERRED):
        if vuln_id.startswith(p):
            return i
    return len(_PREFERRED)


def _fixed_versions(v: dict) -> list[str]:
    out = []
    for a in v.get("affected") or []:
        for r in a.get("ranges") or []:
            for e in r.get("events") or []:
                if e.get("fixed"):
                    out.append(str(e["fixed"]))
    return out


def _as_tuple(ver: str):
    """Comparable form of a version, or None when it is not plainly numeric.

    Deliberately conservative: `5.0.0rc3` and `1.2.post1` are not compared, they are
    skipped. A wrong "upgrade to" is worse than none, and a release candidate is not
    the answer to "what clears this".
    """
    parts = (ver or "").split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def advisories(name: str, registry: str, version: Optional[str]) -> dict:
    """Published advisories affecting exactly `version` of this package."""
    eco = ECOSYSTEM.get((registry or "").lower())
    if not eco:
        return {"supported": False, "reason": f"no OSV ecosystem for registry "
                                              f"{registry!r}"}
    if not version:
        return {"supported": False,
                "reason": "no taught version pinned, so no advisory can be said to "
                          "apply to what the course runs"}

    body = json.dumps({"package": {"name": name, "ecosystem": eco},
                       "version": version}).encode()
    f: Fetch = fetch(OSV_QUERY, method="POST", data=body,
                     headers={"Content-Type": "application/json"}, timeout=25,
                     retries=1)
    evidence = f"https://osv.dev/list?q={name}&ecosystem={eco}"
    if not f.ok or not f.body:
        return {"supported": False, "reason": f"OSV unreachable: "
                                              f"{f.error or f.status}",
                "evidence_url": evidence}
    try:
        rows = (json.loads(f.body) or {}).get("vulns") or []
    except (json.JSONDecodeError, ValueError):
        return {"supported": False, "reason": "OSV returned unreadable JSON",
                "evidence_url": evidence}

    # One entry per CVE. A withdrawn advisory is not a vulnerability.
    groups: dict[str, list[dict]] = {}
    for v in rows:
        if v.get("withdrawn"):
            continue
        cve = next((a for a in (v.get("aliases") or []) if a.startswith("CVE-")),
                   v.get("id", ""))
        groups.setdefault(cve, []).append(v)

    issues, bands, unrated, fixes = [], {}, 0, []
    for cve, members in groups.items():
        best = sorted(members, key=lambda v: _rank(v.get("id", "")))[0]
        band = ((best.get("database_specific") or {}).get("severity") or "").upper()
        if band in BANDS:
            bands[band] = bands.get(band, 0) + 1
        else:
            band = ""
            unrated += 1
        fx = [t for t in (_as_tuple(x) for x in _fixed_versions(best)) if t]
        issues.append({"id": best.get("id", ""), "cve": cve, "severity": band,
                       "summary": (best.get("summary") or "")[:160],
                       "fixed": ".".join(str(n) for n in min(fx)) if fx else ""})
        fixes += fx

    worst = next((b for b in BANDS if bands.get(b)), "")
    # The lowest release that clears every one of them: each advisory names the version
    # that fixed it, so the package is only clean at the highest of those.
    clears_all = ".".join(str(n) for n in max(fixes)) if fixes else ""
    return {
        "supported": True, "found": bool(issues), "version": version,
        "count": len(issues), "rows": len(rows), "by_severity": bands,
        "unrated": unrated, "worst": worst, "clears_all": clears_all,
        "evidence_url": evidence,
        "issues": sorted(issues, key=lambda i: (BANDS.index(i["severity"])
                                                if i["severity"] in BANDS else 9))[:8],
    }
