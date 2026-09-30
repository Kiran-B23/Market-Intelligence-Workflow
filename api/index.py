"""Vercel's entry point.

Vercel runs this as a serverless function, which decides what the deployment can be:
the filesystem is read-only apart from an ephemeral `/tmp`, so nothing written here
survives the request. `MIW_READ_ONLY` is set in `vercel.json` for exactly that reason -
every write endpoint refuses with a 503 that says why, rather than returning 200 for a
triage verdict that is gone a second later.

What works: the findings list, the severity filter, the detail panel, the digest, the
inventory - everything that reads the artifacts bundled at deploy time.

What does not, and cannot here: recording a triage decision, adding a course, and
starting a run (which holds its process for minutes against a 60-300s function cap).
Those need the local app or a container host with a volume; see DEPLOY.md.
"""
import os
import sys
from pathlib import Path

# The function's working directory is not the repo root, and every path in the app is
# resolved from it (`ROOT / "out"` and friends).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MIW_READ_ONLY", "1")
# Reachable from off this machine, so MIW_AUTH_TOKEN stops being optional. Vercel sets
# `VERCEL` itself and the app checks that too, so removing this line does not reopen it.
os.environ.setdefault("MIW_HOSTED", "1")

from miw.api.app import app  # noqa: E402

__all__ = ["app"]
