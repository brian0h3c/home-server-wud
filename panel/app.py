#!/usr/bin/env python3
"""
home-server-wud — neon "command center" for a self-hosted server.
Dependency-free (Python stdlib). Reads host stats from server-status.json and
exposes live container/backup info plus one-click actions.

Actions: full backup, per-container update/restart/logs, OS update (host flag),
NAS speed test, graceful reboot (host flag). LAN-only; optional PANEL_TOKEN.
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

WUD_URL = os.environ.get("WUD_URL", "http://wud:3000")
SCRIPTS = os.environ.get("SCRIPTS_DIR", "/app/scripts")
LOGDIR = os.environ.get("LOG_DIR", "/app/logs")
STATUS_JSON = os.environ.get("STATUS_JSON", os.path.join(LOGDIR, "server-status.json"))
OS_RUNLOG = os.environ.get("OS_RUNLOG", os.path.join(LOGDIR, "os-update-run.log"))
OS_FLAG = os.environ.get("OS_UPDATE_FLAG", os.path.join(LOGDIR, ".os-update-request"))
REBOOT_FLAG = os.environ.get("REBOOT_FLAG", os.path.join(LOGDIR, ".reboot-request"))
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/app/backups")
MEDIA_CONTAINER = os.environ.get("MEDIA_CONTAINER", "plexms")
MEDIA_PATH = os.environ.get("MEDIA_TEST_PATH", "/media/Black/Movies")
QBIT_URL = os.environ.get("QBIT_URL", "http://192.168.50.10:4009")
PLEX_PREF_FILE = os.environ.get("PLEX_PREF", "/plex/Preferences.xml")
PLEX_URL = os.environ.get("PLEX_URL", "http://192.168.50.10:32400")
HISTORY_DB = os.environ.get("HISTORY_DB", os.path.join(LOGDIR, "history.db"))
QUICK_LINKS = os.environ.get("QUICK_LINKS", "[]")
TOKEN = os.environ.get("PANEL_TOKEN", "")
PORT = int(os.environ.get("PANEL_PORT", "8080"))
ENV = os.environ.copy()


def sh(args, timeout=3600):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=ENV)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"error: {e}"


def running_containers():
    _, out = sh(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"], timeout=30)
    rows = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append({"name": parts[0], "image": parts[1], "status": parts[2]})
    return rows


def container_stats():
    _, out = sh(["docker", "stats", "--no-stream", "--format",
                 "{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}\t{{.MemUsage}}"], timeout=30)
    m = {}
    for line in out.strip().splitlines():
        p = line.split("\t")
        if len(p) >= 3:
            m[p[0]] = {"cpu": p[1], "memp": p[2], "memu": p[3] if len(p) > 3 else ""}
    return m


def wud_updates():
    try:
        with urllib.request.urlopen(WUD_URL + "/api/containers", timeout=8) as r:
            data = json.load(r)
        return {c.get("name"): bool(c.get("updateAvailable")) for c in data}
    except Exception:  # noqa: BLE001
        return {}


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def list_backups(limit=15):
    out = []
    try:
        for n in os.listdir(BACKUP_DIR):
            if n.endswith(".tar.gz"):
                st = os.stat(os.path.join(BACKUP_DIR, n))
                out.append({"name": n, "size_mb": round(st.st_size / 1048576), "mtime": st.st_mtime})
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda x: -x["mtime"])
    for b in out:
        b["date"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.pop("mtime")))
    return out[:limit]


def status():
    st = read_json(STATUS_JSON)
    ups = wud_updates()
    stats = container_stats()
    st["containers"] = [{**c, "update": ups.get(c["name"], False), **stats.get(c["name"], {})}
                        for c in running_containers()]
    st["backups"] = list_backups()
    try:
        with open(OS_RUNLOG, encoding="utf-8", errors="replace") as f:
            st["os_runlog"] = "\n".join(f.read().splitlines()[-30:])
    except Exception:  # noqa: BLE001
        st["os_runlog"] = ""
    st["os_pending"] = os.path.exists(OS_FLAG)
    st["reboot_pending"] = os.path.exists(REBOOT_FLAG)
    st["links"] = []
    lf = os.path.join(LOGDIR, "links.json")
    try:
        if os.path.exists(lf):
            with open(lf, encoding="utf-8") as f:
                st["links"] = json.load(f)
        else:
            st["links"] = json.loads(QUICK_LINKS)
    except Exception:  # noqa: BLE001
        st["links"] = []
    return st


# ---------- real-time sampling (needs pid:host so /proc/1 is host init) ----------
def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return ""


def _net_counters(iface):
    for ln in _read("/proc/1/net/dev").splitlines():
        if ":" in ln and ln.split(":", 1)[0].strip() == iface:
            p = ln.split(":", 1)[1].split()
            if len(p) >= 9:
                return int(p[0]), int(p[8])  # rx_bytes, tx_bytes
    return None


def _cpu_counters():
    line = _read("/proc/stat").splitlines()
    if not line or not line[0].startswith("cpu "):
        return None
    n = [int(x) for x in line[0].split()[1:]]
    idle = n[3] + (n[4] if len(n) > 4 else 0)
    return idle, sum(n)


def _disk_counters():
    r = w = 0
    for ln in _read("/proc/diskstats").splitlines():
        p = ln.split()
        if len(p) >= 10 and re.match(r"^(sd[a-z]+|nvme\d+n\d+|mmcblk\d+)$", p[2]):
            r += int(p[5])
            w += int(p[9])  # sectors of 512 bytes
    return r * 512, w * 512


def _mem_pct():
    mt = ma = 0
    for ln in _read("/proc/meminfo").splitlines():
        if ln.startswith("MemTotal:"):
            mt = int(ln.split()[1])
        elif ln.startswith("MemAvailable:"):
            ma = int(ln.split()[1])
    return round((1 - ma / mt) * 100, 1) if mt else 0


def live():
    iface = (read_json(STATUS_JSON).get("net") or {}).get("iface") or ""
    n1, c1, d1, t1 = _net_counters(iface), _cpu_counters(), _disk_counters(), time.time()
    time.sleep(0.5)
    n2, c2, d2 = _net_counters(iface), _cpu_counters(), _disk_counters()
    dt = max(time.time() - t1, 0.1)
    out = {"cpu": 0, "mem": _mem_pct(), "net": {"rx_mbps": 0, "tx_mbps": 0},
           "disk": {"r_mbps": 0, "w_mbps": 0}, "qbit": {"dl_mbps": 0, "up_mbps": 0}}
    if n1 and n2:
        out["net"] = {"rx_mbps": round((n2[0] - n1[0]) * 8 / dt / 1e6, 2),
                      "tx_mbps": round((n2[1] - n1[1]) * 8 / dt / 1e6, 2)}
    if c1 and c2 and (c2[1] - c1[1]) > 0:
        out["cpu"] = round((1 - (c2[0] - c1[0]) / (c2[1] - c1[1])) * 100, 1)
    out["disk"] = {"r_mbps": round((d2[0] - d1[0]) / dt / 1e6, 2),
                   "w_mbps": round((d2[1] - d1[1]) / dt / 1e6, 2)}
    try:
        with urllib.request.urlopen(QBIT_URL + "/api/v2/transfer/info", timeout=2) as r:
            qb = json.load(r)
        out["qbit"] = {"dl_mbps": round(qb.get("dl_info_speed", 0) * 8 / 1e6, 2),
                       "up_mbps": round(qb.get("up_info_speed", 0) * 8 / 1e6, 2)}
    except Exception:  # noqa: BLE001
        pass
    return out


def speedtest():
    res = {"down": 0, "up": 0}
    ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) command-center/1.0"}
    try:
        t = time.time()
        n = 0
        req = urllib.request.Request("https://speed.cloudflare.com/__down?bytes=25000000", headers=ua)
        with urllib.request.urlopen(req, timeout=30) as r:
            while True:
                b = r.read(65536)
                if not b:
                    break
                n += len(b)
        res["down"] = round(n * 8 / max(time.time() - t, 0.1) / 1e6, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        payload = b"0" * 10_000_000
        t = time.time()
        req = urllib.request.Request("https://speed.cloudflare.com/__up", data=payload,
                                     method="POST", headers=ua)
        urllib.request.urlopen(req, timeout=30)
        res["up"] = round(len(payload) * 8 / max(time.time() - t, 0.1) / 1e6, 1)
    except Exception:  # noqa: BLE001
        pass
    return res


def plex_token():
    try:
        m = re.search(r'PlexOnlineToken="([^"]+)"', open(PLEX_PREF_FILE, encoding="utf-8", errors="replace").read())
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def history(hours=24):
    cols = ["ts", "cpu_temp", "mem_pct", "disk_pct", "nas_pct", "rx", "tx", "gpu_temp", "gpu_util", "plex"]
    rows = []
    try:
        import sqlite3
        con = sqlite3.connect(HISTORY_DB, timeout=5)
        cur = con.execute(f"SELECT {','.join(cols)} FROM metrics WHERE ts>=? ORDER BY ts",
                          (int(time.time()) - hours * 3600,))
        rows = cur.fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        pass
    return {"cols": cols, "rows": rows}


PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<title>Command Center</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<link rel=manifest href=/manifest.webmanifest>
<meta name=theme-color content="#050810">
<link rel=icon href=/icon.svg>
<link rel=apple-touch-icon href=/icon.svg>
<meta name=apple-mobile-web-app-capable content=yes>
<meta name=apple-mobile-web-app-title content="Command">
<meta name=apple-mobile-web-app-status-bar-style content=black-translucent>
<style>
 :root{--bg:#050810;--card:#0b1120cc;--line:#16324a;--txt:#d6f3ff;--mut:#5f7d97;
   --cyan:#22d3ee;--blue:#3b82f6;--gold:#ffb020;--green:#2ee6a6;--amber:#f5b544;--red:#ff5470;--violet:#a78bfa}
 *{box-sizing:border-box}
 body{font-family:'Segoe UI',system-ui,Arial,sans-serif;margin:0;color:var(--txt);
   background:radial-gradient(1200px 600px at 70% -10%,#0a1830 0%,#050810 55%),#050810;min-height:100vh}
 body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
   background:linear-gradient(rgba(34,211,238,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(34,211,238,.03) 1px,transparent 1px);
   background-size:40px 40px;mask:radial-gradient(900px 500px at 60% 0,#000,transparent 80%)}
 .app{position:relative;z-index:1}
 header{padding:15px 22px;display:flex;align-items:center;gap:13px;flex-wrap:wrap;
   border-bottom:1px solid var(--line);background:linear-gradient(90deg,rgba(10,24,48,.7),rgba(5,8,16,.4))}
 .logo{width:26px;height:26px;border-radius:50%;background:radial-gradient(circle,#9becff,#22d3ee 45%,#0a3550);
   box-shadow:0 0 14px #22d3ee,0 0 30px #22d3ee88;animation:pulse 3s ease-in-out infinite}
 @keyframes pulse{0%,100%{box-shadow:0 0 12px #22d3ee,0 0 26px #22d3ee55}50%{box-shadow:0 0 20px #22d3ee,0 0 44px #22d3eeaa}}
 header h1{margin:0;font-size:16px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
   color:#eafcff;text-shadow:0 0 12px #22d3ee99}
 .pill{font-size:11px;padding:3px 10px;border-radius:999px;border:1px solid var(--line);color:var(--cyan);
   font-family:ui-monospace,monospace;letter-spacing:1px;background:#08111f}
 .spacer{flex:1}
 .wrap{padding:18px 22px 40px;max-width:1120px;margin:0 auto}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin-bottom:16px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 15px;position:relative;overflow:hidden;
   backdrop-filter:blur(4px);transition:.2s;box-shadow:inset 0 0 24px rgba(34,211,238,.04)}
 .card:hover{border-color:#2b587e;box-shadow:0 0 18px rgba(34,211,238,.10),inset 0 0 30px rgba(34,211,238,.05)}
 .card h3{margin:0 0 8px;font-size:11px;font-weight:700;color:var(--cyan);text-transform:uppercase;letter-spacing:1.5px;
   display:flex;align-items:center;gap:7px}
 .big{font-size:21px;font-weight:800;font-family:ui-monospace,monospace;color:#eafcff;text-shadow:0 0 10px rgba(34,211,238,.35)}
 .sub{color:var(--mut);font-size:12px;margin-top:2px}
 .kv{display:flex;justify-content:space-between;font-size:13px;padding:3px 0}
 .kv .k{color:var(--mut)} .kv .v{font-family:ui-monospace,monospace}
 .dot{width:9px;height:9px;border-radius:50%;display:inline-block;vertical-align:middle}
 .d-ok{background:var(--green);box-shadow:0 0 8px var(--green)} .d-warn{background:var(--amber);box-shadow:0 0 8px var(--amber)}
 .d-bad{background:var(--red);box-shadow:0 0 8px var(--red);animation:blink 1.1s infinite} .d-mut{background:var(--mut)}
 @keyframes blink{50%{opacity:.35}}
 .bar{height:7px;background:#071018;border-radius:6px;overflow:hidden;margin-top:8px;border:1px solid #0e2233}
 .bar>span{display:block;height:100%;background:linear-gradient(90deg,#22d3ee,#3b82f6);box-shadow:0 0 10px #22d3ee88;transition:width .6s}
 .bar.warn>span{background:linear-gradient(90deg,#f5b544,#ff8a3d)} .bar.bad>span{background:linear-gradient(90deg,#ff5470,#ff2e63)}
 table{width:100%;border-collapse:collapse}
 th,td{text-align:left;padding:8px 8px;border-bottom:1px solid #0f2436;font-size:13px}
 th{color:var(--mut);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:1px}
 tr:last-child td{border-bottom:0}
 .badge{font-size:10px;padding:2px 9px;border-radius:999px;font-weight:700;letter-spacing:.5px}
 .b-up{background:rgba(245,181,68,.14);color:var(--amber);border:1px solid #5a4416} .b-ok{background:rgba(46,230,166,.1);color:var(--green);border:1px solid #14503c}
 .b-bad{background:rgba(255,84,112,.14);color:var(--red);border:1px solid #5a1f2c}
 button{background:linear-gradient(180deg,#0e2f4a,#0a2036);color:var(--cyan);border:1px solid #1c5578;border-radius:9px;
   padding:8px 14px;cursor:pointer;font-size:12px;font-weight:700;letter-spacing:.5px;transition:.15s;font-family:inherit}
 button:hover{border-color:#33b7e6;box-shadow:0 0 12px rgba(34,211,238,.3);color:#eafcff}
 button:disabled{opacity:.4;cursor:not-allowed;box-shadow:none}
 button.warn{border-color:#7a5a12;color:var(--gold)} button.warn:hover{box-shadow:0 0 12px rgba(255,176,32,.35)}
 button.danger{border-color:#7a1f2f;color:var(--red)} button.danger:hover{box-shadow:0 0 12px rgba(255,84,112,.35)}
 button.sm{padding:5px 10px;font-size:11px}
 .row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
 pre{background:#040a12;border:1px solid #0f2436;border-radius:8px;padding:11px;white-space:pre-wrap;word-break:break-word;
   max-height:260px;overflow:auto;font-size:11.5px;font-family:ui-monospace,monospace;color:#a9e6ff;margin:8px 0 0}
 .sec{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px;margin-bottom:16px;backdrop-filter:blur(4px)}
 .sec h2{margin:0 0 12px;font-size:13px;font-weight:700;color:var(--cyan);text-transform:uppercase;letter-spacing:1.5px}
 small,.mut{color:var(--mut)}
 details summary{cursor:pointer;color:var(--mut);font-size:12px;margin-top:8px}
 .links{display:flex;gap:9px;flex-wrap:wrap}
 .links a{text-decoration:none;font-size:12px;font-weight:700;color:var(--cyan);border:1px solid var(--line);
   padding:7px 12px;border-radius:9px;background:#08111f;transition:.15s}
 .links a:hover{border-color:#33b7e6;box-shadow:0 0 10px rgba(34,211,238,.25)}
 .now{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #0f2436}
 .now:last-child{border-bottom:0}
 .eq{display:inline-flex;gap:2px;align-items:flex-end;height:16px}
 .eq i{width:3px;background:var(--cyan);box-shadow:0 0 6px var(--cyan);animation:eq 1s infinite ease-in-out}
 .eq i:nth-child(2){animation-delay:.2s}.eq i:nth-child(3){animation-delay:.4s}.eq i:nth-child(4){animation-delay:.15s}
 @keyframes eq{0%,100%{height:4px}50%{height:16px}}
 /* hardware brand badges */
 .brandrow{display:flex;gap:7px;flex-wrap:wrap;margin:0 0 10px}
 .brand{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:800;letter-spacing:1.5px;
   padding:3px 9px 3px 8px;border-radius:7px;border:1px solid currentColor;background:#060d18;text-transform:uppercase;
   font-family:ui-monospace,monospace;line-height:1}
 .beye{width:22px;height:12px;flex:none}
 .b-nvidia{color:#76b900;box-shadow:0 0 10px rgba(118,185,0,.35),inset 0 0 12px rgba(118,185,0,.08);text-shadow:0 0 8px rgba(118,185,0,.6)}
 .b-rog{color:#ff1e40;box-shadow:0 0 10px rgba(255,30,64,.35),inset 0 0 12px rgba(255,30,64,.08);text-shadow:0 0 8px rgba(255,30,64,.6)}
 .b-asus{color:#31a9e0;box-shadow:0 0 10px rgba(49,169,224,.3)}
 .b-intel{color:#3ec6ff;box-shadow:0 0 10px rgba(62,198,255,.3);text-shadow:0 0 8px rgba(62,198,255,.5)}
 .b-amd{color:#ff4d4d;box-shadow:0 0 10px rgba(255,77,77,.3)}
 /* section header w/ action + container tiles */
 .sechead{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;flex-wrap:wrap}
 .sechead h2{margin:0}
 .tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:11px}
 .tiles.collapsed{display:none}
 .tile{background:#08111f;border:1px solid var(--line);border-radius:11px;padding:11px 12px;transition:.15s;position:relative;overflow:hidden}
 .tile::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--green);box-shadow:0 0 8px var(--green)}
 .tile.down::before{background:var(--red);box-shadow:0 0 8px var(--red)}
 .tile.upd::before{background:var(--amber);box-shadow:0 0 8px var(--amber)}
 .tile:hover{border-color:#2b587e;box-shadow:0 0 14px rgba(34,211,238,.14);transform:translateY(-2px)}
 .tile .tname{font-weight:700;font-size:13px;display:flex;align-items:center;gap:7px;word-break:break-word}
 .tile .tstat{font-size:11px;color:var(--mut);margin:4px 0 9px;min-height:14px}
 .tile .tacts{display:flex;gap:6px;flex-wrap:wrap}
 /* live sparklines */
 .spark{width:100%;height:34px;display:block;margin:9px 0 2px;overflow:visible}
 .spark .sl{fill:none;stroke:currentColor;stroke-width:1.8;vector-effect:non-scaling-stroke;
   filter:drop-shadow(0 0 3px currentColor);stroke-linejoin:round;stroke-linecap:round}
 .spark .sa{stroke:none;fill:currentColor;opacity:.13;vector-effect:non-scaling-stroke}
 .sk-cyan{color:var(--cyan)}.sk-blue{color:var(--blue)}.sk-green{color:var(--green)}
 .sk-gold{color:var(--gold)}.sk-violet{color:var(--violet)}.sk-red{color:var(--red)}
 .live{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);
   box-shadow:0 0 7px var(--green);margin-left:6px;animation:blink 1.4s infinite;vertical-align:middle}
 /* quick-link health dots */
 .links a .ld{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:middle;background:var(--mut)}
 .links a.up .ld{background:var(--green);box-shadow:0 0 7px var(--green)}
 .links a.down{border-color:#5a1f2c;color:var(--red)}
 .links a.down .ld{background:var(--red);box-shadow:0 0 7px var(--red);animation:blink 1.1s infinite}
 /* plex now-playing rich */
 .pnow{display:flex;gap:12px;padding:11px 0;border-bottom:1px solid #0f2436;align-items:center}
 .pnow:last-child{border-bottom:0}
 .pposter{width:46px;height:69px;border-radius:6px;object-fit:cover;flex:none;background:#0a1830;border:1px solid var(--line)}
 .pmeta{flex:1;min-width:0}
 .pmeta b{font-size:14px}
 .pbar{height:6px;background:#071018;border-radius:5px;overflow:hidden;margin-top:6px;border:1px solid #0e2233}
 .pbar>span{display:block;height:100%;background:linear-gradient(90deg,#22d3ee,#3b82f6);box-shadow:0 0 8px #22d3ee88}
 .ptags{display:flex;gap:6px;flex-wrap:wrap;margin-top:5px}
 .ptag{font-size:10px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);color:var(--mut)}
 /* disk / media mini rows */
 .drow{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #0f2436;font-size:13px}
 .drow:last-child{border-bottom:0}
 /* history charts */
 .hgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
 .hbox{background:#08111f;border:1px solid var(--line);border-radius:11px;padding:11px 13px}
 .hbox h4{margin:0 0 2px;font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:1px;font-weight:700}
 .hbox .hv{font-size:16px;font-weight:800;font-family:ui-monospace,monospace;color:#eafcff}
 .hchart{width:100%;height:54px;display:block;margin-top:6px;overflow:visible}
 .hchart .sl{fill:none;stroke:currentColor;stroke-width:1.6;vector-effect:non-scaling-stroke;filter:drop-shadow(0 0 3px currentColor)}
 .hchart .sa{stroke:none;fill:currentColor;opacity:.12}
 #toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#0a1830;border:1px solid #1c5578;
   padding:10px 16px;border-radius:10px;font-size:13px;opacity:0;transition:.25s;pointer-events:none;box-shadow:0 0 18px rgba(34,211,238,.2)}
 #toast.show{opacity:1}
</style></head><body><div class=app>
<header>
  <span class=logo></span>
  <h1>Command Center</h1>
  <span class=pill id=host></span>
  <span class=spacer></span>
  <span class=pill id=asof></span>
  <button class=sm onclick="load()">&#8635;</button>
</header>
<div class=wrap>

  <div class=sec><h2>&#128279; Quick links</h2><div class=links id=links></div></div>

  <div class=grid>
    <div class=card><h3>&#9889; System <span class=live></span></h3>
      <div class=brandrow id=sys-brands></div>
      <div class=big id=sys-os>&ndash;</div><div class=sub id=sys-kernel></div>
      <div class=kv><span class=k>Uptime</span><span class=v id=sys-uptime></span></div>
      <div class=kv><span class=k>CPU load</span><span class=v id=sys-load></span></div>
      <svg class=spark id=sp-cpu viewBox="0 0 100 34" preserveAspectRatio=none></svg>
      <div class=kv><span class=k>CPU temp</span><span class=v id=sys-temp></span></div>
      <div class=kv><span class=k>Memory</span><span class=v id=sys-mem></span></div><div class=bar id=membar><span></span></div>
      <div class=kv style=margin-top:6px><span class=k>Disk</span><span class=v id=sys-disk></span></div>
      <div class=kv style=margin-top:6px><span class=k>Disk I/O</span><span class=v id=sys-io>&mdash;</span></div>
      <svg class=spark id=sp-disk viewBox="0 0 100 34" preserveAspectRatio=none></svg>
    </div>
    <div class=card><h3>&#128225; Network <span class=live></span></h3>
      <div class=big id=net-dn>&ndash;</div><div class=sub>download Mbps &middot; <span id=net-up></span> up</div>
      <svg class=spark id=sp-net viewBox="0 0 100 34" preserveAspectRatio=none></svg>
      <div class=kv><span class=k>interface</span><span class=v id=net-if></span></div>
      <div class=kv><span class=k>internet</span><span class=v id=speed-res>&mdash;</span></div>
      <button class=sm style=margin-top:6px onclick="doSpeedtest(this)">Speed test</button>
    </div>
    <div class=card><h3>&#128190; NAS storage</h3>
      <div class=big id=nas-state>&ndash;</div><div class=sub id=nas-sub></div><div class=bar id=nasbar><span></span></div>
      <div class=kv style=margin-top:8px><span class=k>read speed</span><span class=v id=nas-speed>&mdash;</span></div>
      <button class=sm style=margin-top:6px onclick="nasSpeed(this)">Test speed</button>
    </div>
    <div class=card><h3>&#128274; VPN (torrents) <span class=live></span></h3>
      <div class=big id=vpn-state>&ndash;</div>
      <div class=kv><span class=k>exit IP</span><span class=v id=vpn-ip></span></div>
      <div class=kv><span class=k>port</span><span class=v id=vpn-port></span></div>
      <div class=kv><span class=k>tunnel throughput</span><span class=v id=vpn-tp>&mdash;</span></div>
      <svg class=spark id=sp-vpn viewBox="0 0 100 34" preserveAspectRatio=none></svg>
      <div class=sub id=vpn-note></div>
    </div>
    <div class=card><h3>&#127918; GPU / drivers</h3>
      <div class=brandrow id=gpu-brands></div>
      <div class=big id=gpu-name>&ndash;</div>
      <div class=kv><span class=k>driver</span><span class=v id=gpu-driver></span></div>
      <div class=kv><span class=k>temp / usage</span><span class=v id=gpu-tu></span></div>
      <div class=bar id=gpubar><span></span></div>
      <div class=sub id=gpu-note></div>
    </div>
    <div class=card><h3>&#8681; Downloads <span class=live></span></h3>
      <div class=big id=dl-speed>&ndash;</div><div class=sub>total down (Mbps)</div>
      <svg class=spark id=sp-qbit viewBox="0 0 100 34" preserveAspectRatio=none></svg>
      <div class=kv style=margin-top:6px><span class=k>Usenet (SAB)</span><span class=v id=dl-sab></span></div>
      <div class=kv><span class=k>Torrents</span><span class=v id=dl-qbit></span></div>
    </div>
    <div class=card><h3>&#128190; Disks (SMART)</h3>
      <div id=disks><small class=mut>&ndash;</small></div>
    </div>
    <div class=card><h3>&#127916; Media library</h3>
      <div id=media><small class=mut>&ndash;</small></div>
    </div>
  </div>

  <div class=sec><h2>&#127909; Plex &mdash; now playing <small id=plex-n></small></h2>
    <div id=plex-list><small class=mut>nothing playing</small></div>
  </div>

  <div class=sec>
    <div class=sechead><h2>&#128200; History (24h)</h2></div>
    <div class=hgrid id=hist></div>
  </div>

  <div class=sec><h2>&#11014; Updates &amp; actions</h2>
    <div class=row>
      <button onclick="doBackup(this)">&#128190; Back up now</button>
      <button class=warn id=btn-os onclick="doOsUpdate(this)">&#11014; Update OS</button>
      <span id=os-line class=mut></span>
    </div>
    <div id=reboot style=margin-top:9px></div>
    <pre id=out style=display:none></pre>
    <details><summary>OS update run log</summary><pre id=os-run>&ndash;</pre></details>
  </div>

  <div class=sec>
    <div class=sechead>
      <h2>&#128230; Containers <small id=cont-count></small></h2>
      <button class=sm id=cont-toggle onclick="toggleCont()">Collapse</button>
    </div>
    <div class=tiles id=tiles></div>
    <pre id=logbox style=display:none></pre>
  </div>

  <div class=sec><h2>&#128451; Backups <small id=bk-count></small></h2>
    <table id=bktbl><thead><tr><th>File</th><th>Size</th><th>When</th></tr></thead><tbody></tbody></table>
  </div>

  <div class=sec><h2>&#9211; Power</h2>
    <div class=row><button class=danger onclick="doReboot(this)">Reboot server</button>
      <small class=mut>graceful reboot &mdash; containers auto-start, NAS auto-remounts</small></div>
  </div>

</div></div>
<div id=toast></div>
<script>
const TOKEN=new URLSearchParams(location.search).get('token')||'';
const qs=TOKEN?('?token='+encodeURIComponent(TOKEN)):'';
const $=id=>document.getElementById(id);
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2800);}
function dot(k){return '<span class="dot d-'+k+'"></span> ';}
function setbar(el,pct,warn,bad){const b=$(el);b.className='bar'+(pct>=bad?' bad':(pct>=warn?' warn':''));b.firstElementChild.style.width=Math.min(100,pct||0)+'%';}
function busy(b,on,l){if(!b)return;b.disabled=on;if(on){b.dataset.t=b.textContent;b.textContent='… '+l;}else if(b.dataset.t){b.textContent=b.dataset.t;}}
function showout(t){const o=$('out');o.style.display='';o.textContent=t;}
const EYE='<svg class=beye viewBox="0 0 44 22"><ellipse cx=22 cy=11 rx=19 ry=9 fill=none stroke=currentColor stroke-width=2.6/><circle cx=22 cy=11 r=3.4 fill=currentColor/></svg>';
function brand(kind,label,title){const eye=(kind==='nvidia'||kind==='rog')?EYE:'';return '<span class="brand b-'+kind+'" title="'+(title||label).replace(/"/g,'')+'">'+eye+label+'</span>';}
function brandsFor(text){const t=(text||'').toLowerCase(),out=[];
 if(/rog|republic of gamers/.test(t))out.push(brand('rog','ROG',text));
 else if(/asus/.test(t))out.push(brand('asus','ASUS',text));
 if(/intel|core\s*i\d|\bi[3579]-/.test(t))out.push(brand('intel','intel',text));
 else if(/amd|ryzen|threadripper/.test(t))out.push(brand('amd','AMD',text));
 return out;}
function toggleCont(){const t=$('tiles');t.classList.toggle('collapsed');$('cont-toggle').textContent=t.classList.contains('collapsed')?'Expand':'Collapse';}
// live sparklines
const LB={};
function pushv(k,v){const a=LB[k]||(LB[k]=[]);a.push(Math.max(+v||0,0));if(a.length>50)a.shift();}
function spark(id,series){const el=$(id);if(!el)return;const W=100,H=34;
 let mx=0.001;series.forEach(s=>(LB[s.k]||[]).forEach(v=>{if(v>mx)mx=v;}));
 let html='';series.forEach(s=>{const a=LB[s.k]||[];if(a.length<2)return;
  const pts=a.map((v,i)=>[(i/(a.length-1))*W,(H-2)-(v/mx)*(H-5)]);
  const d='M'+pts.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' L');
  if(s.area)html+='<path class="sa '+s.cls+'" d="'+d+' L'+W+','+H+' L0,'+H+' Z"/>';
  html+='<path class="sl '+s.cls+'" d="'+d+'"/>';});
 el.innerHTML=html;}
const fmt=v=>v>=1?v.toFixed(1):v.toFixed(2);
async function live(){
 let d;try{d=await(await fetch('/api/live'+qs)).json();}catch(e){return;}
 pushv('rx',d.net.rx_mbps);pushv('tx',d.net.tx_mbps);pushv('cpu',d.cpu);
 pushv('dr',d.disk.r_mbps);pushv('dw',d.disk.w_mbps);
 pushv('qdl',d.qbit.dl_mbps);pushv('qup',d.qbit.up_mbps);
 $('net-dn').textContent=fmt(d.net.rx_mbps);$('net-up').textContent=fmt(d.net.tx_mbps)+' Mbps';
 if(d.cpu)$('sys-load').textContent=(($('sys-load').textContent.split(' (')[0])||'')+' ('+d.cpu+'%)';
 $('sys-io').textContent='R '+fmt(d.disk.r_mbps)+' · W '+fmt(d.disk.w_mbps)+' MB/s';
 $('vpn-tp').textContent='↓'+fmt(d.qbit.dl_mbps)+' · ↑'+fmt(d.qbit.up_mbps)+' Mbps';
 $('dl-qbit').textContent='↓'+fmt(d.qbit.dl_mbps)+' · ↑'+fmt(d.qbit.up_mbps)+' Mbps';
 spark('sp-net',[{k:'rx',cls:'sk-cyan',area:1},{k:'tx',cls:'sk-gold'}]);
 spark('sp-cpu',[{k:'cpu',cls:'sk-green',area:1}]);
 spark('sp-disk',[{k:'dr',cls:'sk-cyan',area:1},{k:'dw',cls:'sk-gold'}]);
 spark('sp-vpn',[{k:'qdl',cls:'sk-violet',area:1},{k:'qup',cls:'sk-gold'}]);
 spark('sp-qbit',[{k:'qdl',cls:'sk-green',area:1},{k:'qup',cls:'sk-gold'}]);
}

async function load(){
 let d; try{ d=await (await fetch('/api/status'+qs)).json(); }catch(e){ toast('offline'); return; }
 $('asof').textContent=(d.generated||'?');
 const s=d.system||{};
 $('sys-os').textContent=s.os||'—'; $('sys-kernel').textContent='kernel '+(s.kernel||'?');
 const hw=s.hw||{};
 $('sys-brands').innerHTML=[].concat(brandsFor((hw.board||'')+' '+(hw.vendor||'')),brandsFor(hw.cpu||'')).join('');
 $('sys-uptime').textContent=s.uptime||'?';
 const nc=s.ncpu||1, cp=Math.round((parseFloat(s.load||0)/nc)*100);
 $('sys-load').textContent=(s.load||'?')+' ('+cp+'%)';
 $('sys-temp').textContent=(s.cpu_temp!=null?s.cpu_temp+'°C':'—');
 const mu=s.mem_used_mb||0,mt=s.mem_total_mb||1;
 $('sys-mem').textContent=(mu/1024).toFixed(1)+'/'+(mt/1024).toFixed(1)+' GB'; setbar('membar',Math.round(mu/mt*100),75,90);
 $('sys-disk').textContent=(s.disk_root_pct||'?')+' · '+(s.disk_root_free||'?')+' free';
 const nt=d.net||{}; $('net-dn').textContent=(nt.rx_mbps||0); $('net-up').textContent=(nt.tx_mbps||0)+' Mbps'; $('net-if').textContent=nt.iface||'—';
 const n=d.nas||{};
 if(!n.configured){$('nas-state').innerHTML=dot('mut')+'n/a';}
 else if(n.mounted){$('nas-state').innerHTML=dot('ok')+'Mounted';$('nas-sub').textContent=(n.used||'?')+' / '+(n.total||'?')+' · '+(n.free||'?')+' free';setbar('nasbar',parseInt(n.used_pct)||0,85,95);}
 else{$('nas-state').innerHTML=dot('bad')+'NOT mounted';$('nas-sub').textContent='auto-remounts within ~3 min';}
 const v=d.vpn||{}, up=(v.health==='healthy'||v.health==='running');
 $('vpn-state').innerHTML=up?dot('ok')+'Connected':dot('bad')+(v.health||'down');
 $('vpn-ip').textContent=v.exit_ip||'—'; $('vpn-port').textContent=v.port||'—';
 $('vpn-note').textContent=up?'torrents exit via VPN, not home IP':'kill-switch: torrents blocked until VPN is back';
 const g=d.gpu||{};
 if(!g.present){$('gpu-name').textContent='none';$('gpu-driver').textContent='—';$('gpu-tu').textContent='—';$('gpu-brands').innerHTML='';}
 else{$('gpu-name').textContent=g.name;$('gpu-driver').textContent=g.driver;$('gpu-tu').textContent=(g.temp||'?')+'°C · '+(g.util||'0')+'%';
   setbar('gpubar',parseInt(g.util)||0,70,90);
   $('gpu-brands').innerHTML=/nvidia|geforce|rtx|gtx/i.test(g.name||'')?brand('nvidia','NVIDIA',g.name):(/radeon|amd/i.test(g.name||'')?brand('amd','AMD',g.name):'');}
 const dw=d.downloads||{}, sab=dw.sab||{}, qb=dw.qbit||{};
 $('dl-speed').textContent=((sab.speed_mbps||0)+(qb.dl_mbps||0)).toFixed(1);
 $('dl-sab').textContent=(sab.speed_mbps||0)+' Mbps · '+(sab.items||0)+' q'+(sab.status?(' ('+sab.status+')'):'');
 $('dl-qbit').textContent=(qb.dl_mbps||0)+' Mbps · '+(qb.active||0)+' active';
 // plex
 const ps=(d.plex||{}).sessions||[]; $('plex-n').textContent=ps.length?('· '+ps.length+' streaming'):'';
 $('plex-list').innerHTML=ps.length?ps.map(function(x){
  const poster=x.thumb?('<img class=pposter loading=lazy src="/plex-img?key='+encodeURIComponent(x.thumb)+(TOKEN?('&token='+encodeURIComponent(TOKEN)):'')+'">'):'<div class=pposter></div>';
  const tags=[x.quality,x.mode,x.player].filter(Boolean).map(t=>'<span class=ptag>'+t+'</span>').join('');
  const bw=x.bandwidth?('<span class=ptag>'+x.bandwidth+' Mbps</span>'):'';
  return '<div class=pnow>'+poster+'<div class=pmeta><b>'+x.title+'</b><br><small class=mut>'+(x.user||'')+'</small>'+
   '<div class=pbar><span style="width:'+(x.progress||0)+'%"></span></div>'+
   '<div class=ptags>'+tags+bw+'</div></div></div>';
 }).join(''):'<small class=mut>nothing playing</small>';
 // updates
 const u=d.updates||{};
 $('os-line').textContent=(u.os_count>0)?(u.os_count+' OS update(s)'+(u.os_security>0?' · '+u.os_security+' security':'')):'up to date';
 const bo=$('btn-os'); bo.disabled=d.os_pending||!(u.os_count>0); if(d.os_pending)$('os-line').textContent='OS update running…';
 $('reboot').innerHTML=(u.reboot?'<span class="badge b-bad">&#9888; reboot required</span> ':'')+(d.reboot_pending?'<span class="badge b-up">rebooting…</span>':'');
 $('os-run').textContent=d.os_runlog||'(nothing yet)';
 // gpu note
 const rec=(u.nvidia_recommended||''); $('gpu-note').textContent=(rec&&g.driver&&rec.indexOf(g.driver.split('.')[0])<0)?('newer: '+rec):'';
 // containers
 const til=$('tiles');
 const cs=(d.containers||[]).slice().sort((a,b)=>(b.update-a.update)||a.name.localeCompare(b.name));
 $('cont-count').textContent='· '+cs.length+' running · '+cs.filter(c=>c.update).length+' updates';
 til.innerHTML=cs.map(function(c){
  const running=(c.status||'').toLowerCase().startsWith('up');
  const bdg=c.update?'<span class="badge b-up">update</span>':'<span class="badge b-ok">ok</span>';
  const ub=c.update?'<button class=sm onclick="upd(\''+c.name+'\',this)">Update</button> ':'';
  return '<div class="tile'+(c.update?' upd':(running?'':' down'))+'">'+
   '<div class=tname>'+dot(running?'ok':'bad')+c.name+'</div>'+
   '<div class=tstat>'+c.status+(c.cpu?(' · '+c.cpu+' cpu'):'')+(c.memp?(' · '+c.memp+' mem'):'')+'</div>'+
   '<div style=margin-bottom:8px>'+bdg+'</div>'+
   '<div class=tacts>'+ub+'<button class=sm onclick="rst(\''+c.name+'\',this)">Restart</button> <button class=sm onclick="lg(\''+c.name+'\')">Logs</button></div>'+
   '</div>';
 }).join('');
 // backups
 const bt=document.querySelector('#bktbl tbody');bt.innerHTML='';const bk=d.backups||[];
 $('bk-count').textContent='· '+bk.length;
 bt.innerHTML=bk.length?bk.map(b=>'<tr><td><small>'+b.name+'</small></td><td>'+(b.size_mb>=1024?(b.size_mb/1024).toFixed(1)+' GB':b.size_mb+' MB')+'</td><td><small>'+b.date+'</small></td></tr>').join(''):'<tr><td colspan=3><small class=mut>none yet</small></td></tr>';
 // links
 // links + service health
 const svc={};(d.services||[]).forEach(s=>svc[s.name]=s);
 $('links').innerHTML=(d.links||[]).map(function(l){const s=svc[l.name];const cls=s?(s.up?'up':'down'):'';
  return '<a class="'+cls+'" href="'+l.url+'" target=_blank><span class=ld></span>'+l.name+' &#8599;</a>';}).join('')||'<small class=mut>none configured</small>';
 // disks (SMART)
 const disks=d.disks||[];
 $('disks').innerHTML=disks.length?disks.map(function(k){
  const bad=(k.ok===false)||(k.warn&&k.warn!==0);
  const bits=[];if(k.temp!=null)bits.push(k.temp+'°C');if(k.pct_used!=null)bits.push(k.pct_used+'% used');if(k.hours!=null)bits.push(Math.round(k.hours/24)+'d on');
  return '<div class=drow><span>'+dot(bad?'bad':'ok')+(k.name||'')+' <small class=mut>'+(k.model||'')+'</small></span><span class=v>'+bits.join(' · ')+'</span></div>';
 }).join(''):'<small class=mut>no SMART data yet</small>';
 // media library (radarr/sonarr)
 const md=d.media||{};
 function arrRow(name,a){return a?('<div class=drow><span>'+name+'</span><span class=v>'+(a.queue||0)+' queue · '+(a.missing||0)+' missing</span></div>'):('<div class=drow><span>'+name+'</span><span class=mut>offline</span></div>');}
 $('media').innerHTML=arrRow('Radarr (movies)',md.radarr)+arrRow('Sonarr (TV)',md.sonarr);
}
async function post(path){return (await fetch(path+qs,{method:'POST'})).text();}
async function doBackup(b){busy(b,true,'backing up');showout('Running full backup…');try{showout(await post('/backup'));toast('backup done');}catch(e){showout(''+e);}finally{busy(b,false);load();}}
async function doOsUpdate(b){if(!confirm('Apply OS updates now? Docker may briefly restart.'))return;busy(b,true,'…');try{showout(await post('/os-update'));toast('OS update requested');}catch(e){showout(''+e);}finally{busy(b,false);setTimeout(load,1500);}}
async function doReboot(b){if(!confirm('Reboot the whole server now?'))return;busy(b,true,'…');try{showout(await post('/reboot'));toast('reboot requested');}catch(e){showout(''+e);}finally{busy(b,false);setTimeout(load,1500);}}
async function upd(n,b){if(!confirm('Back up and update '+n+'?'))return;busy(b,true,'updating');showout('Updating '+n+'…');try{showout(await post('/update?name='+encodeURIComponent(n)));toast(n+' updated');}catch(e){showout(''+e);}finally{busy(b,false);load();}}
async function rst(n,b){if(!confirm('Restart '+n+'?'))return;busy(b,true,'…');try{showout(await post('/restart?name='+encodeURIComponent(n)));toast(n+' restarted');}catch(e){showout(''+e);}finally{busy(b,false);load();}}
async function lg(n){const box=$('logbox');box.style.display='';box.textContent='loading '+n+' logs…';box.textContent=await (await fetch('/logs'+qs+(qs?'&':'?')+'name='+encodeURIComponent(n))).text();box.scrollIntoView({behavior:'smooth'});}
async function nasSpeed(b){busy(b,true,'testing');try{const t=await post('/nasspeed');$('nas-speed').textContent=t.trim();toast('NAS: '+t.trim());}catch(e){toast(''+e);}finally{busy(b,false);}}
async function doSpeedtest(b){busy(b,true,'testing');$('speed-res').textContent='running…';try{const r=JSON.parse(await post('/speedtest'));$('speed-res').textContent='↓'+r.down+' · ↑'+r.up+' Mbps';toast('Internet: ↓'+r.down+' ↑'+r.up+' Mbps');}catch(e){$('speed-res').textContent='failed';}finally{busy(b,false);}}
function drawChart(id,a){const el=$(id);if(!el||a.length<2)return;const W=100,H=54;const mx=Math.max.apply(null,a.concat(0.001)),mn=Math.min.apply(null,a.concat(0)),rng=(mx-mn)||1;
 const pts=a.map((v,i)=>[(i/(a.length-1))*W,(H-3)-((v-mn)/rng)*(H-6)]);
 const p='M'+pts.map(q=>q[0].toFixed(1)+','+q[1].toFixed(1)).join(' L');
 el.innerHTML='<path class=sa d="'+p+' L'+W+','+H+' L0,'+H+' Z"/><path class=sl d="'+p+'"/>';}
async function loadHist(){
 let h;try{h=await(await fetch('/api/history?hours=24'+(qs?('&'+qs.slice(1)):''))).json();}catch(e){return;}
 const rows=h.rows||[],idx={};(h.cols||[]).forEach((c,i)=>idx[c]=i);
 const S=[{k:'cpu_temp',t:'CPU temp',u:'°C',c:'sk-red'},{k:'gpu_temp',t:'GPU temp',u:'°C',c:'sk-green'},{k:'mem_pct',t:'Memory',u:'%',c:'sk-violet'},{k:'nas_pct',t:'NAS used',u:'%',c:'sk-cyan'},{k:'rx',t:'Net down',u:'Mbps',c:'sk-cyan'},{k:'plex',t:'Plex streams',u:'',c:'sk-gold'}];
 $('hist').innerHTML=S.map((s,i)=>'<div class=hbox><h4>'+s.t+'</h4><div class=hv id=hv'+i+'>–</div><svg class="hchart '+s.c+'" id=hc'+i+' viewBox="0 0 100 54" preserveAspectRatio=none></svg></div>').join('');
 S.forEach(function(s,i){const a=rows.map(r=>+r[idx[s.k]]||0);if(!a.length)return;$('hv'+i).textContent=a[a.length-1]+(s.u?(' '+s.u):'');drawChart('hc'+i,a);});
}
load();setInterval(load,20000);
live();setInterval(live,2000);
loadHist();setInterval(loadHist,60000);
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){});}
</script></body></html>"""


ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
            '<rect width="128" height="128" rx="26" fill="#050810"/>'
            '<circle cx="64" cy="64" r="40" fill="none" stroke="#22d3ee" stroke-width="7"/>'
            '<circle cx="64" cy="64" r="20" fill="#22d3ee"/>'
            '<circle cx="64" cy="64" r="52" fill="none" stroke="#22d3ee" stroke-width="3" opacity="0.5"/>'
            '</svg>')

MANIFEST = json.dumps({
    "name": "Command Center", "short_name": "Command", "start_url": "/",
    "display": "standalone", "background_color": "#050810", "theme_color": "#050810",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
})

SW_JS = (
    "const C='hsw-v1';"
    "self.addEventListener('install',e=>{self.skipWaiting();});"
    "self.addEventListener('activate',e=>{self.clients.claim();});"
    "self.addEventListener('fetch',e=>{const u=new URL(e.request.url);"
    "if(u.pathname.startsWith('/api')||u.pathname.startsWith('/logs')||u.pathname==='/plex-img'){return;}"
    "e.respondWith(fetch(e.request).then(r=>{const c=r.clone();"
    "caches.open(C).then(x=>x.put(e.request,c));return r;})"
    ".catch(()=>caches.match(e.request)));});"
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authed(self, q):
        if not TOKEN:
            return True
        return q.get("token", [""])[0] == TOKEN or self.headers.get("X-Token") == TOKEN

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/manifest.webmanifest":
            return self._send(200, MANIFEST, "application/manifest+json")
        if u.path == "/sw.js":
            return self._send(200, SW_JS, "application/javascript")
        if u.path == "/icon.svg":
            return self._send(200, ICON_SVG, "image/svg+xml")
        if not self._authed(q):
            return self._send(401, "unauthorized", "text/plain")
        if u.path == "/":
            return self._send(200, PAGE)
        if u.path == "/api/status":
            return self._send(200, json.dumps(status()), "application/json")
        if u.path == "/api/live":
            return self._send(200, json.dumps(live()), "application/json")
        if u.path == "/api/history":
            hrs = q.get("hours", ["24"])[0]
            try:
                hrs = max(1, min(168, int(hrs)))
            except Exception:  # noqa: BLE001
                hrs = 24
            return self._send(200, json.dumps(history(hrs)), "application/json")
        if u.path == "/plex-img":
            key = q.get("key", [""])[0]
            tok = plex_token()
            if not key.startswith("/library/") or not tok:
                return self._send(404, b"", "image/gif")
            try:
                url = PLEX_URL + key + ("&" if "?" in key else "?") + "X-Plex-Token=" + tok
                with urllib.request.urlopen(url, timeout=8) as r:
                    return self._send(200, r.read(), r.headers.get("Content-Type", "image/jpeg"))
            except Exception:  # noqa: BLE001
                return self._send(404, b"", "image/gif")
        if u.path == "/logs":
            name = q.get("name", [""])[0]
            if name not in {c["name"] for c in running_containers()}:
                return self._send(400, "unknown container", "text/plain")
            _, out = sh(["docker", "logs", "--tail", "80", name], timeout=30)
            return self._send(200, out or "(no logs)", "text/plain")
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._send(401, "unauthorized", "text/plain")
        names = {c["name"] for c in running_containers()}
        if u.path == "/backup":
            _, out = sh([os.path.join(SCRIPTS, "backup.sh")])
            return self._send(200, out, "text/plain")
        if u.path == "/speedtest":
            return self._send(200, json.dumps(speedtest()), "application/json")
        if u.path == "/os-update":
            if read_json(STATUS_JSON).get("updates", {}).get("os_count", 0) <= 0:
                return self._send(200, "Already up to date.", "text/plain")
            open(OS_FLAG, "w").write(str(int(time.time())))
            return self._send(200, "OS update requested — applies within ~1 minute.", "text/plain")
        if u.path == "/reboot":
            open(REBOOT_FLAG, "w").write(str(int(time.time())))
            return self._send(200, "Reboot requested — the server will restart within ~1 minute.", "text/plain")
        if u.path == "/nasspeed":
            f = f"F=$(find {MEDIA_PATH} -maxdepth 2 -name '*.mkv' 2>/dev/null | shuf | head -1); " \
                f"dd if=\"$F\" of=/dev/null bs=1M count=100 2>&1 | tail -1 | grep -oE '[0-9.]+ [MG]B/s'"
            _, out = sh(["docker", "exec", MEDIA_CONTAINER, "sh", "-c", f], timeout=60)
            return self._send(200, out.strip() or "n/a", "text/plain")
        if u.path == "/update":
            name = q.get("name", [""])[0]
            if name not in names:
                return self._send(400, "unknown container", "text/plain")
            _, out = sh([os.path.join(SCRIPTS, "update.sh"), name])
            return self._send(200, out, "text/plain")
        if u.path == "/restart":
            name = q.get("name", [""])[0]
            if name not in names:
                return self._send(400, "unknown container", "text/plain")
            _, out = sh(["docker", "restart", name], timeout=90)
            return self._send(200, out or "restarted", "text/plain")
        return self._send(404, "not found", "text/plain")


if __name__ == "__main__":
    print(f"command center on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
