"""Daemon-mode tests: --daemon flag, SIGTERM clean stop, SIGKILL no data loss, INSERT OR REPLACE deduplication."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def config_file(tmp_path):
    """Minimal config pointing at an unreachable URL."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("node:\n  axum_url: http://localhost:39999\n")
    return str(cfg)


@pytest.fixture
def project_root():
    return str(Path(__file__).parent.parent.parent)


@pytest.fixture
def wrapper_script(project_root):
    """Path to the test wrapper that patches fetch_all for instant snapshots."""
    return str(Path(project_root) / "run_collector_for_test.py")


# ---------------------------------------------------------------------------
# Test: --daemon flag is accepted by the CLI (no argparse error)
# ---------------------------------------------------------------------------

def test_daemon_flag_accepted_by_cli(project_root, config_file, temp_db):
    """The --daemon flag must not cause an argparse error."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "collector", "run",
             "--config", config_file, "--db", temp_db, "--daemon"],
            cwd=project_root,
            capture_output=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired as e:
        # Timeout is expected — collector runs forever. Check no argparse error.
        combined = (e.stdout or b"") + (e.stderr or b"")
    else:
        combined = result.stdout + result.stderr
    combined_str = combined.decode(errors="replace")
    assert "unrecognized arguments" not in combined_str
    assert "error: argument" not in combined_str


def test_daemon_flag_logs_daemon_message(project_root, config_file, temp_db):
    """With --daemon, the collector logs 'Running as daemon'."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "collector", "run",
         "--config", config_file, "--db", temp_db, "--daemon"],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    time.sleep(3)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    stdout, _ = proc.communicate()
    combined = stdout.decode()
    assert "daemon" in combined.lower(), (
        f"Expected 'daemon' in output when --daemon flag is used, got:\n{combined[:500]}"
    )


# ---------------------------------------------------------------------------
# Test: SIGTERM clean stop
#
# Uses the wrapper script so fetch_all returns instantly and the collector
# writes a snapshot before we send SIGTERM. This lets us verify:
#   1. SIGTERM is handled and the collector exits cleanly (code 0)
#   2. Pre-written data is preserved in the DB
#   3. Collector-written snapshot is also preserved
# ---------------------------------------------------------------------------

def test_sigterm_clean_stop_preserves_data(tmp_path, config_file, temp_db,
                                           project_root, wrapper_script):
    """Sending SIGTERM to the collector subprocess stops it gracefully."""
    from collector.db import init_db, write_snapshot

    init_db(temp_db)

    # Pre-write a known snapshot
    now = int(time.time())
    ts0 = (now // 600) * 600 - 600
    write_snapshot(
        db_path=temp_db,
        timestamp=ts0,
        chain_tip=5000,
        lib="pre_sigterm_lib",
        mode="Normal",
        epoch=10,
        mempool_depth=3,
        peer_count=7,
        n_connections=2,
        wallet_balances={},
    )

    # Use the wrapper script so fetch_all is pre-patched
    proc = subprocess.Popen(
        [sys.executable, wrapper_script,
         "--config", config_file,
         "--db", temp_db,
         "--daemon"],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    time.sleep(3)

    # Send SIGTERM to the collector subprocess (not to the test process)
    os.kill(proc.pid, signal.SIGTERM)

    try:
        stdout, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise

    combined = (stdout or b"").decode(errors="replace")

    # Should see shutdown message
    assert "Shutdown signal received" in combined or "Collector stopped" in combined, (
        f"Expected clean shutdown message, got:\n{combined[:500]}"
    )

    # Exit code 0 = clean exit
    assert proc.returncode == 0, (
        f"Collector exited with code {proc.returncode}, expected 0"
    )

    # All data must be preserved
    conn = sqlite3.connect(temp_db)
    count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    lib_val = conn.execute(
        "SELECT lib FROM snapshots WHERE timestamp = ?", (ts0,)
    ).fetchone()[0]
    conn.close()

    assert count >= 2, f"Expected at least 2 snapshots (pre-written + collector), got {count}"
    assert lib_val == "pre_sigterm_lib"


def test_sigterm_shutdown_logs_clean_message(tmp_path, config_file, temp_db,
                                              project_root, wrapper_script):
    """SIGTERM causes the collector to log 'Shutdown signal received'."""
    from collector.db import init_db
    init_db(temp_db)

    proc = subprocess.Popen(
        [sys.executable, wrapper_script,
         "--config", config_file,
         "--db", temp_db],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    time.sleep(3)

    os.kill(proc.pid, signal.SIGTERM)

    try:
        stdout, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise

    combined = (stdout or b"").decode(errors="replace")
    assert "Shutdown signal received" in combined or "Collector stopped" in combined, (
        f"Expected 'Shutdown signal received' in logs after SIGTERM, got:\n{combined[:500]}"
    )


# ---------------------------------------------------------------------------
# Test: SIGKILL no data loss
#
# We write known snapshots first, then SIGKILL the collector subprocess,
# then verify the DB is intact. SQLite's journaling guarantees committed
# data survives a hard kill.
# ---------------------------------------------------------------------------

def test_sigkill_db_remains_intact_after_hard_kill(tmp_path, config_file, temp_db,
                                                    project_root, wrapper_script):
    """SIGKILL does not corrupt the DB; committed data and schema are readable."""
    from collector.db import init_db, write_snapshot

    init_db(temp_db)

    now = int(time.time())
    snapshots = []
    for i in range(3):
        ts = (now // 600) * 600 - (2 - i) * 600
        write_snapshot(
            db_path=temp_db,
            timestamp=ts,
            chain_tip=10000 + i * 100,
            lib=f"lib_{i}",
            mode="Normal",
            epoch=50 + i,
            mempool_depth=5 + i,
            peer_count=10 + i,
            n_connections=3 + i,
            wallet_balances={f"wallet_{i}": (i + 1) * 1000},
        )
        snapshots.append((ts, 10000 + i * 100))

    proc = subprocess.Popen(
        [sys.executable, wrapper_script,
         "--config", config_file,
         "--db", temp_db],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=5)

    # Verify schema intact
    conn = sqlite3.connect(temp_db)
    table_info = conn.execute("PRAGMA table_info(snapshots)").fetchall()
    columns = {r[1] for r in table_info}
    assert "timestamp" in columns
    assert "chain_tip" in columns
    assert "wallet_balances" in columns

    # All committed rows present
    rows = conn.execute(
        "SELECT timestamp, chain_tip FROM snapshots ORDER BY timestamp"
    ).fetchall()
    conn.close()

    assert len(rows) == 3, f"Expected 3 snapshots, got {len(rows)}"
    for ts, tip in snapshots:
        assert any(r[0] == ts and r[1] == tip for r in rows), (
            f"Snapshot (ts={ts}, tip={tip}) missing after SIGKILL"
        )

    # PRAGMA integrity_check passes
    conn = sqlite3.connect(temp_db)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    assert integrity == "ok", f"DB integrity check failed after SIGKILL: {integrity}"


def test_sigkill_pre_written_snapshot_survives(tmp_path, config_file, temp_db,
                                                project_root, wrapper_script):
    """A snapshot written before SIGKILL is readable after the hard kill."""
    from collector.db import init_db, write_snapshot, get_latest_snapshot

    init_db(temp_db)

    now = int(time.time())
    ts = (now // 600) * 600 - 600
    write_snapshot(
        db_path=temp_db,
        timestamp=ts,
        chain_tip=7000,
        lib="pre_kill_lib",
        mode="Normal",
        epoch=30,
        mempool_depth=7,
        peer_count=12,
        n_connections=4,
        wallet_balances={"survivor_wallet": 54321},
    )

    proc = subprocess.Popen(
        [sys.executable, wrapper_script,
         "--config", config_file,
         "--db", temp_db],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=5)

    snap = get_latest_snapshot(temp_db)
    assert snap is not None, "Pre-written snapshot must survive SIGKILL"
    assert snap["chain_tip"] == 7000
    assert snap["lib"] == "pre_kill_lib"
    assert snap["wallet_balances"] == '{"survivor_wallet": 54321}'


# ---------------------------------------------------------------------------
# Test: INSERT OR REPLACE deduplication
# ---------------------------------------------------------------------------

def test_insert_or_replace_same_window_overwrites(tmp_path):
    """Two writes for the same 10-min timestamp results in exactly one row with the latest data."""
    from collector.db import init_db, write_snapshot, get_latest_snapshot, get_connection

    db_path = str(tmp_path / "dedup.db")
    init_db(db_path)

    now = int(time.time())
    ts = (now // 600) * 600

    write_snapshot(
        db_path=db_path,
        timestamp=ts,
        chain_tip=7000,
        lib="lib_v1",
        mode="Bootstrapping",
        epoch=5,
        mempool_depth=1,
        peer_count=3,
        n_connections=1,
        wallet_balances={"wallet_a": 100},
    )
    write_snapshot(
        db_path=db_path,
        timestamp=ts,
        chain_tip=7500,
        lib="lib_v2",
        mode="Normal",
        epoch=6,
        mempool_depth=20,
        peer_count=9,
        n_connections=4,
        wallet_balances={"wallet_a": 200, "wallet_b": 300},
    )

    conn = get_connection(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE timestamp = ?", (ts,)
    ).fetchone()[0]
    conn.close()

    assert count == 1, f"Expected 1 row for timestamp {ts}, got {count}"

    snap = get_latest_snapshot(db_path)
    assert snap["chain_tip"] == 7500
    assert snap["lib"] == "lib_v2"
    assert snap["mode"] == "Normal"
    assert snap["mempool_depth"] == 20
    assert snap["wallet_balances"] == '{"wallet_a": 200, "wallet_b": 300}'


def test_insert_or_replace_different_windows_both_kept(tmp_path):
    """Writes for different 10-min windows are both preserved (no cross-window deduplication)."""
    from collector.db import init_db, write_snapshot, get_snapshots_since

    db_path = str(tmp_path / "twowindows.db")
    init_db(db_path)

    now = int(time.time())
    ts1 = (now // 600) * 600 - 600
    ts2 = (now // 600) * 600

    write_snapshot(
        db_path=db_path,
        timestamp=ts1,
        chain_tip=8000,
        lib="lib_t1",
        mode="Normal",
        epoch=10,
        mempool_depth=5,
        peer_count=6,
        n_connections=2,
        wallet_balances={},
    )
    write_snapshot(
        db_path=db_path,
        timestamp=ts2,
        chain_tip=8100,
        lib="lib_t2",
        mode="Normal",
        epoch=11,
        mempool_depth=8,
        peer_count=7,
        n_connections=3,
        wallet_balances={},
    )

    snaps = get_snapshots_since(db_path, 0)
    assert len(snaps) == 2, f"Expected 2 snapshots, got {len(snaps)}"
    timestamps = {s["timestamp"] for s in snaps}
    assert ts1 in timestamps
    assert ts2 in timestamps


def test_insert_or_replace_idempotent_multiple_restarts(tmp_path):
    """Simulates collector restarts: same 10-min window written by multiple processes results in one row."""
    from collector.db import init_db, write_snapshot, get_latest_snapshot

    db_path = str(tmp_path / "restarts.db")
    init_db(db_path)

    now = int(time.time())
    ts = (now // 600) * 600

    for i in range(3):
        write_snapshot(
            db_path=db_path,
            timestamp=ts,
            chain_tip=9000 + i * 10,
            lib=f"lib_restart_{i}",
            mode="Normal",
            epoch=100 + i,
            mempool_depth=i,
            peer_count=5 + i,
            n_connections=2 + i,
            wallet_balances={"restart_wallet": 1000 * (i + 1)},
        )

    snap = get_latest_snapshot(db_path)
    assert snap["chain_tip"] == 9020  # last write wins
    assert snap["wallet_balances"] == '{"restart_wallet": 3000}'

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    conn.close()
    assert count == 1, f"Expected exactly 1 row after 3 restarts for same window, got {count}"
