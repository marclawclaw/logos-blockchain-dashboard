"""Collector entry point: CLI and 10-minute cron loop."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .config import load, default_config_path, Config
from .db import init_db, write_snapshot, write_kind_snapshot, prune_old_snapshots
from .fetcher import fetch_all
from .host import read_host_metrics
from .modules import read_modules

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
    run_p.add_argument("--once", action="store_true", help="Run one collection and exit")
    run_p.add_argument("--config", default=None,
                       help="Path to config (default: config.local.yaml if present, else config.yaml)")
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
        project_root = Path(__file__).parent.parent
        config = load(args.config or str(default_config_path(project_root)))
        _run(config, args.db, daemon=args.daemon, once=args.once)


def _run(config: Config, db_path: str, daemon: bool = False, once: bool = False) -> None:
    interval_secs = config.interval_minutes * 60
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Init DB on first run
    init_db(db_path)

    # Prune old snapshots on startup
    prune_old_snapshots(db_path)

    if once:
        logger.info("Collector running once — DB at %s", db_path)
        _collect_and_store(config, db_path)
        return

    # Handle shutdown gracefully
    running = True

    def shutdown(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received — finishing current interval...")
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info(
        "Collector starting — polling %s every %d min, DB at %s",
        ", ".join(config.kinds) or "the host only (no node kind configured)",
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
    """Read every deployed node kind plus the host, and write one snapshot each.

    Kinds are independent: a kind that cannot be read is skipped for this
    interval (its history shows a gap) without affecting the others. Host
    metrics are written every interval, whatever the nodes do.
    """
    # Truncate to 10-min window
    now = int(time.time())
    timestamp = (now // 600) * 600

    host = read_host_metrics()
    write_kind_snapshot(db_path, "host", timestamp, asdict(host))

    if config.blockchain is not None:
        _collect_blockchain(config, db_path, timestamp)

    for kind, reading in read_modules(config).items():
        if not reading.ok:
            logger.warning("%s: %s — skipping snapshot for this interval", kind, reading.error)
            continue
        write_kind_snapshot(db_path, kind, timestamp, reading.data)
        logger.info(
            "%s snapshot stored: peers=%s, memory=%sMB",
            kind, reading.data.get("peers"), reading.data.get("memory_mb"),
        )

    # Prune after write
    prune_old_snapshots(db_path)

    logger.info(
        "Host snapshot stored: temp=%s°C, cpu=%s%%, mem=%s%%",
        host.cpu_temp, host.cpu_pct, host.mem_pct,
    )


def _collect_blockchain(config: Config, db_path: str, timestamp: int) -> None:
    """Write a blockchain snapshot, unless every API endpoint failed."""
    wallet_tuples = [(w.name, w.address) for w in config.blockchain.wallets]
    result = fetch_all(config.blockchain.axum_url, wallet_tuples)

    # If all APIs failed, skip this interval entirely
    if result.chain_tip is None:
        logger.warning("blockchain: all APIs failed — skipping snapshot for this interval")
        return

    write_snapshot(
        db_path=db_path,
        timestamp=timestamp,
        chain_tip=result.chain_tip,
        lib=result.lib,
        mode=result.mode,
        epoch=result.epoch,
        mempool_depth=result.mempool_depth,
        peer_count=result.peer_count,
        n_connections=result.n_connections,
        wallet_balances=result.wallet_balances,
    )

    logger.info(
        "blockchain snapshot stored: tip=%s, peers=%s, mempool=%s, wallets=%s",
        result.chain_tip,
        result.peer_count,
        result.mempool_depth,
        result.wallet_balances,
    )


if __name__ == "__main__":
    main()
