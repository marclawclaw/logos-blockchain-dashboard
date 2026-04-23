"""Dashboard Flask API endpoints."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import sys

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")


def get_db_path() -> str:
    """Return the configured database path as an absolute path.
    
    Resolves relative to the project root (parent of this file's parent)
    to work regardless of the current working directory.
    """
    from collector.config import load
    try:
        cfg = load()
        db = cfg.database
    except Exception:
        db = "data/snapshots.db"
    
    # Make absolute relative to project root (parent of dashboard/)
    if not os.path.isabs(db):
        project_root = Path(__file__).parent.parent
        db = str(project_root / db)
    return db


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
    # hours=0 means Max — return all available snapshots (no time filter)
    since = 0 if hours == 0 else int(time.time()) - (hours * 3600)
    rows = get_snapshots_since(db_path, since)
    return jsonify({"snapshots": rows, "count": len(rows)})


@api.route("/health", methods=["GET"])
def health():
    """Simple liveness check."""
    return jsonify({"status": "ok"})
