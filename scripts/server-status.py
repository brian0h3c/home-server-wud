#!/usr/bin/env python3
"""
server-status.py — collect whole-server health into JSON for the command center.
Runs as a normal user in the docker group (no sudo). Secrets (Plex token, SAB
key) are read locally and used only to fetch data — they are NOT written to the
output JSON.
"""
import glob
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OUT = os.environ.get("STATUS_JSON", "/tmp/server-status.json")
NAS = os.environ.get("NAS_PATH", "")
OS_SNAP = os.environ.get("OS_SNAP", "")
VPNC = os.environ.get("VPN_CONTAINER", "gluetun")
PLEX_PREF = os.environ.get("PLEX_PREF", "")
PLEX_URL = os.environ.get("PLEX_URL", "http://localhost:32400")
SAB_INI = os.environ.get("SAB_INI", "")
SAB_URL = os.environ.get("SAB_URL", "")
QBIT_URL = os.environ.get("QBIT_URL", "")
NET_STATE = os.environ.get("NET_STATE", OUT + ".net")
_DIR = os.path.dirname(OUT) or "."
LINKS_JSON = os.environ.get("LINKS_JSON", os.path.join(_DIR, "links.json"))
SMART_JSON = os.environ.get("SMART_JSON", os.path.join(_DIR, "smart.json"))
HISTORY_DB = os.environ.get("HISTORY_DB", os.path.join(_DIR, "history.db"))
RADARR_CFG = os.environ.get("RADARR_CFG", "/home/b/docker/radarr/config.xml")
RADARR_URL = os.environ.get("RADARR_URL", "http://192.168.50.10:4002")
SONARR_CFG = os.environ.get("SONARR_CFG", "/home/b/docker/sonarr4/config.xml")
SONARR_URL = os.environ.get("SONARR_URL", "http://192.168.50.10:4003")


def run(args, t=8):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=t).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return ""


def http_json(url, t=6, headers=None):
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=t) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001
        return None


def http_text(url, t=6, headers=None):
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=t) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


# ---------- system ----------
osr = {}
for line in read("/etc/os-release").splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        osr[k] = v.strip().strip('"')
uptime_s = float((read("/proc/uptime").split() or [0])[0] or 0)
d, h, m = int(uptime_s // 86400), int(uptime_s % 86400 // 3600), int(uptime_s % 3600 // 60)
uptime = (f"{d}d " if d else "") + f"{h}h {m}m"
load = (read("/proc/loadavg").split() or ["0"])[0]
ncpu = os.cpu_count() or 1
mem = {}
for line in read("/proc/meminfo").splitlines():
    if ":" in line:
        k, v = line.split(":", 1)
        mem[k] = int(v.split()[0])
mem_total = mem.get("MemTotal", 0) // 1024
mem_used = (mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)) // 1024
dl = run(["df", "-h", "/"]).splitlines()
dr = dl[1].split() if len(dl) > 1 else []
disk_pct = dr[4] if len(dr) > 4 else "?"
disk_free = dr[3] if len(dr) > 3 else "?"

cpu_temp = None
for tp in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
    ty = read(tp.replace("temp", "type")).lower()
    val = read(tp).strip()
    if val.isdigit() and ("pkg" in ty or "core" in ty or cpu_temp is None):
        cpu_temp = round(int(val) / 1000)
        if "pkg" in ty or "core" in ty:
            break


# ---------- hardware identity (non-secret DMI + CPU model) ----------
def dmi(name):
    return read(f"/sys/class/dmi/id/{name}").strip()


cpu_model = ""
for line in read("/proc/cpuinfo").splitlines():
    if line.lower().startswith("model name"):
        cpu_model = line.split(":", 1)[1].strip()
        break
hw = {"vendor": dmi("sys_vendor"), "board": dmi("board_name"),
      "product": dmi("product_name"), "bios": dmi("bios_version"), "cpu": cpu_model}

# ---------- network (rate via delta) ----------
iface = ""
for line in run(["ip", "route"]).splitlines():
    if line.startswith("default"):
        mch = re.search(r"dev (\S+)", line)
        if mch:
            iface = mch.group(1)
            break
net = {"iface": iface, "rx_mbps": 0, "tx_mbps": 0}
if iface:
    rx = int(read(f"/sys/class/net/{iface}/statistics/rx_bytes").strip() or 0)
    tx = int(read(f"/sys/class/net/{iface}/statistics/tx_bytes").strip() or 0)
    now = time.time()
    prev = read(NET_STATE).split()
    if len(prev) == 3:
        pt, prx, ptx = float(prev[0]), int(prev[1]), int(prev[2])
        dt = max(now - pt, 1)
        net["rx_mbps"] = round(max(rx - prx, 0) * 8 / dt / 1e6, 1)
        net["tx_mbps"] = round(max(tx - ptx, 0) * 8 / dt / 1e6, 1)
    open(NET_STATE, "w").write(f"{now} {rx} {tx}")

# ---------- NAS ----------
nas = {"configured": bool(NAS), "mounted": False}
if NAS and subprocess.run(["mountpoint", "-q", NAS]).returncode == 0:
    nas["mounted"] = True
    df = run(["df", "-h", NAS]).splitlines()
    if len(df) > 1:
        p = df[1].split()
        nas.update(total=p[1], used=p[2], free=p[3], used_pct=p[4])

# ---------- GPU ----------
gl = run(["nvidia-smi", "--query-gpu=name,driver_version,temperature.gpu,utilization.gpu,memory.used,memory.total",
          "--format=csv,noheader,nounits"]).splitlines()
gpu = {"present": False}
if gl:
    p = [x.strip() for x in gl[0].split(",")]
    gpu = {"present": True, "name": p[0], "driver": p[1], "temp": p[2], "util": p[3],
           "mem_used": p[4] if len(p) > 4 else "", "mem_total": p[5] if len(p) > 5 else ""}

# ---------- VPN ----------
vpn = {"health": run(["docker", "inspect", "-f",
       "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", VPNC]) or "absent"}
ip = ""
try:
    j = subprocess.run(["docker", "exec", VPNC, "wget", "-qO-", "-T", "6",
                        "http://127.0.0.1:8000/v1/publicip/ip"], capture_output=True, text=True, timeout=10).stdout
    ip = (json.loads(j).get("public_ip", "") if j.strip().startswith("{") else "")
except Exception:  # noqa: BLE001
    ip = ""
if not ip:
    ip = run(["docker", "exec", VPNC, "wget", "-qO-", "-T", "6", "https://api.ipify.org"], t=10)
vpn["exit_ip"] = ip
vpn["port"] = run(["docker", "exec", VPNC, "cat", "/tmp/gluetun/forwarded_port"])

# ---------- Plex now playing ----------
plex = {"configured": bool(PLEX_PREF), "sessions": []}
if PLEX_PREF:
    tok = re.search(r'PlexOnlineToken="([^"]+)"', read(PLEX_PREF))
    if tok:
        xml = http_text(f"{PLEX_URL}/status/sessions?X-Plex-Token={tok.group(1)}")
        for vb in re.findall(r"<Video\b.*?</Video>", xml, re.S):
            def a(name, b=vb):
                m = re.search(r'\b' + name + r'="([^"]*)"', b)
                return m.group(1) if m else ""
            title = a("title")
            gp = a("grandparentTitle")
            user = (re.search(r'<User [^>]*title="([^"]*)"', vb) or [None, ""])[1]
            offset = float(a("viewOffset") or 0)
            dur = float(a("duration") or 0)
            bw = re.search(r'<Session [^>]*bandwidth="(\d+)"', vb)
            player = (re.search(r'<Player [^>]*(?:title|product)="([^"]*)"', vb) or [None, ""])[1]
            mres = (re.search(r'<Media [^>]*videoResolution="([^"]*)"', vb) or [None, ""])[1]
            thumb = a("grandparentThumb") or a("thumb") or a("parentThumb")
            plex["sessions"].append({
                "title": (gp + " · " if gp else "") + title,
                "user": user,
                "mode": "transcode" if 'videoDecision="transcode"' in vb else "direct play",
                "progress": round(offset / dur * 100) if dur else 0,
                "bandwidth": round(int(bw.group(1)) / 1000, 1) if bw else 0,
                "player": player,
                "quality": mres.upper() if mres else "",
                "thumb": thumb,
            })

# ---------- Downloads ----------
downloads = {"sab": {}, "qbit": {}}
if SAB_INI and SAB_URL:
    key = re.search(r"^api_key\s*=\s*(\S+)", read(SAB_INI), re.M)
    if key:
        j = http_json(f"{SAB_URL}/api?mode=queue&output=json&apikey={key.group(1)}")
        q = (j or {}).get("queue", {})
        downloads["sab"] = {"speed_mbps": round(float(q.get("kbpersec", 0) or 0) * 8 / 1000, 1),
                            "items": int(q.get("noofslots", 0) or 0),
                            "status": q.get("status", "")}
if QBIT_URL:
    ti = http_json(f"{QBIT_URL}/api/v2/transfer/info")
    tl = http_json(f"{QBIT_URL}/api/v2/torrents/info?filter=downloading")
    if ti:
        downloads["qbit"] = {"dl_mbps": round(ti.get("dl_info_speed", 0) * 8 / 1e6, 1),
                            "up_mbps": round(ti.get("up_info_speed", 0) * 8 / 1e6, 1),
                            "active": len(tl or [])}

# ---------- OS updates ----------
osc, oss = 0, 0
if OS_SNAP:
    snap = read(OS_SNAP)
    mc = re.search(r"updates_available=(\d+)", snap)
    ms = re.search(r"security=(\d+)", snap)
    osc = int(mc.group(1)) if mc else 0
    oss = int(ms.group(1)) if ms else 0
reboot = os.path.exists("/var/run/reboot-required")
nvrec = ""
for line in run(["ubuntu-drivers", "devices"]).splitlines():
    if "recommended" in line:
        mm = re.search(r"(nvidia-driver-\S+)", line)
        if mm:
            nvrec = mm.group(1)

# ---------- service health (probe each quick link) ----------
def probe(url, t=4):
    if not url:
        return False, None
    start = time.time()
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=t)
        return True, round((time.time() - start) * 1000)
    except urllib.error.HTTPError:
        return True, round((time.time() - start) * 1000)  # responded (even 401/403) = up
    except Exception:  # noqa: BLE001
        return False, None


links = []
try:
    links = json.loads(read(LINKS_JSON)) or []
except Exception:  # noqa: BLE001
    links = []
services = []
if links:
    with ThreadPoolExecutor(max_workers=8) as ex:
        probed = list(ex.map(lambda l: (l, probe(l.get("url", ""))), links))
    services = [{"name": l.get("name"), "url": l.get("url"), "up": r[0], "ms": r[1]}
                for l, r in probed]

# ---------- Radarr / Sonarr queues ----------
def arr(cfg, base):
    if not os.path.exists(cfg):
        return None
    key = re.search(r"<ApiKey>([a-f0-9]+)</ApiKey>", read(cfg))
    if not key:
        return None
    h = {"X-Api-Key": key.group(1)}
    q = http_json(f"{base}/api/v3/queue?page=1&pageSize=1", headers=h) or {}
    miss = http_json(f"{base}/api/v3/wanted/missing?page=1&pageSize=1", headers=h) or {}
    return {"queue": q.get("totalRecords", 0), "missing": miss.get("totalRecords", 0)}


media = {"radarr": arr(RADARR_CFG, RADARR_URL), "sonarr": arr(SONARR_CFG, SONARR_URL)}

# ---------- disk SMART health (written by root smart-check cron) ----------
disks = []
try:
    disks = json.loads(read(SMART_JSON)).get("disks", [])
except Exception:  # noqa: BLE001
    disks = []

data = {
    "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    "system": {"os": osr.get("PRETTY_NAME", "Linux"), "kernel": os.uname().release,
               "uptime": uptime, "load": load, "ncpu": ncpu, "cpu_temp": cpu_temp,
               "mem_used_mb": mem_used, "mem_total_mb": mem_total,
               "disk_root_pct": disk_pct, "disk_root_free": disk_free, "hw": hw},
    "net": net,
    "nas": nas,
    "gpu": gpu,
    "vpn": vpn,
    "plex": plex,
    "downloads": downloads,
    "services": services,
    "media": media,
    "disks": disks,
    "updates": {"os_count": osc, "os_security": oss, "reboot": reboot, "nvidia_recommended": nvrec},
}
tmp = OUT + ".tmp"
open(tmp, "w").write(json.dumps(data, indent=1))
os.replace(tmp, OUT)

# ---------- append to history (24h/7d trend graphs) ----------
try:
    import sqlite3
    con = sqlite3.connect(HISTORY_DB, timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS metrics(ts INTEGER PRIMARY KEY, cpu_temp REAL, "
                "mem_pct REAL, disk_pct REAL, nas_pct REAL, rx REAL, tx REAL, gpu_temp REAL, "
                "gpu_util REAL, plex INTEGER)")

    def _i(v):
        try:
            return int(str(v).rstrip("%"))
        except Exception:  # noqa: BLE001
            return 0

    con.execute("INSERT OR REPLACE INTO metrics VALUES(?,?,?,?,?,?,?,?,?,?)", (
        int(time.time()), cpu_temp or 0,
        round(mem_used / mem_total * 100, 1) if mem_total else 0,
        _i(disk_pct), _i(nas.get("used_pct", 0)),
        net.get("rx_mbps", 0), net.get("tx_mbps", 0),
        float(gpu.get("temp") or 0) if gpu.get("present") else 0,
        float(gpu.get("util") or 0) if gpu.get("present") else 0,
        len(plex["sessions"])))
    con.execute("DELETE FROM metrics WHERE ts < ?", (int(time.time()) - 7 * 86400,))
    con.commit()
    con.close()
except Exception:  # noqa: BLE001
    pass
