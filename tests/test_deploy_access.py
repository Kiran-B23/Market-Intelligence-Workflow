"""Nothing reachable from outside this machine may be reachable without a token.

MIW has no user model and never needed one: it reads and writes the local state of
one operator's machine, and `main.py serve` binds 127.0.0.1. The moment it is
reachable from anywhere else that stops being a missing feature and becomes an open
door — every endpoint is unauthenticated, `POST /api/triage` writes reviewer verdicts
that feed the precision metric and the learning/holdout split, and the detail panel
returns verbatim session excerpts and quiz text from course exports that `.gitignore`
itself calls "large, proprietary".

The old control was a printed sentence — "No authentication ... Keep it on localhost"
— followed by binding whatever host it was handed. A warning is not a control.
"""
from __future__ import annotations

import base64
import importlib
import pathlib
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def client(monkeypatch):
    def build(token: str | None):
        if token is None:
            monkeypatch.delenv("MIW_AUTH_TOKEN", raising=False)
        else:
            monkeypatch.setenv("MIW_AUTH_TOKEN", token)
        import miw.api.app as app_mod
        importlib.reload(app_mod)
        return TestClient(app_mod.app)
    return build


def _basic(user: str, password: str) -> dict:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def test_a_token_is_demanded_on_every_path_once_one_is_set(client):
    c = client("s3cret")
    for path in ("/", "/api/summary", "/api/findings", "/api/docs"):
        r = c.get(path)
        assert r.status_code == 401, path
        # The browser has to be told how to ask, or the operator sees a bare 401.
        assert "Basic" in r.headers.get("www-authenticate", ""), path


def test_the_write_endpoint_is_not_an_exception(client):
    """A triage verdict is the one request here that changes what the next run learns."""
    c = client("s3cret")
    r = c.post("/api/triage", json={"finding_id": "x", "verdict": "accepted"})
    assert r.status_code == 401


def test_the_right_token_gets_through_by_basic_or_bearer(client):
    c = client("s3cret")
    assert c.get("/healthz").status_code == 200
    # Any username: there is no user model to check one against, so only the
    # password half is compared.
    assert c.get("/api/summary", headers=_basic("anyone", "s3cret")).status_code == 200
    assert c.get("/api/summary",
                 headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_a_wrong_or_malformed_credential_is_refused(client):
    c = client("s3cret")
    for header in ({"Authorization": "Basic not-base64!!"},
                   {"Authorization": "Basic " + base64.b64encode(b"a:wrong").decode()},
                   {"Authorization": "Bearer wrong"},
                   {"Authorization": "s3cret"},
                   {}):
        assert c.get("/api/summary", headers=header).status_code == 401, header


def test_the_health_probe_needs_no_credential_and_reveals_nothing(client):
    """A platform probes this before any secret is in place, and a leaked URL should
    disclose only that something is listening."""
    c = client("s3cret")
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.text.strip() == "ok"


def test_an_unset_token_leaves_the_local_workflow_untouched(client):
    """Loopback is the case this app was built for and it must stay frictionless."""
    c = client(None)
    assert c.get("/api/summary").status_code == 200


def test_serving_off_loopback_without_a_token_refuses_to_start(monkeypatch):
    """The second half, and the one that cannot be forgotten: the guard above only
    fires once someone has set the variable."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "MIW_AUTH_TOKEN"}
    out = subprocess.run(
        [sys.executable, "main.py", "serve", "--host", "0.0.0.0", "--port", "9099"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 2, out.stdout
    assert "Refusing to serve" in out.stderr
    assert "MIW_AUTH_TOKEN" in out.stderr


def test_the_image_carries_no_course_content():
    """`out/`, `state/` and `data/` are not in the repo and must not reach the image:
    a build from a working checkout would otherwise bake ~80MB of course exports,
    findings and SQLite state into something pushable to a registry."""
    # As an ACTIVE line, not merely somewhere in the file. A substring check passed
    # happily against `# out/`, which ignores nothing - the comment character is the
    # whole difference between a rule and a note about a rule.
    lines = [l.strip() for l in (ROOT / ".dockerignore").read_text().splitlines()]
    active = {l for l in lines if l and not l.startswith("#")}
    for d in ("out/", "state/", "data/"):
        assert d in active, f"{d} is not an active .dockerignore rule"
    docker = (ROOT / "Dockerfile").read_text()
    for bad in ("COPY . ", "COPY ./ ", "ADD . "):
        assert bad not in docker, f"{bad!r} would sweep the data in"
    for d in ("out", "state", "data"):
        assert f"COPY {d}" not in docker, d


def test_the_volume_holds_everything_the_app_writes():
    """A redeploy must not take the triage history with it."""
    entry = (ROOT / "docker-entrypoint.sh").read_text()
    for d in ("out", "state", "data"):
        assert d in entry, d
    assert "ln -s" in entry, "the code resolves these relative to the repo root"
    fly = (ROOT / "fly.toml").read_text()
    assert 'destination = "/data"' in fly
    # SQLite on a network filesystem corrupts, and two machines would each mount
    # their own volume: two triage histories and a precision figure from half of it.
    assert "min_machines_running = 1" in fly
    assert "auto_stop_machines = false" in fly, "a stop mid-probe kills the run"


def test_the_refusal_is_not_maskable_by_an_unrelated_failure():
    """It sat after `import uvicorn`, so a missing dependency produced exit 2 with a
    different message and the access check never ran. Both paths return 2, which is
    exactly why the order has to be fixed rather than reasoned about."""
    src = (ROOT / "main.py").read_text()
    body = src[src.index("def cmd_serve("):]
    body = body[:body.index("\ndef ")]
    assert body.index("Refusing to serve") < body.index("import uvicorn")
