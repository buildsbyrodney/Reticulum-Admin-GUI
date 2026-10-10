#!/bin/sh
set -eu

CONFIG_DIR="/root/.reticulum"
STATUS_DIR="$CONFIG_DIR/admin-status"
mkdir -p "$CONFIG_DIR" "$STATUS_DIR"

# rnsd creates its safe default config and persistent storage on its first run.
rnsd --config "$CONFIG_DIR" --service &
RNSD_PID=$!
/usr/local/bin/reticulum-observer "$CONFIG_DIR" &
OBSERVER_PID=$!

while kill -0 "$RNSD_PID" 2>/dev/null; do
  date +%s > "$STATUS_DIR/rnsd.heartbeat"
  sleep 15
done

kill "$OBSERVER_PID" 2>/dev/null || true
wait "$RNSD_PID"
