#!/bin/bash
set -e

# Paperless-Firefly Pipeline Entrypoint
# Supports multiple modes: server, extract, import, pipeline

# Color output for logging
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Validate required environment variables
validate_env() {
    local missing=()
    
    if [ -z "$PAPERLESS_URL" ]; then
        missing+=("PAPERLESS_URL")
    fi
    
    if [ -z "$PAPERLESS_TOKEN" ]; then
        missing+=("PAPERLESS_TOKEN")
    fi
    
    if [ -z "$FIREFLY_URL" ]; then
        missing+=("FIREFLY_URL")
    fi
    
    if [ -z "$FIREFLY_TOKEN" ]; then
        missing+=("FIREFLY_TOKEN")
    fi
    
    if [ ${#missing[@]} -gt 0 ]; then
        log_error "Missing required environment variables: ${missing[*]}"
        log_info "Please set these variables or mount a config file at /app/config/config.yaml"
        exit 1
    fi
}

# Generate config from environment variables if not mounted
generate_config() {
    if [ ! -f "$CONFIG_PATH" ]; then
        log_info "Generating config from environment variables..."
        cat > "$CONFIG_PATH" << EOF
# Auto-generated configuration
paperless:
  base_url: "${PAPERLESS_URL}"
  token: "${PAPERLESS_TOKEN}"
  filter_tag: "${PAPERLESS_FILTER_TAG:-finance/inbox}"

firefly:
  base_url: "${FIREFLY_URL}"
  token: "${FIREFLY_TOKEN}"
  default_source_account: "${FIREFLY_DEFAULT_ACCOUNT:-Checking Account}"

confidence:
  auto_threshold: ${CONFIDENCE_AUTO_THRESHOLD:-0.85}
  review_threshold: ${CONFIDENCE_REVIEW_THRESHOLD:-0.60}

state_db_path: "${STATE_DB_PATH}"
EOF
        log_info "Config written to $CONFIG_PATH"
    else
        log_info "Using mounted config file at $CONFIG_PATH"
    fi
}

# Initialize the database if needed
init_db() {
    log_info "Initializing state database..."
    python -c "
from paperless_firefly.state_store import StateStore
store = StateStore('${STATE_DB_PATH}')
print('Database initialized successfully')
"
    
    # Run Django migrations for auth database
    log_info "Running Django migrations..."
    python -c "
import django
from django.conf import settings
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'paperless_firefly.review.web.settings')
django.setup()
from django.core.management import call_command
call_command('migrate', '--run-syncdb', verbosity=0)
print('Django migrations complete')
"
    
    # Create default admin user if not exists
    if [ -n "$ADMIN_USERNAME" ] && [ -n "$ADMIN_PASSWORD" ]; then
        log_info "Creating admin user..."
        python -c "
import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'paperless_firefly.review.web.settings')
django.setup()
from django.contrib.auth.models import User
username = '${ADMIN_USERNAME}'
password = '${ADMIN_PASSWORD}'
if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, '', password)
    print(f'Admin user {username} created')
else:
    print(f'Admin user {username} already exists')
"
    fi
}

# Preflight connectivity check for worker/reconcile services
preflight_check() {
    local service_name="${1:-worker}"
    log_info "Running preflight connectivity check for $service_name..."
    
    # Check Paperless connectivity
    log_info "Checking Paperless at: $PAPERLESS_URL"
    if ! python -c "
import requests
import sys
try:
    r = requests.get('${PAPERLESS_URL}/api/', timeout=10, headers={'Authorization': 'Token ${PAPERLESS_TOKEN}'})
    if r.status_code < 500:
        print('Paperless connection: OK')
        sys.exit(0)
    else:
        print(f'Paperless returned HTTP {r.status_code}')
        sys.exit(1)
except requests.exceptions.ConnectionError as e:
    print(f'ERROR: Cannot connect to Paperless at ${PAPERLESS_URL}')
    print(f'  Error: {e}')
    print('')
    print('Troubleshooting:')
    print('  1. Check PAPERLESS_URL is set correctly')
    print('  2. Verify this container is on the same Docker network as Paperless')
    print('  3. Run: docker network inspect <network> to see available hosts')
    sys.exit(1)
except Exception as e:
    print(f'ERROR: Paperless check failed: {e}')
    sys.exit(1)
"; then
        log_error "Paperless connectivity check failed!"
        return 1
    fi
    
    # Check Firefly connectivity
    log_info "Checking Firefly at: $FIREFLY_URL"
    if ! python -c "
import requests
import sys
try:
    r = requests.get('${FIREFLY_URL}/api/v1/about', timeout=10, headers={'Authorization': 'Bearer ${FIREFLY_TOKEN}'})
    if r.status_code < 500:
        print('Firefly connection: OK')
        sys.exit(0)
    else:
        print(f'Firefly returned HTTP {r.status_code}')
        sys.exit(1)
except requests.exceptions.ConnectionError as e:
    print(f'ERROR: Cannot connect to Firefly at ${FIREFLY_URL}')
    print(f'  Error: {e}')
    print('')
    print('Troubleshooting:')
    print('  1. Check FIREFLY_URL is set correctly')
    print('  2. Verify this container is on the same Docker network as Firefly')
    print('  3. Run: docker network inspect <network> to see available hosts')
    sys.exit(1)
except Exception as e:
    print(f'ERROR: Firefly check failed: {e}')
    sys.exit(1)
"; then
        log_error "Firefly connectivity check failed!"
        return 1
    fi
    
    log_info "Preflight checks passed!"
    return 0
}

# Run the web server (review interface)
run_server() {
    log_info "Starting review web interface..."
    log_info "  Host: ${HOST}"
    log_info "  Port: ${PORT}"
    log_info "  Paperless: ${PAPERLESS_URL}"
    log_info "  Firefly: ${FIREFLY_URL}"
    
    exec paperless-firefly -c "$CONFIG_PATH" review --host "$HOST" --port "$PORT"
}

# Run document extraction
run_extract() {
    local tag="${1:-finance/inbox}"
    local limit="${2:-100}"
    
    log_info "Running extraction..."
    log_info "  Tag: $tag"
    log_info "  Limit: $limit"
    
    preflight_check "extract" || exit 1
    
    exec paperless-firefly -c "$CONFIG_PATH" extract --tag "$tag" --limit "$limit"
}

# Run import to Firefly
run_import() {
    local auto_only="${1:-false}"
    
    log_info "Running import to Firefly III..."
    
    if [ "$auto_only" = "true" ]; then
        exec paperless-firefly -c "$CONFIG_PATH" import --auto-only
    else
        exec paperless-firefly -c "$CONFIG_PATH" import
    fi
}

# Run full pipeline
run_pipeline() {
    local tag="${1:-finance/inbox}"
    local limit="${2:-100}"
    
    log_info "Running full pipeline..."
    
    preflight_check "pipeline" || exit 1
    
    exec paperless-firefly -c "$CONFIG_PATH" pipeline --tag "$tag" --limit "$limit" --auto-only
}

# Show status
run_status() {
    exec paperless-firefly -c "$CONFIG_PATH" status
}

# Run reconciliation (Spark v1.0)
run_reconcile() {
    local sync="--sync"
    local match="--match"
    local full_sync=""
    local dry_run=""
    
    # Parse arguments
    while [ $# -gt 0 ]; do
        case "$1" in
            --sync)
                sync="--sync"
                shift
                ;;
            --no-sync)
                sync="--no-sync"
                shift
                ;;
            --match)
                match="--match"
                shift
                ;;
            --no-match)
                match="--no-match"
                shift
                ;;
            --full-sync)
                full_sync="--full-sync"
                shift
                ;;
            --dry-run)
                dry_run="--dry-run"
                shift
                ;;
            *)
                log_warn "Unknown option for reconcile: $1"
                shift
                ;;
        esac
    done
    
    log_info "Running bank reconciliation (Spark v1.0)..."
    log_info "  Sync: $sync"
    log_info "  Match: $match"
    [ -n "$full_sync" ] && log_info "  Full sync: yes"
    [ -n "$dry_run" ] && log_info "  Dry run: yes"
    
    preflight_check "reconcile" || exit 1
    
    exec paperless-firefly -c "$CONFIG_PATH" reconcile $sync $match $full_sync $dry_run
}

# Main entrypoint logic
main() {
    local command="${1:-server}"
    shift || true
    
    log_info "Paperless-Firefly Pipeline starting..."
    log_info "Command: $command"
    
    # Always validate and generate config
    validate_env
    generate_config
    init_db
    
    case "$command" in
        server)
            run_server
            ;;
        extract)
            run_extract "$@"
            ;;
        import)
            run_import "$@"
            ;;
        pipeline)
            run_pipeline "$@"
            ;;
        status)
            run_status
            ;;
        reconcile)
            run_reconcile "$@"
            ;;
        shell)
            exec /bin/bash
            ;;
        *)
            log_error "Unknown command: $command"
            echo "Available commands:"
            echo "  server    - Start the review web interface (default)"
            echo "  extract   - Run document extraction"
            echo "  import    - Import approved transactions to Firefly"
            echo "  pipeline  - Run full automated pipeline"
            echo "  status    - Show pipeline statistics"
            echo "  reconcile - Run bank reconciliation (Spark v1.0)"
            echo "  shell     - Start a bash shell"
            exit 1
            ;;
    esac
}

main "$@"
