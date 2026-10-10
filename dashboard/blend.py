"""Blend Network status for the dashboard.

Combines the node's on-chain SDP declarations (``/mantle/sdp/declarations``),
``/blend/info`` and the host's detected public IP into one summary: network
stats, this node's own core-node declaration, and whether the IP registered
in that declaration still matches the host's current public IP.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BLEND_SERVICE_TYPE = "BN"
# A declaration takes effect this many epochs after the epoch it was included in.
ACTIVATION_DELAY_EPOCHS = 2
# A node re-attests with an Active message every epoch; allow one epoch of lag.
ACTIVE_LAG_EPOCHS = 1

PUBLIC_IP_SOURCES = ("https://api.ipify.org", "https://ifconfig.me/ip")
PUBLIC_IP_TTL_SECONDS = 600

_LOCATOR_RE = re.compile(r"^/(ip4|ip6|dns4|dns6|dns)/([^/]+)/(udp|tcp)/(\d+)")
_public_ip_cache: dict = {"ip": None, "at": 0.0}


def parse_locator(locator: str) -> tuple[str | None, int | None]:
    """Return (host, port) from a multiaddr like /ip4/1.2.3.4/udp/3400/quic-v1."""
    m = _LOCATOR_RE.match(locator or "")
    if not m:
        return None, None
    return m.group(2), int(m.group(4))


def latest_epoch(declarations: dict) -> int | None:
    """Best available view of the current epoch: the highest epoch any
    declaration was created in or re-attested for.

    A declaration's ``active`` starts at ``created + 2`` (the epoch it takes
    effect) and only tracks real attestations once the node has sent an Active
    message (``nonce`` > 0), so an un-attested ``active`` is a future epoch and
    must not be counted.
    """
    epochs = []
    for d in declarations.values():
        if isinstance(d.get("created"), int):
            epochs.append(d["created"])
        if isinstance(d.get("active"), int) and (d.get("nonce") or 0) > 0:
            epochs.append(d["active"])
    return max(epochs) if epochs else None


def classify(decl: dict, epoch: int | None) -> str:
    """One of: withdrawn, pending, active, inactive."""
    if decl.get("withdraw_at") is not None:
        return "withdrawn"
    created = decl.get("created")
    if epoch is None or created is None:
        return "pending"
    if epoch < created + ACTIVATION_DELAY_EPOCHS:
        return "pending"
    active = decl.get("active")
    if active is not None and active >= epoch - ACTIVE_LAG_EPOCHS:
        return "active"
    return "inactive"


def summarize(declarations: dict, blend_info: dict | None, zk_id: str | None,
              provider_id: str | None, public_ip: str | None) -> dict:
    """Build the /api/blend/status payload from raw node data."""
    blend = {k: v for k, v in (declarations or {}).items()
             if v.get("service_type") == BLEND_SERVICE_TYPE}
    epoch = latest_epoch(blend)

    counts = {"active": 0, "pending": 0, "inactive": 0, "withdrawn": 0}
    hosts = set()
    ours = None
    for decl_id, d in blend.items():
        counts[classify(d, epoch)] += 1
        for loc in d.get("locators") or []:
            host, _ = parse_locator(loc)
            if host:
                hosts.add(host)
        if (zk_id and d.get("zk_id") == zk_id) or (provider_id and d.get("provider_id") == provider_id):
            ours = (decl_id, d)

    node = {
        "declared": ours is not None,
        "status": "not_declared",
        "core_info": (blend_info or {}).get("core_info"),
        "public_ip": public_ip,
        "registered_ip": None,
        "ip_match": None,
    }
    if ours:
        decl_id, d = ours
        locator = (d.get("locators") or [None])[0]
        reg_ip, reg_port = parse_locator(locator) if locator else (None, None)
        node.update({
            "status": classify(d, epoch),
            "declaration_id": decl_id,
            "created": d.get("created"),
            "active": d.get("active"),
            "activates_at": (d["created"] + ACTIVATION_DELAY_EPOCHS) if d.get("created") is not None else None,
            "withdraw_at": d.get("withdraw_at"),
            "locked_note_id": d.get("service_note_id") or d.get("locked_note_id"),
            "locator": locator,
            "registered_ip": reg_ip,
            "registered_port": reg_port,
        })
        if reg_ip and public_ip:
            node["ip_match"] = reg_ip == public_ip

    return {
        "epoch": epoch,
        "network": {"core_nodes": len(blend), "distinct_hosts": len(hosts), **counts},
        "node": node,
    }


def our_blend_keys(node_config_path: str | None) -> tuple[str | None, str | None]:
    """Read (BlendZk key id, Blend signing key id) from the node's user_config.yaml.

    The declaration's zk_id is the BlendZk public key and its provider_id the
    Blend signing key; in the node config both are KMS ids equal to the
    public keys (``blend.core.zk.secret_key_kms_id`` and
    ``blend.non_ephemeral_signing_key_id``).
    """
    if not node_config_path or not Path(node_config_path).exists():
        return None, None
    import yaml
    from collector.config import _yaml_loader  # tolerates the kms `!Zk`/`!Ed25519` tags
    try:
        cfg = yaml.load(Path(node_config_path).read_text(), Loader=_yaml_loader) or {}
    except Exception as e:
        logger.warning("Cannot read node config %s: %s", node_config_path, e)
        return None, None
    blend = cfg.get("blend") or {}
    zk = ((blend.get("core") or {}).get("zk") or {}).get("secret_key_kms_id")
    return zk, blend.get("non_ephemeral_signing_key_id")


def detect_public_ip() -> str | None:
    """The host's public IPv4, as seen from the internet (cached)."""
    now = time.time()
    if _public_ip_cache["ip"] and now - _public_ip_cache["at"] < PUBLIC_IP_TTL_SECONDS:
        return _public_ip_cache["ip"]
    import requests
    for url in PUBLIC_IP_SOURCES:
        try:
            ip = requests.get(url, timeout=5).text.strip()
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
                _public_ip_cache.update(ip=ip, at=now)
                return ip
        except Exception as e:
            logger.warning("Public IP lookup via %s failed: %s", url, e)
    return _public_ip_cache["ip"]
