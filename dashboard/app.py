"""Dashboard Flask application."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

from flask import Flask, render_template, send_from_directory

from collector.config import Config, default_config_path, load

logger = logging.getLogger(__name__)


def create_app(cfg: Optional[Config] = None) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["JSON_SORT_KEYS"] = False

    # Load the config once at startup.
    # Use an absolute path (project root) so it resolves regardless of CWD —
    # main() does os.chdir(dashboard_dir) before create_app() runs. Prefer the
    # untracked config.local.yaml (per-machine) over the tracked config.yaml
    # template. A broken config stops the dashboard: showing a half-configured
    # page would hide the mistake.
    if cfg is None:
        cfg = load(str(default_config_path(Path(__file__).parent.parent)))
    app.config["CONFIG"] = cfg

    from . import api as api_module
    from .api import api
    from .live import LiveCache
    app.config["LIVE"] = LiveCache(cfg, lambda: api_module.get_db_path())

    if cfg.blockchain is not None:
        node_url = cfg.blockchain.axum_url
        app.config["NODE_URL"] = node_url
        app.config["NODE_CONFIG_PATH"] = cfg.blockchain.node_config_path
        app.config["BLEND_KEYS"] = cfg.blockchain.blend

        # Proxy: /api/proxy/<path> → Flask forwards to the blockchain node.
        # The page no longer uses it (current values come from /api/live); it is
        # kept for operators who query the node through the dashboard's origin.
        @app.route("/api/proxy/<path:path>", methods=["GET", "POST"])
        def proxy(path):
            import requests
            from flask import request
            url = f"{node_url}/{path}"
            try:
                if request.method == "POST":
                    resp = requests.request(request.method, url, json=request.json,
                                            timeout=10)
                else:
                    resp = requests.get(url, timeout=10)
                return resp.json(), resp.status_code
            except Exception as e:
                logger.warning("Proxy error %s -> %s: %s", request.path, url, e)
                return {"error": str(e)}, 502

    # Register API blueprint (SQLite-based endpoints)
    app.register_blueprint(api)

    @app.route("/")
    def index():
        wallets = [w.name for w in cfg.blockchain.wallets] if cfg.blockchain else []
        return render_template(
            "index.html",
            kinds=cfg.kinds,
            wallets=wallets,
            refresh_seconds=cfg.refresh_seconds,
            interval_seconds=cfg.interval_minutes * 60,
        )

    @app.route("/static/refresh.js")
    def refresh_js():
        """Serve the visibility polling module as an ES module from the static dir."""
        return send_from_directory(Path(__file__).parent / "static", "refresh.js",
                                   mimetype="application/javascript")

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Logos Node Observer Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8282, help="Port to bind to")
    args = parser.parse_args()

    # Change to dashboard directory so templates resolve
    dashboard_dir = Path(__file__).parent
    os.chdir(dashboard_dir)

    app = create_app()
    logger.info("Dashboard starting on http://%s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
