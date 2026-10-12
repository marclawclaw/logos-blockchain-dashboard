"""Tests for the storage and delivery readers (logosctl-backed kinds).

The canned answers below have the shapes logosctl 0.3.0 returned from a live
node (storage_module 3.0.2, delivery_module 0.3.2), with identifiers replaced.
"""

from __future__ import annotations

import json
import subprocess
import time
from unittest.mock import patch

import pytest

from collector.config import Config, LogosctlConfig, ModuleConfig


def _ok(module, method, result):
    return {"method": method, "module": module, "result": result, "status": "ok"}


def _env(value):
    return {"error": None, "success": True, "value": value}


STATUS = {
    "daemon": {"pid": 3, "status": "running", "version": "1.0.0"},
    "modules": [
        {"name": "storage_module", "status": "loaded", "uptime_seconds": 4056, "version": "3.0.2"},
        {"name": "delivery_module", "status": "loaded", "uptime_seconds": 4050, "version": "0.3.2"},
        {"name": "capability_module", "status": "loaded", "uptime_seconds": 4058, "version": "1.0.0"},
    ],
    "modules_summary": {"crashed": 0, "loaded": 3, "not_loaded": 0},
}

STATS = [
    {"cpu_percent": 0.0, "cpu_time_seconds": 3.27, "memory_mb": 54.01, "name": "storage_module"},
    {"cpu_percent": 0.0, "cpu_time_seconds": 61.61, "memory_mb": 122.31, "name": "delivery_module"},
]

OPENMETRICS = """\
# HELP libp2p_peers total connected peers
# TYPE libp2p_peers gauge
libp2p_peers 32.0
# TYPE libp2p_network_bytes counter
libp2p_network_bytes_total{direction="in"} 2407743194.0
libp2p_network_bytes_created{direction="in"} 1791636697.0
libp2p_network_bytes_total{direction="out"} 1229804058.0
libp2p_network_bytes_created{direction="out"} 1791636697.0
# HELP libp2p_gossipsub_received number of messages received (deduplicated)
libp2p_gossipsub_received_total 73000.0
# HELP libp2p_pubsub_messages_published published messages
# TYPE logos_delivery_version gauge
logos_delivery_version{version="v0.39.0-g3a478e"} 1.0
logos_delivery_connected_peers{direction="Out",protocol="/vac/waku/relay/2.0.0"} 30.0
"""

CALLS = {
    ("storage_module", "isRunning"): True,
    ("storage_module", "network"): _env("logos.test"),
    ("storage_module", "debug"): _env({
        "addrs": ["/ip4/192.0.2.10/tcp/8399/p2p/16Uiu2HAmRelay/p2p-circuit"],
        "connections": [{"direct": True, "peerId": "16Uiu2HAkPeerA"},
                        {"direct": True, "peerId": "16Uiu2HAkPeerB"}],
        "id": "16Uiu2HAmStorageNode",
        "nat": {"clientMode": True, "portMapping": "none", "reachability": "NotReachable"},
        "storage": {"revision": "0.5.3-56854f4e", "version": "0.5.3-56854f4e"},
    }),
    ("storage_module", "space"): _env({
        "quotaMaxBytes": 21474836480, "quotaReservedBytes": 0,
        "quotaUsedBytes": 1048576, "totalBlocks": 16,
    }),
    ("storage_module", "manifests"): _env([{"cid": "zDvExample1"}, {"cid": "zDvExample2"}]),
    ("storage_module", "collectMetrics"): {"metrics": [
        {"help": "storage API uploads", "labels": {}, "type": "counter",
         "name": "logos_storage_api_uploads_total", "value": 2.0},
        {"help": "storage blockexchange blocks sent", "labels": {}, "type": "counter",
         "name": "logos_storage_block_exchange_blocks_sent_total", "value": 40.0},
        {"help": "total connected peers", "labels": {}, "type": "gauge",
         "name": "logos_storage_libp2p_peers", "value": 30.0},
    ]},
    ("delivery_module", "getNodeInfo", "IsRunning"): _env("true"),
    ("delivery_module", "getConnectionStatus"): _env("Connected"),
    ("delivery_module", "rlnState"): _env({
        "epochSizeSec": 600, "state": "Failed",
        "message": "rln module start failed: module not loaded",
    }),
    ("delivery_module", "collectOpenMetricsText"): OPENMETRICS,
}


class FakeLogosctl:
    """Stands in for the logosctl process: answers from STATUS / STATS / CALLS."""

    def __init__(self, status=STATUS, stats=STATS, calls=CALLS, daemon_up=True):
        self.status, self.stats, self.calls, self.daemon_up = status, stats, dict(calls), daemon_up
        self.argvs = []

    def __call__(self, argv, **kwargs):
        self.argvs.append(argv)
        args = argv[argv.index("--json") + 1:]
        if not self.daemon_up:
            return self._done(argv, 2, {"status": "error", "code": "NO_DAEMON",
                                        "message": "No daemon running."})
        if args == ["status"]:
            return self._done(argv, 0, self.status)
        if args == ["stats"]:
            return self._done(argv, 0, self.stats)
        key = tuple(args[1:])
        if key not in self.calls:
            return self._done(argv, 4, {"status": "error", "code": "METHOD_NOT_FOUND",
                                        "message": f"Method '{args[2]}' not found."})
        return self._done(argv, 0, _ok(args[1], args[2], self.calls[key]))

    @staticmethod
    def _done(argv, code, payload):
        return subprocess.CompletedProcess(argv, code, stdout=json.dumps(payload), stderr="")


def _config(storage=True, delivery=True, command=None):
    return Config(
        storage=ModuleConfig("storage_module") if storage else None,
        delivery=ModuleConfig("delivery_module") if delivery else None,
        logosctl=LogosctlConfig(command=command or ["logosctl"]),
    )


def _read(fake, **cfg_kw):
    from collector.modules import read_modules
    with patch("collector.logosctl.subprocess.run", side_effect=fake):
        return read_modules(_config(**cfg_kw))


# ---------------------------------------------------------------------------
# Readings
# ---------------------------------------------------------------------------

def test_storage_reading():
    reading = _read(FakeLogosctl())["storage"]
    assert reading.ok and reading.error is None
    assert reading.data == {
        "module_status": "loaded", "module_version": "3.0.2", "uptime_seconds": 4056,
        "memory_mb": 54.01, "cpu_seconds": 3.27, "running": 1,
        "network": "logos.test", "peer_id": "16Uiu2HAmStorageNode",
        "lib_version": "0.5.3-56854f4e", "peers": 2,
        "quota_max_bytes": 21474836480, "quota_used_bytes": 1048576,
        "quota_reserved_bytes": 0, "blocks": 16, "manifests": 2,
        "uploads_total": 2,
        "downloads_total": 0,          # counter never incremented: absent from the metrics
        "blocks_sent_total": 40, "blocks_received_total": 0,
    }


def test_delivery_reading():
    reading = _read(FakeLogosctl())["delivery"]
    assert reading.ok
    assert reading.data == {
        "module_status": "loaded", "module_version": "0.3.2", "uptime_seconds": 4050,
        "memory_mb": 122.31, "cpu_seconds": 61.61, "running": 1,
        "connection_status": "Connected",
        "rln_state": "Failed", "rln_message": "rln module start failed: module not loaded",
        "node_version": "v0.39.0-g3a478e", "peers": 32,
        "bytes_in_total": 2407743194, "bytes_out_total": 1229804058,
        "messages_in_total": 73000, "messages_out_total": 0,
    }


def test_disconnected_delivery_node_is_still_up():
    """Losing connectivity is a state of a running node, not a node failure."""
    fake = FakeLogosctl()
    fake.calls[("delivery_module", "getConnectionStatus")] = _env("Disconnected")
    reading = _read(fake)["delivery"]
    assert reading.ok
    assert reading.data["connection_status"] == "Disconnected"


def test_only_deployed_kinds_are_read():
    fake = FakeLogosctl()
    readings = _read(fake, delivery=False)
    assert set(readings) == {"storage"}
    assert not any("delivery_module" in argv for argv in fake.argvs)


def test_no_module_kind_deployed_never_runs_logosctl():
    fake = FakeLogosctl()
    assert _read(fake, storage=False, delivery=False) == {}
    assert fake.argvs == []


def test_command_prefix_is_used():
    fake = FakeLogosctl()
    _read(fake, delivery=False, command=["podman", "exec", "logos-core", "logosctl"])
    assert fake.argvs[0] == ["podman", "exec", "logos-core", "logosctl", "--json", "status"]


# ---------------------------------------------------------------------------
# Nodes that are not available
# ---------------------------------------------------------------------------

def test_daemon_down_marks_every_module_kind_down():
    readings = _read(FakeLogosctl(daemon_up=False))
    assert set(readings) == {"storage", "delivery"}
    for reading in readings.values():
        assert not reading.ok
        assert "NO_DAEMON" in reading.error


def test_logosctl_not_installed():
    from collector.modules import read_modules
    with patch("collector.logosctl.subprocess.run", side_effect=FileNotFoundError("logosctl")):
        readings = read_modules(_config())
    assert not readings["storage"].ok
    assert "NOT_INSTALLED" in readings["storage"].error


def test_logosctl_timeout():
    from collector.modules import read_modules
    with patch("collector.logosctl.subprocess.run",
               side_effect=subprocess.TimeoutExpired("logosctl", 20)):
        readings = read_modules(_config())
    assert not readings["delivery"].ok
    assert "TIMEOUT" in readings["delivery"].error


@pytest.mark.parametrize("module_status", ["crashed", None])
def test_one_module_down_does_not_affect_the_other(module_status):
    """A crashed or unloaded storage module leaves delivery readable."""
    status = json.loads(json.dumps(STATUS))
    if module_status is None:
        status["modules"] = [m for m in status["modules"] if m["name"] != "storage_module"]
    else:
        status["modules"][0]["status"] = module_status
    readings = _read(FakeLogosctl(status=status))
    assert not readings["storage"].ok
    assert (module_status or "not_loaded") in readings["storage"].error
    assert readings["delivery"].ok


def test_stopped_node_inside_loaded_module_is_down():
    fake = FakeLogosctl()
    fake.calls[("storage_module", "isRunning")] = False
    reading = _read(fake)["storage"]
    assert not reading.ok
    assert reading.data["running"] == 0


def test_failing_call_leaves_the_rest_of_the_reading():
    """One method failing costs its fields only."""
    fake = FakeLogosctl()
    del fake.calls[("storage_module", "space")]
    reading = _read(fake)["storage"]
    assert reading.ok
    assert "quota_used_bytes" not in reading.data
    assert reading.data["manifests"] == 2
    assert reading.data["peer_id"] == "16Uiu2HAmStorageNode"


def test_envelope_failure_is_a_failed_call():
    fake = FakeLogosctl()
    fake.calls[("storage_module", "manifests")] = {"error": "boom", "success": False, "value": None}
    reading = _read(fake)["storage"]
    assert reading.ok
    assert "manifests" not in reading.data


# ---------------------------------------------------------------------------
# OpenMetrics parsing
# ---------------------------------------------------------------------------

def test_parse_openmetrics_and_metric_sum():
    from collector.modules import metric_sum, parse_openmetrics
    samples = parse_openmetrics(OPENMETRICS)
    assert metric_sum(samples, "libp2p_peers") == 32
    assert metric_sum(samples, "libp2p_network_bytes_total", direction="in") == 2407743194
    # Without a label filter every series of the name is summed.
    assert metric_sum(samples, "libp2p_network_bytes_total") == 2407743194 + 1229804058
    assert metric_sum(samples, "never_seen") is None
    assert metric_sum(samples, "never_seen", 0) == 0


# ---------------------------------------------------------------------------
# Collector: kinds are stored independently
# ---------------------------------------------------------------------------

def _collect(tmp_path, fake, config):
    from collector.db import init_db
    from collector.main import _collect_and_store
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    with patch("collector.logosctl.subprocess.run", side_effect=fake):
        _collect_and_store(config, db_path)
    return db_path


def test_collector_stores_one_snapshot_per_kind(tmp_path):
    from collector.db import get_latest_kind_snapshot, get_latest_snapshot
    db_path = _collect(tmp_path, FakeLogosctl(), _config())

    storage = get_latest_kind_snapshot(db_path, "storage")
    assert storage["quota_used_bytes"] == 1048576
    assert storage["manifests"] == 2
    delivery = get_latest_kind_snapshot(db_path, "delivery")
    assert delivery["peers"] == 32
    assert delivery["rln_state"] == "Failed"
    assert get_latest_kind_snapshot(db_path, "host") is not None
    # No blockchain node configured: its table stays empty and nothing is fetched over HTTP.
    assert get_latest_snapshot(db_path) is None
    assert storage["timestamp"] % 600 == 0


def test_collector_skips_a_kind_that_is_down_and_keeps_the_others(tmp_path):
    from collector.db import get_latest_kind_snapshot
    fake = FakeLogosctl()
    fake.calls[("storage_module", "isRunning")] = False
    db_path = _collect(tmp_path, fake, _config())

    assert get_latest_kind_snapshot(db_path, "storage") is None      # a gap, not a row of zeros
    assert get_latest_kind_snapshot(db_path, "delivery")["peers"] == 32
    assert get_latest_kind_snapshot(db_path, "host") is not None


def test_collector_records_host_when_no_node_answers(tmp_path):
    """Host metrics do not depend on any node, including the blockchain one."""
    from collector.config import BlockchainConfig
    from collector.db import get_latest_kind_snapshot, get_latest_snapshot
    from collector.fetcher import FetchResult

    config = _config()
    config.blockchain = BlockchainConfig(axum_url="http://localhost:39999")
    with patch("collector.main.fetch_all", return_value=FetchResult()):
        db_path = _collect(tmp_path, FakeLogosctl(daemon_up=False), config)

    assert get_latest_snapshot(db_path) is None
    assert get_latest_kind_snapshot(db_path, "storage") is None
    assert get_latest_kind_snapshot(db_path, "host") is not None


def test_collector_with_no_kind_configured_records_host_only(tmp_path):
    from collector.db import get_latest_kind_snapshot
    fake = FakeLogosctl()
    db_path = _collect(tmp_path, fake, Config())
    assert get_latest_kind_snapshot(db_path, "host") is not None
    assert fake.argvs == []


# ---------------------------------------------------------------------------
# DB: per-kind tables
# ---------------------------------------------------------------------------

def test_kind_tables_are_added_to_an_existing_database(tmp_path):
    """A database created before storage/delivery existed gains the new tables."""
    import sqlite3
    from collector.db import SCHEMA, get_kind_snapshots_since, init_db, write_kind_snapshot

    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    init_db(db_path)
    write_kind_snapshot(db_path, "storage", 600, {"peers": 3, "not_a_column": 1})
    rows = get_kind_snapshots_since(db_path, "storage", 0)
    assert len(rows) == 1 and rows[0]["peers"] == 3 and rows[0]["blocks"] is None


def test_host_history_includes_rows_from_the_blockchain_table(tmp_path):
    """Host metrics recorded before host_snapshots existed stay in the host history."""
    from collector.db import (
        get_connection, get_kind_snapshots_since, init_db, write_kind_snapshot, write_snapshot,
    )
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    write_snapshot(db_path, 600, 1000, "lib", "Normal", 1, 0, 5, 5, {})
    conn = get_connection(db_path)
    conn.execute("UPDATE snapshots SET cpu_temp = 41.5, mem_pct = 30.0 WHERE timestamp = 600")
    conn.commit()
    conn.close()
    write_snapshot(db_path, 1200, 1001, "lib", "Normal", 1, 0, 5, 5, {})   # no host values
    write_kind_snapshot(db_path, "host", 1800, {"cpu_temp": 44.0})

    rows = get_kind_snapshots_since(db_path, "host", 0)
    assert [(r["timestamp"], r["cpu_temp"]) for r in rows] == [(600, 41.5), (1800, 44.0)]


def test_prune_covers_every_table(tmp_path):
    from collector.db import (
        get_kind_snapshots_since, init_db, prune_old_snapshots, write_kind_snapshot,
    )
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    now = (int(time.time()) // 600) * 600
    for kind in ("storage", "delivery", "host"):
        write_kind_snapshot(db_path, kind, now, {})
        write_kind_snapshot(db_path, kind, now - 91 * 24 * 3600, {})

    assert prune_old_snapshots(db_path, retention_days=90) == 3
    for kind in ("storage", "delivery", "host"):
        assert len(get_kind_snapshots_since(db_path, kind, 0)) == 1
