#!/bin/sh
set -eu

CONFIG_DIR="/root/.reticulum"
STATUS_DIR="$CONFIG_DIR/admin-status"
CONTROL_DIR="$CONFIG_DIR/admin-control"
RESTART_REQUEST="$CONTROL_DIR/restart-request"
mkdir -p "$CONFIG_DIR" "$STATUS_DIR" "$CONTROL_DIR"

/usr/local/bin/reticulum-observer "$CONFIG_DIR" &
OBSERVER_PID=$!

while true; do
  # rnsd creates its safe default config and persistent storage on its first run.
  rnsd --config "$CONFIG_DIR" --service &
  RNSD_PID=$!
  RESTARTING=no
  while kill -0 "$RNSD_PID" 2>/dev/null; do
    date +%s > "$STATUS_DIR/rnsd.heartbeat"
    if [ -f "$RESTART_REQUEST" ]; then
      rm -f "$RESTART_REQUEST"
      RESTARTING=yes
      kill "$RNSD_PID" 2>/dev/null || true
    fi
    sleep 2
  done
  wait "$RNSD_PID" || true
  [ "$RESTARTING" = yes ] || break
done

kill "$OBSERVER_PID" 2>/dev/null || true
