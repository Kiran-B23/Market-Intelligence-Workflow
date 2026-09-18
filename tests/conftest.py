"""Test-wide invariants that no individual test should have to remember.

**No test may reach a model.** This suite is the thing that runs on every change, and
it has to be free, offline and fast for the same reason the probe stage is: a check you
hesitate to run is a check that stops being run. The constraint is also literal here -
there is no API key, the provider is the local `claude` CLI, and a suite that quietly
started shelling out to it would cost real money per run and take minutes.

It is not hypothetical. Giving `research_dependency` a fit judgement that no longer sat
behind `--nominate` made exactly one previously-offline test start calling the model,
and the only symptom was the suite getting slower. This turns that into a failure with
the test's name on it.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_model_calls(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError(
            "this test reached the LLM. Pass the offline switch the call site provides "
            "(`judge_fit=False`, `use_model=False`, `refine=False`), or stub "
            "`miw.llm.complete` explicitly if the model is what you are testing.")

    import miw.llm
    monkeypatch.setattr(miw.llm, "complete", _refuse, raising=False)
    # Two modules bind the name at import time, so patching `miw.llm` alone leaves
    # their copies live. Every other call site imports inside the function, which is
    # why the module patch works there.
    for mod in ("miw.analyse.notes", "miw.agents.nodes.plan"):
        try:
            monkeypatch.setattr(__import__(mod, fromlist=["complete"]),
                                "complete", _refuse, raising=False)
        except ImportError:
            pass
