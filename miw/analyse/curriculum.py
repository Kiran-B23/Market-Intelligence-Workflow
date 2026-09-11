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
from dataclasses import dataclass, field
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


@dataclass
class SessionDoc:
    """One session, as a searchable description of its deck."""
    course: str
    session_no: int
    session_name: str
    topic_name: str = ""
    outline: str = ""
    key_takeaways: str = ""
    row: int = 0
    workbook: str = ""

    # Derived, filled by CurriculumIndex.
    taught: set = field(default_factory=set)      # normalised keys of taught items
    tokens: list = field(default_factory=list)
    blob: str = ""                                # whole description, normalised

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
        have = set(self.tokens)
        return all(t in have for t in words)


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
            text = " ".join([d.session_name, d.topic_name, d.outline, d.key_takeaways])
            d.tokens = _tokens(text)
            d.blob = normalise(text)
            d.taught = {normalise(f) for f in _fragments(text)}
            d.taught |= {normalise(t) for t in d.tokens}
            d.taught.discard("")
        # Document frequency over the session corpus. A term appearing in 60 of 71
        # sessions ("model", "ai") must not decide placement; one appearing in two
        # ("diffusion", "chain") should.
        self.df: dict[str, int] = {}
        for d in self.docs:
            for t in set(d.tokens):
                self.df[t] = self.df.get(t, 0) + 1
        self.n = max(1, len(self.docs))

    # ------------------------------------------------------------------ build
    @classmethod
    def from_outlines(cls, outlines: dict) -> "CurriculumIndex":
        """Build from `main._outlines_by_course()` — {course title: Outline}."""
        docs = []
        for course, outline in (outlines or {}).items():
            book = getattr(getattr(outline, "stats", None), "workbook", "") or ""
            for s in getattr(outline, "sessions", []):
                docs.append(SessionDoc(
                    course=course, session_no=s.session_no,
                    session_name=s.session_name, topic_name=s.topic_name,
                    outline=s.outline, key_takeaways=s.key_takeaways,
                    row=s.row, workbook=book))
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
        """
        return [t for t in dict.fromkeys(_tokens(name))
                if self.df.get(t, 0) <= self.n / 2]

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
