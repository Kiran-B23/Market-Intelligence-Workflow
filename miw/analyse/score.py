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

import dataclasses
import hashlib
import re
from typing import Optional

from config.constants import ARTIFACT_ORDER, artifact_word
from miw.analyse import notes
from miw.schema import (Alternative, Claim, Dependency, Finding, Location,
                        ProbeResult,
                        ResearchResult, UncitedClaim, utcnow)
from miw.trust import ClaimKind, domain as _domain

# How much each kind of reference counts toward blast radius.
EVIDENCE_WEIGHT = {
    "solution_import": 5.0, "n8n_workflow": 5.0, "test_case_enum": 4.0,
    "install_command": 4.0, "link:a_href": 2.0, "link:iframe": 2.0,
    "model_id": 2.0, "link:bare": 1.0, "link:markdown": 1.0,
    # A node type named in a display-name reference table, not built into a workflow.
    # Worth what a prose mention is worth, which is what it is: the course says the
    # name exists. Recording it as `n8n_workflow` gave nine glossary rows the weight of
    # a wired node and put two of them in the digest at `high`.
    "question_tag": 0.5, "title": 0.5, "prose_name": 0.2, "n8n_mention": 0.2,
    # The course writes this key into a request body. That is a runtime dependency in
    # the most literal sense available - it is the payload the student's code sends.
    "payload_key": 5.0,
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
    # Not S10. S10 means "we searched for alternatives and verified one could do the
    # taught job" - a fit judgement. S12 asserts only what the vendor's own catalogue
    # says: this exists, it is served, and we do not teach it.
    "S12": ("Newer option from a vendor we already use", "opportunity", "low"),
    # A field INSIDE a live API. Every other regression signal asks whether the
    # dependency is still there; this one asks whether what the course puts in the
    # request still exists. High rather than critical by default: the vendor still
    # serves the field today, and `severity_for` lifts it where the course executes it.
    "S13": ("Taught API field deprecated", "regression", "high"),
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
    # A deprecation notice that was not on the vendor's pages last week. Distinct from
    # the flag above, which is about the page's standing content and is permanently on
    # for any vendor that keeps a changelog.
    "deprecation_notice_added": "S4",
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
    "taught_field_deprecated": "S13",
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


# --------------------------------------------------------------- location scoping
#
# A finding used to inherit `dep.locations[:12]` - every place the dependency is named
# anywhere in the curriculum - regardless of what the signal was about. So Composio's
# dead dashboard URL listed 12 locations of which 6 were links and 6 were a quiz
# question saying the word "Composio" and two tracking-sheet rows; Ngrok listed 9 of
# which 2 were links. The recommendation sentence right below that list already said
# "the 6 place(s) Composio is linked", because `recommend()` was scoped years before
# the list under it was. This closes that gap.
#
# The rule per signal is *what evidence could this event invalidate*, and it is
# deliberately about evidence kind rather than about how alarming the event is:
#
#   a dead URL          -> the places that LINK to it
#   a paywall           -> the places that LINK to it
#   money changed       -> everywhere, because price changes the instruction itself
#   the thing is dying  -> everywhere, for the same reason
#   the UI moved        -> where the UI is DEPICTED or walked through
#   a version moved     -> where a version is pinned or the thing is executed
#   a model retired     -> where the model ID is passed to an API, and the sheet
#   the docs were rewritten -> the places that LINK to the docs
#   an n8n node changed -> the workflows that WIRE that node
#
# A signal with no rule keeps the whole footprint, because inventing a narrower answer
# would be a guess dressed as a measurement.

_LINK = ("link:a_href", "link:iframe", "link:bare", "link:markdown")
_REGISTRY_USE = ("install_command", "solution_import", "sheet_pin", "n8n_workflow",
                 "test_case_enum", "sheet_declared")

# --------------------------------------------------------- reach, per OBSERVATION
#
# The rule that stops this table being rewritten every time somebody reports a finding
# that claimed too much: **a finding may reach only what the observation behind it
# justifies.**
#
# It was declared per FINDING SIGNAL before, and that is the bug generator. A finding
# signal is a bucket, and several different observations pour into each one:
#
#     S4  registry_missing | registry_deprecated | no_release_in_2y  a PACKAGE is dead
#         sunset_language_about_subject                              the VENDOR says so
#     S3  free_tier_language_lost | pricing_restriction_language     money changed
#         pricing_page_changed                                       a PAGE was rewritten
#
# A reach declared on the bucket is therefore either too wide for some members or too
# narrow for others, and too wide is the one that ships: it reads as a bigger finding,
# nobody's test fails, and the reviewer is sent to places nothing happened. Every defect
# of this shape found so far is the same mistake at a different level -
#
#     Composio S1   the dependency's whole footprint, for one dead URL
#     OpenAI   S5   21 reading materials that say "OpenAI", for a docs reorganisation
#     lmOpenAi S9   a glossary row scored as a wired node
#     Nango    S10  "verified" (it exists) rendered as "candidate replacement"
#
# - so it is fixed once, here, by keying on the observation instead.
#
# `None` means "everywhere the dependency is taught" and has to be EARNED: it is for
# events that change the instruction itself, wherever it appears.
EVIDENCE_REACH: dict[str, Optional[tuple]] = {
    # A URL event reaches the places that link to that URL.
    "url_gone": _LINK, "domain_parked": _LINK, "redirected_off_path": _LINK,
    "access_wall_language": _LINK, "page_text_changed": _LINK,
    "pricing_page_changed": _LINK,
    # A registry event is about a package: it reaches where the package is installed,
    # imported, pinned or declared - not every paragraph that names it.
    "registry_missing": _REGISTRY_USE, "registry_deprecated": _REGISTRY_USE,
    "no_release_in_2y": _REGISTRY_USE,
    "new_release": _REGISTRY_USE, "major_behind_taught_pin": _REGISTRY_USE,
    # The vendor itself saying it is sunsetting, or that the money changed. These change
    # what the course should TEACH, so they reach everywhere it is taught.
    "sunset_language_about_subject": None,
    "deprecation_notice_added": None,
    # A retired field reaches the records that WRITE it, and that is not expressible
    # here: `evidence_source` records how the DEPENDENCY was found, not what the record
    # contains. `scope_locations` special-cases S13 onto `score.PARAM_SITES`,
    # which the extractor measured. `None` keeps `verify`'s over-reach check from
    # second-guessing a scope that is already narrower than any rule it could apply.
    "taught_field_deprecated": None,
    "free_tier_language_lost": None, "pricing_restriction_language": None,
    # A model id is passed to an API; the workbook row names it too.
    "model_shutdown_passed": ("model_id", "sheet_declared", "sheet_pin"),
    "model_deprecation_declared": ("model_id", "sheet_declared", "sheet_pin"),
    "model_tier_restricted": ("model_id", "sheet_declared", "sheet_pin"),
    # n8n: the workflows that wire the node, and the tables that merely name it.
    "node_removed_upstream": ("n8n_workflow", "n8n_mention"),
    "breaking_change_declared": ("n8n_workflow", "n8n_mention"),
    "breaking_change_possible": ("n8n_workflow", "n8n_mention"),
    "n8n_new_release": ("n8n_workflow", "n8n_mention"),
    "node_named_in_release_notes": ("n8n_workflow", "n8n_mention"),
}

_RUNTIME = ("install_command", "solution_import", "sheet_pin", "n8n_workflow",
            "test_case_enum")
_DEPICTED = ("SESSION_PPT", "LEARNING_RESOURCE")

# evidence_source prefixes/values a signal can reach; None = every location.
SIGNAL_EVIDENCE: dict[str, Optional[tuple[str, ...]]] = {
    "S1": _LINK,
    "S2": _LINK,
    "S3": None,
    "S4": None,
    "S5": _LINK,                      # widened by object_type below
    "S6": _RUNTIME,
    "S7": ("model_id", "sheet_declared", "sheet_pin"),
    "S8": _LINK,
    # Both: a breaking change invalidates the workflows that wire the node AND the
    # reference table that names it. They are different work and very different
    # urgency, which `EVIDENCE_WEIGHT` and the severity cap below express - not a
    # filter that would drop the glossary row and leave it wrong for ever.
    "S9": ("n8n_workflow", "n8n_mention"),
    "S10": None,
    "S11": None,
    "S12": None,
}
MAX_LOCATIONS = 12


def _get(loc, name: str) -> str:
    """Field access that works on a Location and on its serialised form.

    The API reads locations straight out of the artifact as dicts, and it needs the
    same answer as the analyser or the detail panel contradicts the finding above it.
    """
    if isinstance(loc, dict):
        return loc.get(name) or ""
    return getattr(loc, name, "") or ""


def s5_reach(redirects) -> str:
    """What an S5 is actually about, which decides how far it reaches.

    S5 is labelled "the taught steps changed" and every one of its findings is produced
    by a REDIRECT. A redirect is two different events wearing one name:

      `cookbook.openai.com` -> `developers.openai.com`   OpenAI reorganised its docs
      `windsurf.com`        -> `devin.ai/desktop`        the product was absorbed

    The first implicates the two links that point at it and nothing else. The second
    makes the prose that names the tool wrong as well. Reporting both as "redirected"
    is why an OpenAI docs move claimed 24 places, 21 of them reading material that
    merely says the word "OpenAI" - the tool had not changed at all.

    Returns `links` | `moved` | `behaviour`.
    """
    if not redirects:
        # No redirect recorded, so this is a researched IMPLEMENTATION claim: the
        # vendor says it changed how the thing works. That is the case where a
        # screenshot and a walkthrough really do go stale.
        return "behaviour"
    return "moved" if any(r.get("off_site") for r in redirects) else "links"


def reaches(signal: str, loc, reach: str = "behaviour") -> bool:
    """Can this signal's event invalidate what is at this location?

    Kept for callers that have a signal and no observations. `_reaches_with` is the
    real test; this is it with the per-signal fallback already resolved.
    """
    return _reaches_with(signal, loc, reach, SIGNAL_EVIDENCE.get(signal, None))


def _reaches_with(signal: str, loc, reach: str, sources: Optional[tuple]) -> bool:
    if sources is None:
        return True
    if _get(loc, "evidence_source") in sources:
        return True
    if signal == "S5":
        # A UX change invalidates the places that SHOW the UI as much as the places
        # that link to it - but only when the UI is what changed. A URL moving inside
        # the vendor's own estate changes nothing a deck depicts.
        if reach == "behaviour" and _get(loc, "object_type") in _DEPICTED:
            return True
        # The product moved off its own domain: every place that NAMES it is now
        # naming something that has been rebranded or absorbed, which is a real edit.
        if reach == "moved" and _get(loc, "evidence_source") in ("prose_name", "title"):
            return True
    return False


def evidence_reach(signal: str, probe_signals=()) -> Optional[tuple]:
    """What the OBSERVATIONS behind this finding justify reaching.

    The union over the probe signals actually present, because a finding can carry
    several - a tool whose free-tier wording went AND whose pricing page was rewritten
    reaches everywhere the first one does. `None` anywhere in the union means "the whole
    footprint", and it stays `None`.

    Falls back to the per-signal table only when there are no probe signals at all,
    which means a research-only finding: nothing was observed, a source was quoted, and
    the quote is about the dependency rather than about one of its pages.
    """
    known = [EVIDENCE_REACH[p] for p in probe_signals if p in EVIDENCE_REACH]
    if not known:
        return SIGNAL_EVIDENCE.get(signal, None)
    if any(r is None for r in known):
        return None
    out: tuple = ()
    for r in known:
        out += tuple(x for x in r if x not in out)
    return out


def reaching_locations(signal: str, affected_urls, locations,
                       redirects=(), probe_signals=()) -> tuple[list, list]:
    """Split `locations` into (reached by this signal, merely mentioning).

    Uncapped, and usable on both `Location` objects and their serialised dicts, so the
    analyser and the detail panel cannot disagree about what a finding affects.
    """
    reach = s5_reach(redirects) if signal == "S5" else "behaviour"
    sources = evidence_reach(signal, probe_signals)
    reached = [l for l in locations if _reaches_with(signal, l, reach, sources)]
    # S5 joins the URL narrowing when the redirect stayed inside the vendor's estate:
    # the event is "these links now land elsewhere", and the links are nameable.
    narrowing = ("S1", "S2", "S8") + (("S5",) if reach == "links" else ())
    if affected_urls and signal in narrowing:
        broken = {str(u).rstrip("/") for u in affected_urls}
        exact = [l for l in reached if _get(l, "url").rstrip("/") in broken
                 and _get(l, "url")]
        if exact:
            reached = exact
    keep = {id(l) for l in reached}
    return reached, [l for l in locations if id(l) not in keep]


# How a field site is recorded once the vendor has confirmed the field is theirs.
# Distinct from every other evidence kind because it is the only one that is not a name
# or a link: the course WRITES this key into a payload. Weighted as runtime evidence -
# a request body is something a student's code actually sends.
PAYLOAD_KEY = "payload_key"


# `{field: [site, ...]}` for the whole curriculum, set once per run by the caller that
# loaded the inventory. Shared rather than copied onto each dependency - see
# `extract/params.attach`.
PARAM_SITES: dict = {}


def set_param_sites(sites: dict) -> None:
    PARAM_SITES.clear()
    PARAM_SITES.update(sites or {})


def _recount_from_locations(dep: Dependency, f: Finding) -> None:
    """Re-derive the impact numbers from the places this finding actually reaches.

    Only called where the finding's scope is built rather than filtered — see the note
    at the call site. Everywhere else the dependency's own totals are the honest ones,
    because the finding is about the dependency.
    """
    locs = f.locations or []
    f.courses = sorted({l.course for l in locs if l.course})
    f.blast_radius = blast_radius(dataclasses.replace(dep, locations=locs))
    f.graded_locations = sum(
        1 for l in locs
        if l.is_graded and l.evidence_source not in ("prose_name", "question_tag"))
    executing = [l for l in locs if l.is_graded and dep._executes(l)]
    f.questions_executing = len({l.content_id for l in executing})
    f.questions_mentioning = len({l.content_id for l in locs if l.is_graded}) \
        - f.questions_executing
    f.question_ids = list(dict.fromkeys(l.content_id for l in locs if l.is_graded))[:6]


def _field_locations(dep: Dependency, f: Finding) -> list:
    """Every place the course writes a field this vendor has deprecated.

    The `Location`s are minted here rather than in the extractor on purpose: until the
    vendor's own page confirms the field is theirs, a key written near a dependency is a
    candidate and nothing more. Confirmation is what turns it into a place on the map.
    """
    out, seen = [], set()
    for row in f.deprecated_fields or []:
        for site in PARAM_SITES.get(row.get("field") or "", []):
            key = (site.get("content_id"), site.get("field_path"))
            if key in seen:
                continue
            seen.add(key)
            out.append(Location(
                course=site.get("course", ""), topic_name=site.get("topic_name", ""),
                unit_id=site.get("unit_id", ""), unit_name=site.get("unit_name", ""),
                content_id=site.get("content_id", ""),
                field_path=site.get("field_path", ""),
                evidence_source=PAYLOAD_KEY, object_type=site.get("object_type", ""),
                session_no=site.get("session_no")))
    return out


def scope_locations(dep: Dependency, f: Finding) -> None:
    """Split `dep.locations` into the places this finding reaches and the rest.

    Sets `f.locations`, `f.mention_locations` and `f.locations_scoped` in place.

    Where the finding names specific URLs and we recorded a URL on the link locations,
    narrow once more to the links that actually point at a broken URL. The narrowing is
    conditional on it finding something: `Location.url` is only populated for `link:*`
    evidence extracted after this field existed, so an older inventory has none, and a
    rule that silently empties a finding on old data is worse than one that stops at
    evidence kind.
    """
    if f.signal == "S13":
        # Built from the records that WRITE the field, not filtered from the ones where
        # the dependency was NAMED. `evidence_source` cannot express this question: it
        # records how the dependency was found in a record, not what the record
        # contains. Murf's ten `multiNativeLocale` records carry `link:a_href`,
        # `link:markdown` and `prose_name` where they carry anything at all — session
        # 20's module quiz writes the key seven times and never says "Murf" — so
        # intersecting with `dep.locations` reported three places out of ten and called
        # eight graded items one.
        reached = _field_locations(dep, f)
        seen = {(l.content_id, l.field_path) for l in reached}
        rest = [l for l in dep.locations
                if (l.content_id, l.field_path) not in seen]
    else:
        reached, rest = reaching_locations(f.signal, f.affected_urls, dep.locations,
                                           f.redirects, f.probe_signals)

    # Counted before the cap, and recorded, because `locations` is a display list and
    # every surface that counted it understated a busy finding by an order of magnitude.
    counts: dict = {}
    for l in reached:
        counts[l.object_type] = counts.get(l.object_type, 0) + 1
    f.affects_counts = counts
    f.affects_total = len(reached)

    if not reached:
        # Structural refusal, the same discipline `supported=False` applies elsewhere:
        # we could not establish where this lands, so we say that instead of handing
        # back the dependency's whole footprint and letting it read as a measurement.
        f.locations = []
        f.mention_locations = dep.locations[:MAX_LOCATIONS]
        f.locations_scoped = False
        return

    f.locations = reached[:MAX_LOCATIONS]
    f.mention_locations = rest[:MAX_LOCATIONS]
    f.locations_scoped = True



def front_door_gone(dep: Dependency, affected_urls) -> bool:
    """Did the DEPENDENCY die, or did one of its pages move?

    S1 covers both and they call for opposite actions. Measured on the 2026-09-18 run,
    every one of the six dead-link findings was a page:

        Composio      mcp.composio.dev/dashboard   composio.dev answers 200
        LangChain     docs.langchain.com/oss/.../pypdfloader
        Stability AI  api.stability.ai/v2beta/stable-image/generate/core
        Earth Ai      earth-ai.com/technology
        Gradio        xxxxx.gradio.live            a placeholder in the teaching text
        Ngrok         abc123.ngrok.io              a placeholder in the teaching text

    Not one of those vendors has gone anywhere, and the action for all six is to
    repoint a link. A replacement is only the right conversation when the front door
    itself is gone - which is what the CodeToTutorial backtest is: `codetotutorial.com/`
    returns 404 and the vendor's own page names a successor.
    """
    home = (dep.homepage or "").rstrip("/").lower()
    if not home:
        return False
    return any((u or "").rstrip("/").lower() == home for u in (affected_urls or []))



_CLAIMED_N = re.compile(r"\bthe (\d+) place\(s\)|\bin the (\d+) place\(s\)")


def _assert_claim_fits(f: Finding) -> None:
    """A finding may not say a number the location list does not support.

    Cheap, and it closes the specific way this goes wrong in production: the sentence
    and the list are built from different sources and drift. `recommend()`'s S1 line
    once read "the 6 place(s) Composio is linked" directly above twelve rows, because
    the sentence counted `dep.link_locations` and the list held `dep.locations[:12]`.
    Both now read `f.locations`, and this refuses to let them part again.
    """
    for m in _CLAIMED_N.finditer(f.recommendation or ""):
        claimed = int(m.group(1) or m.group(2))
        if claimed != len(f.locations):
            raise AssertionError(
                f"{f.finding_id} ({f.signal}) claims {claimed} place(s) but carries "
                f"{len(f.locations)}")


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
                # Left empty on purpose. `scope_locations` fills it once every signal
                # and affected URL is known, which is not until the loops below have
                # run - and seeding it here is exactly the bug this replaces.
                courses=dep.courses, locations=[],
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
            f.successors = list(probe.successors)
            f.redirects = list(probe.redirects)
            f.deprecated_fields = list(probe.deprecated_fields)
            # A retired field is a vendor DECLARATION, so it goes through the trust
            # layer like every other one — the same construction the n8n and model
            # paths use below. Without this the finding shipped `claims: []`, its
            # evidence URL was never tier-checked, and the one thing a reviewer needs
            # to see (the vendor's own sentence) reached no evidence panel.
            for row in probe.deprecated_fields:
                url, quote = row.get("evidence_url", ""), row.get("quote", "")
                if not url or len(quote) < 12:
                    continue
                try:
                    c = Claim.build(
                        kind=ClaimKind.IMPLEMENTATION,
                        statement=(f"{dep.canonical_name} marks the "
                                   f"`{row.get('field')}` field deprecated"),
                        source_url=url, quote=quote, subject=dep.subject())
                except UncitedClaim:
                    c = None
                # Same discipline as the declared-change path below: a claim that
                # cannot substantiate its own kind must not be attached, and the
                # refusal is counted rather than swallowed. Only the vendor can retire
                # the vendor's own field, so a page we happened to reach that does not
                # speak for this subject is not evidence of its API contract.
                if c is not None and c.substantiating:
                    if not any(x.source_url == c.source_url and x.quote == c.quote
                               for x in f.claims):
                        f.claims.append(c)
                elif c is not None:
                    f.probe_signals = list(f.probe_signals) + [
                        f"field_evidence_unciteable:{_domain(url)}"]
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
                if change.get("shutdown_date") and not f.shutdown_date:
                    f.shutdown_date = str(change["shutdown_date"])
                vend = VENDOR_SEVERITY.get(str(change.get("severity", "")).lower())
                if vend and SEVERITY_ORDER.index(vend) > SEVERITY_ORDER.index(f.severity):
                    f.severity = vend
                if sig == "breaking_change_possible":
                    # Absolute, not relative. This branch fires on a rule that names no
                    # node types at all - n8n matched it by prose - and its own detail
                    # line says "whether the course is affected needs a human check".
                    # A relative step down still landed on `high` for a widely-used
                    # node, which is a confident severity on an admittedly unconfirmed
                    # claim. `low` is what "someone should look" is worth.
                    f.severity = "low"
                url = change.get("doc_url") or ""
                quote = " ".join(x for x in (change.get("title"),
                                             change.get("description")) if x)
                # The rule's identity, carried onto the finding so `merge.py` can put
                # one rule's blast back together. n8n's `wait-node-subworkflow` names
                # 16 node types; the curriculum teaches 4 of them, and a reviewer was
                # handed the same paragraph four times.
                if change.get("rule_id"):
                    tag = f"n8n_rule:{change['rule_id']}"
                    if tag not in f.probe_signals:
                        f.probe_signals = list(f.probe_signals) + [tag]
                if change.get("version_checked") and change.get("taught_version"):
                    f.probe_signals = list(f.probe_signals) + [
                        f"n8n_version_checked:{change['taught_version']}"
                        f"{change.get('version_bound', '')}"]
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
            # NOT S1. A dead URL means a link moved, not that the vendor is finished -
            # and attaching a competitor to it said the second thing on evidence for
            # the first. Composio's `mcp.composio.dev/dashboard` 404s while
            # `mcp.composio.dev/` and `app.composio.dev` both answer 200 and redirect
            # to the reorganised dashboard; the finding nonetheless carried "Candidate
            # replacement: Nango". The action for a moved link is to repoint it.
            #
            # S4 is where a replacement belongs: `registry_missing`,
            # `registry_deprecated`, `no_release_in_2y` - the tool itself is going
            # away. S7 gets the vendor's own named successor, separately, below.
            independent = [a for a in verified_alts if a.independently_nominated]
            gone = "S1" in out and front_door_gone(dep, out["S1"].affected_urls)
            if "S4" in out or gone:
                # A tool that is actually going away: show every verified lead,
                # self-promoted ones included but labelled, because a reviewer with a
                # dying dependency wants the whole field even if half of it is
                # marketing.
                (out.get("S4") or out["S1"]).alternatives = verified_alts
            elif out:
                # Something else is wrong with this dependency - most often S1, a link
                # that moved - and a replacement is not the answer to it. Composio's
                # `mcp.composio.dev/dashboard` 404s while `mcp.composio.dev/` and
                # `app.composio.dev` both answer 200 and redirect to the reorganised
                # dashboard; the tool is fine and the link is stale. Attaching Nango to
                # that said the vendor was finished on evidence that one page moved,
                # and raising a separate S10 would say the same thing twice.
                for g in out.values():
                    g.probe_signals = list(g.probe_signals) + [
                        f"alternatives_not_surfaced:{len(verified_alts)}"]
            elif independent:
                # An S10 asserts "a better option exists", which is a claim about the
                # curriculum's choice of tool. A candidate that nominated itself cannot
                # support it: `nango.dev/blog/composio-alternatives` is an
                # advertisement, and it was the source of 9 of the 11 leads this
                # produced. Raising S10 from those made a competitor's SEO page into a
                # curriculum recommendation.
                verified_alts = independent
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
            # else: the probe found nothing wrong and every lead nominated itself.
            # Nothing is raised, and nothing needs to be - there is no finding to hang
            # the note on. The refutation accounting in the research artifact is where
            # "we looked and found only the competitors' own marketing" is recorded.

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
        # The wording is generated from the location set, so the two can only disagree
        # through a bug - but that bug is the one this system keeps making, and it is
        # invisible without an assertion because a too-wide claim still reads fine.
        # Checked after `recommend()` runs, below.
        # An n8n node the curriculum only NAMES cannot break a student's workflow,
        # because there is no workflow. Nine of the 41 taught nodes are in exactly that
        # position: 32 locations each, every one of them the same display-name
        # reference table, zero wired instances. The finding is still real - a table
        # listing a node n8n has removed is wrong and should be corrected - but it is
        # documentation work, so it is capped absolutely rather than stepped down, the
        # same treatment `breaking_change_possible` gets and for the same reason.
        if (f.signal == "S9" and dep.kind == "n8n_node"
                and not dep.wired_locations and dep.locations):
            f.severity = "low"
            f.summary = (f"{f.summary.rstrip('.')}. The curriculum names this node in a "
                         f"reference table but never builds a workflow with it, so "
                         f"nothing a student runs is affected.")
        # A URL the course PRINTS as an example - `xxxxx.gradio.live`,
        # `abc123.ngrok.io` - is not a dead link, and the probe already knows it:
        # `successor.is_placeholder` set `placeholder: True` and `recommend()` writes
        # "Not a broken link: ... is an example address a student generates for
        # themselves." The severity and the summary did not get the message, so the
        # triage list showed two `critical` rows reading "returns 404/410" whose own
        # recommendation said the opposite. A reviewer triages on severity and summary;
        # a finding that argues with itself there is one they stop trusting.
        #
        # Capped absolutely rather than stepped down, like the n8n reference-table case
        # above: the work is to unlink a line of text, whatever the blast radius.
        if f.signal == "S1" and any(sc.get("placeholder") for sc in f.successors or []):
            f.severity = "low"
            f.summary = (f"{f.affected_urls[0] if f.affected_urls else 'The taught URL'} "
                         f"is an example address students generate for themselves, "
                         f"published as a live link.")
        scope_locations(dep, f)
        if f.signal == "S13":
            # The counts have to follow the finding, not the dependency. An S13 is
            # scoped to the records that WRITE one field, which is routinely narrower
            # than everywhere the vendor is taught: Murf is named across three courses,
            # and `multiNativeLocale` is written in one. Leaving the dependency's
            # numbers on it said "Scope: 3 course(s)" over ten records that are all in
            # Building LLM Applications, and "0 graded items execute this" over eight
            # quiz questions that send the field.
            _recount_from_locations(dep, f)
        f.recommendation = recommend(dep, f)
        _assert_claim_fits(f)
        notes.compose(dep, f)          # deterministic triad; refine() may replace it
        final.append(f)
    return sorted(final, key=lambda x: (-SEVERITY_ORDER.index(x.severity), -x.blast_radius))


def _field_summary(dep: Dependency, probe: ProbeResult) -> str:
    """What the vendor said, in the vendor's own terms."""
    rows = probe.deprecated_fields or []
    if not rows:
        return f"{dep.canonical_name} marks a field the course uses as deprecated."
    first = rows[0]
    more = f" (and {len(rows) - 1} more field(s))" if len(rows) > 1 else ""
    to = (f"; {dep.canonical_name} names `{first['successor']}` as its replacement"
          if first.get("successor") else "")
    return (f"{dep.canonical_name}'s own API reference marks `{first['field']}` "
            f"deprecated{to}. The course writes it{more}.")


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
        "taught_field_deprecated": _field_summary(dep, probe),
        "deprecation_notice_added": (
            f"{dep.canonical_name}'s own pages carry a deprecation notice that was not "
            f"there at the last check: \u201c{(probe.new_notices or [''])[0][:200]}\u201d"),
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


def _place(loc) -> str:
    """One location, as an address a reviewer can act on."""
    word = artifact_word(loc.object_type)
    sess = f"session {loc.session_no}" if loc.session_no else loc.unit_name[:40]
    return f"the {word} in {loc.course} / {sess}"


def where_line(dep: Dependency, f: Finding) -> str:
    """Which places this finding affects, named by what the reviewer has to open.

    Replaces a line that read "Starts at <course> / session N / <unit_name>". Two
    things were wrong with it. It printed the *unit* name, and the unit holding this
    curriculum's MCQ bank is called "Coding Practice" - so 25 of 43 findings announced
    "Coding Practice" while every one of them pointed at a quiz question and not one
    pointed at a coding question. And `locations[0]` was extract order, so "starts"
    named an arbitrary member of an unordered set.

    Now: the artifact type comes from `object_type`, which we recorded; the exemplar is
    the location that *executes* the dependency where one exists, because that is the
    one that is already broken rather than merely stale; and the phrasing leads with
    the count, because one place is the common case and "starts at" implies a sequence.
    """
    if not f.locations:
        if f.locations_scoped:
            return ""
        n = len(f.mention_locations)
        return (f" Affected places not determined: no place {dep.canonical_name} is "
                f"named ({n}+) is one this kind of change is known to reach - confirm "
                f"by hand before acting.")

    # The already-broken one first, then the graded ones, then the rest.
    ranked = sorted(f.locations,
                    key=lambda l: (not dep._executes(l), not l.is_graded,
                                   l.session_no or 999))
    head = _place(ranked[0])

    # `affects_counts`, not `f.locations`: the list is capped at 12 and the sentence
    # would then say "12 quiz questions" about 32 of them.
    by_type = f.affects_counts or {}
    spread = ", ".join(f"{n} {artifact_word(t, n != 1)}"
                       for t in ARTIFACT_ORDER if (n := by_type.get(t, 0)))

    if (f.affects_total or len(f.locations)) == 1:
        return f" Affects {head}."
    return f" Affects {spread} - starting with {head}."


def action_only(row: dict) -> str:
    """The action from a serialised finding, without the clause about where it lands.

    A list row is triaged from - severity, name, one instruction - and is read to decide
    what to open next. The location clause belongs in the detail panel, which says it
    better: grouped by session and by artifact type, counted, and deep-linked.

    `recommendation` and `affects_line` are built together by `recommend()`, so this
    removes a clause it can see rather than guessing at a sentence boundary. A
    model-refined `what_to_act` never contains it and comes back untouched.
    """
    action = (row.get("what_to_act") or row.get("recommendation") or "").strip()
    clause = (row.get("affects_line") or "").strip()
    if clause and clause in action:
        # The clause sits between two other sentences, so removing it leaves the space
        # that separated them. Collapse rather than strip: "available.  Candidate" is a
        # visible seam on every row that had an alternative attached.
        action = re.sub(r"\s{2,}", " ", action.replace(clause, "")).strip()
    return action


def recommend(dep: Dependency, f: Finding) -> str:
    """A concrete next action. Deterministic: no LLM required.

    The location clause is also stored on `f.affects_line`, so a surface that wants the
    action alone - the findings list, the run page - can drop it without string surgery
    on a sentence it did not build.
    """
    where = where_line(dep, f)
    f.affects_line = where.strip()

    alt_txt = ""
    if f.alternatives:
        a = f.alternatives[0]
        # A vendor-named successor arrives as a name only - it has no homepage until it
        # has been verified against its own domain. Rendering "DeepWiki ()" advertises
        # a missing field; saying so plainly tells a reviewer what is left to do.
        where_alt = f" ({a.homepage})" if a.homepage else " (homepage not yet verified)"
        free = "free path confirmed" if a.free_student_path else "free path unconfirmed"
        # "Candidate replacement" is a recommendation, and it may only be used when one
        # was actually made. `verified` means the candidate EXISTS and publishes
        # checkable pricing - the anti-hallucination test - and nothing about whether it
        # does the job the course teaches. `does_taught_job` is that judgement, and it is
        # set by `research/fit.py` — which runs in the weekly pipeline too, on by default
        # via `main.py`'s `judge_fit=not args.no_fit`, not only on the agent path as this
        # comment used to say. It still needs a model provider and it still leaves the
        # factor `None` when the quotes do not say, so most candidates arrive without
        # it. The wording below branches on `recommendable` and is correct either way:
        # a lead is not a replacement, and the sentence says which rung of the ladder
        # the thing actually reached.
        if a.recommendable:
            alt_txt = (f" Candidate replacement: {a.name}{where_alt} - {free}"
                       f"{'; signup required' if a.signup_required else ''}.")
        elif a.self_promoted:
            alt_txt = (f" One lead, {a.name}{where_alt}, but it was named by its own "
                       f"comparison page - marketing, not a recommendation.")
        elif a.opinion is not None:
            # Assessed, and the assessment declined. That is a different fact from "no
            # assessment was made" and a more useful one: it says the candidate's own
            # pages do not establish that it does the taught job, which is a gap a
            # reviewer can close in a minute by looking, or accept as a no.
            why = (a.opinion.one_line or "").rstrip(".")
            alt_txt = (f" One lead, {a.name}{where_alt}. Fit was assessed and could not "
                       f"be established{': ' + why if why else ''}.")
        else:
            alt_txt = (f" One lead to look at: {a.name}{where_alt} - {free}"
                       f"{'; signup required' if a.signup_required else ''}. "
                       f"Nobody has assessed whether it does the taught job.")

    # Scope the action to the links actually affected, not to every mention of the
    # dependency: "repoint the dead link in 845 locations" is false when one URL broke.
    url_note = ""
    if f.affected_urls:
        url_note = f" Affected URL: {f.affected_urls[0]}"
        if len(f.affected_urls) > 1:
            url_note += f" (+{len(f.affected_urls) - 1} more)"
        url_note += "."
    # Where it went. The probe already tried the vendor's own site and recorded what
    # answered, so the action can name a URL instead of asking the reviewer to go and
    # find one - which is the step they were doing by hand every week.
    move_note, placeholder = "", False
    for sc in f.successors or []:
        if sc.get("placeholder"):
            placeholder = True
            break
        if sc.get("url"):
            move_note = (f" Now live at {sc['url']} ({sc.get('note', '')}) - confirm it "
                         f"is the page the session meant.")
            break

    # The field edit, named exactly. `deprecated_fields` carries what the vendor's own
    # reference says, so the sentence can be specific without inferring anything.
    fields = f.deprecated_fields or []
    if fields:
        first = fields[0]
        rename = (f"rename it to `{first['successor']}`" if first.get("successor")
                  else "check the reference for its replacement")
        extra = (f" {len(fields) - 1} other taught field(s) are deprecated too: "
                 + ", ".join(f"`{x['field']}`" for x in fields[1:4]) + "."
                 if len(fields) > 1 else "")
        s13 = (f"{dep.canonical_name} still serves `{first['field']}` but its own API "
               f"reference marks it deprecated - {rename} in the session's request "
               f"payloads before the field is removed.{extra}")
    else:
        s13 = (f"A field the course sends to {dep.canonical_name} is marked deprecated "
               f"on its own API reference; check the payloads in the session.")

    reach = s5_reach(f.redirects)
    moved = next((r for r in (f.redirects or []) if r.get("off_site")), None)
    if reach == "links":
        hop = (f.redirects or [{}])[0]
        s5 = (f"Repoint the link{'s' if len(f.locations) != 1 else ''}: "
              f"{hop.get('from', 'the taught URL')} now lands on "
              f"{hop.get('to', 'another page')}. Same vendor, reorganised site - "
              f"nothing about how {dep.canonical_name} works has changed, so only the "
              f"{len(f.locations)} link(s) need editing.")
    elif reach == "moved":
        s5 = (f"{dep.canonical_name} has moved off its own domain: "
              f"{moved.get('from')} now lands on {moved.get('to')}. Check whether it "
              f"has been rebranded or acquired - if so the prose that names it is "
              f"wrong too, not just the link.")
    else:
        s5 = (f"Re-verify the taught steps for {dep.canonical_name} against its "
              f"current docs; screenshots and click-paths may be stale.{url_note}")

    # The lead sentence has to change, not just gain a clause: "repoint or replace the
    # dead link" is the wrong instruction when there is no link to repoint.
    s1 = (f"Not a broken link: {f.affected_urls[0] if f.affected_urls else 'this URL'} "
          f"is an example address a student generates for themselves. Remove the "
          f"hyperlink in the {len(f.locations)} place(s) it appears and show it as "
          f"sample text."
          if placeholder else
          f"Repoint or replace the dead link in the {len(f.locations)} place(s) "
          f"{dep.canonical_name} is linked.{url_note}{move_note}")

    base = {
        # `len(f.locations)`, not `dep.link_locations`: the two now measure the same
        # thing, and reading it off the finding means the sentence can never again
        # disagree with the list printed under it.
        "S1": s1,
        "S2": f"Check whether the taught step for {dep.canonical_name} still works "
              f"without an account; if not, rewrite the step or swap the tool.{url_note}",
        "S3": f"Re-check {dep.canonical_name}'s free tier against what the session "
              f"asks a student to do.",
        "S4": f"{dep.canonical_name} shows deprecation/abandonment signals - plan a "
              f"replacement before the next cohort.",
        # Three sentences, because a redirect is three different events. The one that
        # used to be printed for all of them - "re-verify the taught steps, screenshots
        # may be stale" - was right only for the third, and it was the third that
        # almost never happened.
        "S5": s5,
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
              # The action depends on whether the course BUILDS with the node or only
              # names it. "Re-import the workflow" is not something a reviewer can do
              # for a node that appears solely in a display-name reference table, and
              # nine of the 41 taught nodes are in exactly that position.
              + ("; correct or drop the reference-table row - no workflow builds with "
                 "this node." if dep.kind == "n8n_node" and dep.locations
                 and not dep.wired_locations
                 else "; re-import the workflow and confirm node behaviour."),
        # Deliberately not "a better-suited tool is now available" - nothing here
        # established that. What was established is that a named alternative exists and
        # somebody other than itself pointed at it.
        "S10": f"A lead worth a look, not a conclusion: something in the same space as "
               f"{dep.canonical_name} exists and was pointed at by a third party.",
        "S11": "Review course coverage against current industry expectations.",
        # Names the field and the successor, because that IS the edit. "Review Murf"
        # would send a reviewer to read a healthy status page; "rename
        # multiNativeLocale to locale" is a find-and-replace they can do today.
        "S13": s13,
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
