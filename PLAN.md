# Implementation Plan: Logos Node Observer Dashboard

## Overview

A read-only web dashboard for Logos blockchain node operators, consisting of a Python collector (polls the node every 10 min, stores SQLite snapshots) and a Flask/Chart.js dashboard (serves live + historical data on port 8282). The project is currently empty — only `SPEC.md` exists. All code must be written from scratch.

---

## Architecture Decisions

| Decision | Rationale |
|---|---|
| SQLite schema as the shared contract | Collector writes, dashboard reads; no ORM, raw SQL for predictability |
| `INSERT OR REPLACE` for snapshot upserts | Handles collector restarts without duplicate windows |
| UTC Unix epoch integers in DB, browser-local rendering | Keeps storage simple; avoids timezone edge cases at the storage layer |
| Chart.js from CDN | No build step, matches spec constraint |
| Config auto-detect before manual override | Operator zero-config experience on standard setups |

---

## Dependency Graph

```
config.py         config.py         config.py
    │                 │                 │
    ▼                 ▼                 ▼
db.py           fetcher.py       dashboard/api.py
    │                 │                 │
    ▼                 ▼                 ▼
collector/main.py    │           dashboard/app.py
    │                 │                 │
    ▼                 ▼                 ▼
tests/collector/   tests/        dashboard/templates/index.html
                   dashboard/
```

Collector path: `config → fetcher → db → collector/main` (sequential)
Dashboard path: `db → dashboard/api → dashboard/app → index.html` (sequential)
Tests: parallel to implementation of each module
README: written last, after all modules exist

---

## Task List

### Phase 1: Project Scaffolding

**All files in this phase are independent of each other — safe to write in any order.**

---

#### Task 1: `requirements.txt`

**Description:** Pin the three runtime dependencies (`PyYAML`, `requests`, `flask`) with version constraints. No `pytest` here — test deps added separately.

**Acceptance criteria:**
- [ ] `requirements.txt` exists and contains `PyYAML`, `requests`, `flask`
- [ ] Can `pip install -r requirements.txt` without errors on Python 3.10+

**Verification:**
- `pip install -r requirements.txt && python -c "import yaml, requests, flask; print('OK')"`

**Dependencies:** None

**Files touched:**
- `requirements.txt`

**Estimated scope:** XS

---

#### Task 2: `config.yaml`

**Description:** Create the default `config.yaml` with commented-out `node_config_path` pointing to the standard Logos node config location, and placeholder `wallets` and `node` sections. Use the YAML structure documented in SPEC.md.

**Acceptance criteria:**
- [ ] `config.yaml` exists with `node_config_path` defaulting to `~/.config/logos-node/user_config.yaml`
- [ ] Placeholder `wallets: []` and `node.axum_url` commented out
- [ ] YAML is valid and parseable

**Verification:**
- `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`

**Dependencies:** None

**Files touched:**
- `config.yaml`

**Estimated scope:** XS

---

#### Task 3: `data/.gitkeep` + `.gitignore`

**Description:** Create `data/.gitkeep` (so the directory is committed) and a `.gitignore` that excludes `data/snapshots.db` and any `__pycache__`.

**Acceptance criteria:**
- [ ] `data/` directory tracked, `data/snapshots.db` ignored
- [ ] `__pycache__`, `*.pyc`, `.pytest_cache` ignored

**Verification:**
- `.gitignore` covers `data/snapshots.db`; `git check-ignore` returns non-zero for a new `data/snapshots.db`

**Dependencies:** None

**Files touched:**
- `data/.gitkeep`
- `.gitignore`

**Estimated scope:** XS

---

### Checkpoint: Scaffolding
- [ ] `pip install -r requirements.txt` succeeds
- [ ] `config.yaml` parses cleanly
- [ ] `data/` directory exists and is git-tracked

---

### Phase 2: Collector Foundation

**Collector path is sequential: config → db → fetcher → collector/main**

---

#### Task 4: `collector/config.py`

**Description:** Module to load and validate configuration. Reads `config.yaml`, auto-detects `user_config.yaml` to extract `api.backend.listen_address` → `axum_url` and `wallet.known_keys` → wallet list, then merges with any manual overrides. Raises `ConfigError` (custom exception) on startup if no API URL is available.

**Acceptance criteria:**
- [ ] `load_config()` returns a `Config` dataclass (or similar) with `axum_url: str` and `wallets: list[dict]`
- [ ] Raises `ConfigError` with a clear message if no API URL is set and `user_config.yaml` is absent
- [ ] Logs a warning if `wallet.known_keys` is absent from `user_config.yaml` and no manual wallets are set
- [ ] Skips (logs warning) any wallet address that fails base58 validation
- [ ] All public functions have type hints; module has a docstring

**Verification:**
- [ ] Unit tests: happy-path config load, missing file, invalid wallet address

**Dependencies:** Task 1 (requirements), Task 2 (config.yaml)

**Files touched:**
- `collector/__init__.py`
- `collector/config.py`

**Estimated scope:** S

---

#### Task 5: `collector/db.py`

**Description:** SQLite schema management, snapshot write, snapshot read (latest + historical range), retention pruning. Implements the schema, `compute_blocks_produced`, and `insert_or_replace_snapshot` as documented in SPEC.md.

**Acceptance criteria:**
- [ ] `init_db()` creates `data/snapshots.db` with the full schema (`snapshots` table + index)
- [ ] `write_snapshot()` uses `INSERT OR REPLACE`; deduplicates by 10-min truncated timestamp
- [ ] `get_latest_snapshot()` returns a dict or `None`
- [ ] `get_snapshots_since(ts: int)` returns list of snapshots ordered by timestamp ASC
- [ ] `prune_older_than(ts: int)` deletes rows older than the given cutoff
- [ ] Retention pruning (90 days) is called from `init_db()` on startup
- [ ] All SQL uses parameterized queries (no string interpolation)
- [ ] All public functions have type hints; module has a docstring

**Verification:**
- [ ] `python -m collector init-db` produces a valid SQLite file
- [ ] `sqlite3 data/snapshots.db ".schema"` shows the full schema
- [ ] Unit tests for `compute_blocks_produced` (including first-snapshot edge case), upsert, prune

**Dependencies:** Task 1 (requirements)

**Files touched:**
- `collector/db.py`

**Estimated scope:** S

---

#### Task 6: `collector/fetcher.py`

**Description:** Makes HTTP requests to the Logos node API. One function per endpoint. Handles errors gracefully — logs and returns `None` for each metric individually. The set of endpoints to call:
- `GET /cryptarchia/info` → `chain_tip`, `lib`, `epoch`
- `GET /mempool/info` → `mempool_depth`
- `GET /connection_manager/info` → `peer_count`
- Wallet balance endpoint TBD (see Open Questions)

**Acceptance criteria:**
- [ ] `fetch_cryptarchia_info(base_url)` returns `{"chain_tip": int, "lib": int, "epoch": int | None}`
- [ ] `fetch_mempool_info(base_url)` returns `{"mempool_depth": int}`
- [ ] `fetch_connection_manager_info(base_url)` returns `{"peer_count": int}`
- [ ] Each function logs URL and HTTP status on failure, returns `None` for the failed field
- [ ] `fetch_all(base_url)` calls all three and returns a flat dict; partial results returned
- [ ] `requests.get` uses `timeout=10`
- [ ] Malformed JSON / unexpected response shape treated as endpoint failure
- [ ] All public functions have type hints; module has a docstring

**Verification:**
- [ ] Unit tests with `unittest.mock.patch` for each endpoint (success + failure paths)
- [ ] Test that a single endpoint failure does not corrupt other metrics in the returned dict

**Dependencies:** Task 4 (config module for URL structure)

**Files touched:**
- `collector/fetcher.py`

**Estimated scope:** S

---

#### Task 7: `collector/main.py` — CLI and Loop

**Description:** The entry point for the collector. Implements the CLI (`init-db`, `run`, `run --daemon`), the 10-minute-aligned snapshot loop, snapshot assembly (fetch → compute blocks_produced → write), and graceful exit on SIGINT/SIGTERM.

**Acceptance criteria:**
- [ ] `python -m collector init-db` calls `db.init_db()` and prints a success message
- [ ] `python -m collector run` fetches immediately, then sleeps in 10-min aligned windows
- [ ] `python -m collector run --daemon` runs in the background (no loop exit on SIGINT in daemon mode)
- [ ] First snapshot timestamp is `now // 600 * 600` (truncated to nearest 10 min)
- [ ] On subsequent runs, duplicate 10-min windows are upserted (no error, no duplicate row)
- [ ] Collector retries DB write once after 30s on failure, then exits with code 1
- [ ] Collector skips snapshot entirely if all APIs fail and a prior snapshot exists
- [ ] Collector writes a snapshot (with nulls) if all APIs fail and no prior snapshot exists
- [ ] Retention pruning runs on startup and after each successful snapshot write
- [ ] All errors logged with context; collector never silently exits
- [ ] All public functions have type hints; module has a docstring

**Verification:**
- [ ] Smoke test: `python -m collector init-db` → file created
- [ ] Smoke test: `python -m collector run` (run for ~30s, interrupt with Ctrl+C) → no traceback
- [ ] Unit tests for the snapshot assembly logic (mocked fetcher + mocked db)

**Dependencies:** Task 4 (config), Task 5 (db), Task 6 (fetcher)

**Files touched:**
- `collector/main.py`

**Estimated scope:** M

---

### Checkpoint: Collector Complete
- [ ] `python -m collector init-db` creates the DB
- [ ] `python -m collector run` can be started and stopped cleanly
- [ ] All three fetcher endpoints are called and responses handled
- [ ] Retention pruning runs on startup
- [ ] Collector unit tests pass

---

### Phase 3: Dashboard Foundation

**Dashboard path is sequential: db (already built) → api → app → frontend**

---

#### Task 8: `dashboard/api.py`

**Description:** Flask blueprint/namespace for all `/api/*` endpoints. Reads from the SQLite DB and returns JSON. Endpoints:
- `GET /api/latest` → latest snapshot (or 404 if none)
- `GET /api/history?since=<unix_ts>` → list of snapshots since `since` timestamp
- `GET /api/health` → `{"status": "ok", "db_age_seconds": <int>}`
- 404 for any unknown path

**Acceptance criteria:**
- [ ] All responses are JSON with `Content-Type: application/json`
- [ ] `GET /api/latest` returns the most recent snapshot row; field names match DB schema
- [ ] `GET /api/history?since=0` returns all snapshots
- [ ] `GET /api/history` without `since` returns 400 with error message
- [ ] `GET /api/health` returns status and age of latest snapshot
- [ ] Unknown route returns 404 `{"error": "Not found"}`
- [ ] Empty DB: `/api/latest` returns 404, `/api/history` returns `[]`
- [ ] All functions have type hints; module has a docstring

**Verification:**
- [ ] Unit tests with mocked `db` module (patch `collector.db` before importing `api`)

**Dependencies:** Task 5 (db module)

**Files touched:**
- `dashboard/api.py`

**Estimated scope:** S

---

#### Task 9: `dashboard/app.py`

**Description:** Flask application factory / entry point. Registers the API blueprint, serves `index.html` at `/`, runs on port 8282.

**Acceptance criteria:**
- [ ] `python -m dashboard` starts the Flask app on port 8282
- [ ] `GET /` returns `index.html` (via `render_template`)
- [ ] `GET /api/*` routes registered and return JSON
- [ ] 404 for static assets that don't exist (serve nothing extra)
- [ ] Flask `app` is exported so tests can create a test client

**Verification:**
- [ ] `python -m dashboard &` → `curl http://localhost:8282/` returns HTML
- [ ] `curl http://localhost:8282/api/latest` returns JSON or 404

**Dependencies:** Task 8 (api module)

**Files touched:**
- `dashboard/__init__.py`
- `dashboard/app.py`

**Estimated scope:** XS

---

### Phase 4: Frontend

---

#### Task 10: `dashboard/templates/index.html`

**Description:** Single-page dashboard using Chart.js (CDN). Displays:
- **Header banner** — "Collecting data — first snapshot in approximately 10 minutes" when no data; hidden otherwise
- **Metric panels** (current values): chain tip, LIB, epoch, mempool depth, peer count, wallet balances
- **Historical charts** (one per metric): line chart, time on X-axis, metric value on Y-axis
- **Auto-refresh**: `fetch` on page visibility change + `setInterval(5000)` only when tab is visible
- **Empty state**: "—" for missing values; charts render with axes and no data line

**Acceptance criteria:**
- [ ] All six metrics displayed as current-value panels
- [ ] All six metrics displayed as historical line charts
- [ ] "Collecting data" banner visible on first load (0 snapshots)
- [ ] "Very little data" banner visible when < 1 hour of data
- [ ] Values update every 5s when tab is visible; stop when tab is hidden
- [ ] Timestamps rendered in browser's local timezone (no hardcoded UTC display)
- [ ] Chart.js loaded from CDN; no build step required
- [ ] Graceful degradation: last known values shown when API is temporarily unreachable
- [ ] Wallet balance panel iterates over all wallets in the snapshot

**Verification:**
- [ ] Manual: open in browser, confirm charts render and refresh
- [ ] Manual: confirm browser console has no errors on load
- [ ] Manual: confirm timestamps display in local timezone (DevTools → Network → inspect response)

**Dependencies:** Task 9 (Flask app serving the template)

**Files touched:**
- `dashboard/templates/index.html`

**Estimated scope:** M

---

### Checkpoint: Dashboard Complete
- [ ] `python -m dashboard` starts and serves on port 8282
- [ ] `curl http://localhost:8282/api/latest` returns valid JSON
- [ ] Dashboard frontend loads in browser without JS errors
- [ ] All six charts render (empty initially, with data after collector runs)

---

### Phase 5: Testing

---

#### Task 11: `tests/collector/test_collector.py`

**Description:** Unit tests for the collector layer.

**Test cases:**
- `test_compute_blocks_produced_first_snapshot` → `previous_tip=None` returns `0`
- `test_compute_blocks_produced_normal` → positive delta returned correctly
- `test_compute_blocks_produced_reorg` → `current_tip < previous_tip` returns `0` (no negative)
- `test_config_loads_defaults` → happy path
- `test_config_raises_on_missing_api_url`
- `test_config_skips_invalid_wallet_address`
- `test_fetcher_handles_http_error` → logs and returns `None` for that field
- `test_fetcher_handles_malformed_json` → logs and returns `None`
- `test_fetcher_partial_success` → one endpoint fails, others return correct values
- `test_db_init_creates_schema`
- `test_db_insert_or_replace`
- `test_db_retention_pruning` → rows older than 90 days deleted
- `test_snapshot_assembly` → mocked fetcher → mocked db write

**Acceptance criteria:**
- [ ] All tests pass with `pytest tests/collector/`
- [ ] No external network calls in any test (all HTTP mocked)
- [ ] Tests are isolated (each test function is independent)

**Dependencies:** Tasks 4, 5, 6, 7

**Files touched:**
- `tests/__init__.py` (empty)
- `tests/collector/__init__.py` (empty)
- `tests/collector/test_collector.py`

**Estimated scope:** M

---

#### Task 12: `tests/dashboard/test_api.py`

**Description:** Unit tests for the dashboard API layer.

**Test cases:**
- `test_latest_returns_404_when_empty`
- `test_latest_returns_snapshot_row`
- `test_history_returns_400_without_since`
- `test_history_returns_snapshots_since`
- `test_history_returns_all_when_since_0`
- `test_health_returns_status_and_age`
- `test_unknown_route_returns_404`

**Acceptance criteria:**
- [ ] All tests pass with `pytest tests/dashboard/`
- [ ] Uses Flask test client (no real HTTP, no real DB — mocked or test DB fixture)
- [ ] Tests are isolated

**Dependencies:** Task 8 (api module)

**Files touched:**
- `tests/dashboard/__init__.py` (empty)
- `tests/dashboard/test_api.py`

**Estimated scope:** S

---

### Checkpoint: Testing Complete
- [ ] `pytest` runs cleanly with no failures
- [ ] No network calls in any test (all mocked)

---

### Phase 6: Documentation

---

#### Task 13: `README.md`

**Description:** Setup instructions for a new node operator, matching the commands in SPEC.md.

**Acceptance criteria:**
- [ ] Explains prerequisites (Python 3.10+, pip)
- [ ] Shows `pip install -r requirements.txt`
- [ ] Shows `python -m collector init-db`
- [ ] Shows `python -m collector run --daemon`
- [ ] Shows `python -m dashboard`
- [ ] Explains how to configure `config.yaml` (auto-detect vs manual override)
- [ ] Documents the Docker command (post-MVP placeholder)
- [ ] Describes what each metric means (brief, operator-facing)
- [ ] Points to port 8282 as the dashboard URL

**Dependencies:** Tasks 1-12 (all modules must exist before README can be accurate)

**Files touched:**
- `README.md`

**Estimated scope:** S

---

### Final Checkpoint: All Criteria Met
- [ ] `python -m collector init-db` creates `data/snapshots.db` with correct schema
- [ ] `python -m collector run` polls all APIs and stores a snapshot every 10 min
- [ ] `python -m collector run --daemon` survives restart; duplicate windows upserted
- [ ] `python -m dashboard` serves dashboard on port 8282
- [ ] Dashboard shows all 6 metric panels
- [ ] Dashboard auto-refreshes every 5s (visible) / stops (hidden)
- [ ] Config auto-detects from `user_config.yaml`
- [ ] Collector logs errors without crashing on single-API failure
- [ ] Malformed API responses logged and treated as endpoint failures
- [ ] First run: "Collecting data" banner + "—" for current values
- [ ] Historical graphs render (empty or with data)
- [ ] Timestamps stored as UTC epoch; rendered in browser local timezone
- [ ] 90-day retention pruning on startup and after each write
- [ ] README has clear operator setup instructions
- [ ] All unit tests pass

---

## Parallelisation Opportunities

| Parallel? | Tasks | Reason |
|---|---|---|
| Yes | Tasks 1, 2, 3 (scaffolding) | No inter-dependencies |
| Yes | Tests 11, 12 (can be written alongside Phase 2/3) | Mocked; don't require implementation to be written first |
| No | Phase 2 (collector) | Sequential: config → db → fetcher → main |
| No | Phase 3 (dashboard) | Sequential: db → api → app → frontend |
| No | Phase 6 (README) | Depends on all modules being complete |

**Recommended approach:** Scaffold (Tasks 1-3) first. Then run Tasks 4, 5, 7, 8, 11 in parallel where possible (config+db can start simultaneously; fetcher needs config; main needs all three). Dashboard API needs db; app needs API; index.html needs app.

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Logos node API response shapes are unknown | High | Implement fetcher defensively; log unexpected structure; treat as endpoint failure. Add explicit "Open Questions" issue. |
| `user_config.yaml` format differs from assumption | High | Add integration test against a real config file; log clearly on parse failure; allow manual override as fallback |
| Wallet balance API endpoint unknown | High | Default to a plausible endpoint name; make it configurable via `config.yaml` if the endpoint varies; add explicit TODO |
| Timezone edge cases in Chart.js rendering | Medium | Always pass Unix epoch to JS; use `new Date(epoch * 1000)`; avoid any string-based timezone conversion |
| 90-day retention query performance on large DB | Medium | Index on `timestamp` already specified in schema; prune in batches if needed |
| Collector restart creates duplicate snapshots | Low | `INSERT OR REPLACE` is the intended behaviour per spec; verify with test |

---

## Open Questions

1. **Wallet balance API endpoint:** What endpoint returns wallet balances? Is it `/wallet/balance?address=<addr>`? Are there multiple endpoints for different wallet types?
2. **API authentication:** Does the Logos node API require any auth headers or is it open? Does `requests` need any special handling?
3. **`user_config.yaml` schema:** Confirm the exact path to `api.backend.listen_address` and `wallet.known_keys` in the real file. Is the structure stable?
4. **Mempool/peer count endpoints:** Are `/mempool/info` and `/connection_manager/info` definitely the correct endpoints? Any other consensus-relevant endpoints worth polling?
5. **Docker deployment:** The SPEC mentions Docker post-MVP. Should the `Dockerfile` be scaffolded now or deferred entirely?

---

## Files Summary

```
logos-blockchain-dashboard/
├── SPEC.md
├── PLAN.md                         ← this file
├── README.md                       ← Task 13
├── requirements.txt                ← Task 1
├── config.yaml                     ← Task 2
├── .gitignore                      ← Task 3
├── data/
│   └── .gitkeep                    ← Task 3
├── collector/
│   ├── __init__.py                 ← Tasks 4, 5, 6, 7
│   ├── config.py                   ← Task 4
│   ├── db.py                       ← Task 5
│   ├── fetcher.py                  ← Task 6
│   └── main.py                     ← Task 7
├── dashboard/
│   ├── __init__.py                 ← Task 9
│   ├── app.py                      ← Task 9
│   ├── api.py                      ← Task 8
│   └── templates/
│       └── index.html              ← Task 10
└── tests/
    ├── __init__.py
    ├── collector/
    │   ├── __init__.py
    │   └── test_collector.py       ← Task 11
    └── dashboard/
        ├── __init__.py
        └── test_api.py             ← Task 12
```
