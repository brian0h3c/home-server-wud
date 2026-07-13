#!/usr/bin/env bash
#
# backup.sh — full-stack backup of your Docker compose project.
#
# Archives your compose file(s), .env, and all app config directories into one
# timestamped tar.gz, skipping big/regenerable stuff (media, downloads, caches).
# Optionally include extra paths (e.g. an exported NAS config) and copy the
# archive to a second location (e.g. a NAS mount).
#
# Usage:
#   ./scripts/backup.sh                          # back up the project dir
#   ./scripts/backup.sh --extra /srv/nas-config  # + extra path(s)
#   ./scripts/backup.sh --list                   # list existing backups
#
# Environment:
#   PROJECT_DIR   what to back up      (default: your compose project dir)
#   BACKUP_DIR    where archives go    (default: <repo>/backups)
#   KEEP          archives to keep     (default: 7)
#   DEST_DIR      also copy archive here, e.g. /mnt/nas/_Backups  (optional)
#   EXTRA_PATHS   space-separated extra paths to include          (optional)
#   EXCLUDES      space-separated glob patterns to skip           (has defaults)
#
# Restore:
#   sudo tar -xzf backups/stack_<timestamp>.tar.gz -C /
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$REPO_DIR}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_DIR/backups}"
KEEP="${KEEP:-7}"
DEST_DIR="${DEST_DIR:-}"
EXTRA_PATHS="${EXTRA_PATHS:-}"
DEFAULT_EXCLUDES="*/Media */media */Movies */movies */TVShows */tv */Downloads */downloads */transcode */Transcode */Cache */Metadata */MediaCover *.sock *.log node_modules backups logs"
EXCLUDES="${EXCLUDES:-$DEFAULT_EXCLUDES}"

if [ "${1:-}" = "--list" ]; then
  ls -lht "$BACKUP_DIR"/stack_*.tar.gz 2>/dev/null || echo "no backups yet in $BACKUP_DIR"
  exit 0
fi

extra=()
if [ "${1:-}" = "--extra" ]; then shift; extra=("$@"); fi
# shellcheck disable=SC2206
[ -n "$EXTRA_PATHS" ] && extra+=($EXTRA_PATHS)

mkdir -p "$BACKUP_DIR"
ts="$(date +%Y%m%d_%H%M%S)"
out="$BACKUP_DIR/stack_${ts}.tar.gz"

exargs=()
for e in $EXCLUDES; do exargs+=("--exclude=$e"); done

echo "Backing up:"
echo "  project : $PROJECT_DIR"
[ ${#extra[@]} -gt 0 ] && echo "  extra   : ${extra[*]}"
echo "  archive : $out"

# absolute paths (tar strips the leading /, restore with -C /)
tar -czf "$out" "${exargs[@]}" "$PROJECT_DIR" "${extra[@]}" 2>/dev/null || {
  echo "[!] backup failed"; rm -f "$out"; exit 1; }
echo "  size    : $(du -h "$out" | cut -f1)"

# rotate local
ls -1t "$BACKUP_DIR"/stack_*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

# optional copy to a second location (e.g. NAS)
if [ -n "$DEST_DIR" ]; then
  if [ -d "$DEST_DIR" ]; then
    cp "$out" "$DEST_DIR/" && echo "  copied  : $DEST_DIR/$(basename "$out")"
    ls -1t "$DEST_DIR"/stack_*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
  else
    echo "[!] DEST_DIR '$DEST_DIR' not a directory — skipped remote copy"
  fi
fi

echo "done."
