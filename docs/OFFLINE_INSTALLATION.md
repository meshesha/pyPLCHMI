# Installing pyPLCHMI on an offline network (Windows and Linux)

This guide explains how to install and run pyPLCHMI on a computer with **no Internet
access**, such as a plant / OT network, an isolated control room or a DMZ server.
Docker is not used.

The idea is simple:

1. On a **connected computer** (the *staging computer*), download everything once: the
   pyPLCHMI source code, Python (if the target does not have it) and every Python library
   as a *wheel* file.
2. Carry these files to the **target computer** with your site's approved transfer
   medium.
3. On the target, create a Python virtual environment and install the libraries **from
   the local files**. pip is told never to contact the Internet.

pyPLCHMI itself needs no Internet at run time. Bootstrap, the icons, the fonts and the
chart library are bundled, so operator browsers need no Internet either.

---

## Contents

1. [System requirements](#1-system-requirements)
2. [What to download (on the staging computer)](#2-what-to-download-on-the-staging-computer)
3. [Transfer to the offline network](#3-transfer-to-the-offline-network)
4. [Installation on Windows](#4-installation-on-windows)
5. [Installation on Linux](#5-installation-on-linux)
6. [First steps after the installation](#6-first-steps-after-the-installation)
7. [Upgrading to a new version](#7-upgrading-to-a-new-version)
8. [Backup and restore](#8-backup-and-restore)
9. [Troubleshooting](#9-troubleshooting)
10. [Security checklist for OT networks](#10-security-checklist-for-ot-networks)

---

## 1. System requirements

### 1.1 Server (target computer)

| Item | Windows | Linux |
|---|---|---|
| Operating system | Windows 10 / 11 (64-bit), Windows Server 2016 or newer | Any distribution with **glibc 2.28 or newer** and Python 3.10 – 3.13, for example Ubuntu 22.04+, Debian 12+, RHEL / Rocky / Alma 8+ |
| CPU architecture | x86-64 (64-bit) | x86-64 or ARM64 (aarch64), e.g. industrial PCs, Raspberry Pi 4/5 with a 64-bit OS |
| Python | **3.10, 3.11, 3.12 or 3.13, 64-bit**. Python 3.14 is not supported yet, because not every library publishes 3.14 wheels. **Recommended: 3.12.** | Same versions; usually the distribution's own Python (see *1.3*) |
| CPU / RAM | 2 cores, 4 GB RAM minimum; 4 cores, 8 GB recommended | same |
| Disk | 1 GB for the program and libraries, plus the data (see *1.5*) | same |
| Clock | Synchronised with the plant's NTP server or domain controller. Alarm and history timestamps come from this clock. | same |
| Rights | Administrator, to install Python, register auto-start and open the firewall | `root` / `sudo`, for the same reasons |

No compiler is needed. Every library is available as a ready-made wheel for these
systems.

### 1.2 Staging computer (with Internet)

* Any operating system with **Python 3 and pip**. It does not need the same OS as the
  target: pip can download wheels for another platform (see *2.3*).
* **git** (optional), to clone the repository.
* About 200 MB of free space.

### 1.3 Python on Linux targets

Use the distribution's Python when its version is between 3.10 and 3.13:

| Distribution | Default Python | Packages needed |
|---|---|---|
| Ubuntu 22.04 | 3.10 | `python3`, `python3-venv` |
| Ubuntu 24.04 | 3.12 | `python3`, `python3-venv` |
| Debian 12 | 3.11 | `python3`, `python3-venv` |
| Debian 13 | 3.13 | `python3`, `python3-venv` |
| RHEL / Rocky / Alma 9 | 3.9 (too old) | install `python3.12` from AppStream |
| RHEL / Rocky / Alma 8 | 3.6 (too old) | install `python3.11` or `python3.12` from AppStream |
| Ubuntu 20.04 / Debian 11 | 3.8 / 3.9 (too old) | no newer Python in the standard repositories, so upgrade the OS |

On Debian and Ubuntu, `venv` is a separate package (`python3-venv` or `python3.X-venv`).
It is often missing on minimal servers.

### 1.4 Network

| Direction | Port | Purpose |
|---|---|---|
| Operator browsers → server | TCP **8000** (any port you choose) | Web interface |
| Server → PLC | TCP **102** | Siemens S7 (S7-300/400/1200/1500, LOGO!). S7-1200/1500: enable *PUT/GET* and disable *optimized block access* for the DBs that are read. |
| Server → device | TCP **502** | Modbus TCP |
| Server ↔ devices | UDP **47808** (broadcast) | BACnet/IP |
| Server → device | serial port (COM*n* / `/dev/ttyUSB0`) | Modbus RTU through a USB / RS-485 adapter |

Operator stations need only a current browser (Chrome, Edge, Firefox, Safari 16+), with
no plug-ins and no Internet.

### 1.5 Disk space for the data

All data lives in the `instance/` folder of the installation:

* the SQLite database: configuration, alarms, audit log and tag history;
* uploaded images and documents.

The tag history dominates. It takes about **130 bytes per sample**, including the indexes:

> size ≈ logged tags × (86 400 ÷ interval in seconds) × retention days × 130 bytes
>
> Example: 20 tags every 10 s, kept for 90 days ≈ **2 GB**.

The retention period is set in *Admin → System settings* (default: 90 days).

---

## 2. What to download (on the staging computer)

Create a transfer folder, for example `pyplchmi-transfer/`, and collect the following in it.

| # | What | Where from | Needed when |
|---|---|---|---|
| 1 | pyPLCHMI source code | GitHub | always |
| 2 | Python libraries (wheels) | PyPI, through `pip download` | always |
| 3 | Python installer | python.org (Windows); distribution packages (Linux) | only if the target has no suitable Python |
| 4 | Driver of a USB / RS-485 adapter | adapter vendor | only for Modbus RTU on Windows |

### 2.1 Source code

Either clone a tagged release:

```bash
git clone --depth 1 https://github.com/meshesha/pyPLCHMI.git pyplchmi-transfer/pyPLCHMI
```

or download the ZIP from the GitHub page (*Code → Download ZIP*, or a release) into the
transfer folder.

Use **the same version** for the source code and the wheels: download the wheels from the
`requirements.txt` of the source you are transferring.

### 2.2 Choose the target platform

Write down three facts about the **target** computer:

| Fact | How to find it on the target |
|---|---|
| Python version (3.10 – 3.13) | Windows: `py -0p` or `python --version` · Linux: `python3 --version` |
| Operating system | Windows or Linux |
| Architecture | Windows: always x86-64 here · Linux: `uname -m` → `x86_64` or `aarch64` |

The examples below use **Python 3.12**. Replace `3.12` with your version.

### 2.3 Download the wheels

Run the matching command **in the source folder** (where `requirements.txt` is). pip
downloads every library and every dependency as a wheel into `../wheelhouse`. Nothing is
installed on the staging computer.

**Target = Windows x64**

```bash
python -m pip download -r requirements.txt -d ../wheelhouse \
    --only-binary=:all: --implementation cp --python-version 3.12 \
    --platform win_amd64
```

**Target = Linux x86-64**

```bash
python -m pip download -r requirements.txt -d ../wheelhouse \
    --only-binary=:all: --implementation cp --python-version 3.12 \
    --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64
```

**Target = Linux ARM64**

```bash
python -m pip download -r requirements.txt -d ../wheelhouse \
    --only-binary=:all: --implementation cp --python-version 3.12 \
    --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64
```

On Windows staging computers, write each command on one line, or replace the `\` line
endings with `` ` `` in PowerShell.

Notes:

* The two `--platform` values on Linux are both needed. Some libraries only publish
  wheels for the newer tag, and others only for the older one.
* If the staging computer **is** an identical copy of the target (same OS, architecture
  and Python), the simple form also works:
  `python -m pip download -r requirements.txt -d ../wheelhouse`.
* For several targets, run the command once per platform into the same `wheelhouse`
  folder. pip picks the right files on each target.
* To record the exact versions for your change documentation, list the downloaded files:
  `ls ../wheelhouse` (Linux) or `dir ..\wheelhouse` (Windows).

The wheelhouse is typically 30 – 40 MB per platform.

### 2.4 Python itself (only if the target does not have it)

**Windows:** download the *Windows installer (64-bit)* of Python 3.12 from
<https://www.python.org/downloads/windows/>, for example `python-3.12.10-amd64.exe`, into
the transfer folder.

**Linux:** use the distribution's packages. The package versions must match the target's
OS release exactly, so the easiest way is a connected computer or VM with **the same
distribution and release**:

* Debian / Ubuntu: download `python3-venv` and its dependencies without installing them:
  ```bash
  mkdir debs && cd debs
  apt-get download $(apt-cache depends --recurse --no-recommends --no-suggests \
      --no-conflicts --no-breaks --no-replaces --no-enhances python3-venv \
      | grep "^\w" | sort -u)
  ```
  On the target, install them with `sudo dpkg -i *.deb`. Packages that are already
  installed are skipped.
* RHEL / Rocky / Alma:
  ```bash
  dnf download --resolve --destdir rpms python3.12 python3.12-pip
  ```
  On the target, install them with `sudo dnf install ./rpms/*.rpm`.

Many sites keep a local mirror or an installation DVD of their distribution; using it is
just as good.

### 2.5 Checksums

Create a checksum list so the files can be verified after the transfer.

```bash
# Linux / macOS
cd pyplchmi-transfer && find . -type f ! -name SHA256SUMS.txt -exec sha256sum {} + > ../SHA256SUMS.tmp \
    && mv ../SHA256SUMS.tmp SHA256SUMS.txt
```

```powershell
# Windows PowerShell
cd pyplchmi-transfer
Get-ChildItem -Recurse -File -Force -Exclude SHA256SUMS.txt | Get-FileHash -Algorithm SHA256 |
    ForEach-Object { "{0}  {1}" -f $_.Hash.ToLower(), (Resolve-Path -Relative $_.Path) } |
    Set-Content SHA256SUMS.txt
```

The transfer folder now looks like this:

```
pyplchmi-transfer/
├── pyPLCHMI/            source code (with requirements.txt)
├── wheelhouse/          *.whl files
├── python-3.12.10-amd64.exe   (Windows, optional)  or  debs/ / rpms/  (Linux, optional)
└── SHA256SUMS.txt
```

---

## 3. Transfer to the offline network

1. Copy the transfer folder to the medium approved by your site: USB stick, data diode,
   transfer server… Run the usual malware scan.
2. Copy it to a local disk on the target, for example `C:\Install\` or
   `/tmp/pyplchmi-transfer/`.
3. Verify the files:
   * Linux: `cd /tmp/pyplchmi-transfer && sha256sum -c SHA256SUMS.txt`
   * Windows PowerShell:
     ```powershell
     cd C:\Install\pyplchmi-transfer
     Get-Content SHA256SUMS.txt | ForEach-Object {
       $hash, $file = $_ -split '  ', 2
       if ((Get-FileHash $file -Algorithm SHA256).Hash -ne $hash) { "MISMATCH: $file" } }
     ```
     No output means every file is intact.
4. Windows only: remove the "downloaded from the Internet" mark, so scripts and the
   installer are not blocked:
   `Get-ChildItem C:\Install\pyplchmi-transfer -Recurse | Unblock-File`

---

## 4. Installation on Windows

Open **PowerShell as administrator** for the whole procedure. The examples install to
`C:\pyPLCHMI` and use the transfer folder `C:\Install\pyplchmi-transfer`.

### 4.1 Python

Check whether a suitable Python is already installed:

```powershell
py -0p
```

If Python 3.10 – 3.13 (64-bit) is listed, skip to *4.2*. Otherwise install the downloaded
installer silently for all users:

```powershell
Start-Process -Wait C:\Install\pyplchmi-transfer\python-3.12.10-amd64.exe `
    -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_test=0"
```

Then open a **new** PowerShell window, so the new PATH applies, and check with
`py -3.12 --version`.
You can also run the installer by double-click. Tick *Add python.exe to PATH* and choose
*Customize → Install for all users*.

### 4.2 Application files

```powershell
New-Item -ItemType Directory C:\pyPLCHMI
Copy-Item C:\Install\pyplchmi-transfer\pyPLCHMI\* C:\pyPLCHMI -Recurse
cd C:\pyPLCHMI
```

`C:\pyPLCHMI` now contains `wsgi.py`, `requirements.txt`, `app\`, `migrations\` and so on.

### 4.3 Virtual environment and libraries (offline)

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --no-index --find-links C:\Install\pyplchmi-transfer\wheelhouse -r requirements.txt
.\venv\Scripts\python.exe -m pip check
```

* `--no-index` forbids any download, and `--find-links` points pip to the wheel files.
  The libraries are installed into `C:\pyPLCHMI\venv\Lib\site-packages`.
* `pip check` must report *No broken requirements found*.

### 4.4 Settings file (`.env`)

```powershell
Copy-Item .env.example .env
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
notepad .env
```

In `.env`, set at least:

```ini
FLASK_CONFIG=production
SECRET_KEY=<paste the 64-character value printed above>
PLC_SIMULATION=0
SESSION_COOKIE_SECURE=0
```

* `SECRET_KEY` signs the login sessions. Keep it secret and never change it afterwards,
  or every user is logged out.
* `PLC_SIMULATION=1` runs the built-in PLC simulator instead of real PLCs, which is useful
  for a first test.
* The other settings in the file are explained by their comments: BACnet, history and
  time zone.

### 4.5 Database and first administrator

```powershell
.\venv\Scripts\python.exe -m flask db upgrade
.\venv\Scripts\python.exe -m flask seed
.\venv\Scripts\python.exe -m flask create-admin --username admin
```

These commands do the following:

* `db upgrade` creates `instance\plchmi.sqlite`;
* `seed` adds the default roles and permissions;
* `create-admin` asks for the password, at least 8 characters;
* optionally, `flask demo` adds a demo PLC and screen to try the simulator.

### 4.6 Start and test

```powershell
.\venv\Scripts\python.exe -m waitress --host=0.0.0.0 --port=8000 --threads=8 wsgi:app
```

Open `http://localhost:8000` on the server, or `http://<server-ip>:8000` from an
operator station, and log in. In a second PowerShell window you can check the health
endpoint:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing   # → {"status":"ok"}
```

Stop the server with **Ctrl+C**.

Always run **one** server process, with threads instead of several processes. Every
process opens its own PLC connections, and S7 CPUs accept only a few.

### 4.7 Start automatically with Windows

1. Create `C:\pyPLCHMI\start-hmi.bat`:
   ```bat
   @echo off
   cd /d "%~dp0"
   if not exist logs mkdir logs
   "%~dp0venv\Scripts\python.exe" -m waitress --host=0.0.0.0 --port=8000 --threads=8 wsgi:app >> logs\hmi.log 2>&1
   ```
2. Register a scheduled task that starts it at boot as SYSTEM, even when nobody is
   logged in:
   ```powershell
   $action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c "C:\pyPLCHMI\start-hmi.bat"' -WorkingDirectory C:\pyPLCHMI
   $trigger = New-ScheduledTaskTrigger -AtStartup
   $princ   = New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
   $set     = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
   Register-ScheduledTask -TaskName pyPLCHMI -Action $action -Trigger $trigger -Principal $princ -Settings $set
   Start-ScheduledTask pyPLCHMI
   ```
3. Manage it with `Start-ScheduledTask pyPLCHMI`, `Stop-ScheduledTask pyPLCHMI`,
   `Get-ScheduledTask pyPLCHMI`, or in the Task Scheduler (`taskschd.msc`).

### 4.8 Firewall

Allow the web port from the operator networks (Domain and Private profiles):

```powershell
New-NetFirewallRule -DisplayName "pyPLCHMI web (TCP 8000)" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Domain,Private
```

For BACnet/IP, also allow inbound UDP 47808:

```powershell
New-NetFirewallRule -DisplayName "pyPLCHMI BACnet (UDP 47808)" -Direction Inbound -Protocol UDP -LocalPort 47808 -Action Allow -Profile Domain,Private
```

For Modbus RTU, install the driver of the USB / RS-485 adapter and note its COM port in
the Device Manager.

---

## 5. Installation on Linux

The examples install to `/opt/pyplchmi`, run the service as the unprivileged user
`pyplchmi`, and use the transfer folder `/tmp/pyplchmi-transfer`. Run the commands with
`sudo` as shown.

### 5.1 Python and venv

```bash
python3 --version                 # must be 3.10 – 3.13
python3 -m venv --help >/dev/null && echo "venv OK"
```

If `venv` is missing, install the packages you downloaded in *2.4*:

* Debian / Ubuntu: `sudo dpkg -i /tmp/pyplchmi-transfer/debs/*.deb`
* RHEL family: `sudo dnf install /tmp/pyplchmi-transfer/rpms/*.rpm`. Then use
  `python3.12` instead of `python3` in the following steps.

### 5.2 Service user and application files

```bash
sudo useradd --system --home-dir /opt/pyplchmi --shell /usr/sbin/nologin pyplchmi
sudo mkdir -p /opt/pyplchmi
sudo cp -r /tmp/pyplchmi-transfer/pyPLCHMI/. /opt/pyplchmi/
sudo chown -R pyplchmi:pyplchmi /opt/pyplchmi
```

For Modbus RTU, add the user to the group that owns the serial ports:
`sudo usermod -aG dialout pyplchmi`. On RHEL the group is also `dialout`.

### 5.3 Virtual environment and libraries (offline)

```bash
cd /opt/pyplchmi
sudo -u pyplchmi python3 -m venv venv
sudo -u pyplchmi venv/bin/python -m pip install --no-index \
     --find-links /tmp/pyplchmi-transfer/wheelhouse -r requirements.txt
sudo -u pyplchmi venv/bin/python -m pip check        # → No broken requirements found.
```

### 5.4 Settings file (`.env`)

```bash
sudo -u pyplchmi cp .env.example .env
venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
sudo -u pyplchmi nano .env
sudo chmod 600 .env
```

Set at least `FLASK_CONFIG=production`, `SECRET_KEY=<value printed above>` and
`PLC_SIMULATION=0`, as in *4.4*.

### 5.5 Database and first administrator

```bash
sudo -u pyplchmi venv/bin/python -m flask db upgrade
sudo -u pyplchmi venv/bin/python -m flask seed
sudo -u pyplchmi venv/bin/python -m flask create-admin --username admin
```

### 5.6 systemd service

Create `/etc/systemd/system/pyplchmi.service`:

```ini
[Unit]
Description=pyPLCHMI web HMI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pyplchmi
Group=pyplchmi
# SupplementaryGroups=dialout       # uncomment for Modbus RTU
WorkingDirectory=/opt/pyplchmi
Environment=FLASK_CONFIG=production
ExecStart=/opt/pyplchmi/venv/bin/python -m waitress --host=0.0.0.0 --port=8000 --threads=8 wsgi:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pyplchmi
systemctl status pyplchmi
curl -s http://127.0.0.1:8000/api/health            # → {"status":"ok",...}
journalctl -u pyplchmi -f                            # live log
```

Ports below 1024 need root. Keep 8000, or put a reverse proxy in front.

### 5.7 Firewall

* ufw (Ubuntu / Debian):
  `sudo ufw allow from <operator-subnet> to any port 8000 proto tcp`, plus
  `sudo ufw allow 47808/udp` for BACnet.
* firewalld (RHEL family):
  `sudo firewall-cmd --permanent --add-port=8000/tcp && sudo firewall-cmd --reload`, plus
  `--add-port=47808/udp` for BACnet.

BACnet note: *Who-Is* uses broadcasts. If the server has several network cards, set
`BACNET_LOCAL_IP=<ip>/<mask>` in `.env` to the card on the BACnet network.

---

## 6. First steps after the installation

1. Log in with the administrator, and change the password under *Settings* if it was
   shared during the installation.
2. *PLCs → Add* each PLC or device, then click *Test connection*.
3. *Admin → Tag table*: create tags, or import them from Excel.
4. *Screens → New screen*: build the screens.
5. *Admin → Users*: create personal accounts for the operators. The *Viewer* role is
   read-only. For operators who may write set-points, create a role with the
   *write values* permission under *Admin → Roles*.
6. Optional: *Admin → System settings*: history retention, public wall-display screen.

---

## 7. Upgrading to a new version

1. On the staging computer, repeat *2.1* and *2.3* for the new version: new source code
   and new wheels, from the new `requirements.txt`.
2. Transfer and verify the files (*3*).
3. On the target:
   1. Stop the server: `Stop-ScheduledTask pyPLCHMI` on Windows, or
      `sudo systemctl stop pyplchmi` on Linux.
   2. **Back up** `instance/` and `.env` (*8*).
   3. Replace the program files but **keep** `instance/`, `.env` and `venv/`. The
      simplest way is to delete the folders `app/` and `migrations/`, then copy the new
      source over the installation folder.
   4. Install the new libraries offline into the existing venv:
      `… -m pip install --no-index --find-links <new wheelhouse> -r requirements.txt --upgrade`
   5. Upgrade the database schema: `… -m flask db upgrade`.
   6. Start the server again.

If the Python version of the target changes, delete `venv/` and create it again
(*4.3* / *5.3*).

---

## 8. Backup and restore

Back up **`instance/`** (database, uploaded images and documents) and **`.env`**
(settings, `SECRET_KEY`). Stop the server first for a consistent copy.

```powershell
# Windows
Stop-ScheduledTask pyPLCHMI
Compress-Archive C:\pyPLCHMI\instance\*, C:\pyPLCHMI\.env D:\Backup\pyPLCHMI-$(Get-Date -f yyyyMMdd).zip
Start-ScheduledTask pyPLCHMI
```

```bash
# Linux
sudo systemctl stop pyplchmi
sudo tar czf /backup/pyplchmi-$(date +%Y%m%d).tgz -C /opt/pyplchmi instance .env
sudo systemctl start pyplchmi
```

To restore, stop the server, unpack the backup into the installation folder, and start
the server again.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `pip download` on the staging computer: *No matching distribution found* | No wheel exists for that Python version or platform. Check `--python-version` (3.10 – 3.13) and `--platform`. Python 3.14 is not supported yet. |
| `pip install --no-index` on the target: *No matching distribution found for X* | The wheelhouse was downloaded for another Python version or platform, or from another `requirements.txt`. Repeat *2.3* with the target's facts (*2.2*). |
| *ERROR: … is not a supported wheel on this platform* | Same cause: wheels for a different Python version or architecture. |
| `No module named venv` (Linux) | Install `python3-venv` / `python3.X-venv` (*2.4*, *5.1*). |
| `python` opens the Microsoft Store (Windows) | That is a placeholder, not Python. Use `py -3.12` or the full path, e.g. `C:\Program Files\Python312\python.exe`. |
| Running scripts is disabled (Windows) | The commands in this guide do not run `.ps1` scripts. Use `.\venv\Scripts\python.exe …`, not `Activate.ps1`. |
| Server stops at start with a `SECRET_KEY` error | `.env` is missing, still has the example key, or `FLASK_CONFIG` is not `production`. See *4.4*. |
| Other computers cannot open the HMI | Firewall rule missing (*4.8* / *5.7*), Windows network profile set to *Public*, or a different VLAN or subnet. |
| PLC stays offline | Ping the PLC from the server. For S7, check PUT/GET and rack/slot (S7-1200/1500: 0/1, S7-300: 0/2). For Modbus, check the unit ID. Use *PLCs → Test connection* and the server log. |
| Where is the log? | Windows: `logs\hmi.log` (with the start script of *4.7*) · Linux: `journalctl -u pyplchmi` |

---

## 10. Security checklist for OT networks

* Keep the server in the OT network or OT DMZ and **never expose it to the Internet**.
* Open the web port only towards the operator networks.
* Use personal user accounts. Give the *write values* permission only to trained
  operators, and review *Admin → Audit log* regularly.
* Protect `.env` (it holds `SECRET_KEY`) and the backups: Linux `chmod 600`, Windows NTFS
  permissions for administrators and SYSTEM only.
* For HTTPS, put a reverse proxy in front (IIS ARR, nginx, Caddy) and set
  `SESSION_COOKIE_SECURE=1` in `.env`.
* Safety functions belong in the PLC or safety system, never in the HMI.

More: [User guide](USER_GUIDE.md) · [Docker](DOCKER.md) · [Architecture](ARCHITECTURE.md)
