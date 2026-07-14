#!/usr/bin/env python3
"""
home-server-wud control panel — a tiny, dependency-free web UI to:
  * see running containers + which have image updates (via WUD's API)
  * see available OS updates (+ reboot-required)
  * "Full backup"  -> runs scripts/backup.sh
  * "Update OS"    -> requests a host-side apt full-upgrade (writes a flag file;
                      a root cron running scripts/os-update-runner.sh applies it)
  * per-container "Update" -> scripts/update.sh <name> (backup, then update)

Security: bind to your LAN only, optionally set PANEL_TOKEN. Container names are
validated against the live `docker ps` list and passed as argv (no shell).
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
OS_SNAP = os.environ.get("OS_SNAPSHOT", os.path.join(LOGDIR, "os-updates-latest.txt"))
OS_RUNLOG = os.environ.get("OS_RUNLOG", os.path.join(LOGDIR, "os-update-run.log"))
OS_FLAG = os.environ.get("OS_UPDATE_FLAG", os.path.join(LOGDIR, ".os-update-request"))
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
    _, out = sh(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"], timeout=30)
    rows = []
    for line in out.strip().splitlines():
        if "\t" in line:
            n, img = line.split("\t", 1)
            rows.append({"name": n, "image": img})
    return rows


def wud_updates():
    try:
        with urllib.request.urlopen(WUD_URL + "/api/containers", timeout=8) as r:
            data = json.load(r)
        return {c.get("name"): bool(c.get("updateAvailable")) for c in data}
    except Exception:  # noqa: BLE001
        return {}


def _read(path, tail=0):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = f.read()
        if tail:
            data = "\n".join(data.splitlines()[-tail:])
        return data
    except Exception:  # noqa: BLE001
        return ""


def os_info():
    snap = _read(OS_SNAP)
    count, security, reboot = 0, 0, False
    for line in snap.splitlines():
        if line.startswith("updates_available="):
            for tok in line.split():
                if tok.startswith("updates_available="):
                    count = int(tok.split("=", 1)[1] or 0)
                if tok.startswith("security="):
                    security = int(tok.split("=", 1)[1] or 0)
        if "REBOOT REQUIRED" in line:
            reboot = True
    return {
        "count": count, "security": security, "reboot": reboot,
        "snapshot": snap or "No snapshot yet.",
        "runlog": _read(OS_RUNLOG, tail=40),
        "pending": os.path.exists(OS_FLAG),
    }


PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<title>Home Server Panel</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
 :root{--bg:#0e1116;--card:#161b24;--line:#232a36;--txt:#e8eaed;--mut:#93a0b4;
   --accent:#3b82f6;--green:#22c55e;--amber:#f59e0b;--red:#ef4444}
 *{box-sizing:border-box}
 body{font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;margin:0;background:var(--bg);color:var(--txt)}
 header{padding:16px 22px;background:linear-gradient(90deg,#12161d,#171d27);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px}
 header h1{margin:0;font-size:17px;font-weight:600}
 .pill{font-size:12px;padding:3px 9px;border-radius:999px;background:#1f2734;color:var(--mut)}
 .wrap{padding:22px;max-width:940px;margin:0 auto}
 .grid{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}
 .stat{flex:1;min-width:150px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
 .stat .n{font-size:26px;font-weight:700} .stat .l{color:var(--mut);font-size:13px;margin-top:2px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
 .card h2{margin:0 0 10px;font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px}
 table{width:100%;border-collapse:collapse}
 th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);font-size:14px}
 th{color:var(--mut);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
 tr:last-child td{border-bottom:0}
 .badge{font-size:12px;padding:2px 9px;border-radius:999px;font-weight:600}
 .b-up{background:rgba(245,158,11,.15);color:var(--amber)} .b-ok{background:rgba(34,197,94,.13);color:var(--green)}
 .b-warn{background:rgba(239,68,68,.15);color:var(--red)}
 button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px;font-weight:600;transition:.15s}
 button:hover{filter:brightness(1.1)} button:disabled{opacity:.5;cursor:not-allowed}
 button.ghost{background:#232b38} button.warn{background:var(--amber);color:#111} button.sm{padding:5px 11px;font-size:12px}
 .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 pre{background:#0a0d12;border:1px solid var(--line);border-radius:8px;padding:12px;white-space:pre-wrap;word-break:break-word;max-height:320px;overflow:auto;font-size:12px;line-height:1.5;margin:8px 0 0}
 small,.mut{color:var(--mut)} .spacer{flex:1}
 #toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1f2734;border:1px solid var(--line);padding:10px 16px;border-radius:10px;font-size:13px;opacity:0;transition:.25s;pointer-events:none}
 #toast.show{opacity:1}
 .dot{width:8px;height:8px;border-radius:50%;display:inline-block} .d-ok{background:var(--green)}.d-up{background:var(--amber)}
 .muted-img{color:var(--mut);font-size:12px}
</style></head><body>
<header>
  <span style="font-size:20px">&#128295;</span>
  <h1>Home Server Panel</h1>
  <span class=pill id=clock></span>
  <span class=spacer></span>
  <button class="ghost sm" onclick="load()">&#8635; Refresh</button>
</header>
<div class=wrap>

  <div class=grid>
    <div class=stat><div class=n id=s-cont>&ndash;</div><div class=l>containers</div></div>
    <div class=stat><div class=n id=s-upd style="color:var(--amber)">&ndash;</div><div class=l>image updates</div></div>
    <div class=stat><div class=n id=s-os style="color:var(--accent)">&ndash;</div><div class=l>OS updates</div></div>
    <div class=stat><div class=n id=s-sec>&ndash;</div><div class=l>security</div></div>
  </div>

  <div class=card>
    <h2>&#9889; Actions</h2>
    <div class=row>
      <button id=btn-backup onclick="doBackup()">&#128190; Full backup</button>
      <button id=btn-os class=warn onclick="doOsUpdate()">&#11014;&#65039; Update OS</button>
      <small id=os-hint class=mut></small>
    </div>
    <pre id=out>Ready.</pre>
  </div>

  <div class=card>
    <h2>&#128230; Containers</h2>
    <table id=tbl><thead><tr><th>Container</th><th>Image</th><th>Status</th><th></th></tr></thead><tbody></tbody></table>
  </div>

  <div class=card>
    <h2>&#128421;&#65039; OS updates</h2>
    <div id=reboot></div>
    <pre id=os-snap>...</pre>
    <div id=os-runwrap style="display:none"><small class=mut>Last OS update run:</small><pre id=os-run></pre></div>
  </div>

</div>
<div id=toast></div>
<script>
const TOKEN=new URLSearchParams(location.search).get('token')||'';
const qs=TOKEN?('?token='+encodeURIComponent(TOKEN)):'';
const $=id=>document.getElementById(id);
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2600);}
function tick(){$('clock').textContent=new Date().toLocaleTimeString();}
setInterval(tick,1000);tick();

async function load(){
 try{
  const d=await (await fetch('/api/status'+qs)).json();
  const nUp=d.containers.filter(c=>c.update).length;
  $('s-cont').textContent=d.containers.length;
  $('s-upd').textContent=nUp;
  $('s-os').textContent=d.os.count;
  $('s-sec').textContent=d.os.security;
  $('s-sec').style.color=d.os.security>0?'var(--red)':'var(--txt)';
  const tb=document.querySelector('#tbl tbody');tb.innerHTML='';
  d.containers.sort((a,b)=>(b.update-a.update)||a.name.localeCompare(b.name)).forEach(c=>{
    const tr=document.createElement('tr');
    const st=c.update?'<span class="badge b-up">update available</span>':'<span class="badge b-ok">up to date</span>';
    const btn=c.update?'<button class=sm onclick="upd(\''+c.name+'\',this)">Update</button>':'';
    tr.innerHTML='<td><span class="dot '+(c.update?'d-up':'d-ok')+'"></span> '+c.name+'</td>'+
                 '<td class=muted-img>'+c.image+'</td><td>'+st+'</td><td style=text-align:right>'+btn+'</td>';
    tb.appendChild(tr);
  });
  $('os-snap').textContent=d.os.snapshot;
  $('reboot').innerHTML=d.os.reboot?'<span class="badge b-warn">&#9888; reboot required</span>':'';
  const btnOs=$('btn-os');
  if(d.os.pending){btnOs.disabled=true;$('os-hint').textContent='OS update running on host\u2026';}
  else if(d.os.count>0){btnOs.disabled=false;$('os-hint').textContent=d.os.count+' update(s) available';}
  else{btnOs.disabled=true;$('os-hint').textContent='system is up to date';}
  if(d.os.runlog){$('os-runwrap').style.display='';$('os-run').textContent=d.os.runlog;}
 }catch(e){toast('failed to load: '+e);}
}
function busy(b,on,label){b.disabled=on;if(on){b.dataset.t=b.textContent;b.textContent='\u2026 '+label;}else if(b.dataset.t){b.textContent=b.dataset.t;}}
async function doBackup(){
 const b=$('btn-backup');busy(b,true,'backing up');$('out').textContent='Running full backup\u2026 (this can take a minute)';
 try{const r=await fetch('/backup'+qs,{method:'POST'});$('out').textContent=await r.text();toast('backup done');}
 catch(e){$('out').textContent='error: '+e;}finally{busy(b,false);}
}
async function doOsUpdate(){
 if(!confirm('Apply available OS updates on the host now?\nDocker may briefly restart if it is among the updates.'))return;
 const b=$('btn-os');busy(b,true,'requesting');
 try{const r=await fetch('/os-update'+qs,{method:'POST'});$('out').textContent=await r.text();toast('OS update requested');}
 catch(e){$('out').textContent='error: '+e;}finally{busy(b,false);setTimeout(load,1500);}
}
async function upd(name,btn){
 if(!confirm('Back up and update '+name+'?'))return;
 busy(btn,true,'updating');$('out').textContent='Updating '+name+' (backup first)\u2026';
 try{const r=await fetch('/update'+qs+(qs?'&':'?')+'name='+encodeURIComponent(name),{method:'POST'});
     $('out').textContent=await r.text();toast(name+' updated');}
 catch(e){$('out').textContent='error: '+e;}finally{busy(btn,false);load();}
}
load();setInterval(load,30000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter
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
            ups = wud_updates()
            conts = [{**c, "update": ups.get(c["name"], False)} for c in running_containers()]
            return self._send(200, json.dumps({"containers": conts, "os": os_info()}),
                              "application/json")
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
            info = os_info()
            if info["count"] <= 0:
                return self._send(200, "System is already up to date — nothing to do.", "text/plain")
            try:
                with open(OS_FLAG, "w", encoding="utf-8") as f:
                    f.write(str(int(time.time())))
            except Exception as e:  # noqa: BLE001
                return self._send(500, f"could not write request flag: {e}", "text/plain")
            return self._send(
                200,
                "OS update requested. A host job will apply it within ~1 minute.\n"
                "Watch the 'OS updates' panel for the run log. A reboot may be needed after.",
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
    print(f"panel on :{PORT} (token {'set' if TOKEN else 'off'})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
