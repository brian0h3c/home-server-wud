#!/usr/bin/env bash
#
# update.sh — safely update Docker containers with an automatic backup first.
#
# For each container you name, this will:
#   1. snapshot its config volume(s) to a timestamped .tar.gz  (so you can roll back)
#   2. pull the new image
#   3. recreate the container
#
# This is the SAFE way to apply updates that WUD shows are available: WUD's
# built-in one-click button recreates a container WITHOUT a backup, so for any
# stateful app (databases, *arr apps, Plex, etc.) use this script instead.
#
# Usage:
#   ./scripts/update.sh <container> [<container> ...]
#   ./scripts/update.sh --list                 # list running containers
#
# Environment overrides:
#   COMPOSE_FILE   docker-compose.yml that manages the container(s)
#                  (default: docker compose auto-detects the current dir)
#   BACKUP_DIR     where backups are written        (default: <repo>/backups)
#   KEEP           backups to keep per container     (default: 5)
#   BACKUP_DESTS   in-container mount destinations to back up
#                  (default: "/config /data /app/config")
#
# Restore a backup:
#   docker compose stop <container>
#   sudo tar -xzf backups/<container>_<timestamp>.tar.gz -C /
#   docker compose start <container>
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_DIR/backups}"
KEEP="${KEEP:-5}"
BACKUP_DESTS="${BACKUP_DESTS:-/config /data /app/config}"
COMPOSE_FILE="${COMPOSE_FILE:-}"

compose() {
  if [ -n "$COMPOSE_FILE" ]; then
    docker compose -f "$COMPOSE_FILE" "$@"
  else
    docker compose "$@"
  fi
}

if [ "${1:-}" = "--list" ]; then
  docker ps --format '  {{.Names}}\t{{.Image}}'
  exit 0
fi

[ $# -ge 1 ] || { echo "usage: $0 <container> [<container> ...]   (or --list)"; exit 1; }

backup_container() {
  local name="$1"
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "  [!] no such container: $name"; return 1
  fi
  mkdir -p "$BACKUP_DIR"
  local srcs=()
  while IFS=$'\t' read -r dest src; do
    for d in $BACKUP_DESTS; do
      [ "$dest" = "$d" ] && [ -n "$src" ] && srcs+=("$src")
    done
  done < <(docker inspect "$name" \
            --format '{{range .Mounts}}{{.Destination}}{{"\t"}}{{.Source}}{{"\n"}}{{end}}')

  if [ ${#srcs[@]} -eq 0 ]; then
    echo "  [i] no config mount ($BACKUP_DESTS) found for '$name' — skipping backup"
    return 0
  fi
  local ts out
  ts="$(date +%Y%m%d_%H%M%S)"
  out="$BACKUP_DIR/${name}_${ts}.tar.gz"
  echo "  backing up: ${srcs[*]}"
  echo "          -> $out"
  tar -czf "$out" "${srcs[@]}" 2>/dev/null || { echo "  [!] backup failed"; return 1; }
  # keep only the newest $KEEP backups for this container
  ls -1t "$BACKUP_DIR/${name}_"*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
  echo "  backup ok ($(du -h "$out" | cut -f1))"
}

update_one() {
  local name="$1"
  echo "== $name =="
  backup_container "$name" || { echo "  aborting: backup failed"; return 1; }
  echo "  pulling latest image..."
  compose pull "$name" || { echo "  [!] pull failed (is '$name' the compose service name?)"; return 1; }
  echo "  recreating container..."
  compose up -d "$name"
  echo "  updated."
}

rc=0
for c in "$@"; do update_one "$c" || rc=1; done
exit $rc
