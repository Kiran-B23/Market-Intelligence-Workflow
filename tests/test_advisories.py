"""Published vulnerabilities in the version a course actually pins.

81 taught packages had no security signal at all — a release yanked for a vulnerability
looked identical to a normal one, and students `pip install` these. OSV closes it for
free and without a key, but the raw API is three traps in a row, and each one produces a
number that reads as a measurement and is not one:

    transformers 4.46.3   44 rows -> 26 distinct issues
    gradio       4.44.0   43 rows -> 23
    langchain    0.3.7     4 rows ->  2

GHSA and PyPA both publish most issues, so the row count is nearly double. 26 of those
44 rows carried a CVSS vector and no rated band. And asking about the *package* rather
than the *version* answers a question nobody asked.
"""
import json

import pytest

from miw.probe import advisories as A
from miw.probe.advisories import advisories


class _F:
    """A `net.Fetch` stand-in. Every test here is offline."""
    def __init__(self, payload, ok=True, error="", status=200):
        self.body = json.dumps(payload) if payload is not None else ""
        self.ok, self.error, self.status = ok, error, status


def _vuln(vid, cve=None, band=None, fixed=None, withdrawn=None, summary="x"):
    v = {"id": vid, "summary": summary,
         "aliases": [cve] if cve else [],
         "database_specific": ({"severity": band} if band else {}),
         "affected": [{"ranges": [{"type": "ECOSYSTEM", "events": (
             [{"introduced": "0"}] + ([{"fixed": fixed}] if fixed else []))}]}]}
    if withdrawn:
        v["withdrawn"] = withdrawn
    return v


def _run(monkeypatch, vulns):
    monkeypatch.setattr(A, "fetch", lambda *a, **k: _F({"vulns": vulns}))
    return advisories("pkg", "pypi", "1.0.0")


# --------------------------------------------------------------- the refusals

def test_no_pinned_version_means_no_answer():
    """The union of every advisory ever filed against a package is true of the package
    and says nothing about this curriculum."""
    got = advisories("transformers", "pypi", None)
    assert got["supported"] is False
    assert "no taught version" in got["reason"]


def test_an_ecosystem_we_cannot_ask_about_refuses():
    assert advisories("x", "github", "1.0")["supported"] is False


def test_osv_being_unreachable_is_not_a_clean_bill_of_health(monkeypatch):
    monkeypatch.setattr(A, "fetch",
                        lambda *a, **k: _F(None, ok=False, error="timeout"))
    got = advisories("pkg", "pypi", "1.0.0")
    assert got["supported"] is False and "unreachable" in got["reason"]
    assert "found" not in got, "an unanswered question must not read as 'none found'"


def test_unreadable_json_refuses_rather_than_reporting_zero(monkeypatch):
    class Bad:
        body, ok, error, status = "<html>not json", True, "", 200
    monkeypatch.setattr(A, "fetch", lambda *a, **k: Bad())
    assert advisories("pkg", "pypi", "1.0.0")["supported"] is False


# --------------------------------------------------------------- the counting

def test_one_cve_published_by_two_databases_is_one_issue(monkeypatch):
    """Measured on the live API: 44 rows for 26 issues. Counting rows would have
    overstated every one of these findings by roughly 40%."""
    got = _run(monkeypatch, [
        _vuln("GHSA-aaa", cve="CVE-2026-1", band="HIGH", fixed="2.0.0"),
        _vuln("PYSEC-1",  cve="CVE-2026-1", fixed="2.0.0"),
        _vuln("GHSA-bbb", cve="CVE-2026-2", band="LOW", fixed="1.5.0"),
    ])
    assert got["count"] == 2 and got["rows"] == 3


def test_the_rated_row_wins_within_a_group(monkeypatch):
    """GHSA carries the band; PYSEC usually does not. Preferring GHSA cuts the unrated
    count as a side effect - measured 26 unrated before dedup, 8 after."""
    got = _run(monkeypatch, [
        _vuln("PYSEC-1", cve="CVE-2026-1"),
        _vuln("GHSA-aaa", cve="CVE-2026-1", band="CRITICAL", fixed="2.0.0"),
    ])
    assert got["by_severity"] == {"CRITICAL": 1} and got["unrated"] == 0


def test_a_withdrawn_advisory_is_not_a_vulnerability(monkeypatch):
    got = _run(monkeypatch, [
        _vuln("GHSA-aaa", cve="CVE-1", band="HIGH", fixed="2.0.0"),
        _vuln("GHSA-bbb", cve="CVE-2", band="HIGH", withdrawn="2026-01-01T00:00:00Z"),
    ])
    assert got["count"] == 1


def test_an_unrated_advisory_is_counted_as_unrated_not_guessed_at(monkeypatch):
    """A CVSS vector is not a rating until somebody scores it, and scoring it here
    would present our arithmetic as the database's judgement."""
    got = _run(monkeypatch, [_vuln("PYSEC-9", cve="CVE-9")])
    assert got["unrated"] == 1 and got["by_severity"] == {} and got["worst"] == ""


def test_the_worst_band_is_the_worst_not_the_commonest(monkeypatch):
    got = _run(monkeypatch, [
        _vuln("GHSA-a", cve="C1", band="LOW"), _vuln("GHSA-b", cve="C2", band="LOW"),
        _vuln("GHSA-c", cve="C3", band="HIGH"),
    ])
    assert got["worst"] == "HIGH"


# --------------------------------------------------------------- the upgrade

def test_the_version_offered_is_the_one_that_clears_every_issue(monkeypatch):
    """Each advisory names the release that fixed IT; the package is only clean at the
    highest of those."""
    got = _run(monkeypatch, [
        _vuln("GHSA-a", cve="C1", band="HIGH", fixed="1.5.0"),
        _vuln("GHSA-b", cve="C2", band="LOW", fixed="2.3.1"),
    ])
    assert got["clears_all"] == "2.3.1"


def test_a_release_candidate_is_never_offered_as_the_fix():
    """`5.0.0rc3` is in the live data. A wrong "upgrade to" is worse than none."""
    assert A._as_tuple("5.0.0rc3") is None
    assert A._as_tuple("1.2.post1") is None
    assert A._as_tuple("5.10.0") == (5, 10, 0)


def test_no_fixed_release_anywhere_offers_nothing(monkeypatch):
    got = _run(monkeypatch, [_vuln("GHSA-a", cve="C1", band="HIGH")])
    assert got["clears_all"] == "" and got["count"] == 1


# --------------------------------------------- what the course does with it

def _dep(evidence):
    from miw.schema import Dependency, Location
    return Dependency(kind="package", canonical_name="pkg", registry="pypi",
                      taught_version="1.0.0",
                      locations=[Location(course="C", topic_name="T", unit_id="u",
                                          unit_name="U", content_id="c", field_path="f",
                                          evidence_source=evidence,
                                          object_type="CODING_QUESTIONS")])


@pytest.mark.parametrize("band,expected", [("CRITICAL", "critical"), ("HIGH", "high"),
                                           ("MODERATE", "medium"), ("LOW", "low")])
def test_the_ceiling_is_the_worst_rated_band(band, expected):
    from miw.analyse.score import advisory_severity
    assert advisory_severity(_dep("solution_import"), {"worst": band}) == expected


def test_a_package_the_course_never_runs_is_capped():
    """A version number in a spreadsheet is a spreadsheet edit. The same argument caps
    an n8n node the curriculum only names in a reference table."""
    from miw.analyse.score import advisory_severity
    runs = advisory_severity(_dep("solution_import"), {"worst": "HIGH"})
    lists = advisory_severity(_dep("sheet_declared"), {"worst": "HIGH"})
    assert runs == "high" and lists == "medium"


def test_a_count_never_raises_the_severity():
    """26 moderate issues are not worse than one critical one."""
    from miw.analyse.score import advisory_severity
    many = advisory_severity(_dep("solution_import"),
                             {"worst": "MODERATE", "count": 26})
    one = advisory_severity(_dep("solution_import"), {"worst": "CRITICAL", "count": 1})
    assert many == "medium" and one == "critical"


def test_nothing_rated_is_worth_a_look_and_not_a_deadline():
    from miw.analyse.score import advisory_severity
    assert advisory_severity(_dep("solution_import"), {"worst": "", "unrated": 9}) == "low"
