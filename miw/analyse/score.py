"""Turn probe results and researched claims into ranked, substantiated findings.

Three rules shape this module:

* **Evidence, not volume.** Blast radius is a weighted count, not a location count.
  A dependency imported by a coding question outranks one named in a paragraph, so
  prose mentions are worth a fraction of a runtime dependency. Without weighting,
  LangChain's 786 prose mentions would outrank a dead tool a student is told to open.
* **Probe observations stand alone; model conclusions do not.** A 404 we fetched
  ourselves is first-hand evidence and needs no citation. Everything researched needs
  an authoritative source or it does not ship.
* **Regressions and opportunities are scored separately.** A better alternative is
  interesting; a blocked student is urgent. Ranking them in one list buries the second
  under the first.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from miw.analyse import notes
from miw.schema import (Alternative, Claim, Dependency, Finding, ProbeResult,
                        ResearchResult, UncitedClaim, utcnow)
from miw.trust import ClaimKind, domain as _domain

# How much each kind of reference counts toward blast radius.
EVIDENCE_WEIGHT = {
    "solution_import": 5.0, "n8n_workflow": 5.0, "test_case_enum": 4.0,
    "install_command": 4.0, "link:a_href": 2.0, "link:iframe": 2.0,
    "model_id": 2.0, "link:bare": 1.0, "link:markdown": 1.0,
    "question_tag": 0.5, "title": 0.5, "prose_name": 0.2,
}
GRADED_MULTIPLIER = 2.0

SIGNALS = {
    "S1":  ("Dead / moved URL", "regression", "critical"),
    "S2":  ("Login- or paywall added", "regression", "high"),
    "S3":  ("Free tier cut or now paid", "regression", "high"),
    "S4":  ("Deprecated / abandoned", "regression", "high"),
    "S5":  ("Implementation or UX change", "regression", "medium"),
    "S6":  ("Package version drift", "regression", "medium"),
    "S7":  ("Model deprecated or superseded", "regression", "high"),
    "S8":  ("Docs rewritten", "regression", "low"),
    "S9":  ("n8n node / version update", "regression", "medium"),
    "S10": ("Better alternative available", "opportunity", "low"),
    "S11": ("Curriculum topic gap", "opportunity", "low"),
}

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


def blast_radius(dep: Dependency) -> int:
    total = 0.0
    for loc in dep.locations:
        w = EVIDENCE_WEIGHT.get(loc.evidence_source, 1.0)
        if loc.is_graded and loc.evidence_source not in ("prose_name", "question_tag"):
            w *= GRADED_MULTIPLIER
        total += w
    return int(round(total))


def _bump(sev: str, steps: int) -> str:
    i = max(0, min(len(SEVERITY_ORDER) - 1, SEVERITY_ORDER.index(sev) + steps))
    return SEVERITY_ORDER[i]


def retirement_severity(signal: str, dep: Dependency, radius: int,
                        executing: int, graded: int) -> str:
    """How bad a retired/withdrawn model id is, given what the material DOES with it.

    Shared by scoring and by the per-course projection so the two cannot disagree. A
    projection computing this differently produced a local severity *higher* than the
    global one while covering fewer locations, which is plainly impossible.
    """
    if executing:
        return "critical"          # passed to an API at run time; already failing
    if graded:
        return "high"              # nothing breaks, but graded answers are now wrong
    return _bump(severity_for(signal, dep, radius), -1)


def severity_for(signal: str, dep: Dependency, radius: int) -> str:
    base = SIGNALS[signal][2]
    if dep.watch_tier == "critical" and radius >= 8:
        base = _bump(base, 1)
    elif dep.watch_tier == "mention-only" or radius <= 1:
        base = _bump(base, -1)
    return base


# probe signal -> finding signal
PROBE_TO_SIGNAL = {
    "url_gone": "S1", "domain_parked": "S1",
    "registry_missing": "S4", "registry_deprecated": "S4", "no_release_in_2y": "S4",
    "sunset_language_about_subject": "S4",
    "access_wall_language": "S2",
    "free_tier_language_lost": "S3", "pricing_restriction_language": "S3",
    "pricing_page_changed": "S3",
    "redirected_off_path": "S5",
    "page_text_changed": "S8",
    "new_release": "S6", "major_behind_taught_pin": "S6",
    "n8n_new_release": "S9", "node_named_in_release_notes": "S9",
    "node_removed_upstream": "S9", "breaking_change_declared": "S9",
    "breaking_change_possible": "S9",
    # A taught model id its own provider lists as retired.
    "model_shutdown_passed": "S7", "model_deprecation_declared": "S7",
    # Retired on the developer plan but still served under a quote-only tier. The
    # action is the same as S7 - change the taught model id - so it stays one finding
    # rather than duplicating into S3, which is how 8 findings appeared where there
    # were 4 in Phase 2.
    "model_tier_restricted": "S7",
}

# n8n states a severity on each of its own breaking-change rules. Honour it rather
# than substituting our own guess about someone else's product.
VENDOR_SEVERITY = {"low": "low", "medium": "medium", "high": "high",
                   "critical": "critical"}

CLAIM_TO_SIGNAL = {
    ClaimKind.DEPRECATION: "S4",
    ClaimKind.PRICING: "S3",
    ClaimKind.IMPLEMENTATION: "S5",
    ClaimKind.VERSION: "S6",
}

# Pricing quotes that actually indicate a *restriction*, not just a price list.
PRICING_RESTRICTION = ("no longer free", "free tier has been", "free plan has been",
                       "discontinued the free", "requires a paid", "paid plans only",
                       "trial has ended", "quota exceeded", "reached your limit",
                       "removed the free")


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def findings_for(dep: Dependency, probe: Optional[ProbeResult],
                 research: Optional[ResearchResult]) -> list[Finding]:
    radius = blast_radius(dep)
    out: dict[str, Finding] = {}

    def ensure(signal: str) -> Finding:
        if signal not in out:
            label, kind, _ = SIGNALS[signal]
            out[signal] = Finding(
                dep_id=dep.dep_id, canonical_name=dep.canonical_name,
                signal=signal, signal_label=label, kind_of_signal=kind,
                severity=severity_for(signal, dep, radius),
                blast_radius=radius, graded_locations=dep.graded_locations,
                courses=dep.courses, locations=dep.locations[:12],
                questions_executing=len(dep.questions_that_execute_it),
                questions_mentioning=len(dep.questions_that_mention_it),
                # Dedupe: one question can reference a dependency from several field
                # paths, and listing the same id twice makes the sample look wrong.
                question_ids=list(dict.fromkeys(
                    l.content_id for l in (dep.questions_that_execute_it +
                                           dep.questions_that_mention_it)))[:6],
                raised_at=utcnow())
        return out[signal]

    # --- from the probe: first-hand observations ----------------------------
    if probe:
        # A registry bump on its own is not curriculum news. 71 taught packages
        # release constantly, and under daily polling every patch would raise an S6
        # and, once a channel exists, send a notification. What makes a release
        # curriculum-relevant is that it moved away from what the course pins - which
        # is exactly `major_behind_taught_pin`. The new version is still written to
        # the artifact either way: state is recorded, only news is reported.
        bare_bump = ("new_release" in probe.signals
                     and "major_behind_taught_pin" not in probe.signals)
        for sig in probe.signals:
            signal = PROBE_TO_SIGNAL.get(sig)
            if not signal:
                continue
            if sig == "page_text_changed" and dep.watch_tier == "mention-only":
                continue        # docs prose churn on a passing mention is not news
            if sig == "new_release" and bare_bump:
                continue        # the pin still holds; nothing for a reviewer to do
            f = ensure(signal)
            f.probe_signals.append(sig)
            f.affected_urls = list(probe.affected_urls)
            f.latest_version = probe.latest_version or ""
            if not f.summary:
                f.summary = _probe_summary(sig, dep, probe)

            # A vendor-declared change comes with the vendor's own severity, doc URL
            # and wording. Cite it rather than paraphrase it.
            if sig == "node_removed_upstream":
                f.severity = "critical"
            if sig in ("model_shutdown_passed", "model_tier_restricted"):
                # The announced date has passed - but "already broken" depends on what
                # the curriculum DOES with the id, not just that it is retired. Setting
                # `critical` unconditionally ranked 5 broken coding questions and one
                # line of reading material identically, and told a reviewer to fix both
                # this sprint. A model id inside a coding question is passed to an API at
                # run time (`Dependency._executes`), so only that case is a live outage.
                f.severity = retirement_severity(
                    signal, dep, radius, f.questions_executing, f.graded_locations)
            if sig == "free_tier_language_lost":
                # The strongest S3 evidence there is: wording the vendor advertised
                # while it was true has gone.
                f.severity = _bump(f.severity, 1)
            if sig == "pricing_page_changed" and len(f.probe_signals) == 1:
                # A pricing rewrite on its own routes to research; it is not yet a
                # finding about money. Set absolutely, not relatively: blast radius has
                # already bumped the base severity by this point, so a relative step
                # down still landed on medium for a widely-used tool.
                f.severity = "low"
            for change in probe.declared_changes:
                vend = VENDOR_SEVERITY.get(str(change.get("severity", "")).lower())
                if vend and SEVERITY_ORDER.index(vend) > SEVERITY_ORDER.index(f.severity):
                    f.severity = vend
                if sig == "breaking_change_possible":
                    f.severity = _bump(f.severity, -1)
                url = change.get("doc_url") or ""
                quote = " ".join(x for x in (change.get("title"),
                                             change.get("description")) if x)
                if not url or len(quote) < 12:
                    continue
                # A model's serving provider is authoritative about it even when the
                # family owner differs — Groq can retire a Meta model. Widening is
                # earned: the probe only sets `provider_domains` when that provider's
                # own catalogue named the exact id.
                subject = (dep.subject_with_provider(probe.provider_domains)
                           if probe.provider_domains else dep.subject())
                is_model = dep.kind == "model"
                try:
                    c = Claim.build(
                        kind=(ClaimKind.DEPRECATION if is_model
                              else ClaimKind.IMPLEMENTATION),
                        statement=(change.get("title") if is_model else
                                   f"n8n declares: {change.get('title')} "
                                   f"(n8n {change.get('n8n_version')})"),
                        source_url=url, quote=quote, subject=subject)
                except UncitedClaim:
                    c = None
                # A claim that cannot substantiate its own kind must not be attached.
                # `Claim.build` only refuses an EXCLUDED source, so a strict kind on a
                # merely CORROBORATING one was still appended - and a finding carrying
                # it LOOKS cited while resting on nothing, which is what `main.py
                # verify` flagged for `@n8n/n8n-nodes-langchain.chatTrigger`: an
                # `implementation` claim citing `npmjs.com/package/@n8n/chat`. npm is a
                # canonical registry for versions and existence, not for how a vendor's
                # embedded chat protocol changed. Dropped, and counted, because silence
                # here is what made it invisible until `verify` shouted.
                if c is not None and c.substantiating:
                    f.claims.append(c)
                elif c is not None:
                    f.probe_signals = list(f.probe_signals) + [
                        f"declared_change_unciteable:{_domain(url)}"]

                # Cite the SECOND sighting too, when the vendor contradicts itself. The
                # live listing is the half a reviewer would otherwise use to disprove
                # us - "you said it was shut down and here it is on the models page" -
                # so it belongs in the evidence, quoted from the same vendor, rather
                # than left out for the digest to look tidier.
                listed_url = change.get("listed_url") or ""
                listed_quote = change.get("listed_quote") or ""
                if change.get("still_listed") and listed_url and len(listed_quote) >= 12:
                    try:
                        f.claims.append(Claim.build(
                            kind=ClaimKind.PRICING,
                            statement=(f"{dep.canonical_name} is still listed by "
                                       f"{probe.provider or 'the provider'} at "
                                       f"{change.get('listed_price') or 'quote-only'} "
                                       f"pricing"),
                            source_url=listed_url, quote=listed_quote, subject=subject))
                    except UncitedClaim:
                        pass
        # Only fall back to "dead URL" when nothing more specific fired. A model its
        # provider has retired is S7; reporting it as S1 as well double-counts one event
        # and pads the digest.
        if probe.status == "broken" and not out:
            f = ensure("S1")
            f.probe_signals.append(probe.status)
            f.summary = probe.detail

    # --- from research: only substantiated claims ---------------------------
    if research:
        for claim in research.claims:
            if not claim.substantiating:
                continue
            signal = CLAIM_TO_SIGNAL.get(claim.kind)
            if not signal:
                continue
            if claim.kind is ClaimKind.PRICING and \
                    not any(p in claim.quote.lower() for p in PRICING_RESTRICTION):
                # A pricing page that merely lists prices is not a finding.
                continue
            if claim.kind is ClaimKind.IMPLEMENTATION and not probe:
                continue
            f = ensure(signal)
            f.claims.append(claim)
            if not f.summary:
                f.summary = claim.statement

        verified_alts = [a for a in research.alternatives if a.verified]
        if verified_alts:
            target = out.get("S1") or out.get("S4")
            if target:
                target.alternatives = verified_alts
            else:
                f = ensure("S10")
                f.alternatives = verified_alts
                f.summary = (f"{len(verified_alts)} verified alternative(s) to "
                             f"{dep.canonical_name} exist")
                # Lift the alternatives' own citations onto the finding. Without this
                # an S10 can never be reported at all: `Finding.is_substantiated` reads
                # `probe_signals` and `self.claims`, and an S10 has no probe signals
                # while its evidence lives on `alternatives[*].claims` - so it was
                # dropped at the `if not f.is_substantiated` gate below. These are
                # already properly built Claims with the right subject and tier, so
                # this needs no new trust machinery, and it also makes
                # `evidence_urls`, the digest's evidence block and `cmd_verify` work.
                for a in verified_alts:
                    for c in a.claims:
                        if c.substantiating and c not in f.claims:
                            f.claims.append(c)

    # Model-kind dependencies with a deprecation finding are S7, not S4.
    if dep.kind == "model" and "S4" in out:
        f = out.pop("S4")
        f.signal, f.signal_label, _ = "S7", SIGNALS["S7"][0], None
        f.severity = severity_for("S7", dep, radius)
        f.finding_id = ""
        f.__post_init__()
        out["S7"] = f

    # Successors the vendor named and still serves are the best alternative available,
    # and they need no search key. Attach them before research is even consulted.
    if probe and probe.alternatives_verified:
        for f in out.values():
            if f.signal != "S7":
                continue
            for alt in probe.alternatives_verified:
                a = Alternative(name=alt["name"], homepage=alt.get("homepage", ""),
                                nominated_by=probe.evidence_url,
                                maturity_note=f"named by {probe.provider} as the "
                                              f"replacement, and still listed as "
                                              f"{alt.get('status', 'available')}")
                try:
                    a.claims.append(Claim.build(
                        kind=ClaimKind.ALTERNATIVE,
                        statement=f"{probe.provider} lists {alt['name']} as "
                                  f"{alt.get('status', 'available')}",
                        source_url=alt.get("homepage") or probe.evidence_url,
                        quote=alt.get("quote", ""),
                        subject=dep.subject_with_provider(probe.provider_domains)))
                except UncitedClaim:
                    pass
                f.alternatives.append(a)

    # A dead URL supersedes "the docs moved" for the same dependency: they are the
    # same underlying event, and reporting both makes the digest look padded.
    if ("S1" in out) and ("S5" in out) and \
            set(out["S5"].probe_signals) <= {"redirected_off_path", "page_text_changed"}:
        del out["S5"]

    final = []
    for f in out.values():
        if not f.is_substantiated:
            continue
        f.recommendation = recommend(dep, f)
        notes.compose(dep, f)          # deterministic triad; refine() may replace it
        final.append(f)
    return sorted(final, key=lambda x: (-SEVERITY_ORDER.index(x.severity), -x.blast_radius))


def _probe_summary(sig: str, dep: Dependency, probe: ProbeResult) -> str:
    """A summary specific to this signal, not the probe's single detail string."""
    urls = probe.affected_urls or ([probe.evidence_url] if probe.evidence_url else [])
    first = urls[0] if urls else "(no url)"
    more = f" (and {len(urls) - 1} more)" if len(urls) > 1 else ""
    return {
        "url_gone": f"{first} returns 404/410{more}.",
        "domain_parked": f"{first} looks like a parked or expired domain.",
        "redirected_off_path": (
            f"{first} now redirects to {probe.final_url}."
            if probe.final_url and probe.final_url.rstrip("/") != first.rstrip("/")
            else f"{first} redirects away from the path the course links to."),
        "access_wall_language": f"{first} now shows access-wall wording"
                                f" (sign-in or quota required to continue){more}.",
        "free_tier_language_lost": probe.detail,
        "pricing_restriction_language": (f"{dep.canonical_name}'s pricing page now "
                                         f"carries restriction wording."),
        "pricing_page_changed": probe.detail,
        "page_text_changed": f"The prose on {first} changed substantially since the "
                             f"last run.",
        "registry_missing": f"{dep.registry or 'the registry'} no longer lists "
                            f"'{dep.registry_id or dep.canonical_name}'.",
        "registry_deprecated": f"The current release of {dep.canonical_name} is marked "
                               f"yanked or deprecated on {dep.registry}.",
        "no_release_in_2y": f"{dep.canonical_name} has had no release in over two years.",
        "node_removed_upstream": probe.detail,
        "model_shutdown_passed": probe.detail,
        "model_deprecation_declared": probe.detail,
        "model_catalogue_unreadable": probe.detail,
        "breaking_change_declared": probe.detail,
        "breaking_change_possible": probe.detail,
        "n8n_upstream_unreachable": probe.detail,
        "sunset_language_about_subject": probe.detail,
        "model_tier_restricted": probe.detail,
        "new_release": f"{dep.canonical_name}: {probe.detail}.",
        "n8n_new_release": f"n8n released a new version: {probe.detail}.",
        "node_named_in_release_notes": probe.detail,
        "major_behind_taught_pin": f"The course pins {dep.canonical_name} "
                                   f"{dep.taught_version}; the registry's latest is "
                                   f"{probe.latest_version}.",
    }.get(sig, probe.detail or f"{sig} observed on {probe.evidence_url}")


# Signals that are about a third-party flow changing, and therefore about captures of
# that flow going stale. A dead package has no screenshots to redo.
SCREENSHOT_SIGNALS = ("S2", "S5", "S8")


def screenshots_at_risk(f: Finding, census: dict[str, int]) -> int:
    """Step-by-step imagery in the units this finding touches.

    Deduplicated by unit: a dependency referenced eight times in one unit must not
    multiply that unit's screenshot count by eight.
    """
    if f.signal not in SCREENSHOT_SIGNALS:
        return 0
    units = {(l.course, l.unit_id) for l in f.locations}
    return sum(census.get(f"{course}|{unit}", 0) for course, unit in units)


def fingerprint_of(f: Finding) -> str:
    return _fingerprint(f.signal, ",".join(sorted(f.probe_signals)),
                        ",".join(f.evidence_urls))


def recommend(dep: Dependency, f: Finding) -> str:
    """A concrete next action. Deterministic: no LLM required."""
    where = ""
    if f.locations:
        first = f.locations[0]
        sess = f"session {first.session_no}" if first.session_no else first.unit_name
        where = f" Starts at {first.course} / {sess} / {first.unit_name[:50]}."

    alt_txt = ""
    if f.alternatives:
        a = f.alternatives[0]
        # A vendor-named successor arrives as a name only - it has no homepage until it
        # has been verified against its own domain. Rendering "DeepWiki ()" advertises
        # a missing field; saying so plainly tells a reviewer what is left to do.
        where_alt = f" ({a.homepage})" if a.homepage else " (homepage not yet verified)"
        free = "free path confirmed" if a.free_student_path else "free path unconfirmed"
        alt_txt = (f" Candidate replacement: {a.name}{where_alt} - {free}"
                   f"{'; signup required' if a.signup_required else ''}.")

    # Scope the action to the links actually affected, not to every mention of the
    # dependency: "repoint the dead link in 845 locations" is false when one URL broke.
    url_note = ""
    if f.affected_urls:
        url_note = f" Affected URL: {f.affected_urls[0]}"
        if len(f.affected_urls) > 1:
            url_note += f" (+{len(f.affected_urls) - 1} more)"
        url_note += "."

    base = {
        "S1": f"Repoint or replace the dead link in the {dep.link_locations} place(s) "
              f"{dep.canonical_name} is linked.{url_note}",
        "S2": f"Check whether the taught step for {dep.canonical_name} still works "
              f"without an account; if not, rewrite the step or swap the tool.{url_note}",
        "S3": f"Re-check {dep.canonical_name}'s free tier against what the session "
              f"asks a student to do.",
        "S4": f"{dep.canonical_name} shows deprecation/abandonment signals - plan a "
              f"replacement before the next cohort.",
        "S5": f"Re-verify the taught steps for {dep.canonical_name} against its "
              f"current docs; screenshots and click-paths may be stale.{url_note}",
        "S6": (f"Confirm the session's code still runs: the course teaches "
               f"{dep.canonical_name}"
               + (f" {dep.taught_version}" if dep.taught_version else "")
               + (f", the registry's latest is {f.latest_version}"
                  if f.latest_version else "")
               + ("; that is a major-version gap." if "major_behind_taught_pin"
                  in f.probe_signals else ".")),
        "S7": ((f"{dep.canonical_name} has left its provider's developer plan - it is "
                f"still served, but only on a quote-only tier, so replace the model id "
                f"in the session and re-run its examples on a free key.")
               if "model_tier_restricted" in f.probe_signals else
               (f"{dep.canonical_name} is retired at its provider - replace the model "
                f"id in the session and re-run its examples.")
               if "model_shutdown_passed" in f.probe_signals else
               (f"{dep.canonical_name} is scheduled for retirement - plan the model id "
                f"change before the shutdown date.")),
        "S8": f"{dep.canonical_name}'s docs changed; spot-check the session's "
              f"screenshots and step list.",
        "S9": f"n8n node {dep.canonical_name}"
              + (" has been removed from n8n" if "node_removed_upstream" in f.probe_signals
                 else " is affected by a declared breaking change"
                 if "breaking_change_declared" in f.probe_signals else " changed")
              + (f" (course teaches typeVersion {dep.taught_version})" if dep.taught_version else "")
              + "; re-import the workflow and confirm node behaviour.",
        "S10": f"Consider whether a better-suited tool than {dep.canonical_name} is "
               f"now available.",
        "S11": "Review course coverage against current industry expectations.",
    }[f.signal]
    # Assessment fallout: the reading material is only half the edit.
    q = ""
    if f.questions_executing:
        q = (f" {f.questions_executing} graded item(s) run this in solution code or "
             f"test cases and will break.")
    # Only when the *tool* is gone does merely naming it date a question. One dead
    # docs link does not make 814 MCQs wrong, and saying so destroys the estimate.
    if f.questions_mentioning and f.signal in ("S4", "S7"):
        q += (f" A further {f.questions_mentioning} question(s) name it and may need "
              f"rewording.")
    return base + where + alt_txt + q
