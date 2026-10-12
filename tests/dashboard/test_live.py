"""Tests for the dashboard's current values: the live cache, /api/live, /api/history, the page."""

from __future__ import annotations

import json
import os
import tempfile
import time
from unittest.mock import patch

import pytest

from collector.config import BlockchainConfig, Config, ModuleConfig, Wallet
from collector.fetcher import FetchResult
from collector.modules import Reading


@pytest.fixture
def db_path():
    import collector.db as db_module
    import dashboard.api as api_module

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_module.init_db(path)
    original = api_module.get_db_path
    api_module.get_db_path = lambda: path
    yield path
    api_module.get_db_path = original
    os.unlink(path)


def _app(cfg):
    from dashboard.app import create_app
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


ALL_KINDS = Config(
    blockchain=BlockchainConfig(
        axum_url="http://localhost:38437", wallets=[Wallet("voucher", "aa11")]),
    storage=ModuleConfig("storage_module"),
    delivery=ModuleConfig("delivery_module"),
)

CHAIN_OK = FetchResult(chain_tip=500, lib="abc", mode="Normal", epoch=7, mempool_depth=2,
                       mempool_last_ms=1700000000000, peer_count=9, n_connections=11,
                       wallet_balances={"voucher": 1000})


def _refresh(app, chain=CHAIN_OK, modules=None):
    """Run one blocking refresh of the live cache with the given node answers."""
    if modules is None:
        modules = {"storage": Reading(True, {"peers": 3, "manifests": 2}),
                   "delivery": Reading(True, {"peers": 32, "connection_status": "Connected"})}
    with patch("dashboard.live.fetch_all", return_value=chain), \
         patch("dashboard.live.read_modules", return_value=modules):
        app.config["LIVE"].refresh()


def _live(app):
    """GET /api/live without letting it start a background refresh."""
    with patch("dashboard.live.threading.Thread"):
        return json.loads(app.test_client().get("/api/live").data)


# ---------------------------------------------------------------------------
# /api/live
# ---------------------------------------------------------------------------

def test_live_before_first_reading_is_pending(db_path):
    live = _live(_app(ALL_KINDS))
    assert live["refresh_seconds"] == 30
    assert live["refreshed_at"] is None
    assert set(live["kinds"]) == {"blockchain", "storage", "delivery"}
    for state in live["kinds"].values():
        assert state == {"status": "pending", "last_ok_at": None, "error": None, "data": None}


def test_live_lists_only_deployed_kinds(db_path):
    live = _live(_app(Config(storage=ModuleConfig("storage_module"))))
    assert set(live["kinds"]) == {"storage"}
    assert _live(_app(Config()))["kinds"] == {}


def test_live_after_reading(db_path):
    app = _app(ALL_KINDS)
    _refresh(app)
    live = _live(app)
    assert live["kinds"]["blockchain"]["status"] == "up"
    assert live["kinds"]["blockchain"]["data"]["chain_tip"] == 500
    assert live["kinds"]["blockchain"]["data"]["wallet_balances"] == {"voucher": 1000}
    assert live["kinds"]["storage"]["data"] == {"peers": 3, "manifests": 2}
    assert live["kinds"]["delivery"]["status"] == "up"
    assert set(live["host"]) == {"cpu_temp", "cpu_pct", "mem_pct", "load1"}
    assert live["refreshed_at"] is not None


def test_node_that_stops_answering_keeps_last_known_values(db_path):
    app = _app(ALL_KINDS)
    _refresh(app)
    seen = _live(app)["kinds"]["storage"]["last_ok_at"]

    _refresh(app, modules={
        "storage": Reading(False, {"module_status": "crashed"}, "module storage_module is crashed"),
        "delivery": Reading(True, {"peers": 30}),
    })
    live = _live(app)
    storage = live["kinds"]["storage"]
    assert storage["status"] == "down"
    assert storage["error"] == "module storage_module is crashed"
    assert storage["data"] == {"peers": 3, "manifests": 2}
    assert storage["last_ok_at"] == seen
    # The other kinds are unaffected.
    assert live["kinds"]["delivery"]["status"] == "up"
    assert live["kinds"]["delivery"]["data"] == {"peers": 30}
    assert live["kinds"]["blockchain"]["status"] == "up"


def test_node_never_seen_live_falls_back_to_stored_snapshot(db_path):
    """After a dashboard restart, a down node still shows what the collector last stored."""
    from collector.db import write_kind_snapshot, write_snapshot
    write_kind_snapshot(db_path, "storage", 1200, {"manifests": 5, "peers": 4})
    write_snapshot(db_path, 1800, 900, "lib", "Normal", 3, 1, 8, 8, {"voucher": 42})

    app = _app(ALL_KINDS)
    _refresh(app, chain=FetchResult(), modules={
        "storage": Reading(False, {}, "Logos Core daemon unreachable (NO_DAEMON: )"),
        "delivery": Reading(False, {}, "Logos Core daemon unreachable (NO_DAEMON: )"),
    })
    live = _live(app)

    storage = live["kinds"]["storage"]
    assert storage["status"] == "down"
    assert storage["data"]["manifests"] == 5
    assert storage["last_ok_at"] == 1200

    chain = live["kinds"]["blockchain"]
    assert chain["status"] == "down"
    assert chain["data"]["chain_tip"] == 900
    assert chain["data"]["wallet_balances"] == {"voucher": 42}

    # Never stored, never seen: nothing to show.
    assert live["kinds"]["delivery"]["data"] is None
    assert live["kinds"]["delivery"]["last_ok_at"] is None


def test_live_reads_nodes_at_most_once_per_refresh_period(db_path):
    app = _app(ALL_KINDS)
    _refresh(app)
    with patch("dashboard.live.threading.Thread") as thread:
        app.test_client().get("/api/live")
        assert not thread.called                      # fresh: served from the cache
        app.config["LIVE"]._refreshed_at = time.time() - 31
        app.test_client().get("/api/live")
        app.test_client().get("/api/live")
        assert thread.call_count == 1                 # stale: one refresh, not one per request


# ---------------------------------------------------------------------------
# /api/history/<kind>
# ---------------------------------------------------------------------------

def test_history_returns_kind_rows_in_window(db_path):
    from collector.db import write_kind_snapshot
    now = (int(time.time()) // 600) * 600
    write_kind_snapshot(db_path, "delivery", now - 30 * 3600, {"peers": 1})
    write_kind_snapshot(db_path, "delivery", now, {"peers": 2})

    client = _app(Config()).test_client()
    day = json.loads(client.get("/api/history/delivery?hours=24").data)
    assert day["count"] == 1 and day["snapshots"][0]["peers"] == 2
    assert json.loads(client.get("/api/history/delivery?hours=0").data)["count"] == 2
    assert json.loads(client.get("/api/history/host").data)["count"] == 0


def test_history_unknown_kind_is_404(db_path):
    assert _app(Config()).test_client().get("/api/history/blockchain").status_code == 404


# ---------------------------------------------------------------------------
# Page and blockchain-only routes
# ---------------------------------------------------------------------------

def test_page_shows_only_deployed_kinds(db_path):
    html = _app(Config(storage=ModuleConfig("storage_module"))).test_client().get("/").data.decode()
    assert 'id="section-storage"' in html
    assert 'id="section-host"' in html
    assert 'id="section-blockchain"' not in html
    assert 'id="section-delivery"' not in html
    assert 'const KINDS = ["storage"]' in html


def test_page_with_no_kind_shows_host_only(db_path):
    html = _app(Config()).test_client().get("/").data.decode()
    assert "No node kind configured" in html
    assert 'id="section-host"' in html
    assert 'class="kind-section" id="section-storage"' not in html


def test_page_wallet_cards_come_from_config(db_path):
    html = _app(ALL_KINDS).test_client().get("/").data.decode()
    assert 'data-wallet="voucher"' in html
    assert 'const WALLET_NAMES = ["voucher"]' in html


def test_blockchain_routes_absent_without_blockchain_node(db_path):
    client = _app(Config(storage=ModuleConfig("storage_module"))).test_client()
    assert client.get("/api/blend/status").status_code == 404
    assert client.get("/api/proxy/cryptarchia/info").status_code == 404
