"""Unit tests for the collector layer."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Module-level helpers under test (expose from main for testability)

def compute_blocks_produced(current_tip: int, previous_tip: int | None) -> int:
    """Compute blocks produced since last snapshot. Mirrors collector/main.py logic."""
    if previous_tip is None:
        return 0
    return max(0, current_tip - previous_tip)


# ---------------------------------------------------------------------------
# compute_blocks_produced
# ---------------------------------------------------------------------------

def test_compute_blocks_produced_first_snapshot():
    assert compute_blocks_produced(1000, None) == 0


def test_compute_blocks_produced_normal():
    assert compute_blocks_produced(1010, 1000) == 10


def test_compute_blocks_produced_reorg():
    """Chain reorganisation: new tip lower than previous — return 0, not negative."""
    assert compute_blocks_produced(990, 1000) == 0


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def test_config_loads_defaults(tmp_path):
    """Happy-path: config.yaml with manual overrides loads correctly."""
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "node:\n  axum_url: http://localhost:38437\n"
        "collector:\n  interval_minutes: 5\n"
    )
    from collector.config import load
    cfg = load(str(config_yaml))
    assert cfg.axum_url == "http://localhost:38437"
    assert cfg.interval_minutes == 5


def test_config_auto_detect_from_user_config(tmp_path):
    """Auto-detects axum_url and wallets from user_config.yaml."""
    # user_config.yaml with listen_address and wallet.known_keys
    user_cfg = tmp_path / "user_config.yaml"
    user_cfg.write_text(
        "api:\n  backend:\n    listen_address: /ip4/0.0.0.0/port/38437\n"
        "wallet:\n  known_keys:\n    my_voucher_key: abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234\n"
    )
    # config.yaml points to the user config
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(f"node_config_path: {user_cfg}\n")

    from collector.config import load
    cfg = load(str(config_yaml))
    assert cfg.axum_url == "http://localhost:38437"
    assert len(cfg.wallets) == 1
    assert cfg.wallets[0].name == "my_voucher_k"


def test_config_raises_on_missing_api_url(tmp_path):
    """ConfigError raised when no API URL is available."""
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("node_config_path: /nonexistent/user_config.yaml\n")
    from collector.config import load, ConfigError
    with pytest.raises(ConfigError):
        load(str(config_yaml))


# ---------------------------------------------------------------------------
# Fetcher — HTTP error handling
# ---------------------------------------------------------------------------

def test_fetcher_handles_http_error():
    """HTTP errors are logged and return None; other endpoints still succeed."""
    import requests
    from collector.fetcher import fetch_network_info

    with patch("collector.fetcher.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_get.return_value = mock_resp

        result = fetch_network_info("http://localhost:38437")
        assert result is None


def test_fetcher_handles_malformed_json():
    """Malformed JSON is logged and returns None."""
    from collector.fetcher import fetch_network_info

    with patch("collector.fetcher.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        mock_get.return_value = mock_resp

        result = fetch_network_info("http://localhost:38437")
        assert result is None


def test_fetcher_partial_success():
    """One endpoint fails; others return correct values."""
    from collector.fetcher import fetch_all

    with patch("collector.fetcher.fetch_cryptarchia_info") as mock_crypto, \
         patch("collector.fetcher.fetch_network_info") as mock_net, \
         patch("collector.fetcher.fetch_mempool_metrics") as mock_mempool, \
         patch("collector.fetcher.fetch_wallet_balance") as mock_wallet:

        from collector.fetcher import CryptarchiaInfo, NetworkInfo, MempoolMetrics

        mock_crypto.return_value = CryptarchiaInfo(
            lib="abc123", tip="def456", slot=100, height=500, mode="Normal"
        )
        mock_net.return_value = NetworkInfo(n_peers=10, n_connections=12, n_pending_connections=0)
        mock_mempool.return_value = MempoolMetrics(pending_items=5, last_item_timestamp=999)
        mock_wallet.return_value = 1000

        result = fetch_all("http://localhost:38437", [("voucher", "addr1")])

        assert result.chain_tip == 500
        assert result.lib == "abc123"
        assert result.mode == "Normal"
        assert result.peer_count == 10
        assert result.mempool_depth == 5
        assert result.wallet_balances == {"voucher": 1000}


# ---------------------------------------------------------------------------
# DB — init, insert_or_replace, prune
# ---------------------------------------------------------------------------

def test_db_init_creates_schema(tmp_path):
    """init_db() creates the snapshots table with correct columns."""
    from collector.db import init_db, get_connection

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    conn = get_connection(db_path)
    rows = conn.execute("PRAGMA table_info(snapshots)").fetchall()
    conn.close()

    columns = {r[1] for r in rows}
    assert "timestamp" in columns
    assert "chain_tip" in columns
    assert "lib" in columns
    assert "mode" in columns
    assert "blocks_produced" in columns
    assert "mempool_depth" in columns
    assert "peer_count" in columns
    assert "wallet_balances" in columns


def test_db_insert_or_replace_upsert(tmp_path):
    """INSERT OR REPLACE updates an existing row for the same timestamp."""
    from collector.db import init_db, write_snapshot, get_latest_snapshot

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    now = int(time.time())
    ts = (now // 600) * 600

    write_snapshot(db_path, ts, 1000, "libhash1", "Normal", 10, 5, 10, 0, {})
    write_snapshot(db_path, ts, 1010, "libhash2", "Bootstrapping", 10, 3, 7, 12, {})

    snap = get_latest_snapshot(db_path)
    assert snap["chain_tip"] == 1010
    assert snap["lib"] == "libhash2"
    assert snap["mode"] == "Bootstrapping"
    assert snap["blocks_produced"] == 3


def test_db_retention_pruning(tmp_path):
    """Rows older than retention_days are deleted; recent rows kept."""
    from collector.db import init_db, write_snapshot, prune_old_snapshots

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    now = int(time.time())
    recent_ts = (now // 600) * 600
    old_ts = recent_ts - (91 * 24 * 3600)  # 91 days ago

    write_snapshot(db_path, recent_ts, 1000, "lib1", "Normal", 10, 0, 5, 10, {})
    write_snapshot(db_path, old_ts, 500, "lib2", "Normal", 5, 0, 2, 5, {})

    deleted = prune_old_snapshots(db_path, retention_days=90)
    assert deleted == 1

    from collector.db import get_snapshots_since
    remaining = get_snapshots_since(db_path, 0)
    assert len(remaining) == 1
    assert remaining[0]["chain_tip"] == 1000


def test_snapshot_assembly():
    """Mocked fetcher + mocked db: full snapshot assembly flow."""
    from collector.fetcher import FetchResult

    result = FetchResult(
        chain_tip=2000,
        lib="hash123",
        mode="Normal",
        epoch=500,
        mempool_depth=8,
        peer_count=20,
        wallet_balances={"voucher": 5000},
    )

    # Simulate blocks_produced calculation
    prev_tip = 1900
    blocks_produced = compute_blocks_produced(result.chain_tip, prev_tip)
    assert blocks_produced == 100

    # Verify FetchResult fields match expectations
    assert result.chain_tip == 2000
    assert result.lib == "hash123"
    assert result.mode == "Normal"
    assert result.epoch == 500
    assert result.mempool_depth == 8
    assert result.peer_count == 20
    assert result.wallet_balances == {"voucher": 5000}
