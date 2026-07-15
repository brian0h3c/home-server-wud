#!/usr/bin/env bash
#
# host-action-runner.sh — runs as ROOT via root cron. Applies host actions the
# panel requests via flag files (currently: reboot).
#
set -uo pipefail
REBOOT_FLAG="${REBOOT_FLAG:-/home/b/docker/backup/.reboot-request}"
LOG="${HOST_ACTION_LOG:-/home/b/docker/backup/host-action.log}"

if [ -f "$REBOOT_FLAG" ]; then
  rm -f "$REBOOT_FLAG"
  echo "$(date '+%F %T') reboot requested via panel -> rebooting" >> "$LOG"
  sync
  /sbin/reboot
fi
