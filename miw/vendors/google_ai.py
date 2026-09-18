"""Google AI (Gemini): the model catalogue and its retirement markers.

Google states status inside the *name* cell of a row — "Gemini 2.0 Flash (Shut down)" —
while the exact id lives in a `<code>` span in that same row's Endpoint cell. Both
columns map to the `id` role, which is why `Table.identifier_cell` prefers the coded one
for matching and reads the status from either.
"""
from __future__ import annotations

from miw.vendors.base import Catalogue, build_catalogue

MODELS_URL = "https://ai.google.dev/gemini-api/docs/models"
# The models page carries a status parenthetical - "Gemini 2.0 Flash (Shut down)" - and
# no dates at all. Every shutdown DATE Google announces is on the deprecations page, in
# a `Model | Release date | Shutdown date | Recommended replacement` table. Reading only
# the first page meant 0 of 44 entries carried a date, so `model_deprecation_declared`
# could never fire for Google and a retirement was only ever noticed once it had already
# happened. Both pages are read; `build_catalogue` reconciles an id listed on both.
DEPRECATIONS_URL = "https://ai.google.dev/gemini-api/docs/deprecations"


class GoogleAIAdapter:
    key = "google_ai"
    vendor = "Google"
    official_domains = ("ai.google.dev", "google.dev", "cloud.google.com",
                        "deepmind.google")
    kinds = ("model",)

    def catalogue(self, refresh: bool = False) -> Catalogue:
        return build_catalogue(self.vendor, self.key, [MODELS_URL, DEPRECATIONS_URL],
                               refresh=refresh)
