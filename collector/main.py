"""Collector entry point: CLI and 10-minute cron loop."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from .config import load, Config
from .db import init_db, write_snapshot, prune_old_snapshots, get_latest_snapshot
from .fetcher import fetch_all

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Logos Node Observer Collector")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init-db", help="Initialise the SQLite database")
    init_p.add_argument("--db", default="data/snapshots.db", help="Path to SQLite DB")

    run_p = sub.add_parser("run", help="Run the collector loop")
    run_p.add_argument("--daemon", action="store_true", help="Run as background daemon")
    run_p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    run_p.add_argument("--db", default="data/snapshots.db", help="Path to SQLite DB")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.command == "init-db":
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        init_db(args.db)
        print(f"Database initialised at {args.db}")

    elif args.command == "run":
        config = load(args.config)
        _run(config, args.db, daemon=args.daemon)


def _run(config: Config, db_path: str, daemon: bool = False) -> None:
    interval_secs = config.interval_minutes * 60
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Init DB on first run
    init_db(db_path)

    # Prune old snapshots on startup
    prune_old_snapshots(db_path)

    # Handle shutdown gracefully
    running = True

    def shutdown(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received — finishing current interval...")
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info(
        "Collector starting — polling every %d min, DB at %s",
        config.interval_minutes,
        db_path,
    )

    if daemon:
        logger.info("Running as daemon (background)")

    while running:
        _collect_and_store(config, db_path)

        if not running:
            break

        elapsed = 0
        while elapsed < interval_secs and running:
            time.sleep(min(30, interval_secs - elapsed))
            elapsed += 30

    logger.info("Collector stopped.")


def _collect_and_store(config: Config, db_path: str) -> None:
    """Fetch metrics, compute delta, and write snapshot."""
    wallet_tuples = [(w.name, w.address) for w in config.wallets]
    result = fetch_all(config.axum_url, wallet_tuples)

    # Compute blocks_produced delta
    latest = get_latest_snapshot(db_path)
    prev_tip = latest["chain_tip"] if latest else None

    if prev_tip is not None and result.chain_tip is not None:
        blocks_produced = max(0, result.chain_tip - prev_tip)
    else:
        blocks_produced = 0

    # Truncate to 10-min window
    now = int(time.time())
    timestamp = (now // 600) * 600

    write_snapshot(
        db_path=db_path,
        timestamp=timestamp,
        chain_tip=result.chain_tip,
        lib=result.lib,
        mode=result.mode,
        epoch=result.epoch,
        blocks_produced=blocks_produced,
        mempool_depth=result.mempool_depth,
        peer_count=result.peer_count,
        n_connections=result.n_connections,
        wallet_balances=result.wallet_balances,
    )

    # Prune after write
    prune_old_snapshots(db_path)

    logger.info(
        "Snapshot stored: tip=%s, peers=%s, mempool=%s, wallets=%s",
        result.chain_tip,
        result.peer_count,
        result.mempool_depth,
        result.wallet_balances,
    )


if __name__ == "__main__":
    main()
