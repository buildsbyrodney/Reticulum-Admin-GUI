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
OBSERVER_PID=""
CONTROL_PID=""

write_unready() {
  printf '{"reticulum_ready":false,"status":null,"status_error":"%s"}\n' "$1" > "$STATUS_DIR/snapshot.json"
  rm -f "$STATUS_DIR/rnsd.ready"
}
stop_support() {
  [ -n "$OBSERVER_PID" ] && { log "Stopping observer for restart"; kill "$OBSERVER_PID" 2>/dev/null || true; wait "$OBSERVER_PID" 2>/dev/null || true; }
  [ -n "$CONTROL_PID" ] && { log "Stopping control helper for restart"; kill "$CONTROL_PID" 2>/dev/null || true; wait "$CONTROL_PID" 2>/dev/null || true; }
  OBSERVER_PID=""; CONTROL_PID=""; write_unready "rnsd restarting"
}
wait_shared() {
  tries=0
  while [ "$tries" -lt 20 ]; do
    if /usr/local/bin/check-shared-instance "$CONFIG_DIR" >/dev/null 2>&1; then return 0; fi
    tries=$((tries + 1)); sleep 1
  done
  return 1
}
start_support() {
  log "Starting observer"
  /usr/local/bin/reticulum-observer "$CONFIG_DIR" & OBSERVER_PID=$!
  log "Starting control helper"
  /usr/local/bin/reticulum-control-helper "$CONFIG_DIR" & CONTROL_PID=$!
  tries=0
  while [ "$tries" -lt 20 ]; do
    if /usr/local/bin/check-rnsd-health >/dev/null 2>&1; then : > "$STATUS_DIR/rnsd.ready"; log "Observer healthy"; return 0; fi
    tries=$((tries + 1)); sleep 1
  done
  return 1
}

while true; do
  # rnsd creates its safe default config and persistent storage on its first run.
  write_unready "rnsd starting"
  log "Starting rnsd candidate generation $((GENERATION + 1))"
  rnsd --config "$CONFIG_DIR" --service &
  RNSD_PID=$!
  log "Waiting for shared instance"
  if kill -0 "$RNSD_PID" 2>/dev/null && wait_shared; then
    log "Shared instance verified"
    GENERATION=$((GENERATION + 1))
    printf '%s\n' "$GENERATION" > "$GENERATION_FILE"
    : > "$OBSERVE_REQUEST"
    log "Generation $GENERATION active"
    if ! start_support; then
      log "Observer did not become healthy; stopping failed candidate"
      stop_support; kill "$RNSD_PID" 2>/dev/null || true; wait "$RNSD_PID" 2>/dev/null || true; exit 1
    fi
    if [ -n "$PENDING_REQUEST" ]; then
      printf '%s\n' "$PENDING_REQUEST" > "$RESTART_ACK"
      log "Restart completed"
      PENDING_REQUEST=""
    fi
  else
    log "rnsd started but did not become shared instance; stopping failed candidate"
    kill "$RNSD_PID" 2>/dev/null || true; wait "$RNSD_PID" 2>/dev/null || true; write_unready "rnsd did not become shared instance"; exit 1
  fi
  RESTARTING=no
  while kill -0 "$RNSD_PID" 2>/dev/null; do
    date +%s > "$STATUS_DIR/rnsd.heartbeat"
    if [ -f "$RESTART_REQUEST" ]; then
      log "Restart requested; stopping generation $GENERATION"
      PENDING_REQUEST=$(cat "$RESTART_REQUEST" 2>/dev/null || true)
      rm -f "$RESTART_REQUEST"
      RESTARTING=yes
      stop_support
      kill "$RNSD_PID" 2>/dev/null || true
    fi
    sleep 2
  done
  wait "$RNSD_PID" || true
  [ "$RESTARTING" = yes ] && log "Generation $GENERATION stopped for restart"
  [ "$RESTARTING" = yes ] || break
done

stop_support
