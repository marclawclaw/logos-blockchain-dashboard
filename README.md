# Logos Node Observer Dashboard

A read-only web dashboard for Logos blockchain node operators. Shows node health, blockchain state, and wallet balances with historical graphs — no `curl` commands required.

## What it shows

| Metric | Description |
|--------|-------------|
| **Chain tip** | Current block height of the node's view of the chain |
| **LIB** | Last Irreversible Block hash |
| **Mode** | Node operating mode: "Bootstrapping" while syncing, "Normal" when caught up |
| **Blocks produced** | New blocks produced by this node since the last snapshot (10-min interval) |
| **Mempool depth** | Number of pending items in the mempool |
| **Peer count** | Number of active peer connections |
| **Wallet balances** | Balance for each configured wallet address |

Dashboard refreshes every 5 seconds when the tab is visible, stops when hidden. Historical graphs show up to 90 days of data.

---

## Setup

### Prerequisites

- Python 3.10 or later
- pip
- A running Logos node with the REST API enabled (default port varies; auto-detected from node config)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Initialise the database

```bash
python -m collector init-db
```

This creates `data/snapshots.db` with the snapshots table.

### 3. Configure

The collector auto-detects your node's API URL and wallet addresses from the standard Logos node config file (`~/.config/logos-node/user_config.yaml`). No configuration is needed for a standard setup.

If you need manual overrides, edit `config.yaml`:

```yaml
# Auto-detect API URL and wallets from user_config.yaml (default)
node_config_path: "~/.config/logos-node/user_config.yaml"

# Manual API URL override (uncomment and edit if needed)
# node:
#   axum_url: "http://localhost:38437"

# Manual wallet list override (uncomment and edit if needed)
# wallets:
#   - name: "voucher"
#     address: "e59ffc735020e875982dcf84906738224aae576ea9119714a6d1d44de96f6d16"
```

### 4. Run the collector

The collector polls the node every 10 minutes and stores snapshots. Run it in the background:

```bash
python -m collector run --daemon
```

Or foreground (for debugging):

```bash
python -m collector run
```

Collector exit codes: `0` normal, `1` fatal error (e.g., database write failure after retry).

### 5. Run the dashboard

```bash
python -m dashboard
```

Dashboard available at: **http://localhost:8282**

---

## Configuration details

### Auto-detection

When `node_config_path` points to a valid `user_config.yaml`:

- **API URL** — extracted from `api.backend.listen_address` (port number used with `http://localhost:<port>`)
- **Wallets** — all public keys in `wallet.known_keys` are added automatically

### Manual wallet addresses

Add wallets to `config.yaml` under `wallets`:

```yaml
wallets:
  - name: "voucher"
    address: "e59ffc735020e875982dcf84906738224aae576ea9119714a6d1d44de96f6d16"
  - name: "funding"
    address: "another_base58_address_here"
```

Manual wallet entries replace the auto-detected list entirely.

### Snapshot interval

Change the polling interval in `config.yaml`:

```yaml
collector:
  interval_minutes: 5
```

Default: 10 minutes. Snapshots are truncated to the nearest 10-minute window, so shorter intervals increase storage.

---

## Deploy on a new node

1. Copy this project directory to the node
2. `pip install -r requirements.txt`
3. `python -m collector init-db`
4. `python -m collector run --daemon`
5. `python -m dashboard &`
6. Open `http://<node-ip>:8282`

The collector and dashboard can run on a different machine than the node as long as the node's REST API is accessible at the configured URL.

---

## Docker (post-MVP)

Docker support is planned for after the initial release.

```bash
docker build -t logos-dashboard .
docker run -p 8282:8282 \
  -v /home/user/.config/logos-node:/node-config \
  logos-dashboard
```

---

## Testing

```bash
pip install pytest requests
pytest tests/ -v
```

Collector tests mock all HTTP responses — no network calls are made. Dashboard tests use an in-memory temporary database.

---

## File structure

```
logos-blockchain-dashboard/
├── collector/          # Polls the node, stores snapshots
│   ├── config.py       # Config loading + user_config.yaml parsing
│   ├── db.py           # SQLite schema, writes, reads, pruning
│   ├── fetcher.py      # REST API calls to the Logos node
│   └── main.py         # CLI entry point and 10-min loop
├── dashboard/          # Flask web dashboard
│   ├── api.py          # JSON API endpoints
│   ├── app.py          # Flask application
│   └── templates/
│       └── index.html  # Single-page dashboard (Chart.js via CDN)
├── data/               # SQLite database (gitignored)
├── tests/
│   ├── collector/
│   │   └── test_collector.py
│   └── dashboard/
│       └── test_api.py
├── config.yaml         # Own configuration
├── requirements.txt    # PyYAML, requests, flask
└── README.md
```

---

## Retention

Snapshots older than 90 days are automatically deleted on collector startup and after each snapshot write.

---

## Troubleshooting

**Dashboard shows "Node unreachable"**

The collector is not running, or the API URL in `config.yaml` does not match the node's actual REST endpoint. Check that the Logos node's API is listening and accessible.

**Wallet balances show "—"**

The wallet address is not registered with the node's wallet service. This is normal for addresses that have not been imported into the node.

**"Collecting data — first snapshot in approximately 10 minutes"**

Normal on first run. The collector stores the first snapshot after the first poll interval (10 minutes by default).

**Empty historical graphs**

Historical data accumulates over time. Graphs become meaningful after a few hours of collector operation.
