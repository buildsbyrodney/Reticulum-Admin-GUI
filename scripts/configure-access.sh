#!/bin/sh
# Usage: ./scripts/configure-access.sh local 8740
#        ./scripts/configure-access.sh network 8740
set -eu
MODE="${1:-local}"
PORT="${2:-8740}"
case "$MODE" in
  local) BIND="127.0.0.1" ;;
  network) BIND="0.0.0.0" ;;
  *) echo "Usage: $0 {local|network} [port]" >&2; exit 2 ;;
esac
test -f .env || cp .env.example .env
sed -i.bak -e "s/^ADMIN_PORT=.*/ADMIN_PORT=$PORT/" -e "s/^ADMIN_ACCESS_MODE=.*/ADMIN_ACCESS_MODE=$MODE/" -e "s/^ADMIN_BIND_ADDRESS=.*/ADMIN_BIND_ADDRESS=$BIND/" .env
rm -f .env.bak
printf 'Configured %s access on port %s. Review ADMIN_SECRET_KEY in .env.\n' "$MODE" "$PORT"
