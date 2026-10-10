#!/bin/sh
set -eu

CONFIG_DIR="/root/.reticulum"
STATUS_DIR="$CONFIG_DIR/admin-status"
CONTROL_DIR="$CONFIG_DIR/admin-control"
RESTART_REQUEST="$CONTROL_DIR/restart-request"
GENERATION_FILE="$STATUS_DIR/rnsd.generation"
RESTART_ACK="$STATUS_DIR/rnsd.restart-ack"
OBSERVE_REQUEST="$CONTROL_DIR/observe-request"
LOCK_FILE="$CONTROL_DIR/rnsd.lock"
mkdir -p "$CONFIG_DIR" "$STATUS_DIR" "$CONTROL_DIR"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) managed-rnsd: $*"; }
# Keep descriptor 9 open for the launcher's lifetime. flock releases it on a
# container crash/exit; no stale PID or manual lock cleanup is required.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "Another managed Reticulum instance already owns this state directory. Refusing to start."
  exit 1
fi
log "Managed rnsd launcher started"
log "Acquired Reticulum state lock"

GENERATION=0
if [ -f "$GENERATION_FILE" ]; then
  GENERATION=$(cat "$GENERATION_FILE" 2>/dev/null || printf '0')
fi
case "$GENERATION" in ''|*[!0-9]*) GENERATION=0 ;; esac
PENDING_REQUEST=""

/usr/local/bin/reticulum-observer "$CONFIG_DIR" &
OBSERVER_PID=$!
/usr/local/bin/reticulum-control-helper "$CONFIG_DIR" &
CONTROL_PID=$!

while true; do
  # rnsd creates its safe default config and persistent storage on its first run.
  log "Starting rnsd generation $((GENERATION + 1))"
  rnsd --config "$CONFIG_DIR" --service &
  RNSD_PID=$!
  # A generation advances only for a process that survived its initial launch.
  sleep 1
  if kill -0 "$RNSD_PID" 2>/dev/null; then
    GENERATION=$((GENERATION + 1))
    printf '%s\n' "$GENERATION" > "$GENERATION_FILE"
    # Ask the long-running observer for an immediate, generation-tagged probe.
    : > "$OBSERVE_REQUEST"
    log "Generation $GENERATION started; waiting for observer readiness"
    if [ -n "$PENDING_REQUEST" ]; then
      printf '%s\n' "$PENDING_REQUEST" > "$RESTART_ACK"
      PENDING_REQUEST=""
    fi
  fi
  RESTARTING=no
  while kill -0 "$RNSD_PID" 2>/dev/null; do
    date +%s > "$STATUS_DIR/rnsd.heartbeat"
    if [ -f "$RESTART_REQUEST" ]; then
      log "Restart requested; stopping generation $GENERATION"
      PENDING_REQUEST=$(cat "$RESTART_REQUEST" 2>/dev/null || true)
      rm -f "$RESTART_REQUEST"
      RESTARTING=yes
      kill "$RNSD_PID" 2>/dev/null || true
    fi
    sleep 2
  done
  wait "$RNSD_PID" || true
  [ "$RESTARTING" = yes ] && log "Generation $GENERATION stopped for restart"
  [ "$RESTARTING" = yes ] || break
done

kill "$OBSERVER_PID" 2>/dev/null || true
kill "$CONTROL_PID" 2>/dev/null || true
