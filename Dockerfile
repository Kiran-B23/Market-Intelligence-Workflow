# MIW as a long-running container.
#
# Not a serverless function: a run probes hundreds of URLs over minutes, and every
# triage decision, job record and upstream snapshot is a write to SQLite under
# `state/`. Both of those need a process that outlives a request and a filesystem
# that outlives the process.
FROM python:3.12-slim

# `curl` for the platform health probe; `git` because `main.py verify` reports the
# revision it checked. No build toolchain: every runtime dependency ships a wheel.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so editing the app does not reinstall them. Only the core
# file: `requirements-optional.txt` is the API-key path, and "works with no API
# key" holds here by construction rather than by a marker.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Code only. `out/`, `state/` and `data/` are NOT copied and are not in the repo -
# `.gitignore` calls the last one "large, proprietary" - so the image carries no
# course content and can be pushed to a registry without publishing anything. The
# data arrives on the mounted volume.
COPY main.py ./
COPY config ./config
COPY miw ./miw
COPY registry ./registry
COPY eval ./eval
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Not root. The app writes only to the volume, so it needs no privilege beyond it.
RUN useradd --create-home --uid 10001 miw && chown -R miw:miw /app
USER miw

ENV PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=4s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
