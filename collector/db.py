"""SQLite database: schema, snapshots, retention pruning.

One table per node kind, plus one for the host. `snapshots` is the blockchain
table; it keeps its name (and its now-unwritten host columns) so existing
databases stay readable.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL UNIQUE,  -- Unix epoch (UTC), truncated to 10-min window
    chain_tip INTEGER,
    lib TEXT,                            -- Last Irreversible Block hash (string)
    mode TEXT,                           -- Node mode: "Bootstrapping", "Normal", etc.
    epoch INTEGER,
    mempool_depth INTEGER DEFAULT 0,
    peer_count INTEGER DEFAULT 0,
    n_connections INTEGER DEFAULT 0,
    cpu_temp REAL,                       -- legacy: host metrics now live in host_snapshots
    cpu_pct REAL,
    mem_pct REAL,
    load1 REAL,
    wallet_balances TEXT NOT NULL         -- JSON: {"wallet_name": balance}
);

CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp);
"""

HOST_COLUMNS = {"cpu_temp": "REAL", "cpu_pct": "REAL", "mem_pct": "REAL", "load1": "REAL"}

# Fields every Logos Core module kind shares.
_MODULE_COLUMNS = {
    "module_status": "TEXT",             # loaded | crashed | not_loaded
    "module_version": "TEXT",
    "uptime_seconds": "INTEGER",
    "memory_mb": "REAL",
    "cpu_seconds": "REAL",
    "running": "INTEGER",                # 1 when the node inside the module is started
}

# Columns of the per-kind tables. `*_total` columns are counters that restart
# from zero with the node; the dashboard charts their per-interval differences.
KIND_TABLES: dict[str, tuple[str, dict[str, str]]] = {
    "storage": ("storage_snapshots", {
        **_MODULE_COLUMNS,
        "lib_version": "TEXT",
        "network": "TEXT",
        "peer_id": "TEXT",
        "peers": "INTEGER",
        "quota_max_bytes": "INTEGER",
        "quota_used_bytes": "INTEGER",
        "quota_reserved_bytes": "INTEGER",
        "blocks": "INTEGER",
        "manifests": "INTEGER",          # files stored locally
        "uploads_total": "INTEGER",
        "downloads_total": "INTEGER",
        "blocks_sent_total": "INTEGER",  # blocks served to peers
        "blocks_received_total": "INTEGER",
    }),
    "delivery": ("delivery_snapshots", {
        **_MODULE_COLUMNS,
        "node_version": "TEXT",
        "connection_status": "TEXT",
        "rln_state": "TEXT",
        "rln_message": "TEXT",
        "peers": "INTEGER",
        "bytes_in_total": "INTEGER",
        "bytes_out_total": "INTEGER",
        "messages_in_total": "INTEGER",
        "messages_out_total": "INTEGER",
    }),
    "host": ("host_snapshots", dict(HOST_COLUMNS)),
}


def get_connection(db_path: str) -> sqlite3.Connection:
    """Get a SQLite connection with row factory."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col, typ in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            logger.info("Migrated: added column %s.%s", table, col)


def _migrate(conn: sqlite3.Connection) -> None:
    """Create the per-kind tables and add any column an older database lacks."""
    _add_missing_columns(conn, "snapshots", HOST_COLUMNS)
    for table, columns in KIND_TABLES.values():
        column_defs = "".join(f", {col} {typ}" for col, typ in columns.items())
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table} ("
            f"id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL UNIQUE{column_defs})"
        )
        _add_missing_columns(conn, table, columns)


def init_db(db_path: str) -> None:
    """Create the database and schema (and migrate older databases if needed)."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()
    logger.info("Database initialised at %s", db_path)


def write_snapshot(
    db_path: str,
    timestamp: int,
    chain_tip: Optional[int],
    lib: Optional[str],
    mode: Optional[str],
    epoch: Optional[int],
    mempool_depth: int,
    peer_count: int,
    n_connections: int,
    wallet_balances: dict[str, Optional[int]],
) -> None:
    """Write a blockchain snapshot using INSERT OR REPLACE (upsert).

    If a snapshot for the same 10-min window already exists, it is replaced.
    """
    conn = get_connection(db_path)
    wallet_json = json.dumps(wallet_balances)
    conn.execute(
        """
        INSERT OR REPLACE INTO snapshots
            (timestamp, chain_tip, lib, mode, epoch, mempool_depth, peer_count, n_connections,
             wallet_balances)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp, chain_tip, lib, mode, epoch, mempool_depth, peer_count, n_connections,
         wallet_json),
    )
    conn.commit()
    conn.close()
    logger.debug("Snapshot written for timestamp %s", datetime.fromtimestamp(timestamp, tz=timezone.utc))


def write_kind_snapshot(db_path: str, kind: str, timestamp: int, data: dict) -> None:
    """Write a storage, delivery or host snapshot (upsert on the 10-min window).

    Keys of `data` that are not columns of the kind's table are ignored.
    """
    table, columns = KIND_TABLES[kind]
    names = list(columns)
    placeholders = ", ".join("?" for _ in range(len(names) + 1))
    conn = get_connection(db_path)
    conn.execute(
        f"INSERT OR REPLACE INTO {table} (timestamp, {', '.join(names)}) VALUES ({placeholders})",
        (timestamp, *(data.get(name) for name in names)),
    )
    conn.commit()
    conn.close()


def prune_old_snapshots(db_path: str, retention_days: int = 90) -> int:
    """Delete snapshots older than retention_days from every table.

    Returns the number of rows deleted.
    """
    cutoff = int(time.time()) - (retention_days * 24 * 60 * 60)
    conn = get_connection(db_path)
    deleted = 0
    for table in ["snapshots", *(t for t, _ in KIND_TABLES.values())]:
        deleted += conn.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,)).rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.info("Pruned %d snapshots older than %d days", deleted, retention_days)
    return deleted


def get_latest_snapshot(db_path: str) -> Optional[dict]:
    """Return the most recent blockchain snapshot, or None if the table is empty."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def get_snapshots_since(db_path: str, since_timestamp: int) -> list[dict]:
    """Return all blockchain snapshots since the given Unix timestamp."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE timestamp >= ? ORDER BY timestamp ASC",
        (since_timestamp,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_latest_kind_snapshot(db_path: str, kind: str) -> Optional[dict]:
    """Return the most recent snapshot of a kind, or None if there is none."""
    table, _ = KIND_TABLES[kind]
    conn = get_connection(db_path)
    row = conn.execute(f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row is not None else None


def get_kind_snapshots_since(db_path: str, kind: str, since_timestamp: int) -> list[dict]:
    """Return a kind's snapshots since the given Unix timestamp, oldest first.

    For the host, rows recorded in the blockchain table before host_snapshots
    existed are included, so older history is not lost.
    """
    table, _ = KIND_TABLES[kind]
    conn = get_connection(db_path)
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM {table} WHERE timestamp >= ? ORDER BY timestamp ASC",
        (since_timestamp,),
    )]
    if kind == "host":
        legacy = conn.execute(
            f"""
            SELECT timestamp, cpu_temp, cpu_pct, mem_pct, load1 FROM snapshots
            WHERE timestamp >= ? AND timestamp NOT IN (SELECT timestamp FROM {table})
              AND (cpu_temp IS NOT NULL OR cpu_pct IS NOT NULL
                   OR mem_pct IS NOT NULL OR load1 IS NOT NULL)
            """,
            (since_timestamp,),
        )
        rows = sorted(rows + [dict(r) for r in legacy], key=lambda r: r["timestamp"])
    conn.close()
    return rows
