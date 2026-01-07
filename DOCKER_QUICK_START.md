# Docker Quick Start Guide

Quick reference for running Spark (Paperless-Firefly Bridge) with Docker.

## Prerequisites

- Docker and Docker Compose installed
- Paperless-ngx instance running and accessible
- Firefly III instance running and accessible
- API tokens for both services

---

## Service Architecture

**Understanding the two types of services:**

| Service Type | Behavior | How to Run |
|--------------|----------|------------|
| **Web UI** | Long-running, continuous | `docker compose up -d` |
| **Workers/Commands** | One-shot, exit when done | `docker compose run --rm ...` |

- **Web UI** runs continuously and serves the review interface
- **Workers** (reconcile, extract, import) are one-shot commands that exit after completing
- Do NOT use `docker compose up` for workers - they will keep restarting!

---

## Initial Setup

### 1. Create `.env` file

```bash
nano .env
```

Paste this template and fill in your actual values:

```bash
# Paperless Connection
PAPERLESS_URL=http://192.168.1.XXX:8000
PAPERLESS_TOKEN=your-actual-paperless-token-here
PAPERLESS_FILTER_TAG=finance/inbox

# Firefly III Connection
FIREFLY_URL=http://192.168.1.XXX:8080
FIREFLY_TOKEN=your-actual-firefly-token-here
FIREFLY_DEFAULT_ACCOUNT=Checking Account

# Spark Reconciliation Settings (Spark v1.0)
SPARK_RECONCILIATION_SYNC_DAYS=90      # Lookback window for transaction sync
SPARK_RECONCILIATION_AUTO_MATCH_THRESHOLD=0.90  # Auto-link confidence
SPARK_RECONCILIATION_PROPOSAL_THRESHOLD=0.60    # Minimum for proposals

# Optional Settings
CONFIDENCE_AUTO_THRESHOLD=0.85
CONFIDENCE_REVIEW_THRESHOLD=0.60
PF_PORT=8080
```

**Save**: Press `Ctrl+X`, then `Y`, then `Enter`

### 2. Network Configuration (for Docker networks)

If Paperless and Firefly are on separate Docker networks, create `docker-compose.override.yml`:

```yaml
networks:
  paperless_default:
    external: true
  firefly_default:
    external: true

services:
  paperless-firefly:
    networks:
      - default
      - paperless_default
      - firefly_default
```

### 3. Start the Container

```bash
docker compose up -d
```

### 4. Check if it's Running

```bash
docker ps
```

You should see a container named `paperless-firefly` with status `Up`.

### 5. Access the Web Interface

Open your browser and go to:
```
http://YOUR_HOST_IP:8080/review/
```

---

## Running Commands (Spark v1.0)

> **⚠️ Important:** Use `docker compose run --rm` for one-shot commands (reconcile, extract, import).  
> Do NOT use `docker compose up` for these - they will exit and keep restarting!

### Bank Reconciliation Pipeline

```bash
# Full reconciliation (sync + match)
docker compose run --rm paperless-firefly reconcile

# Sync Firefly transactions only (no matching)
docker compose run --rm paperless-firefly reconcile --no-match

# Run matching only (no sync)
docker compose run --rm paperless-firefly reconcile --no-sync

# Force full cache refresh
docker compose run --rm paperless-firefly reconcile --full-sync

# Dry run (show what would be done)
docker compose run --rm paperless-firefly reconcile --dry-run
```

Expected output for successful reconcile:
```
🔄 Starting bank reconciliation (Spark v1.0)...
  → Connecting to Firefly: http://firefly:8080
  ✓ Firefly connection OK

Configuration:
  Sync transactions: yes
  Full sync (clear cache): no
  Run matching: yes
  Auto-link threshold: 90%

📊 Reconciliation Results
========================================
  Status:              completed
  Transactions synced: 42
  Transactions cached: 3
  Proposals created:   5
  Proposals existing:  2
  Auto-linked:         3
  Duration:            1250ms

✓ Reconciliation completed successfully
```

### Document Processing

```bash
# Scan for documents with finance tag
docker compose run --rm paperless-firefly scan --tag finance/inbox

# Extract financial data from documents
docker compose run --rm paperless-firefly extract --tag finance/inbox --limit 10

# Import approved transactions to Firefly
docker compose run --rm paperless-firefly import
```

### Check Status

```bash
docker compose run --rm paperless-firefly status
```

---

## Common Docker Commands

### View Logs

```bash
# View all logs
docker logs paperless-firefly

# Follow logs in real-time (Ctrl+C to exit)
docker logs -f paperless-firefly

# View last 50 lines
docker logs --tail 50 paperless-firefly
```

### Stop/Start/Restart

```bash
# Stop the container
docker compose down

# Start the container
docker compose up -d

# Restart the container
docker compose restart

# Rebuild and restart (after code changes)
docker compose up -d --build
```

### Debug Inside Container

```bash
# Open a shell inside the running container
docker exec -it paperless-firefly /bin/bash

# Run a one-off command
docker exec paperless-firefly paperless-firefly --help

# Check environment variables
docker exec paperless-firefly env | grep -E "(PAPERLESS|FIREFLY|SPARK)"
```

---

## Troubleshooting

### Container Keeps Restarting

```bash
# Check why it's failing
docker logs paperless-firefly

# Common causes:
# - Missing or invalid .env file
# - Wrong PAPERLESS_URL or FIREFLY_URL
# - Invalid API tokens
# - Network connectivity issues
```

### "Connection refused" or DNS errors

1. Check that both Paperless and Firefly are running
2. Verify network configuration in `docker-compose.override.yml`
3. Use container names (e.g., `firefly`, `paperless`) not `localhost`

**Option 1 - Use LAN IP Addresses:**
```bash
PAPERLESS_URL=http://192.168.1.100:8000
FIREFLY_URL=http://192.168.1.100:8080
```

**Option 2 - Use Host Networking (if all services on same host):**

Create `docker-compose.override.yml`:
```yaml
services:
  paperless-firefly:
    network_mode: "host"
```

### No Transactions Synced

1. Check the sync date range (default: last 90 days)
2. Verify Firefly has unlinked transactions in that date range
3. Run with `--full-sync` to force complete refresh
4. Increase `SPARK_RECONCILIATION_SYNC_DAYS` if needed

### Exit Codes

| Code | Meaning |
|------|---------|
| 0    | Success |
| 1    | Failure (connection error, processing error, etc.) |

The reconcile command **fails loudly** - it returns exit code 1 and an error message if anything goes wrong.

### Remove Everything and Start Fresh

```bash
# Stop and remove containers, networks, and volumes
docker compose down -v

# Remove the image
docker rmi paperless-firefly:latest

# Start from scratch
docker compose up -d
```

---

## Updating

When you pull new code from GitHub:

```bash
# Pull latest code
git pull

# Rebuild and restart
docker compose up -d --build

# Check logs
docker logs -f paperless-firefly
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `PAPERLESS_URL` | - | Paperless-ngx base URL |
| `PAPERLESS_TOKEN` | - | Paperless API token |
| `FIREFLY_URL` | - | Firefly III base URL |
| `FIREFLY_TOKEN` | - | Firefly API token |
| `SPARK_STATE_DB_PATH` | `spark.db` | SQLite database path |
| `SPARK_RECONCILIATION_SYNC_DAYS` | `90` | Transaction sync lookback (days) |
| `SPARK_RECONCILIATION_AUTO_MATCH_THRESHOLD` | `0.90` | Auto-link confidence threshold |
| `SPARK_RECONCILIATION_PROPOSAL_THRESHOLD` | `0.60` | Proposal creation threshold |
