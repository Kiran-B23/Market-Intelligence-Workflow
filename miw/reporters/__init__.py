"""Delivery surfaces.

The delivery decision is deliberately deferred (PRD D1: "will decide later"), so the
pipeline writes the canonical `findings_<date>.json` and hands rendering to a reporter
looked up by name. Adding a Sheets row-appender or a Slack digest means adding a
module here and registering it — no pipeline stage changes.

A reporter takes the findings plus run context and *emits*; it never re-derives
anything. Anything a surface needs must already be on the `Finding`.
"""
from __future__ import annotations

from typing import Callable, Iterable

from miw.schema import Finding

Reporter = Callable[..., str]

_REGISTRY: dict[str, Reporter] = {}


def register(name: str) -> Callable[[Reporter], Reporter]:
    def deco(fn: Reporter) -> Reporter:
        _REGISTRY[name] = fn
        return fn
    return deco


def available() -> list[str]:
    return sorted(_REGISTRY)


def get(name: str) -> Reporter:
    if name not in _REGISTRY:
        raise KeyError(f"unknown reporter '{name}'; available: {available()}")
    return _REGISTRY[name]


def _bootstrap() -> None:
    from miw.reporters import markdown          # noqa: F401  (registers itself)


_bootstrap()
