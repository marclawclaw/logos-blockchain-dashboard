#!/usr/bin/env python
"""Wrapper for running the collector with a patched fetch_all in daemon tests.

Usage:
    python run_collector_for_test.py --config config.yaml --db data.db [--daemon]

This applies a mock fetch_all that returns successful data immediately,
bypassing real network I/O. The mock writes a realistic FetchResult so the
collector can be tested end-to-end without a live Logos node.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on the path
_root = Path(__file__).parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Apply the patch BEFORE importing collector modules
from unittest.mock import MagicMock
from collector.fetcher import FetchResult


def mock_fetch_all(url, wallets):
    """Return a successful FetchResult — collector will write a snapshot."""
    return FetchResult(
        chain_tip=99999,
        lib="test_lib_hash",
        mode="Normal",
        epoch=1,
        mempool_depth=7,
        peer_count=13,
        n_connections=5,
        wallet_balances={"test_wallet": 111111},
    )


# Patch at the module level before collector.main imports it
import collector.fetcher as fetcher_mod
fetcher_mod.fetch_all = mock_fetch_all

# Also patch the name that collector.main looks up at call time
import collector.main as main_mod
main_mod.fetch_all = mock_fetch_all

if __name__ == "__main__":
    from collector.main import main
    main()
