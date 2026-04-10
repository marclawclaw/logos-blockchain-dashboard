"""SQLite database: schema, snapshots, retention pruning."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL UNIQUE,  -- Unix epoch (UTC), truncated to 10-min window
    chain_tip INTEGER,
    lib TEXT,                            -- Last Irreversible Block hash (string)
    mode TEXT,                           -- Node mode: "Bootstrapping", "Normal", etc.
    epoch INTEGER,
    blocks_produced INTEGER DEFAULT 0,
    mempool_depth INTEGER DEFAULT 0,
    peer_count INTEGER DEFAULT 0,
    wallet_balances TEXT NOT NULL  -- JSON: {"wallet_name": balance}
);

CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """Get a SQLite connection with row factory."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Create the database and schema."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
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
    blocks_produced: int,
    mempool_depth: int,
    peer_count: int,
    wallet_balances: dict[str, Optional[int]],
) -> None:
    """Write a snapshot using INSERT OR REPLACE (upsert).

    If a snapshot for the same 10-min window already exists, it is replaced.
    """
    conn = get_connection(db_path)
    wallet_json = json.dumps(wallet_balances)
    conn.execute(
        """
        INSERT OR REPLACE INTO snapshots
            (timestamp, chain_tip, lib, mode, epoch, blocks_produced, mempool_depth, peer_count, wallet_balances)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp, chain_tip, lib, mode, epoch, blocks_produced, mempool_depth, peer_count, wallet_json),
    )
    conn.commit()
    conn.close()
    logger.debug("Snapshot written for timestamp %s", datetime.fromtimestamp(timestamp, tz=timezone.utc))


def prune_old_snapshots(db_path: str, retention_days: int = 90) -> int:
    """Delete snapshots older than retention_days.

    Returns the number of rows deleted.
    """
    cutoff = int(time.time()) - (retention_days * 24 * 60 * 60)
    conn = get_connection(db_path)
    cursor = conn.execute(
        "DELETE FROM snapshots WHERE timestamp < ?",
        (cutoff,),
    )
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    if deleted > 0:
        logger.info("Pruned %d snapshots older than %d days", deleted, retention_days)
    return deleted


def get_latest_snapshot(db_path: str) -> Optional[dict]:
    """Return the most recent snapshot, or None if the table is empty."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def get_snapshots_since(db_path: str, since_timestamp: int) -> list[dict]:
    """Return all snapshots since the given Unix timestamp."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE timestamp >= ? ORDER BY timestamp ASC",
        (since_timestamp,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
