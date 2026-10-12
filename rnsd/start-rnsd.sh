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
CANDIDATE_READY_TRIES="${RNSD_CANDIDATE_READY_TRIES:-8}"
CANDIDATE_ATTEMPTS="${RNSD_CANDIDATE_ATTEMPTS:-3}"

write_unready() {
  printf '{"reticulum_ready":false,"status":null,"status_error":"%s"}\n' "$1" > "$STATUS_DIR/snapshot.json"
  rm -f "$STATUS_DIR/rnsd.ready"
}
discovery_requested() {
  [ -f "$CONFIG_DIR/config" ] && grep -Eqi '^[[:space:]]*(discover_interfaces|discoverable)[[:space:]]*=[[:space:]]*(yes|true|1)\b' "$CONFIG_DIR/config"
}
verify_discovery_dependency() {
  if discovery_requested && ! /usr/local/bin/check-discovery-dependencies >/dev/null 2>&1; then
    log "Interface Discovery requires LXMF, but LXMF is unavailable in this rnsd image"
    write_unready "Interface Discovery requires the LXMF module to be installed"
    return 1
  fi
  return 0
}
wait_for_discovery_dependency() {
  if verify_discovery_dependency; then return 0; fi
  # Compose intentionally restarts rnsd if it crashes. Do not turn a missing
  # image dependency into an endless restart storm; keep this launcher alive
  # and unhealthy so a management rollback can remove the bad setting.
  log "Discovery is configured but LXMF is unavailable; waiting for configuration rollback or an image update"
  printf '%s\n' '{"lxmf_available":false,"interface_discovery":false,"error":"Discovery requires LXMF in the managed rnsd runtime."}' > "$STATUS_DIR/capabilities.json"
  while ! verify_discovery_dependency; do
    write_unready "Discovery requires LXMF in the managed rnsd runtime"
    sleep 2
  done
  log "LXMF discovery dependency is available"
}
stop_support() {
  [ -n "$OBSERVER_PID" ] && { log "Stopping observer for restart"; kill "$OBSERVER_PID" 2>/dev/null || true; wait "$OBSERVER_PID" 2>/dev/null || true; }
  [ -n "$CONTROL_PID" ] && { log "Stopping control helper for restart"; kill "$CONTROL_PID" 2>/dev/null || true; wait "$CONTROL_PID" 2>/dev/null || true; }
  OBSERVER_PID=""; CONTROL_PID=""; write_unready "rnsd restarting"
}
wait_shared() {
  tries=0
  while [ "$tries" -lt "$CANDIDATE_READY_TRIES" ]; do
    kill -0 "$RNSD_PID" 2>/dev/null || return 1
    if /usr/local/bin/check-shared-instance "$CONFIG_DIR" >/dev/null 2>&1; then return 0; fi
    tries=$((tries + 1)); sleep 1
  done
  return 1
}
# A restart must not race a still-closing local shared instance. The local
# probe uses require_shared_instance, so it cannot create a master while it is
# checking. This also prevents a candidate from silently joining an old owner.
wait_for_no_shared_instance() {
  tries=0
  while [ "$tries" -lt "$CANDIDATE_READY_TRIES" ]; do
    if ! /usr/local/bin/check-shared-instance "$CONFIG_DIR" >/dev/null 2>&1; then return 0; fi
    log "Waiting for previous shared instance to close"
    tries=$((tries + 1)); sleep 1
  done
  return 1
}
stop_candidate() {
  kill "$RNSD_PID" 2>/dev/null || true
  tries=0
  while kill -0 "$RNSD_PID" 2>/dev/null && [ "$tries" -lt 10 ]; do
    tries=$((tries + 1)); sleep 1
  done
  if kill -0 "$RNSD_PID" 2>/dev/null; then
    log "Candidate did not stop cleanly; forcing stop"
    kill -9 "$RNSD_PID" 2>/dev/null || true
  fi
  wait "$RNSD_PID" 2>/dev/null || true
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
  # Verify before starting rnsd. A missing dependency waits in-process for
  # rollback rather than pretending a longer startup timeout can fix it.
  wait_for_discovery_dependency
  # Keep bounded retries in this launcher instead of relying on a container
  # restart. A transient shared-socket race stays inside the health budget.
  candidate_attempt=0
  candidate_ready=no
  while [ "$candidate_attempt" -lt "$CANDIDATE_ATTEMPTS" ]; do
    write_unready "rnsd starting"
    if ! wait_for_no_shared_instance; then
      log "Previous shared instance did not close; refusing to start a competing candidate"
      exit 1
    fi
    log "Starting rnsd candidate generation $((GENERATION + 1))"
    rnsd --config "$CONFIG_DIR" --service &
    RNSD_PID=$!
    log "Waiting for shared instance"
    if wait_shared; then
      candidate_ready=yes
      break
    fi
    diagnostic=$(/usr/local/bin/check-shared-instance "$CONFIG_DIR" 2>&1 || true)
    log "rnsd candidate did not become shared instance${diagnostic:+: $diagnostic}"
    stop_candidate
    candidate_attempt=$((candidate_attempt + 1))
    if [ "$candidate_attempt" -lt "$CANDIDATE_ATTEMPTS" ]; then
      log "Retrying shared-instance startup ($((candidate_attempt + 1))/$CANDIDATE_ATTEMPTS)"
      sleep 1
    fi
  done
  if [ "$candidate_ready" = yes ]; then
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
    log "rnsd did not become shared instance after $CANDIDATE_ATTEMPTS attempts"
    write_unready "rnsd did not become shared instance"
    exit 1
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
