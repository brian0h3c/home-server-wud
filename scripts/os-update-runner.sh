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
DETAILS="${OS_UPDATE_DETAILS:-$LOG.details}"
SNAP="${SNAPSHOT_SCRIPT:-}"
SNAP_USER="${SNAPSHOT_USER:-}"
APT="${APT_CMD:-apt}"
APT_GET="${APT_GET_CMD:-apt-get}"

[ -f "$FLAG" ] || exit 0
exec 9>"$FLAG.lock"
flock -n 9 || exit 0
trap 'rm -f "$FLAG"' EXIT

started="$(date '+%F %T')"
available=0
security=0
packages=""

write_summary() {
  local status="$1" progress="$2" detail="$3" remaining="${4:--}"
  local reboot="Pending check"
  [ "$status" = "Complete" ] || [ "$status" = "Failed" ] && {
    if [ -f /var/run/reboot-required ]; then reboot="Yes"; else reboot="No"; fi
  }
  {
    echo "OS update summary"
    echo "Status: $status"
    echo "Available: $available update(s) ($security security)"
    echo "Progress: $progress% — $detail"
    [ "$remaining" = "-" ] || echo "Remaining: $remaining"
    echo "Reboot required: $reboot"
    echo "Started: $started"
    [ "$status" = "Complete" ] || [ "$status" = "Failed" ] && echo "Finished: $(date '+%F %T')"
    [ -z "$packages" ] || echo "Packages: $packages"
  } > "$LOG.tmp"
  mv "$LOG.tmp" "$LOG"
}

: > "$DETAILS"
write_summary "Checking" 0 "refreshing package information"
export DEBIAN_FRONTEND=noninteractive
"$APT_GET" update -y >> "$DETAILS" 2>&1
update_rc=$?

available="$("$APT" list --upgradable 2>/dev/null | grep -c upgradable || true)"
security="$("$APT" list --upgradable 2>/dev/null | grep -ci security || true)"
packages="$("$APT" list --upgradable 2>/dev/null | grep upgradable | cut -d/ -f1 | paste -sd ', ' -)"

if [ "$update_rc" -ne 0 ]; then
  write_summary "Failed" 0 "package information refresh failed" "$available"
  exit "$update_rc"
fi

write_summary "Running" 20 "package information refreshed"
write_summary "Running" 30 "applying $available update(s)"
"$APT_GET" -y -o Dpkg::Options::="--force-confold" -o Dpkg::Options::="--force-confdef" \
  full-upgrade >> "$DETAILS" 2>&1
rc=$?

remaining="$("$APT" list --upgradable 2>/dev/null | grep -c upgradable || true)"
applied=$((available - remaining))
[ "$applied" -ge 0 ] || applied=0
if [ "$available" -gt 0 ]; then progress=$((applied * 100 / available)); else progress=100; fi
[ "$progress" -ge 90 ] || progress=90
write_summary "Running" "$progress" "$applied of $available update(s) applied; cleaning up" "$remaining"
"$APT_GET" -y autoremove >> "$DETAILS" 2>&1 || true

if [ "$rc" -eq 0 ]; then
  write_summary "Complete" 100 "$applied of $available update(s) applied" "$remaining"
else
  write_summary "Failed" "$progress" "apt exited with code $rc; $applied of $available applied" "$remaining"
fi

# Keep diagnostic details bounded without exposing them in the panel.
tail -n 1000 "$DETAILS" > "$DETAILS.tmp" 2>/dev/null && mv "$DETAILS.tmp" "$DETAILS"

# refresh the "updates available" snapshot so the panel reflects the new state
if [ -n "$SNAP" ] && [ -x "$SNAP" ]; then
  if [ -n "$SNAP_USER" ]; then sudo -u "$SNAP_USER" "$SNAP" >/dev/null 2>&1 || true
  else "$SNAP" >/dev/null 2>&1 || true; fi
fi

exit "$rc"
