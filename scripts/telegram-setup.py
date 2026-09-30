#!/usr/bin/env python3
"""
telegram-setup.py — finish Telegram alert setup for an already-created bot.

You provide the bot TOKEN (from @BotFather) in ~/docker/.env as TG_BOT_TOKEN.
This script then:
  1. calls getUpdates to discover the chat ID of whoever messaged the bot,
  2. writes TG_CHAT_ID back into ~/docker/.env,
  3. sends a confirmation message so you know it works.

Run it AFTER you have (a) added TG_BOT_TOKEN to .env and (b) opened Telegram and
sent your bot ANY message (e.g. "hi").
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ENV_FILE = os.environ.get("ENV_FILE", "/home/b/docker/.env")


def env_val(key):
    v = os.environ.get(key, "")
    if not v and os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*" + key + r"\s*=\s*(.+?)\s*$", line)
            if m:
                v = m.group(1).strip().strip('"')
    return v


token = env_val("TG_BOT_TOKEN")
if not token:
    print("[x] No TG_BOT_TOKEN found in", ENV_FILE)
    print("    Add a line:  TG_BOT_TOKEN=123456789:AA....   then re-run.")
    sys.exit(1)

try:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10) as r:
        data = json.load(r)
except Exception as e:  # noqa: BLE001
    print("[x] Could not reach Telegram:", e)
    sys.exit(1)

if not data.get("ok"):
    print("[x] Telegram rejected the token:", data.get("description", data))
    print("    Double-check TG_BOT_TOKEN is the exact string from @BotFather.")
    sys.exit(1)

chats = {}
for upd in data.get("result", []):
    msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
    ch = msg.get("chat", {})
    if ch.get("id"):
        chats[ch["id"]] = ch.get("username") or ch.get("title") or ch.get("first_name", "")

if not chats:
    print("[!] The bot has no messages yet.")
    print("    Open Telegram, find your bot, send it ANY message (e.g. 'hi'),")
    print("    then run this again.")
    sys.exit(2)

chat_id = list(chats.keys())[-1]  # most recent chat
print("[✓] Found chat(s):", chats)
print("[✓] Using chat_id:", chat_id)

# write TG_CHAT_ID into .env (replace existing or append)
lines, found = [], False
if os.path.exists(ENV_FILE):
    for line in open(ENV_FILE, encoding="utf-8", errors="replace"):
        if re.match(r"\s*TG_CHAT_ID\s*=", line):
            lines.append(f"TG_CHAT_ID={chat_id}\n")
            found = True
        else:
            lines.append(line)
if not found:
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"TG_CHAT_ID={chat_id}\n")
open(ENV_FILE, "w", encoding="utf-8").writelines(lines)
print("[✓] Saved TG_CHAT_ID to", ENV_FILE)

payload = urllib.parse.urlencode({
    "chat_id": chat_id,
    "text": "\u2705 Command Center alerts are connected. "
            "I'll message you here the moment something needs attention "
            "(NAS down, VPN down, a container crash, disk warnings, and more).",
}).encode()
try:
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, timeout=10)
    print("[✓] Test message sent — check your Telegram!")
except Exception as e:  # noqa: BLE001
    print("[x] Test send failed:", e)
