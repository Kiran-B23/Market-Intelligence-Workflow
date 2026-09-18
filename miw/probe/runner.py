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

from miw.net import SIMHASH_DISTANCE, domain, hash_distance
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

    elif dep.kind == "model":
        from miw.probe.models import probe_model_dependency, verified_replacements
        got = probe_model_dependency(dep)
        # Carry the discovered provider and its declared change through unchanged; the
        # scoring stage builds the cited claim from `declared_changes`.
        for fld in ("status", "detail", "evidence_url", "provider", "provider_domains",
                    "declared_changes", "signals", "latest_version"):
            setattr(res, fld, getattr(got, fld))
        res.alternatives_verified = verified_replacements(got)

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
            # `None` on a dependency never probed before: a first look records the
            # notices as a baseline and raises nothing, the same rule the catalogue
            # diff uses. Reporting every standing deprecation on day one would be a
            # flood, and none of it would be news.
            res_from_urls(res, obs, prev_hash, dep,
                          seen_notices=set(state.notice_keys(dep.dep_id))
                          if prev is not None else None)
            # Where did it go? A finding that says "repoint or replace the dead link"
            # and stops hands the reviewer's whole job back to them: they open the URL,
            # see the 404, and then try the obvious candidates on the vendor's own site
            # by hand. This is the stage that already makes HTTP requests and the one
            # that costs nothing, so it does that instead - bounded to six tries per
            # dead URL, all on domains the dependency already owns.
            if "url_gone" in res.signals or "domain_parked" in res.signals:
                res.successors = _successors_for(dep, res, obs)

    # --- flap protection ----------------------------------------------------
    # Two-run confirmation exists to stop a transient network failure reading as a dead
    # tool. It does not apply to a vendor's own published declaration: re-reading Groq's
    # deprecation table tomorrow adds no information, and delaying a critical finding by
    # a day buys no safety. Absence observations still need confirming — "not in the
    # source tree" could be a partial fetch — but a dated row in a vendor's table is a
    # document, not an observation.
    DECLARED = {"model_shutdown_passed", "model_deprecation_declared",
                "model_tier_restricted", "breaking_change_declared",
                "registry_deprecated"}
    if DECLARED & set(res.signals):
        res.consecutive_failures = 0
    elif res.status in ("broken", "unreachable"):
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
        notice_keys=res.notice_keys,
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



def _applicable(rules, node: str, dep: Dependency, res: ProbeResult) -> list[dict]:
    """The rules that can still be true of the version this course teaches.

    Two filters, both of which used to be missing:

    * **typeVersion.** A rule that states the node versions it breaks is checked
      against `dep.taught_version` - the typeVersion the course's workflow JSON
      declares. `AI Agent versions below 2 are removed` was being raised against a node
      taught at 2.2, which is not below 2. Suppressed rules are counted in a probe
      signal rather than dropped in silence, because "we checked and you are fine" is
      itself worth being able to audit.
    * **Placeholders.** n8n writes some titles as TypeScript template literals, and two
      findings shipped reading "${removedNodeName} node removed". The binding is known
      here, so it is filled in; a rule whose placeholder cannot be resolved is refused
      rather than printed raw, because a finding whose own title did not parse is not a
      finding.
    """
    out = []
    for r in rules:
        verdict = r.affects_version(dep.taught_version)
        if verdict is False:
            res.flag(f"n8n_rule_not_applicable:{r.rule_id}")
            continue
        title, description = r.render(node)
        if "${" in title + description:
            res.flag(f"n8n_rule_unparsed:{r.rule_id}")
            continue
        d = dict(r.__dict__)
        d["title"], d["description"] = title, description
        # What the gate concluded, carried so the finding can say it out loud: a rule
        # that stated no version range is a weaker claim than one we checked.
        d["version_bound"] = ("".join(r.version_bound) if r.version_bound else "")
        d["version_checked"] = verdict is not None
        d["taught_version"] = dep.taught_version or ""
        out.append(d)
    return out


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

    named = _applicable(rules_for_node(up["rules"], node), node, dep, res)
    if named:
        res.status = "changed"
        res.flag("breaking_change_declared")
        res.declared_changes = named
        r = named[0]
        res.detail = (f"n8n declares a breaking change affecting this node: "
                      f"{r['title']} (n8n {r['n8n_version']}, vendor severity "
                      f"{r['severity']})")
        res.evidence_url = r["doc_url"] or "https://docs.n8n.io/release-notes/"
        return

    leaf = node.rsplit(".", 1)[-1]
    human = re.sub(r"(?<!^)(?=[A-Z])", " ", leaf).lower()
    cap = _applicable(rules_mentioning(up["rules"], [leaf, human]), node, dep, res)
    if cap:
        res.status = "changed"
        res.flag("breaking_change_possible")
        res.declared_changes = cap
        r = cap[0]
        res.detail = (f"n8n declares a breaking change that may affect this node: "
                      f"{r['title']} (n8n {r['n8n_version']}). It names no node types, "
                      f"so whether the course is affected needs a human check.")
        res.evidence_url = r["doc_url"] or "https://docs.n8n.io/release-notes/"
        return

    if dep.taught_version and res.latest_version:
        res.flag("node_present_at_taught_version")
    res.status = "ok"



def _successors_for(dep: Dependency, res: ProbeResult, obs) -> list[dict]:
    """The live page that replaced each dead URL, where one can be found first-hand."""
    from miw.probe.successor import find_successor

    finals = {o.url: o.final_url for o in obs}
    out: list[dict] = []
    for dead in res.affected_urls[:4]:
        s = find_successor(dead, final_url=finals.get(dead, ""),
                           homepage=dep.homepage,
                           official_domains=dep.official_domains)
        if s.placeholder:
            # Not a broken link: a URL the course prints as an example, which a student
            # generates for themselves. `abc123.ngrok.io` and `xxxxx.gradio.live` are
            # both in the inventory and both were reported as dead links every week.
            # There is nothing to repoint, and saying so is the finding.
            out.append({"dead": dead, "placeholder": True})
        elif s.found:
            out.append({"dead": dead, "url": s.found.url, "rule": s.found.rule,
                        "note": s.found.note, "title": s.found.title,
                        "tried": len(s.tried)})
        else:
            out.append({"dead": dead, "tried": len(s.tried)})
    return out



def _record_redirect(res: ProbeResult, o, dep: Optional[Dependency]) -> None:
    """Where this URL went, and whether it left the dependency's own estate.

    `cookbook.openai.com -> developers.openai.com` is OpenAI reorganising its docs, and
    it implicates the two links that point at it. `windsurf.com -> devin.ai` is the
    product having been absorbed, which makes the prose that names it wrong too.
    Reporting both as "redirected" made the first one claim 24 places, 21 of which were
    reading material that merely says the word "OpenAI".
    """
    own = set()
    if dep is not None:
        own = {(d or "").lower() for d in dep.official_domains} | {
            (domain(dep.homepage) or "").lower()} - {""}
    landed = (domain(o.final_url) or "").lower()
    inside = bool(landed) and any(
        landed == d or landed.endswith("." + d) or d.endswith("." + landed)
        for d in own)
    res.redirects.append({"from": o.url, "to": o.final_url, "off_site": not inside})


def res_from_urls(res: ProbeResult, obs: list[UrlObservation], prev_hash: str,
                  dep: Optional[Dependency] = None,
                  seen_notices: Optional[set] = None) -> None:
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

    # A notice that was not on these pages last week. This is the ARRIVAL of a
    # deprecation, which neither of the two detectors below could see:
    #
    # * `text_hash` is a simhash over 3-word shingles of alphabetic words, tuned so
    #   rotating banners do not read as change. Measured on the live 5,790-word Gemini
    #   release notes, adding "gemini-2.5-flash is deprecated and will be shut down on
    #   March 1, 2027" moves 0 of 64 bits; adding it five times moves 1.
    # * `sunset_language_about_subject` is saturated on exactly the pages that matter.
    #   That same changelog already answers ["now deprecated", "will be shut down"]
    #   every week, before anything is added, so the flag is on permanently and says
    #   nothing about this week.
    #
    # Counting the notices themselves is what separates "this page discusses
    # deprecations" from "this page deprecated something since we last looked".
    from miw.probe.http_probe import notice_key
    current = {notice_key(sent): sent for o in obs for sent in o.sunset_sentences}
    res.notice_keys = sorted(current)
    fresh = [current[k] for k in sorted(current) if k not in (seen_notices or set())]
    if fresh and seen_notices is not None:
        res.flag("deprecation_notice_added")
        res.new_notices = fresh[:3]

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
            # Recorded HERE, in the loop, not in the `redirected_off_path` branch
            # below: that branch is unreachable when a different URL on the same
            # dependency is also gone, because `url_gone` returns first. Composio has
            # exactly that shape - a dead dashboard and a moved docs path - and its S5
            # was left with no redirect record, so the scoper could not tell a
            # reorganisation from a rebrand and fell back to the widest reading.
            _record_redirect(res, o, dep)

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



def _has_something_to_probe(dep: Dependency) -> bool:
    """Is there an authority to check this against at all?

    A dependency with no domain, no registry and no referenced URL has nothing the
    probe can do. Including it would spend a scheduling slot to record
    `inconclusive: no URL known`, which is what the inventory already says.
    """
    return bool(dep.official_domains or dep.registry or dep.referenced_urls
                or dep.homepage or dep.kind == "n8n_node")


def probe_all(deps: Iterable[Dependency], state: State, *,
              scope=None, progress=None) -> list[ProbeResult]:
    """Probe the dependencies a scope selects.

    Scope is what makes an on-demand run usable: at 1.5s per domain, the whole
    inventory takes about an hour, while one course's critical dependencies take a
    minute or two.
    """
    import dataclasses

    from miw.scope import Scope
    scope = scope or Scope(tiers={"critical", "standard"})
    deps = list(deps)
    todo = scope.select(deps)

    # `watch_tier` rations RESEARCH budget - Tavily searches, model calls, the many
    # fetches `official.gather` makes per dependency. Applying it to the PROBE is a
    # category error: a probe is one request per referenced URL, and for an n8n node it
    # is a read of a source tree already in memory.
    #
    # It cost real coverage. Measured on the live inventory the moment the mute
    # dependencies were given vendors: 48 dependencies had an authoritative domain and
    # were never probed because nothing executes them, and they included Hugging Face
    # at blast radius 56 - the largest in the set - plus Telegram, Google Colab, Claude
    # Code and every n8n node the curriculum only names. The whole extra cost is ~48
    # fetches, about a minute.
    #
    # So the tier no longer gates the probe. A dependency is probed when there is
    # something to probe it with; whether a student EXECUTES it still decides the
    # research budget, which is where the money is. Course, session, kind, dep-id and
    # limit all still apply, so `--course X` or `--kinds model` cannot be widened here.
    if scope.tiers:
        untiered = dataclasses.replace(scope, tiers=set())
        picked = {d.dep_id for d in todo}
        todo += [d for d in untiered.select(deps)
                 if d.dep_id not in picked and _has_something_to_probe(d)]
        # `select` applies the limit to each pass separately, so widening could return
        # up to twice it. `--limit 3` means three dependencies probed, not three per
        # selection, and an operator asking for a bounded spot-check must not get a
        # full-inventory sweep.
        if scope.limit:
            todo = todo[:scope.limit]
    out = []
    for i, dep in enumerate(todo, 1):
        out.append(probe_dependency(dep, state))
        if progress:
            progress(i, len(todo), out[-1])
    return out
