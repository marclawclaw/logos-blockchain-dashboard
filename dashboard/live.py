"""Current values for the dashboard: one cached reading per deployed node kind.

Reading every node takes several seconds (each logosctl call is a process), so
browsers never trigger a read directly. They get the cached reading, and a
stale cache is refreshed in the background, at most once per refresh period.
Nothing is read while nobody is looking at the dashboard.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from typing import Callable, Optional

from collector.config import Config
from collector.db import get_latest_kind_snapshot, get_latest_snapshot
from collector.fetcher import fetch_all
from collector.host import read_host_metrics
from collector.modules import Reading, read_modules

logger = logging.getLogger(__name__)


def read_blockchain(cfg: Config) -> Reading:
    """Read the blockchain node over its REST API."""
    wallets = [(w.name, w.address) for w in cfg.blockchain.wallets]
    result = fetch_all(cfg.blockchain.axum_url, wallets)
    if result.chain_tip is None:
        return Reading(False, {}, "blockchain node API unreachable")
    return Reading(True, asdict(result))


class LiveCache:
    def __init__(self, cfg: Config, db_path: Callable[[], str]):
        self._cfg = cfg
        self._db_path = db_path
        self._ttl = cfg.refresh_seconds
        self._lock = threading.Lock()
        self._refreshing = False
        self._refreshed_at = 0.0
        self._host: Optional[dict] = None
        # status: pending (never read) | up | down
        self._kinds = {
            kind: {"status": "pending", "last_ok_at": None, "error": None, "data": None}
            for kind in cfg.kinds
        }

    def get(self) -> dict:
        """Return the cached readings, starting a background refresh if they are stale."""
        with self._lock:
            stale = time.time() - self._refreshed_at >= self._ttl
            start = stale and not self._refreshing
            if start:
                self._refreshing = True
        if start:
            threading.Thread(target=self._refresh_guarded, daemon=True).start()
        return self._payload()

    def _refresh_guarded(self) -> None:
        try:
            self.refresh()
        except Exception:
            logger.exception("Live refresh failed")
        finally:
            with self._lock:
                self._refreshing = False

    def refresh(self) -> None:
        """Read every deployed kind and the host now (blocking)."""
        readings: dict[str, Reading] = {}
        if self._cfg.blockchain is not None:
            readings["blockchain"] = read_blockchain(self._cfg)
        readings.update(read_modules(self._cfg))
        host = asdict(read_host_metrics())

        now = int(time.time())
        with self._lock:
            self._host = host
            for kind, reading in readings.items():
                state = self._kinds[kind]
                if reading.ok:
                    state.update(status="up", last_ok_at=now, error=None, data=reading.data)
                else:
                    # Keep the last known values; only the status changes.
                    state.update(status="down", error=reading.error)
            self._refreshed_at = time.time()

    def _last_known(self, kind: str) -> tuple[Optional[dict], Optional[int]]:
        """The newest stored snapshot of a kind, for when it was never read live."""
        try:
            db_path = self._db_path()
            if kind == "blockchain":
                row = get_latest_snapshot(db_path)
                if row is not None:
                    row["wallet_balances"] = json.loads(row.get("wallet_balances") or "{}")
            else:
                row = get_latest_kind_snapshot(db_path, kind)
        except Exception as e:
            logger.warning("Could not load last known %s snapshot: %s", kind, e)
            return None, None
        if row is None:
            return None, None
        return row, row.get("timestamp")

    def _payload(self) -> dict:
        with self._lock:
            kinds = {kind: dict(state) for kind, state in self._kinds.items()}
            host = self._host
            refreshed_at = int(self._refreshed_at) or None
        for kind, state in kinds.items():
            if state["data"] is None:
                state["data"], state["last_ok_at"] = self._last_known(kind)
        return {
            "refresh_seconds": self._ttl,
            "refreshed_at": refreshed_at,
            "kinds": kinds,
            "host": host,
        }
