"""URL health, content drift, and access-wall detection.

The URLs probed are the ones the curriculum actually links to, not just a vendor
homepage: a live marketing page says nothing about a dead deep link, and the deep link
is what a student clicks.

Nothing here reports a wall or a rewrite on its own. Almost every SaaS homepage says
"Sign in" and lists prices, so an absolute reading of those phrases would flag every
tool we teach in week one. What matters is *change* against last week's snapshot,
which is why this module records signals and leaves the judgement to `analyse`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from miw.net import Fetch, fetch, main_text, text_hash

# Phrasing that gates content, as opposed to merely offering a login.
WALL_PHRASES = (
    "sign in to continue", "log in to continue", "login to continue",
    "sign up to continue", "create an account to continue", "subscribe to continue",
    "to continue reading", "members only", "upgrade to view", "upgrade your plan",
    "start your free trial", "free trial has ended", "trial expired",
    "you have reached your limit", "quota exceeded", "requires a paid plan",
    "available on paid plans", "this feature requires",
)
# HOW a vendor lets you in, as opposed to WHETHER it does. `WALL_PHRASES` above answers
# the second: a wall appeared and the taught step now demands an account. These answer
# the first, and the difference matters to a session that walks a student through setup
# — a tool that swaps an API key for OAuth is perfectly open and every screenshot of its
# key page is wrong.
#
# Presence is NOT the signal; every docs page names a mechanism. A mechanism appearing
# or disappearing between two runs is, which is the shape `pricing.free_signals` already
# uses for free-tier wording.
AUTH_PHRASES = (
    "api key", "api token", "secret key", "publishable key", "access token",
    "personal access token", "bearer token", "service account", "client secret",
    "client id", "oauth", "openid connect", "session token", "basic auth",
    "sign in with google", "sign in with github", "sign in with microsoft",
    "device code", "service principal", "managed identity",
)

# Split by strength. A vendor docs index legitimately contains "migrate to" and
# "sunset" while describing something else entirely - on Google's model docs those
# words sit next to a *different* model's deprecation notice. Weak phrases are recorded
# as flags; only strong phrases, and only near the subject's own name, move a status.
SUNSET_PHRASES_STRONG = (
    "has been deprecated", "is deprecated", "now deprecated", "no longer maintained",
    "no longer available", "has been discontinued", "is shutting down",
    "will be shut down", "has been archived", "we are winding down",
    "has been retired", "is retired", "has been sunset", "was sunset",
)
SUNSET_PHRASES_WEAK = (
    "sunset", "end of life", "end-of-life", "migrate to", "successor",
    "legacy version", "deprecation", "will be removed",
)
SUNSET_PHRASES = SUNSET_PHRASES_STRONG + SUNSET_PHRASES_WEAK

# How close a phrase must sit to the subject's name to be about the subject.
PROXIMITY_CHARS = 240
PAID_PHRASES = (
    "per month", "/month", "per seat", "billed annually", "upgrade to pro",
    "free plan", "free tier", "pricing", "credits",
)
PARKED_PHRASES = (
    "domain is for sale", "buy this domain", "this domain may be for sale",
    "parked domain", "domain parking", "expired domain",
)


def _hits(text: str, phrases: tuple[str, ...]) -> list[str]:
    low = text.lower()
    return [p for p in phrases if p in low]


def _hits_near(text: str, phrases: tuple[str, ...], terms: tuple[str, ...]) -> list[str]:
    """Phrases occurring within PROXIMITY_CHARS of one of the subject's own names.

    This is what separates "n8n has been deprecated" from an n8n docs page that
    happens to describe a deprecated *node*. Without it, every well-documented vendor
    looks like it is shutting down.
    """
    low = text.lower()
    spots = [m.start() for t in terms if t for m in re.finditer(re.escape(t.lower()), low)]
    if not spots:
        return []
    out = []
    for p in phrases:
        for m in re.finditer(re.escape(p), low):
            if any(abs(m.start() - s) <= PROXIMITY_CHARS for s in spots):
                out.append(p)
                break
    return out


@dataclass
class UrlObservation:
    url: str
    status: int | None = None
    final_url: str = ""
    redirected_off_path: bool = False
    gone: bool = False
    blocked: bool = False
    reachable: bool = False
    error: str = ""
    text_hash: str = ""
    wall_phrases: list[str] = field(default_factory=list)
    sunset_phrases: list[str] = field(default_factory=list)
    sunset_near_subject: list[str] = field(default_factory=list)
    # The sentences those phrases sit in, so a later run can tell a NEW notice from a
    # page that has always talked about deprecations. The phrase list alone cannot:
    # on Google's release notes `sunset_near_subject` reads ["now deprecated",
    # "will be shut down"] every week, unchanged, whatever was announced this week.
    sunset_sentences: list[str] = field(default_factory=list)
    # Which authentication mechanisms this page names. See `AUTH_PHRASES`.
    auth_phrases: list[str] = field(default_factory=list)
    # Fields this page labels deprecated, with the successor it names. See
    # `deprecated_fields` for why a reference page needs its own reader.
    deprecated_fields: list[dict] = field(default_factory=list)
    # API versions this page says are ending, read generically because an observation
    # never keeps raw text. The runner intersects these with the versions the course
    # actually calls. See `api_version_notices`.
    api_version_notices: list[dict] = field(default_factory=list)
    paid_phrases: list[str] = field(default_factory=list)
    parked: bool = False
    title: str = ""


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _sunset_sentences(text: str, subject_terms: tuple[str, ...] = (),
                      limit: int = 40) -> list[str]:
    """Sentences that say something is ending, near the subject's own name.

    The unit is the sentence rather than the phrase because the question a weekly run
    has to answer is "is this notice NEW", and only a sentence is specific enough to
    be counted once.
    """
    terms = tuple(t.lower() for t in subject_terms if t)
    out: list[str] = []
    for raw in _SENT_SPLIT.split(text or ""):
        sent = raw.strip()
        if not (12 < len(sent) < 400):
            continue
        low = sent.lower()
        if not any(ph in low for ph in SUNSET_PHRASES_STRONG):
            continue
        if terms and not any(t in low for t in terms):
            continue
        out.append(sent)
        if len(out) >= limit:
            break
    return out


# --- deprecation as a REFERENCE PAGE writes it ------------------------------
#
# `SUNSET_PHRASES_*` were written for changelogs, which announce in sentences. An API
# reference does not: it prints a definition list and hangs a badge on the field.
#
#   multiNativeLocale  string  Optional  Deprecated
#     This field is superseded by locale field. Please migrate to locale field.
#
#   exclude_domains    array or null  Optional  Deprecated
#     Deprecated: Use search_settings.exclude_domains instead.
#
# Measured across the doc pages the curriculum links to: of the sampled pages that say
# something is ending, NONE matched a strong phrase. Both examples above are live, on
# two different vendors, and the curriculum sends the first one in three sessions.
#
# The field name is the point. A bare "Deprecated" anywhere on a page is worthless -
# it matches a nav item, a filter chip, a changelog heading. Bound to the identifier it
# labels, and to the successor named beside it, it is a fact specific enough to act on.
_FIELD = r"[A-Za-z_][A-Za-z0-9_.]{2,48}"
_SUCCESSOR_CUE = (r"superseded by|use|in favou?r of|migrate to|replaced by|"
                  r"please use|instead use")
# The badge follows the field within a short window of type/optionality words, so an
# unrelated "Deprecated" further down the page cannot reach back and label it.
# The successor is usually on the line AFTER the badge — the badge ends the signature
# line and the explanation starts the next one — so the tail spans a bounded couple of
# lines rather than stopping at the first newline.
_DEPRECATED_FIELD = re.compile(
    rf"\b({_FIELD})\b(?P<between>[^.!?\n]{{0,80}}?)\bdeprecated\b"
    rf"(?P<after>[^\n]{{0,160}}(?:\n[^\n]{{0,200}}){{0,2}})", re.I)
_NAMED_SUCCESSOR = re.compile(
    rf"(?:{_SUCCESSOR_CUE})\s+`?({_FIELD})`?", re.I)
# Words that mean the match is prose about the page, not a labelled field.
_NOT_A_FIELD = {"the", "this", "is", "are", "was", "were", "has", "have", "be", "been",
                "it", "they", "which", "that", "api", "field", "parameter", "method",
                "endpoint", "model", "and", "or", "now", "all", "any", "these", "those"}

# A field declaration states a TYPE. That is what a reference page IS, and it is the
# rule that separates a parameter from a heading that happens to carry the same badge.
#
# Found by holding out every vendor the detector was built from and running it against
# ones it had never seen. It behaved on Deepgram (`diarize` -> `diarize_model`) and
# ElevenLabs, and on Stripe it reported the field `Create` out of
#
#     Create a charge  deprecated  Ask about this section
#
# which is a section heading. Requiring a type token in the window rejects it and keeps
# all four true positives, because every one of them declares one:
#
#     multiNativeLocale  string   Optional  deprecated        (Murf)
#     exclude_domains    deprecated  array or null  Optional  (Groq)
#     optimize_streaming_latency  integer or null  Optional  deprecated  (ElevenLabs)
#     diarize  boolean  Optional  Defaults to false  deprecated          (Deepgram)
_TYPE_TOKEN = re.compile(
    r"\b(string|str|boolean|bool|integer|int|number|float|double|long|array|list|"
    r"object|map|dict|enum|null|uuid|date|datetime|timestamp|file|binary|any)\b", re.I)

# A successor is an identifier, not an English word. `use_pvc_as_ivc`'s row reads
# "we won't use PVC versioning", and the bare cue `use` lifted `PVC` out of it — a
# capitalised acronym mid-sentence, not a field anyone can rename to.
_LOOKS_LIKE_FIELD = re.compile(r"^(?=.*[a-z])([a-z][A-Za-z0-9_.]*|[A-Za-z0-9]+[_.][A-Za-z0-9_.]+)$")

# A METHOD declares a signature where a field declares a type, and that is the only
# difference between them worth encoding. Both are the same proof - this name is a
# member the vendor documents, not a word occurring in a sentence - so this is one more
# accepted shape of declaration rather than a second detector.
#
# Written this way on purpose. The obvious alternative was a rename detector of its own,
# built from a phrase list, and there is already one: `official.py`'s IMPLEMENTATION
# cues ("renamed", "replaced", "removed the"). Measured over the whole inventory it
# produced three claims, none substantiating, two of them Wikipedia prose about OpenAI
# removing a chief executive. A phrase list finds sentences; it does not find renames.
#
# The parenthesis must follow the name with NO space and must close, because that is
# what a parameter list is. `^\s*\(` was the first attempt and it is prose-permeable:
# "voice (and its locale) are deprecated" reports `voice` as a renamed METHOD, offered
# to a reviewer as a call to go and fix. A declaration is written `generate_content(...)`
# and never `generate_content (...)`, so the space is the whole distinction.
_SIGNATURE = re.compile(r"^\([^(]*\)")


def deprecated_fields(text: str, limit: int = 30) -> list[dict]:
    """Fields a reference page labels deprecated, with the successor it names.

    Returns `{"field":, "successor":, "quote":}` per hit. The quote is verbatim and
    long enough to clear `Claim.build`'s floor, because this is evidence a reviewer
    will be asked to act on.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for m in _DEPRECATED_FIELD.finditer(text or ""):
        field = m.group(1)
        if field.lower() in _NOT_A_FIELD or field.lower() in seen:
            continue
        # Between the name and the badge there may be a type and an optionality, and
        # nothing else. A sentence in that gap means the two are not bound.
        between = (m.group("between") or "").strip(" \t:|-—,")
        if len(between.split()) > 6:
            continue
        after = m.group("after") or ""
        # See `_TYPE_TOKEN` and `_SIGNATURE`: a member declares a type or a signature.
        # Tested against the RAW group - `between` has had its leading punctuation
        # stripped, and for a signature the very first character is the evidence.
        signature = bool(_SIGNATURE.match(m.group("between") or ""))
        if not signature and not _TYPE_TOKEN.search(between + " " + after[:60]):
            continue
        succ = _NAMED_SUCCESSOR.search(after)
        successor = succ.group(1) if succ else ""
        if (successor.lower() in _NOT_A_FIELD
                or not _LOOKS_LIKE_FIELD.match(successor or "x")):
            successor = ""
        quote = re.sub(r"\s+", " ",
                       " ".join(x for x in (field, between, "deprecated", after) if x)).strip()
        if len(quote) < 12:
            continue
        seen.add(field.lower())
        out.append({"field": field, "successor": successor, "quote": quote[:280],
                    "shape": "method" if signature else "field"})
        if len(out) >= limit:
            break
    return out


# --- an API VERSION as a vendor announces its end ---------------------------
#
# The same sentence-level machinery `_sunset_sentences` uses, with one rule added and
# one loosened. `_sunset_sentences` matches its subject terms as substrings, which is
# right for a tool's name and catastrophic for a version: `v1` occurs inside `v10`,
# `rev1` and `Nov1`, so a substring match would report a vendor announcing its TENTH
# version as retiring the first. `research/news.py` learned the same lesson about tool
# names occurring inside ordinary words. The boundary lives in `_VERSION_TOKEN` below,
# which reads versions OFF the page rather than looking given ones up in it.

# A third rule, and it is restraint rather than precision. A sunset phrase plus a
# bounded `v1` still matches "Whisper v1 will be removed" on a page that also serves an
# API - a model's version, not the endpoint's. The sentence must therefore name the
# interface itself. The cost of being wrong here is a reviewer told their endpoint is
# closing, so a sentence that merely says "version v1 will be removed" is deliberately
# left alone, and this comment is where that choice is recorded rather than discovered.
_ABOUT_THE_API = re.compile(r"\bapi\b|\bendpoint|\bbase url\b", re.I)

# `SUNSET_PHRASES_STRONG` is tuned for a different sentence: "the TOOL is finished". It
# is past-tense and absolute - `has been deprecated`, `is retired` - because a tool that
# is going away says so about itself. A vendor retiring one API version announces it in
# the future tense and on a date: "the v1 API will be retired on 1 June", "we are
# sunsetting /v1/". None of those match, and adding them to the shared list would fire
# S4 and S8 on every reference page that says a field "will be removed".
#
# They are safe HERE for a reason that does not hold there: this set only ever runs
# against a sentence that already names a version the course calls, as a bounded token,
# in a sentence about the API. The three rules together are the precision; the phrase
# list on its own is not asked to carry it.
_VERSION_SUNSET_PHRASES = SUNSET_PHRASES_STRONG + (
    "will be removed", "will be retired", "will be deprecated", "will be discontinued",
    "will be shut off", "will stop working", "will no longer be supported",
    "sunsetting", "scheduled for removal", "end of life", "end-of-life",
)


# A version token as a page writes it, bounded so `v1` cannot be lifted out of `v10`.
_VERSION_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(v\d+(?:[a-z]+\d*)?|\d{4}-\d{2}-\d{2})(?![A-Za-z0-9])", re.I)


def api_version_notices(text: str, limit: int = 20) -> list[dict]:
    """Every sentence where this page says an API VERSION is ending.

    Generic on purpose. `UrlObservation` records derived facts and never raw page text,
    so the page is read once here without knowing which versions the curriculum calls,
    and the runner intersects the result with the ones it teaches. Keeping the raw text
    around to filter later would put a megabyte of HTML per URL into memory for a
    question answerable in one pass.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for raw in _SENT_SPLIT.split(text or ""):
        sent = raw.strip()
        if not (12 < len(sent) < 400):
            continue
        low = sent.lower()
        if not any(ph in low for ph in _VERSION_SUNSET_PHRASES):
            continue
        if not _ABOUT_THE_API.search(sent):
            continue
        for m in _VERSION_TOKEN.finditer(sent):
            v = m.group(1).lower()
            if v in seen:
                continue
            seen.add(v)
            out.append({"version": v, "quote": re.sub(r"\s+", " ", sent)[:280]})
            if len(out) >= limit:
                return out
    return out


def api_version_sunset(text: str, versions: tuple[str, ...],
                       limit: int = 10) -> list[dict]:
    """Those notices that concern a version the course actually CALLS.

    A vendor retiring a version we never taught is not our problem, and reporting it
    would put every long-lived API in the digest for ever.
    """
    want = {(v or "").lower() for v in versions} - {""}
    if not want:
        return []
    return [n for n in api_version_notices(text) if n["version"] in want][:limit]


def notice_key(sentence: str) -> str:
    """A stable key for one notice: case and whitespace folded, nothing else.

    Numbers are deliberately NOT normalised. Collapsing them looks tidy - it would fold
    a rotating "4 endpoints affected" into one key - and it silently merges the notices
    that matter most: `v1` with `v2`, `gemini-2.5-flash` with `gemini-3.8-flash`, and a
    shutdown date moved from March to June with the announcement that preceded it. A
    second model's retirement reading as "already seen" is a missed deprecation, which
    is the one outcome this signal exists to prevent.

    The cost of the strict key is the opposite error: a vendor re-wording a standing
    notice re-reports it once. `probe_state.notice_keys` unions rather than replaces,
    so that costs one line in one digest and never repeats.
    """
    import hashlib
    norm = " ".join((sentence or "").lower().split())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def observe(url: str, subject_terms: tuple[str, ...] = ()) -> UrlObservation:
    f: Fetch = fetch(url)
    o = UrlObservation(
        url=url, status=f.status, final_url=f.final_url or "",
        redirected_off_path=f.redirected_off_path, gone=f.gone, blocked=f.blocked,
        reachable=f.reachable, error=f.error,
    )
    if f.body:
        text = main_text(f.body)
        o.text_hash = text_hash(text)
        o.wall_phrases = _hits(text, WALL_PHRASES)
        o.auth_phrases = _hits(text, AUTH_PHRASES)
        o.sunset_phrases = _hits(text, SUNSET_PHRASES)
        o.sunset_near_subject = _hits_near(text, SUNSET_PHRASES_STRONG, subject_terms)
        o.sunset_sentences = _sunset_sentences(text, subject_terms)
        o.deprecated_fields = deprecated_fields(text)
        o.api_version_notices = api_version_notices(text)
        o.paid_phrases = _hits(text, PAID_PHRASES)
        o.parked = bool(_hits(text, PARKED_PHRASES))
        m = _TITLE.search(f.body)
        if m:
            o.title = re.sub(r"\s+", " ", m.group(1)).strip()[:160]
    return o
