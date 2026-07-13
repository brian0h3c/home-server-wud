# home-server-wud

A tiny, **plug-and-play update-management stack** for a self-hosted Docker box.

It gives you:

- 📊 **A dashboard** ([What's Up Docker / WUD](https://github.com/getwud/wud)) to **see which containers have image updates** and update them with **one click** — nothing auto-updates behind your back.
- 💾 **`update.sh`** — a safe updater that **backs up a container's config *before* pulling the new image**, so you can always roll back.
- �️ **`backup.sh`** — full-stack backup of your **compose folder + all app configs** (skips media/downloads/caches), with rotation, an **optional copy to a NAS/second location**, and **optional extra paths** (e.g. an exported NAS config).
- 🖥️ **`os-update-check.sh`** — logs available **OS (apt) updates** daily so host updates are visible too.

Everything is generic — no secrets, no hard-coded IPs. Clone, set a couple of env vars, and go.

> **Heads-up:** WUD is an *image-update watcher* — it can't run shell/backup jobs
> itself. So backups run three ways: **on a schedule** (cron), **on demand**
> (`backup.sh`), and **automatically before an update** (`update.sh --full`).

---

## Requirements

- Linux host with **Docker** + **Docker Compose v2** (`docker compose ...`)
- The OS logger targets **Debian/Ubuntu** (`apt`)

## Quick start

```bash
git clone https://github.com/brian0h3c/home-server-wud.git
cd home-server-wud
cp .env.example .env          # edit TZ / WUD_PORT if you like
docker compose up -d
```

Open the dashboard: **http://<your-server-ip>:4012**

WUD scans all your running containers every 6 hours (configurable) and shows
which have a newer image. Each one gets a manual **Update** button.

> Tip: for a home LAN, bind the port to your LAN IP so it isn't exposed —
> set `WUD_PORT=192.168.1.10:4012` style by editing `docker-compose.yml`.

## Control panel (Backup now / Update buttons)

A second small UI at **http://<your-server-ip>:4013** gives you buttons:

- **Backup now** — runs a full-stack backup (see below)
- **Update** — per container: **backs up, then pulls + recreates** it
- Live view of which containers have updates + the OS update count

Point `PROJECT_DIR` in `.env` at your stack (e.g. `/home/me/docker`) — it's
mounted at the same path inside the panel so `docker compose` resolves your
bind-mount paths correctly. Set `PANEL_TOKEN` to require `?token=...`.

> ⚠️ The panel executes Docker operations — keep it on your LAN, never expose it
> to the internet.

---

## Safe updates with automatic backup

WUD's one-click button recreates a container **without** a backup. For anything
stateful (databases, *arr apps, etc.) use the wrapper instead — it snapshots the
config first:

```bash
./scripts/update.sh sonarr            # backup -> pull -> recreate
./scripts/update.sh radarr prowlarr   # multiple at once
./scripts/update.sh --list            # list running containers
```

Backups land in `./backups/<container>_<timestamp>.tar.gz` (last 5 kept per
container). Point it at your stack's compose file if it lives elsewhere:

```bash
COMPOSE_FILE=/path/to/your/docker-compose.yml ./scripts/update.sh sonarr
```

**Roll back** an update:

```bash
docker compose stop sonarr
sudo tar -xzf backups/sonarr_20260712_2312.tar.gz -C /
docker compose start sonarr
```

Config knobs (env): `BACKUP_DIR`, `KEEP`, `BACKUP_DESTS` (default
`/config /data /app/config`), `COMPOSE_FILE`.

---

## Full-stack backups

Back up your whole compose project (compose files, `.env`, every app's config
dir) in one archive — big/regenerable stuff (media, downloads, caches) is
skipped automatically:

```bash
PROJECT_DIR=/path/to/your/docker ./scripts/backup.sh
./scripts/backup.sh --list                       # list existing backups
```

Optional extras and off-box copy:

```bash
# also include an exported NAS config, and drop a copy on a mounted NAS share
PROJECT_DIR=/home/me/docker \
DEST_DIR=/mnt/nas/_Backups \
EXTRA_PATHS="/home/me/nas-config-export" \
  ./scripts/backup.sh
```

Back up **before every update** (full snapshot, then update the container):

```bash
PROJECT_DIR=/home/me/docker ./scripts/update.sh --full sonarr
```

Restore a stack backup:

```bash
sudo tar -xzf backups/stack_<timestamp>.tar.gz -C /
```

Env knobs: `PROJECT_DIR`, `BACKUP_DIR`, `KEEP` (default 7), `DEST_DIR`,
`EXTRA_PATHS`, `EXCLUDES`.

---

## Scheduling (cron)

```bash
./scripts/setup-cron.sh          # OS check @07:00 + full backup @03:30
NO_BACKUP=1 ./scripts/setup-cron.sh    # OS check only
BACKUP_TIME="0 2" ./scripts/setup-cron.sh   # backup @02:00
```

---

## OS update visibility

```bash
./scripts/os-update-check.sh          # writes logs/os-updates-latest.txt + history
```

Then just read `logs/os-updates-latest.txt`. Apply the updates yourself with
`sudo apt full-upgrade` when you're ready.

---

## Good to know / gotchas

- **WUD watches container images only — not the OS.** That's what the OS script
  is for.
- Some apps show an **in-app "update available"** that checks their GitHub, not
  the Docker `:latest` tag. If `docker pull` says *"Image is up to date"*, WUD is
  right and there's simply no newer **image** yet.
- **Don't one-click-update fragile containers from the WUD UI** — e.g. anything
  using a `tmpfs` transcode dir (Plex) or `network_mode: service:<vpn>`
  (a torrent client behind a VPN). Update those with `./scripts/update.sh` or
  `docker compose pull && docker compose up -d` so their special config is
  preserved.
- WUD needs the Docker socket (`/var/run/docker.sock`). Keep the dashboard on
  your LAN, not the public internet.

## License

MIT — see [LICENSE](LICENSE).
