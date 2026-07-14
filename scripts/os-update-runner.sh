#!/usr/bin/env bash
#
# os-update-runner.sh — applies OS updates when the control panel requests one.
#
# Runs on the HOST as ROOT (via root's crontab, every minute). When the panel's
# "Update OS" button is pressed it drops a flag file; this picks it up, runs
# `apt-get full-upgrade`, logs the result, and refreshes the update snapshot.
#
# Install (one-time, needs sudo):
#   sudo crontab -e
#   * * * * * OS_UPDATE_FLAG=/path/.os-update-request OS_RUNLOG=/path/os-update-run.log \
#             SNAPSHOT_SCRIPT=/path/os-update-check.sh SNAPSHOT_USER=youruser \
#             /path/os-update-runner.sh
#
# Env (all optional, sensible defaults for the flag/log location):
#   OS_UPDATE_FLAG   flag file the panel writes   (default: ./.os-update-request next to this)
#   OS_RUNLOG        where to log the run          (default: ./os-update-run.log)
#   SNAPSHOT_SCRIPT  os-update-check.sh to refresh availability afterwards (optional)
#   SNAPSHOT_USER    run the snapshot script as this user (optional)
#
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FLAG="${OS_UPDATE_FLAG:-$HERE/.os-update-request}"
LOG="${OS_RUNLOG:-$HERE/os-update-run.log}"
SNAP="${SNAPSHOT_SCRIPT:-}"
SNAP_USER="${SNAPSHOT_USER:-}"

[ -f "$FLAG" ] || exit 0
rm -f "$FLAG"

{
  echo "===== $(date '+%F %T') OS update requested — starting ====="
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get -y -o Dpkg::Options::="--force-confold" -o Dpkg::Options::="--force-confdef" full-upgrade
  rc=$?
  apt-get -y autoremove
  echo "apt full-upgrade exit=$rc"
  [ -f /var/run/reboot-required ] && echo "*** REBOOT REQUIRED ***"
  echo "===== $(date '+%F %T') done ====="
  echo
} >> "$LOG" 2>&1

# keep the log bounded
tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"

# refresh the "updates available" snapshot so the panel reflects the new state
if [ -n "$SNAP" ] && [ -x "$SNAP" ]; then
  if [ -n "$SNAP_USER" ]; then sudo -u "$SNAP_USER" "$SNAP" >/dev/null 2>&1 || true
  else "$SNAP" >/dev/null 2>&1 || true; fi
fi
