#!/bin/sh
# The three directories the app writes to live on the volume, not in the image, so
# that a redeploy cannot take the triage history with it. The code resolves them
# relative to the repo root (`ROOT / "out"` and friends), so they are symlinked
# rather than reconfigured - no path logic changes, nothing to keep in sync.
set -eu

VOL="${MIW_DATA_DIR:-/data}"
for d in out state data; do
  mkdir -p "$VOL/$d"
  if [ ! -L "/app/$d" ]; then
    rm -rf "/app/$d"
    ln -s "$VOL/$d" "/app/$d"
  fi
done

# Belt and brace: `cmd_serve` refuses a non-loopback bind without a token, and this
# says so in the platform's own logs where an operator will actually read it.
if [ -z "${MIW_AUTH_TOKEN:-}" ]; then
  echo "MIW_AUTH_TOKEN is not set. Every endpoint is unauthenticated and the" >&2
  echo "detail panel returns course content verbatim, so this will not start." >&2
  echo "Set one:  fly secrets set MIW_AUTH_TOKEN=\$(openssl rand -hex 24)" >&2
fi

exec python3 main.py serve --host 0.0.0.0 --port "${PORT:-8080}"
