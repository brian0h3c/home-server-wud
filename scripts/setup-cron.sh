#!/usr/bin/env bash
#
# setup-cron.sh — install a daily cron job that logs available OS updates.
# Runs as the current user (no sudo). Default time: 07:00 daily.
#
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
HOUR="${1:-7}"   # optional: pass an hour 0-23 (default 7)

line="0 $HOUR * * * $DIR/os-update-check.sh >/dev/null 2>&1"
( crontab -l 2>/dev/null | grep -v "os-update-check.sh"; echo "$line" ) | crontab -
echo "installed cron: $line"
echo "current crontab:"; crontab -l | grep os-update-check.sh
