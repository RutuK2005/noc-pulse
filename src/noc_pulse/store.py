"""SQLite storage for raw probe samples, collector runs, alarms and tickets.

SQLite (stdlib) is enough for one collector: WAL mode lets the dashboard and
reports read while the collector writes. Samples store target *ids* only, never
the resolved IP of the site gateway, so exported samples are anonymous. (Ticket
diagnostics from a live engine run do contain the addresses that ping/tracert
print; they are not part of the CSV export.)
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .probes import ProbeResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id          INTEGER PRIMARY KEY,
    ts          REAL NOT NULL,          -- unix epoch seconds (UTC)
    target      TEXT NOT NULL,
    probe       TEXT NOT NULL,          -- ping | tcp | dns | http
    ok          INTEGER NOT NULL,
    latency_ms  REAL,
    sent        INTEGER,
    received    INTEGER,
    rtts        TEXT,                   -- JSON list of echo RTTs (ping)
    status      INTEGER,                -- HTTP status
    cert_days   REAL,
    error       TEXT,
    detail      TEXT                    -- JSON (timing breakdown, backend, ...)
);
CREATE INDEX IF NOT EXISTS ix_samples_tp ON samples(target, probe, ts);
CREATE INDEX IF NOT EXISTS ix_samples_ts ON samples(ts);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started     REAL NOT NULL,
    heartbeat   REAL NOT NULL,
    stopped     REAL,
    meta        TEXT
);

CREATE TABLE IF NOT EXISTS alarms (
    id            INTEGER PRIMARY KEY,
    key           TEXT NOT NULL,
    target        TEXT NOT NULL,
    probe         TEXT NOT NULL,
    rule          TEXT NOT NULL,
    severity      TEXT NOT NULL,
    state         TEXT NOT NULL,        -- active | cleared
    raised_at     REAL NOT NULL,
    cleared_at    REAL,
    last_seen     REAL,
    occurrences   INTEGER NOT NULL DEFAULT 1,
    flap_count    INTEGER NOT NULL DEFAULT 0,
    suppressed    INTEGER NOT NULL DEFAULT 0,
    root_cause_id INTEGER,
    ticket_id     TEXT,
    message       TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id               TEXT PRIMARY KEY,
    alarm_id         INTEGER,
    target           TEXT,
    title            TEXT,
    impact           TEXT,
    urgency          TEXT,
    priority         TEXT,
    state            TEXT,
    opened_at        REAL,
    acknowledged_at  REAL,
    in_progress_at   REAL,
    resolved_at      REAL,
    closed_at        REAL,
    response_due     REAL,
    resolve_due      REAL,
    parent_id        TEXT,
    services         TEXT,
    diagnostics      TEXT,
    resolution       TEXT,
    data             TEXT
);

CREATE TABLE IF NOT EXISTS ticket_events (
    id         INTEGER PRIMARY KEY,
    ticket_id  TEXT NOT NULL,
    ts         REAL NOT NULL,
    kind       TEXT NOT NULL,
    text       TEXT
);
"""

SAMPLE_COLS = (
    "ts",
    "target",
    "probe",
    "ok",
    "latency_ms",
    "sent",
    "received",
    "rtts",
    "status",
    "cert_days",
    "error",
    "detail",
)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- samples ----------------------------------------------------------
    def insert_sample(self, r: ProbeResult) -> int:
        row = r.to_row()
        cur = self.conn.execute(
            f"INSERT INTO samples ({','.join(SAMPLE_COLS)}) VALUES ({','.join('?' * len(SAMPLE_COLS))})",
            [row[c] for c in SAMPLE_COLS],
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def iter_samples(
        self,
        target: str | None = None,
        probe: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> Iterator[ProbeResult]:
        sql = f"SELECT {','.join(SAMPLE_COLS)} FROM samples WHERE 1=1"
        args: list[Any] = []
        if target:
            sql += " AND target = ?"
            args.append(target)
        if probe:
            sql += " AND probe = ?"
            args.append(probe)
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        if until is not None:
            sql += " AND ts < ?"
            args.append(until)
        sql += " ORDER BY ts, id"
        for row in self.conn.execute(sql, args):
            yield ProbeResult.from_row(row)

    def samples(self, **kw: Any) -> list[ProbeResult]:
        return list(self.iter_samples(**kw))

    def series_keys(self) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT DISTINCT target, probe FROM samples ORDER BY target, probe"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def time_range(self) -> tuple[float | None, float | None, int]:
        r = self.conn.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM samples").fetchone()
        return r[0], r[1], r[2]

    # ---- collector runs ---------------------------------------------------
    def start_run(self, meta: dict[str, Any]) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO runs (started, heartbeat, meta) VALUES (?, ?, ?)",
            (now, now, json.dumps(meta)),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def heartbeat(self, run_id: int) -> None:
        self.conn.execute("UPDATE runs SET heartbeat = ? WHERE id = ?", (time.time(), run_id))
        self.conn.commit()

    def stop_run(self, run_id: int) -> None:
        now = time.time()
        self.conn.execute("UPDATE runs SET heartbeat = ?, stopped = ? WHERE id = ?", (now, now, run_id))
        self.conn.commit()

    def runs(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM runs ORDER BY id")]

    # ---- alarms / tickets (written by the engine) -------------------------
    def clear_engine_state(self) -> None:
        for table in ("alarms", "tickets", "ticket_events"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    def upsert(self, table: str, row: dict[str, Any], key: str = "id") -> None:
        cols = list(row)
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != key)
        self.conn.execute(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({key}) DO UPDATE SET {updates}",
            [row[c] for c in cols],
        )
        self.conn.commit()

    def add_event(self, ticket_id: str, ts: float, kind: str, text: str) -> None:
        self.conn.execute(
            "INSERT INTO ticket_events (ticket_id, ts, kind, text) VALUES (?, ?, ?, ?)",
            (ticket_id, ts, kind, text),
        )
        self.conn.commit()

    def rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, args)]
