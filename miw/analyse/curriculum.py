"""What each session actually teaches — the curriculum side of gap analysis.

A gap finding has two halves and they fail in different ways. "This technique exists"
is an evidence problem, solved by `probe/frontier.py` and `Claim.build()`. "It belongs
in Intro to Gen AI session 8" is a *placement* problem, and placement is what makes the
finding actionable: "course coverage has fallen behind" is not a task, and
"add it to Advanced Prompt Engineering, whose Key Takeaways already list Zero-shot,
One-shot, Few-shot and CoT" is.

The only record of what a session teaches is the workbook. The published JSON export
carries units, questions and links; it does not carry the deck's own outline — 0 of the
24 `Session PPT` decks' outlines appear in it. So this module reads the same
`Course Outline` sheet that already settles session NUMBERING (`ingest/outline.py`) and
treats each session's `Outline` + `Key Takeaways` as that session's description.

Two consequences worth stating plainly:

* The comparison is between two texts written by different people for different
  purposes, so it is done on **fragments, not prose**. "Prompting Techniques
  (Zero-shot, One-shot, Few-shot, CoT)" is split into four taught items; matching the
  whole cell against a vendor's heading would find nothing and report four false gaps.
* Placement is **ranked and shown, never asserted alone**. A technique could plausibly
  belong in session 5 (Prompt Engineering Fundamentals, which teaches frameworks) or
  session 8 (Advanced Prompt Engineering, which teaches techniques). The finding names
  the best match and lists the runners-up with their scores, because a reviewer can
  settle that in five seconds and a scorer cannot.
"""
from __future__ import annotations

import math
import re
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from miw.probe.frontier import normalise

# Fragment separators. A workbook cell is a hand-written list: newlines, commas,
# semicolons, bullets, parentheses and dash-led continuations all separate items.
_SPLIT = re.compile(r"[\n\r,;()\[\]/·•]|\s+[-–—]\s+|\s{2,}")
_WORD = re.compile(r"[a-z0-9][a-z0-9+.#-]*")
_WS = re.compile(r"\s+")

# Words that carry no topical signal in a curriculum outline. Kept short on purpose: a
# long stoplist starts deleting real terms ("agent", "model", "chain" all matter here).
_STOP = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are",
    "we", "it", "its", "this", "that", "how", "what", "why", "when", "using", "use",
    "used", "our", "own", "your", "you", "be", "by", "as", "at", "from", "into", "vs",
    "etc", "more", "other", "others", "new", "intro", "introduction", "understanding",
    "overview", "part", "session", "hands", "handson", "hando", "handsonn", "demo",
})

# Substring matching on a normalised key is only safe above a length floor — the same
# floor `ingest/sheets.py` applies to workbook keys, for the same reason. "cot"
# appears inside "protocol"; "fewshot" appears inside nothing else.
_MIN_SUBSTRING_KEY = 5

# Which content records count as evidence that a session teaches something.
#
# Prose only. An MCQ that asks about chain-of-thought IS coverage — a student meets the
# idea there — and so is a reading material or a worked explanation. Source files are
# not: `import langchain` in a solution says the session uses a library, which the
# dependency inventory already records far more precisely, and feeding code into a
# natural-language coverage check mostly adds identifiers that collide with topic names.
# `url_field` is a bare URL, not language.
_PROSE_SOURCES = frozenset({
    "markdown", "solution_prose", "title", "question_tag", "test_case_enum",
})
_CODE_SOURCES = frozenset({"solution_code", "url_field"})

# Below this, `place()` declines to name a session. Calibrated on the four worked cases
# in `tests/test_gaps.py`: self-consistency and tree-of-thoughts must reach session 8,
# RISEN must reach session 5, and a topic from a different area must reach none.
MIN_PLACEMENT = 0.06


def _fragments(text: str) -> list[str]:
    return [f.strip(" .:-–—\"'") for f in _SPLIT.split(text or "") if f.strip()]


def _stem(word: str) -> str:
    """Fold inflections a curriculum outline and a vendor page spell differently.

    The workbook writes "Ready-to-use prompts", a vendor writes "prompt engineering",
    the session title writes "Prompting". All three are the same term, and matching
    them as three left session 8 — the session that is *about* prompting — scoring no
    higher on a prompting topic than a session that says "Updated AI Prompt" once.
    Deliberately cruder than a real stemmer: it only strips three suffixes, because a
    stemmer that conflates "model" and "modelling" also conflates terms that matter.
    """
    w = word
    for suf, floor in (("ing", 6), ("ies", 5), ("es", 5), ("s", 4)):
        if len(w) >= floor and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


# Body proximity. A window of ~60 terms is roughly a paragraph; the half-window stride
# means any phrase is wholly inside at least one window rather than being split across
# a boundary. Both are cheap to widen if coverage proves too strict — the measurement to
# watch is how many corroborated topics come back as "already taught".
_WINDOW, _STRIDE = 60, 30


def _windows(body: str) -> dict:
    """Body term -> the set of window ids it occurs in.

    An inverted index rather than a scan, because coverage asks the same question for
    every topic against every session: "do these terms share a window?" becomes a set
    intersection, which is constant-ish per topic instead of re-reading 400KB.
    """
    toks = _tokens(body)
    if not toks:
        return {}
    out: dict = {}
    for start in range(0, max(1, len(toks) - _STRIDE), _STRIDE):
        wid = start // _STRIDE
        for t in toks[start:start + _WINDOW]:
            out.setdefault(t, set()).add(wid)
    return out


def _tokens(text: str) -> list[str]:
    """Topical terms, with hyphenated compounds contributing their parts too.

    "Chain-of-Thought" is one token by the word pattern, which means the workbook cell
    that contains it shares no term with a vendor page describing "Tree of Thoughts" —
    and a reviewer spots that analogy instantly. Emitting the compound AND its parts is
    what lets the score see it, without loosening the match to substrings.
    """
    out = []
    for w in _WORD.findall((text or "").lower()):
        if len(w) > 2 and w not in _STOP:
            out.append(_stem(w))
        if "-" in w:
            out += [_stem(p) for p in w.split("-") if len(p) > 2 and p not in _STOP]
    return out


def session_bodies(path: str | Path = "out/content_records.jsonl",
                   max_chars: int = 600_000) -> dict:
    """Every session's own prose, keyed `(course, session_no)`.

    Reads the artifact `ingest` already writes. No fetching, no new parsing, and no new
    input: the records have carried `session_no` since session numbering moved to the
    workbook, so the grouping is exact rather than inferred.

    `max_chars` is a per-session guard, not a policy. The largest session today is
    412,232 characters (AI for Finance session 7), so nothing truncates in normal
    operation; the cap exists so one pathological record cannot make the index
    unbuildable. A cap that fires routinely would be a silent sampling policy, which is
    a different and much worse thing.
    """
    out: dict = {}
    path = Path(path)
    if not path.exists():
        return out
    with path.open() as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            sess = r.get("session_no")
            if not sess or r.get("evidence_source") not in _PROSE_SOURCES:
                continue
            # The deck summary is already in `outline`/`key_takeaways`; counting it
            # twice inflates nothing but says the same thing in two places.
            if r.get("object_type") == "SESSION_PPT":
                continue
            body = (r.get("body_text") or "").strip()
            if not body:
                continue
            key = (r.get("course") or "", int(sess))
            cur = out.get(key, "")
            if len(cur) >= max_chars:
                continue
            out[key] = f"{cur}\n{body}" if cur else body
    return out


def deck_bodies(path: str | Path | None = None) -> dict:
    """Slide text per session, `{(course, session_no): text}`, from `main.py decks`.

    Read from the artifact rather than fetched here: 85 decks are 0.6-14MB each against
    a host that throttles, which is a refresh cadence, not something `gaps` should do.
    Missing artifact means no slide text and nothing else - the index still builds.

    Only decks that actually parsed contribute. A deck we were served a sign-in shell
    for carries ~108 characters of boilerplate identical across every such session, and
    stamping that into the coverage index would be worse than having no deck text: it
    would make 51 sessions look like they all teach the same thing.
    """
    out: dict = {}
    if path is None:
        found = sorted(Path("out").glob("decks_*.json")) if Path("out").exists() else []
        if not found:
            return out
        path = found[-1]
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return out
    for row in data.get("decks") or []:
        if not row.get("supported"):
            continue
        text = (row.get("text") or "").strip()
        if text and row.get("session_no"):
            out[(row.get("course") or "", int(row["session_no"]))] = text
    return out


def merge_bodies(*sources: dict) -> dict:
    """Join several `{(course, session): text}` maps into one."""
    out: dict = {}
    for src in sources:
        for key, text in (src or {}).items():
            if not text:
                continue
            out[key] = f"{out[key]}\n{text}" if key in out else text
    return out


@dataclass
class SessionDoc:
    """One session: the deck's summary, plus everything the session actually contains."""
    course: str
    session_no: int
    session_name: str
    topic_name: str = ""
    outline: str = ""
    key_takeaways: str = ""
    row: int = 0
    workbook: str = ""
    # Every prose record the session owns - reading material, question text, solution
    # explanations. Joined from `out/content_records.jsonl`, which already carries a
    # session number on every record, so this costs one pass over a file we already
    # write and no fetching at all.
    #
    # Why this exists: the coverage check used to read only the four summary fields
    # above - 39,144 characters across all 71 sessions, against 9,226,771 characters of
    # course content already on disk. It was deciding "do we already teach this?" from
    # 0.4% of the evidence, which is the likeliest reason a topic taught in a reading
    # material or asked about in an MCQ was still reported as a gap.
    body: str = ""

    # Derived, filled by CurriculumIndex. Two token sets on purpose - `tokens` ranks
    # sessions against each other (placement) and `all_tokens` answers whether this
    # session mentions something at all (coverage). See `__init__`.
    taught: set = field(default_factory=set)      # normalised keys of taught items
    tokens: list = field(default_factory=list)    # SUMMARY terms only
    windows: dict = field(default_factory=dict)   # body term -> window ids it occurs in
    blob: str = ""                                # summary + body, normalised

    @property
    def label(self) -> str:
        return f"{self.course} session {self.session_no} — {self.session_name}"

    @property
    def field_path(self) -> str:
        """Where this description lives, in `extract/locate.py`'s path grammar.

        Points at the `Outline` cell of the session's own row, so the UI's finding
        panel can show the reviewer the exact text the placement was judged against
        instead of asking them to trust a score.
        """
        return f"{self.workbook}::Course Outline::Outline::row{self.row}"

    def teaches(self, key: str, terms: Iterable[str] = ()) -> bool:
        """Is this topic already in this session's own description?

        Two rules, either sufficient. The key rule catches an exact restatement
        ("Chain-of-Thought" vs "chain of thought"). The TERM rule catches the case the
        key rule misses and that matters most in practice: a vendor heading that names
        the same technique in a longer phrase. Google writes
        "Zero-shot vs few-shot prompts"; session 8's Key Takeaways read
        "Prompting Techniques (Zero-shot, One-shot, Few-shot, CoT)". The two normalise
        to different keys, so the key rule alone reported a technique the session has
        taught since day one as a gap - the single worst thing this module could do.

        The term rule demands that EVERY distinctive word of the topic's name already
        appear in this session, not merely one. One shared word is a coincidence;
        "break", "down" and "component" all appearing is not.
        """
        if key and key in self.taught:
            return True
        if key and len(key) >= _MIN_SUBSTRING_KEY and key in self.blob:
            return True
        words = [t for t in terms if t]
        if not words:
            return False
        # In the SUMMARY, "every distinctive word is present" is strong evidence,
        # because the whole thing is ~550 characters — anything appearing in it appears
        # in the same breath.
        if all(t in set(self.tokens) for t in words):
            return True
        # In the BODY it is not, and this is the rule that has to scale. Applying the
        # same "somewhere in the document" test to 400KB of reading material declared
        # "Start with clear instructions" taught in 36 sessions, because `start`,
        # `clear` and `instruction` each occur somewhere in almost any large body of
        # teaching prose. Co-occurrence is only evidence when the words are NEAR each
        # other, so the body is indexed as overlapping windows and the terms must share
        # one. That is what the summary rule always meant; it was implicit only because
        # the document was short enough for it not to matter.
        if not self.windows:
            return False
        shared = None
        for t in words:
            ids = self.windows.get(t)
            if not ids:
                return False
            shared = ids if shared is None else (shared & ids)
            if not shared:
                return False
        return bool(shared)


@dataclass
class Placement:
    """Where a topic belongs, and how confident that is."""
    session: Optional[SessionDoc] = None
    score: float = 0.0
    matched_terms: list = field(default_factory=list)
    runners_up: list = field(default_factory=list)   # [(SessionDoc, score)]
    reason: str = ""


class CurriculumIndex:
    """Every session in every course, as documents that can be searched by meaning.

    "By meaning" here is IDF-weighted term overlap over a 71-session corpus, not an
    embedding model. That is a deliberate trade: the corpus is tiny, the vocabulary is
    technical and shared between the two sides, and the score has to be *explainable*
    in the finding ("matched on: prompting, technique, reasoning"). An embedding gives a
    better score and a worse finding, and adds a runtime dependency to a system whose
    whole premise is that its ground truth is checkable by hand.
    """

    def __init__(self, docs: Iterable[SessionDoc]) -> None:
        self.docs = list(docs)
        for d in self.docs:
            summary = " ".join([d.session_name, d.topic_name, d.outline, d.key_takeaways])
            full = f"{summary}\n{d.body}" if d.body else summary
            # COVERAGE reads everything; PLACEMENT ranks on the summary alone.
            #
            # Not an oversight - the two questions want different evidence. "Do we
            # already teach this?" is answered by any mention anywhere, so it reads the
            # full text. "Which session does this belong in?" is a comparison BETWEEN
            # sessions, and `place()` normalises by the query's weight rather than the
            # document's length, so a session with 130KB of content would out-hit one
            # with a 550-character outline on sheer surface area. That is exactly the
            # failure that put "Tree of Thoughts" in a session about n8n merge nodes.
            # The outline is a deliberate summary of what a session is *about*, which
            # is the right basis for placement; the body is evidence of what it
            # *contains*, which is the right basis for coverage.
            d.tokens = _tokens(summary)
            d.blob = normalise(full)
            d.windows = _windows(d.body)
            d.taught = {normalise(f) for f in _fragments(full)}
            d.taught |= {normalise(t) for t in d.tokens}
            d.taught |= {normalise(t) for t in d.windows}
            d.taught.discard("")
        # Two document frequencies, over two different corpora, because they answer
        # two different questions.
        #
        # `df` counts the SUMMARIES and feeds `idf`, which weights placement — and
        # placement scores against summaries, so its notion of "common" has to be the
        # summaries' own.
        #
        # `body_df` counts everything, and feeds `topic_terms`, which decides which
        # words of a topic name are distinctive enough to demand. Getting that from the
        # summaries was a real defect: "support" occurs in almost none of 39,144
        # characters of outline, so it counted as distinctive, and coverage then
        # required it to appear beside "json" and "schema" in a body of 8.2M characters
        # where it is everywhere and means nothing. Distinctiveness has to be measured
        # against the corpus you are matching in.
        self.df: dict[str, int] = {}
        self.body_df: dict[str, int] = {}
        for d in self.docs:
            for t in set(d.tokens):
                self.df[t] = self.df.get(t, 0) + 1
            for t in set(d.tokens) | set(d.windows):
                self.body_df[t] = self.body_df.get(t, 0) + 1
        self.n = max(1, len(self.docs))

    # ------------------------------------------------------------------ build
    @classmethod
    def from_outlines(cls, outlines: dict,
                      bodies: Optional[dict] = None) -> "CurriculumIndex":
        """Build from `main._outlines_by_course()` — {course title: Outline}.

        `bodies` is `{(course, session_no): text}` from `session_bodies()`. Optional so
        the index still builds from the workbook alone — every test that predates this
        passes none, and a run before `ingest` has written any records still works.
        """
        bodies = bodies or {}
        docs = []
        for course, outline in (outlines or {}).items():
            book = getattr(getattr(outline, "stats", None), "workbook", "") or ""
            for s in getattr(outline, "sessions", []):
                docs.append(SessionDoc(
                    course=course, session_no=s.session_no,
                    session_name=s.session_name, topic_name=s.topic_name,
                    outline=s.outline, key_takeaways=s.key_takeaways,
                    row=s.row, workbook=book,
                    body=bodies.get((course, s.session_no), "")))
        return cls(docs)

    # ------------------------------------------------------------------ query
    def idf(self, term: str) -> float:
        return math.log(1.0 + self.n / (1.0 + self.df.get(term, 0)))

    def affinity(self, doc: SessionDoc, scope_terms: Iterable[str]) -> float:
        """How much this session is ABOUT an area, on 0..1.

        Without this, placement is decided by whichever session happens to share a
        common word with the topic's description. "Tree of Thoughts" landed in
        session 10 (an n8n workflow session) because its description says "multiple"
        and so does session 10's. Affinity is what encodes the thing a human knows
        instantly: session 8 is a prompting session and session 10 is not.
        """
        want = {t for term in scope_terms for t in _tokens(term)}
        if not want:
            return 1.0
        have = set(doc.tokens)
        denom = sum(self.idf(t) for t in want) or 1.0
        return sum(self.idf(t) for t in want if t in have) / denom

    def area_sessions(self, scope_terms: Iterable[str],
                      course: str = "") -> list[SessionDoc]:
        """Sessions belonging to a curriculum area.

        Area membership is decided by the session's OWN words, not by a hand-kept list
        of session numbers — a renumbered or rewritten session must not silently leave
        its area. A session qualifies when any scope term appears in its description.
        """
        keys = [normalise(t) for t in scope_terms if normalise(t)]
        words = {w for t in scope_terms for w in _tokens(t)}
        out = []
        for d in self.docs:
            if course and d.course != course:
                continue
            if any(k in d.blob for k in keys) or (words & set(d.tokens)):
                out.append(d)
        return out

    def teaches_anywhere(self, key: str, terms: Iterable[str] = (),
                         course: str = "") -> list[SessionDoc]:
        """Sessions that already cover this topic — in ANY course, by default.

        A topic taught anywhere is not a gap, even outside the area it was found in.
        Intro to Gen AI teaches ReAct in session 19 as an agent pattern; reporting it as
        a missing prompting technique because session 8 does not name it would be wrong
        in the way that costs a digest its authority.
        """
        return [d for d in self.docs
                if (not course or d.course == course) and d.teaches(key, terms)]

    def topic_terms(self, name: str) -> list[str]:
        """The distinctive words of a topic name, for `teaches`.

        Terms appearing in more than half the corpus are dropped: "prompt" is in most
        sessions of this curriculum, so requiring it proves nothing, while "affordance"
        or "component" decides the question on its own.

        Measured over the SUMMARIES (`df`), not over everything. Switching it to the
        full corpus was tried and reverted: across 8.2M characters almost every ordinary
        word appears in more than half the sessions, so nearly every term was dropped as
        common, topics were left with no distinctive terms at all, and `teaches` — which
        returns False on an empty term list — stopped recognising coverage it had been
        getting right. The summaries are a vocabulary of what sessions are *about*,
        which is the right place to ask whether a word discriminates.

        The known cost is a generic word in a topic name ("JSON schema **support**")
        counting as distinctive and blocking an otherwise sound match. That surfaces as
        an `info` finding with both citations attached, which a reviewer rejects in one
        click — and that rejection is recorded and learned from. A silent false negative
        would not be.
        """
        return [t for t in dict.fromkeys(_tokens(name))
                if self.df.get(t, 0) <= self.n / 2]

    # `df` is built from `tokens` (the summaries), which is what `place()` scores
    # against, so `idf` and `topic_terms` stay on the same footing they always were.
    # Coverage does not use `df` at all.

    def place(self, name: str, context: str,
              candidates: Optional[Iterable[SessionDoc]] = None,
              area_terms: Iterable[str] = ()) -> Placement:
        """Rank candidate sessions for a topic by IDF-weighted term overlap.

        Two factors multiply: how well the topic's own words match the session, and
        how much the session is about the area at all (`affinity`). A placement below
        `MIN_PLACEMENT` returns no session rather than the least-bad one — "this area,
        exact session unclear" is a usable finding and a confidently wrong session
        number is not.
        """
        pool = list(candidates if candidates is not None else self.docs)
        if not pool:
            return Placement(reason="no session in this curriculum area")
        # The topic NAME is worth more than the prose around it: a vendor's description
        # of chain-of-thought mentions "reasoning", "steps" and "model", and those
        # words place it no better than its name does.
        weighted: dict[str, float] = {}
        own: set[str] = set()
        for t in _tokens(name):
            weighted[t] = weighted.get(t, 0.0) + 3.0
            own.add(t)
        for t in _tokens(context):
            weighted[t] = weighted.get(t, 0.0) + 1.0
            own.add(t)
        # The area's own vocabulary is part of the query, because the item was found
        # enumerated UNDER that heading — "Tree of Thoughts" appearing in a section
        # titled "Prompting techniques" is evidence that it is a prompting topic, even
        # though its own description never uses the word. Weighted below the name so it
        # breaks ties between sessions in the area rather than choosing the area.
        for t in _tokens(" ".join(area_terms)):
            weighted[t] = weighted.get(t, 0.0) + 1.0

        # Normalise by the topic's own weight so a long vendor description cannot
        # outscore a short one just by being long.
        denom = sum(w * self.idf(t) for t, w in weighted.items()) or 1.0
        scored = []
        for d in pool:
            have = set(d.tokens)
            hits = [(t, w * self.idf(t)) for t, w in weighted.items() if t in have]
            if not hits:
                continue
            aff = self.affinity(d, area_terms)
            score = (sum(s for _t, s in hits) / denom) * (0.25 + 0.75 * aff)
            # A session may only be NAMED on the strength of the topic's own words.
            # Matching nothing but the area vocabulary is the same score for every
            # session in the area, so the winner would be decided by session number -
            # which is how "Tree of Thoughts" was placed in a session about n8n merge
            # nodes. Such a session may still appear as a runner-up.
            scored.append((d, score, [t for t, _s in sorted(hits, key=lambda x: -x[1])[:6]],
                           any(t in own for t, _s in hits)))
        if not scored:
            return Placement(reason="no term in common with any session in the area")
        scored.sort(key=lambda x: (-x[1], x[0].course, x[0].session_no))
        runners = [(d, round(s, 3)) for d, s, _t, _o in scored[:4]]
        nameable = [r for r in scored if r[3]]
        if not nameable or nameable[0][1] < MIN_PLACEMENT:
            return Placement(
                score=round(nameable[0][1], 3) if nameable else 0.0,
                runners_up=runners,
                reason=(f"no session matched this topic's own terms above "
                        f"{MIN_PLACEMENT}; the area is known but the session is a "
                        f"judgement call"))
        best, score, terms, _o = nameable[0]
        runners = [r for r in runners if r[0] is not best][:3]
        return Placement(session=best, score=round(score, 3), matched_terms=terms,
                         runners_up=runners,
                         reason="ranked by IDF-weighted term overlap with the "
                                "session's own Outline and Key Takeaways, weighted by "
                                "how much the session is about this area")
