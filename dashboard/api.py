"""Dashboard Flask API endpoints."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")


def get_db_path() -> str:
    """Return the configured database path, defaulting to data/snapshots.db."""
    # Import here to avoid circular imports at module level
    from collector.config import load
    try:
        cfg = load()
        return cfg.database
    except Exception:
        return os.environ.get("DASHBOARD_DB", "data/snapshots.db")


@api.route("/snapshot/latest", methods=["GET"])
def snapshot_latest():
    """Return the most recent snapshot."""
    from collector.db import get_latest_snapshot
    db_path = get_db_path()
    snapshot = get_latest_snapshot(db_path)
    if snapshot is None:
        return jsonify({"error": "No snapshots yet"}), 404
    snapshot["_ts"] = snapshot.pop("timestamp")
    return jsonify(snapshot)


@api.route("/snapshots", methods=["GET"])
def snapshots():
    """Return snapshots from the last N hours (default 24)."""
    from collector.db import get_snapshots_since
    db_path = get_db_path()
    hours = int(request.args.get("hours", 24))
    since = int(time.time()) - (hours * 3600)
    rows = get_snapshots_since(db_path, since)
    return jsonify({"snapshots": rows, "count": len(rows)})


@api.route("/health", methods=["GET"])
def health():
    """Simple liveness check."""
    return jsonify({"status": "ok"})
