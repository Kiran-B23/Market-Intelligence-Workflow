"""Groq: models it serves, and models it has retired.

Groq's deprecations page is the best-shaped vendor source encountered so far — a real
table of `Deprecated Model | Shutdown Date | Recommended Replacement Model ID`, which
gives the finding, its urgency and its fix in one row. The replacement column is why
this vendor needs no search key to answer "what should we use instead".

`api.groq.com/openai/v1/models` returns 401 without a key, so the HTML tables are the
keyless path and the only one used here.
"""
from __future__ import annotations

from miw.vendors.base import Catalogue, build_catalogue

MODELS_URL = "https://console.groq.com/docs/models"
DEPRECATIONS_URL = "https://console.groq.com/docs/deprecations"


class GroqAdapter:
    key = "groq"
    vendor = "Groq"
    official_domains = ("groq.com", "console.groq.com", "api.groq.com")
    kinds = ("model",)

    def catalogue(self, refresh: bool = False) -> Catalogue:
        return build_catalogue(self.vendor, self.key,
                               [DEPRECATIONS_URL, MODELS_URL], refresh=refresh)
