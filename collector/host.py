"""Host metrics: CPU temperature, CPU load, memory — read from /proc and /sys.

Stdlib only (no psutil dependency). All readers fail gracefully to None so a
missing sensor never breaks a snapshot.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class HostMetrics:
    cpu_temp: Optional[float] = None    # hottest thermal zone, °C
    cpu_pct: Optional[float] = None     # CPU utilisation % since last sample
    mem_pct: Optional[float] = None     # used memory %
    load1: Optional[float] = None       # 1-minute load average


def read_cpu_temp() -> Optional[float]:
    """Hottest /sys/class/thermal zone, in °C. None if no sensors."""
    temps = []
    base = "/sys/class/thermal"
    try:
        for zone in os.listdir(base):
            if not zone.startswith("thermal_zone"):
                continue
            try:
                with open(f"{base}/{zone}/temp") as f:
                    temps.append(int(f.read().strip()) / 1000.0)
            except (OSError, ValueError):
                continue
    except OSError:
        return None
    return round(max(temps), 1) if temps else None


def _read_cpu_times() -> Optional[tuple[int, int]]:
    """Return (idle, total) jiffies from /proc/stat aggregate line."""
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        if parts[0] != "cpu":
            return None
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
        return idle, sum(vals)
    except (OSError, ValueError, IndexError):
        return None


def read_cpu_pct(sample_secs: float = 0.5) -> Optional[float]:
    """CPU utilisation % over a short sampling window."""
    first = _read_cpu_times()
    if first is None:
        return None
    time.sleep(sample_secs)
    second = _read_cpu_times()
    if second is None:
        return None
    idle_d = second[0] - first[0]
    total_d = second[1] - first[1]
    if total_d <= 0:
        return None
    return round((1.0 - idle_d / total_d) * 100.0, 1)


def read_mem_pct() -> Optional[float]:
    """Used memory % from /proc/meminfo (MemTotal - MemAvailable)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                info[k] = int(rest.split()[0])  # kB
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", 0)
        if total <= 0:
            return None
        return round((total - avail) / total * 100.0, 1)
    except (OSError, ValueError, KeyError):
        return None


def read_load1() -> Optional[float]:
    """1-minute load average."""
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


def read_host_metrics() -> HostMetrics:
    """Collect all host metrics, each failing independently to None."""
    return HostMetrics(
        cpu_temp=read_cpu_temp(),
        cpu_pct=read_cpu_pct(),
        mem_pct=read_mem_pct(),
        load1=read_load1(),
    )
