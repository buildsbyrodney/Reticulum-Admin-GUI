#!/bin/sh
set -eu

# This uses the image Dockerfiles themselves. Host Python only parses the HTTP
# response; all Reticulum and LXMF imports happen inside the built containers.
rnsd_image="reticulum-admin-rnsd-discovery-test:$$"
agent_image="reticulum-admin-agent-discovery-test:$$"
bot_image="reticulum-admin-bot-discovery-test:$$"
missing_image="reticulum-admin-rnsd-no-lxmf-test:$$"
rnsd_container="reticulum-admin-rnsd-discovery-test-$$"
agent_container="reticulum-admin-agent-discovery-test-$$"
bot_container="reticulum-admin-bot-discovery-test-$$"
missing_container="reticulum-admin-rnsd-no-lxmf-test-$$"
state=$(mktemp -d)
missing_state=$(mktemp -d)
bot_state=$(mktemp -d)
bot_status=$(mktemp -d)
bot_config=$(mktemp -d)
port=18081
cleanup() {
  docker rm -f "$missing_container" "$bot_container" "$agent_container" "$rnsd_container" >/dev/null 2>&1 || true
  # Containers run as root and own files beneath these test mounts. The hosted
  # runner is ephemeral, so cleanup must not turn a passing test into failure.
  rm -rf "$state" "$missing_state" "$bot_state" "$bot_status" "$bot_config" || true
}
trap cleanup EXIT INT TERM

wait_for() {
  limit=$1
  shift
  attempts=0
  while [ "$attempts" -lt "$limit" ]; do
    if "$@"; then return 0; fi
    attempts=$((attempts + 1))
    sleep 1
  done
  return 1
}

docker build -t "$rnsd_image" ./rnsd >/dev/null
docker build -t "$agent_image" ./management-agent >/dev/null
docker build -t "$bot_image" ./lxmf-test-bot >/dev/null

# Baseline: Discovery disabled must retain ordinary rnsd startup behavior.
docker run -d --name "$rnsd_container" --network host -v "$state:/root/.reticulum" "$rnsd_image" >/dev/null
if ! wait_for 35 docker exec "$rnsd_container" /usr/local/bin/check-rnsd-health; then
  docker logs "$rnsd_container" >&2 || true
  echo "Discovery-disabled rnsd did not become healthy" >&2
  exit 1
fi
generation_before=$(docker exec "$rnsd_container" sh -c 'cat /root/.reticulum/admin-status/rnsd.generation')
docker exec "$rnsd_container" python -c 'import RNS, LXMF; from importlib.metadata import version; assert version("rns") == "1.5.7"; assert version("lxmf") == "0.9.6"'

# LXMF being available in rnsd must not create a router there. The existing
# bot remains the only LXMF router and attaches as one shared-instance client.
docker run -d --name "$bot_container" --network host \
  -e BOT_STATE_DIR=/bot -e BOT_STATUS_DIR=/status -e BOT_CONFIG_DIR=/config \
  -v "$state:/reticulum:ro" -v "$bot_state:/bot" -v "$bot_status:/status" -v "$bot_config:/config" "$bot_image" >/dev/null
if ! wait_for 35 sh -c "test -s '$bot_status/status.json' && grep -q '\"running\": true' '$bot_status/status.json'"; then
  docker logs "$bot_container" >&2 || true
  echo "LXMF bot did not attach to the rnsd shared instance" >&2
  exit 1
fi
docker exec "$rnsd_container" rnstatus --config /root/.reticulum | grep -Eq 'Serving[[:space:]]*:[[:space:]]*1 program'

# Actual management image applies the same config path the GUI uses. The rnsd
# image then restarts, creates generation N+1, and the observer verifies the
# shared instance before the operation may return Applied.
docker run -d --name "$agent_container" -p "127.0.0.1:${port}:8081" \
  -e MANAGEMENT_TOKEN=discovery-test-token -e RETICULUM_DIR=/reticulum \
  -e RESTART_HEALTH_TIMEOUT=70 -v "$state:/reticulum" "$agent_image" >/dev/null
if ! wait_for 20 sh -c "curl -fsS -H 'X-Management-Token: discovery-test-token' http://127.0.0.1:${port}/config >/dev/null"; then
  docker logs "$agent_container" >&2 || true
  echo "Management agent did not become ready" >&2
  exit 1
fi
capabilities=$(curl -fsS -H 'X-Management-Token: discovery-test-token' "http://127.0.0.1:${port}/config")
printf '%s' "$capabilities" | python -c 'import json,sys; value=json.load(sys.stdin)["config"]["discovery_capabilities"]; assert value["lxmf_available"] is True, value; assert value["supported"] is True, value'
response=$(curl -fsS -X POST -H 'Content-Type: application/json' -H 'X-Management-Token: discovery-test-token' \
  --data '{"admin":"ci","reason":"Discovery regression","operation_id":"CFG-20261012-000000-ABCDEF","change":{"kind":"global_batch","changes":{"discover_interfaces":true}}}' \
  "http://127.0.0.1:${port}/config/apply")
printf '%s' "$response" | python -c 'import json,sys; value=json.load(sys.stdin); assert value.get("ok") is True, value; assert not value.get("rolled_back"), value'
generation_after=$(docker exec "$rnsd_container" sh -c 'cat /root/.reticulum/admin-status/rnsd.generation')
if [ "$generation_after" -le "$generation_before" ]; then
  echo "Generation did not advance after Discovery apply" >&2
  exit 1
fi
docker exec "$rnsd_container" /usr/local/bin/check-rnsd-health
docker exec "$rnsd_container" rnstatus --config /root/.reticulum --json --all | grep -q 'Shared Instance\['
docker exec "$rnsd_container" grep -Eqi '^\s*discover_interfaces\s*=\s*Yes\b' /root/.reticulum/config

# The actual management image must place the Reticulum log level in [logging],
# not the similarly named but ineffective legacy key in [reticulum]. This also
# verifies the restart-and-observer health path for this global config change.
response=$(curl -fsS -X POST -H 'Content-Type: application/json' -H 'X-Management-Token: discovery-test-token' \
  --data '{"admin":"ci","reason":"Logging regression","operation_id":"CFG-20261012-000001-ABCDEF","change":{"kind":"global","setting":"loglevel","value":"6"}}' \
  "http://127.0.0.1:${port}/config/apply")
printf '%s' "$response" | python -c 'import json,sys; value=json.load(sys.stdin); assert value.get("ok") is True, value; assert not value.get("rolled_back"), value'
docker exec "$rnsd_container" /usr/local/bin/check-rnsd-health
docker exec "$rnsd_container" python -c 'from pathlib import Path; import re; text=Path("/root/.reticulum/config").read_text(); logging=re.search(r"(?ms)^\s*\[logging\]\s*$.*?(?=^\s*\[|\Z)", text); reticulum=re.search(r"(?ms)^\s*\[reticulum\]\s*$.*?(?=^\s*\[|\Z)", text); assert logging and re.search(r"^\s*loglevel\s*=\s*6\s*$", logging.group(), re.M), text; assert not re.search(r"^\s*loglevel\s*=", reticulum.group() if reticulum else "", re.M), text'

# Deliberately remove LXMF from a derivative of the actual image. A
# Discovery-enabled service must remain running-but-unhealthy with an explicit
# error, rather than making Docker repeatedly recreate the container.
cat > "$missing_state/config" <<'EOF'
[reticulum]
share_instance = Yes
instance_name = missing-lxmf-regression
discover_interfaces = Yes
EOF
missing_dockerfile=$(mktemp)
printf 'FROM %s\nRUN pip uninstall -y lxmf\n' "$rnsd_image" > "$missing_dockerfile"
docker build -t "$missing_image" -f "$missing_dockerfile" . >/dev/null
rm -f "$missing_dockerfile"
docker run -d --name "$missing_container" -v "$missing_state:/root/.reticulum" "$missing_image" >/dev/null
sleep 4
test "$(docker inspect -f '{{.State.Running}}' "$missing_container")" = true
docker exec "$missing_container" grep -F 'Discovery requires LXMF in the managed rnsd runtime' /root/.reticulum/admin-status/snapshot.json
