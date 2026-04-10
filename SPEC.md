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
5. Operator selects a time scale (1h | 1d | 1w | 1m | Max) → historical charts re-fetch and re-render for that window

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
```

> **Docker:** Post-MVP. Remove this once implemented.
> ```bash
> docker build -t logos-dashboard .
> docker run -p 8282:8282 -v /home/user/.config/logos-node:/node-config logos-dashboard
> ```

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
├── dashboard/
│   ├── __init__.py
│   ├── app.py                # Flask app
│   ├── api.py                # /api/* endpoints
│   └── templates/
│       └── index.html        # Single-page dashboard
└── tests/
    ├── collector/
    │   └── test_collector.py
    └── dashboard/
        └── test_api.py
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
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected response type from {url}: {type(data)}")
    return data
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
    timestamp INTEGER NOT NULL,          -- Unix epoch (UTC)
    chain_tip INTEGER NOT NULL,
    lib TEXT NOT NULL,                   -- Last Irreversible Block hash (string)
    mode TEXT,                           -- Node mode: "Bootstrapping", "Normal", etc.
    epoch INTEGER,
    mempool_depth INTEGER DEFAULT 0,
    peer_count INTEGER DEFAULT 0,
    n_connections INTEGER DEFAULT 0,
    block_producer TEXT,                  -- Public key of the most recent block's producer
    wallet_balances TEXT NOT NULL,        -- JSON: {"voucher": balance, "funding": balance}
    UNIQUE(timestamp)                    -- One snapshot per 10-min interval
);
CREATE INDEX idx_snapshots_timestamp ON snapshots(timestamp);
```

## Snapshot and Data Logic

**Timestamp:** Unix epoch integer (UTC). The 10-minute window aligns to `now // 600 * 600` (truncated to nearest 10 min).

**Upsert policy:** `INSERT OR REPLACE INTO snapshots ...` — if a snapshot for the same 10-min window already exists, replace it. This handles collector restarts gracefully.

**Retention:** Rows with `timestamp < (now - 90 days)` are deleted on collector startup and after each snapshot write.

**Timestamps in database:** Always UTC Unix epoch integers.
**Timestamps in dashboard:** Always rendered in the browser's local timezone.

## Config Loading

**Auto-detect from `user_config.yaml`:**
1. Read `node_config_path` from `config.yaml` (default: `~/.config/logos-node/user_config.yaml`)
2. Parse `api.backend.listen_address` → extract port → set `axum_url`
3. Parse `wallet.known_keys` → extract all public key addresses → populate wallet list
4. If `wallet` section exists in `config.yaml`, merge and override (allows renaming, filtering)
5. If `wallets` is empty after auto-parse, log a warning and continue with empty list

**Missing `user_config.yaml`:** If the file doesn't exist and no manual `node.axum_url` is set in `config.yaml`, the collector raises a `ConfigError` on startup with a clear message:
```
Error: No API URL configured. Set node_config_path in config.yaml or specify node.axum_url manually.
```

**Wallet validation:** If a wallet address in the config is not a valid base58 string, log a warning and skip that wallet.

## Failure Modes

**Single API endpoint fails:**
- Log error with URL and HTTP status or exception message
- That metric is recorded as `null`/`None` for this interval
- Snapshot still written with other metrics
- Dashboard shows last known value for that metric (from most recent non-null snapshot)

**All API endpoints fail (first run, no prior snapshot):**
- Dashboard shows an error banner: "Node unreachable — cannot connect to APIs. Check node status and config."
- Historical graphs show "No data" state

**All API endpoints fail (subsequent runs, prior snapshots exist):**
- Snapshot is skipped entirely (not written)
- Dashboard continues to show last known values

**Database write fails:**
- Collector logs the error
- Collector retries once after 30 seconds
- If retry fails, collector logs a fatal error and exits with code 1
- Dashboard remains functional (serves from last successful snapshot)

**Malformed API response (HTTP 200 but bad JSON, missing fields, wrong types):**
- `resp.json()` raises `JSONDecodeError` → treated as endpoint failure (see above)
- Response has unexpected structure (e.g., `null` for a required field) → treated as endpoint failure
- Wallet balance not a number → log warning, record as `null`

## Empty State (First Run)

**0 snapshots in database:**
- Dashboard banner: "Collecting data — first snapshot in approximately 10 minutes"
- All current-value panels show "—" (dashes) until first snapshot arrives
- Historical graphs render as empty charts with axes but no data lines

**< 24 hours of data:**
- Historical graphs display all available data
- If < 1 hour of data: show banner "Very little data — graphs become meaningful after a few hours"

## Chart Types

All time-series data rendered as **line charts** using Chart.js. One chart per metric:

| Metric | Y-axis | X-axis |
|--------|--------|--------|
| Chain tip height | Block height | Time |
| LIB height | Block height | Time |
| Mempool depth | Tx count | Time |
| Peer count | Peer count | Time |
| Block producer | Count per leader | Time (stacked bar) |
| Wallet balances | Native token units | Time |

### Time Scale Selector

**Feature:** Time Scale Selector

The user selects a time window for the historical charts. The live panel (5-second refresh) is completely independent and always shows the latest node data regardless of the selected window.

**Options:**

| Button | `hours` param | Meaning |
|--------|--------------|---------|
| `1h`   | `1`          | Last 1 hour |
| `1d`   | `24`         | Last 24 hours (default) |
| `1w`   | `168`        | Last 168 hours (7 days) |
| `1m`   | `720`        | Last 720 hours (30 days) |
| `Max`  | `0`          | All available data (up to 90 days) |

**Implementation:**

- **Location:** A button group rendered in the dashboard header, visually grouped and aligned (e.g., inline after the title or in a dedicated toolbar row).
- **Default:** `1d` is selected on first load.
- **Active state:** The selected button has a distinct visual style (e.g., accent background or border) to indicate the current selection.
- **On selection change:**
  1. Clear all historical chart data (reset datasets to empty arrays).
  2. Issue `GET /api/snapshots?hours=N` where N is the numeric value from the table above.
  3. Re-populate charts with the returned snapshots.
- **Max (`hours=0`):** The API returns all snapshots regardless of age (up to the 90-day retention limit). The `since` parameter is set to `0` to disable the time filter server-side.
- **Live panel unaffected:** The 5-second polling loop that fetches from the node APIs (via `/api/proxy/*`) runs independently of the time scale selection. Changing the time scale does not restart or alter that polling loop.
- **Initial load:** On page load, the default `1d` window is fetched before charts are populated.

## Testing Strategy

**Collector tests (`tests/collector/`):**
- Mock HTTP responses with `unittest.mock.patch`
- Verify correct data stored in SQLite
- Test config parsing: yaml auto-detect + manual override
- Test retention pruning (rows older than 90 days deleted on startup)
- Test malformed response handling (bad JSON, null fields)

**Dashboard API tests (`tests/dashboard/`):**
- Mock SQLite responses
- Verify JSON structure returned by each endpoint
- Test 404 on unknown endpoint
- Test empty-state response

**Frontend:** Manual testing only for MVP.

## Boundaries

**Always:**
- Parse `user_config.yaml` automatically if present
- Log API errors with URL and response code
- Graceful degradation if any API endpoint fails (show last known value)
- Store timestamps as Unix epoch integers in SQLite (UTC)
- Dashboard port defaults to `8282`
- Render timestamps in browser's local timezone
- Use `INSERT OR REPLACE` for snapshot writes
- Prune snapshots older than 90 days on startup and after each write
- Line charts for all time-series data

**Ask first:**
- Adding new API endpoints to poll
- Changing snapshot interval (10 min is deliberate)
- Adding external dependencies beyond PyYAML, requests, Flask
- Schema changes to snapshots table
- Switching chart library

**Never:**
- Write to the blockchain or submit transactions
- Hard-code wallet addresses
- Expose private keys or signing keys
- Commit `data/snapshots.db` (gitignored)
- Change the time scale for the live panel (always shows latest snapshot)

## Success Criteria

- [ ] `python -m collector init-db` creates `data/snapshots.db` with correct schema
- [ ] `python -m collector run` polls all APIs and stores a snapshot every 10 min (aligned to 10-min boundary)
- [ ] `python -m collector run --daemon` survives restart; duplicate 10-min windows use `INSERT OR REPLACE`
- [ ] `python -m dashboard` serves dashboard on port 8282
- [ ] Dashboard shows: chain tip, LIB, epoch, mempool depth, peer count, wallet balances, block producer
- [ ] Dashboard auto-refreshes every 5s when tab is visible, stops when hidden
- [ ] Config auto-detects API URL from `api.backend.listen_address` in `user_config.yaml`
- [ ] Config auto-detects wallet addresses from `wallet.known_keys` in `user_config.yaml`
- [ ] Collector logs errors without crashing when an API is temporarily unavailable
- [ ] Malformed API responses are logged and treated as endpoint failures, not crashes
- [ ] First run (0 snapshots): dashboard shows "Collecting data" banner and "—" for current values
- [ ] Historical graphs display all available data up to 90 days; empty charts when no data
- [ ] Time scale selector (1h | 1d | 1w | 1m | Max) filters historical chart data; default is 1d; live panel unaffected
- [ ] Timestamps stored as UTC Unix epoch; rendered in browser's local timezone
- [ ] Snapshot retention: 90 days — older rows pruned on startup and after each write
- [ ] README has clear setup instructions for a new node operator
