#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  ROOMIEWATCH LAUNCHER — Reliable auto-restart wrapper
# ═══════════════════════════════════════════════════════════════════
#
#  Usage:
#    chmod +x start_roomiewatch.sh
#    ./start_roomiewatch.sh              # motion detection + live stream
#    ./start_roomiewatch.sh --no-stream  # motion detection only
#
#  This script:
#    1. Prevents macOS sleep (caffeinate)
#    2. Auto-restarts roomiewatch if it crashes
#    3. Shows Tailscale remote access URL if connected
#    4. Logs everything to roomiewatch_captures/launcher.log
#    5. Cleans up all child processes on exit
# ═══════════════════════════════════════════════════════════════════

set -e

# ─── Configuration ───────────────────────────────────────────────
STREAM_ENABLED=true
PORT=8080
SENSITIVITY=3
COOLDOWN=5
DURATION=""             # leave empty for unlimited, or set e.g. DURATION=420 for 7 hours
CAMERA=0
MAX_CAPTURES=1000       # max snapshots to keep (0=unlimited)
MAX_RESTARTS=10         # give up after this many restarts in one session
RESTART_DELAY=5         # seconds to wait before restarting
ENABLE_TAILSCALE=true   # set to false if you don't want Tailscale remote access

# Parse args
if [ "$1" = "--no-stream" ]; then
    STREAM_ENABLED=false
fi

# ─── Paths ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
CAPTURE_DIR="$SCRIPT_DIR/roomiewatch_captures"
LOG_FILE="$CAPTURE_DIR/launcher.log"

mkdir -p "$CAPTURE_DIR"

# ─── Logging ─────────────────────────────────────────────────────
log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

# ─── Activate virtual environment ────────────────────────────────
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
    log "Activated venv: $VENV_DIR"
else
    log "WARNING: No venv found at $VENV_DIR"
    log "Tip: pip install roomiewatch"
    log "Falling back to system Python..."
fi

# ─── Cleanup on exit ────────────────────────────────────────────
PIDS_TO_KILL=()

cleanup() {
    log "Shutting down all processes..."
    for pid in "${PIDS_TO_KILL[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            log "  Stopped PID $pid"
        fi
    done
    if [ -n "$CAFFEINATE_PID" ] && kill -0 "$CAFFEINATE_PID" 2>/dev/null; then
        kill "$CAFFEINATE_PID" 2>/dev/null || true
        log "  Stopped caffeinate"
    fi
    log "RoomieWatch launcher stopped."
    exit 0
}

trap cleanup EXIT SIGINT SIGTERM

# ─── Prevent macOS sleep ────────────────────────────────────────
if command -v caffeinate &>/dev/null; then
    caffeinate -is &
    CAFFEINATE_PID=$!
    log "caffeinate started (PID $CAFFEINATE_PID) — system will stay awake"
else
    log "WARNING: caffeinate not found (not macOS?). Make sure system won't sleep."
fi

# ─── Check Tailscale ──────────────────────────────────────────
if [ "$STREAM_ENABLED" = true ] && [ "$ENABLE_TAILSCALE" = true ]; then
    if command -v tailscale &>/dev/null; then
        TAILSCALE_IP=$(tailscale ip -4 2>/dev/null)
        if [ -n "$TAILSCALE_IP" ]; then
            log "═══════════════════════════════════════════"
            log "TAILSCALE ACTIVE"
            log "For remote access, run in another terminal:"
            log "  tailscale serve $PORT"
            log "═══════════════════════════════════════════"
        else
            log "Tailscale is installed but not connected."
            log "Run: tailscale up"
            log "Continuing without remote access — stream on localhost:$PORT only"
        fi
    else
        log "Tailscale not installed. Install with: brew install tailscale"
        log "Continuing without remote access — stream on localhost:$PORT only"
    fi
fi

# ─── Build command ──────────────────────────────────────────────
build_cmd() {
    local cmd="python3 -m roomiewatch"
    cmd="$cmd --sensitivity $SENSITIVITY"
    cmd="$cmd --cooldown $COOLDOWN"
    cmd="$cmd --camera $CAMERA"

    if [ "$STREAM_ENABLED" = true ]; then
        cmd="$cmd --stream --port $PORT"
    fi

    if [ -n "$DURATION" ]; then
        cmd="$cmd --duration $DURATION"
    fi

    cmd="$cmd --max-captures $MAX_CAPTURES"

    echo "$cmd"
}

# ─── Main loop with auto-restart ────────────────────────────────
RESTART_COUNT=0

log "═══════════════════════════════════════════"
log "  ROOMIEWATCH LAUNCHER"
log "  Stream: $STREAM_ENABLED | Port: $PORT"
log "  Sensitivity: $SENSITIVITY | Cooldown: ${COOLDOWN}s"
log "═══════════════════════════════════════════"

while [ $RESTART_COUNT -lt $MAX_RESTARTS ]; do
    CMD=$(build_cmd)
    log "Starting roomiewatch (attempt $((RESTART_COUNT + 1)))..."
    log "Command: $CMD"

    eval "$CMD" &
    WATCH_PID=$!
    PIDS_TO_KILL+=("$WATCH_PID")

    wait "$WATCH_PID" 2>/dev/null
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 130 ] || [ $EXIT_CODE -eq 143 ]; then
        log "RoomieWatch exited cleanly (code $EXIT_CODE). Not restarting."
        break
    fi

    RESTART_COUNT=$((RESTART_COUNT + 1))
    log "RoomieWatch crashed (exit code $EXIT_CODE). Restarting in ${RESTART_DELAY}s... ($RESTART_COUNT/$MAX_RESTARTS)"
    sleep "$RESTART_DELAY"
done

if [ $RESTART_COUNT -ge $MAX_RESTARTS ]; then
    log "ERROR: Max restarts ($MAX_RESTARTS) reached. Giving up."
fi
