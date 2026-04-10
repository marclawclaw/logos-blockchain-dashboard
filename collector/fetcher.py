"""API fetcher: poll Logos node REST endpoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


@dataclass
class CryptarchiaInfo:
    lib: str
    tip: str
    slot: int
    height: int
    mode: str


@dataclass
class NetworkInfo:
    n_peers: int
    n_connections: int
    n_pending_connections: int


@dataclass
class MempoolMetrics:
    pending_items: int
    last_item_timestamp: int


@dataclass
class WalletBalance:
    balance: int


@dataclass
class FetchResult:
    """All metrics fetched in one interval."""

    chain_tip: Optional[int] = None
    lib: Optional[str] = None  # hash string, not numeric
    mode: Optional[str] = None  # "Bootstrapping", "Normal", etc.
    epoch: Optional[int] = None
    mempool_depth: int = 0
    peer_count: int = 0
    n_connections: int = 0
    wallet_balances: dict[str, Optional[int]] = None  # {name: balance}

    def __post_init__(self):
        if self.wallet_balances is None:
            self.wallet_balances = {}


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def _get(base_url: str, path: str, timeout: int = 10) -> Optional[dict]:
    """GET an endpoint. Return None on failure (caller handles gracefully)."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.warning("GET %s failed: %s", url, e)
        return None
    except (ValueError, TypeError) as e:
        logger.warning("GET %s returned malformed JSON: %s", url, e)
        return None


def _post(base_url: str, path: str, data: dict, timeout: int = 10) -> Optional[dict]:
    """POST to an endpoint. Return None on failure."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        resp = requests.post(url, json=data, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.warning("POST %s failed: %s", url, e)
        return None
    except (ValueError, TypeError) as e:
        logger.warning("POST %s returned malformed JSON: %s", url, e)
        return None


def fetch_cryptarchia_info(base_url: str) -> Optional[CryptarchiaInfo]:
    """Fetch consensus state from /cryptarchia/info."""
    data = _get(base_url, "/cryptarchia/info")
    if data is None:
        return None
    try:
        return CryptarchiaInfo(
            lib=data["lib"],
            tip=data["tip"],
            slot=int(data["slot"]),
            height=int(data["height"]),
            mode=str(data.get("mode", "unknown")),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("/cryptarchia/info returned unexpected structure: %s (%s)", data, e)
        return None


def fetch_network_info(base_url: str) -> Optional[NetworkInfo]:
    """Fetch network state from /network/info."""
    data = _get(base_url, "/network/info")
    if data is None:
        return None
    try:
        return NetworkInfo(
            n_peers=int(data["n_peers"]),
            n_connections=int(data["n_connections"]),
            n_pending_connections=int(data["n_pending_connections"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("/network/info returned unexpected structure: %s (%s)", data, e)
        return None


def fetch_mempool_metrics(base_url: str) -> Optional[MempoolMetrics]:
    """Fetch mempool metrics from /mantle/metrics."""
    data = _get(base_url, "/mantle/metrics")
    if data is None:
        return None
    try:
        return MempoolMetrics(
            pending_items=int(data["pending_items"]),
            last_item_timestamp=int(data["last_item_timestamp"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("/mantle/metrics returned unexpected structure: %s (%s)", data, e)
        return None


def fetch_wallet_balance(base_url: str, name: str, address: str) -> Optional[int]:
    """Fetch a single wallet's balance.

    Returns None on failure. A 200 with 'address not found' is treated as failure
    (balance unknown).
    """
    # The API returns 200 with an error message if not found,
    # or a balance string/number if found.
    url = f"{base_url.rstrip('/')}/wallet/{address}/balance"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            logger.debug("Wallet %s (%s) not found in wallet service", name, address)
            return None
        resp.raise_for_status()
        text = resp.text.strip()
        # Empty body or error message means not found
        if not text or text.startswith("The requested"):
            return None
        # Try to parse as JSON first
        try:
            body = resp.json()
            balance = int(body["balance"])
            return balance
        except (KeyError, ValueError, TypeError):
            # Fallback: treat response text as raw balance
            return int(text)
    except requests.RequestException as e:
        logger.warning("GET %s failed: %s", url, e)
        return None


# ---------------------------------------------------------------------------

def fetch_latest_block(base_url: str, slot: int) -> Optional[str]:
    """Fetch the block producer (leader key) for a given slot.

    Returns the leader's public key as a hex string, or None on failure.
    The Logos block structure: Block.header.proof_of_leadership.leader_key
    """
    url = f"{base_url.rstrip('/')}/cryptarchia/blocks?slot_from={slot}&to_slot={slot}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 400:
            return None
        resp.raise_for_status()
        blocks = resp.json()
        if not blocks or not isinstance(blocks, list) or len(blocks) == 0:
            return None
        block = blocks[0]
        # Navigate: header -> proof_of_leadership -> leader_key
        header = block.get("header") or block.get("Header")
        if not header:
            # Try raw block if header not present
            return None
        leader_proof = header.get("proof_of_leadership") or header.get("leader_proof") or {}
        return leader_proof.get("leader_key") or leader_proof.get("leaderPublicKey")
    except (requests.RequestException, ValueError, TypeError) as e:
        logger.warning("GET %s failed: %s", url, e)
        return None


# Combined fetch
# ---------------------------------------------------------------------------


def fetch_all(base_url: str, wallet_names: list[tuple[str, str]]) -> FetchResult:
    """Fetch all metrics in one interval.

    Args:
        base_url: Base URL of the Logos node API (e.g. http://localhost:38437)
        wallet_names: List of (name, address) tuples for wallets to query.

    Returns:
        FetchResult with whatever fields were successfully retrieved.
        Failed endpoints leave their fields at defaults (None or 0).
    """
    result = FetchResult()

    info = fetch_cryptarchia_info(base_url)
    if info:
        result.chain_tip = info.height
        result.lib = info.lib  # hash string
        result.mode = info.mode
        result.epoch = info.slot  # slot is close enough to epoch for now

    net = fetch_network_info(base_url)
    if net:
        result.peer_count = net.n_peers
        result.n_connections = net.n_connections

    mempool = fetch_mempool_metrics(base_url)
    if mempool:
        result.mempool_depth = mempool.pending_items

    result.wallet_balances = {}
    for name, address in wallet_names:
        balance = fetch_wallet_balance(base_url, name, address)
        result.wallet_balances[name] = balance

    return result
