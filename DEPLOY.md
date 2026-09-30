# Deploying MIW

MIW is a long-running process with a filesystem, not a set of functions. Three things
in its design decide the shape of any deployment:

- **A run holds its process for minutes.** `main.py run` probes hundreds of URLs.
  Serverless platforms cap a request at 60–300s, so a run there is a run that dies
  halfway and leaves its job row marked interrupted.
- **Every decision is a write.** Triage verdicts, job records, the LLM cache and the
  upstream snapshots that S12 diffs against are all SQLite under `state/`. Lose the
  filesystem between requests and you lose the feedback loop the precision metric is
  computed from — silently, because nothing errors.
- **The data is not in the repo, on purpose.** `.gitignore` excludes `out/`, `state/`
  and `data/`, the last as *"large, proprietary"*. The image carries no course content;
  it arrives on the volume.

That rules out Vercel and every other function host. What works is any platform that
runs a container with a persistent volume attached.

## Access control

**There is none in the application itself, by design** — it was built to read and write
one operator's local state, and `serve` binds `127.0.0.1`. Two structural controls stop
that assumption travelling:

- `main.py serve` **refuses to start** on a non-loopback host unless `MIW_AUTH_TOKEN`
  is set. Checked before anything else in `cmd_serve`, so an unrelated failure cannot
  mask it.
- When `MIW_AUTH_TOKEN` is set, every request needs it — HTTP Basic (any username, the
  token as the password) or `Authorization: Bearer <token>`. `/healthz` is the sole
  exemption and returns the word `ok` and nothing else.

Basic auth rather than a login page, because the browser prompts natively and `fetch`
re-sends the credential by itself: the UI gains no login screen and still makes no
external request of any kind.

This is a shared secret, not a user model. It says *someone with the token*, never
*who* — so it is a gate on a private deployment, not an audit trail. If you need to
know which reviewer confirmed a finding, that is a different piece of work.

## Fly.io

```sh
fly launch --no-deploy                  # accept the existing fly.toml
fly volumes create miw_data --size 3    # out/ + state/ + data/ is ~80MB today
fly secrets set MIW_AUTH_TOKEN="$(openssl rand -hex 24)"
fly deploy
```

`fly launch` is interactive — run it yourself, and note the token: it is shown once.

### Getting the data there

The image ships no artifacts, so the first boot renders an empty page rather than
failing. Either copy a working set up:

```sh
fly ssh console -C "mkdir -p /data/out /data/state /data/data"
tar czf - out state data | fly ssh console -C "tar xzf - -C /data"
```

…or run the pipeline in place, which is the better habit:

```sh
fly ssh console -C "python3 main.py run --all"
```

One caveat on running it there: the configured LLM provider is the `claude` CLI in
print mode, which is not in the image. Without it the deterministic path still works in
full — probing, scoring, findings, digest — and only the note-refinement step is
skipped, which is the same posture as `requirements-optional.txt` not being installed.

## Vercel

Vercel runs this as a serverless function, which fixes what the deployment can be. It
is a **read-only view**: the findings list, the severity filter, the detail panel, the
digest and the inventory. Not triage, not adding a course, not runs.

That is not a configuration choice. The filesystem is read-only apart from an ephemeral
`/tmp`, and every write here is SQLite under `state/` — so a triage verdict would
return 200, print its precision figure, and be gone by the next request, with nothing
to tell the reviewer it never happened. `MIW_READ_ONLY=1` makes those endpoints refuse
with a 503 that says why, and the page stops drawing the Confirm button at all.

```sh
vercel login                                    # interactive — run it yourself
vercel env add MIW_AUTH_TOKEN production        # paste `openssl rand -hex 24`
vercel --prod
```

**Set the token before the first deploy, not after.** A hosted deployment with no token
serves nothing at all — 503 on every path but `/healthz` — because the bind check that
protects the container cannot help here: a serverless platform imports the ASGI app
directly, so there is no host argument to refuse.

### What is uploaded, and what is not

`.vercelignore` keeps out `data/` and `out/content_records.jsonl` — the session bodies
and quiz text, about 60MB of it. The upload is ~12MB of findings, gaps, probe results
and the inventory.

The cost is the detail panel's excerpts: without the exports it cannot quote the line a
term appears on, and it says so in place of the excerpt. Everything else about a
location survives — which session, what kind of item, how many, the change, the
evidence, the alternatives. That is the trade this deployment makes, and it is the
reason it can exist at all.

### Before you point anyone at it

The findings include, by design, published advisories against the exact package
versions these courses pin. That is a useful list to a reviewer and a useful list to
somebody else, which is why the token is mandatory rather than advisory, and why the
responses carry `X-Robots-Tag: noindex`.

## Railway, Render, or any other container host

The same `Dockerfile` runs unchanged. What they need instead of `fly.toml`:

- a volume mounted at `/data` (override with `MIW_DATA_DIR`)
- `MIW_AUTH_TOKEN` in the environment
- `PORT` if the platform assigns one; the entrypoint honours it
- **one instance.** Two would each mount their own volume and diverge into two triage
  histories and a precision figure computed from half the evidence. SQLite on shared
  network storage corrupts, so this is a constraint rather than a preference.

## What is checked, and what is not

`tests/test_deploy_access.py` covers the token guard on reads, on the triage write, on
malformed credentials, the open health probe, the untouched loopback workflow, the
startup refusal and its ordering, and that the image cannot sweep in `out/`, `state/`
or `data/`.

The image itself has **not been built** — there is no container runtime on the machine
this was written on. The Dockerfile's `COPY` sources are all verified to exist and the
entrypoint passes `sh -n`, but the first `fly deploy` is the first real build.
