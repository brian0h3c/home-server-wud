#!/usr/bin/env bash
#
# update.sh — safely update Docker containers with an automatic backup first.
#
# For each container you name, this will:
#   1. snapshot its config volume(s) to a timestamped .tar.gz  (so you can roll back)
#   2. pull the new image
#   3. recreate the container
#
# The panel Update button calls this script. It snapshots config before pulling
# and recreating, which matters for stateful apps (databases, *arr apps, Plex).
#
# Usage:
#   ./scripts/update.sh <container> [<container> ...]
#   ./scripts/update.sh --full <container> ...  # ALSO snapshot the whole stack
#                                               # (backup.sh) before updating
#   ./scripts/update.sh --list                  # list running containers
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

# optional: full-stack snapshot before updating anything
FULL=0
if [ "${1:-}" = "--full" ]; then FULL=1; shift; fi

[ $# -ge 1 ] || { echo "usage: $0 [--full] <container> [<container> ...]   (or --list)"; exit 1; }

if [ "$FULL" = "1" ]; then
  echo "== full-stack backup (backup.sh) =="
  "$(dirname "$0")/backup.sh" || { echo "  [!] full backup failed — aborting"; exit 1; }
  echo
fi

backup_container() {
  local name="$1"
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "  [!] no such container: $name"; return 1
  fi
  mkdir -p "$BACKUP_DIR"
  if [ "$name" = "plexms" ]; then
    local config_src db_dir manifest ts out file
    config_src="$(docker inspect "$name" --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}')"
    [ -n "$config_src" ] || { echo "  [!] Plex /config mount not found"; return 1; }
    db_dir="$config_src/Library/Application Support/Plex Media Server/Plug-in Support/Databases"
    ts="$(date +%Y%m%d_%H%M%S)"
    out="$BACKUP_DIR/${name}_${ts}.tar.gz"
    manifest="$(mktemp)"
    printf '%s\0' "Library/Application Support/Plex Media Server/Preferences.xml" > "$manifest"
    while IFS= read -r -d '' file; do
      printf '%s\0' "${file#"$config_src"/}" >> "$manifest"
    done < <(find "$db_dir" -maxdepth 1 -type f ! -name '*-20??-??-??' -print0)
    echo "  backing up Plex preferences + databases"
    echo "          -> $out"
    if ! tar -C "$config_src" --null -T "$manifest" -czf "$out"; then
      rm -f "$manifest"
      rm -f "$out"
      echo "  [!] backup failed"
      return 1
    fi
    rm -f "$manifest"
    ls -1t "$BACKUP_DIR/${name}_"*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
    echo "  backup ok ($(du -h "$out" | cut -f1))"
    return 0
  fi
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
  local service ref running latest stopped=0
  echo "== $name =="
  if [ "$name" = "plexms" ]; then
    echo "  stopping Plex for a consistent database backup..."
    docker stop -t 30 "$name" >/dev/null
    stopped=1
  fi
  backup_container "$name" || {
    [ "$stopped" = 0 ] || docker start "$name" >/dev/null
    echo "  aborting: backup failed"; return 1;
  }
  service="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$name" 2>/dev/null)"
  [ -n "$service" ] || service="$name"
  ref="$(docker inspect -f '{{.Config.Image}}' "$name")"
  echo "  pulling latest image..."
  compose pull "$service" || {
    [ "$stopped" = 0 ] || docker start "$name" >/dev/null
    echo "  [!] pull failed for service '$service'"; return 1;
  }
  echo "  recreating container..."
  compose up -d "$service"
  running="$(docker inspect -f '{{.Image}}' "$name")"
  latest="$(docker image inspect -f '{{.Id}}' "$ref")"
  [ "$running" = "$latest" ] || { echo "  [!] recreate completed but '$name' is still on the old image"; return 1; }
  if [ "$name" = "plexms" ]; then
    [ "$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/transcode"}}{{.Type}}{{end}}{{end}}' "$name")" = "tmpfs" ] || {
      echo "  [!] Plex /transcode is not tmpfs after recreation"; return 1;
    }
  fi
  echo "  updated."
}

rc=0
for c in "$@"; do update_one "$c" || rc=1; done
exit $rc
