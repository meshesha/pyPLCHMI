# pyPLCHMI – installation on an offline network (Windows, without Docker)

This document is for networks with **no Internet access**, such as a plant / OT
network, an isolated control room or a DMZ server. Everything you need is in one folder,
**`pyPLCHMI_offline`**:

* the application;
* a ZIP with every Python library, a ready-made `Lib\site-packages` for Python 3.12 64-bit;
* the official Python 3.12 installer (`.exe`);
* an installer script that sets up Python, the virtual environment, the database, the
  administrator, auto-start and the firewall;
* this document, as Markdown and as a printable HTML page.

The target computer never downloads anything.

---

## 1. System requirements

### 1.1 Server (the computer that runs pyPLCHMI)

| Item | Minimum | Recommended |
|---|---|---|
| Operating system | Windows 10 / 11 64-bit, Windows Server 2016 or newer | Windows Server 2019 / 2022 or Windows 10/11 IoT Enterprise LTSC |
| CPU | 2 cores, x64 | 4 cores |
| RAM | 4 GB | 8 GB |
| Disk | 1 GB for the program, plus the data (see *1.4*) | SSD; data on a separate, backed-up drive |
| PowerShell | Windows PowerShell 5.1 (part of Windows) | – |
| Python | 64-bit Python **3.12**. **Not needed beforehand:** the package installs Python 3.12.10 if it is missing. Other installed Python versions are not touched and not used. | – |
| Clock | Correct time, synchronised with the plant's NTP / domain controller | Alarm and history timestamps come from this clock |
| Rights for the installation | Local administrator, for the Python installation, the auto-start task and the firewall rules | – |

32-bit Windows and ARM Windows are not supported by this package.

### 1.2 Operator stations (clients)

* Any current browser: Chrome, Edge or Firefox from 2022 or later, or Safari 16+.
  It must be on the same network as the server.
* No software to install. All scripts, fonts and icons are served by the HMI server
  itself, so the client needs no Internet either.
* Screen resolution of 1280 × 720 or more. Full-screen kiosk mode works on any size.

### 1.3 Network

| Direction | Protocol / port | Purpose |
|---|---|---|
| Clients → server | TCP **8000** (selectable) | Web UI. The installer can open it in Windows Firewall. |
| Server → PLC | TCP **102** | Siemens S7 (S7-300/400/1200/1500, LOGO!). On S7-1200/1500 enable *PUT/GET* and turn off *optimized block access* for the DBs that are read. |
| Server → device | TCP **502** | Modbus TCP |
| Server ↔ devices | UDP **47808** (broadcast) | BACnet/IP. The installer can open it with `-OpenBacnet`. |
| Server → device | COM port | Modbus RTU through a USB / RS-485 adapter. Install the adapter's driver before pyPLCHMI. |

If the server has two network cards (operator LAN and PLC network), allow port 8000 on
the operator side only.

### 1.4 Disk space for the data

Everything is stored in `<installation folder>\instance\`:

* `plchmi.sqlite`: configuration, alarms, audit log and **tag history**;
* `uploads\`: image library, manuals and PDFs attached to PLCs.

The history dominates. Measured, it takes about **130 bytes per sample** including the
indexes. One sample is one value of one logged tag.

> size ≈ number of logged tags × (86 400 / interval in s) × retention days × 130 bytes
>
> Example: 20 tags every 10 s, kept 90 days ≈ 20 × 8 640 × 90 × 130 B ≈ **2 GB**.

Tags that are not logged cost nothing. The retention period is set in
*Admin → System settings*; the default is 90 days.

---

## 2. Contents of the `pyPLCHMI_offline` folder

```
pyPLCHMI_offline\
├── install.bat                  ← double-click this on the target computer
├── install.ps1                  the installer (called by install.bat)
├── uninstall.bat / .ps1         removal; keeps the data unless -RemoveData
├── OFFLINE_INSTALL.md / .html   this document
├── VERSION.txt                  package version and build date
├── SHA256SUMS.txt               checksum of every file in the folder
├── app\                         the application (code, migrations, documentation)
├── site-packages\
│   ├── site-packages-py312-win_amd64.zip   every Python library (ready-made Lib\site-packages)
│   └── packages.txt                        exact name==version list of the libraries
└── python\
    └── python-3.12.10-amd64.exe   official Python installer (signed by the Python Software Foundation)
```

The site-packages ZIP is made for **Python 3.12, 64-bit Windows**. It contains compiled
modules (`.pyd`) for exactly that version, so the installer always uses Python 3.12 and
installs it from `python\` when it is missing. The installer creates a virtual environment,
checks the ZIP's SHA-256 against `SHA256SUMS.txt`, unpacks it into
`<installation folder>\venv\Lib\site-packages` and runs `pip check`, which confirms that
every library and every dependency is present. No network is used at any point.

---

## 3. Transferring the folder to the offline network

1. Copy the whole **`pyPLCHMI_offline`** folder (about 60 MB) onto the transfer medium
   your site allows: USB stick, data diode, transfer server… If your process needs a
   single file, ZIP the folder first. Run your site's malware scan as usual.
2. On the target computer, copy the folder to a local disk, for example
   `C:\Install\pyPLCHMI_offline`. Do not run it from the USB stick.
3. Remove the "downloaded from the Internet" mark, so Windows does not ask about every
   script:
   ```powershell
   Get-ChildItem C:\Install\pyPLCHMI_offline -Recurse | Unblock-File
   ```
4. Optional: verify every file after the transfer.
   ```powershell
   cd C:\Install\pyPLCHMI_offline
   Get-Content SHA256SUMS.txt | ForEach-Object {
     $hash, $file = $_ -split '  ', 2
     if ((Get-FileHash $file -Algorithm SHA256).Hash -ne $hash) { "MISMATCH: $file" } }
   ```
   No output means every file is intact. The installer itself always checks the
   site-packages ZIP and the signature of the Python installer.

---

## 4. Installation (interactive)

1. Double-click **`install.bat`** and confirm the Windows administrator prompt.
2. Answer the questions; press Enter to accept the value in `[brackets]`.

| Question | Default | Notes |
|---|---|---|
| Installation folder | `C:\pyPLCHMI` | The program, data (`instance\`), settings (`.env`) and logs all live here. |
| Web port | `8000` | |
| Use the PLC simulator? | No | *Yes* gives a demo without hardware. You can change it later in `.env`. |
| First administrator user name | `admin` | |
| Password (twice) | – | At least 8 characters. It is passed to the application through a temporary environment variable, never on a command line and never written to a file. |
| Start automatically with Windows? | Yes | A scheduled task named **pyPLCHMI** runs as SYSTEM at boot and restarts every minute after a failure. |
| Allow access from other computers? | Yes | Adds a Windows Firewall rule for the web port (Domain and Private profiles). |

3. The installer then works through 8 steps and prints `OK` for each one:

| Step | What happens |
|---|---|
| 1 Package check and Python | Verifies the SHA-256 of the site-packages ZIP **before anything is changed**, so a damaged copy never leaves a half-upgraded installation. Then looks for a 64-bit Python 3.12: the py launcher (`py -3.12`) first, then `C:\Program Files\Python312` and the user's Python312 folder. If it is missing, it checks the **digital signature** of the bundled installer (it must be *Valid* and signed by the *Python Software Foundation*). It then installs Python 3.12.10 silently for all users in `C:\Program Files\Python312`, side by side with any other Python. |
| 2 Stop and back up | Stops a running pyPLCHMI. On an upgrade it zips `instance\` and `.env` into `backups\`. |
| 3 Copy | Copies the application. `instance\`, `.env`, `logs\` and `backups\` are never touched. |
| 4 Libraries | Creates a new `venv\` and unpacks `site-packages-py312-win_amd64.zip` into `venv\Lib\site-packages`. It then runs `pip check` and test imports of Flask, snap7, pymodbus, BAC0, waitress, openpyxl, bcrypt, SQLAlchemy and Pillow, and pre-compiles the libraries so the first start is fast. |
| 5 Settings | First installation only: creates `.env` with `FLASK_CONFIG=production`, a random 64-character `SECRET_KEY` and `PLC_SIMULATION`. |
| 6 Database | `flask db upgrade` creates or upgrades the database, `flask seed` adds the roles and permissions, and `flask create-admin` adds the first administrator if missing. |
| 7 Start-up | Writes `start-hmi.bat`, and the scheduled task and firewall rules if you chose them. |
| 8 Check | Starts the server, waits until `http://127.0.0.1:<port>/api/health` answers, and prints the addresses to open. |

4. Open `http://<server>:8000` in a browser on an operator station and log in with
   the administrator.

A complete log of every installation is written to `logs\install-<date-time>.log`.

### 4.1 Unattended installation (scripted, many servers)

Run from an administrator PowerShell in the unpacked package:

```powershell
$pw = Read-Host "Admin password" -AsSecureString
.\install.ps1 -Unattended -InstallDir D:\pyPLCHMI -Port 8000 -AdminUser admin -AdminPassword $pw `
              -AutoStart -OpenFirewall
```

| Parameter | Meaning |
|---|---|
| `-InstallDir <folder>` | Installation folder (default `C:\pyPLCHMI`). |
| `-Port <n>` / `-Threads <n>` | Web port (8000) and waitress threads (8). |
| `-AdminUser` / `-AdminPassword` | First administrator. The password must be a SecureString. |
| `-Python <path\python.exe>` | Use this Python 3.12 (64-bit) instead of searching for one. |
| `-Simulation` | Start in simulator mode (first installation). |
| `-Demo` | Create the demo PLC and demo screen. |
| `-AutoStart` | Scheduled task at boot. |
| `-OpenFirewall` / `-OpenBacnet` | Firewall rules for TCP *port* and UDP 47808. |
| `-NoStart` | Do not start the server at the end. |
| `-Unattended` | Ask nothing; parameters and defaults only. |
| `-SkipSignatureCheck` | Install the bundled Python even if Windows cannot confirm its signature. Use only after checking `SHA256SUMS.txt`. |

---

## 5. After the installation

### 5.1 Configure

* **Settings file:** edit `C:\pyPLCHMI\.env` with Notepad, then restart (see *5.2*).
  * `PLC_SIMULATION=0` for real PLCs.
  * `DISPLAY_TIMEZONE`, for example `Asia/Jerusalem`. Only needed if browsers do not
    report their own time zone.
  * `SESSION_COOKIE_SECURE=1` only if a reverse proxy serves HTTPS.
  * `BACNET_LOCAL_IP=<server IP>/<mask>` when BACnet is used and the server has several
    network cards.
* **In the browser:**
  1. *PLCs → Add* each PLC or device, then *Test connection*.
  2. *Admin → Tag table*: create tags, or import them from Excel.
  3. *Screens → New screen*: build the screens.
  4. *Admin → Users*: create the operators. The *Viewer* role is read-only; for operators
     who may write set-points, create a role with *write values* under *Admin → Roles*.
* **Change the administrator password** under *Settings* if it was shared during the installation.

### 5.2 Start, stop, status

| Action | With auto-start (scheduled task) | Without auto-start |
|---|---|---|
| Start | `Start-ScheduledTask pyPLCHMI` | double-click `start-hmi.bat` (keep the window open) |
| Stop | `Stop-ScheduledTask pyPLCHMI` | close the window |
| Status | `Get-ScheduledTask pyPLCHMI` (State = Running) | – |
| Health | `Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing` → `{"status":"ok"}` | same |

You can also use the Task Scheduler GUI (`taskschd.msc`) → *pyPLCHMI*.

To change the port, edit `set HMI_PORT=` in `start-hmi.bat`, update the firewall rule and
restart. An upgrade keeps the port and threads written in `start-hmi.bat`.

### 5.3 Files and folders

| Path | Content |
|---|---|
| `instance\plchmi.sqlite` | The database. **Back it up.** |
| `instance\uploads\` | Image library, manuals and PDFs. **Back it up.** |
| `.env` | Settings and `SECRET_KEY`. **Back it up and keep it secret.** Without the same key, users must log in again; data is not lost. |
| `logs\hmi.log` | Server messages and errors (start-up, PLC errors). |
| `logs\install-*.log` | Installation logs. |
| `backups\` | Automatic backups made before each upgrade. |
| `venv\` | Python environment and libraries (`venv\Lib\site-packages`); recreated by every installation. |
| `start-hmi.bat` | Start command (port, threads). |

### 5.4 Backup

Stop the server for a consistent copy, then copy `instance\` and `.env` to the backup
location:

```powershell
Stop-ScheduledTask pyPLCHMI
Compress-Archive C:\pyPLCHMI\instance\*, C:\pyPLCHMI\.env D:\Backup\pyPLCHMI-$(Get-Date -f yyyyMMdd).zip
Start-ScheduledTask pyPLCHMI
```

To restore, stop the server, unpack the backup into `C:\pyPLCHMI` and start it again.

---

## 6. Upgrading to a new version

1. Get the new `pyPLCHMI_offline` folder and transfer it (see *3*).
2. Run its `install.bat` with the **same installation folder**. The installer finds
   the existing installation and:
   * stops the server and zips the data into `backups\instance-<date>.zip`;
   * replaces the program files and the `venv`, keeping `instance\`, `.env`, the logs
     and `start-hmi.bat`'s port;
   * upgrades the database schema (`flask db upgrade`) and starts the server again.
3. Check the version and the screens in the browser.

To go back to the previous version, run the previous package's `install.bat`, then
restore `backups\instance-<date>.zip` into `instance\`. A database that was already
upgraded can only be used by the new version or a later one.

### 6.1 Moving an existing (development) installation

A development installation uses `instance\dev.sqlite`; production uses
`instance\plchmi.sqlite`. To take over its configuration and history:

1. Stop both installations.
2. Copy `dev.sqlite` to `C:\pyPLCHMI\instance\plchmi.sqlite`, and copy the `uploads\`
   folder.
3. Start the new installation. The schema is upgraded automatically on the next
   `install.bat` run; or run it directly from `C:\pyPLCHMI`:
   `venv\Scripts\python.exe -m flask db upgrade`

---

## 7. Removal

Run `uninstall.bat` from the package as administrator. It:
* stops the server;
* removes the scheduled task and the firewall rules;
* deletes the program files.

It **keeps** `instance\`, `.env`, `logs\` and `backups\`. To delete everything, run
`uninstall.bat -RemoveData`. To remove Python itself, use *Settings → Apps* if nothing
else uses it.

---


## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `install.bat` closes at once or "running scripts is disabled" | Start it from an administrator command prompt: `install.bat`. The batch file always calls PowerShell with `-ExecutionPolicy Bypass`; a Group Policy *AllSigned* can still block it, so ask IT for an exception. |
| "The signature of the Python installer could not be confirmed" | Rare on fully offline machines with very old root certificates. Compare `SHA256SUMS.txt` (section 3), then run `install.ps1 -SkipSignatureCheck`, or install Python yourself first. |
| "Python 3.12 (64-bit) is not installed and the package has no python\…exe" | The `python\` folder was not copied. Copy the whole folder again, or install Python 3.12 64-bit yourself first. Other versions (3.11, 3.13…), a 32-bit Python or the Microsoft Store placeholder cannot be used. |
| "site-packages… is damaged (SHA-256 differs)" | The ZIP was changed or truncated during the transfer. Copy the folder again. |
| `pip check` reports a missing library | The site-packages ZIP does not match the application version. Use the `site-packages` and `app` folders of the same `pyPLCHMI_offline` package. |
| Server does not answer after the installation | Read `logs\hmi.log`. Common causes: the port is used by another program (`netstat -ano \| findstr :8000`), or `.env` was edited with a syntax error. |
| "SECRET_KEY" error in `hmi.log` | `.env` lost its `SECRET_KEY` line. Add one: `python -c "import secrets;print(secrets.token_hex(32))"`. |
| Other computers cannot open the HMI | Firewall rule missing (re-run the installer, or `New-NetFirewallRule …` as in `install.ps1`), wrong network profile (*Public* is not opened), or a different subnet or VLAN. |
| PLC stays offline | Ping the PLC from the server. For S7, check PUT/GET and rack/slot (S7-1200/1500: 0/1, S7-300: 0/2). For Modbus, check the unit ID. Messages are in `logs\hmi.log` and *PLCs → Test connection*. |
| BACnet devices not found | Use `-OpenBacnet`, set `BACNET_LOCAL_IP` to the card on the BACnet network, and only one BACnet application may use UDP 47808 on the server. |
| Operators see "Connection to the server lost" | The browser cannot reach the server: the server is stopped (check the task), or there is a network problem. The banner disappears by itself when the server answers again. |

---

## 9. Security checklist for OT networks

* Put the server in the OT network or OT DMZ and **never expose it to the Internet**.
* Open the web port only towards the operator networks (Firewall *Domain/Private*
  profiles, or a stricter `-RemoteAddress` on the rule).
* Use personal user accounts. Give the *write values* permission only to trained
  operators, and check *Admin → Audit log* regularly.
* Protect `.env` (it holds `SECRET_KEY`) and the backups with NTFS permissions:
  administrators and SYSTEM only.
* For HTTPS, put a reverse proxy in front (IIS ARR, nginx for Windows, Caddy) and set
  `SESSION_COOKIE_SECURE=1`.
* Safety functions belong in the PLC or safety system, never in the HMI.
