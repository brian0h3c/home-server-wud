#!/usr/bin/env python3
"""
telegram-alert.py — send Telegram alerts on server issues (edge-triggered:
alerts when a problem STARTS, and a "resolved" note when it clears).

Reads the command-center JSON (server-status.json) plus live docker state.
Credentials come from env or an .env file (TG_BOT_TOKEN, TG_CHAT_ID).

Run every 2-3 min via cron. State is kept in a sidecar file so you don't get
spammed every run.
"""
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

STATUS_JSON = os.environ.get("STATUS_JSON", "/home/b/docker/backup/server-status.json")
STATE = os.environ.get("ALERT_STATE", "/home/b/docker/backup/.alert-state.json")
ENV_FILE = os.environ.get("ENV_FILE", "/home/b/docker/.env")
TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT = os.environ.get("TG_CHAT_ID", "")
NTFY_URL = os.environ.get("NTFY_URL", "")        # e.g. https://ntfy.sh/my-server-topic
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
DISK_ROOT_MAX = int(os.environ.get("DISK_ROOT_MAX", "90"))
NAS_MAX = int(os.environ.get("NAS_MAX", "96"))
HOSTNAME = os.environ.get("SERVER_NAME", os.uname().nodename)

# fall back to reading creds from the .env file
_ENVK = ("TG_BOT_TOKEN", "TG_CHAT_ID", "NTFY_URL", "DISCORD_WEBHOOK")
if os.path.exists(ENV_FILE):
    _vals = {}
    for line in open(ENV_FILE, encoding="utf-8", errors="replace"):
        m = re.match(r"\s*(" + "|".join(_ENVK) + r")\s*=\s*(.+?)\s*$", line)
        if m:
            _vals[m.group(1)] = m.group(2).strip().strip('"')
    TOKEN = TOKEN or _vals.get("TG_BOT_TOKEN", "")
    CHAT = CHAT or _vals.get("TG_CHAT_ID", "")
    NTFY_URL = NTFY_URL or _vals.get("NTFY_URL", "")
    DISCORD_WEBHOOK = DISCORD_WEBHOOK or _vals.get("DISCORD_WEBHOOK", "")

if not ((TOKEN and CHAT) or NTFY_URL or DISCORD_WEBHOOK):
    raise SystemExit(0)  # no channel configured yet — do nothing quietly


def send(text):
    plain = re.sub(r"<[^>]+>", "", text)
    if TOKEN and CHAT:
        try:
            data = urllib.parse.urlencode({"chat_id": CHAT, "text": text, "parse_mode": "HTML",
                                           "disable_web_page_preview": "true"}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, timeout=10)
        except Exception:  # noqa: BLE001
            pass
    if NTFY_URL:
        try:
            req = urllib.request.Request(NTFY_URL, data=plain.encode(),
                                         headers={"Title": HOSTNAME, "Tags": "warning"})
            urllib.request.urlopen(req, timeout=10)
        except Exception:  # noqa: BLE001
            pass
    if DISCORD_WEBHOOK:
        try:
            data = json.dumps({"content": f"**{HOSTNAME}**\n{plain}"}).encode()
            req = urllib.request.Request(DISCORD_WEBHOOK, data=data,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception:  # noqa: BLE001
            pass


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def docker_containers():
    """Live docker state: server-status.json has no containers key (the panel
    injects that per request, so it never reaches this script)."""
    try:
        p = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:  # noqa: BLE001
        return []
    if p.returncode != 0:
        return []
    rows = []
    for ln in p.stdout.splitlines():
        if "|" in ln:
            name, st = ln.split("|", 1)
            rows.append({"name": name.strip(), "status": st.strip()})
    return rows


def _wud_url():
    """WUD's port is deliberately unpublished, so reach it on its bridge IP."""
    try:
        p = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", "wud"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:  # noqa: BLE001
        return ""
    ip = p.stdout.strip()
    return "http://%s:3000/api/containers" % ip if ip else ""


def registry_updates():
    """Containers WUD reports as having a newer image published upstream."""
    url = _wud_url()
    if not url:
        return []
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001
        return []
    return [c["name"] for c in data if c.get("updateAvailable") and c.get("name")]


def unapplied_images():
    """Containers running an image older than the tag they were created from.

    Docker rewrites .Config.Image to a bare ID once that tag moves to a newer
    image, so a hex-only reference is itself proof the container is behind.
    This is the case WUD cannot see: it stops watching ID-referenced containers.
    """
    out = []
    try:
        p = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=20)
    except Exception:  # noqa: BLE001
        return out
    for name in p.stdout.split():
        try:
            q = subprocess.run(
                ["docker", "inspect", "--format", "{{.Image}}|{{.Config.Image}}", name],
                capture_output=True, text=True, timeout=15,
            )
            running_id, ref = q.stdout.strip().split("|", 1)
        except Exception:  # noqa: BLE001
            continue
        if re.fullmatch(r"(sha256:)?[0-9a-f]{12,64}", ref):
            out.append(name)
            continue
        try:
            t = subprocess.run(
                ["docker", "images", "--no-trunc", "--format", "{{.ID}}", ref],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:  # noqa: BLE001
            continue
        tag_id = (t.stdout.splitlines() or [""])[0].strip()
        if tag_id and tag_id != running_id:
            out.append(name)
    return out


d = load(STATUS_JSON, {})
if not d:
    raise SystemExit(0)

issues = {}
nas = d.get("nas", {})
if nas.get("configured") and not nas.get("mounted"):
    issues["nas_down"] = "🔴 NAS is <b>not mounted</b> — media unavailable"
vpn = d.get("vpn", {})
if vpn.get("health") not in ("healthy", "running", ""):
    issues["vpn_down"] = f"🔴 VPN is <b>{vpn.get('health','down')}</b> — torrents blocked"
try:
    dr = int(str(d.get("system", {}).get("disk_root_pct", "0")).rstrip("%"))
    if dr >= DISK_ROOT_MAX:
        issues["disk_root"] = f"🟠 System disk is <b>{dr}%</b> full"
except Exception:  # noqa: BLE001
    pass
try:
    npct = int(str(nas.get("used_pct", "0")).rstrip("%"))
    if nas.get("mounted") and npct >= NAS_MAX:
        issues["nas_full"] = f"🟠 NAS is <b>{npct}%</b> full"
except Exception:  # noqa: BLE001
    pass
if d.get("updates", {}).get("reboot"):
    issues["reboot"] = "🟠 A <b>reboot is required</b> (kernel/firmware update)"
if d.get("updates", {}).get("os_security", 0) > 0:
    issues["os_sec"] = f"🟠 <b>{d['updates']['os_security']} security update(s)</b> available"
for c in docker_containers():
    if not str(c.get("status", "")).lower().startswith("up"):
        issues["cont_" + c["name"]] = f"🔴 Container <b>{c['name']}</b> is down ({c.get('status','?')})"
for s in d.get("services", []):
    if not s.get("up"):
        issues["svc_" + s["name"]] = f"🔴 Service <b>{s['name']}</b> is not responding"
for disk in d.get("disks", []):
    if disk.get("ok") is False or (disk.get("warn") not in (None, 0)):
        issues["smart_" + disk["name"]] = f"🔴 Disk <b>{disk['name']}</b> SMART health warning"
    pu = disk.get("pct_used")
    if isinstance(pu, (int, float)) and pu >= 90:
        issues["wear_" + disk["name"]] = f"🟠 Disk <b>{disk['name']}</b> wear at <b>{pu}%</b>"

for _n in registry_updates():
    issues["img_" + _n] = f"\U0001F535 <b>{_n}</b>: a newer image is published upstream"
_stale = unapplied_images()
if _stale:
    # grouped into one key so nine stale containers do not send nine messages
    issues["stale_images"] = (
        "\U0001F535 <b>%d container(s)</b> running an image older than the one already "
        "pulled: <b>%s</b>" % (len(_stale), ", ".join(sorted(_stale)))
    )

prev = load(STATE, {})
prev_keys = set(prev.keys()) if isinstance(prev, dict) else set()
cur_keys = set(issues.keys())

new = cur_keys - prev_keys
gone = prev_keys - cur_keys

msgs = []
for k in sorted(new):
    msgs.append(issues[k])
for k in sorted(gone):
    label = prev.get(k, k)
    msgs.append("✅ Resolved: " + re.sub(r"^[^ ]+ ", "", label))

if msgs:
    send(f"<b>{HOSTNAME}</b>\n" + "\n".join(msgs))

json.dump({k: re.sub(r"<[^>]+>", "", v) for k, v in issues.items()}, open(STATE, "w"))
