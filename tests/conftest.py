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
import pathlib

import pytest

from tests._guard import NetworkReached


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


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """**No test may reach the network**, for the same reasons as the model rule above.

    The workflow gates the weekly run on this suite with the comment "they need no keys
    and no network", and that was true only by luck: `state/n8n_upstream.json` carries a
    seven-day TTL, so the moment it expired, `test_the_probe_is_not_rationed_by_a
    _research_budget` started fetching n8n's repository tree for real — about 40
    requests at a 20-second timeout with two retries each. The suite did not fail. It
    hung, which in CI reads as an infrastructure problem rather than as the test doing
    something it was never meant to do.

    The code under it is correct either way: an unreachable GitHub is recorded as
    `inconclusive` with `n8n_upstream_unreachable`, never as "the node was removed". The
    defect is in the test reaching out at all, and this turns that into a named failure
    in milliseconds.

    Every egress path is covered, not only HTTP. Nothing in `miw/` issues a request
    except through `miw.net.fetch`, but DNS is its own reach: `url_safety` resolves a
    hostname before any request is made, and on a fake domain that is a real lookup with
    a real timeout.

    A test that genuinely needs the network marks itself `@pytest.mark.network`, and
    then has to justify being in a suite that gates every change.
    """
    if request.node.get_closest_marker("network"):
        return

    import socket
    import subprocess

    import requests

    def _refuse(what: str):
        raise NetworkReached(
            f"this test reached the network ({what}). The suite gates every change and "
            f"must stay offline: inject a fixture through the seam the call site "
            f"provides (`adapters=`, `upstream=`, `fetcher=`, `observer=`), or mark the "
            f"test `@pytest.mark.network` and justify it.")

    monkeypatch.setattr(requests, "request",
                        lambda method, url, *a, **k: _refuse(f"{method} {url}"))
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, method, url, *a, **k: _refuse(f"{method} {url}"))
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: _refuse(f"DNS {host}"))
    monkeypatch.setattr(socket, "create_connection",
                        lambda addr, *a, **k: _refuse(f"connect {addr}"))

    # The model rule above patches `miw.llm.complete`, which is where every caller goes
    # in. This catches the layer under it: a test exercising a provider backend directly
    # would otherwise shell out to the operator's `claude` CLI and spend real money.
    real_run, real_popen = subprocess.run, subprocess.Popen

    def _guard(fn):
        def inner(cmd, *a, **k):
            argv0 = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd)
            if pathlib.Path(str(argv0)).name in ("claude", "claude.exe"):
                _refuse(f"spawned the {argv0!r} CLI")
            return fn(cmd, *a, **k)
        return inner

    monkeypatch.setattr(subprocess, "run", _guard(real_run))
    monkeypatch.setattr(subprocess, "Popen", _guard(real_popen))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: this test reaches the network and is exempt from the "
                   "offline rule in `_no_network`")


@pytest.fixture(autouse=True)
def _no_shared_site_index():
    """`score.PARAM_SITES` and `score.API_SITES` are module-level dicts that `main.py`
    fills before `analyse` runs, because the same content record is the same record for
    every dependency and indexing it per dependency would be quadratic.

    That makes them shared mutable state in the module every finding passes through. A
    test that sets one and does not reset it changes what a later test measures, and the
    later test is the one that looks broken. Demonstrated: setting `PARAM_SITES` in one
    test and reading it in the next returns the first test's value.

    Cleared around every test, so isolation stops depending on each author remembering
    a `finally`.
    """
    from miw.analyse import score
    before = dict(score.PARAM_SITES), dict(score.API_SITES)
    score.PARAM_SITES.clear()
    score.API_SITES.clear()
    yield
    score.PARAM_SITES.clear()
    score.PARAM_SITES.update(before[0])
    score.API_SITES.clear()
    score.API_SITES.update(before[1])
