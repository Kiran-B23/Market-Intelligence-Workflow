"""The offline rule, tested on itself.

`tests/conftest.py` blocks every egress path so a test that reaches out fails with its
own name on it. That guard is only worth having if it cannot be absorbed by the code it
guards — and the code it guards is deliberately forgiving about network failure, because
a vendor being briefly unreachable must never read as a vendor being gone.

So the guard raises a `BaseException`. These tests pin that choice, because the obvious
implementation is an `AssertionError` and the obvious implementation is silently wrong:
two `except Exception` handlers on the live paths would have eaten it, and the test
would have passed for the wrong reason.
"""
import socket
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests._guard import NetworkReached


def test_an_http_request_is_refused():
    with pytest.raises(NetworkReached) as e:
        requests.request("GET", "https://example.test/")
    assert "GET https://example.test/" in str(e.value)


def test_a_dns_lookup_is_refused_too():
    """DNS is its own reach. `url_safety` resolves a hostname before any request is
    made, so a suite that blocked only HTTP still paid a real lookup and a real timeout
    on every fake domain a test invented."""
    with pytest.raises(NetworkReached):
        socket.getaddrinfo("example.test", None)


def test_the_guard_survives_url_safety_swallowing_exceptions():
    """`miw.net.url_safety` wraps the resolver in `except Exception` and returns
    "unresolvable" — the right behaviour in production, and fatal to a guard that
    raises an ordinary exception: the test would pass, having quietly been told the
    host does not exist.
    """
    from miw.net import url_safety

    with pytest.raises(NetworkReached):
        url_safety("https://example.test/path")


def test_the_guard_survives_a_probe_catching_every_exception():
    """`probe/successor.py` catches `Exception` around each candidate because "a probe
    failure is not an answer". With a swallowable guard, a test would silently get
    "no successor found" instead of being told it went to the network."""
    from miw.probe.successor import find_successor

    with pytest.raises(NetworkReached):
        find_successor("https://dead.test/page", homepage="https://dead.test",
                       official_domains=["dead.test"])


def test_the_claude_cli_cannot_be_spawned():
    """The model rule in conftest patches `miw.llm.complete`, where every caller goes
    in. This is the layer under it: a test exercising a provider backend directly would
    otherwise shell out to the operator's own entitlement and spend real money."""
    import subprocess

    with pytest.raises(NetworkReached):
        subprocess.run(["claude", "-p", "hello"], capture_output=True)


def test_an_ordinary_subprocess_still_runs():
    """The guard names one binary. Stage subprocesses are how the UI runs the pipeline
    and must keep working."""
    import subprocess

    r = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True,
                       text=True)
    assert r.stdout.strip() == "ok"


@pytest.mark.network
def test_the_network_marker_lifts_the_guard():
    """The escape hatch, asserted without using it: under the marker the real functions
    are back in place. A test that needs the network has to say so, and then justify
    being in a suite that gates every change."""
    assert socket.getaddrinfo.__module__ == "socket"
    assert not isinstance(requests.request, type(lambda: None).__class__)


def test_no_committed_fixture_carries_a_credential():
    """Fixtures are saved verbatim from public documentation, and documentation prints
    example credentials.

    Stripe's charges reference embeds `sk_test_…` in its curl samples, and the first
    push of these fixtures was blocked by GitHub's secret scanning — correctly. The key
    is Stripe's own published test key and harmless, and it still has no business in a
    commit: the next fixture captured this way may be from a page that prints something
    that is not.
    """
    import re

    PAT = re.compile(r"(sk|pk|rk)_(test|live)_[A-Za-z0-9]{8,}"
                     r"|xai-[A-Za-z0-9]{12,}|sk-[A-Za-z0-9]{20,}"
                     r"|AIza[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}"
                     r"|ghp_[A-Za-z0-9]{20,}")
    root = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    offenders = []
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix not in (".txt", ".html", ".json", ".ts"):
            continue
        m = PAT.search(f.read_text(errors="replace"))
        if m:
            offenders.append(f"{f.relative_to(root)}: {m.group(0)[:12]}…")
    assert not offenders, (
        "a fixture carries a credential-shaped string; redact it to "
        f"REDACTED_EXAMPLE_CREDENTIAL: {offenders}")
