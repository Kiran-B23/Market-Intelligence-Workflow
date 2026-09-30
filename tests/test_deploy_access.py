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


# ---------------------------------------------------------------------------------
# A host whose filesystem does not outlive the request.
#
# Every write in this app is SQLite under `state/`. On a serverless platform that
# write goes to an ephemeral `/tmp` and disappears, so a triage verdict would return
# 200, print its precision figure, and be gone by the next request — with nothing to
# tell the reviewer their decision never happened, and the holdout split quietly
# computed from nothing. Refusing is the honest failure.


@pytest.fixture
def ro_client(monkeypatch):
    def build(read_only: bool):
        monkeypatch.setenv("MIW_READ_ONLY", "1" if read_only else "0")
        monkeypatch.delenv("MIW_AUTH_TOKEN", raising=False)
        import miw.api.app as app_mod
        importlib.reload(app_mod)
        return TestClient(app_mod.app)
    return build


def test_every_write_is_refused_not_swallowed(ro_client):
    c = ro_client(True)
    for method, path, body in (
        ("post", "/api/triage", {"finding_id": "x", "verdict": "accepted"}),
        ("post", "/api/runs", {"stages": ["probe"]}),
        ("post", "/api/courses", {"slug": "x"}),
        ("delete", "/api/courses/x", None),
        ("post", "/api/agent-runs/t/review", {"verdict": "accepted"}),
    ):
        r = getattr(c, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 503, f"{method} {path} returned {r.status_code}"
        assert "read-only" in r.json()["detail"]


def test_reading_is_untouched(ro_client):
    c = ro_client(True)
    for path in ("/", "/api/summary", "/api/findings"):
        assert c.get(path).status_code == 200, path


def test_the_page_is_told_so_it_can_stop_offering_the_button(ro_client):
    """A Confirm button that 503s is honest. One that is never drawn is kinder: the
    reviewer would otherwise read the finding, decide, click, and only then learn the
    decision was never possible."""
    assert ro_client(True).get("/api/summary").json()["read_only"] is True
    assert ro_client(False).get("/api/summary").json()["read_only"] is False
    page = (ROOT / "miw" / "api" / "static" / "index.html").read_text()
    assert "SUMMARY.read_only" in page
    panel = page[page.index("$('#slide-foot').innerHTML"):]
    panel = panel[:panel.index("wireTriage(")]
    assert "Read-only view" in panel
    assert panel.index("SUMMARY.read_only") < panel.index('data-do="accept"')


def test_writes_are_allowed_when_the_filesystem_is_real(ro_client):
    """The local app is the case this was all built for; it must be unaffected."""
    c = ro_client(False)
    r = c.post("/api/triage", json={"finding_id": "nope", "verdict": "accepted"})
    assert r.status_code != 503


def test_the_deployment_carries_no_course_text():
    """`data/` is the session bodies and quiz text, and `content_records.jsonl` is
    34MB of the same thing. Neither is read by the API, and neither is uploaded — the
    detail panel loses its excerpts and says why, and keeps everything else."""
    ignore = (ROOT / ".vercelignore").read_text()
    active = {l.strip() for l in ignore.splitlines()
              if l.strip() and not l.strip().startswith("#")}
    assert "data/" in active
    assert "out/content_records.jsonl" in active
    assert "state/" in active
    # ...and the artifacts the API actually reads are NOT excluded.
    for needed in ("out/findings_", "out/inventory.json", "miw/"):
        assert not any(a.rstrip("/") == needed.rstrip("/") for a in active), needed


def test_the_serverless_entrypoint_defaults_to_read_only():
    """Set in two places on purpose: `vercel.json` can be edited in the dashboard, and
    the entrypoint cannot."""
    import json
    entry = (ROOT / "api" / "index.py").read_text()
    assert 'setdefault("MIW_READ_ONLY", "1")' in entry
    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert cfg["env"]["MIW_READ_ONLY"] == "1"
    # A findings page is not something to hand a crawler. Set in the platform config
    # AND in the app, because the first is one dashboard edit away and the second is not.
    assert "noindex" in cfg["routes"][0]["headers"]["X-Robots-Tag"]
    assert "X-Robots-Tag" in (ROOT / "miw" / "api" / "app.py").read_text()


def test_the_original_request_path_reaches_the_app():
    """`rewrites` REPLACES the path with its destination, so every request arrived as
    `/api/index.py`: FastAPI matched no route and `/healthz` was never recognised as
    the one open path. The first working deploy failed on exactly this, and the build
    output had said so. `routes` passes the path through."""
    import json
    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert "rewrites" not in cfg, "a rewrite destination becomes the request path"
    assert cfg["routes"][0]["src"] == "/(.*)"
    assert cfg["routes"][0]["dest"] == "/api/index.py"


def test_a_hosted_deployment_with_no_token_serves_nothing(monkeypatch):
    """The gap the bind check cannot cover.

    `main.py serve` refuses a non-loopback host without a token, but a serverless
    platform imports the ASGI app directly — there is no host argument to refuse. The
    token guard is opt-in by construction: it only fires once someone has set a token.
    So a deployment where nobody set one was open to the internet with every test
    passing, serving among other things the published advisories against the exact
    package versions these courses pin.
    """
    monkeypatch.setenv("MIW_HOSTED", "1")
    monkeypatch.delenv("MIW_AUTH_TOKEN", raising=False)
    import miw.api.app as app_mod
    importlib.reload(app_mod)
    c = TestClient(app_mod.app)
    for path in ("/", "/api/summary", "/api/findings"):
        r = c.get(path)
        assert r.status_code == 503, path
        assert "MIW_AUTH_TOKEN" in r.text
    # Empty would be the kinder-looking failure and the worse one: it reads as
    # "no findings" rather than "this is misconfigured".
    assert "[]" not in c.get("/api/findings").text
    assert c.get("/healthz").status_code == 200


def test_the_platforms_own_variable_also_counts(monkeypatch):
    """Removing MIW_HOSTED from the config must not reopen it."""
    monkeypatch.delenv("MIW_HOSTED", raising=False)
    monkeypatch.delenv("MIW_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    import miw.api.app as app_mod
    importlib.reload(app_mod)
    assert TestClient(app_mod.app).get("/api/findings").status_code == 503


def test_the_local_app_is_still_open_on_loopback(monkeypatch):
    for var in ("MIW_HOSTED", "MIW_AUTH_TOKEN", "VERCEL", "MIW_READ_ONLY"):
        monkeypatch.delenv(var, raising=False)
    import miw.api.app as app_mod
    importlib.reload(app_mod)
    assert TestClient(app_mod.app).get("/api/findings").status_code == 200


def test_no_read_path_opens_the_state_store(ro_client):
    """The first deploy died on this, and it died at STARTUP so nothing served at all.

    Opening the jobs or decision database creates `state/`, and on a read-only host
    `mkdir` raises. The store is not uploaded there either — it is a reviewer's
    decision history and belongs on the machine that took the decisions — so every
    read path has to answer without it rather than crash reaching for it.
    """
    import miw.api.app as app_mod
    c = ro_client(True)
    bad = []
    for route in app_mod.app.routes:
        methods = getattr(route, "methods", set()) or set()
        if "GET" not in methods or "{" in route.path:
            continue
        code = c.get(route.path).status_code
        if code >= 500:
            bad.append((route.path, code))
    assert not bad, bad


def test_startup_does_not_reach_for_a_writable_disk(ro_client):
    """`TestClient` as a context manager fires the startup event; without the guard
    this raises and the application never comes up."""
    import miw.api.app as app_mod
    with TestClient(app_mod.app) as c:  # noqa: F841 — the construction is the assertion
        pass
    body = (ROOT / "miw" / "api" / "app.py").read_text()
    startup = body[body.index("def _startup()"):]
    startup = startup[:startup.index("\ndef ")]
    assert "_read_only()" in startup
    assert startup.index("_read_only()") < startup.index("runner()")


def test_an_absent_decision_history_reads_as_absent_not_as_a_verdict(ro_client):
    """`_decision` must come back empty rather than defaulting to something. A finding
    shown as "Confirmed" because the store could not be opened would be a lie with a
    chip on it."""
    c = ro_client(True)
    rows = c.get("/api/findings").json()
    for f in rows["findings"] + rows["standing"]:
        assert f["_decision"] is None, f["finding_id"]
    assert c.get("/api/summary").json()["precision"]["triaged"] == 0
