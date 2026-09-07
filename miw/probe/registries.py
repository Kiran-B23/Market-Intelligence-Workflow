"""Canonical registry probes: PyPI, npm, GitHub, n8n.

These are the cheapest and most trustworthy signals in the system — machine-readable
answers straight from the source of record, needing no LLM and no interpretation.
Under `miw.trust` these hosts are AUTHORITATIVE, but only within their remit: PyPI
settles a version question and says nothing about pricing.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from miw.net import Fetch, fetch


def _json(url: str) -> tuple[Optional[dict], Fetch]:
    f = fetch(url, timeout=20, retries=1)
    if not f.ok or not f.body:
        return None, f
    try:
        return json.loads(f.body), f
    except (json.JSONDecodeError, ValueError):
        return None, f


def pypi(name: str) -> dict:
    """Latest version, release date, and yank status for a PyPI distribution."""
    url = f"https://pypi.org/pypi/{name}/json"
    data, f = _json(url)
    if data is None:
        return {"found": False, "http_status": f.status, "error": f.error,
                "evidence_url": url, "reachable": f.reachable}
    info = data.get("info") or {}
    version = info.get("version") or ""
    releases = data.get("releases") or {}
    files = releases.get(version) or []
    return {
        "found": True, "evidence_url": f"https://pypi.org/project/{name}/",
        "latest_version": version,
        "released_at": (files[0].get("upload_time_iso_8601") or "") if files else "",
        "yanked": bool(files and all(x.get("yanked") for x in files)),
        "summary": (info.get("summary") or "")[:300],
        "project_urls": info.get("project_urls") or {},
        "home_page": info.get("home_page") or "",
        "requires_python": info.get("requires_python") or "",
        "reachable": True,
    }


def npm(name: str) -> dict:
    url = f"https://registry.npmjs.org/{name.replace('/', '%2F')}"
    data, f = _json(url)
    if data is None:
        return {"found": False, "http_status": f.status, "error": f.error,
                "evidence_url": url, "reachable": f.reachable}
    latest = ((data.get("dist-tags") or {}).get("latest")) or ""
    return {
        "found": True, "evidence_url": f"https://www.npmjs.com/package/{name}",
        "latest_version": latest,
        "released_at": (data.get("time") or {}).get(latest, ""),
        "deprecated": bool(((data.get("versions") or {}).get(latest) or {}).get("deprecated")),
        "reachable": True,
    }


def github_repo(owner_repo: str) -> dict:
    """Archived flag and last push for a repo. Unauthenticated; rate-limited by IP."""
    url = f"https://api.github.com/repos/{owner_repo}"
    data, f = _json(url)
    if data is None:
        return {"found": False, "http_status": f.status, "error": f.error,
                "evidence_url": f"https://github.com/{owner_repo}",
                "reachable": f.reachable, "rate_limited": f.status == 403}
    return {
        "found": True, "evidence_url": data.get("html_url") or f"https://github.com/{owner_repo}",
        "archived": bool(data.get("archived")), "disabled": bool(data.get("disabled")),
        "pushed_at": data.get("pushed_at") or "", "open_issues": data.get("open_issues_count"),
        "description": (data.get("description") or "")[:300],
        "reachable": True,
    }


N8N_RELEASES = "https://api.github.com/repos/n8n-io/n8n/releases?per_page=5"


def n8n_latest() -> dict:
    """Latest n8n release. n8n publishes releases on GitHub, which is its own repo."""
    data, f = _json(N8N_RELEASES)
    if not isinstance(data, list) or not data:
        return {"found": False, "http_status": f.status, "error": f.error,
                "evidence_url": "https://docs.n8n.io/release-notes/",
                "reachable": f.reachable}
    # n8n publishes a rolling "n8n@stable" tag alongside versioned releases, so take
    # the first release whose tag actually carries a version number.
    rel = next((r for r in data if re.search(r"\d+\.\d+", r.get("tag_name") or "")), data[0])
    # Anchor on the `@` or a `v` prefix. A bare `(\d+[\w.]*)` matched "8n" out of the
    # tag "n8n@2.38.4", so every version comparison downstream was comparing garbage
    # and S9 silently never fired.
    ver = re.search(r"(?:@|\bv)(\d+\.\d+(?:\.\d+)?[\w.\-]*)", rel.get("tag_name") or "")
    return {
        "found": True, "latest_version": ver.group(1) if ver else "",
        "released_at": rel.get("published_at") or "",
        "evidence_url": rel.get("html_url") or "https://docs.n8n.io/release-notes/",
        "body": (rel.get("body") or "")[:4000],
        "reachable": True,
    }
