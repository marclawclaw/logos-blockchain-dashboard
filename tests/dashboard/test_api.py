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


def _write(db_path, hours_ago, chain_tip, **kw):
    """Helper: write a snapshot hours_ago from now."""
    import collector.db as db_module
    now = int(time.time())
    ts = ((now - hours_ago * 3600) // 600) * 600
    db_module.write_snapshot(
        db_path=db_path,
        timestamp=ts,
        chain_tip=chain_tip,
        lib=f"hash{chain_tip}",
        mode="Normal",
        epoch=10,
        mempool_depth=1,
        peer_count=5,
        n_connections=2,
        wallet_balances={},
        **kw,
    )


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
    """/api/snapshots without hours param uses default 24-hour window."""
    resp = client.get("/api/snapshots")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 0


def test_history_returns_snapshots_since(client, fresh_db):
    """hours=1: only snapshots within the last hour are returned."""
    from dashboard.api import get_db_path
    db_path = get_db_path()

    # 2 hours ago — outside 1-hour window
    _write(db_path, hours_ago=2, chain_tip=1000)
    # now — within 1-hour window
    _write(db_path, hours_ago=0, chain_tip=1020)

    resp = client.get("/api/snapshots?hours=1")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 1
    assert data["snapshots"][0]["chain_tip"] == 1020


def test_history_returns_all_when_since_0(client, fresh_db):
    """/api/snapshots?hours=0 returns all snapshots."""
    from dashboard.api import get_db_path
    db_path = get_db_path()

    _write(db_path, hours_ago=1, chain_tip=1000)
    _write(db_path, hours_ago=0, chain_tip=1010)

    resp = client.get("/api/snapshots?hours=0")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 2


def test_snapshots_filter_by_24_hours(client, fresh_db):
    """hours=24: snapshots older than 24h excluded."""
    from dashboard.api import get_db_path
    db_path = get_db_path()

    # 23 hours ago — within window
    _write(db_path, hours_ago=23, chain_tip=1001)
    # 25 hours ago — outside window
    _write(db_path, hours_ago=25, chain_tip=1000)

    resp = client.get("/api/snapshots?hours=24")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 1
    assert data["snapshots"][0]["chain_tip"] == 1001


def test_snapshots_filter_by_1_week(client, fresh_db):
    """hours=168 (7 days): snapshots older than 7 days excluded."""
    from dashboard.api import get_db_path
    db_path = get_db_path()

    # 3 days ago — within 7-day window
    _write(db_path, hours_ago=3 * 24, chain_tip=2001)
    # 8 days ago — outside 7-day window
    _write(db_path, hours_ago=8 * 24, chain_tip=2000)

    resp = client.get("/api/snapshots?hours=168")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 1
    assert data["snapshots"][0]["chain_tip"] == 2001


def test_snapshots_filter_by_1_month(client, fresh_db):
    """hours=720 (30 days): snapshots older than 30 days excluded."""
    from dashboard.api import get_db_path
    db_path = get_db_path()

    # 10 days ago — within 30-day window
    _write(db_path, hours_ago=10 * 24, chain_tip=3001)
    # 35 days ago — outside 30-day window
    _write(db_path, hours_ago=35 * 24, chain_tip=3000)

    resp = client.get("/api/snapshots?hours=720")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 1
    assert data["snapshots"][0]["chain_tip"] == 3001


def test_snapshots_max_returns_all_via_hours_zero(client, fresh_db):
    """hours=0 returns all snapshots regardless of age (Max button)."""
    from dashboard.api import get_db_path
    db_path = get_db_path()

    # 60 days ago — within 90-day retention
    _write(db_path, hours_ago=60 * 24, chain_tip=4001)
    # 1 hour ago
    _write(db_path, hours_ago=1, chain_tip=4002)

    resp = client.get("/api/snapshots?hours=0")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 2


def test_snapshots_default_is_24_hours(client, fresh_db):
    """No hours param: API defaults to 24-hour window."""
    from dashboard.api import get_db_path
    db_path = get_db_path()

    # 12 hours ago — within default window
    _write(db_path, hours_ago=12, chain_tip=5001)
    # 30 hours ago — outside default window
    _write(db_path, hours_ago=30, chain_tip=5000)

    resp = client.get("/api/snapshots")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["count"] == 1
    assert data["snapshots"][0]["chain_tip"] == 5001


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
