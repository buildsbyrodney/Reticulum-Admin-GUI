#!/bin/sh
# Build a clean, inspectable release payload from the repository root.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=$(tr -d '\r\n' < "$ROOT/VERSION")
NAME="reticulum-admin-$VERSION"
OUT="$ROOT/release"
STAGE="$OUT/$NAME"
rm -rf "$STAGE"
mkdir -p "$STAGE"
for item in VERSION README.md compose.yaml install.sh .env.example rnsd reticulum-admin management-agent scripts; do
  [ -e "$ROOT/$item" ] && cp -a "$ROOT/$item" "$STAGE/"
done
find "$STAGE" -type d \( -name __pycache__ -o -name .pytest_cache -o -name data -o -name logs -o -name release \) -prune -exec rm -rf {} +
ARCHIVE="$OUT/$NAME.tar.gz"
CHECKSUM="$ARCHIVE.sha256"
rm -f "$ARCHIVE" "$CHECKSUM"
tar -C "$OUT" --sort=name --mtime='UTC 2020-01-01' --owner=0 --group=0 --numeric-owner -czf "$ARCHIVE" "$NAME"
(cd "$OUT" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$CHECKSUM")")
printf 'Archive: %s\nChecksum: %s\n' "$ARCHIVE" "$CHECKSUM"
cat "$CHECKSUM"
