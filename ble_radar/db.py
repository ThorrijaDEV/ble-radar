"""SQLite storage: schema, recording, queries (mirrors wifi-time-machine style)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ble_radar.scan import Observation
from ble_radar.util import normalize_address, now_epoch

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY,
    started_at   INTEGER NOT NULL,
    ended_at     INTEGER,
    observations INTEGER NOT NULL DEFAULT 0,
    label        TEXT,
    backend      TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    address    TEXT PRIMARY KEY,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 0,
    last_name  TEXT,
    best_rssi  INTEGER,
    last_rssi  INTEGER
);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    address     TEXT NOT NULL,
    name        TEXT,
    rssi        INTEGER,
    observed_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_addr_time ON observations(address, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_time ON observations(observed_at);
"""


def default_db_path() -> Path:
    env = os.environ.get("BLE_RADAR_DB")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "ble-radar" / "radar.db"


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn


def start_session(
    conn: sqlite3.Connection, label: str | None = None, backend: str = ""
) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (started_at, label, backend) VALUES (?, ?, ?)",
        (now_epoch(), label, backend),
    )
    conn.commit()
    return cur.lastrowid


def end_session(conn: sqlite3.Connection, session_id: int, observations: int) -> None:
    conn.execute(
        "UPDATE sessions SET ended_at = ?, observations = ? WHERE id = ?",
        (now_epoch(), observations, session_id),
    )
    conn.commit()


def delete_session(conn: sqlite3.Connection, session_id: int) -> None:
    """Remove a session that never really ran (e.g. aborted before startup)."""
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


def record_observation(
    conn: sqlite3.Connection, session_id: int | None, obs: Observation
) -> None:
    conn.execute(
        "INSERT INTO observations (session_id, address, name, rssi, observed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, obs.address, obs.name, obs.rssi, obs.seen_at),
    )
    conn.execute(
        """
        INSERT INTO devices (address, first_seen, last_seen, times_seen, last_name, best_rssi, last_rssi)
        VALUES (?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            last_seen = excluded.last_seen,
            times_seen = devices.times_seen + 1,
            last_name = COALESCE(NULLIF(excluded.last_name, ''), devices.last_name),
            best_rssi = MIN(COALESCE(devices.best_rssi, -1000), COALESCE(excluded.best_rssi, -1000)),
            last_rssi = excluded.last_rssi
        """,
        (
            normalize_address(obs.address),
            obs.seen_at,
            obs.seen_at,
            obs.name,
            obs.rssi,
            obs.rssi,
        ),
    )
    conn.commit()


def list_sessions(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.id, s.started_at, s.ended_at, s.observations, s.label, s.backend,
               (SELECT COUNT(DISTINCT o.address) FROM observations o WHERE o.session_id = s.id) AS devices
        FROM sessions s
        ORDER BY s.started_at DESC, s.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def observations(
    conn: sqlite3.Connection,
    session_id: int | None = None,
    address: str | None = None,
    since: int | None = None,
    until: int | None = None,
    limit: int = 2000,
    order: str = "DESC",
) -> list[sqlite3.Row]:
    where, params = ["1=1"], []
    if session_id is not None:
        where.append("o.session_id = ?")
        params.append(session_id)
    if address:
        where.append("o.address = ?")
        params.append(address)
    if since is not None:
        where.append("o.observed_at >= ?")
        params.append(since)
    if until is not None:
        where.append("o.observed_at <= ?")
        params.append(until)
    sql = f"""
        SELECT o.observed_at, o.address, o.name, o.rssi, o.session_id
        FROM observations o
        WHERE {' AND '.join(where)}
        ORDER BY o.observed_at {order}
        LIMIT ?
    """
    return conn.execute(sql, (*params, limit)).fetchall()


def session_observations(conn, session_id: int, order: str = "ASC") -> list[sqlite3.Row]:
    return observations(conn, session_id=session_id, limit=10_000_000, order=order)


def device_history(
    conn: sqlite3.Connection, address: str, since: int | None = None, limit: int = 1000
) -> list[sqlite3.Row]:
    return observations(conn, address=address, since=since, limit=limit)


def device_census(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT address, first_seen, last_seen, times_seen, last_name,
               best_rssi, last_rssi
        FROM devices
        ORDER BY last_seen DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def stats(conn: sqlite3.Connection) -> dict:
    n_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    n_devices = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    n_obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    first, last = conn.execute(
        "SELECT MIN(observed_at), MAX(observed_at) FROM observations"
    ).fetchone()
    return {
        "sessions": n_sessions,
        "devices": n_devices,
        "observations": n_obs,
        "first": first,
        "last": last,
    }
