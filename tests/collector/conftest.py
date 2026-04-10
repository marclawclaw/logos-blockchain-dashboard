"""conftest: shared fixtures and subprocess patches for daemon tests."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Patch fetch_all for all daemon-mode subprocess tests
#
# collector.main imports fetch_all at the top level. By patching
# collector.fetcher.fetch_all (the actual module-level name used in main.py)
# before the subprocess imports it, we make the collector write snapshots
# instantly without waiting for real network I/O.
# ---------------------------------------------------------------------------

def _mock_fetch_all(url, wallets):
    """Return a successful FetchResult for daemon-mode tests."""
    from collector.fetcher import FetchResult
    return FetchResult(
        chain_tip=99999,
        lib="daemon_test_lib",
        mode="Normal",
        epoch=1,
        mempool_depth=7,
        peer_count=13,
        n_connections=5,
        wallet_balances={"daemon_test_wallet": 111111},
    )


@pytest.fixture(autouse=True)
def patch_fetch_all_for_subprocess(monkeypatch):
    """Patch collector.fetcher.fetch_all so subprocess collector writes instantly."""
    import collector.fetcher as fetcher_module
    monkeypatch.setattr(fetcher_module, "fetch_all", _mock_fetch_all)
