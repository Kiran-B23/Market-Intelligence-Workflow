"""Reviewing an agent run from the UI: resume the graph, record the verdict.

The endpoint is a real `Command(resume=...)` against a durable checkpoint, so these
tests build a suspended run, then drive it through the API exactly as a click does.

Two rules carry most of the weight:

* a rejection needs a reason, matching `/api/triage` — a decision with no reason
  teaches a later reader nothing;
* an already-reviewed run must NOT be resumable again, or a second click overwrites a
  decision someone already made.

And one boundary asserted on purpose: this must not feed `miw.triage`. Triage drives the
precision metric and reviewer suppression, and both are claims about the *pipeline*,
whose findings carry two-run confirmation. An agent run carries none.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("langgraph")

from fastapi.testclient import TestClient

from miw.api import app as api
from miw.schema import Dependency, Location

INTRO = "Intro to Gen AI"
CLAIM = {"source_url": "https://gizmo.example/deprecations", "tier": "AUTHORITATIVE",
         "kind": "deprecation", "quote": "gizmo-1 is deprecated and will be shut down",
         "statement": "gizmo-1 is listed as deprecated"}


def _dep():
    return Dependency(kind="model", canonical_name="gizmo-1",
                      official_domains=["gizmo.example"],
                      locations=[Location(course=INTRO, topic_name="T", unit_id="u1",
                                          unit_name="Coding Practice", content_id="q1",
                                          field_path="[0]", evidence_source="model_id",
                                          object_type="OBJECTIVE_QUESTIONS",
                                          session_no=10)])


@pytest.fixture()
def suspended(tmp_path, monkeypatch):
    """A real suspended run on a temporary checkpoint DB and artifact dir."""
    from miw.agents import impact_agent
    from miw.agents.nodes import review

    db = tmp_path / "cp.db"
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(review, "OUT", out)
    monkeypatch.setattr(impact_agent, "CHECKPOINT_DB", db)
    monkeypatch.setattr(api, "OUT", out)

    app = impact_agent.compile_impact_graph(db_path=db)
    st, _ = impact_agent.run_impact_agent(_dep(), ["model_shutdown_passed"], [CLAIM],
                                          graph=app, thread_id="t-api")
    assert st["findings"], "the run reached its gate"
    return {"client": TestClient(api.app), "thread": "t-api", "out": out, "db": db}


def _artifact(out: Path) -> dict:
    f = sorted(out.glob("agent_findings_*.json"))[-1]
    return json.loads(f.read_text())


# ------------------------------------------------------------------ accepting

def test_accepting_resumes_the_run_and_stamps_the_artifact(suspended):
    c, thread, out = suspended["client"], suspended["thread"], suspended["out"]
    assert _artifact(out)["runs"][0]["awaiting_review"] is True

    r = c.post(f"/api/agent-runs/{thread}/review", json={"verdict": "accepted"})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "accepted"

    row = _artifact(out)["runs"][0]
    assert row["review"] == "accepted"
    assert row["awaiting_review"] is False, "the UI must stop showing it as pending"


def test_the_endpoint_says_plainly_that_it_does_not_feed_triage(suspended):
    c, thread = suspended["client"], suspended["thread"]
    body = c.post(f"/api/agent-runs/{thread}/review",
                  json={"verdict": "accepted"}).json()
    assert body["feeds_triage"] is False


def test_an_agent_verdict_leaves_the_pipeline_precision_metric_untouched(suspended):
    """The boundary that matters: precision is a claim about the pipeline."""
    from miw.state import State
    st = State()
    before = st.precision_stats()
    st.close()

    c, thread = suspended["client"], suspended["thread"]
    assert c.post(f"/api/agent-runs/{thread}/review",
                  json={"verdict": "accepted"}).status_code == 200

    st = State()
    after = st.precision_stats()
    st.close()
    assert after == before


# ------------------------------------------------------------------ rejecting

def test_a_rejection_without_a_reason_is_refused(suspended):
    c, thread, out = suspended["client"], suspended["thread"], suspended["out"]
    r = c.post(f"/api/agent-runs/{thread}/review",
               json={"verdict": "rejected", "reason": "   "})
    assert r.status_code == 400
    assert "reason" in r.json()["detail"]
    assert _artifact(out)["runs"][0]["awaiting_review"] is True, "still pending"


def test_a_rejection_with_a_reason_is_recorded(suspended):
    c, thread, out = suspended["client"], suspended["thread"], suspended["out"]
    r = c.post(f"/api/agent-runs/{thread}/review",
               json={"verdict": "rejected", "reason": "vendor still lists it for us"})
    assert r.status_code == 200
    assert _artifact(out)["runs"][0]["review"] == "rejected"


# ------------------------------------------------------------------ guards

def test_a_reviewed_run_cannot_be_reviewed_twice(suspended):
    """A second click must not overwrite a decision already made."""
    c, thread = suspended["client"], suspended["thread"]
    assert c.post(f"/api/agent-runs/{thread}/review",
                  json={"verdict": "accepted"}).status_code == 200
    again = c.post(f"/api/agent-runs/{thread}/review",
                   json={"verdict": "rejected", "reason": "changed my mind"})
    assert again.status_code == 409
    assert "already been reviewed" in again.json()["detail"]


def test_an_unknown_thread_is_a_404(suspended):
    c = suspended["client"]
    r = c.post("/api/agent-runs/no-such-thread/review", json={"verdict": "accepted"})
    assert r.status_code == 404


def test_an_invalid_verdict_is_refused_by_the_schema(suspended):
    c, thread = suspended["client"], suspended["thread"]
    r = c.post(f"/api/agent-runs/{thread}/review", json={"verdict": "maybe"})
    assert r.status_code == 422


def test_no_checkpoint_database_is_a_404_not_a_crash(tmp_path, monkeypatch):
    from miw.agents import impact_agent
    monkeypatch.setattr(impact_agent, "CHECKPOINT_DB", tmp_path / "absent.db")
    c = TestClient(api.app)
    r = c.post("/api/agent-runs/t/review", json={"verdict": "accepted"})
    assert r.status_code == 404
