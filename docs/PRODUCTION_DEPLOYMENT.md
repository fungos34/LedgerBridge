# Production Deployment Guide - Gunicorn Migration

## Changes Made

### 1. Added Gunicorn Production WSGI Server

**Why**: Django's development server (`runserver`) is NOT suitable for production:
- Single-threaded (blocks on each request)
- No security hardening
- Memory leaks over time
- Can't handle concurrent users
- No proper request queuing

**Solution**: Gunicorn is a battle-tested, production-grade WSGI server.

### 2. Files Modified

- **pyproject.toml**: Added `gunicorn>=21.0.0` dependency
- **docker/entrypoint.sh**: Changed server startup to use gunicorn instead of runserver
- **src/paperless_firefly/review/web/app.py**: Added WSGI `application` export for gunicorn
- **docker-compose.yml**: Added optional gunicorn configuration variables

### 3. Configuration Options

Default gunicorn settings (suitable for most deployments):
- **Workers**: 4 (handles 4 concurrent requests)
- **Threads per worker**: 2 (each worker can handle 2 threads)
- **Timeout**: 120 seconds (for long-running operations)

You can override these in your `/srv/stack.env`:

```bash
# Optional: Tune gunicorn for your server
GUNICORN_WORKERS=4           # Rule of thumb: (2 × CPU_cores) + 1
GUNICORN_THREADS=2           # Keep low for CPU-bound tasks
GUNICORN_TIMEOUT=120         # Increase if extraction/reconciliation takes longer
```

## Deployment Steps

### 1. Commit and Push Changes

```bash
# On your local machine
cd C:\Users\accou\Code\Paperless_FireflyIII_Parser

git add .
git commit -m "Migrate to Gunicorn production WSGI server

- Replace Django runserver with gunicorn for production
- Add gunicorn configuration options
- Update entrypoint.sh with production server logic
- Fixes admin styling, reconciliation bugs, extract+sync"

git push origin main
```

### 2. Deploy to Orion Server

```bash
# SSH into Orion
ssh helios@orion

# Navigate to deployment directory
cd /srv/sparklink

# Pull latest changes
git pull origin main

# Rebuild images (required for dependency changes)
docker compose build --no-cache

# Restart services
docker compose down
docker compose up -d

# Verify deployment
docker compose logs -f sparklink --tail=50
```

### 3. Verify Production Server

Look for this in the logs:
```
[INFO] Using Gunicorn (production WSGI server)
[INFO]   Workers: 4
[INFO]   Threads: 2
[INFO]   Timeout: 120s
```

You should **NOT** see:
```
WARNING: This is a development server. Do not use it in a production setting.
```

### 4. Test the Application

1. **Admin Panel**: Visit `https://your-domain/admin/` - should have proper Django styling
2. **Reconciliation**: Click items in both lists - no 500 errors
3. **Extract & Sync**: Button should extract from Paperless AND sync Firefly transactions
4. **Concurrent Access**: Open multiple browser tabs - all should work simultaneously

### 5. Performance Tuning (Optional)

#### For More Powerful Servers (8+ CPU cores):
```bash
# In /srv/stack.env
GUNICORN_WORKERS=8
GUNICORN_THREADS=2
```

#### For Resource-Constrained Servers (2-4 CPU cores):
```bash
# In /srv/stack.env
GUNICORN_WORKERS=2
GUNICORN_THREADS=1
```

#### For Long-Running Operations:
If you see timeout errors during reconciliation or extraction:
```bash
# In /srv/stack.env
GUNICORN_TIMEOUT=300  # 5 minutes
```

## Architecture Overview

```
┌──────────────┐
│  Internet    │
└──────┬───────┘
       │ HTTPS
┌──────▼───────────┐
│  Cloudflare      │  ← SSL Termination
│  Tunnel          │  ← DDoS Protection
└──────┬───────────┘  ← Caching
       │
┌──────▼───────────┐
│  RPI5 Server     │  ← Cloudflared Agent
└──────┬───────────┘  ← Routes to Orion
       │ Local Network
┌──────▼───────────┐
│  Orion Server    │
│  ┌─────────────┐ │
│  │  Sparklink  │ │  ← Gunicorn WSGI
│  │  Container  │ │  ← 4 Workers × 2 Threads
│  └─────────────┘ │  ← Django + Whitenoise
└──────────────────┘
```

## Security Benefits

1. **Cloudflare handles**:
   - SSL/TLS termination
   - DDoS protection
   - Rate limiting
   - CDN for static assets

2. **Gunicorn provides**:
   - Worker process isolation
   - Request timeout protection
   - Graceful restart capability
   - Production logging

3. **Whitenoise handles**:
   - Static file serving (admin CSS/JS)
   - Compression and caching
   - Security headers

## Rollback Plan

If something goes wrong, you can temporarily use the dev server:

```bash
# In docker-compose.yml, add to sparklink environment:
- USE_DEV_SERVER=true

# Then restart
docker compose down
docker compose up -d
```

**Note**: This is ONLY for emergency debugging. Do not leave it enabled.

## Monitoring

### Check Worker Status
```bash
# See gunicorn workers
docker compose exec sparklink ps aux | grep gunicorn
```

### Check Memory Usage
```bash
# Monitor resource usage
docker stats sparklink
```

### View Access Logs
```bash
# Live access log
docker compose logs -f sparklink | grep "GET\|POST"
```

## Common Issues

### "502 Bad Gateway" after deployment
- Check logs: `docker compose logs sparklink`
- Verify gunicorn is starting: Look for `[INFO] Using Gunicorn`
- Check worker count: `GUNICORN_WORKERS` should be ≥ 1

### High CPU usage
- Reduce workers: Set `GUNICORN_WORKERS=2`
- Check for infinite loops in logs

### Timeout errors (504)
- Increase timeout: Set `GUNICORN_TIMEOUT=300`
- Check if operations are genuinely taking too long

### Memory leaks
- Restart workers: `docker compose restart sparklink`
- Monitor with `docker stats sparklink`

## Success Criteria

✅ No "development server" warning in logs  
✅ Admin panel has proper Django styling  
✅ Multiple concurrent users work smoothly  
✅ Reconciliation page works without 500 errors  
✅ Extract & Sync button works correctly  
✅ CPU usage stays reasonable under load  
✅ Memory usage stable over 24+ hours  

## Questions?

If you see any issues after deployment, check:
1. Logs: `docker compose logs sparklink --tail=100`
2. Container status: `docker compose ps`
3. Health check: `docker compose exec sparklink curl http://localhost:8080/`
