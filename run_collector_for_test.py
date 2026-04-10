#!/usr/bin/env python
"""Wrapper for running the collector with a patched fetch_all in daemon tests.

Usage:
    python run_collector_for_test.py --config config.yaml --db data.db [--daemon]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Patch fetch_all BEFORE importing collector modules
class _MockResult:
    chain_tip = 99999
    lib = "test_lib_hash"
    mode = "Normal"
    epoch = 1
    mempool_depth = 7
    peer_count = 13
    n_connections = 5
    wallet_balances = {"test_wallet": 111111}

def _mock_fetch_all(url, wallets):
    return _MockResult()

import collector.fetcher as _fm
_fm.fetch_all = _mock_fetch_all

import collector.main as _mm
_mm.fetch_all = _mock_fetch_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Logos Collector Test Wrapper")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--db", default="data/snapshots.db")
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from collector.config import load
    from collector.main import _run

    config = load(args.config)
    _run(config, args.db, daemon=args.daemon)


if __name__ == "__main__":
    main()
