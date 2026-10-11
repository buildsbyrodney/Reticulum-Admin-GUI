#!/bin/sh
# Read-only host diagnostics; run from the installation directory.
set -u
echo "Reticulum Admin diagnostic"
echo "Architecture: $(uname -m)"
echo "OS: $(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
if [ "$(uname -s)" = Linux ]; then
  echo "rnsd networking: host network mode (native Linux expected)"
  echo "Host IPv4 addresses:"
  ip -4 -brief addr 2>/dev/null || echo "ip command unavailable"
else
  echo "rnsd networking: unsupported host platform (native Linux is required)"
fi
docker version --format 'Docker: {{.Server.Version}}' 2>/dev/null || echo "Docker server unavailable"
docker compose version 2>/dev/null || echo "Compose v2 unavailable"
docker compose ps || exit 1
docker compose exec -T rnsd rnsd --version || true
echo "rnsd-visible IPv4 addresses:"
docker compose exec -T rnsd ip -4 -brief addr 2>/dev/null || echo "unable to inspect rnsd network namespace"
docker compose exec -T rnsd rnstatus --config /root/.reticulum --json --all >/dev/null && echo "rnstatus: OK" || echo "rnstatus: FAILED"
docker compose exec -T rnsd rnpath --config /root/.reticulum --table --json >/dev/null && echo "rnpath: OK" || echo "rnpath: FAILED"
docker compose exec -T reticulum-admin sh -c 'test -w /var/lib/reticulum-admin && test -r /reticulum/admin-status/snapshot.json' && echo "persistent mounts: OK" || echo "persistent mounts: check failed"
docker compose logs --tail=50 rnsd management-agent reticulum-admin
