#!/usr/bin/env bash
# Root cron: dump SMART / NVMe health for all physical disks to JSON so the
# (unprivileged) collector + dashboard can show drive health without sudo.
# Uses smartctl if installed; falls back to hwmon temperature otherwise.
OUT="${SMART_OUT:-/home/b/docker/backup/smart.json}"
OWNER="${SMART_OWNER:-b}"

python3 - "$OUT" <<'PY'
import glob, json, os, subprocess, sys

out = sys.argv[1]
disks = []
names = []
try:
    r = subprocess.run(["lsblk", "-dno", "NAME,TYPE"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        p = line.split()
        if len(p) == 2 and p[1] == "disk":
            names.append(p[0])
except Exception:
    pass

have_smart = subprocess.run(["which", "smartctl"], capture_output=True).returncode == 0

for n in names:
    dev = "/dev/" + n
    d = {"name": n, "dev": dev, "ok": True}
    got = False
    if have_smart:
        try:
            j = json.loads(subprocess.run(["smartctl", "-a", "-j", dev],
                                          capture_output=True, text=True).stdout or "{}")
            d["model"] = j.get("model_name", "")
            if "smart_status" in j:
                d["ok"] = bool(j["smart_status"].get("passed", True))
            if "nvme_smart_health_information_log" in j:
                h = j["nvme_smart_health_information_log"]
                d["temp"] = h.get("temperature")
                d["pct_used"] = h.get("percentage_used")
                d["spare"] = h.get("available_spare")
                d["warn"] = h.get("critical_warning")
                d["hours"] = h.get("power_on_hours")
                got = True
            elif "temperature" in j:
                d["temp"] = j.get("temperature", {}).get("current")
                d["hours"] = j.get("power_on_time", {}).get("hours")
                for a in j.get("ata_smart_attributes", {}).get("table", []):
                    if a.get("name") in ("Media_Wearout_Indicator", "Wear_Leveling_Count"):
                        d["pct_used"] = 100 - int(a.get("value", 100))
                got = True
        except Exception as e:  # noqa: BLE001
            d["error"] = str(e)
    if not got and d.get("temp") is None:
        for hw in glob.glob("/sys/class/hwmon/hwmon*"):
            try:
                if open(hw + "/name").read().strip() == "nvme":
                    d["temp"] = round(int(open(hw + "/temp1_input").read()) / 1000)
                    break
            except Exception:
                pass
    disks.append(d)

tmp = out + ".tmp"
open(tmp, "w").write(json.dumps({"disks": disks}, indent=1))
os.replace(tmp, out)
PY

chown "$OWNER":"$OWNER" "$OUT" 2>/dev/null || true
