"""Unit tests for the dashboard API layer."""

from __future__ import annotations

import json
import tempfile
import time
import pytest


@pytest.fixture
def app():
    """Create a test Flask app with an in-memory test DB."""
    from dashboard.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def fresh_db():
    """Fresh temporary SQLite DB registered as the global test db."""
    import collector.db as db_module
    import dashboard.api as api_module

    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)

    db_module.init_db(path)

    # Patch get_db_path to return our temp path
    import dashboard.api
    original_get_db_path = api_module.get_db_path

    def fake_get_db_path():
        return path

    api_module.get_db_path = fake_get_db_path

    yield path

    # Restore
    api_module.get_db_path = original_get_db_path
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# /api/snapshot/latest
# ---------------------------------------------------------------------------

def test_latest_returns_404_when_empty(client, fresh_db):
    """Empty DB: /api/snapshot/latest returns 404."""
    resp = client.get("/api/snapshot/latest")
    assert resp.status_code == 404
    data = json.loads(resp.data)
    assert "error" in data


def test_latest_returns_snapshot_row(client, fresh_db):
    """Populated DB: /api/snapshot/latest returns the most recent row."""
    import collector.db as db_module
    from dashboard.api import get_db_path

    now = int(time.time())
    ts = (now // 600) * 600

    db_module.write_snapshot(
        db_path=get_db_path(),
        timestamp=ts,
        chain_tip=5000,
        lib="abc123def",
        mode="Normal",
        epoch=100,
        mempool_depth=7,
        peer_count=15,
        n_connections=3,
        wallet_balances={"voucher": 9999},
    )

    resp = client.get("/api/snapshot/latest")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["chain_tip"] == 5000
    assert data["lib"] == "abc123def"
    assert data["mode"] == "Normal"
    assert data["mempool_depth"] == 7
    assert data["peer_count"] == 15
    wallet = json.loads(data["wallet_balances"])
    assert wallet["voucher"] == 9999


# ---------------------------------------------------------------------------
# /api/snapshots
# ---------------------------------------------------------------------------

def test_history_without_since_uses_default_window(client, fresh_db):
    """/api/snapshots without "since" param uses default 24-hour window."""
    resp = client.get("/api/snapshots")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 0


def test_history_returns_snapshots_since(client, fresh_db):
    """Correct time window returned for given hours=N."""
    import collector.db as db_module
    from dashboard.api import get_db_path

    now = int(time.time())

    # Snapshot 1: 2 hours ago
    ts1 = ((now - 2 * 3600) // 600) * 600
    db_module.write_snapshot(
        db_path=get_db_path(), timestamp=ts1, chain_tip=1000,
        lib="hash1", mode="Normal", epoch=10,
        mempool_depth=1, peer_count=5, n_connections=2, wallet_balances={},
    )

    # Snapshot 2: now
    ts2 = (now // 600) * 600
    db_module.write_snapshot(
        db_path=get_db_path(), timestamp=ts2, chain_tip=1020,
        lib="hash2", mode="Normal", epoch=11,
        mempool_depth=3, peer_count=8, n_connections=4, wallet_balances={},
    )

    resp = client.get("/api/snapshots?hours=1")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    # ts1 is outside the 1-hour window
    assert data["count"] == 1
    assert data["snapshots"][0]["chain_tip"] == 1020


def test_history_returns_all_when_since_0(client, fresh_db):
    """/api/snapshots?since=0 returns all snapshots."""
    import collector.db as db_module
    from dashboard.api import get_db_path

    now = int(time.time())
    ts1 = ((now - 3600) // 600) * 600
    ts2 = (now // 600) * 600

    for ts, tip in [(ts1, 1000), (ts2, 1010)]:
        db_module.write_snapshot(
            db_path=get_db_path(), timestamp=ts, chain_tip=tip,
            lib=f"hash{tip}", mode="Normal", epoch=10,
            mempool_depth=1, peer_count=5, n_connections=2, wallet_balances={},
        )

    resp = client.get("/api/snapshots?since=0")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 2


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

def test_health_returns_status_and_age(client, fresh_db):
    """/api/health returns status ok."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# Unknown route
# ---------------------------------------------------------------------------

def test_unknown_route_returns_404(client, fresh_db):
    """/api/unknown returns 404 status."""
    resp = client.get("/api/unknown")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# n_connections field
# ---------------------------------------------------------------------------

def test_latest_snapshot_includes_n_connections(client, fresh_db):
    """The n_connections field is returned in /api/snapshot/latest."""
    import collector.db as db_module
    from dashboard.api import get_db_path

    now = int(time.time())
    ts = (now // 600) * 600

    db_module.write_snapshot(
        db_path=get_db_path(),
        timestamp=ts,
        chain_tip=5000,
        lib="abc123def",
        mode="Normal",
        epoch=100,
        mempool_depth=7,
        peer_count=15,
        n_connections=7,
        wallet_balances={},
    )

    resp = client.get("/api/snapshot/latest")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "n_connections" in data
    assert data["n_connections"] == 7
