"""Probe orchestration: one deterministic observation per dependency, per week.

Status resolution is the whole point of this module, and the rule it exists to protect
is that **`unreachable` is not `broken`**. Our own network failing, or a vendor's
anti-bot rule rejecting us, must never be reported to the content team as a dead tool.
So:

* a 404/410 on a URL the curriculum links to is `broken`, but only after
  `MIN_CONSECUTIVE_FAILURES` runs agree — one bad week is noise;
* a 401/403/429 is `inconclusive`, because it proves the host is alive and serving;
* a transport error with no HTTP status at all is `unreachable`, our problem;
* a changed page hash, an off-path redirect, or a new sunset phrase is `changed`,
  which routes the dependency to the research stage rather than straight to a finding.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Optional

from miw.net import SIMHASH_DISTANCE, hash_distance
from miw.probe import registries as R
from miw.probe.http_probe import UrlObservation, observe
from miw.schema import Dependency, ProbeResult, utcnow
from miw.state import State

MIN_CONSECUTIVE_FAILURES = 2
MAX_URLS_PER_DEP = 4


def _days_since(iso: str) -> Optional[int]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).days


def _targets(dep: Dependency) -> list[str]:
    """Curriculum-referenced URLs first, then the vendor's own entry points."""
    seen, out = set(), []
    for u in list(dep.referenced_urls) + [dep.docs_url, dep.homepage]:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= MAX_URLS_PER_DEP:
            break
    return out


def probe_dependency(dep: Dependency, state: State) -> ProbeResult:
    res = ProbeResult(dep_id=dep.dep_id, canonical_name=dep.canonical_name)
    prev = state.probe_prev(dep.dep_id)
    prev_hash = (prev["text_hash"] if prev else "") or ""
    prev_version = (prev["latest_version"] if prev else "") or ""
    prev_fails = (prev["consecutive_failures"] if prev else 0) or 0

    # --- registry-backed kinds: machine-readable and cheap ------------------
    if dep.kind == "package" and dep.registry in ("pypi", "npm"):
        info = (R.pypi if dep.registry == "pypi" else R.npm)(dep.registry_id or dep.canonical_name)
        res.evidence_url = info.get("evidence_url", "")
        if not info.get("found"):
            if info.get("reachable"):
                res.status = "broken"
                res.flag("registry_missing")
                res.detail = f"{dep.registry} has no project '{dep.registry_id or dep.canonical_name}'"
            else:
                res.status = "unreachable"
                res.detail = info.get("error") or f"http {info.get('http_status')}"
        else:
            res.latest_version = info.get("latest_version") or ""
            res.version_released_at = info.get("released_at") or ""
            if info.get("yanked") or info.get("deprecated"):
                res.status = "changed"
                res.flag("registry_deprecated")
                res.detail = "release is yanked/deprecated on the registry"
            elif prev_version and res.latest_version and res.latest_version != prev_version:
                res.status = "changed"
                res.flag("new_release")
                res.detail = f"{prev_version} -> {res.latest_version}"
            else:
                res.status = "ok"
            if dep.taught_version and res.latest_version and \
                    dep.taught_version.split(".")[0] != res.latest_version.split(".")[0]:
                res.flag("major_behind_taught_pin")
            stale = _days_since(res.version_released_at)
            if stale is not None and stale > 730:
                res.flag("no_release_in_2y")

    elif dep.kind == "n8n_node":
        _probe_n8n_node(dep, res, prev_version)

    # --- URL-backed kinds ---------------------------------------------------
    else:
        _probe_pricing(dep, res, state)
        terms = tuple({dep.canonical_name, dep.canonical_name.rsplit(".", 1)[-1],
                       *dep.aliases} - {""})
        obs = [observe(u, terms) for u in _targets(dep)]
        if not obs:
            res.status = "inconclusive"
            res.detail = "no URL known for this dependency"
        else:
            res_from_urls(res, obs, prev_hash)

    # --- flap protection ----------------------------------------------------
    if res.status in ("broken", "unreachable"):
        res.consecutive_failures = prev_fails + 1
        if res.status == "broken" and res.consecutive_failures < MIN_CONSECUTIVE_FAILURES:
            res.flag("awaiting_confirmation")
            res.status = "inconclusive"
            res.detail = (f"{res.detail} (run {res.consecutive_failures} of "
                          f"{MIN_CONSECUTIVE_FAILURES} before this is reported)")
    else:
        res.consecutive_failures = 0

    # A pricing change is a change even when every URL returns 200.
    if res.status == "ok" and ("free_tier_language_lost" in res.signals
                               or "pricing_page_changed" in res.signals):
        res.status = "changed"

    res.checked_at = utcnow()
    state.probe_save(
        dep_id=dep.dep_id, canonical_name=dep.canonical_name, status=res.status,
        checked_at=res.checked_at, text_hash=res.text_hash,
        latest_version=res.latest_version or "", http_status=res.http_status,
        repo_archived=res.repo_archived, consecutive_failures=res.consecutive_failures,
    )
    return res


PRICING_TIERS = ("critical",)


def _probe_pricing(dep: Dependency, res: ProbeResult, state: State) -> None:
    """Snapshot the vendor's pricing page and compare it with last week's.

    Restricted to `critical` dependencies: this costs up to six extra fetches the first
    time a vendor's pricing URL is discovered, and one thereafter. A tool a student only
    reads about does not justify that.
    """
    if dep.watch_tier not in PRICING_TIERS or not dep.subject().official_domains:
        return
    from miw.probe.pricing import compare, observe_pricing

    prev_url, prev_hash, prev_free = state.pricing_prev(dep.dep_id)
    obs = observe_pricing(dep, known_url=prev_url)
    if not obs.usable:
        if obs.error:
            res.flag("pricing_page_unreadable")
        return

    signals, lost, _dist = compare(obs, prev_hash, prev_free)
    for sig in signals:
        res.flag(sig)
    if "free_tier_language_lost" in signals:
        res.detail = (f"free-tier wording disappeared from {obs.url}: "
                      f"{', '.join(repr(p) for p in lost[:3])} no longer present")
        res.evidence_url = obs.url
    elif "pricing_page_changed" in signals and not res.detail:
        res.detail = f"{obs.url} changed substantially since the last run"
        res.evidence_url = obs.url

    state.pricing_save(dep_id=dep.dep_id, pricing_url=obs.url,
                       text_hash=obs.text_hash, free_signals=obs.free_present,
                       now=utcnow())


def _probe_n8n_node(dep: Dependency, res: ProbeResult, prev_version: str) -> None:
    """Does the taught node still exist, and has n8n declared a break against it?

    Three questions in priority order, because they need different responses:
      1. Is the node still in n8n's source tree? Absence is a hard break.
      2. Does a breaking-change rule name this exact node type? That is n8n saying so.
      3. Does a capability rule mention it without naming node types (Python in the
         Code node)? Reported as needing a look, never as confirmed - MIW knows the
         course uses the node, not whether it uses the affected parameter.
    """
    from miw.probe.n8n_upstream import rules_for_node, rules_mentioning, upstream

    info = R.n8n_latest()
    res.latest_version = info.get("latest_version") or ""
    res.version_released_at = info.get("released_at") or ""

    up = upstream()
    if not up.get("ok"):
        # GitHub unreachable must never read as "the node was removed".
        res.status = "inconclusive"
        res.flag("n8n_upstream_unreachable")
        res.detail = f"could not read n8n's source of truth: {up.get('error')}"
        res.evidence_url = "https://docs.n8n.io/release-notes/"
        return

    node = dep.canonical_name
    if node not in set(up["nodes"]):
        res.status = "broken"
        res.flag("node_removed_upstream")
        res.detail = (f"{node} is no longer in n8n's shipped node set "
                      f"(checked against the n8n source tree at {res.latest_version})")
        res.evidence_url = "https://github.com/n8n-io/n8n/tree/master/packages"
        return

    named = rules_for_node(up["rules"], node)
    if named:
        res.status = "changed"
        res.flag("breaking_change_declared")
        res.declared_changes = [r.__dict__ for r in named]
        r = named[0]
        res.detail = (f"n8n declares a breaking change affecting this node: "
                      f"{r.title} (n8n {r.n8n_version}, vendor severity {r.severity})")
        res.evidence_url = r.doc_url or "https://docs.n8n.io/release-notes/"
        return

    leaf = node.rsplit(".", 1)[-1]
    human = re.sub(r"(?<!^)(?=[A-Z])", " ", leaf).lower()
    cap = rules_mentioning(up["rules"], [leaf, human])
    if cap:
        res.status = "changed"
        res.flag("breaking_change_possible")
        res.declared_changes = [r.__dict__ for r in cap]
        r = cap[0]
        res.detail = (f"n8n declares a breaking change that may affect this node: "
                      f"{r.title} (n8n {r.n8n_version}). It names no node types, so "
                      f"whether the course is affected needs a human check.")
        res.evidence_url = r.doc_url or "https://docs.n8n.io/release-notes/"
        return

    if dep.taught_version and res.latest_version:
        res.flag("node_present_at_taught_version")
    res.status = "ok"


def res_from_urls(res: ProbeResult, obs: list[UrlObservation], prev_hash: str) -> None:
    """Fold several URL observations into one dependency status."""
    primary = obs[0]
    res.http_status = primary.status
    res.final_url = primary.final_url
    res.text_hash = primary.text_hash
    res.evidence_url = primary.final_url or primary.url

    gone = [o for o in obs if o.gone]
    parked = [o for o in obs if o.parked]
    reachable = [o for o in obs if o.reachable]
    blocked = [o for o in obs if o.blocked]

    for o in obs:
        if o.sunset_near_subject:
            res.flag("sunset_language_about_subject")
        elif o.sunset_phrases:
            res.flag("sunset_language_elsewhere_on_page")
        if o.wall_phrases:
            res.flag("access_wall_language")
            if o.url not in res.affected_urls:
                res.affected_urls.append(o.url)
        if o.redirected_off_path:
            res.flag("redirected_off_path")

    if parked:
        res.status = "broken"
        res.affected_urls = [o.url for o in parked]
        res.flag("domain_parked")
        res.detail = f"{parked[0].url} looks like a parked/expired domain"
        return
    if gone:
        res.status = "broken"
        res.affected_urls = [o.url for o in gone]
        res.flag("url_gone")
        res.detail = (f"{len(gone)} of {len(obs)} referenced URLs return "
                      f"{gone[0].status}: {gone[0].url}")
        return
    if all(o.error == "disallowed_by_robots" for o in obs):
        res.status = "inconclusive"
        res.flag("disallowed_by_robots")
        res.detail = ("robots.txt asks us not to fetch these paths; not probed. "
                      "This says nothing about whether the tool works.")
        return
    if not reachable:
        res.status = "unreachable"
        res.detail = f"no response from {len(obs)} URL(s): {obs[0].error or 'transport error'}"
        return
    if blocked and len(blocked) == len(reachable):
        res.status = "inconclusive"
        res.flag("blocked_by_host")
        res.detail = (f"host answers but refuses us (http {blocked[0].status}); "
                      "alive, but we cannot read the page")
        return
    if "sunset_language_about_subject" in res.signals:
        res.status = "changed"
        near = next((o for o in obs if o.sunset_near_subject), None)
        res.detail = (f"page says \"{near.sunset_near_subject[0]}\" near the tool's own "
                      f"name: {near.url}") if near else "sunset language about this tool"
        return
    if "redirected_off_path" in res.signals:
        res.status = "changed"
        off = next((o for o in obs if o.redirected_off_path), None)
        res.affected_urls = [o.url for o in obs if o.redirected_off_path]
        res.detail = f"{off.url} now redirects to {off.final_url}" if off else "redirected"
        return
    dist = hash_distance(prev_hash, res.text_hash)
    if dist is not None and dist > SIMHASH_DISTANCE:
        res.status = "changed"
        res.flag("page_text_changed")
        res.detail = (f"landing/docs prose changed substantially since last run "
                      f"(simhash distance {dist}/64)")
        return
    res.status = "ok"


def probe_all(deps: Iterable[Dependency], state: State, *,
              scope=None, progress=None) -> list[ProbeResult]:
    """Probe the dependencies a scope selects.

    Scope is what makes an on-demand run usable: at 1.5s per domain, the whole
    inventory takes about an hour, while one course's critical dependencies take a
    minute or two.
    """
    from miw.scope import Scope
    scope = scope or Scope(tiers={"critical", "standard"})
    todo = scope.select(list(deps))
    out = []
    for i, dep in enumerate(todo, 1):
        out.append(probe_dependency(dep, state))
        if progress:
            progress(i, len(todo), out[-1])
    return out
