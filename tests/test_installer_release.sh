#!/bin/sh
# Verify that installer download names match the filename recorded by sha256sum.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=$(tr -d '\r\n' < "$ROOT/VERSION")
PROJECT_VERSION=$(sed -n 's/^PROJECT_VERSION="\(.*\)"$/\1/p' "$ROOT/install.sh")
PROJECT_TAG=$(sed -n 's/^PROJECT_TAG="\(.*\)"$/\1/p' "$ROOT/install.sh")
if [ "$VERSION" != "$PROJECT_VERSION" ] || [ "v$VERSION" != "$PROJECT_TAG" ]; then
  echo "VERSION, PROJECT_VERSION, and PROJECT_TAG must describe the same release." >&2
  exit 1
fi
grep -Fx "RETICULUM_ADMIN_VERSION=$VERSION" "$ROOT/.env.example" >/dev/null
grep -Fx "WEB_PUSH_VAPID_SUBJECT=" "$ROOT/.env.example" >/dev/null
grep -F 'WEB_PUSH_VAPID_SUBJECT: ${WEB_PUSH_VAPID_SUBJECT:-}' "$ROOT/compose.yaml" >/dev/null
grep -F "RETICULUM_ADMIN_VERSION: \${RETICULUM_ADMIN_VERSION:-$VERSION}" "$ROOT/compose.yaml" >/dev/null
grep -F "\"version\":\"$VERSION\"" "$ROOT/release.json" >/dev/null
ARCHIVE_NAME="reticulum-admin-${VERSION}.tar.gz"
CHECKSUM_NAME="${ARCHIVE_NAME}.sha256"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT HUP INT TERM

make_release() {
  target=$1
  mkdir -p "$target/payload/reticulum-admin-$VERSION"
  printf '%s\n' "$VERSION" > "$target/payload/reticulum-admin-$VERSION/VERSION"
  tar -C "$target/payload" -czf "$target/$ARCHIVE_NAME" "reticulum-admin-$VERSION"
  (cd "$target" && sha256sum "$ARCHIVE_NAME" > "$CHECKSUM_NAME")
}

verify_and_extract() {
  target=$1
  install_dir=$2
  (cd "$target" && sha256sum -c "$CHECKSUM_NAME")
  mkdir -p "$install_dir"
  tar -xzf "$target/$ARCHIVE_NAME" -C "$install_dir" --strip-components=1
  test "$(tr -d '\r\n' < "$install_dir/VERSION")" = "$VERSION"
}

# Standalone flow: the release's published archive name is retained locally.
make_release "$WORK/published"
mkdir -p "$WORK/standalone"
cp "$WORK/published/$ARCHIVE_NAME" "$WORK/published/$CHECKSUM_NAME" "$WORK/standalone/"
verify_and_extract "$WORK/standalone" "$WORK/standalone-extracted"

# A local rename must fail, exactly as the Raspberry Pi report demonstrated.
mv "$WORK/standalone/$ARCHIVE_NAME" "$WORK/standalone/renamed.tar.gz"
if (cd "$WORK/standalone" && sha256sum -c "$CHECKSUM_NAME") >/dev/null 2>&1; then
  echo "Checksum verification unexpectedly accepted a renamed archive." >&2
  exit 1
fi

# Update flow also retains the release filename and extracts successfully.
mkdir -p "$WORK/update"
cp "$WORK/published/$ARCHIVE_NAME" "$WORK/published/$CHECKSUM_NAME" "$WORK/update/"
mkdir -p "$WORK/update-extracted/data/messaging" "$WORK/update-extracted/data/messaging-requests" "$WORK/update-extracted/data/nomad" "$WORK/update-extracted/data/nomad-requests"
printf '%s\n' 'persistent-messaging-identity' > "$WORK/update-extracted/data/messaging/identity"
python3 -c 'import sqlite3,sys; con=sqlite3.connect(sys.argv[1]); con.executescript("CREATE TABLE messages(body TEXT); CREATE TABLE contacts(name TEXT); CREATE TABLE announced_peers(display_name TEXT);"); con.execute("INSERT INTO messages VALUES (\"existing message\")"); con.execute("INSERT INTO contacts VALUES (\"Existing contact\")"); con.execute("INSERT INTO announced_peers VALUES (\"Existing announced peer\")"); con.commit()' "$WORK/update-extracted/data/messaging/messages.db"
printf '%s\n' 'persistent-messaging-request' > "$WORK/update-extracted/data/messaging-requests/outbound-test.json"
printf '%s\n' 'persistent-nomad-cache' > "$WORK/update-extracted/data/nomad/cache-marker"
printf '%s\n' 'persistent-nomad-request' > "$WORK/update-extracted/data/nomad-requests/browse-test.json"
mkdir -p "$WORK/update-extracted/data/reticulum" "$WORK/update-extracted/data/bot"
printf '%s\n' 'persistent-reticulum-config' > "$WORK/update-extracted/data/reticulum/config"
printf '%s\n' 'persistent-bot-identity' > "$WORK/update-extracted/data/bot/identity"
verify_and_extract "$WORK/update" "$WORK/update-extracted"
test "$(cat "$WORK/update-extracted/data/messaging/identity")" = "persistent-messaging-identity"
python3 -c 'import sqlite3,sys; con=sqlite3.connect(sys.argv[1]); assert con.execute("SELECT body FROM messages").fetchone()[0] == "existing message"; assert con.execute("SELECT name FROM contacts").fetchone()[0] == "Existing contact"; assert con.execute("SELECT display_name FROM announced_peers").fetchone()[0] == "Existing announced peer"' "$WORK/update-extracted/data/messaging/messages.db"
test "$(cat "$WORK/update-extracted/data/messaging-requests/outbound-test.json")" = "persistent-messaging-request"
test "$(cat "$WORK/update-extracted/data/nomad/cache-marker")" = "persistent-nomad-cache"
test "$(cat "$WORK/update-extracted/data/nomad-requests/browse-test.json")" = "persistent-nomad-request"
test "$(cat "$WORK/update-extracted/data/reticulum/config")" = "persistent-reticulum-config"
test "$(cat "$WORK/update-extracted/data/bot/identity")" = "persistent-bot-identity"

# Keep the test coupled to the installer call sites, not just an equivalent flow.
grep -F "fetch_release \"\$TMPDIR_INSTALL/\$ARCHIVE_NAME\" \"\$TMPDIR_INSTALL/\$CHECKSUM_NAME\"" "$ROOT/install.sh" >/dev/null
grep -F "tar -xzf \"\$TMPDIR_INSTALL/\$ARCHIVE_NAME\"" "$ROOT/install.sh" >/dev/null
grep -F 'TMP=${TMPDIR:-/tmp}/$ARCHIVE_NAME' "$ROOT/install.sh" >/dev/null
grep -F 'lxmf-messaging' "$ROOT/scripts/build-release.sh" >/dev/null
grep -F 'nomad-browser' "$ROOT/scripts/build-release.sh" >/dev/null
# An update preserves credentials and other settings, but its footer must
# identify the newly unpacked release rather than a stale .env value.
grep -F 'RETICULUM_ADMIN_VERSION=$APP_VERSION' "$ROOT/install.sh" >/dev/null
grep -F 'sed "s/^RETICULUM_ADMIN_VERSION=.*/RETICULUM_ADMIN_VERSION=$APP_VERSION/"' "$ROOT/install.sh" >/dev/null
grep -F "if ! grep -q '^WEB_PUSH_VAPID_SUBJECT=' \"\$ROOT/.env\"; then" "$ROOT/install.sh" >/dev/null
# Updating retains intentional tuning, but migrates the former shipped restart
# defaults so an old .env cannot reintroduce the short verification budget.
grep -F 'upgrade_timeout_default RESTART_HEALTH_TIMEOUT 35 70' "$ROOT/install.sh" >/dev/null
grep -F 'upgrade_timeout_default MANAGEMENT_HTTP_TIMEOUT 100 180' "$ROOT/install.sh" >/dev/null
# Discovery's runtime dependency is explicitly pinned for the rnsd build;
# it must not depend on whatever an indirect package happens to install.
grep -F 'LXMF_VERSION=0.9.6' "$ROOT/.env.example" >/dev/null
grep -F 'LXMF_VERSION: ${LXMF_VERSION:-0.9.6}' "$ROOT/compose.yaml" >/dev/null
# Host-networked rnsd intentionally refuses Docker Desktop/WSL semantics.
grep -F 'native Linux Docker Engine' "$ROOT/install.sh" >/dev/null
grep -F 'Docker Desktop and WSL are not supported' "$ROOT/install.sh" >/dev/null

# Every interpreter script in the payload must retain a Linux shebang.
if find "$ROOT" -path "$ROOT/.git" -prune -o -path "$ROOT/release" -prune -o -type f \( -name '*.sh' -o -name '*.py' \) -exec grep -Il "$(printf '\r')" {} + | grep -q .; then
  echo "CRLF line ending found in an executable script." >&2
  exit 1
fi

# Docker group re-exec must use a fresh group context, verify daemon access,
# and never fall back to an implicit sudo continuation.
grep -F '1. Start a new shell with Docker access now' "$ROOT/install.sh" >/dev/null
grep -F '2. Exit and log back in' "$ROOT/install.sh" >/dev/null
grep -F "exec sg docker -c" "$ROOT/install.sh" >/dev/null
grep -F "docker info >/dev/null 2>&1" "$ROOT/install.sh" >/dev/null
if grep -F "Continue using sudo" "$ROOT/install.sh" >/dev/null || grep -F "DOCKER_USE_SUDO" "$ROOT/install.sh" >/dev/null; then
  echo "Installer still contains the deprecated sudo continuation." >&2
  exit 1
fi

echo "Installer release naming and Docker-group flow checks passed."
