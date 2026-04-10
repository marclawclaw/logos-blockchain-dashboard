"""Dashboard Flask application."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from flask import Flask, render_template, send_from_directory

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["JSON_SORT_KEYS"] = False

    # Load the Logos node URL once at startup
    try:
        from collector.config import load
        cfg = load()
        node_url = cfg.axum_url
    except Exception:
        node_url = "http://localhost:38437"

    # Proxy: browser calls /api/proxy/<path> → Flask forwards to Logos node
    # This avoids CORS since browser always talks to the same origin (port 8282)
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
    from .api import api
    app.register_blueprint(api)

    @app.route("/")
    def index():
        return render_template("index.html")

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
