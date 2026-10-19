"""Readings for nodes that run as Logos Core modules: storage, delivery, and
the Blend traffic of the blockchain node.

A reading is a flat dict whose keys match the columns of that kind's snapshot
table (see db.py). A value that could not be read is None, so one failing call
never discards the rest of the reading.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import Config
from .logosctl import Logosctl, LogosctlError

logger = logging.getLogger(__name__)

MODULE_KINDS = ("blend", "storage", "delivery")


@dataclass
class Reading:
    """One look at a node. `ok` is False when the node cannot be considered up."""

    ok: bool
    data: dict = field(default_factory=dict)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try(ctl: Logosctl, module: str, method: str, *args: str) -> Any:
    """Call a module method. Return None on failure (caller handles gracefully)."""
    try:
        return ctl.call(module, method, *args)
    except LogosctlError as e:
        logger.warning("logosctl call %s %s failed: %s", module, method, e)
        return None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _module_base(status: dict, stats: list, module: str) -> dict:
    """Fields every module kind shares, from `logosctl status` and `logosctl stats`."""
    entry = next((m for m in status.get("modules", []) if m.get("name") == module), {})
    usage = next((s for s in stats if isinstance(s, dict) and s.get("name") == module), {})
    return {
        "module_status": entry.get("status", "not_loaded"),
        "module_version": entry.get("version"),
        "uptime_seconds": _int(entry.get("uptime_seconds")),
        "memory_mb": usage.get("memory_mb"),
        "cpu_seconds": usage.get("cpu_time_seconds"),
    }


_SAMPLE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)")
_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse_openmetrics(text: str) -> list[tuple[str, dict, float]]:
    """Parse OpenMetrics/Prometheus text into (name, labels, value) samples."""
    samples = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        try:
            value = float(m.group(3))
        except ValueError:
            continue
        samples.append((m.group(1), dict(_LABEL_RE.findall(m.group(2) or "")), value))
    return samples


def metric_sum(samples: list[tuple[str, dict, float]], name: str,
               default: Optional[int] = None, **labels: str) -> Optional[int]:
    """Sum every sample of `name` whose labels include `labels`.

    A counter that was never incremented is absent from the text; pass
    `default=0` for those.
    """
    values = [
        v for n, lab, v in samples
        if n == name and all(lab.get(k) == want for k, want in labels.items())
    ]
    return int(sum(values)) if values else default


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _read_storage(ctl: Logosctl, module: str, data: dict) -> Reading:
    running = _try(ctl, module, "isRunning")
    data["running"] = None if running is None else int(bool(running))
    if running is not True:
        return Reading(False, data, "storage node is not running")

    data["network"] = _try(ctl, module, "network")

    debug = _try(ctl, module, "debug")
    if isinstance(debug, dict):
        data["peer_id"] = debug.get("id")
        data["lib_version"] = (debug.get("storage") or {}).get("version")
        connections = debug.get("connections")
        data["peers"] = len(connections) if isinstance(connections, list) else None

    space = _try(ctl, module, "space")
    if isinstance(space, dict):
        data["quota_max_bytes"] = _int(space.get("quotaMaxBytes"))
        data["quota_used_bytes"] = _int(space.get("quotaUsedBytes"))
        data["quota_reserved_bytes"] = _int(space.get("quotaReservedBytes"))
        data["blocks"] = _int(space.get("totalBlocks"))

    manifests = _try(ctl, module, "manifests")
    if isinstance(manifests, list):
        data["manifests"] = len(manifests)

    metrics = _try(ctl, module, "collectMetrics")
    if isinstance(metrics, dict) and isinstance(metrics.get("metrics"), list):
        samples = [
            (m.get("name"), m.get("labels") or {}, m.get("value") or 0)
            for m in metrics["metrics"] if isinstance(m, dict)
        ]
        data["uploads_total"] = metric_sum(samples, "logos_storage_api_uploads_total", 0)
        data["downloads_total"] = metric_sum(samples, "logos_storage_api_downloads_total", 0)
        data["blocks_sent_total"] = metric_sum(
            samples, "logos_storage_block_exchange_blocks_sent_total", 0)
        data["blocks_received_total"] = metric_sum(
            samples, "logos_storage_block_exchange_blocks_received_total", 0)

    return Reading(True, data)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _read_delivery(ctl: Logosctl, module: str, data: dict) -> Reading:
    running = _try(ctl, module, "getNodeInfo", "IsRunning")
    data["running"] = None if running is None else int(str(running).lower() == "true")
    if not data["running"]:
        return Reading(False, data, "delivery node is not running")

    # "Disconnected" is a state of a running node, not a failure of the node.
    data["connection_status"] = _try(ctl, module, "getConnectionStatus")

    rln = _try(ctl, module, "rlnState")
    if isinstance(rln, dict):
        data["rln_state"] = rln.get("state")
        data["rln_message"] = rln.get("message") or None

    text = _try(ctl, module, "collectOpenMetricsText")
    if isinstance(text, str) and text:
        samples = parse_openmetrics(text)
        data["node_version"] = next(
            (lab.get("version") for n, lab, _ in samples if n == "logos_delivery_version"), None)
        data["peers"] = metric_sum(samples, "libp2p_peers")
        data["bytes_in_total"] = metric_sum(
            samples, "libp2p_network_bytes_total", 0, direction="in")
        data["bytes_out_total"] = metric_sum(
            samples, "libp2p_network_bytes_total", 0, direction="out")
        # Received is counted after de-duplication; published is this node's own sends.
        data["messages_in_total"] = metric_sum(samples, "libp2p_gossipsub_received_total", 0)
        data["messages_out_total"] = metric_sum(
            samples, "libp2p_pubsub_messages_published_total", 0)

    return Reading(True, data)


# ---------------------------------------------------------------------------
# Blend traffic (blockchain node)
# ---------------------------------------------------------------------------

BLEND_METRICS_OFF = (
    "node metrics are off: set tracing.metrics.enable_open_metrics: true "
    "in the node's user_config.yaml and restart it"
)


def _blend_counter(samples, name: str, **labels: str) -> int:
    """A Blend counter. The node's OpenTelemetry exporter appends a second
    `_total` to names that already end in one (blockchain_module 0.3.2), so
    accept both spellings."""
    value = metric_sum(samples, f"{name}_total", None, **labels)
    if value is None:
        value = metric_sum(samples, name, None, **labels)
    return value or 0


def _read_blend(ctl: Logosctl, module: str, data: dict) -> Reading:
    text = _try(ctl, module, "collectOpenMetricsText")
    if not isinstance(text, str) or not text:
        return Reading(False, data, BLEND_METRICS_OFF)
    samples = parse_openmetrics(text)
    data["running"] = 1
    data["core_peers"] = metric_sum(samples, "blend_core_peers_negotiated")
    data["blacklist_size"] = metric_sum(samples, "blend_core_blacklist_size")
    data["messages_forwarded_total"] = _blend_counter(
        samples, "blend_messages_sent_total", action="forward")
    data["messages_published_total"] = _blend_counter(
        samples, "blend_messages_sent_total", action="publish")
    data["messages_received_total"] = _blend_counter(samples, "blend_messages_received_total")
    data["mix_packets_processed_total"] = _blend_counter(
        samples, "blend_mix_packets_processed_total")
    data["publish_failed_total"] = _blend_counter(
        samples, "blend_outbound_messages_failed_total", action="publish")
    data["forward_failed_total"] = _blend_counter(
        samples, "blend_outbound_messages_failed_total", action="forward")
    data["inbound_failed_total"] = _blend_counter(samples, "blend_inbound_messages_failed_total")
    data["inbound_dropped_total"] = _blend_counter(samples, "blend_inbound_messages_dropped_total")
    data["peers_blocked_total"] = _blend_counter(samples, "blend_core_peers_blocked_total")
    data["payloads_bypassed_total"] = _blend_counter(samples, "blend_payloads_bypassed_total")
    return Reading(True, data)


_READERS = {"blend": _read_blend, "storage": _read_storage, "delivery": _read_delivery}


# ---------------------------------------------------------------------------
# Combined read
# ---------------------------------------------------------------------------


def read_modules(cfg: Config) -> dict[str, Reading]:
    """Read every deployed module kind. Kinds that are not deployed are absent."""
    wanted = {k: getattr(cfg, k).module for k in MODULE_KINDS if getattr(cfg, k) is not None}
    if not wanted:
        return {}

    ctl = Logosctl(cfg.logosctl)
    try:
        status = ctl.status()
    except LogosctlError as e:
        logger.warning("logosctl status failed: %s", e)
        return {k: Reading(False, {}, f"Logos Core daemon unreachable ({e})") for k in wanted}

    try:
        stats = ctl.stats()
    except LogosctlError as e:
        logger.warning("logosctl stats failed: %s", e)
        stats = []

    readings: dict[str, Reading] = {}
    for kind, module in wanted.items():
        data = _module_base(status, stats, module)
        if data["module_status"] != "loaded":
            readings[kind] = Reading(False, data, f"module {module} is {data['module_status']}")
            continue
        try:
            readings[kind] = _READERS[kind](ctl, module, data)
        except Exception as e:  # a malformed answer must not take the other kind down
            logger.exception("Reading %s failed", kind)
            readings[kind] = Reading(False, data, f"unexpected answer from {module}: {e}")
    return readings
