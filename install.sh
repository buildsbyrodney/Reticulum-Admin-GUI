#!/bin/sh
# Stable public release metadata. Payloads are always verified before extraction.
PROJECT_OWNER="buildsbyrodney"
PROJECT_REPO="Reticulum-Admin-GUI"
PROJECT_VERSION="0.1.0"
PROJECT_TAG="v0.1.0"
PROJECT_RELEASE_URL="https://github.com/buildsbyrodney/Reticulum-Admin-GUI/releases/download/v0.1.0/reticulum-admin-0.1.0.tar.gz"
PROJECT_CHECKSUM_URL="https://github.com/buildsbyrodney/Reticulum-Admin-GUI/releases/download/v0.1.0/reticulum-admin-0.1.0.tar.gz.sha256"
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ARCH=$(uname -m)
case "$ARCH" in x86_64|aarch64|arm64) ;; *) echo "Unsupported architecture: $ARCH (supported: x86_64, aarch64, arm64)" >&2; exit 1;; esac
has_project() { test -f "$1/compose.yaml" && test -d "$1/reticulum-admin"; }
download() { if command -v curl >/dev/null 2>&1; then curl -fL "$1" -o "$2"; elif command -v wget >/dev/null 2>&1; then wget -O "$2" "$1"; else echo "Install curl or wget to download the release." >&2; return 1; fi; }
verify() { command -v sha256sum >/dev/null 2>&1 || { echo "sha256sum is required; refusing unverified archive." >&2; return 1; }; (cd "$(dirname "$1")" && sha256sum -c "$(basename "$2")"); }
fetch_release() { archive="$1"; checksum="$2"; rm -f "$archive" "$checksum"; download "$PROJECT_RELEASE_URL" "$archive" && download "$PROJECT_CHECKSUM_URL" "$checksum" && verify "$archive" "$checksum" || { echo "Release verification failed; nothing was extracted." >&2; rm -f "$archive" "$checksum"; return 1; }; }
update_local() { TMP=${TMPDIR:-/tmp}/reticulum-admin-$PROJECT_VERSION.tar.gz; SUM="$TMP.sha256"; BACKUP="$ROOT/backup-$(date +%Y%m%d%H%M%S)"; mkdir -p "$BACKUP"; fetch_release "$TMP" "$SUM"; cp -a .env data "$BACKUP/" 2>/dev/null || true; tar -xzf "$TMP" -C "$ROOT" --strip-components=1; rm -f "$TMP" "$SUM"; docker compose up -d --build; echo "Updated to $PROJECT_TAG. Persistent state backed up in $BACKUP."; }
if ! has_project "$ROOT"; then
  if [ "$(id -u)" = 0 ] || [ -w /opt ]; then DEST=/opt/reticulum-admin; else DEST="$HOME/reticulum-admin"; fi
  mkdir -p "$DEST"; TMP=${TMPDIR:-/tmp}/reticulum-admin-$PROJECT_VERSION.tar.gz; SUM="$TMP.sha256"
  echo "Downloading and verifying stable $PROJECT_TAG for $ARCH..."; fetch_release "$TMP" "$SUM"
  BACKUP="$DEST/backup-$(date +%Y%m%d%H%M%S)"; mkdir -p "$BACKUP"
  for item in .env data; do [ -e "$DEST/$item" ] && mv "$DEST/$item" "$BACKUP/"; done
  tar -xzf "$TMP" -C "$DEST" --strip-components=1
  for item in .env data; do [ -e "$BACKUP/$item" ] && mv "$BACKUP/$item" "$DEST/"; done
  rm -f "$TMP" "$SUM"; echo "Installed in $DEST"; exec "$DEST/install.sh"
fi
cd "$ROOT"
APP_VERSION=$(tr -d '\r\n' < VERSION 2>/dev/null || printf '%s' "$PROJECT_VERSION")
command -v docker >/dev/null 2>&1 || { echo "Docker Engine is required. On Debian/Raspberry Pi OS, install Docker Engine and the Compose v2 plugin, then rerun." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required (docker compose)." >&2; exit 1; }
if [ -f .env ]; then
  echo "Reticulum Admin is already installed."; printf "1. Update  2. Reconfigure  3. Repair  4. Cancel [4]: "; read -r choice || true
  case "${choice:-4}" in 1) update_local; exit 0;; 2) mv .env .env.backup-$(date +%Y%m%d%H%M%S);; 3) docker compose up -d --build; exit 0;; *) exit 0;; esac
fi
default_tz=$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo America/Chicago)
printf "Admin web port [8740]: "; read -r PORT || true; PORT=${PORT:-8740}; case "$PORT" in *[!0-9]*|'') echo "Invalid port"; exit 1;; esac
if command -v ss >/dev/null 2>&1 && ss -ltn | awk '{print $4}' | grep -Eq "[:.]$PORT$"; then echo "Port $PORT appears in use."; exit 1; fi
printf "Access: 1) Local Only  2) Network Accessible [1]: "; read -r mode || true; case "${mode:-1}" in 1) BIND=127.0.0.1; ACCESS=local;; 2) BIND=0.0.0.0; ACCESS=network;; *) echo "Invalid access mode"; exit 1;; esac
printf "Timezone [%s]: " "$default_tz"; read -r TZ || true; TZ=${TZ:-$default_tz}
if command -v openssl >/dev/null 2>&1; then SECRET=$(openssl rand -hex 48); TOKEN=$(openssl rand -hex 48); else SECRET=$(od -An -N48 -tx1 /dev/urandom | tr -d ' \n'); TOKEN=$(od -An -N48 -tx1 /dev/urandom | tr -d ' \n'); fi
HOST_OS=$( . /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-Linux}" || echo Linux )
umask 077
cat > .env <<EOF
ADMIN_PORT=$PORT
ADMIN_ACCESS_MODE=$ACCESS
ADMIN_BIND_ADDRESS=$BIND
ADMIN_SECRET_KEY=$SECRET
MANAGEMENT_TOKEN=$TOKEN
SERVER_TIMEZONE=$TZ
RNS_VERSION=1.5.7
RETICULUM_ADMIN_VERSION=$APP_VERSION
OBSERVER_INTERVAL=30
EVENT_RETENTION_DAYS=90
LOG_RETENTION_DAYS=14
HOST_ARCH=$ARCH
HOST_OS=$HOST_OS
INSTALL_DOCKER_VERSION=$(docker version --format '{{.Server.Version}}')
INSTALL_COMPOSE_VERSION=$(docker compose version --short)
EOF
docker compose up -d --build
echo "Reticulum Admin $APP_VERSION started on $ARCH. Open http://$([ "$ACCESS" = local ] && echo 127.0.0.1 || hostname -I | awk '{print $1}'):$PORT"
