"""Config loading: parse config.yaml and auto-detect from user_config.yaml.

A node kind (blockchain, storage, delivery) is deployed only if its block is
present in config.yaml. An absent block means "not deployed": no polling, no
dashboard section, no error.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# Custom YAML loader that handles the !Ed25519 tag used in Logos node config files.
# Without this, yaml.safe_load and yaml.unsafe_load both fail on unknown tags.
def _logos_loader():
    """Construct a YAML loader that accepts unknown tags as plain strings."""
    loader = yaml.SafeLoader

    def _construct_undefined(loader, node):
        # For unknown tags, return the scalar value as a string
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        elif isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        elif isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        return None

    loader.add_constructor(None, _construct_undefined)
    return loader


_yaml_loader = _logos_loader()

# Node kinds, in the order the dashboard shows them. `blend` is the Blend
# traffic of the blockchain node, read from its metrics through logosctl.
KINDS = ("blockchain", "blend", "storage", "delivery")

# Top-level keys from before node kinds were explicit; they now live under `blockchain:`.
_LEGACY_KEYS = {
    "node_config_path": "blockchain.node_config_path",
    "node": "blockchain.axum_url",
    "wallets": "blockchain.wallets",
    "blend": "blockchain.blend",
}


class ConfigError(Exception):
    """Raised when config is invalid or missing required fields."""


@dataclass
class Wallet:
    name: str
    address: str


@dataclass
class BlockchainConfig:
    axum_url: str
    wallets: list[Wallet] = field(default_factory=list)
    # Resolved path to the node's user_config.yaml (may not exist).
    node_config_path: Optional[str] = None
    # Optional overrides for this node's Blend keys (zk_id, provider_id);
    # by default they are read from the node config.
    blend: dict = field(default_factory=dict)


@dataclass
class ModuleConfig:
    """A node that runs as a Logos Core module and is queried through logosctl."""

    module: str


@dataclass
class LogosctlConfig:
    # argv prefix, e.g. ["podman", "exec", "logos-core", "logosctl"].
    command: list[str] = field(default_factory=lambda: ["logosctl"])
    timeout_seconds: int = 20


@dataclass
class Config:
    blockchain: Optional[BlockchainConfig] = None
    # Blend traffic counters of the blockchain node (its module's OpenMetrics).
    # On whenever the blockchain kind is, unless `blockchain.blend_metrics: false`.
    blend: Optional[ModuleConfig] = None
    storage: Optional[ModuleConfig] = None
    delivery: Optional[ModuleConfig] = None
    logosctl: LogosctlConfig = field(default_factory=LogosctlConfig)
    interval_minutes: int = 10
    database: str = "data/snapshots.db"
    # How often the dashboard re-reads the nodes for its current values.
    refresh_seconds: int = 30

    @property
    def kinds(self) -> list[str]:
        """The deployed node kinds, in display order."""
        return [k for k in KINDS if getattr(self, k) is not None]


def expand_path(path: str) -> Path:
    """Expand ~ and environment variables in a path."""
    return Path(os.path.expandvars(os.path.expanduser(path)))


def _kind_block(raw: dict, kind: str) -> Optional[dict]:
    """Return a kind's config block, or None when the kind is not deployed.

    A bare `storage:` (no value) counts as deployed with defaults.
    """
    if kind not in raw:
        return None
    block = raw[kind]
    if block is None:
        block = {}
    if not isinstance(block, dict):
        raise ConfigError(f"`{kind}:` must be a mapping, got {type(block).__name__}.")
    if block.get("enabled") is False:
        return None
    return block


def _load_blockchain(block: dict) -> BlockchainConfig:
    node_config_path = block.get("node_config_path", "~/.config/logos-node/user_config.yaml")
    node_config_file = expand_path(node_config_path)

    # Auto-detect from user_config.yaml if present
    axum_url: Optional[str] = None
    auto_wallets: list[Wallet] = []

    if node_config_file.exists():
        with open(node_config_file) as f:
            node_raw = yaml.load(f, Loader=_yaml_loader) or {}

        # Extract API URL from api.backend.listen_address
        listen_addr = node_raw.get("api", {}).get("backend", {}).get("listen_address", "")
        if listen_addr:
            # listen_address format: "/ip4/0.0.0.0/port" or "0.0.0.0:port"
            port_match = re.search(r"(?::|/)\s*(\d+)$", str(listen_addr))
            if port_match:
                port = port_match.group(1)
                axum_url = f"http://localhost:{port}"

        # Extract wallets from wallet.known_keys
        known_keys = node_raw.get("wallet", {}).get("known_keys", {})
        if known_keys:
            for key_id, key_addr in known_keys.items():
                auto_wallets.append(Wallet(name=key_id[:12], address=key_addr))

    # Manual override: blockchain.axum_url in config.yaml
    manual_url = block.get("axum_url")
    if manual_url:
        axum_url = manual_url

    if not axum_url:
        raise ConfigError(
            "No API URL configured for the blockchain node. "
            "Set blockchain.node_config_path in config.yaml to point to your node's "
            "user_config.yaml, or set blockchain.axum_url manually. "
            "Remove the `blockchain:` block if no blockchain node runs on this machine."
        )

    # Manual wallets override (replace auto-detected)
    wallets: list[Wallet] = []
    manual_wallets = block.get("wallets") or []
    if manual_wallets:
        for w in manual_wallets:
            if isinstance(w, dict) and "address" in w:
                wallets.append(Wallet(name=w.get("name", "unknown"), address=w["address"]))
            else:
                raise ConfigError(f"Invalid wallet entry: {w}")
    else:
        wallets = auto_wallets

    return BlockchainConfig(
        axum_url=axum_url,
        wallets=wallets,
        node_config_path=str(node_config_file),
        blend=block.get("blend") or {},
    )


def _load_logosctl(raw: dict) -> LogosctlConfig:
    block = raw.get("logosctl") or {}
    command = block.get("command", ["logosctl"])
    if isinstance(command, str):
        command = command.split()
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise ConfigError("logosctl.command must be a non-empty list of strings.")
    return LogosctlConfig(command=command, timeout_seconds=int(block.get("timeout_seconds", 20)))


def load(config_path: Optional[str] = None) -> Config:
    """Load config from config.yaml, auto-detecting from user_config.yaml if present.

    Args:
        config_path: Path to config.yaml. Defaults to ./config.yaml

    Returns:
        Config object with one entry per deployed node kind.

    Raises:
        ConfigError: If the file is missing, uses pre-`blockchain:` top-level keys,
            or a deployed kind is misconfigured.
    """
    if config_path is None:
        config_path = "config.yaml"

    config_file = Path(config_path)
    if not config_file.exists():
        raise ConfigError(f"config.yaml not found at {config_path}.")

    with open(config_file) as f:
        raw = yaml.safe_load(f) or {}

    legacy = [k for k in _LEGACY_KEYS if k in raw]
    if legacy:
        moves = ", ".join(f"`{k}` -> `{_LEGACY_KEYS[k]}`" for k in legacy)
        raise ConfigError(
            f"{config_path} uses top-level keys that moved under `blockchain:` ({moves}). "
            "Node kinds are now explicit: a kind is polled only if its block "
            "(`blockchain:`, `storage:`, `delivery:`) is present."
        )

    blockchain_block = _kind_block(raw, "blockchain")
    storage_block = _kind_block(raw, "storage")
    delivery_block = _kind_block(raw, "delivery")

    blend_on = blockchain_block is not None and blockchain_block.get("blend_metrics", True) is not False

    return Config(
        blockchain=_load_blockchain(blockchain_block) if blockchain_block is not None else None,
        blend=(
            ModuleConfig(module=blockchain_block.get("module", "blockchain_module"))
            if blend_on else None
        ),
        storage=(
            ModuleConfig(module=storage_block.get("module", "storage_module"))
            if storage_block is not None else None
        ),
        delivery=(
            ModuleConfig(module=delivery_block.get("module", "delivery_module"))
            if delivery_block is not None else None
        ),
        logosctl=_load_logosctl(raw),
        interval_minutes=raw.get("collector", {}).get("interval_minutes", 10),
        database=raw.get("collector", {}).get("database", "data/snapshots.db"),
        refresh_seconds=int(raw.get("dashboard", {}).get("refresh_seconds", 30)),
    )


def default_config_path(project_root: Path) -> Path:
    """Prefer the untracked config.local.yaml (per-machine) over the tracked template."""
    local_config = project_root / "config.local.yaml"
    return local_config if local_config.exists() else project_root / "config.yaml"
