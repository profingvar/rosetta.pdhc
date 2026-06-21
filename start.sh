#!/usr/bin/env bash
# rosetta.pdhc prod entry point (Rule 16 / CLAUDE.md §6).
#
# Patterned on gateway.pdhc.se/safe_restart.sh. Changes from the prior version:
#   - gunicorn daemon (was flask dev server — CLAUDE.md §6 violation)
#   - OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES (macOS fork-safety; prevents
#     the post-max-requests SIGKILL spiral that hit gateway.pdhc 2026-04-16)
#   - pid file under shared/ (survives release swaps per CLAUDE.md §7)
#   - binds 127.0.0.1 only (was 0.0.0.0 in script text — CLAUDE.md §3)
#   - kills only port 9092 (app). NEVER kills 9091 — that's the docker DB
#     forward, and killing it triggers the Colima host↔VM socket break (see
#     feedback memory "Colima socket-forwarding break → colima restart").
#   - bounded health-check exit (no more blocking `wait`)
set -e

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# macOS ObjC fork-safety: CoreFoundation in parent poisons fork()s; setting
# this env var before gunicorn prevents the SIGKILL spiral after worker recycles.
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd -P)"

# Layout detect: prod uses release-symlink (current/ -> releases/TS/) with SHARED
# as a sibling of current/; local dev is a flat repo, so SHARED lives inside it.
if [ -L "$PROJECT_DIR/../../current" ] && [ -d "$PROJECT_DIR/../../shared" ]; then
    ROOT="$(cd "$PROJECT_DIR/../.." && pwd -P)"   # /usr/local/www/rosetta.pdhc
    SHARED="$ROOT/shared"
else
    ROOT="$PROJECT_DIR"
    SHARED="$ROOT/.shared"
fi
APP_PORT="${APP_PORT:-9092}"
PID_FILE="$SHARED/gunicorn.pid"
LOG_DIR="$SHARED/logs"
HEALTH_URL="http://127.0.0.1:${APP_PORT}/healthz"

mkdir -p "$LOG_DIR"

echo "=== rosetta.pdhc start — $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "  PROJECT_DIR: $PROJECT_DIR"
echo "  SHARED:      $SHARED"
echo "  APP_PORT:    $APP_PORT"

# Step 1: Stop any existing process (gunicorn master via pid file, or a stale flask dev server on the app port).
echo "Stopping existing application..."
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$OLD_PID" ]; then
        kill -TERM "$OLD_PID" 2>/dev/null || true
        for i in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$OLD_PID" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi
# Belt-and-braces for non-pid-file leftovers (e.g. legacy flask-dev process).
# IMPORTANT: only port 9092 (app). NEVER 9091 (docker DB forward) — killing the
# docker-proxy on 9091 triggers the colima host↔VM socket bridge break.
lsof -ti :"$APP_PORT" 2>/dev/null | xargs kill -TERM 2>/dev/null || true
sleep 1
lsof -ti :"$APP_PORT" 2>/dev/null | xargs kill -9 2>/dev/null || true

# Step 2: Ensure docker is reachable (do NOT start/restart colima unprompted).
cd "$PROJECT_DIR"
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: 'docker info' failed — host↔VM socket bridge is likely down."
    echo "       Operator should run 'colima restart' and re-run this script."
    exit 1
fi
DC="docker compose"
command -v docker-compose >/dev/null 2>&1 && DC="docker-compose"

echo "Loading .env..."
set -a; . ./.env; set +a

# Step 3: Ensure Postgres container is up (re-creating only if missing).
if ! docker ps --format '{{.Names}}' | grep -q '^rosetta_pdhc_db$'; then
    echo "Starting rosetta_pdhc_db..."
    $DC -p rosetta_pdhc up -d db
fi
echo "Waiting for Postgres..."
for i in 1 2 3 4 5 6 7 8 9 10; do
    if docker exec rosetta_pdhc_db pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        echo "  DB ready after ${i}s"
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo "ERROR: Postgres not ready after 10s — abort"
        exit 1
    fi
    sleep 1
done

# Step 4: Activate venv + run migrations.
echo "Activating venv..."
# shellcheck disable=SC1091
. app/.venv/bin/activate

echo "Running migrations..."
FLASK_APP=app:create_app flask db upgrade 2>/dev/null || echo "  (no pending migrations or first run)"

# Step 5: Start gunicorn as daemon.
echo "Starting gunicorn on 127.0.0.1:${APP_PORT}..."
gunicorn \
    --bind "127.0.0.1:${APP_PORT}" \
    --workers 2 \
    --timeout 120 \
    --graceful-timeout 30 \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --daemon \
    --pid "$PID_FILE" \
    --access-logfile "$LOG_DIR/gunicorn.access.log" \
    --error-logfile  "$LOG_DIR/gunicorn.error.log" \
    "app:create_app()"

# Step 6: Bounded health check.
echo "Verifying health..."
for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    code=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$code" = "200" ]; then
        echo "Health check: OK (attempt $i)"
        echo "PID: $(cat "$PID_FILE" 2>/dev/null || echo unknown)"
        exit 0
    fi
    echo "  attempt $i: HTTP $code"
done
echo "ERROR: health check failed after 10 attempts."
echo "Last 40 lines of $LOG_DIR/gunicorn.error.log:"
tail -40 "$LOG_DIR/gunicorn.error.log" 2>/dev/null
exit 1
