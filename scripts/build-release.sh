#!/bin/sh
# Build a clean, inspectable release payload from the repository root.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=$(tr -d '\r\n' < "$ROOT/VERSION")
NAME="reticulum-admin-$VERSION"
OUT="$ROOT/release"
STAGE="$OUT/$NAME"

if find "$ROOT" -path "$ROOT/.git" -prune -o -path "$ROOT/release" -prune -o -type f \( -name '*.sh' -o -name '*.py' \) -exec grep -Il "$(printf '\r')" {} + | grep -q .; then
  echo "Refusing to package executable scripts with CRLF line endings." >&2
  exit 1
fi

rm -rf "$STAGE"
mkdir -p "$STAGE"
for item in VERSION README.md compose.yaml install.sh .env.example rnsd reticulum-admin management-agent scripts; do
  [ -e "$ROOT/$item" ] && cp -a "$ROOT/$item" "$STAGE/"
done
find "$STAGE" -type d \( -name __pycache__ -o -name .pytest_cache -o -name data -o -name logs -o -name release \) -prune -exec rm -rf {} +
# Windows checkouts do not always preserve Git's executable bit. Release scripts
# must remain executable when extracted on Linux.
find "$STAGE" -type f -name '*.sh' -exec chmod 0755 {} +
ARCHIVE="$OUT/$NAME.tar.gz"
CHECKSUM="$ARCHIVE.sha256"
rm -f "$ARCHIVE" "$CHECKSUM"
tar -C "$OUT" --sort=name --mtime='UTC 2020-01-01' --owner=0 --group=0 --numeric-owner -czf "$ARCHIVE" "$NAME"
(cd "$OUT" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$CHECKSUM")")
printf 'Archive: %s\nChecksum: %s\n' "$ARCHIVE" "$CHECKSUM"
cat "$CHECKSUM"
