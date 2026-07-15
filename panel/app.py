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
    st["containers"] = [{**c, "update": ups.get(c["name"], False)} for c in running_containers()]
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


PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<title>Command Center</title>
<meta name=viewport content="width=device-width,initial-scale=1">
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
    <div class=card><h3>&#9889; System</h3>
      <div class=brandrow id=sys-brands></div>
      <div class=big id=sys-os>&ndash;</div><div class=sub id=sys-kernel></div>
      <div class=kv><span class=k>Uptime</span><span class=v id=sys-uptime></span></div>
      <div class=kv><span class=k>CPU load</span><span class=v id=sys-load></span></div>
      <div class=kv><span class=k>CPU temp</span><span class=v id=sys-temp></span></div>
      <div class=kv><span class=k>Memory</span><span class=v id=sys-mem></span></div><div class=bar id=membar><span></span></div>
      <div class=kv style=margin-top:6px><span class=k>Disk</span><span class=v id=sys-disk></span></div>
    </div>
    <div class=card><h3>&#128225; Network</h3>
      <div class=big id=net-dn>&ndash;</div><div class=sub>download (Mbps)</div>
      <div class=kv style=margin-top:6px><span class=k>&#8593; upload</span><span class=v id=net-up></span></div>
      <div class=kv><span class=k>interface</span><span class=v id=net-if></span></div>
    </div>
    <div class=card><h3>&#128190; NAS storage</h3>
      <div class=big id=nas-state>&ndash;</div><div class=sub id=nas-sub></div><div class=bar id=nasbar><span></span></div>
      <div class=kv style=margin-top:8px><span class=k>read speed</span><span class=v id=nas-speed>&mdash;</span></div>
      <button class=sm style=margin-top:6px onclick="nasSpeed(this)">Test speed</button>
    </div>
    <div class=card><h3>&#128274; VPN (torrents)</h3>
      <div class=big id=vpn-state>&ndash;</div>
      <div class=kv><span class=k>exit IP</span><span class=v id=vpn-ip></span></div>
      <div class=kv><span class=k>port</span><span class=v id=vpn-port></span></div>
      <div class=sub id=vpn-note></div>
    </div>
    <div class=card><h3>&#127918; GPU / drivers</h3>
      <div class=brandrow id=gpu-brands></div>
      <div class=big id=gpu-name>&ndash;</div>
      <div class=kv><span class=k>driver</span><span class=v id=gpu-driver></span></div>
      <div class=kv><span class=k>temp / usage</span><span class=v id=gpu-tu></span></div>
      <div class=sub id=gpu-note></div>
    </div>
    <div class=card><h3>&#8681; Downloads</h3>
      <div class=big id=dl-speed>&ndash;</div><div class=sub>total down (Mbps)</div>
      <div class=kv style=margin-top:6px><span class=k>Usenet (SAB)</span><span class=v id=dl-sab></span></div>
      <div class=kv><span class=k>Torrents</span><span class=v id=dl-qbit></span></div>
    </div>
  </div>

  <div class=sec><h2>&#127909; Plex &mdash; now playing <small id=plex-n></small></h2>
    <div id=plex-list><small class=mut>nothing playing</small></div>
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
   $('gpu-brands').innerHTML=/nvidia|geforce|rtx|gtx/i.test(g.name||'')?brand('nvidia','NVIDIA',g.name):(/radeon|amd/i.test(g.name||'')?brand('amd','AMD',g.name):'');}
 const dw=d.downloads||{}, sab=dw.sab||{}, qb=dw.qbit||{};
 $('dl-speed').textContent=((sab.speed_mbps||0)+(qb.dl_mbps||0)).toFixed(1);
 $('dl-sab').textContent=(sab.speed_mbps||0)+' Mbps · '+(sab.items||0)+' q'+(sab.status?(' ('+sab.status+')'):'');
 $('dl-qbit').textContent=(qb.dl_mbps||0)+' Mbps · '+(qb.active||0)+' active';
 // plex
 const ps=(d.plex||{}).sessions||[]; $('plex-n').textContent=ps.length?('· '+ps.length+' streaming'):'';
 $('plex-list').innerHTML=ps.length?ps.map(x=>'<div class=now><span class=eq><i></i><i></i><i></i><i></i></span><div><b>'+x.title+'</b><br><small class=mut>'+(x.user||'')+' · '+x.mode+'</small></div></div>').join(''):'<small class=mut>nothing playing</small>';
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
   '<div class=tstat>'+c.status+'</div>'+
   '<div style=margin-bottom:8px>'+bdg+'</div>'+
   '<div class=tacts>'+ub+'<button class=sm onclick="rst(\''+c.name+'\',this)">Restart</button> <button class=sm onclick="lg(\''+c.name+'\')">Logs</button></div>'+
   '</div>';
 }).join('');
 // backups
 const bt=document.querySelector('#bktbl tbody');bt.innerHTML='';const bk=d.backups||[];
 $('bk-count').textContent='· '+bk.length;
 bt.innerHTML=bk.length?bk.map(b=>'<tr><td><small>'+b.name+'</small></td><td>'+(b.size_mb>=1024?(b.size_mb/1024).toFixed(1)+' GB':b.size_mb+' MB')+'</td><td><small>'+b.date+'</small></td></tr>').join(''):'<tr><td colspan=3><small class=mut>none yet</small></td></tr>';
 // links
 $('links').innerHTML=(d.links||[]).map(l=>'<a href="'+l.url+'" target=_blank>'+l.name+' &#8599;</a>').join('')||'<small class=mut>none configured</small>';
}
async function post(path){return (await fetch(path+qs,{method:'POST'})).text();}
async function doBackup(b){busy(b,true,'backing up');showout('Running full backup…');try{showout(await post('/backup'));toast('backup done');}catch(e){showout(''+e);}finally{busy(b,false);load();}}
async function doOsUpdate(b){if(!confirm('Apply OS updates now? Docker may briefly restart.'))return;busy(b,true,'…');try{showout(await post('/os-update'));toast('OS update requested');}catch(e){showout(''+e);}finally{busy(b,false);setTimeout(load,1500);}}
async function doReboot(b){if(!confirm('Reboot the whole server now?'))return;busy(b,true,'…');try{showout(await post('/reboot'));toast('reboot requested');}catch(e){showout(''+e);}finally{busy(b,false);setTimeout(load,1500);}}
async function upd(n,b){if(!confirm('Back up and update '+n+'?'))return;busy(b,true,'updating');showout('Updating '+n+'…');try{showout(await post('/update?name='+encodeURIComponent(n)));toast(n+' updated');}catch(e){showout(''+e);}finally{busy(b,false);load();}}
async function rst(n,b){if(!confirm('Restart '+n+'?'))return;busy(b,true,'…');try{showout(await post('/restart?name='+encodeURIComponent(n)));toast(n+' restarted');}catch(e){showout(''+e);}finally{busy(b,false);load();}}
async function lg(n){const box=$('logbox');box.style.display='';box.textContent='loading '+n+' logs…';box.textContent=await (await fetch('/logs'+qs+(qs?'&':'?')+'name='+encodeURIComponent(n))).text();box.scrollIntoView({behavior:'smooth'});}
async function nasSpeed(b){busy(b,true,'testing');try{const t=await post('/nasspeed');$('nas-speed').textContent=t.trim();toast('NAS: '+t.trim());}catch(e){toast(''+e);}finally{busy(b,false);}}
load();setInterval(load,20000);
</script></body></html>"""


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
        if not self._authed(q):
            return self._send(401, "unauthorized", "text/plain")
        if u.path == "/":
            return self._send(200, PAGE)
        if u.path == "/api/status":
            return self._send(200, json.dumps(status()), "application/json")
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
