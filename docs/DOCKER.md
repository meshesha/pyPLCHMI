# Running pyPLCHMI in Docker

pyPLCHMI ships with a `Dockerfile`, a `docker-compose.yml` and a start script
(`docker/entrypoint.sh`). One container is one complete HMI server: web UI,
PLC drivers, alarm scanner and historian.

## Quick start

```bash
cp .env.example .env
#   SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
#   HMI_ADMIN_USER=admin
#   HMI_ADMIN_PASSWORD=<at least 8 characters>
#   PLC_SIMULATION=0            # 1 = built-in simulator, no hardware needed
docker compose up -d --build
docker compose logs -f          # "pyPLCHMI: starting on port 8000"
```

Open `http://<docker-host>:8000`. On every start the container runs `flask db upgrade`
and `flask seed`. It creates the admin user if that user is missing; an existing user's
password is never changed. When you have logged in, remove `HMI_ADMIN_PASSWORD` from
`.env`.

## Requirements checklist

### Host

| Requirement | Notes |
|---|---|
| Docker Engine 20.10+ with Compose v2 (`docker compose`) | Linux is recommended for production. Docker Desktop on Windows/macOS works, with the network limits below. |
| CPU: x86-64 or ARM64 | All dependencies come as ready-made wheels for both, so it runs on industrial PCs, Raspberry Pi 4/5 and edge gateways. No compiler is needed. |
| About 300 MB of disk for the image, plus the data volume | History samples grow with the number of logged tags and the retention period. |
| Reliable clock (NTP) | Alarm and history timestamps come from the host clock. |

### Configuration (`.env`)

| Variable | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | **yes** | Signs the session cookies. The container refuses to start without it, or with the placeholder value. Keep it the same across restarts, or every user is logged out. |
| `FLASK_CONFIG=production` | set by compose | Disables debug mode. |
| `PLC_SIMULATION` | yes | `0` for real PLCs. |
| `HMI_ADMIN_USER` / `HMI_ADMIN_PASSWORD` | first start | First administrator. |
| `TZ`, `DISPLAY_TIMEZONE` | recommended | Server time zone, and the zone used for dates when the browser reports none. |
| `DATABASE_URL` | optional | Default: SQLite in the volume. The PostgreSQL driver is not included (see *Database* below). |
| `SESSION_COOKIE_SECURE=1` | behind HTTPS | Only when a reverse proxy serves HTTPS. |
| `HMI_THREADS`, `HMI_PORT` | optional | waitress threads (default 8) and the internal port (default 8000). |
| `HMI_DEMO=1` | optional | Demo PLC and screen. |
| `HMI_SKIP_MIGRATIONS=1` | optional | Skip the database steps on start. |

### Persistent data: the `/app/instance` volume

Keep this folder in a named volume (the default) or a bind mount. It holds:

* `plchmi.sqlite`: users, PLCs, tags, screens, alarms, audit log and history;
* `uploads/`: image library; `uploads/library/` holds manuals and PDFs attached to PLCs;
* `languages/`: uploaded translation files.

**Back up** by stopping the container and copying the volume, or by using
`sqlite3 plchmi.sqlite ".backup ..."`. A bind mount must be writable by UID 1000:
`sudo chown -R 1000:0 ./data`.

### Network: the important part for OT

| Protocol | Port | Bridge network (default) | Notes |
|---|---|---|---|
| Web UI | TCP 8000 | ✔ | Published as `8000:8000`. |
| Siemens S7 (snap7) | TCP 102, outgoing | ✔ | The host must route to the PLC network. On S7-1200/1500 enable *PUT/GET* and turn off *optimized block access*. |
| Modbus TCP | TCP 502, outgoing | ✔ | |
| BACnet/IP | UDP 47808, in and out | ⚠ | *Who-Is* discovery uses broadcasts, and these do not cross Docker's NAT. On Linux use `network_mode: host`; then remove `ports:`, because the UI is on host port 8000 directly. Set `BACNET_LOCAL_IP=<host IP>/<mask>`. |
| Modbus RTU (serial) | `/dev/ttyUSB0`… | ✔ with `devices:` | Pass the adapter through with `devices: ["/dev/ttyUSB0:/dev/ttyUSB0"]`. The container user is in the `dialout` group. Serial pass-through is Linux only. |

Docker Desktop on Windows/macOS: outgoing S7 and Modbus TCP work. Host networking and
serial ports are limited; recent Docker Desktop versions offer host networking only as an
opt-in. For BACnet or RTU, use a Linux host or run pyPLCHMI without Docker.

Several network cards (office LAN + OT LAN): publish the UI only on the operator network,
for example `"10.10.0.5:8000:8000"`, and keep the PLC network on the other card.

### Operation

* **One container, one process.** Do not run several replicas, or start gunicorn
  with several workers. Each process opens its own PLC connections, and S7 CPUs allow
  only a few of them. Each process would also run its own alarm scanner and historian,
  writing double rows.
* **Health check:** `GET /api/health` returns `200 {"status":"ok"}`, or `503` if the
  database fails. Docker's `HEALTHCHECK` and the browser's connection monitor both use it.
* **Restart policy:** `unless-stopped`. Operator browsers show a red banner while the
  server is gone, and reconnect by themselves when it is back.
* **Updates:** run `git pull && docker compose up -d --build`. Migrations run
  automatically on start; back up the volume first.
* **Logs:** `docker compose logs -f hmi`. Limit their size with the `json-file`
  driver's `max-size` option.

### Database

SQLite in the volume is fine for a single HMI server. For PostgreSQL, add
`psycopg[binary]` to `requirements.txt`, rebuild, and set
`DATABASE_URL=postgresql+psycopg://user:pass@db/plchmi`. You can add a `db:`
service to `docker-compose.yml` for it.

### Security

* Put a reverse proxy with HTTPS in front (Traefik, Caddy, nginx) and set
  `SESSION_COOKIE_SECURE=1`.
* Never publish port 8000 to the internet. Keep the HMI in the OT DMZ, or reach it through a VPN.
* The container runs as an unprivileged user (UID 1000). It needs no `--privileged`
  and no extra capabilities.
