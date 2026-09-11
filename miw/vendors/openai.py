"""OpenAI: the deprecations page, which is the only catalogue it publishes as a table.

The curriculum teaches eleven OpenAI model ids — more than any other vendor — and until
now none of them was covered by a catalogue, so an OpenAI retirement could only ever be
noticed by a human. That is the same shape as the founding failure.

Which page, and why it is the *deprecations* one: `platform.openai.com/docs/models` is
client-rendered and a plain fetch returns a 378KB shell with **zero** tables. The
deprecations page is server-rendered and role-types cleanly — 34 typed tables, 132
distinct ids, 105 of them retired and 27 still listed as available. It is a strange place
to read a live catalogue from, and it is the one OpenAI actually publishes in a form that
can be read without executing JavaScript.

What this unlocks, stated precisely rather than optimistically. Of the 260 rows, 142 sit
in the identifier column and **every one of them is retired** — it is a deprecations
page, so that is what it is for — while the other 118 sit in the *replacement* column and
`build_catalogue` drops them, because being named as the successor to something older is
not the same as being catalogued. So:

* **S7 gains OpenAI entirely.** A taught OpenAI model id that its provider retires is now
  detectable at all, where before it could only be noticed by a human. Zero of the eleven
  ids the curriculum teaches are on that list today, so this raises nothing right now —
  it is a watch being put in place, which is the whole premise of the system.
* **S12 gains nothing from OpenAI.** There is no live catalogue here to diff, so the
  "newer option" signal stays blind to OpenAI until OpenAI publishes a model list that can
  be read without a browser. Saying otherwise would be claiming coverage we do not have.

Tested and rejected, recorded here so the next person does not repeat it:

* `platform.openai.com/docs/models` — client-rendered, 0 tables.
* `docs.anthropic.com/.../models/overview` — one role-typed table, but it is
  **transposed**: models are columns and attributes are rows, so the column-role reader
  binds "Fastest" and "$1 / input MTok" as identifiers. Reading it needs a transposed-table
  mode in `probe/catalogue.py`, not an adapter, and forcing it would produce exactly the
  fabricated ids the role binding exists to prevent.
* `huggingface.co/models` — 0 tables; the model index is a search UI.
* `docs.cohere.com/docs/models` — did not respond to a plain fetch.
"""
from __future__ import annotations

from miw.vendors.base import Catalogue, build_catalogue

# Deliberately the deprecations page and not `/docs/models` — see the module docstring.
DEPRECATIONS_URL = "https://platform.openai.com/docs/deprecations"


class OpenAIAdapter:
    key = "openai"
    vendor = "OpenAI"
    official_domains = ("openai.com", "platform.openai.com")
    kinds = ("model",)

    def catalogue(self, refresh: bool = False) -> Catalogue:
        return build_catalogue(self.vendor, self.key, [DEPRECATIONS_URL],
                               refresh=refresh)
