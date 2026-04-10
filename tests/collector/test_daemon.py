"""Integration tests for collector daemon mode.

These tests verify:
1. Daemon stays running after start
2. SIGTERM stops cleanly (exit code 0)
3. SIGKILL does not erase already-committed DB writes
4. Duplicate 10-min windows are handled by INSERT OR REPLACE (one row, latest wins)
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    """Minimal config.yaml and temp DB for daemon tests."""
    db_path = str(tmp_path / "test.db")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("node:\n  axum_url: http://localhost:39999\n")
    return {"db_path": db_path, "config_path": str(config_path), "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# Test 1: Daemon starts and stays alive
# ---------------------------------------------------------------------------

def test_daemon_stays_running(env):
    """`python -m collector run --daemon` starts and the process remains alive."""
    # Use PYTHONPATH so collector is importable as a module
    env_vars = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "collector", "run", "--daemon",
         "--config", env["config_path"],
         "--db", env["db_path"]],
        cwd=str(PROJECT_ROOT),
        env=env_vars,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(3)
        assert proc.poll() is None, (
            f"Daemon exited prematurely with code {proc.returncode}. "
            f"stderr: {proc.stderr.read().decode(errors='replace')}"
        )
        # Signal 0 = existence check
        os.kill(proc.pid, 0)
    finally:
        proc.send_signal(signal.SIGTERM)
        # Allow up to 40s: 30s for the in-progress sleep + startup overhead
        try:
            proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# Test 2: SIGTERM stops cleanly (exit code 0)
# ---------------------------------------------------------------------------

def test_sigterm_stops_cleanly(env):
    """SIGTERM causes a graceful exit (code 0), not a crash."""
    env_vars = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "collector", "run", "--daemon",
         "--config", env["config_path"],
         "--db", env["db_path"]],
        cwd=str(PROJECT_ROOT),
        env=env_vars,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(2)
        assert proc.poll() is None, "Daemon should be running before SIGTERM"
        proc.send_signal(signal.SIGTERM)
        # Allow up to 40s: 30s for the in-progress sleep + startup overhead
        code = proc.wait(timeout=40)
        assert code == 0, (
            f"SIGTERM exit code should be 0, got {code}. "
            f"stderr: {proc.stderr.read().decode(errors='replace')}"
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail("Daemon did not exit within 40s of SIGTERM (kill + wait fallback used)")


# ---------------------------------------------------------------------------
# Test 3: SIGKILL does NOT erase already-committed DB writes
#
# Strategy: the daemon subprocess runs a thin wrapper that writes a snapshot
# then immediately kills itself with SIGKILL. We verify the snapshot survives.
# ---------------------------------------------------------------------------

@pytest.fixture
def kill_me_script(env, tmp_path):
    """Create a standalone script that writes a snapshot then SIGKILLs itself."""
    script_path = tmp_path / "killme.py"
    script_path.write_text(f"""
import sys, os, signal, time
sys.path.insert(0, "{PROJECT_ROOT}")

from collector.db import write_snapshot

now = int(time.time())
ts = (now // 600) * 600

# Write a distinct snapshot
write_snapshot(
    db_path="{env['db_path']}",
    timestamp=ts,
    chain_tip=9999,
    lib="sigkill_test_lib",
    mode="Normal",
    epoch=99,
    mempool_depth=42,
    peer_count=99,
    n_connections=9,
    wallet_balances={{"voucher": 999}},
)

# Now SIGKILL ourselves — this is the test: data must survive
os.kill(os.getpid(), signal.SIGKILL)
""")
    return str(script_path)


def test_sigkill_does_not_lose_durable_writes(env, kill_me_script):
    """Data committed before SIGKILL survives the ungraceful termination."""
    from collector.db import init_db, get_latest_snapshot

    init_db(env["db_path"])

    # Verify clean DB
    assert get_latest_snapshot(env["db_path"]) is None

    # Run the kill-me script
    result = subprocess.run(
        [sys.executable, kill_me_script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
    )
    # SIGKILL exits with -9 (128 + 9)
    assert result.returncode == -9, f"Expected -9, got {result.returncode}"

    # Data must survive SIGKILL — SQLite commits are durable
    snap = get_latest_snapshot(env["db_path"])
    assert snap is not None, "DB must contain a snapshot after SIGKILL"
    assert snap["chain_tip"] == 9999, f"Snapshot should survive SIGKILL, got {snap}"
    assert snap["wallet_balances"] == '{"voucher": 999}'


# ---------------------------------------------------------------------------
# Test 4: Duplicate 10-min windows → INSERT OR REPLACE (one row, latest wins)
# ---------------------------------------------------------------------------

def test_duplicate_10min_window_one_row(env):
    """Two writes in the same 10-min window produce exactly one DB row."""
    from collector.db import init_db, write_snapshot, get_snapshots_since

    init_db(env["db_path"])

    now = int(time.time())
    ts = (now // 600) * 600  # same window for both writes

    write_snapshot(env["db_path"], ts, 1000, "lib1", "Normal", 10, 5, 10, 2, {"a": 100})
    write_snapshot(env["db_path"], ts, 2000, "lib2", "Bootstrapping", 20, 15, 20, 4, {"a": 200})

    snaps = get_snapshots_since(env["db_path"], 0)
    window_snaps = [s for s in snaps if s["timestamp"] == ts]

    assert len(window_snaps) == 1, (
        f"Expected 1 row for 10-min window, got {len(window_snaps)}. "
        "INSERT OR REPLACE should prevent duplicates."
    )
    assert window_snaps[0]["chain_tip"] == 2000, "Latest write must win"
    assert window_snaps[0]["wallet_balances"] == '{"a": 200}'


def test_duplicate_window_through_collector_loop(env, monkeypatch):
    """Calling _collect_and_store twice with mocked APIs lands in one DB row."""
    from collector.db import init_db, get_snapshots_since
    from collector.main import _collect_and_store
    from collector.config import Config
    from collector.fetcher import FetchResult

    init_db(env["db_path"])
    config = Config(axum_url="http://localhost:38437", wallets=[], interval_minutes=10)

    result1 = FetchResult(chain_tip=1000, lib="l1", mode="Normal", epoch=5,
                          mempool_depth=1, peer_count=5, n_connections=1, wallet_balances={})
    result2 = FetchResult(chain_tip=1010, lib="l2", mode="Normal", epoch=6,
                          mempool_depth=2, peer_count=6, n_connections=2, wallet_balances={})

    import collector.main as main_mod
    call_count = 0

    def mock_fetch(url, wallets):
        nonlocal call_count
        call_count += 1
        return result1 if call_count == 1 else result2

    monkeypatch.setattr(main_mod, "fetch_all", mock_fetch)

    _collect_and_store(config, env["db_path"])
    _collect_and_store(config, env["db_path"])

    snaps = get_snapshots_since(env["db_path"], 0)
    assert len(snaps) == 1, (
        f"Expected 1 snapshot (INSERT OR REPLACE deduplicates same window), got {len(snaps)}"
    )
    assert snaps[0]["chain_tip"] == 1010, "Latest call should overwrite previous"
