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


@api.route("/blend/status", methods=["GET"])
def blend_status():
    """Blend Network stats, this node's core declaration, and an IP drift check."""
    import requests
    from flask import current_app
    from . import blend

    node_url = current_app.config.get("NODE_URL", "http://127.0.0.1:8080")

    def node_get(path):
        try:
            r = requests.get(f"{node_url}{path}", timeout=10)
            return r.json() if r.ok else None
        except Exception as e:
            logger.warning("Blend status: GET %s failed: %s", path, e)
            return None

    declarations = node_get("/mantle/sdp/declarations")
    if declarations is None:
        return jsonify({"error": "Node SDP declarations unavailable"}), 502

    overrides = current_app.config.get("BLEND_KEYS") or {}
    zk_id, provider_id = blend.our_blend_keys(current_app.config.get("NODE_CONFIG_PATH"))
    summary = blend.summarize(
        declarations,
        node_get("/blend/info"),
        overrides.get("zk_id") or zk_id,
        overrides.get("provider_id") or provider_id,
        blend.detect_public_ip(),
    )
    return jsonify(summary)


@api.route("/health", methods=["GET"])
def health():
    """Simple liveness check."""
    return jsonify({"status": "ok"})
