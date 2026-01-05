# Docker Quick Start Guide

## Initial Setup on Raspberry Pi

### 1. Create `.env` file

```bash
cd ~/Code/Paperless_FireflyIII_parser/LedgerBridge
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

# Optional Settings
CONFIDENCE_AUTO_THRESHOLD=0.85
CONFIDENCE_REVIEW_THRESHOLD=0.60
PF_PORT=8080
```

**Save**: Press `Ctrl+X`, then `Y`, then `Enter`

### 2. Start the Container

```bash
docker compose up -d
```

### 3. Check if it's Running

```bash
docker ps
```

You should see a container named `paperless-firefly` with status `Up`.

### 4. Access the Web Interface

Open your browser and go to:
```
http://YOUR_RASPBERRY_PI_IP:8080
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

### Check Container Status

```bash
# List all containers (running and stopped)
docker ps -a

# Check container resource usage
docker stats paperless-firefly
```

### Debug Inside Container

```bash
# Open a shell inside the running container
docker exec -it paperless-firefly /bin/bash

# Run a one-off command inside the container
docker exec paperless-firefly paperless-firefly --help

# Check environment variables
docker exec paperless-firefly env | grep PAPERLESS
```

### View Container Configuration

```bash
# Show detailed container info
docker inspect paperless-firefly

# Show port mappings
docker port paperless-firefly
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

### Port 8080 Already in Use

If another service is using port 8080, change it in your `.env`:

```bash
PF_PORT=8081  # Or any other free port
```

Then restart:
```bash
docker compose down
docker compose up -d
```

### Can't Connect to Paperless/Firefly

**Option 1 - Use LAN IP Addresses:**
```bash
# In .env, use actual IP addresses:
PAPERLESS_URL=http://192.168.1.100:8000
FIREFLY_URL=http://192.168.1.100:8080
```

**Option 2 - Use Host Networking (if all services on same Pi):**

Create `docker-compose.override.yml`:
```yaml
services:
  paperless-firefly:
    network_mode: "host"
```

Then update `.env`:
```bash
PAPERLESS_URL=http://localhost:8000
FIREFLY_URL=http://localhost:8080
```

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

## Running Manual Commands

### Extract Documents

```bash
docker compose run --rm paperless-firefly extract --tag finance/inbox --limit 10
```

### Check Status

```bash
docker compose run --rm paperless-firefly status
```

### Run Full Pipeline

```bash
docker compose run --rm paperless-firefly pipeline finance/inbox
```

### Import Approved Transactions

```bash
docker compose run --rm paperless-firefly import
```

---

## Updating the Application

When you pull new code from GitHub:

```bash
# Pull latest code
git pull

# Rebuild and restart the container
docker compose up -d --build

# Check logs to ensure it started correctly
docker logs -f paperless-firefly
```

---

## Getting Help

If something isn't working:

1. **Check the logs first:**
   ```bash
   docker logs paperless-firefly
   ```

2. **Verify your `.env` file exists and has correct values:**
   ```bash
   cat .env
   ```

3. **Test connectivity to Paperless and Firefly:**
   ```bash
   curl http://YOUR_PAPERLESS_IP:8000/api/
   curl http://YOUR_FIREFLY_IP:8080/api/v1/about
   ```

4. **Check if the port is actually listening:**
   ```bash
   sudo netstat -tlnp | grep 8080
   ```
