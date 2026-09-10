"""Google AI (Gemini): the model catalogue and its retirement markers.

Google states status inside the *name* cell of a row — "Gemini 2.0 Flash (Shut down)" —
while the exact id lives in a `<code>` span in that same row's Endpoint cell. Both
columns map to the `id` role, which is why `Table.identifier_cell` prefers the coded one
for matching and reads the status from either.
"""
from __future__ import annotations

from miw.vendors.base import Catalogue, build_catalogue

MODELS_URL = "https://ai.google.dev/gemini-api/docs/models"


class GoogleAIAdapter:
    key = "google_ai"
    vendor = "Google"
    official_domains = ("ai.google.dev", "google.dev", "cloud.google.com",
                        "deepmind.google")
    kinds = ("model",)

    def catalogue(self, refresh: bool = False) -> Catalogue:
        return build_catalogue(self.vendor, self.key, [MODELS_URL], refresh=refresh)
