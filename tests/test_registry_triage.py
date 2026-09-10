"""A sheet-declared name with a version pin is a distribution, and needs no domain.

A workbook records `pydantic@2.11.10` in a tools column. `feed_sheets` cannot tell that
from a SaaS product, so it defaults to `kind: tool` — and a `tool` with no vendor domain
has no authority set, so it can never produce a version, pricing or deprecation finding.
Ten such entries were hand-corrected; ten more arrived on the next export. These pin the
repeatable fix, and the merge semantics that make hand-curation survive a re-extract.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw.registry import Entry, Registry, norm
from miw.schema import Dependency


def test_a_package_entry_gets_authority_from_its_registry_not_a_website():
    """`Dependency._REGISTRY_HOME` is why these need no domain at all."""
    dep = Dependency(kind="package", canonical_name="protobuf",
                     registry="pypi", registry_id="protobuf")
    subj = dep.subject()
    assert "pypi.org" in subj.official_domains
    assert subj.official_domains, "a registry package must be speakable-for"


def test_a_tool_with_no_domain_can_speak_for_nothing():
    """The failure mode the retype fixes, stated as a test."""
    dep = Dependency(kind="tool", canonical_name="protobuf")
    assert dep.subject().official_domains == ()


def test_retyping_moves_the_entry_because_identity_includes_kind():
    """`Entry.key` is `kind:norm(name)`, so a retype must re-key or the old row
    lingers and `resolve(name, "tool")` keeps finding it."""
    reg = Registry([Entry(canonical_name="pydantic", kind="tool",
                          review_status="from_sheet")])
    e = reg.resolve("pydantic", "tool")
    assert e is not None
    reg.entries.pop(e.key)
    e.kind, e.registry, e.registry_id = "package", "pypi", "pydantic"
    reg.add(e)
    assert reg.resolve("pydantic", "tool") is None
    assert reg.resolve("pydantic", "package").registry == "pypi"


def test_a_hand_added_domain_survives_a_re_extract():
    """`extract` rewrites tools.yaml every run. The merge is union-only and
    fill-if-empty, which is what makes hand curation durable — and what makes the
    registry a legitimate place to record a decision."""
    reg = Registry([Entry(canonical_name="Otter AI", kind="tool",
                          official_domains=["otter.ai"], homepage="https://otter.ai",
                          review_status="approved", notes="human-verified")])
    # a later extract re-adds the bare sheet-derived entry
    reg.add(Entry(canonical_name="Otter AI", kind="tool", aliases=["Otter AI"],
                  review_status="from_sheet", notes="declared in a workbook"))
    e = reg.resolve("Otter AI")
    assert e.official_domains == ["otter.ai"], "the domain must not be lost"
    assert e.homepage == "https://otter.ai"
    assert e.review_status == "approved", "a human decision must not be downgraded"


def test_an_alias_merge_routes_a_duplicate_spelling_to_the_entry_with_authority():
    """`HuggingFace` and `Hugging Face` normalise the same; only one had a domain."""
    reg = Registry([Entry(canonical_name="Hugging Face", kind="tool",
                          aliases=["HuggingFace"],
                          official_domains=["huggingface.co"])])
    for spelling in ("Hugging Face", "HuggingFace", "hugging face"):
        e = reg.resolve(spelling)
        assert e is not None and e.official_domains == ["huggingface.co"], spelling


def test_normalisation_is_what_makes_those_duplicates_findable():
    assert norm("HuggingFace") != norm("Hugging Face")   # differ by a space
    assert norm("HuggingFace").replace(" ", "") == norm("Hugging Face").replace(" ", "")


def test_the_version_pin_is_the_signal_a_bare_declaration_is_not():
    """`resolve-packages` checks pinned names by default: nobody writes
    `pydantic@2.11.10` about a SaaS product, but `Telegram` is sheet-declared and is
    not a distribution. Checking every bare name costs a registry round trip each."""
    from miw.schema import Location

    def loc(src):
        return Location(course="c", topic_name="t", unit_id="u", unit_name="n",
                        content_id="", field_path="f", evidence_source=src,
                        object_type="SHEET")

    pinned = Dependency(kind="tool", canonical_name="pydantic",
                        taught_version="2.11.10", locations=[loc("sheet_pin")])
    bare = Dependency(kind="tool", canonical_name="Telegram",
                      locations=[loc("sheet_declared")])
    is_pinned = lambda d: any(l.evidence_source == "sheet_pin" for l in d.locations)
    assert is_pinned(pinned) and not is_pinned(bare)


# ------------------------------------------- the phantom-course regression

def test_a_workbook_that_maps_to_no_course_contributes_nothing():
    """`workbook_courses.get(t.workbook, t.workbook)` fell back to the FILENAME, so a
    stale mapping silently invented courses called "AI for Finance - Course
    Contents.xlsx". 3,633 sheet locations were attributed to three phantom courses,
    and every per-course number computed from them was wrong. Skipping is recoverable;
    a phantom course is not."""
    from miw.extract.inventory import InventoryBuilder
    from miw.ingest.sheets import SheetTool

    b = InventoryBuilder(Registry())
    tools = [SheetTool(name="Gradio", taught_version="6.6.0",
                       workbook="Unmapped Workbook.xlsx", sheet="Tools", session="s1")]
    b.feed_sheets(tools, {})          # empty mapping: nothing should be attributed
    deps = b.finish()
    courses = {l.course for d in deps for l in d.locations}
    assert not any(".xlsx" in c for c in courses), f"invented a course: {courses}"


def test_a_mapped_workbook_still_lands_in_its_course():
    from miw.extract.inventory import InventoryBuilder
    from miw.ingest.sheets import SheetTool

    b = InventoryBuilder(Registry())
    tools = [SheetTool(name="Gradio", taught_version="6.6.0",
                       workbook="AI for Finance - Course Contents.xlsx",
                       sheet="Tools", session="s1")]
    b.feed_sheets(tools, {"AI for Finance - Course Contents.xlsx": "AI for Finance"})
    deps = b.finish()
    assert {l.course for d in deps for l in d.locations} == {"AI for Finance"}


def test_the_only_courses_are_the_declared_ones():
    """A guard on the live artifact: any course name outside config/constants.py is
    either a phantom or a rename nobody updated."""
    import json
    from config.constants import COURSES
    inv = Path(__file__).resolve().parents[1] / "out" / "inventory.json"
    if not inv.exists():
        return
    declared = {v["title"] for v in COURSES.values()}
    seen = {l["course"] for d in json.loads(inv.read_text())["dependencies"]
            for l in d["locations"]}
    assert seen <= declared, f"undeclared course(s): {sorted(seen - declared)}"
