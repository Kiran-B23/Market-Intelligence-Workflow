"""Invariants on the file that decides who may speak for what.

`registry/tools.yaml` is the only thing `miw.trust` reads. An entry with no
`official_domains` is MUTE: no deprecation, pricing or version claim about it can ever
be authoritative, so the dependency is silently unmonitored however heavily it is
taught. That failure is invisible - the tool simply never appears in a finding.

Measured before this was addressed: **202 of 440 dependencies** were mute, all of them
`from_sheet` - a name lifted from the workbook with no vendor ever attached - and they
included Telegram (229 locations), Claude Code (150), Google Docs (119), Whisper (94)
and Google Colab (67). The system was reporting on Composio's dashboard while saying
nothing whatsoever about Whisper.
"""
import collections
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRIES = yaml.safe_load((ROOT / "registry" / "tools.yaml").read_text())["tools"]
BY = {e["canonical_name"]: e for e in ENTRIES}

STATUSES = {"derived", "from_sheet", "verified", "approved", "not-a-tool"}


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1) if m else "").lower()


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def test_every_review_status_is_one_we_document():
    seen = {e.get("review_status", "") for e in ENTRIES}
    assert seen <= STATUSES, seen - STATUSES


def test_a_verified_entry_carries_the_evidence_it_claims():
    """`verified` means a page was fetched and it named the tool. The minimum that
    leaves behind is a homepage and an authority set that contains it."""
    bad = []
    for e in ENTRIES:
        if e.get("review_status") != "verified":
            continue
        home, doms = e.get("homepage", ""), e.get("official_domains") or []
        if not home or not doms:
            bad.append((e["canonical_name"], "missing homepage or domains"))
            continue
        if not any(_registrable(_host(home)).endswith(_registrable(d)) or
                   _registrable(d).endswith(_registrable(_host(home))) for d in doms):
            bad.append((e["canonical_name"], f"{_host(home)} not in {doms}"))
    assert not bad, bad


def test_a_not_a_tool_entry_says_why_and_claims_no_vendor():
    """A technique, a dataset or a file format has no vendor to be wrong about.
    Inventing one for it is the same error as guessing a domain."""
    for e in ENTRIES:
        if e.get("review_status") != "not-a-tool":
            continue
        assert e.get("notes"), f"{e['canonical_name']} is unmonitored with no reason given"
        assert "deliberately unmonitored" in e["notes"]
        assert not e.get("official_domains"), e["canonical_name"]
        assert e.get("watch_tier") == "mention-only", e["canonical_name"]


def test_no_two_entries_claim_the_same_alias():
    """`alias_index()` keeps the FIRST entry per alias and silently drops the rest, so a
    duplicate does not error - it quietly routes a tool's prose mentions to the wrong
    entry. Murf.AI had three entries; the one with the domains was not the one that won.
    """
    owners = collections.defaultdict(list)
    for e in ENTRIES:
        for a in e.get("aliases") or []:
            owners[re.sub(r"\s+", " ", re.sub(r"[^\w+#.]+", " ", a.lower())).strip()] \
                .append(e["canonical_name"])
    clashes = {a: names for a, names in owners.items() if len(set(names)) > 1}
    assert not clashes, clashes


def test_the_mute_set_is_shrinking_and_is_all_from_sheet_or_declared_not_a_tool():
    """A mute entry is only acceptable when nobody has looked at it yet, or when
    somebody looked and recorded that there is nothing to look at."""
    mute = [e for e in ENTRIES
            if not (e.get("official_domains") or e.get("registry"))]
    for e in mute:
        assert e.get("review_status") in ("from_sheet", "not-a-tool"), \
            f"{e['canonical_name']} is mute but claims status {e.get('review_status')!r}"
    # A ratchet, not a target: this must go down, never up. 202 registry entries were
    # mute when this was first measured; 174 now. The number here is the registry's,
    # which is larger than the inventory's 161 because the registry also holds entries
    # no course currently references.
    assert len(mute) <= 174, f"{len(mute)} mute entries; it was 202 and must not regress"


def test_the_heaviest_taught_tools_are_spoken_for():
    """The specific ones that prompted this. Each is named in 60+ places."""
    for name in ("Telegram", "Claude Code", "Google Docs", "Google Sheets",
                 "Google Colab", "Whisper", "ChromaDB", "Google Calendar"):
        e = BY.get(name)
        assert e, f"{name} left the registry"
        assert e.get("official_domains"), f"{name} is still mute"
