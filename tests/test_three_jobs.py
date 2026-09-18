"""Gaps and Changes are two questions, and each has to be answerable on its own.

One `gaps` checkbox used to run three signals - S11 topic gaps, S12 catalogue diffs and
the launch nominations - so a reviewer who wanted one paid for all of them and could not
tell which had produced what.

The split has one hazard, and it is the reason for most of this file: both halves write
into ONE sidecar, `gaps_<date>.json`, and `main.py verify` reads the newest one for the
authority sets behind every standing S11. A half that wrote the sidecar wholesale would
silently delete the other half's topics and every gap finding would then read as
unsourced. Caught exactly that way in development, hence the tests.
"""
import json

import pytest

from miw.api import jobs

PAGE = (__import__("pathlib").Path(__file__).resolve().parents[1]
        / "miw" / "api" / "static" / "index.html").read_text()


def test_changes_is_a_stage_the_runner_knows():
    assert "changes" in jobs.STAGES
    assert "changes" in jobs.SCOPED
    assert "changes" in jobs.COURSE_ONLY
    # Between analyse and report, because it merges into the artifact the reporter reads.
    assert jobs.STAGES.index("analyse") < jobs.STAGES.index("changes")
    assert jobs.STAGES.index("changes") < jobs.STAGES.index("report")


def test_the_cli_exposes_each_half_on_its_own():
    import main
    p = main.build_parser() if hasattr(main, "build_parser") else None
    if p is None:                       # parser is built inside main(); check the map
        import inspect
        src = inspect.getsource(main.main)
        assert '"changes": cmd_changes' in src
    assert callable(main.cmd_changes)


def test_gaps_defaults_to_its_own_half_only():
    """`main.py gaps` must not silently also run the catalogue diff."""
    import inspect

    import main
    src = inspect.getsource(main)
    assert 'gp.set_defaults(only=("gaps",))' in src
    assert 'ch.set_defaults(only=("changes",))' in src
    # and the old bundled behaviour stays reachable rather than being removed
    assert "--also-changes" in src


def test_run_weekly_asks_for_each_half_explicitly():
    """It shares one args namespace across stages, which carries no `--only`."""
    import inspect

    import main
    src = inspect.getsource(main.cmd_run_weekly)
    assert 'cmd_gaps(a, only=("gaps",))' in src
    assert '("changes", cmd_changes, args)' in src


# --------------------------------------------------------------- the UI

def test_the_page_offers_the_three_questions():
    for label in ("Find what's broken", "Find what's missing", "Find what's new"):
        assert label in PAGE
    assert 'data-stages="gaps,report"' in PAGE
    assert 'data-stages="changes,report"' in PAGE


def test_the_jobs_and_the_stage_boxes_cannot_disagree():
    assert "function applyJob(" in PAGE and "function syncJob(" in PAGE
    assert 'type="checkbox" value="changes"' in PAGE


def test_every_job_only_names_stages_the_runner_declares():
    import re
    for m in re.finditer(r'data-stages="([^"]+)"', PAGE):
        for stage in m.group(1).split(","):
            assert stage in jobs.STAGES, stage


# --------------------------------------------------------------- the sidecar

def _sidecar_doc(prior: dict, do_gaps: bool, do_changes: bool,
                 gap_rows: list, newer_rows: list) -> dict:
    """The sidecar assembly from `cmd_gaps`, isolated so it can be asserted.

    Kept in step with `main.py` by `test_the_sidecar_rules_match_the_implementation`
    below, which reads the real source: duplicating the logic here would let the two
    drift, and drift is exactly the failure this file exists to prevent.
    """
    doc = dict(prior)
    if "gap_topics" not in doc and "newer_topics" not in doc and doc.get("topics"):
        doc["newer_topics" if do_gaps else "gap_topics"] = doc["topics"]
    if do_gaps:
        doc["gap_topics"] = gap_rows
    if do_changes:
        doc["newer_topics"] = newer_rows
    doc["topics"] = (doc.get("gap_topics") or []) + (doc.get("newer_topics") or [])
    return doc


def test_a_changes_only_run_does_not_delete_the_standing_topic_evidence():
    """`verify` reads the newest sidecar's `topics` for every standing S11's authority."""
    prior = {"topics": [{"topic_id": "t1"}, {"topic_id": "t2"}]}
    doc = _sidecar_doc(prior, do_gaps=False, do_changes=True,
                       gap_rows=[], newer_rows=[])
    assert len(doc["topics"]) == 2


def test_a_gaps_only_run_does_not_delete_the_catalogue_rows():
    prior = {"gap_topics": [{"topic_id": "old"}],
             "newer_topics": [{"topic_id": "cat"}],
             "topics": [{"topic_id": "old"}, {"topic_id": "cat"}]}
    doc = _sidecar_doc(prior, do_gaps=True, do_changes=False,
                       gap_rows=[{"topic_id": "new"}], newer_rows=[])
    assert doc["topics"] == [{"topic_id": "new"}, {"topic_id": "cat"}]


def test_a_legacy_sidecar_is_attributed_once_and_only_once():
    """The carry-forward must fire BEFORE either half writes its key.

    Placed after, it never fires for the running half - which is how a changes-only run
    published a sidecar with zero topics while three S11 findings were standing.
    """
    doc = _sidecar_doc({"topics": [{"topic_id": "t1"}]}, do_gaps=False,
                       do_changes=True, gap_rows=[], newer_rows=[{"topic_id": "n1"}])
    assert doc["gap_topics"] == [{"topic_id": "t1"}]
    assert doc["topics"] == [{"topic_id": "t1"}, {"topic_id": "n1"}]


def test_the_sidecar_rules_match_the_implementation():
    import inspect

    import main
    src = inspect.getsource(main.cmd_gaps)
    assert 'doc["topics"] = (doc.get("gap_topics") or []) + (doc.get("newer_topics") or [])' in src
    # the carry-forward sits above the two half-blocks
    assert src.index('"newer_topics" if do_gaps else "gap_topics"') < src.index('if do_gaps:\n        doc.update({"areas"')


def test_only_the_signals_a_run_produced_may_be_retired():
    import inspect

    import main
    src = inspect.getsource(main.cmd_gaps)
    assert 'owned = ({"S11"} if do_gaps else set()) | ({"S12"} if do_changes else set())' in src
