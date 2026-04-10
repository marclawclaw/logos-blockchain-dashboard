# Spec: Logos Node Observer Dashboard

## Objective

**What:** A read-only web dashboard for Logos blockchain node operators showing node health, blockchain state, and wallet balances with historical graphs.

**Who:** Logos testnet node operators who currently use `curl` commands to monitor their nodes.

**Success:** An operator can open the dashboard and understand the health of their node, the state of the blockchain from their node's view, and their wallet balances — without any `curl` commands.

**User flows:**
1. Operator opens dashboard → sees live data refreshing every 5s
2. Operator closes tab → collector stores 10-min snapshots
3. Operator returns days later → sees historical graphs of all metrics
4. Operator deploys to new node → config auto-detected from `user_config.yaml`

## Tech Stack

- **Collector:** Python 3.10+, `requests`, `sqlite3` (stdlib)
- **Dashboard:** Python 3.10+, Flask, Chart.js (CDN)
- **Storage:** SQLite (auto-created at `data/snapshots.db`)
- **Config:** YAML (`config.yaml` + auto-parse of `user_config.yaml`)
- **No external DB, no build step, no Node.js**

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Initialise database
python -m collector init-db

# Run collector (10-min loop, foreground)
python -m collector run

# Run collector (background, cron-friendly)
python -m collector run --daemon

# Run dashboard
python -m dashboard

# Run both (for local dev)
python -m collector run --daemon & python -m dashboard

# Docker
docker build -t logos-dashboard .
docker run -v ~/.config/logos-node:/node-config logos-dashboard
```

## Project Structure

```
logos-blockchain-dashboard/
├── SPEC.md                    # This file
├── README.md                  # Setup instructions
├── requirements.txt           # PyYAML, requests, flask
├── config.yaml                # Own config (API URL, wallet overrides)
├── data/                     # SQLite DB created at runtime
│   └── .gitkeep
├── collector/
│   ├── __init__.py
│   ├── main.py               # Entry point, CLI, cron loop
│   ├── fetcher.py            # API calls to Logos node
│   ├── db.py                 # SQLite schema + writes
│   └── config.py             # Config loading + yaml parsing
└── dashboard/
    ├── __init__.py
    ├── app.py                # Flask app
    ├── api.py                # /api/* endpoints
    └── templates/
        └── index.html        # Single-page dashboard
```

## Code Style

**Python:**
- Type hints on all public functions
- Docstrings on modules and classes, not methods
- Errors logged with context, never silently swallowed
- Example fetcher function:

```python
def fetch_cryptarchia_info(base_url: str) -> dict:
    """Fetch consensus state from /cryptarchia/info endpoint."""
    url = f"{base_url}/cryptarchia/info"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()
```

**YAML config:**
```yaml
# Auto-detect wallets + API URL from node config
node_config_path: "~/.config/logos-node/user_config.yaml"

# Or manual override
node:
  axum_url: "http://localhost:38437"

wallets:
  - name: "voucher"
    address: "e59ffc735020e875982dcf84906738224aae576ea9119714a6d1d44de96f6d16"
```

**SQLite schema:**
```sql
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,          -- Unix epoch
    chain_tip INTEGER NOT NULL,
    lib INTEGER NOT NULL,
    epoch INTEGER,
    blocks_produced INTEGER DEFAULT 0,   -- Delta since last snapshot
    mempool_depth INTEGER DEFAULT 0,
    peer_count INTEGER DEFAULT 0,
    wallet_balances TEXT NOT NULL,       -- JSON: {"wallet_1": balance, "wallet_2": balance}
    UNIQUE(timestamp)                    -- One snapshot per 10-min interval
);
CREATE INDEX idx_snapshots_timestamp ON snapshots(timestamp);
```

## Testing Strategy

**Collector tests:**
- Mock HTTP responses, verify correct data stored in SQLite
- Test config parsing: yaml auto-detect + manual override
- Test retention pruning (rows older than 30 days deleted on startup)

**Dashboard API tests:**
- Mock SQLite responses, verify JSON structure returned
- Test 404 on unknown endpoint

**Frontend:**
- Manual testing only for MVP (Chart.js renders, live refresh works)
- No JS test framework

**Test locations:** `collector/test_*.py`, `dashboard/test_*.py`

## Boundaries

**Always:**
- Parse `user_config.yaml` automatically if present
- Log API errors with URL and response code
- Graceful degradation if any API endpoint fails (show last known value)
- Store timestamps as Unix epoch integers in SQLite
- Dashboard port defaults to `8282`

**Ask first:**
- Adding new API endpoints to poll
- Changing snapshot interval (10 min is deliberate)
- Adding external dependencies beyond PyYAML, requests, Flask
- Schema changes to snapshots table

**Never:**
- Write to the blockchain or submit transactions
- Hard-code wallet addresses
- Expose private keys or signing keys
- Commit `data/snapshots.db` (gitignored)

## Success Criteria

- [ ] `python -m collector init-db` creates `data/snapshots.db` with correct schema
- [ ] `python -m collector run` polls all APIs and stores snapshot every 10 min
- [ ] `python -m dashboard` serves dashboard on port 8282
- [ ] Dashboard shows: chain tip, LIB, mempool depth, peer count, wallet balances
- [ ] Historical graphs show at least 24 hours of data when page is open
- [ ] Dashboard auto-refreshes every 5s when tab is visible, stops when hidden
- [ ] Config auto-detects API URL and wallet addresses from `user_config.yaml`
- [ ] Collector logs errors without crashing when an API is temporarily unavailable
- [ ] README has clear setup instructions for a new node operator

## Open Questions

1. Should the collector also poll Sequencer RPC (`get_last_block`) or is `/cryptarchia/info` sufficient for chain tip?
2. Chart types: line graphs for all time-series data, or are there specific preferences?
3. Retention policy: 30 days OK, or should it be configurable?
4. Docker: is this a hard MVP requirement or post-MVP?
