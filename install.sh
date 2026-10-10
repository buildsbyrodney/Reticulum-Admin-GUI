#!/bin/sh
# Stable public release metadata. Payloads are always verified before extraction.
PROJECT_OWNER="buildsbyrodney"
PROJECT_REPO="Reticulum-Admin-GUI"
PROJECT_VERSION="0.1.3"
PROJECT_TAG="v0.1.3"
ARCHIVE_NAME="reticulum-admin-${PROJECT_VERSION}.tar.gz"
CHECKSUM_NAME="${ARCHIVE_NAME}.sha256"
PROJECT_RELEASE_URL="https://github.com/buildsbyrodney/Reticulum-Admin-GUI/releases/download/${PROJECT_TAG}/${ARCHIVE_NAME}"
PROJECT_CHECKSUM_URL="https://github.com/buildsbyrodney/Reticulum-Admin-GUI/releases/download/${PROJECT_TAG}/${CHECKSUM_NAME}"

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALLER_ARG=${1:-}
NONINTERACTIVE=${NONINTERACTIVE:-no}
[ "$INSTALLER_ARG" = "--non-interactive" ] && NONINTERACTIVE=yes

ARCH=$(uname -m)
case "$ARCH" in
  x86_64|aarch64|arm64) ;;
  *) echo "Unsupported architecture: $ARCH (supported: x86_64, aarch64, arm64)" >&2; exit 1 ;;
esac

has_project() { test -f "$1/compose.yaml" && test -d "$1/reticulum-admin"; }

download() {
  if command -v curl >/dev/null 2>&1; then
    curl -fL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$2" "$1"
  else
    echo "Install curl or wget to download the Reticulum Admin release." >&2
    return 1
  fi
}

verify() {
  command -v sha256sum >/dev/null 2>&1 || {
    echo "sha256sum is required; refusing to extract an unverified archive." >&2
    return 1
  }
  (cd "$(dirname "$1")" && sha256sum -c "$(basename "$2")")
}

fetch_release() {
  archive=$1
  checksum=$2
  rm -f "$archive" "$checksum"
  if ! download "$PROJECT_RELEASE_URL" "$archive" || ! download "$PROJECT_CHECKSUM_URL" "$checksum" || ! verify "$archive" "$checksum"; then
    echo "Release verification failed; nothing was extracted." >&2
    rm -f "$archive" "$checksum"
    return 1
  fi
}

ask_yes() {
  prompt=$1
  if [ "$NONINTERACTIVE" = yes ]; then
    [ "${INSTALL_DOCKER:-no}" = yes ]
    return
  fi
  printf '%s [Y/n] ' "$prompt"
  read -r answer || true
  case "${answer:-Y}" in Y|y|yes|YES|'') return 0 ;; *) return 1 ;; esac
}

ask_group_yes() {
  prompt=$1
  if [ "$NONINTERACTIVE" = yes ]; then
    [ "${INSTALL_DOCKER_GROUP:-no}" = yes ]
    return
  fi
  printf '%s [Y/n] ' "$prompt"
  read -r answer || true
  case "${answer:-Y}" in Y|y|yes|YES|'') return 0 ;; *) return 1 ;; esac
}

require_root() {
  if [ "$(id -u)" = 0 ]; then
    SUDO=
  elif command -v sudo >/dev/null 2>&1 && sudo -v; then
    SUDO=sudo
  else
    echo "Elevated privileges are required. Re-run as root or install/configure sudo." >&2
    return 1
  fi
}

run_root() {
  if [ -n "${SUDO:-}" ]; then sudo "$@"; else "$@"; fi
}

detect_debian_family() {
  if [ ! -r /etc/os-release ]; then
    echo "Cannot detect Linux distribution: /etc/os-release is unavailable." >&2
    return 1
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu) DOCKER_DISTRO=ubuntu ;;
    debian|raspbian) DOCKER_DISTRO=debian ;;
    *)
      case " ${ID_LIKE:-} " in
        *' debian '*) DOCKER_DISTRO=debian ;;
        *)
          echo "Unsupported distribution: ${PRETTY_NAME:-${ID:-unknown}}." >&2
          echo "Install Docker Engine and the Compose v2 plugin manually, then rerun this installer." >&2
          return 1
          ;;
      esac
      ;;
  esac
  DOCKER_CODENAME=${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}
  if [ -z "$DOCKER_CODENAME" ]; then
    echo "No distribution codename was detected in /etc/os-release." >&2
    return 1
  fi
  HOST_OS=${PRETTY_NAME:-$ID}
  DEB_ARCH=$(dpkg --print-architecture)
}

configure_docker_repo() {
  detect_debian_family
  require_root
  echo "Configuring Docker's official APT repository for $HOST_OS ($DOCKER_DISTRO/$DOCKER_CODENAME, $DEB_ARCH)."
  run_root apt-get update
  run_root apt-get install -y ca-certificates curl
  run_root install -m 0755 -d /etc/apt/keyrings
  run_root sh -c "curl -fsSL https://download.docker.com/linux/$DOCKER_DISTRO/gpg -o /etc/apt/keyrings/docker.asc"
  run_root chmod a+r /etc/apt/keyrings/docker.asc
  run_root sh -c "printf '%s\\n' 'deb [arch=$DEB_ARCH signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$DOCKER_DISTRO $DOCKER_CODENAME stable' > /etc/apt/sources.list.d/docker.list"
  run_root apt-get update
}

install_docker_engine() {
  configure_docker_repo
  echo "Installing Docker Engine, Buildx, and Docker Compose v2."
  run_root apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_compose_plugin() {
  configure_docker_repo
  echo "Installing the Docker Compose v2 plugin. Existing Docker Engine packages are not being replaced."
  run_root apt-get install -y docker-compose-plugin
}

docker_cmd() { docker "$@"; }

start_docker_group_shell() {
  if ! command -v sg >/dev/null 2>&1; then
    echo "Cannot start a docker-group shell because sg is unavailable. Log out and back in, then rerun ./install.sh." >&2
    exit 1
  fi
  # Environment variables retain the original argument and installer settings
  # without interpolating user-controlled paths into sg's command string.
  export RETICULUM_ADMIN_REEXEC_ROOT="$ROOT"
  export RETICULUM_ADMIN_REEXEC_ARG="$INSTALLER_ARG"
  exec sg docker -c 'if ! docker info >/dev/null 2>&1; then echo "The new docker-group shell cannot access the Docker daemon. Log out and back in, then rerun ./install.sh." >&2; exit 1; fi; cd "$RETICULUM_ADMIN_REEXEC_ROOT" || exit 1; exec /bin/sh ./install.sh "$RETICULUM_ADMIN_REEXEC_ARG"'
}

configure_docker_access() {
  if docker info >/dev/null 2>&1; then return; fi
  if [ "$(id -u)" = 0 ]; then
    echo "Docker is installed but its daemon is unavailable. Start Docker, then rerun this installer." >&2
    exit 1
  fi

  echo "Your user is not currently able to access Docker without elevated privileges."
  if ! ask_group_yes "Add $(id -un) to the docker group?"; then
    echo "Docker daemon access is required. Add your user to the docker group or rerun as root." >&2
    exit 1
  fi
  require_root
  run_root usermod -aG docker "$(id -un)"

  if [ "$NONINTERACTIVE" = yes ]; then
    echo "Docker group added. Log out and back in (or reboot), then rerun ./install.sh."
    exit 0
  fi
  echo "Docker group added."
  echo
  echo "1. Start a new shell with Docker access now"
  echo "2. Exit and log back in"
  printf 'Choose [1]: '
  read -r choice || true
  case "${choice:-1}" in
    1) start_docker_group_shell ;;
    2)
      echo "Log out and back in (or reboot), then rerun ./install.sh."
      exit 0
      ;;
    *)
      echo "Choose 1 to start a Docker-enabled shell now, or 2 to exit."
      exit 1
      ;;
  esac
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker Engine and Docker Compose v2 are required."
    echo "Docker is not currently installed."
    if ! ask_yes "Install Docker now?"; then
      echo "Install Docker Engine and the Compose v2 plugin, then rerun this installer." >&2
      exit 1
    fi
    install_docker_engine
  elif ! docker compose version >/dev/null 2>&1; then
    echo "Docker exists but Docker Compose v2 is missing."
    if ! ask_yes "Install the Docker Compose v2 plugin now?"; then
      echo "Docker Compose v2 is required (docker compose)." >&2
      exit 1
    fi
    install_compose_plugin
  fi

  docker --version || { echo "Docker installation verification failed." >&2; exit 1; }
  docker compose version || { echo "Docker Compose v2 verification failed." >&2; exit 1; }
  configure_docker_access
}

update_local() {
  TMP=${TMPDIR:-/tmp}/$ARCHIVE_NAME
  SUM="${TMPDIR:-/tmp}/$CHECKSUM_NAME"
  BACKUP="$ROOT/backup-$(date +%Y%m%d%H%M%S)"
  mkdir -p "$BACKUP"
  fetch_release "$TMP" "$SUM"
  cp -a "$ROOT/.env" "$ROOT/data" "$BACKUP/" 2>/dev/null || true
  tar -xzf "$TMP" -C "$ROOT" --strip-components=1
  rm -f "$TMP" "$SUM"
  docker_cmd compose up -d --build
  echo "Updated to $PROJECT_TAG. Persistent state backed up in $BACKUP."
}

if ! has_project "$ROOT"; then
  if [ "$(id -u)" = 0 ] || [ -w /opt ]; then DEST=/opt/reticulum-admin; else DEST="$HOME/reticulum-admin"; fi
  TMPDIR_INSTALL=$(mktemp -d)
  trap 'rm -rf "$TMPDIR_INSTALL"' EXIT HUP INT TERM
  fetch_release "$TMPDIR_INSTALL/$ARCHIVE_NAME" "$TMPDIR_INSTALL/$CHECKSUM_NAME"
  mkdir -p "$DEST"
  if has_project "$DEST"; then
    BACKUP="$DEST/backup-$(date +%Y%m%d%H%M%S)"
    mkdir -p "$BACKUP"
    cp -a "$DEST/.env" "$DEST/data" "$BACKUP/" 2>/dev/null || true
    echo "Existing configuration and persistent data backed up in $BACKUP"
  fi
  tar -xzf "$TMPDIR_INSTALL/$ARCHIVE_NAME" -C "$DEST" --strip-components=1
  echo "Reticulum Admin installed in $DEST"
  cd "$DEST"
  exec /bin/sh "$DEST/install.sh" "$INSTALLER_ARG"
fi

cd "$ROOT"
ensure_docker

if [ -f .env ]; then
  if [ "$NONINTERACTIVE" = yes ]; then choice=${INSTALL_ACTION:-3}; else
    echo "Reticulum Admin is already installed."
    echo "1. Update  2. Reconfigure  3. Repair/start  4. Cancel"
    printf 'Choose [4]: '; read -r choice || true
  fi
  case "${choice:-4}" in
    1) update_local; exit 0 ;;
    2) mv .env ".env.backup-$(date +%Y%m%d%H%M%S)" ;;
    3) docker_cmd compose up -d --build; exit 0 ;;
    *) exit 0 ;;
  esac
fi

if [ "$NONINTERACTIVE" = yes ]; then
  ADMIN_PORT=${ADMIN_PORT:-8740}
  ADMIN_ACCESS=${ADMIN_ACCESS:-${ADMIN_ACCESS_MODE:-local}}
  SERVER_TIMEZONE=${SERVER_TIMEZONE:-America/Chicago}
else
  printf 'Admin web port [8740]: '; read -r ADMIN_PORT || true; ADMIN_PORT=${ADMIN_PORT:-8740}
  echo "1. Local Only (127.0.0.1)  2. Network Accessible (0.0.0.0)"
  printf 'Access mode [1]: '; read -r access || true
  case "${access:-1}" in 2) ADMIN_ACCESS=network;; *) ADMIN_ACCESS=local;; esac
  detected_tz=$(cat /etc/timezone 2>/dev/null || true); SERVER_TIMEZONE=${detected_tz:-America/Chicago}
  printf 'Server timezone [%s]: ' "$SERVER_TIMEZONE"; read -r tz || true; SERVER_TIMEZONE=${tz:-$SERVER_TIMEZONE}
fi

case "$ADMIN_PORT" in *[!0-9]*|'') echo "Port must be numeric." >&2; exit 1;; esac
if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$ADMIN_PORT" | grep -q LISTEN; then
  echo "Port $ADMIN_PORT appears to be in use; choose another port." >&2
  exit 1
fi
if [ "$ADMIN_ACCESS" = network ]; then ADMIN_BIND=0.0.0.0; else ADMIN_BIND=127.0.0.1; fi
ADMIN_SECRET_KEY=$(dd if=/dev/urandom bs=32 count=1 2>/dev/null | base64 | tr -d '\n')
MANAGEMENT_TOKEN=$(dd if=/dev/urandom bs=32 count=1 2>/dev/null | base64 | tr -d '\n')
APP_VERSION=$(tr -d '\r\n' < VERSION)
cat > .env <<EOF
ADMIN_PORT=$ADMIN_PORT
ADMIN_ACCESS_MODE=$ADMIN_ACCESS
ADMIN_BIND_ADDRESS=$ADMIN_BIND
SERVER_TIMEZONE=$SERVER_TIMEZONE
ADMIN_SECRET_KEY=$ADMIN_SECRET_KEY
MANAGEMENT_TOKEN=$MANAGEMENT_TOKEN
RETICULUM_ADMIN_VERSION=$APP_VERSION
EOF

docker_cmd compose up -d --build
INSTALL_DOCKER_VERSION=$(docker_cmd version --format '{{.Server.Version}}')
INSTALL_COMPOSE_VERSION=$(docker_cmd compose version --short)
echo "Reticulum Admin $APP_VERSION is running at http://$ADMIN_BIND:$ADMIN_PORT"
echo "Docker: $INSTALL_DOCKER_VERSION; Compose: $INSTALL_COMPOSE_VERSION"
echo "Next: open the URL and create the first administrator account."
