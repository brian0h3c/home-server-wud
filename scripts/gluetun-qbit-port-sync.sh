#!/usr/bin/env bash
# Keep qBittorrent's listen port in sync with gluetun's VPN-forwarded port.
# ProtonVPN (and most providers) hand out a new forwarded port after every
# reconnect; if qBittorrent keeps listening on the old one, torrents go slow /
# "firewalled". Run this from cron (e.g. every 5 min) to auto-correct it.
set -euo pipefail

VPNC="${VPN_CONTAINER:-gluetun}"
QBIT_URL="${QBIT_URL:-http://192.168.50.10:4009}"
LOG="${SYNC_LOG:-/home/b/docker/backup/qbit-port-sync.log}"

port="$(docker exec "$VPNC" cat /tmp/gluetun/forwarded_port 2>/dev/null | tr -dc '0-9')"
if [ -z "$port" ] || [ "$port" = "0" ]; then
  echo "$(date '+%F %T') no forwarded port from gluetun" >> "$LOG"
  exit 0
fi

cur="$(curl -s --max-time 8 "$QBIT_URL/api/v2/app/preferences" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("listen_port",""))' 2>/dev/null || true)"

if [ "$port" != "$cur" ]; then
  curl -s --max-time 8 -X POST "$QBIT_URL/api/v2/app/setPreferences" \
    --data-urlencode "json={\"listen_port\":$port,\"random_port\":false,\"upnp\":false}" >/dev/null || true
  echo "$(date '+%F %T') synced qBittorrent listen_port ${cur:-?} -> $port" >> "$LOG"
else
  echo "$(date '+%F %T') listen_port already $port" >> "$LOG"
fi

# keep the log small
tail -n 200 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" || true
