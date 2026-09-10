"""The planning node — the only place in the signal graph a model speaks.

It answers one question: *which of this vendor's own pages should we read next?* Its
entire output surface is a list of URLs on hosts we already trust for this subject, plus
a sentence of reasoning that is never treated as evidence.

Everything about the design follows from one measurement: across a real run, 61% of the
582 page fetches were 404s on guessed paths like `/docs/deprecations`, and 44.8 pages
were read per claim produced. The guesses are the problem, and choosing where to look is
judgement, not arithmetic — so it is the one job worth giving a model.

Two guards, both enforced by code in this file rather than by the prompt:

* **Host allowlist.** A URL not on `allowed_domains` is dropped into `rejected_urls`,
  not fetched. A vendor page that tries to redirect the agent elsewhere therefore fails
  closed, and a hallucinated domain cannot be reached at all.
* **No repeats.** Already-visited URLs are filtered out, which is what stops the
  replan loop proposing the same dead path forever.
"""
from __future__ import annotations

from urllib.parse import urlparse

from miw.llm import complete, load_prompt

MAX_URLS = 4


def _host_ok(url: str, allowed: list[str]) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in allowed)


def plan_pages_node(state: dict) -> dict:
    """Ask which pages to read. Returns only URLs, only on allowed hosts."""
    allowed = state.get("allowed_domains") or []
    attempt = int(state.get("attempt", 0)) + 1
    if not allowed:
        return {"status": "no_source", "attempt": attempt,
                "trajectory": ["plan: no official domain, nothing to read"]}

    visited = set(state.get("visited") or [])
    res = complete(load_prompt(
        "agent_plan_pages_v1",
        dependency=state.get("canonical_name", ""),
        kinds=", ".join(state.get("kinds") or ["DEPRECATION", "PRICING"]),
        domains=", ".join(allowed),
        attempt=attempt,
        already_tried=("\n".join(f"- {u}" for u in sorted(visited)) or "none yet"),
        unreadable=("\n".join(f"- {u}" for u in (state.get("unreadable") or [])[:8])
                    or "none yet"),
    ))
    if not res.ok:
        return {"status": "blocked", "attempt": attempt,
                "errors": [f"plan: {res.error}"],
                "trajectory": [f"plan attempt {attempt}: llm unavailable"]}

    data = res.json() or {}
    proposed = [str(u).strip() for u in (data.get("urls") or [])][:MAX_URLS]
    keep, reject = [], []
    for u in proposed:
        if u in visited:
            continue
        (keep if _host_ok(u, allowed) else reject).append(u)

    return {
        "planned_urls": keep,
        "plan_reason": str(data.get("why") or "")[:200],
        "plan_says_nothing": bool(data.get("publishes_nothing")),
        "rejected_urls": reject,
        "attempt": attempt,
        "llm_calls": int(state.get("llm_calls", 0)) + 1,
        "status": "reading" if keep else "exhausted",
        "trajectory": [f"plan attempt {attempt}: proposed {len(proposed)}, "
                       f"kept {len(keep)}, off-allowlist {len(reject)}"],
    }
