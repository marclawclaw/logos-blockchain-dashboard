# Logos Node Observer Dashboard — Specification

## Overview

Standalone read-only dashboard for Logos blockchain node operators. Shows node health, blockchain state, and wallet balances with historical graphs.

**Repository:** `~/src/marclawclaw/logos-blockchain-dashboard`

## Architecture

```
[Logos Node APIs]
       ↓
[Python Collector — every 10 min]
       ↓
[SQLite — historical snapshots]
       ↓
[Web Dashboard — auto-refresh 5s when open]
```

## API Surface

### Axum HTTP API (REST)
| Endpoint | Method | Data |
|----------|--------|------|
| `/cryptarchia/info` | GET | Consensus state (tip, LIB, epoch) |
| `/cryptarchia/blocks` | GET | Blocks by slot range |
| `/network/info` | GET | Peer count, connection info |
| `/mantle/metrics` | GET | Mempool depth, tx rate |
| `/wallet/:public_key/balance` | GET | Wallet balance |

### Sequencer RPC (JSON-RPC 2.0, POST /)
| Method | Data |
|--------|------|
| `get_last_block` | Chain height |
| `get_block` | Block by ID |
| `get_account_balance` | Account balance |

## Data Collected (10-min snapshots)

| Metric | Source |
|--------|--------|
| Chain tip height | `/cryptarchia/info` or `get_last_block` |
| Last Irreversible Block | `/cryptarchia/info` |
| Epoch | `/cryptarchia/info` |
| Blocks since last snapshot | Derived from height delta |
| Mempool depth | `/mantle/metrics` |
| Peer count | `/network/info` |
| Wallet 1 balance | `/wallet/:public_key/balance` |
| Wallet 2 balance | `/wallet/:public_key/balance` |

## Dashboard Sections

1. **Chain Overview** — Current tip height, LIB, epoch, block interval chart
2. **Mempool** — Pending tx count, depth over time graph
3. **Network** — Peer count over time
4. **Wallets** — Balance for both wallets, balance history graph

## Project Structure

```
logos-blockchain-dashboard/
├── SPEC.md
├── config.yaml          # Node API URL, wallet addresses
├── collector/
│   ├── __init__.py
│   ├── main.py          # Entry point, 10-min cron loop
│   ├── fetcher.py       # API calls to Logos node
│   └── db.py            # SQLite schema and writes
├── dashboard/
│   ├── app.py           # Flask/FastAPI server
│   ├── static/
│   │   ├── css/
│   │   └── js/
│   └── templates/
│       └── index.html
└── requirements.txt
```

## Config (`config.yaml`)

```yaml
node:
  axum_url: "http://localhost:8080"  # Axum HTTP API
  rpc_url: "http://localhost:8080"   # JSON-RPC endpoint

wallets:
  - name: "wallet_1"
    address: "..."  # Base58 public address
  - name: "wallet_2"
    address: "..."  # Base58 public address

collector:
  interval_minutes: 10
  database: "data/snapshots.db"

dashboard:
  host: "0.0.0.0"
  port: 8081
  refresh_seconds: 5
```

## Two-Tier Refresh

- **Page open:** Dashboard polls node APIs directly every 5 seconds (live view)
- **Page closed:** Collector stores snapshots every 10 minutes to SQLite
- **Historical graphs:** Read from SQLite (10-min resolution)

## Retention

- 30 days of snapshots in SQLite
- Pruning handled by collector on startup

## Out of Scope (MVP)

- Authentication
- Alerting / notifications
- Multi-node aggregation
- Transaction submission
- Docker deployment

## Out of Scope (Always)

- Write operations (read-only dashboard)
