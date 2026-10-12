#!/bin/sh
set -eu

# The Pi regression: rnstatus cannot confirm the fresh shared instance until
# 18 seconds after the candidate starts. The launcher must wait, not kill it.
image="reticulum-admin-rnsd-readiness-test:$$"
container="reticulum-admin-rnsd-readiness-test-$$"
state=$(mktemp -d)
probe_dir=$(mktemp -d)
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -rf "$state" "$probe_dir" || true
}
trap cleanup EXIT INT TERM
cat > "$state/config" <<'EOF'
[reticulum]
share_instance = Yes
instance_name = delayed-readiness-regression
EOF
cat > "$probe_dir/delayed-probe" <<'EOF'
#!/bin/sh
# Before rnsd exists this is the old-owner check and should stay immediate.
marker="$1/admin-status/rnsd.candidate-started"
if [ ! -s "$marker" ]; then
  exec /usr/local/bin/check-shared-instance "$@"
fi
candidate_pid=$(cat "$marker")
if [ ! -f /test/candidate-started ]; then
  # The first probe after the launcher writes the candidate PID is the only
  # valid start of this artificial delay; Docker build/setup time is excluded.
  date +%s > /test/candidate-started
  printf '%s\n' "$candidate_pid" > /test/candidate-pid
  if [ -f "$1/admin-status/rnsd.generation" ]; then
    cat "$1/admin-status/rnsd.generation" > /test/generation-before
  else
    printf '0\n' > /test/generation-before
  fi
fi
started=$(cat /test/candidate-started)
expected_pid=$(cat /test/candidate-pid)
before_generation=$(cat /test/generation-before)
current_generation=$(cat "$1/admin-status/rnsd.generation" 2>/dev/null || printf '0')
test "$candidate_pid" = "$expected_pid"
kill -0 "$candidate_pid"
test "$current_generation" = "$before_generation"
if [ $(( $(date +%s) - started )) -lt 18 ]; then
  sleep 2
  exit 1
fi
exec /usr/local/bin/check-shared-instance "$@"
EOF
chmod +x "$probe_dir/delayed-probe"
docker build -t "$image" ./rnsd >/dev/null
docker run -d --name "$container" --network host \
  -e RNS_SHARED_PROBE_COMMAND=/test/delayed-probe \
  -v "$state:/root/.reticulum" -v "$probe_dir:/test" "$image" >/dev/null
attempt=0
while [ "$attempt" -lt 35 ]; do
  if docker exec "$container" /usr/local/bin/check-rnsd-health >/dev/null 2>&1; then break; fi
  attempt=$((attempt + 1)); sleep 1
done
docker exec "$container" /usr/local/bin/check-rnsd-health
elapsed=$(( $(date +%s) - $(cat "$probe_dir/candidate-started") ))
test "$elapsed" -ge 18
test "$(cat "$probe_dir/generation-before")" = 0
candidate_pid=$(cat "$probe_dir/candidate-pid")
docker exec "$container" sh -c "kill -0 $candidate_pid"
test "$(docker exec "$container" cat /root/.reticulum/admin-status/rnsd.generation)" = 1
docker logs "$container" | grep -F 'Shared instance verified'
if docker logs "$container" | grep -Fq 'candidate did not become shared instance'; then
  echo "Delayed healthy candidate was killed prematurely" >&2
  exit 1
fi
test "$(docker logs "$container" | grep -Fc 'Starting rnsd candidate generation 1')" = 1
