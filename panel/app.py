#!/usr/bin/env python3
"""
home-server-wud control panel — a tiny, dependency-free web UI to:
  * see running containers + which have image updates (via WUD's API)
  * see available OS updates
  * click "Backup now"  -> runs scripts/backup.sh
  * click "Update"      -> runs scripts/update.sh <container> (backup, then update)

Security: bind to your LAN only (compose port mapping), and optionally set
PANEL_TOKEN to require a token. Container names are validated against the live
`docker ps` list and passed as argv (no shell), so there's no command injection.
"""
import json
import os
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

WUD_URL = os.environ.get("WUD_URL", "http://wud:3000")
SCRIPTS = os.environ.get("SCRIPTS_DIR", "/app/scripts")
OS_SNAP = os.environ.get("OS_SNAPSHOT", "/app/logs/os-updates-latest.txt")
TOKEN = os.environ.get("PANEL_TOKEN", "")
PORT = int(os.environ.get("PANEL_PORT", "8080"))
ENV = os.environ.copy()  # backup.sh/update.sh inherit PROJECT_DIR/BACKUP_DIR/COMPOSE_FILE


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


def os_snapshot():
    try:
        with open(OS_SNAP, encoding="utf-8") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return "OS update snapshot not available yet (run scripts/os-update-check.sh)."


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>home-server-wud panel</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,Arial,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:16px 20px;background:#171a21;border-bottom:1px solid #2a2f3a}
 h1{margin:0;font-size:18px} .wrap{padding:20px;max-width:900px;margin:0 auto}
 table{width:100%;border-collapse:collapse;margin-top:8px}
 th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #232833;font-size:14px}
 .up{color:#f0b429;font-weight:600}.ok{color:#6bbf59}
 button{background:#2d6cdf;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;font-size:13px}
 button.gray{background:#3a4150}.bar{display:flex;gap:10px;align-items:center;margin:6px 0 14px}
 pre{background:#0b0d11;border:1px solid #232833;border-radius:8px;padding:12px;white-space:pre-wrap;max-height:340px;overflow:auto;font-size:12px}
 .card{background:#141821;border:1px solid #232833;border-radius:10px;padding:14px;margin-bottom:16px}
 small{color:#8a93a6}
</style></head><body>
<header><h1>home-server-wud &middot; control panel</h1></header>
<div class=wrap>
 <div class=bar>
   <button onclick="backup()">Backup now</button>
   <button class=gray onclick="load()">Refresh</button>
   <small id=osline></small>
 </div>
 <div class=card><table id=tbl><thead><tr><th>Container</th><th>Image</th><th>Status</th><th></th></tr></thead><tbody></tbody></table></div>
 <div class=card><b>Output</b><pre id=out>ready.</pre></div>
 <div class=card><b>OS updates</b><pre id=os>...</pre></div>
</div>
<script>
const TOKEN=new URLSearchParams(location.search).get('token')||'';
const qs=TOKEN?('?token='+encodeURIComponent(TOKEN)):'';
async function load(){
 const r=await fetch('/api/status'+qs); const d=await r.json();
 const tb=document.querySelector('#tbl tbody'); tb.innerHTML='';
 d.containers.forEach(c=>{
  const tr=document.createElement('tr');
  const st=c.update?'<span class=up>update available</span>':'<span class=ok>up to date</span>';
  const btn=c.update?`<button onclick="upd('${c.name}')">Update</button>`:'';
  tr.innerHTML=`<td>${c.name}</td><td><small>${c.image}</small></td><td>${st}</td><td>${btn}</td>`;
  tb.appendChild(tr);
 });
 document.querySelector('#os').textContent=d.os;
 const n=d.containers.filter(c=>c.update).length;
 document.querySelector('#osline').textContent=`${d.containers.length} containers · ${n} with updates`;
}
async function backup(){
 out.textContent='running backup...';
 const r=await fetch('/backup'+qs,{method:'POST'}); out.textContent=await r.text();
}
async function upd(name){
 if(!confirm('Backup + update '+name+'?'))return;
 out.textContent='updating '+name+' (backup first)...';
 const r=await fetch('/update'+qs+(qs?'&':'?')+'name='+encodeURIComponent(name),{method:'POST'});
 out.textContent=await r.text(); load();
}
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter logs
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
            return self._send(200, json.dumps({"containers": conts, "os": os_snapshot()}),
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
        if u.path == "/update":
            name = q.get("name", [""])[0]
            valid = {c["name"] for c in running_containers()}
            if name not in valid:
                return self._send(400, f"unknown container: {name!r}", "text/plain")
            _, out = sh([os.path.join(SCRIPTS, "update.sh"), name])
            return self._send(200, out, "text/plain")
        return self._send(404, "not found", "text/plain")


if __name__ == "__main__":
    print(f"home-server-wud panel on :{PORT} (token {'set' if TOKEN else 'not set'})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
