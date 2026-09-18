"""The exception the offline guard raises, in a module of its own.

Not in `conftest.py`, and the reason is a trap worth recording: pytest loads a conftest
under its own internal module name, so a test doing `from tests.conftest import
NetworkReached` imports a SECOND copy of the file and gets a different class object.
`pytest.raises` then never matches, and the guard's own tests fail while the guard
works perfectly. One module, imported the same way by both, removes the ambiguity.
"""


class NetworkReached(BaseException):
    """Raised when a test reaches the network. Deliberately a `BaseException`.

    An `AssertionError` here does not work, and the reason is the whole point of the
    guard: the code under test is *supposed* to be forgiving about network failure.
    `miw.net.url_safety` wraps `socket.getaddrinfo` in `except Exception` and returns
    "unresolvable"; `probe/successor.py` wraps a probe in `except Exception` because "a
    probe failure is not an answer". Both would swallow an ordinary exception and let
    the test pass for the wrong reason — quietly reclassifying a live vendor as absent,
    which is the exact failure the audit found in production.

    Deriving from `BaseException` means no `except Exception:` in the package can
    absorb it, so the test fails with its own name on it, every time.
    """
