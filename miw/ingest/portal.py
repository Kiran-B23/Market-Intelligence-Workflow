"""Portal-export adapter: course JSON -> a flat, addressable ContentRecord stream.

Traversal helpers (`is_session`, `unit_label`, `ordered_units`, `markdown_content`)
are vendored from `Market Intelligence Workflow/build_course_sheet.py`; the stable
sort in `ordered_units` matters because two units share `order == 5` in Gen AI's AI
Ethics topic.

Two failure modes drove the shape of this module, and both are silent:

1. **Pooled exams.** Questions live under `unit.contents[]` *or* under
   `exam_sections[i].contents[]` / `exam_details[i].question_details[]`. A walker that
   reads only the inline path skips them and reports success. `IngestStats` counts
   pooling units seen against pooling units traversed so the gap is visible.
2. **Signal provenance.** A tool name found in reference solution code is far stronger
   evidence than the same name in prose, so every record is tagged with the
   `evidence_source` it came from rather than being flattened into one text blob.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator, Optional

from miw.schema import ContentRecord

# --- vendored traversal helpers ---------------------------------------------


def is_session(unit: dict) -> bool:
    """A session is a LEARNING_SET unit carrying an INTERACTIVE_VIDEO content."""
    return unit.get("unit_type") == "LEARNING_SET" and any(
        c.get("learning_resource_type") == "INTERACTIVE_VIDEO"
        for c in unit.get("contents", []))


def unit_label(unit: dict) -> str:
    """unit_name, falling back to exam_details.title — QUIZ/ASSESSMENT have no name."""
    ed = unit.get("exam_details")
    ed_title = ed.get("title") if isinstance(ed, dict) else None
    name = unit.get("unit_name") or ed_title
    if not name and unit.get("unit_type") == "ASSESSMENT":
        return "Module Quiz"
    return name or "(unnamed unit)"


def ordered_units(topic: dict) -> list[dict]:
    """Sort by `order` only — a stable sort preserves array position for ties."""
    return sorted(topic.get("units", []), key=lambda u: u.get("order", 0))


# --- stats ------------------------------------------------------------------


@dataclass
class IngestStats:
    course: str = ""
    topics: int = 0
    units: int = 0
    sessions: int = 0
    contents: int = 0
    records: int = 0
    pooling_units_seen: int = 0
    pooling_units_traversed: int = 0
    pooled_questions: int = 0
    # Session numbering, which has two possible sources. `sessions` above is the
    # POSITIONAL count (LEARNING_SET units carrying a video); these record what the
    # workbook declared and how often the two disagreed, because on Intro to Gen AI
    # they disagreed for 81 of 104 units and nothing surfaced it.
    authoritative_sessions: int = 0
    session_from_workbook: int = 0
    session_inferred: int = 0
    session_conflicts: int = 0
    conflict_examples: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def count(self, source: str) -> None:
        self.by_source[source] = self.by_source.get(source, 0) + 1
        self.records += 1

    def skip(self, why: str) -> None:
        self.skipped.append(why)

    @property
    def pooling_gap(self) -> int:
        """Pooling units we saw but did not traverse. Must be zero."""
        return self.pooling_units_seen - self.pooling_units_traversed


# --- the walk ---------------------------------------------------------------


def _texts_from_content(c: dict, base: str) -> Iterator[tuple[str, str, str]]:
    """Yield (text, field_path, evidence_source) for one content item.

    Ordered strongest-evidence-first. Reference solution code is the highest-precision
    tool signal in the entire export: it contains the actual import statements.
    """
    for i, sol in enumerate(c.get("solutions") or []):
        code = sol.get("solution_answer")
        if code:
            yield str(code), f"{base}.solutions[{i}].solution_answer", "solution_code"
        for fld in ("title_content", "description_content", "evaluation_prompt"):
            if sol.get(fld):
                yield str(sol[fld]), f"{base}.solutions[{i}].{fld}", "solution_prose"

    for i, tc in enumerate(c.get("test_cases") or []):
        if tc.get("test_case_enum"):
            yield str(tc["test_case_enum"]), f"{base}.test_cases[{i}].test_case_enum", "test_case_enum"

    for i, code in enumerate(c.get("codes") or []):
        if code.get("code_content"):
            yield str(code["code_content"]), f"{base}.codes[{i}].code_content", "solution_code"

    if c.get("content"):
        yield str(c["content"]), f"{base}.content", "markdown"
    if c.get("short_text"):
        yield str(c["short_text"]), f"{base}.short_text", "title"

    for i, opt in enumerate(c.get("options") or []):
        if opt.get("content"):
            yield str(opt["content"]), f"{base}.options[{i}].content", "markdown"

    ae = c.get("answer_explanation")
    if isinstance(ae, dict) and ae.get("content"):
        yield str(ae["content"]), f"{base}.answer_explanation.content", "markdown"

    for i, sl in enumerate(c.get("slides") or []):
        if sl.get("slide_url"):
            yield str(sl["slide_url"]), f"{base}.slides[{i}].slide_url", "url_field"


def _emit(course: str, topic: dict, unit: dict, c: dict, base: str,
          session_no: Optional[int], source_file: str,
          stats: IngestStats) -> Iterator[ContentRecord]:
    for text, path, src in _texts_from_content(c, base):
        stats.count(src)
        yield ContentRecord(
            course=course,
            topic_name=topic.get("topic_name", ""),
            unit_id=unit.get("unit_id", ""),
            unit_name=unit_label(unit),
            unit_type=unit.get("unit_type", ""),
            content_id=str(c.get("question_id") or c.get("learning_resource_id") or ""),
            object_type=c.get("object_type", ""),
            content_type=c.get("content_type", ""),
            title=str(c.get("title") or c.get("short_text") or ""),
            body_text=text,
            field_path=path,
            evidence_source=src,
            source_file=source_file,
            session_no=session_no,
        )


def read_course(path: str, course: str,
                session_of_unit: Optional[dict] = None
                ) -> tuple[list[ContentRecord], IngestStats]:
    """Walk one portal export. Never raises on shape surprises — they land in stats.

    `session_of_unit` maps `unit_id` -> the session number the curriculum team assigned,
    read from the workbook by `ingest.outline`. Where it speaks it WINS, because
    `is_session()` only infers numbering from position and is measurably off: Intro to
    Gen AI's `Common Mistakes` is a LEARNING_SET carrying a video at position 8 but is
    not a numbered session, so every later session was reported one too high. The
    positional walk still runs — it is the fallback for units the workbook does not
    cover (and for a course with no workbook), and disagreements are counted rather
    than silently resolved.
    """
    with open(path) as fh:
        data = json.load(fh)
    obj = data[0] if isinstance(data, list) and data else data
    stats = IngestStats(course=course)
    out: list[ContentRecord] = []
    session_no = 0
    declared = session_of_unit or {}
    stats.authoritative_sessions = len(set(declared.values()))

    for ti, topic in enumerate(obj.get("topics", [])):
        stats.topics += 1
        units = ordered_units(topic)
        for ui, unit in enumerate(units):
            stats.units += 1
            ubase = f"[0].topics[{ti}].units[{ui}]"
            if is_session(unit):
                session_no += 1
                stats.sessions += 1

            # The number attached to every record from this unit. The workbook's
            # answer where it has one, the positional walk otherwise.
            told = declared.get(str(unit.get("unit_id") or "").strip())
            if told is None:
                effective = session_no
                if session_no:
                    stats.session_inferred += 1
            else:
                effective = told
                stats.session_from_workbook += 1
                if session_no and told != session_no:
                    stats.session_conflicts += 1
                    if len(stats.conflict_examples) < 8:
                        stats.conflict_examples.append(
                            f"{unit_label(unit)[:40]!r}: workbook s{told}, "
                            f"position s{session_no}")

            # Question tags sit on the unit, not on a content. In the Gen AI export
            # many of these tag names *are* tool names, which makes this the
            # highest-recall structured signal available.
            for qi, tag in enumerate(unit.get("question_tags") or []):
                name = tag.get("tag_name_enum") or tag.get("tag_name") or tag.get("tag")
                if name:
                    stats.count("question_tag")
                    out.append(ContentRecord(
                        course=course, topic_name=topic.get("topic_name", ""),
                        unit_id=unit.get("unit_id", ""), unit_name=unit_label(unit),
                        unit_type=unit.get("unit_type", ""), content_id="",
                        object_type="UNIT_TAG", content_type="TAG",
                        title=unit_label(unit), body_text=str(name),
                        field_path=f"{ubase}.question_tags[{qi}].tag_name_enum",
                        evidence_source="question_tag", source_file=path,
                        session_no=effective or None,
                    ))

            for ci, c in enumerate(unit.get("contents") or []):
                stats.contents += 1
                out.extend(_emit(course, topic, unit, c, f"{ubase}.contents[{ci}]",
                                 effective or None, path, stats))

            # --- pooled exams: the silent-skip trap -------------------------
            sections = unit.get("exam_sections")
            ed = unit.get("exam_details")
            has_pool = bool(sections) or isinstance(ed, list)
            if has_pool:
                stats.pooling_units_seen += 1

            traversed = False
            for si, sec in enumerate(sections or []):
                for ci, c in enumerate(sec.get("contents") or []):
                    stats.contents += 1
                    stats.pooled_questions += 1
                    traversed = True
                    out.extend(_emit(course, topic, unit, c,
                                     f"{ubase}.exam_sections[{si}].contents[{ci}]",
                                     effective or None, path, stats))
            if isinstance(ed, list):
                for ei, exam in enumerate(ed):
                    for qi, q in enumerate(exam.get("question_details") or []):
                        stats.contents += 1
                        stats.pooled_questions += 1
                        traversed = True
                        out.extend(_emit(course, topic, unit, q,
                                         f"{ubase}.exam_details[{ei}].question_details[{qi}]",
                                         effective or None, path, stats))
            if has_pool:
                if traversed:
                    stats.pooling_units_traversed += 1
                else:
                    stats.skip(f"{ubase} ({unit_label(unit)}): pooling unit yielded no questions")

    stats.records = len(out)
    return out, stats
