"""Dashboard Flask application."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from flask import Flask, render_template

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["JSON_SORT_KEYS"] = False

    # Load config for port/host
    try:
        from collector.config import load
        cfg = load()
        port = cfg.interval_minutes  # not used here
        del cfg
    except Exception:
        pass

    # Register API blueprint
    from .api import api
    app.register_blueprint(api)

    @app.route("/")
    def index():
        # Pass the Logos node API URL to the frontend so it can poll live data
        try:
            from collector.config import load
            cfg = load()
            node_url = cfg.axum_url
        except Exception:
            node_url = "http://localhost:38437"
        return render_template("index.html", node_url=node_url)

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
