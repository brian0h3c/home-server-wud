#!/usr/bin/env python3
"""
home-server-wud — server command center.
A tiny, dependency-free web UI that shows the whole server at a glance
(system, NAS, VPN, GPU/drivers, OS + container updates, backups) and lets you
back up / update with one click.

Host-level stats come from a JSON file written by scripts/server-status.sh.
Live container list, backups list, and the action buttons are handled here.

Security: LAN only. Optional PANEL_TOKEN. Container names are validated against
the live `docker ps` list and passed as argv (never shell).
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
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/app/backups")
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


def list_backups(limit=20):
    out = []
    try:
        for n in os.listdir(BACKUP_DIR):
            if n.endswith(".tar.gz"):
                p = os.path.join(BACKUP_DIR, n)
                st = os.stat(p)
                out.append({"name": n, "size_mb": round(st.st_size / 1048576),
                            "mtime": st.st_mtime})
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda x: -x["mtime"])
    for b in out:
        b["date"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.pop("mtime")))
    return out[:limit]


def status():
    st = read_json(STATUS_JSON)
    ups = wud_updates()
    conts = [{**c, "update": ups.get(c["name"], False)} for c in running_containers()]
    st["containers"] = conts
    st["backups"] = list_backups()
    try:
        with open(OS_RUNLOG, encoding="utf-8", errors="replace") as f:
            st["os_runlog"] = "\n".join(f.read().splitlines()[-30:])
    except Exception:  # noqa: BLE001
        st["os_runlog"] = ""
    st["os_pending"] = os.path.exists(OS_FLAG)
    return st


PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<title>Server Command Center</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
 :root{--bg:#0d1017;--card:#161b24;--card2:#1b2130;--line:#242c3a;--txt:#e9edf3;--mut:#8e9bb0;
   --blue:#3b82f6;--green:#22c55e;--amber:#f59e0b;--red:#ef4444;--violet:#8b5cf6}
 *{box-sizing:border-box}
 body{font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;margin:0;background:var(--bg);color:var(--txt)}
 header{padding:16px 22px;background:linear-gradient(90deg,#11151d,#161d29);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;flex-wrap:wrap}
 header h1{margin:0;font-size:17px;font-weight:700}
 .pill{font-size:12px;padding:3px 10px;border-radius:999px;background:#1e2634;color:var(--mut)}
 .spacer{flex:1}
 .wrap{padding:20px;max-width:1040px;margin:0 auto}
 .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;margin-bottom:16px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 16px}
 .card h3{margin:0 0 10px;font-size:13px;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;display:flex;align-items:center;gap:7px}
 .big{font-size:22px;font-weight:700} .sub{color:var(--mut);font-size:13px;margin-top:3px}
 .kv{display:flex;justify-content:space-between;font-size:14px;padding:3px 0}
 .kv .k{color:var(--mut)}
 .dot{width:9px;height:9px;border-radius:50%;display:inline-block;vertical-align:middle}
 .d-ok{background:var(--green)} .d-warn{background:var(--amber)} .d-bad{background:var(--red)} .d-mut{background:var(--mut)}
 .bar{height:8px;background:#0b0e14;border-radius:6px;overflow:hidden;margin-top:8px}
 .bar>span{display:block;height:100%;background:var(--blue)}
 .bar.warn>span{background:var(--amber)} .bar.bad>span{background:var(--red)}
 table{width:100%;border-collapse:collapse}
 th,td{text-align:left;padding:8px 8px;border-bottom:1px solid var(--line);font-size:14px}
 th{color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.4px}
 tr:last-child td{border-bottom:0}
 .badge{font-size:11px;padding:2px 9px;border-radius:999px;font-weight:600}
 .b-up{background:rgba(245,158,11,.16);color:var(--amber)} .b-ok{background:rgba(34,197,94,.14);color:var(--green)}
 .b-bad{background:rgba(239,68,68,.16);color:var(--red)}
 button{background:var(--blue);color:#fff;border:0;border-radius:9px;padding:9px 15px;cursor:pointer;font-size:13px;font-weight:600}
 button:hover{filter:brightness(1.1)} button:disabled{opacity:.45;cursor:not-allowed}
 button.warn{background:var(--amber);color:#161b24} button.ghost{background:#242c3a} button.sm{padding:5px 11px;font-size:12px}
 .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 pre{background:#0a0d12;border:1px solid var(--line);border-radius:8px;padding:11px;white-space:pre-wrap;word-break:break-word;max-height:260px;overflow:auto;font-size:12px;margin:8px 0 0}
 .sec{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:16px}
 .sec h2{margin:0 0 12px;font-size:15px;font-weight:700}
 small,.mut{color:var(--mut)}
 details summary{cursor:pointer;color:var(--mut);font-size:13px;margin-top:8px}
 #toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1e2634;border:1px solid var(--line);padding:10px 16px;border-radius:10px;font-size:13px;opacity:0;transition:.25s;pointer-events:none}
 #toast.show{opacity:1}
 a{color:var(--blue)}
</style></head><body>
<header>
  <span style="font-size:20px">&#128225;</span>
  <h1>Server Command Center</h1>
  <span class=pill id=host></span>
  <span class=spacer></span>
  <span class=pill id=asof></span>
  <button class="ghost sm" onclick="load()">&#8635; Refresh</button>
</header>
<div class=wrap>

  <div class=cards>
    <div class=card><h3>&#128421; System</h3>
      <div class=big id=sys-os>&ndash;</div>
      <div class=sub id=sys-kernel></div>
      <div class=kv><span class=k>Uptime</span><span id=sys-uptime></span></div>
      <div class=kv><span class=k>CPU load</span><span id=sys-load></span></div>
      <div class=kv><span class=k>Memory</span><span id=sys-mem></span></div>
      <div class=bar id=membar><span></span></div>
      <div class=kv style=margin-top:8px><span class=k>Disk (system)</span><span id=sys-disk></span></div>
    </div>

    <div class=card><h3>&#128190; NAS storage</h3>
      <div class=big id=nas-state>&ndash;</div>
      <div class=sub id=nas-sub></div>
      <div class=bar id=nasbar><span></span></div>
    </div>

    <div class=card><h3>&#128274; VPN (torrents)</h3>
      <div class=big id=vpn-state>&ndash;</div>
      <div class=kv><span class=k>Exit IP</span><span id=vpn-ip></span></div>
      <div class=kv><span class=k>Forwarded port</span><span id=vpn-port></span></div>
      <div class=sub id=vpn-note></div>
    </div>

    <div class=card><h3>&#127918; GPU / drivers</h3>
      <div class=big id=gpu-name>&ndash;</div>
      <div class=kv><span class=k>NVIDIA driver</span><span id=gpu-driver></span></div>
      <div class=kv><span class=k>Temp / usage</span><span id=gpu-tu></span></div>
      <div class=sub id=gpu-note></div>
    </div>
  </div>

  <div class=sec>
    <h2>&#11014; Updates &amp; actions</h2>
    <div class=row>
      <button id=btn-backup onclick="doBackup()">&#128190; Back up now</button>
      <button id=btn-os class=warn onclick="doOsUpdate()">&#11014; Update OS</button>
      <span id=os-line class=mut></span>
    </div>
    <div id=reboot style=margin-top:10px></div>
    <pre id=out style=display:none></pre>
    <details><summary>OS update run log</summary><pre id=os-run>&ndash;</pre></details>
  </div>

  <div class=sec>
    <h2>&#128230; Containers <small id=cont-count></small></h2>
    <table id=tbl><thead><tr><th>Name</th><th>Status</th><th>Update</th><th></th></tr></thead><tbody></tbody></table>
  </div>

  <div class=sec>
    <h2>&#128451; Backups <small id=bk-count></small></h2>
    <table id=bktbl><thead><tr><th>File</th><th>Size</th><th>When</th></tr></thead><tbody></tbody></table>
  </div>

</div>
<div id=toast></div>
<script>
const TOKEN=new URLSearchParams(location.search).get('token')||'';
const qs=TOKEN?('?token='+encodeURIComponent(TOKEN)):'';
const $=id=>document.getElementById(id);
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2800);}
function dot(kind){return '<span class="dot d-'+kind+'"></span> ';}
function setbar(el,pct,warn,bad){const b=$(el);b.className='bar'+(pct>=bad?' bad':(pct>=warn?' warn':''));b.firstElementChild.style.width=Math.min(100,pct)+'%';}

async function load(){
 let d;
 try{ d=await (await fetch('/api/status'+qs)).json(); }catch(e){ toast('load failed'); return; }
 $('host').textContent=(d.system&&d.system.os)?'':'';
 $('asof').textContent='updated '+(d.generated||'?');

 // System
 const s=d.system||{};
 $('sys-os').textContent=s.os||'—';
 $('sys-kernel').textContent='kernel '+(s.kernel||'?');
 $('sys-uptime').textContent=s.uptime||'?';
 const load=parseFloat(s.load||0), nc=s.ncpu||1, cpupct=Math.round(load/nc*100);
 $('sys-load').textContent=(s.load||'?')+'  ('+cpupct+'% of '+nc+' cores)';
 const mu=s.mem_used_mb||0, mt=s.mem_total_mb||1, mpct=Math.round(mu/mt*100);
 $('sys-mem').textContent=(mu/1024).toFixed(1)+' / '+(mt/1024).toFixed(1)+' GB';
 setbar('membar',mpct,75,90);
 $('sys-disk').textContent=(s.disk_root_pct||'?')+' used · '+(s.disk_root_free||'?')+' free';

 // NAS
 const n=d.nas||{};
 if(!n.configured){ $('nas-state').innerHTML=dot('mut')+'not configured'; $('nas-sub').textContent=''; $('nasbar').firstElementChild.style.width='0'; }
 else if(n.mounted){ $('nas-state').innerHTML=dot('ok')+'Mounted'; $('nas-sub').textContent=(n.used||'?')+' used of '+(n.total||'?')+' · '+(n.free||'?')+' free'; setbar('nasbar',parseInt(n.used_pct)||0,85,95); }
 else { $('nas-state').innerHTML=dot('bad')+'NOT mounted'; $('nas-sub').textContent='The watchdog remounts it automatically within ~3 min.'; $('nasbar').firstElementChild.style.width='0'; }

 // VPN
 const v=d.vpn||{};
 const up=(v.health==='healthy'||v.health==='running');
 $('vpn-state').innerHTML=up?dot('ok')+'Connected':dot('bad')+(v.health||'down');
 $('vpn-ip').textContent=v.exit_ip||'—';
 $('vpn-port').textContent=v.port||'—';
 $('vpn-note').textContent=up?'Torrents leave via the VPN, not your home IP.':'Torrent traffic is blocked until the VPN reconnects (kill-switch).';

 // GPU
 const g=d.gpu||{};
 if(!g.present){ $('gpu-name').textContent='none'; $('gpu-driver').textContent='—'; $('gpu-tu').textContent='—'; }
 else { $('gpu-name').textContent=g.name||'GPU'; $('gpu-driver').textContent=g.driver||'?';
   $('gpu-tu').textContent=(g.temp||'?')+'°C · '+(g.util||'0')+'% used'; }
 const rec=(d.updates||{}).nvidia_recommended||'';
 $('gpu-note').textContent=(rec && g.driver && rec.indexOf(g.driver.split('.')[0])<0)?('newer available: '+rec+' (staying on current is usually safest)'):'driver up to date';

 // Updates
 const u=d.updates||{};
 $('os-line').textContent=(u.os_count>0)?(u.os_count+' OS update(s) available'+(u.os_security>0?(' · '+u.os_security+' security'):'')):'system is up to date';
 const btnOs=$('btn-os');
 if(d.os_pending){btnOs.disabled=true;$('os-line').textContent='OS update running on host…';}
 else btnOs.disabled=!(u.os_count>0);
 $('reboot').innerHTML=u.reboot?'<span class="badge b-bad">&#9888; reboot required</span>':'';
 $('os-run').textContent=d.os_runlog||'(no OS update has run yet)';

 // Containers
 const tb=document.querySelector('#tbl tbody'); tb.innerHTML='';
 const cs=(d.containers||[]).slice().sort((a,b)=>(b.update-a.update)||a.name.localeCompare(b.name));
 const nUp=cs.filter(c=>c.update).length;
 $('cont-count').textContent='· '+cs.length+' running · '+nUp+' with updates';
 cs.forEach(c=>{
   const tr=document.createElement('tr');
   const st=c.update?'<span class="badge b-up">update available</span>':'<span class="badge b-ok">up to date</span>';
   const running=(c.status||'').toLowerCase().indexOf('up')===0;
   const sdot=running?dot('ok'):dot('bad');
   const btn=c.update?'<button class=sm onclick="upd(\''+c.name+'\',this)">Update</button>':'';
   tr.innerHTML='<td>'+sdot+c.name+'</td><td><small>'+c.status+'</small></td><td>'+st+'</td><td style=text-align:right>'+btn+'</td>';
   tb.appendChild(tr);
 });

 // Backups
 const bt=document.querySelector('#bktbl tbody'); bt.innerHTML='';
 const bk=d.backups||[];
 $('bk-count').textContent='· '+bk.length+' shown';
 if(!bk.length) bt.innerHTML='<tr><td colspan=3><small>no backups yet — click "Back up now"</small></td></tr>';
 bk.forEach(b=>{ const tr=document.createElement('tr');
   tr.innerHTML='<td><small>'+b.name+'</small></td><td>'+(b.size_mb>=1024?(b.size_mb/1024).toFixed(1)+' GB':b.size_mb+' MB')+'</td><td><small>'+b.date+'</small></td>';
   bt.appendChild(tr); });
}
function busy(b,on,label){b.disabled=on;if(on){b.dataset.t=b.textContent;b.textContent='… '+label;}else if(b.dataset.t){b.textContent=b.dataset.t;}}
function showout(t){const o=$('out');o.style.display='';o.textContent=t;}
async function doBackup(){const b=$('btn-backup');busy(b,true,'backing up');showout('Running full backup… (up to a minute)');
 try{showout(await (await fetch('/backup'+qs,{method:'POST'})).text());toast('backup done');}catch(e){showout('error: '+e);}finally{busy(b,false);load();}}
async function doOsUpdate(){if(!confirm('Apply available OS updates on the host now?\nDocker may briefly restart if it is in the list.'))return;
 const b=$('btn-os');busy(b,true,'requesting');
 try{showout(await (await fetch('/os-update'+qs,{method:'POST'})).text());toast('OS update requested');}catch(e){showout('error: '+e);}finally{busy(b,false);setTimeout(load,1500);}}
async function upd(name,btn){if(!confirm('Back up and update '+name+'?'))return;
 busy(btn,true,'updating');showout('Updating '+name+' (backup first)…');
 try{showout(await (await fetch('/update'+qs+(qs?'&':'?')+'name='+encodeURIComponent(name),{method:'POST'})).text());toast(name+' updated');}
 catch(e){showout('error: '+e);}finally{busy(btn,false);load();}}
load();setInterval(load,30000);
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
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._send(401, "unauthorized", "text/plain")
        if u.path == "/backup":
            _, out = sh([os.path.join(SCRIPTS, "backup.sh")])
            return self._send(200, out, "text/plain")
        if u.path == "/os-update":
            st = read_json(STATUS_JSON).get("updates", {})
            if st.get("os_count", 0) <= 0:
                return self._send(200, "System is already up to date.", "text/plain")
            try:
                with open(OS_FLAG, "w", encoding="utf-8") as f:
                    f.write(str(int(time.time())))
            except Exception as e:  # noqa: BLE001
                return self._send(500, f"could not write request: {e}", "text/plain")
            return self._send(200, "OS update requested — the host will apply it within ~1 minute.",
                              "text/plain")
        if u.path == "/update":
            name = q.get("name", [""])[0]
            valid = {c["name"] for c in running_containers()}
            if name not in valid:
                return self._send(400, f"unknown container: {name!r}", "text/plain")
            _, out = sh([os.path.join(SCRIPTS, "update.sh"), name])
            return self._send(200, out, "text/plain")
        return self._send(404, "not found", "text/plain")


if __name__ == "__main__":
    print(f"command center on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
