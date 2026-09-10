"""The course roster, with a writable overlay so a course can be added at runtime.

`config/constants.py` holds a hand-aligned literal under an eleven-line comment that is
the *reviewed justification* for each `expect_sessions` value — those numbers were
mis-calibrated once already (see `PRD.md` §31), so that file stays hand-owned. This
module adds a second, writable source and merges the two.

Three rules, each with a reason:

* **The declared literal wins on conflict.** The overlay is untracked runtime data; it
  must never be able to silently override a tracked number whose reason is documented
  three lines above it. That also bounds the damage a corrupt overlay can do: it can
  only ever add a course, never lose or alter a shipped one.
* **Reads refresh on mtime.** `COURSES` is read in hot paths — `scope._course_tables()`
  rebuilds on every `slug_of()` call — so re-reading the file per access would add
  thousands of syscalls to one `/api/inventory`. Stat-gated caching is the pattern
  `extract/locate.py` already uses for course exports. The subprocess stages are
  untouched by construction: a fresh interpreter stats once and loads.
* **`keys`/`values`/`items` return snapshots.** `cmd_ingest` holds one `COURSES.items()`
  iteration open for minutes while it parses 9 MB exports. A refresh on another thread
  mutating the dict mid-iteration would raise `RuntimeError: dictionary changed size
  during iteration` and kill the whole ingest.

It never raises. `config.constants` is imported at module scope elsewhere, so it has to
stay importable whatever is in the overlay file.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "course_registry.json"

# One stat per second at most, so a 3,000-location loop does not stat 3,000 times.
_MIN_RESTAT = 1.0


def _slugify(title: str) -> str:
    """Kept in step with `miw.scope.slugify`, imported lazily to avoid a cycle."""
    from miw.scope import slugify
    return slugify(title)


def load_overlay(path: Path = REGISTRY_PATH) -> tuple[list[dict], list[str]]:
    """(valid entries, rejection reasons). Never raises.

    A malformed entry is dropped with a reason rather than admitted: a roster entry
    missing `expect_sessions` would make `cmd_ingest` raise `KeyError` and abort ingest
    for *every* course, not just the bad one.
    """
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return [], []
    except (json.JSONDecodeError, OSError) as exc:
        return [], [f"{Path(path).name}: unreadable ({exc})"]
    if not isinstance(raw, dict) or not isinstance(raw.get("courses"), list):
        return [], [f"{Path(path).name}: expected an object with a 'courses' list"]

    out: list[dict] = []
    why: list[str] = []
    seen: set = set()
    for i, e in enumerate(raw["courses"]):
        where = f"{Path(path).name} entry {i}"
        if not isinstance(e, dict):
            why.append(f"{where}: not an object")
            continue
        slug, title = e.get("slug"), e.get("title")
        n = e.get("expect_sessions")
        if not isinstance(slug, str) or not slug.strip():
            why.append(f"{where}: missing slug")
            continue
        if slug != _slugify(slug):
            why.append(f"{where}: slug {slug!r} is not a normalised slug")
            continue
        if not isinstance(title, str) or not title.strip():
            why.append(f"{where} ({slug}): missing title")
            continue
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 200:
            why.append(f"{where} ({slug}): expect_sessions must be an int 1..200")
            continue
        if slug in seen:
            why.append(f"{where} ({slug}): duplicate slug in the file")
            continue
        seen.add(slug)
        out.append({"slug": slug, "title": title.strip(), "expect_sessions": n,
                    "origin": "runtime",
                    "workbook_keys": [k for k in (e.get("workbook_keys") or [])
                                      if isinstance(k, str) and k],
                    "workbook": e.get("workbook") or ""})
    return out, why


def merge(declared: dict, overlay: list[dict]) -> tuple[dict, list[str]]:
    """Declared first and winning; overlay appended. Shadowing is reported, not hidden."""
    merged = {k: {**v, "origin": "declared"} for k, v in declared.items()}
    titles = {v["title"].casefold() for v in declared.values()}
    conflicts: list[str] = []
    for e in overlay:
        if e["slug"] in merged:
            conflicts.append(f"{e['slug']}: shadowed by config/constants.py")
            continue
        if e["title"].casefold() in titles:
            conflicts.append(f"{e['slug']}: title {e['title']!r} already declared")
            continue
        merged[e["slug"]] = {k: v for k, v in e.items() if k != "slug"}
        titles.add(e["title"].casefold())
    return merged, conflicts


class Roster(dict):
    """`COURSES`. A dict, so `isinstance`, `set()` and json all keep working."""

    def __init__(self, declared: dict, path: Path = REGISTRY_PATH):
        self._declared = dict(declared)
        self._path = Path(path)
        self._lock = threading.RLock()
        self._sig: Any = None
        self._checked = 0.0
        self._conflicts: list[str] = []
        self._reasons: list[str] = []
        super().__init__(declared)
        self._refresh(force=True)

    # ---------------------------------------------------------------- internals
    def _sig_now(self):
        try:
            st = self._path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._checked) < _MIN_RESTAT:
            return
        with self._lock:
            self._checked = now
            try:
                sig = self._sig_now()
                if sig == self._sig and not force:
                    return
                self._sig = sig
                overlay, reasons = load_overlay(self._path)
                merged, conflicts = merge(self._declared, overlay)
                self._reasons, self._conflicts = reasons, conflicts
                if dict(self) != merged:
                    dict.clear(self)
                    dict.update(self, merged)
            except Exception as exc:            # must never break an import
                self._reasons = [f"roster overlay ignored: {type(exc).__name__}: {exc}"]
                dict.clear(self)
                dict.update(self, {k: {**v, "origin": "declared"}
                                   for k, v in self._declared.items()})

    def invalidate(self) -> None:
        """Called by the writer after an atomic rename, so it is visible immediately."""
        self._checked = 0.0
        self._refresh(force=True)

    # ------------------------------------------------------------------- reads
    def __getitem__(self, k):
        self._refresh(); return dict.__getitem__(self, k)

    def __contains__(self, k):
        self._refresh(); return dict.__contains__(self, k)

    def get(self, k, default=None):
        self._refresh(); return dict.get(self, k, default)

    def __len__(self):
        self._refresh(); return dict.__len__(self)

    def __iter__(self):
        self._refresh()
        with self._lock:
            return iter(list(dict.keys(self)))

    def keys(self):
        self._refresh()
        with self._lock:
            return list(dict.keys(self))

    def values(self):
        self._refresh()
        with self._lock:
            return list(dict.values(self))

    def items(self):
        self._refresh()
        with self._lock:
            return list(dict.items(self))

    # ----------------------------------------------------------- introspection
    def conflicts(self) -> list[str]:
        self._refresh(); return list(self._conflicts)

    def load_reasons(self) -> list[str]:
        self._refresh(); return list(self._reasons)

    def origin_of(self, slug: str) -> str:
        self._refresh()
        return (dict.get(self, slug) or {}).get("origin", "")

    def declared(self) -> dict:
        return dict(self._declared)


# --------------------------------------------------------------- adding a course

@dataclass
class Candidate:
    """A proposed course, with its uploaded files already staged on disk."""
    title: str
    export_path: Path
    expect_sessions: Optional[int] = None
    workbook_path: Optional[Path] = None


@dataclass
class Verdict:
    ok: bool = False
    slug: str = ""
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    counted: int = 0
    source: str = ""            # "workbook" | "position"
    positional: int = 0
    stats: dict = field(default_factory=dict)
    stopped_at: str = ""


def validate(c: Candidate, path: Path = REGISTRY_PATH) -> Verdict:
    """Check a candidate. Nothing is written; a partial pass is still a refusal.

    Order matters: cheap and pure first, then the filesystem, then the parse, then the
    semantic checks. The cheap checks all run and accumulate, so the UI never has to
    play twenty questions; the later ones cascade and `stopped_at` says which halted it.
    """
    from config.constants import COURSES

    v = Verdict()
    title = (c.title or "").strip()
    if not title or len(title) > 120 or any(ch in title for ch in "/\\\n\r\t"):
        v.reasons.append("title must be 1-120 characters with no path separators")
    if c.expect_sessions is not None and not (
            isinstance(c.expect_sessions, int) and 1 <= c.expect_sessions <= 200):
        v.reasons.append("expect_sessions must be a whole number 1..200, or left blank")

    slug = _slugify(title) if title else ""
    v.slug = slug
    if title and (slug in ("", "unknown") or len(slug) < 3):
        v.reasons.append(f"title {title!r} does not make a usable slug")
    roster = dict(COURSES.items())
    if slug and slug in roster:
        v.reasons.append(f"slug {slug!r} already exists")
    if title and title.casefold() in {m["title"].casefold() for m in roster.values()}:
        # Two courses differing only in case collide in `scope`'s title->slug table and
        # one of them becomes unaddressable.
        v.reasons.append(f"a course titled {title!r} already exists")
    if not c.export_path or not Path(c.export_path).exists():
        v.reasons.append("the course export is missing")
    elif Path(c.export_path).stat().st_size < 1024:
        v.reasons.append("the course export is implausibly small")
    if v.reasons:
        v.stopped_at = "metadata"
        return v

    # --- the parse. `json.load` is the one hard gate we get for free.
    try:
        raw = json.loads(Path(c.export_path).read_text())
    except json.JSONDecodeError as exc:
        v.reasons.append(f"the export is not valid JSON: line {exc.lineno} "
                         f"column {exc.colno}")
        v.stopped_at = "json"
        return v
    obj = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(obj, dict) or not isinstance(obj.get("topics"), list) \
            or not obj["topics"]:
        v.reasons.append("the export has no `topics` list — this does not look like a "
                         "portal course export")
        v.stopped_at = "shape"
        return v

    # --- the real reader, because it never raises and a wrong field name is silent.
    from miw.ingest.portal import read_course
    records, st = read_course(str(c.export_path), title)
    v.stats = {"units": st.units, "contents": st.contents, "records": st.records,
               "sessions": st.sessions, "pooling_gap": st.pooling_gap,
               "by_source": dict(st.by_source)}
    v.positional = st.sessions
    if st.units == 0:
        v.reasons.append("the export produced no units at all")
        v.stopped_at = "shape"
        return v
    if st.records == 0:
        v.reasons.append(
            f"parsed {st.units} units but produced 0 records — the export's field names "
            f"probably do not match the portal's (`content` vs `content_markdown` is "
            f"the usual cause, and it fails silently)")
        v.stopped_at = "records"
        return v
    if st.pooling_gap:
        v.warnings.append(f"{st.pooling_gap} pooling unit(s) yielded no questions")
    v.warnings += list(st.skipped[:3])

    # --- the workbook, when one was supplied.
    declared = 0
    if c.workbook_path:
        from miw.ingest.outline import read_outline
        o = read_outline(c.workbook_path)
        if o.stats.errors:
            v.reasons += [f"workbook: {e}" for e in o.stats.errors[:3]]
            v.stopped_at = "workbook"
            return v
        if not o.session_count:
            v.reasons.append("workbook: no numbered sessions in its Course Outline sheet")
            v.stopped_at = "workbook"
            return v
        declared = o.session_count

    v.counted = declared or st.sessions
    v.source = "workbook" if declared else "position"

    # --- the session count. Adopting silently is how it was mis-calibrated last time.
    if c.expect_sessions is None:
        v.warnings.append(f"expect_sessions was blank, so {v.counted} "
                          f"(from the {v.source}) will be recorded")
    elif c.expect_sessions != v.counted:
        v.reasons.append(
            f"you gave expect_sessions={c.expect_sessions}, but the {v.source} says "
            f"{v.counted}" + (f" and the export's positional count is {st.sessions}"
                              if declared and st.sessions != declared else ""))
        v.stopped_at = "sessions"
        return v

    v.ok = True
    return v


def commit(c: Candidate, v: Verdict, path: Path = REGISTRY_PATH) -> dict:
    """Place the files, then register. Only ever called with `v.ok`.

    **Order is load-bearing.** The roster entry is the switch that makes `cmd_ingest`
    open the export at all, so it must become true LAST. The reverse order leaves a
    window in which a running API advertises a course whose export is not on disk, and a
    concurrent ingest reports `missing export` and exits 1.

    Concurrency: an exclusive lock around read-modify-write, because two quick POSTs
    would otherwise lose one entry.
    """
    import fcntl
    import shutil

    from config.constants import COURSES

    slug = v.slug
    exports = ROOT / "data" / "courses"
    sheets = ROOT / "data" / "sheets"
    exports.mkdir(parents=True, exist_ok=True)
    dest_export = exports / f"{slug}.json"
    dest_book = None
    placed: list = []
    try:
        shutil.move(str(c.export_path), dest_export)
        placed.append(dest_export)
        if c.workbook_path:
            sheets.mkdir(parents=True, exist_ok=True)
            # Named by TITLE, so `norm_stem(filename) == norm_stem(title)` and the
            # roster-derived workbook key matches it by construction — a course added
            # here never needs a hand-written alias.
            dest_book = sheets / f"{c.title} - Course Contents.xlsx"
            shutil.move(str(c.workbook_path), dest_book)
            placed.append(dest_book)

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.parent / ".course_registry.lock"
        lock.touch(exist_ok=True)
        with open(lock, "r+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                entries, _ = load_overlay(path)          # re-read INSIDE the lock
                entries = [e for e in entries if e["slug"] != slug]
                entries.append({
                    "slug": slug, "title": c.title.strip(),
                    "expect_sessions": v.counted,
                    "workbook": dest_book.name if dest_book else "",
                    "added_at": _now(),
                    "session_source": v.source,
                })
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(
                    {"version": 1, "courses": entries}, indent=1))
                os.replace(tmp, path)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except Exception:
        for f in placed:                                 # roll the files back out
            try:
                Path(f).unlink()
            except OSError:
                pass
        raise

    if hasattr(COURSES, "invalidate"):
        COURSES.invalidate()
    return {"slug": slug, "title": c.title.strip(), "expect_sessions": v.counted,
            "session_source": v.source, "export": str(dest_export),
            "workbook": str(dest_book) if dest_book else ""}


def unregister(slug: str, purge: bool = False,
               path: Path = REGISTRY_PATH) -> tuple[bool, str]:
    """Remove a runtime course. Refuses for hand-declared ones."""
    import fcntl

    from config.constants import COURSES

    if COURSES.origin_of(slug) == "declared":
        return False, f"{slug} is declared in config/constants.py; remove it there"
    path = Path(path)
    entries, _ = load_overlay(path)
    if not any(e["slug"] == slug for e in entries):
        return False, f"no runtime course {slug!r}"
    lock = path.parent / ".course_registry.lock"
    lock.touch(exist_ok=True)
    with open(lock, "r+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            entries, _ = load_overlay(path)
            book = next((e.get("workbook") for e in entries if e["slug"] == slug), "")
            entries = [e for e in entries if e["slug"] != slug]
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"version": 1, "courses": entries}, indent=1))
            os.replace(tmp, path)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    if purge:
        for f in (ROOT / "data" / "courses" / f"{slug}.json",
                  (ROOT / "data" / "sheets" / book) if book else None):
            if f:
                try:
                    Path(f).unlink()
                except OSError:
                    pass
    if hasattr(COURSES, "invalidate"):
        COURSES.invalidate()
    return True, ""


def _now() -> str:
    from miw.schema import utcnow
    return utcnow()
