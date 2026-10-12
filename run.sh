#!/usr/bin/env bash
set -e
cd /app
# Ensure DB schema (idempotent; also migrates older databases)
python -m collector init-db --db data/snapshots.db
# Start collector loop in background, dashboard in foreground.
# Both read config.local.yaml if it is mounted, else the bundled config.yaml.
python -m collector run --db data/snapshots.db &
COLLECTOR_PID=$!
trap "kill $COLLECTOR_PID 2>/dev/null" TERM INT
exec python -m dashboard --host 0.0.0.0 --port 8282
