#!/usr/bin/env bash
#
# setup-cron.sh — install cron jobs (as the current user, no sudo):
#   * daily OS update check     (default 07:00)
#   * daily full-stack backup   (default 03:30)  -- disable with NO_BACKUP=1
#
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
OS_HOUR="${1:-7}"          # OS check hour (0-23)
BK="${BACKUP_TIME:-30 3}"  # backup "minute hour" (default 03:30)

os_line="0 $OS_HOUR * * * $DIR/os-update-check.sh >/dev/null 2>&1"
bk_line="$BK * * * $DIR/backup.sh >/dev/null 2>&1"

tmp="$(crontab -l 2>/dev/null | grep -v -e 'os-update-check.sh' -e 'backup.sh' || true)"
{
  echo "$tmp"
  echo "$os_line"
  [ "${NO_BACKUP:-0}" = "1" ] || echo "$bk_line"
} | grep -v '^$' | crontab -

echo "installed cron jobs:"
crontab -l | grep -E 'os-update-check.sh|backup.sh'
