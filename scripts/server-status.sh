#!/usr/bin/env bash
#
# server-status.sh — collect whole-server health into a JSON file the command
# center reads. Runs as your normal user (in the docker group); no sudo needed.
# Schedule it every 1-2 minutes via cron.
#
# Env overrides (all optional):
#   STATUS_JSON  output file        (default: ./server-status.json next to logs)
#   NAS_PATH     mount to check      (default: none -> NAS card hidden)
#   OS_SNAP      os-updates-latest.txt path (from os-update-check.sh)
#   QBIT_URL     qBittorrent WebUI (to read the VPN exit IP)  e.g. http://IP:4009
#   VPN_CONTAINER  gluetun container name (default: gluetun)
#
set -uo pipefail
OUT="${STATUS_JSON:-$(cd "$(dirname "$0")/.." && pwd)/server-status.json}"
NAS="${NAS_PATH:-}"
OS_SNAP="${OS_SNAP:-}"
QBIT_URL="${QBIT_URL:-}"
VPNC="${VPN_CONTAINER:-gluetun}"

OS_PRETTY=$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-Linux}")
KERNEL=$(uname -r)
UPTIME=$(uptime -p 2>/dev/null | sed 's/^up //')
LOAD=$(cut -d" " -f1 /proc/loadavg 2>/dev/null)
NCPU=$(nproc 2>/dev/null || echo 1)
read -r MEM_TOTAL MEM_USED < <(free -m 2>/dev/null | awk '/Mem:/{print $2, $3}')
read -r DROOT_PCT DROOT_FREE < <(df -h / 2>/dev/null | awk 'NR==2{print $5, $4}')

NAS_MOUNTED=false; NAS_TOTAL=""; NAS_USED=""; NAS_FREE=""; NAS_PCT=""
if [ -n "$NAS" ] && mountpoint -q "$NAS" 2>/dev/null; then
  NAS_MOUNTED=true
  read -r NAS_TOTAL NAS_USED NAS_FREE NAS_PCT < <(df -h "$NAS" | awk 'NR==2{print $2, $3, $4, $5}')
elif [ -n "$NAS" ]; then
  NAS_MOUNTED=false
fi

GPU_LINE=$(nvidia-smi --query-gpu=name,driver_version,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)

VPN_HEALTH=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$VPNC" 2>/dev/null || echo "absent")
VPN_IP=""
if [ -n "$QBIT_URL" ]; then
  VPN_IP=$(curl -s -m 6 "$QBIT_URL/api/v2/transfer/info" 2>/dev/null | \
    python3 -c 'import sys,json;print(json.load(sys.stdin).get("last_external_address_v4",""))' 2>/dev/null || echo "")
fi
VPN_PORT=$(docker exec "$VPNC" cat /tmp/gluetun/forwarded_port 2>/dev/null || echo "")

OS_COUNT=0; OS_SEC=0
if [ -n "$OS_SNAP" ] && [ -f "$OS_SNAP" ]; then
  OS_COUNT=$(grep -oP 'updates_available=\K[0-9]+' "$OS_SNAP" 2>/dev/null | head -1 || echo 0)
  OS_SEC=$(grep -oP 'security=\K[0-9]+' "$OS_SNAP" 2>/dev/null | head -1 || echo 0)
fi
REBOOT=false; [ -f /var/run/reboot-required ] && REBOOT=true
NV_REC=$(ubuntu-drivers devices 2>/dev/null | awk '/recommended/{for(i=1;i<=NF;i++) if($i ~ /nvidia-driver/) print $i}' | head -1)

export OS_PRETTY KERNEL UPTIME LOAD NCPU MEM_TOTAL MEM_USED DROOT_PCT DROOT_FREE \
  NAS NAS_MOUNTED NAS_TOTAL NAS_USED NAS_FREE NAS_PCT GPU_LINE VPN_HEALTH VPN_IP VPN_PORT \
  OS_COUNT OS_SEC REBOOT NV_REC OUT
python3 - <<'PY'
import os, json, datetime
g = os.environ.get
gpu = [x.strip() for x in g("GPU_LINE","").split(",")] if g("GPU_LINE") else []
def gi(i): return gpu[i] if len(gpu) > i else ""
def num(x):
    try: return int(x)
    except Exception: return 0
data = {
 "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
 "system": {"os": g("OS_PRETTY"), "kernel": g("KERNEL"), "uptime": g("UPTIME"),
            "load": g("LOAD"), "ncpu": num(g("NCPU")),
            "mem_used_mb": num(g("MEM_USED")), "mem_total_mb": num(g("MEM_TOTAL")),
            "disk_root_pct": g("DROOT_PCT"), "disk_root_free": g("DROOT_FREE")},
 "nas": {"configured": bool(g("NAS")), "mounted": g("NAS_MOUNTED")=="true",
         "total": g("NAS_TOTAL"), "used": g("NAS_USED"), "free": g("NAS_FREE"),
         "used_pct": g("NAS_PCT")},
 "gpu": {"present": bool(gpu), "name": gi(0), "driver": gi(1), "temp": gi(2),
         "util": gi(3), "mem_used": gi(4), "mem_total": gi(5)},
 "vpn": {"health": g("VPN_HEALTH"), "exit_ip": g("VPN_IP"), "port": g("VPN_PORT")},
 "updates": {"os_count": num(g("OS_COUNT")), "os_security": num(g("OS_SEC")),
             "reboot": g("REBOOT")=="true", "nvidia_recommended": g("NV_REC")},
}
tmp = g("OUT") + ".tmp"
open(tmp, "w").write(json.dumps(data, indent=1))
os.replace(tmp, g("OUT"))
PY
