"""Triage and feedback: the loop that makes precision measurable and improvable."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miw import triage
from miw.state import State


def _state(tmp):
    return State(Path(tmp) / "t.db")


def test_rejection_suppresses_only_while_the_evidence_is_unchanged():
    """"Stop showing me this" must never hide a tool going from redirected to dead."""
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        st.record_decision(finding_id="f1", dep_id="d1", signal="S5",
                           verdict="rejected", reason="we only cite that blog",
                           fingerprint="fp-old", now="now")
        assert triage.suppressed(st, "f1", "fp-old"), "same situation stays suppressed"
        assert triage.suppressed(st, "f1", "fp-new") is None, \
            "new evidence must resurface the finding"
        st.close()


def test_acceptance_does_not_suppress():
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        st.record_decision(finding_id="f2", dep_id="d", signal="S1", verdict="accepted",
                           fingerprint="fp", now="now")
        assert triage.suppressed(st, "f2", "fp") is None
        st.close()


def test_precision_counts_only_the_latest_decision_per_finding():
    """A finding rejected then accepted must not count against the system twice."""
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        st.record_decision(finding_id="f3", dep_id="d", signal="S1", verdict="rejected",
                           reason="wrong", fingerprint="a", now="t1")
        st.record_decision(finding_id="f3", dep_id="d", signal="S1", verdict="accepted",
                           fingerprint="a", now="t2")
        assert st.precision_stats() == {"triaged": 1, "accepted": 1, "rejected": 0,
                                        "precision": 1.0}
        st.close()


def test_held_out_split_is_deterministic_and_roughly_even():
    ids = [f"finding{i:04d}" for i in range(400)]
    a = [triage.split_of(i) for i in ids]
    assert a == [triage.split_of(i) for i in ids], "split must be stable across calls"
    learn = a.count("learn")
    assert 140 < learn < 260, f"split is lopsided: {learn}/400 in learn"


def test_feedback_corpus_prefers_corrections_on_the_same_drift_class():
    original = triage.FEEDBACK_FILE
    with tempfile.TemporaryDirectory() as tmp:
        triage.FEEDBACK_FILE = Path(tmp) / "learned.md"
        triage.FEEDBACK_FILE.write_text(
            triage.HEADER
            + '- [S1] reviewer corrected "when" to: immediately\n'
            + '- [S6] reviewer corrected "when" to: next cycle\n')
        corpus = triage.feedback_corpus("S6")
        assert corpus.splitlines()[0].startswith("- [S6]"), \
            "same-class corrections must come first"
        assert "[S1]" in corpus, "other classes are still available as context"
        triage.FEEDBACK_FILE = original
