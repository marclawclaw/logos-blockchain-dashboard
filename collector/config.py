"""Config loading: parse config.yaml and auto-detect from user_config.yaml."""

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


class ConfigError(Exception):
    """Raised when config is invalid or missing required fields."""


@dataclass
class Wallet:
    name: str
    address: str


@dataclass
class Config:
    axum_url: str
    wallets: list[Wallet] = field(default_factory=list)
    interval_minutes: int = 10
    database: str = "data/snapshots.db"


def expand_path(path: str) -> Path:
    """Expand ~ and environment variables in a path."""
    return Path(os.path.expandvars(os.path.expanduser(path)))


def load(config_path: Optional[str] = None) -> Config:
    """Load config from config.yaml, auto-detecting from user_config.yaml if present.

    Args:
        config_path: Path to config.yaml. Defaults to ./config.yaml

    Returns:
        Config object with axum_url and wallets populated.

    Raises:
        ConfigError: If no API URL is configured and auto-detect fails.
    """
    if config_path is None:
        config_path = "config.yaml"

    config_file = Path(config_path)
    if not config_file.exists():
        raise ConfigError(
            f"config.yaml not found at {config_path}. "
            "Create it or set node_config_path to point to your node's user_config.yaml."
        )

    with open(config_file) as f:
        raw = yaml.safe_load(f) or {}

    # Resolve node_config_path for auto-detection
    node_config_path = raw.get("node_config_path", "~/.config/logos-node/user_config.yaml")
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

    # Manual override: node.axum_url in config.yaml
    manual_url = raw.get("node", {}).get("axum_url")
    if manual_url:
        axum_url = manual_url

    if not axum_url:
        raise ConfigError(
            "No API URL configured. "
            "Set node_config_path in config.yaml to point to your node's user_config.yaml, "
            "or set node.axum_url manually."
        )

    # Manual wallets override (replace auto-detected)
    wallets: list[Wallet] = []
    manual_wallets = raw.get("wallets", [])
    if manual_wallets:
        for w in manual_wallets:
            if isinstance(w, dict) and "address" in w:
                wallets.append(Wallet(name=w.get("name", "unknown"), address=w["address"]))
            else:
                raise ConfigError(f"Invalid wallet entry: {w}")
    else:
        wallets = auto_wallets

    return Config(
        axum_url=axum_url,
        wallets=wallets,
        interval_minutes=raw.get("collector", {}).get("interval_minutes", 10),
        database=raw.get("collector", {}).get("database", "data/snapshots.db"),
    )
