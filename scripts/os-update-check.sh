#!/usr/bin/env bash
#
# os-update-check.sh — log available OS package updates (Debian/Ubuntu apt).
# Runs as a normal user (no sudo needed). Your system's apt timer keeps the
# package cache fresh; this just reports what's upgradable.
#
# Writes:
#   logs/os-updates-latest.txt   most recent snapshot
#   logs/os-updates.log          rolling history (trimmed to ~2000 lines)
#
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$REPO_DIR/logs}"
mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/os-updates.log"
SNAP="$OUT_DIR/os-updates-latest.txt"

if ! command -v apt >/dev/null 2>&1; then
  echo "apt not found — this script targets Debian/Ubuntu hosts." >&2
  exit 1
fi

{
  echo "===== $(date '+%F %T') ====="
  count="$(apt list --upgradable 2>/dev/null | grep -c upgradable)"
  sec="$(apt list --upgradable 2>/dev/null | grep -ci security)"
  echo "updates_available=$count  security=$sec"
  [ -f /var/run/reboot-required ] && echo "REBOOT REQUIRED"
  echo "--- packages ---"
  apt list --upgradable 2>/dev/null | grep upgradable | sed 's#/.*\] # => #'
} > "$SNAP"

cat "$SNAP" >> "$LOG"
tail -n 2000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"

cat "$SNAP"
