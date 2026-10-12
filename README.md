# Logos Node Observer Dashboard

A read-only web dashboard for people who run Logos nodes. It shows the health and headline stats of the blockchain, storage and delivery nodes on this machine, with historical graphs — no `curl` or `logosctl` commands required.

A machine does not have to run every kind of node. Each kind is shown only if you configure it; see [CONTEXT.md](CONTEXT.md) for the exact meaning of the terms used here.

## What it shows

| Kind | Current values | Charted history |
|------|----------------|-----------------|
| **Blockchain** | chain tip, slot, mode, mempool depth, peers, wallet balances, Blend status | chain tip, mempool depth, peers, wallet balances |
| **Storage** | module status and version, running, library version, network, peer ID, connected peers, quota used / reserved / max, blocks, files stored, uploads, downloads, memory, CPU time | quota used, blocks, files stored, blocks served to peers per interval, connected peers, memory |
| **Delivery** | module status and version, running, node version, connection status, rate-limit (RLN) state with its failure reason, peers, bytes in / out, messages in / out, memory, CPU time | peers, bytes per interval, messages per interval, connection status, memory |
| **Host** | CPU temperature, CPU usage, memory, load | the same |

A status strip at the top has one chip per configured node. A node that stops answering turns its chip red with the time it was last seen; its section keeps the last known values, and its charts show a gap for the time it was away.

Current values refresh every 30 seconds while the tab is visible. Historical graphs show up to 90 days of data.

---

## Setup

### Prerequisites

- Python 3.10 or later
- pip
- For a blockchain node: its REST API reachable from this machine
- For storage or delivery nodes: `logosctl` able to reach the Logos Core daemon that runs them (see [Reaching Logos Core](#reaching-logos-core))

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Initialise the database

```bash
python -m collector init-db
```

This creates `data/snapshots.db` with one table per node kind and one for the host.

### 3. Configure

Say which kinds of node this machine runs. A kind is polled and shown **only if its block is present**; with no block at all, the dashboard shows the host only.

Put your settings in `config.local.yaml` next to `config.yaml`. It is untracked, and both the collector and the dashboard prefer it over `config.yaml`.

```yaml
# A machine running a blockchain node
blockchain:
  # Auto-detect the API URL and wallets from the node config
  node_config_path: "~/.config/logos-node/user_config.yaml"
  # Or set the URL by hand
  # axum_url: "http://localhost:38437"
  # Wallets shown on the page (replace the auto-detected ones)
  # wallets:
  #   - name: "voucher"
  #     address: "e59ffc735020e875982dcf84906738224aae576ea9119714a6d1d44de96f6d16"
```

```yaml
# A machine running storage and delivery nodes
storage:
delivery:
logosctl:
  command: ["logosctl"]
```

> **Upgrading from an earlier version:** the top-level keys `node_config_path`, `node.axum_url`, `wallets` and `blend` moved under `blockchain:`. The collector and dashboard refuse to start while the old keys are present, and say where each one goes.

### 4. Run the collector

The collector reads every configured node every 10 minutes and stores a snapshot per kind. Run it in the background:

```bash
python -m collector run --daemon
```

Or foreground (for debugging), or once:

```bash
python -m collector run
python -m collector run --once
```

Collector exit codes: `0` normal, `1` fatal error.

### 5. Run the dashboard

```bash
python -m dashboard
```

Dashboard available at: **http://localhost:8282**

---

## Configuration details

### Blockchain auto-detection

When `blockchain.node_config_path` points to a valid `user_config.yaml`:

- **API URL** — extracted from `api.backend.listen_address` (port number used with `http://localhost:<port>`)
- **Wallets** — all public keys in `wallet.known_keys` are added automatically

`blockchain.wallets`, when set, replaces the auto-detected list entirely. A `blockchain:` block from which no API URL can be worked out is an error at startup.

### Storage and delivery

Both run as modules in one Logos Core daemon and are read with `logosctl`. Defaults:

```yaml
storage:
  module: storage_module
delivery:
  module: delivery_module
logosctl:
  command: ["logosctl"]     # argv prefix; anything that ends up running logosctl
  timeout_seconds: 20
```

Only read-only calls are made (`status`, `stats`, and getters such as `space`, `manifests`, `getConnectionStatus`). Nothing typed in the browser is ever passed to `logosctl`.

### Reaching Logos Core

`logosctl` talks to the daemon over local sockets by default, which only works from inside the daemon's own container or session. Pick whichever fits the machine:

**Same host, no containers** — nothing to do; `logosctl` is on `PATH`.

**Daemon in a container, dashboard on the host** — run `logosctl` inside the daemon's container:

```yaml
logosctl:
  command: ["podman", "exec", "logos-core", "logosctl"]
```

**Daemon and dashboard in separate containers** — give the daemon two loopback TCP listeners and the dashboard its own token. Both containers must use host networking.

1. In the daemon's session, install a config with the listeners, issue a token, and restart the daemon (config is read at start):

   ```yaml
   # node.yaml
   modules:
     core_service:
       - protocol: tcp
         host: 127.0.0.1
         port: 8645
     capability_module:
       - protocol: tcp
         host: 127.0.0.1
         port: 8646
   ```

   ```bash
   logosctl daemon config set node.yaml
   logosctl token issue --name dashboard     # writes daemon/tokens/dashboard.json
   ```

   Both listeners are needed: a client handshakes with `capability_module` before its first call. Keep them on `127.0.0.1`; the daemon refuses plain TCP on any other address.

2. Give the dashboard a `logosctl` session directory holding the token and a client config:

   ```
   logosctl/client/dashboard.json     # copy of daemon/tokens/dashboard.json
   logosctl/client/config.yaml
   ```

   ```yaml
   # logosctl/client/config.yaml
   version: 2
   token_file: dashboard.json
   daemon:
     core_service:      { transport: tcp, host: 127.0.0.1, port: 8645 }
     capability_module: { transport: tcp, host: 127.0.0.1, port: 8646 }
   ```

3. Build the dashboard image with `logosctl` in it and mount the session at `/app/logosctl` (see [Container](#container)).

The token is not read-only: it can call any method of any module. Treat the session directory like a credential.

### Intervals

```yaml
collector:
  interval_minutes: 10      # how often a snapshot is stored
dashboard:
  refresh_seconds: 30       # how often current values are re-read
```

Snapshots are aligned to 10-minute windows, so a shorter collector interval only overwrites the same window more often. Reading storage and delivery takes around ten seconds, so the dashboard reads them at most once per refresh period, in the background, and only while a browser is asking.

---

## Container

```bash
podman build -t logos-dashboard:base .
```

That image is enough for a blockchain node. For storage and delivery, add `logosctl` from your Logos Core image (the two images must share a Debian release, as `logosctl` only brings its own libraries):

```bash
podman build -t logos-dashboard:logosctl -f Containerfile.logosctl \
    --build-arg LOGOS_CORE_IMAGE=localhost/logos-core:0.3.0 .
```

Run it with host networking, your config, the snapshot volume and the `logosctl` session:

```bash
podman run -d --name logos-dashboard --network host \
  -v logos-dash-data:/app/data \
  -v ./config.local.yaml:/app/config.local.yaml:ro,Z \
  -v ./logosctl:/app/logosctl:Z \
  -v /sys/class/thermal:/sys/class/thermal:ro \
  logos-dashboard:logosctl
```

---

## Testing

```bash
pip install pytest
python -m pytest
npm ci && npm test        # frontend unit tests
```

Collector tests mock all HTTP responses and the `logosctl` process — no network calls are made and no node is needed. Dashboard tests use a temporary database.

---

## File structure

```
logos-blockchain-dashboard/
├── collector/          # Reads the nodes, stores snapshots
│   ├── config.py       # Config loading + user_config.yaml parsing
│   ├── db.py           # SQLite schema, writes, reads, pruning
│   ├── fetcher.py      # REST API calls to the blockchain node
│   ├── logosctl.py     # Runs logosctl and parses its JSON
│   ├── modules.py      # Storage and delivery readings
│   ├── host.py         # Host metrics from /proc and /sys
│   └── main.py         # CLI entry point and 10-min loop
├── dashboard/          # Flask web dashboard
│   ├── api.py          # JSON API endpoints
│   ├── app.py          # Flask application
│   ├── live.py         # Cached current values
│   ├── blend.py        # Blend network summary
│   ├── static/         # refresh.js (polling), series.js (chart series)
│   └── templates/
│       └── index.html  # Single-page dashboard (Chart.js via CDN)
├── data/               # SQLite database (gitignored)
├── tests/
├── config.yaml         # Config template
├── Containerfile       # Dashboard image
├── Containerfile.logosctl  # Dashboard image + logosctl
├── CONTEXT.md          # Glossary
└── SPEC.md
```

---

## Retention

Snapshots older than 90 days are deleted from every table on collector startup and after each collection.

---

## Troubleshooting

**A chip says "not answering"**

Hover it for the reason. For the blockchain node, check that its API is listening at the configured URL. For storage and delivery, run the configured `logosctl` command yourself: `logosctl status` shows whether the daemon is running and each module is `loaded`.

**Startup fails with "uses top-level keys that moved under `blockchain:`"**

Your config predates explicit node kinds. Move the named keys under a `blockchain:` block.

**A section is missing**

That kind has no block in the config the process actually read (`config.local.yaml` wins over `config.yaml`). The collector logs the kinds it polls at startup.

**Storage or delivery chips say "Logos Core daemon unreachable"**

`logosctl` could not reach the daemon. See [Reaching Logos Core](#reaching-logos-core); `NOT_INSTALLED` means the command itself was not found.

**Wallet balances show "—"**

The wallet address is not registered with the node's wallet service. This is normal for addresses that have not been imported into the node.

**"Collecting data — first snapshot in approximately 10 minutes"**

Normal on first run. Graphs become meaningful after a few hours of collector operation.
