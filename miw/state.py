"""Week-over-week state. Without it, every run reports the same standing facts.

Two jobs:

* **Repeat suppression.** A finding is `new`, `worsened`, `unchanged` or `resolved`
  relative to what we already knew. `unchanged` stays in the store and out of the
  digest — a weekly report that repeats last week's list gets ignored by week three.
* **Flap protection.** `consecutive_failures` is what lets the probe stage hold the
  line between `unreachable` (our problem) and `broken` (the tool's problem). A single
  failed request never becomes a finding.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path("state/miw.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS probe_state (
    dep_id              TEXT PRIMARY KEY,
    canonical_name      TEXT,
    last_status         TEXT,
    last_checked        TEXT,
    text_hash           TEXT,
    latest_version      TEXT,
    http_status         INTEGER,
    repo_archived       INTEGER,
    consecutive_failures INTEGER DEFAULT 0,
    first_seen          TEXT
);
-- Two watermarks, not one, and the difference is the whole of this table's job.
--
-- `fingerprint`/`severity`/`last_seen` record what the last RUN observed.
-- `reported_*` record what a human was last SHOWN. Classifying against the first
-- meant a second `analyse` on the same inputs found its own row already present and
-- returned "unchanged" for everything, so the digest printed "No new or worsened
-- findings this week." while 33 findings carried `first_raised` of that same day.
-- It happened in production on three separate dates, and there is no way back: the
-- artifact is overwritten in place and nothing else reads `first_raised`.
--
-- Keyed on the fingerprint rather than a flag because "shown to a human" is a claim
-- about a specific situation, not about an id: the same finding at a worse severity
-- is news again.
CREATE TABLE IF NOT EXISTS finding_state (
    finding_id   TEXT PRIMARY KEY,
    dep_id       TEXT,
    signal       TEXT,
    severity     TEXT,
    fingerprint  TEXT,
    first_raised TEXT,
    last_seen    TEXT,
    resolved_at  TEXT,
    reported_at          TEXT,
    reported_fingerprint TEXT,
    reported_severity    TEXT
);
-- Per-dependency identity that must survive an `extract`. The inventory is rebuilt
-- from scratch every run, so anything stored on the Dependency object itself is
-- regenerated and cannot express "we have never researched this one".
CREATE TABLE IF NOT EXISTS dep_state (
    dep_id              TEXT PRIMARY KEY,
    canonical_name      TEXT,
    first_seen          TEXT,
    last_researched_at  TEXT
);
-- Pricing pages are tracked separately from docs pages: a vendor can rewrite its
-- pricing without touching its docs, and the working pricing URL is worth
-- remembering so the /pricing, /plans, /#pricing guessing is paid once per vendor.
CREATE TABLE IF NOT EXISTS pricing_state (
    dep_id       TEXT PRIMARY KEY,
    pricing_url  TEXT,
    text_hash    TEXT,
    free_signals TEXT,      -- JSON list of the free-tier phrases last seen
    checked_at   TEXT
);
-- The event path's memory. Without a watermark, a daily poll re-reports every
-- historical deprecation every day.
CREATE TABLE IF NOT EXISTS watch_state (
    source_key   TEXT PRIMARY KEY,
    kind         TEXT,
    value        TEXT,
    evidence_url TEXT,
    observed_at  TEXT,
    first_seen   TEXT
);
-- What a vendor's catalogue contained the last time we looked, as the full id set.
--
-- Deliberately not a hash. `watch_state` already stores `row_set_hash`, which answers
-- "did this change?" and is all the daily watch needs. Reporting what is NEW needs the
-- set itself, because the answer is a difference, not a bit. It is the same rule for
-- every enumerator - vendor model catalogues, n8n's node tree, documentation headings -
-- so a set difference is mostly deliberate curriculum scoping rather than news, and
-- only what APPEARED since the last look is worth a finding.
CREATE TABLE IF NOT EXISTS catalogue_snapshot (
    source_key  TEXT PRIMARY KEY,
    ids         TEXT,        -- JSON array, sorted
    observed_at TEXT,
    first_seen  TEXT
);
CREATE TABLE IF NOT EXISTS watch_signal (
    signal_id    TEXT PRIMARY KEY,
    vendor_key   TEXT,
    source_key   TEXT,
    trigger      TEXT,
    from_value   TEXT,
    to_value     TEXT,
    evidence_url TEXT,
    refs         TEXT,
    status       TEXT,      -- baseline | open | investigated
    observed_at  TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id  TEXT,
    stage   TEXT,
    at      TEXT,
    detail  TEXT
);
CREATE TABLE IF NOT EXISTS review_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id    TEXT,
    dep_id        TEXT,
    signal        TEXT,
    verdict       TEXT,        -- accepted | rejected
    reason        TEXT,
    fingerprint   TEXT,        -- what was true when the call was made
    corrected_what TEXT,
    corrected_why  TEXT,
    corrected_when TEXT,
    reviewer      TEXT,
    at            TEXT
);
CREATE INDEX IF NOT EXISTS idx_finding_dep ON finding_state(dep_id);
CREATE INDEX IF NOT EXISTS idx_decision_finding ON review_decisions(finding_id);
"""

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class State:
    def __init__(self, path: Path = DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=20.0)
        self.conn.row_factory = sqlite3.Row
        # WAL so a stage subprocess writing does not block the UI reading, and a busy
        # timeout so the loser of a race waits instead of raising "database is locked".
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=20000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
            self._migrate(cur)
        self.conn.commit()

    # Columns added after the table shipped. `CREATE TABLE IF NOT EXISTS` is a no-op on
    # an existing table, so a DB written by an earlier version keeps the old shape and
    # every read of a new column raises. Additive only: nothing here drops or rewrites.
    _ADDED_COLUMNS = (
        ("finding_state", "reported_at", "TEXT"),
        ("finding_state", "reported_fingerprint", "TEXT"),
        ("finding_state", "reported_severity", "TEXT"),
        # Hashes of the deprecation notices already seen on this dependency's pages.
        ("probe_state", "notice_keys", "TEXT"),
    )

    def _migrate(self, cur) -> None:
        added = set()
        for table, column, decl in self._ADDED_COLUMNS:
            have = {r[1] for r in cur.execute(f'PRAGMA table_info("{table}")')}
            if column not in have:
                cur.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {decl}')
                added.add(column)
        # Backfill ONCE, in the same call that added the column - never as a standing
        # rule. A standing `WHERE reported_fingerprint IS NULL` would fire on every
        # connection, so the rows a run had just raised would be stamped "reported" by
        # the next `State()` before anyone had seen them, which is the original defect
        # wearing the fix's clothes.
        #
        # Treating pre-existing rows as reported is deliberate: without it the first
        # run after this change re-raises every standing finding at once - 62 of them
        # on the live artifact - which is the alert-fatigue event the fix exists to
        # prevent, delivered by the fix. What the old behaviour missed is still
        # recoverable from `first_raised`; a digest nobody reads is not.
        if "reported_fingerprint" in added:
            cur.execute(
                "UPDATE finding_state SET reported_at = last_seen,"
                " reported_fingerprint = fingerprint, reported_severity = severity"
                " WHERE fingerprint IS NOT NULL")

    def close(self) -> None:
        self.conn.close()

    # --- probe --------------------------------------------------------------

    def probe_prev(self, dep_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM probe_state WHERE dep_id = ?", (dep_id,)).fetchone()

    def probe_save(self, *, dep_id: str, canonical_name: str, status: str,
                   checked_at: str, text_hash: str = "", latest_version: str = "",
                   http_status: Optional[int] = None,
                   repo_archived: Optional[bool] = None,
                   consecutive_failures: int = 0,
                   notice_keys: Optional[list] = None) -> None:
        prev = self.probe_prev(dep_id)
        first_seen = prev["first_seen"] if prev else checked_at
        # Union, never replacement. A notice that scrolls off a changelog has still
        # been seen, and re-reporting it when it reappears on an archive page would be
        # the repeat-suppression failure this table exists to prevent.
        keys = sorted(set(self.notice_keys(dep_id)) | set(notice_keys or []))
        self.conn.execute(
            "INSERT INTO probe_state (dep_id, canonical_name, last_status, last_checked,"
            " text_hash, latest_version, http_status, repo_archived,"
            " consecutive_failures, first_seen, notice_keys)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(dep_id) DO UPDATE SET canonical_name=excluded.canonical_name,"
            " last_status=excluded.last_status, last_checked=excluded.last_checked,"
            " text_hash=excluded.text_hash, latest_version=excluded.latest_version,"
            " http_status=excluded.http_status, repo_archived=excluded.repo_archived,"
            " consecutive_failures=excluded.consecutive_failures,"
            " notice_keys=excluded.notice_keys",
            (dep_id, canonical_name, status, checked_at, text_hash, latest_version,
             http_status, None if repo_archived is None else int(repo_archived),
             consecutive_failures, first_seen, json.dumps(keys)))
        self.conn.commit()

    def notice_keys(self, dep_id: str) -> list:
        """Deprecation notices already seen on this dependency's pages."""
        row = self.probe_prev(dep_id)
        if row is None:
            return []
        try:
            return json.loads(row["notice_keys"] or "[]")
        except (json.JSONDecodeError, TypeError, IndexError, KeyError):
            return []

    # --- findings -----------------------------------------------------------

    def classify_finding(self, *, finding_id: str, dep_id: str, signal: str,
                         severity: str, fingerprint: str, now: str) -> str:
        """Diff this finding against what we already knew. Returns the diff class."""
        prev = self.conn.execute(
            "SELECT * FROM finding_state WHERE finding_id = ?", (finding_id,)).fetchone()
        if prev is None:
            self.conn.execute(
                "INSERT INTO finding_state (finding_id, dep_id, signal, severity,"
                " fingerprint, first_raised, last_seen) VALUES (?,?,?,?,?,?,?)",
                (finding_id, dep_id, signal, severity, fingerprint, now, now))
            self.conn.commit()
            return "new"

        # Compared against what a human was last SHOWN, never against what the last run
        # merely saw. Re-running `analyse` without an intervening `report` must not
        # consume the news, because nothing has been said to anybody yet.
        reported_fp = prev["reported_fingerprint"]
        was = SEVERITY_ORDER.get(prev["reported_severity"] or "info", 0)
        now_sev = SEVERITY_ORDER.get(severity, 0)
        changed = fingerprint != (reported_fp or "")
        # A finding that had been resolved and is back is news again.
        reopened = prev["resolved_at"] is not None
        self.conn.execute(
            "UPDATE finding_state SET severity=?, fingerprint=?, last_seen=?,"
            " resolved_at=NULL WHERE finding_id=?",
            (severity, fingerprint, now, finding_id))
        self.conn.commit()
        if reopened:
            return "new"
        if reported_fp is None:
            # Seen on an earlier run, never shown to anyone - an `analyse` that was
            # re-run, or one whose `report` never completed. Still news.
            return "new"
        # Direction matters. Treating any fingerprint change as "worsened" reported a
        # tool going from broken back to merely redirected as a deterioration.
        if now_sev > was:
            return "worsened"
        if now_sev < was:
            return "improved"
        return "changed" if changed else "unchanged"

    def finding_count(self) -> int:
        """How many findings this store remembers. 0 means no week-over-week memory."""
        return self.conn.execute("SELECT COUNT(*) FROM finding_state").fetchone()[0]

    def mark_reported(self, findings: list, now: str) -> int:
        """Record that these findings, as they now stand, have been shown to a human.

        Called by `report` once the digest is on disk. It is what makes the classifier
        idempotent: until this runs, every `analyse` keeps calling the same finding new,
        however many times it is executed.

        `findings` is a list of (finding_id, fingerprint, severity).
        """
        rows = [(now, fp, sev, fid) for fid, fp, sev in findings]
        if not rows:
            return 0
        self.conn.executemany(
            "UPDATE finding_state SET reported_at=?, reported_fingerprint=?,"
            " reported_severity=? WHERE finding_id=?", rows)
        self.conn.commit()
        return len(rows)

    def resolve_absent(self, seen_ids: set[str], now: str,
                       examined_dep_ids: Optional[set[str]] = None
                       ) -> list[dict[str, Any]]:
        """Mark previously-open findings that did not recur as resolved.

        `examined_dep_ids` is the set of dependencies this run actually looked at.
        Passing None means "everything was examined", which is only true of an
        unscoped run.

        This parameter exists because omitting it was a real bug: a scoped run over one
        course's sessions marked every *other* course's open findings resolved, telling
        the team a fire went out when the run had simply not looked at it. Silence is
        not evidence of repair.
        """
        rows = self.conn.execute(
            "SELECT * FROM finding_state WHERE resolved_at IS NULL").fetchall()
        out = []
        for r in rows:
            if r["finding_id"] in seen_ids:
                continue
            if examined_dep_ids is not None and r["dep_id"] not in examined_dep_ids:
                continue                      # out of scope: not looked at, not resolved
            self.conn.execute("UPDATE finding_state SET resolved_at=? WHERE finding_id=?",
                              (now, r["finding_id"]))
            out.append(dict(r))
        self.conn.commit()
        return out

    # --- dependency identity across extract runs ----------------------------

    def dep_state(self) -> dict[str, sqlite3.Row]:
        return {r["dep_id"]: r for r in
                self.conn.execute("SELECT * FROM dep_state").fetchall()}

    def upsert_dep_state(self, deps: list[tuple[str, str, str]]) -> int:
        """Record (dep_id, canonical_name, first_seen), keeping an existing first_seen.

        `extract` rebuilds every Dependency, so `first_seen` on the object is always
        "now". Persisting it here is what makes "how long have we known about this"
        answerable at all.
        """
        self.conn.executemany(
            "INSERT INTO dep_state (dep_id, canonical_name, first_seen)"
            " VALUES (?,?,?) ON CONFLICT(dep_id) DO UPDATE SET"
            " canonical_name=excluded.canonical_name", deps)
        self.conn.commit()
        return len(deps)

    def mark_researched(self, dep_ids: list[str], now: str) -> None:
        """Stamp the rotation clock. Ordering research by this is what makes the
        rotating watch actually rotate."""
        self.conn.executemany(
            "INSERT INTO dep_state (dep_id, first_seen, last_researched_at)"
            " VALUES (?,?,?) ON CONFLICT(dep_id) DO UPDATE SET"
            " last_researched_at=excluded.last_researched_at",
            [(d, now, now) for d in dep_ids])
        self.conn.commit()

    # --- watch: watermarks and the signal ledger ----------------------------

    def watermark(self, source_key: str):
        return self.conn.execute(
            "SELECT * FROM watch_state WHERE source_key = ?", (source_key,)).fetchone()

    def watermark_save(self, *, source_key: str, kind: str, value: str,
                       evidence_url: str, now: str) -> None:
        prev = self.watermark(source_key)
        first = prev["first_seen"] if prev else now
        self.conn.execute(
            "INSERT INTO watch_state (source_key, kind, value, evidence_url,"
            " observed_at, first_seen) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(source_key) DO UPDATE SET kind=excluded.kind,"
            " value=excluded.value, evidence_url=excluded.evidence_url,"
            " observed_at=excluded.observed_at",
            (source_key, kind, value, evidence_url, now, first))
        self.conn.commit()

    def snapshot(self, source_key: str) -> Optional[set]:
        """The id set we last saw for this source, or None if we have never looked.

        None and the empty set are different answers and the caller must treat them so:
        never looked means "seed a baseline and report nothing", while an empty
        catalogue means the vendor lists nothing, which is a real (and alarming) state.
        """
        row = self.conn.execute(
            "SELECT ids FROM catalogue_snapshot WHERE source_key = ?",
            (source_key,)).fetchone()
        if row is None:
            return None
        try:
            return set(json.loads(row["ids"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            return None

    def snapshot_save(self, *, source_key: str, ids, now: str) -> None:
        row = self.conn.execute(
            "SELECT first_seen FROM catalogue_snapshot WHERE source_key = ?",
            (source_key,)).fetchone()
        first = row["first_seen"] if row else now
        self.conn.execute(
            "INSERT INTO catalogue_snapshot (source_key, ids, observed_at, first_seen)"
            " VALUES (?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET"
            " ids=excluded.ids, observed_at=excluded.observed_at",
            (source_key, json.dumps(sorted(ids)), now, first))
        self.conn.commit()

    def signal_save(self, sig, status: str, now: str) -> bool:
        """Record a signal. Returns True if it is new to the ledger."""
        import json as _json
        existing = self.conn.execute(
            "SELECT signal_id FROM watch_signal WHERE signal_id = ?",
            (sig.signal_id,)).fetchone()
        self.conn.execute(
            "INSERT INTO watch_signal (signal_id, vendor_key, source_key, trigger,"
            " from_value, to_value, evidence_url, refs, status, observed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(signal_id) DO UPDATE SET"
            " status=excluded.status, observed_at=excluded.observed_at",
            (sig.signal_id, sig.vendor_key, sig.source_key, sig.trigger,
             sig.from_value, sig.to_value, sig.evidence_url,
             _json.dumps(sig.refs), status, now))
        self.conn.commit()
        return existing is None

    def open_signals(self, limit: int = 50):
        return self.conn.execute(
            "SELECT * FROM watch_signal WHERE status = 'open'"
            " ORDER BY observed_at DESC LIMIT ?", (limit,)).fetchall()

    def signal_mark(self, signal_id: str, status: str) -> None:
        self.conn.execute("UPDATE watch_signal SET status=? WHERE signal_id=?",
                          (status, signal_id))
        self.conn.commit()

    # --- pricing snapshots --------------------------------------------------

    def pricing_prev(self, dep_id: str) -> tuple[str, str, list[str]]:
        row = self.conn.execute(
            "SELECT pricing_url, text_hash, free_signals FROM pricing_state "
            "WHERE dep_id = ?", (dep_id,)).fetchone()
        if not row:
            return "", "", []
        import json as _json
        try:
            free = _json.loads(row["free_signals"] or "[]")
        except ValueError:
            free = []
        return row["pricing_url"] or "", row["text_hash"] or "", free

    def pricing_save(self, *, dep_id: str, pricing_url: str, text_hash: str,
                     free_signals: list[str], now: str) -> None:
        import json as _json
        self.conn.execute(
            "INSERT INTO pricing_state (dep_id, pricing_url, text_hash, free_signals,"
            " checked_at) VALUES (?,?,?,?,?) ON CONFLICT(dep_id) DO UPDATE SET"
            " pricing_url=excluded.pricing_url, text_hash=excluded.text_hash,"
            " free_signals=excluded.free_signals, checked_at=excluded.checked_at",
            (dep_id, pricing_url, text_hash, _json.dumps(free_signals), now))
        self.conn.commit()

    # --- reviewer decisions -------------------------------------------------

    def record_decision(self, *, finding_id: str, dep_id: str, signal: str,
                        verdict: str, reason: str = "", fingerprint: str = "",
                        corrected_what: str = "", corrected_why: str = "",
                        corrected_when: str = "", reviewer: str = "", now: str = "") -> None:
        self.conn.execute(
            "INSERT INTO review_decisions (finding_id, dep_id, signal, verdict, reason,"
            " fingerprint, corrected_what, corrected_why, corrected_when, reviewer, at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (finding_id, dep_id, signal, verdict, reason, fingerprint, corrected_what,
             corrected_why, corrected_when, reviewer, now))
        self.conn.commit()

    def latest_decision(self, finding_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM review_decisions WHERE finding_id = ? ORDER BY id DESC LIMIT 1",
            (finding_id,)).fetchone()

    def decisions(self, verdict: Optional[str] = None) -> list[sqlite3.Row]:
        if verdict:
            return self.conn.execute(
                "SELECT * FROM review_decisions WHERE verdict = ? ORDER BY id DESC",
                (verdict,)).fetchall()
        return self.conn.execute(
            "SELECT * FROM review_decisions ORDER BY id DESC").fetchall()

    def precision_stats(self) -> dict:
        """Accepted / triaged. The PRD's supporting metric, finally measurable.

        Counts the LATEST decision per finding only: a finding rejected once and
        accepted after a fix should not count against the system twice.
        """
        rows = self.conn.execute(
            "SELECT verdict FROM review_decisions d WHERE d.id = ("
            "  SELECT MAX(id) FROM review_decisions x WHERE x.finding_id = d.finding_id)"
        ).fetchall()
        acc = sum(1 for r in rows if r["verdict"] == "accepted")
        return {"triaged": len(rows), "accepted": acc,
                "rejected": len(rows) - acc,
                "precision": round(acc / len(rows), 3) if rows else None}

    def log_run(self, run_id: str, stage: str, at: str, detail: str = "") -> None:
        self.conn.execute("INSERT INTO runs (run_id, stage, at, detail) VALUES (?,?,?,?)",
                          (run_id, stage, at, detail))
        self.conn.commit()
