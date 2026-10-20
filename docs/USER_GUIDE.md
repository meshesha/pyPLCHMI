# pyPLCHMI

A web-based HMI for **Siemens S7 PLCs** (S7-300/400/1200/1500, LOGO! 8),
**Modbus TCP / RTU devices**, **BACnet/IP devices** and **web devices** that send
**JSON or XML over HTTP/HTTPS (also header-less HTTP/0.9) or WebSocket**, built with **Flask**,
**SQLAlchemy**, **python-snap7**, **PyModbus**, **pyModbusTCP** and **BAC0**.

* Role-based access: **Admin**, **Viewer**, **Guest** (plus custom roles), with a
  permission matrix editable from the web UI.
* Admins add PLCs (IP, rack, slot, port), test the connection, and build
  **screens** (HMI screens) from data points on any PLC.
* Data points are addressed in Siemens notation: `DB1.DBD4`, `DB1.DBX0.0`,
  `M10.5`, `MW20`, `IW64`, `Q0.1`, `T5`, `C3`, with types BOOL, BYTE, (U)SINT,
  CHAR, WORD, (U)INT, DWORD, (U)DINT, REAL, LREAL, TIME, DTL, STRING[n].
* Two screen types: **Grid** (automatic tiles) and **Plane** – a graphic
  (mimic) screen drawn in a drag-and-drop **SVG designer** with buildings,
  floors, rooms, walls, pipes, ~20 built-in equipment symbols (fan, pump,
  generator, valve, tank…), your own uploaded images (PNG/JPEG/GIF/WebP/SVG),
  free text, live PLC values, lamps, level bars and buttons that write to the PLC,
  ~5,200 **Tabler icons**, per-object **visibility by tag**, multi-selection, a
  right-click menu, and **SVG export/import** of screens.
* Live values are refreshed in the browser; operators with the `write_values`
  permission can write setpoints, strings and switch bits.
* **Short communication drops are bridged**: a per-PLC *communication timeout*
  keeps the last values on screen while the HMI reconnects; only longer outages
  mark the PLC offline and raise a **communication alarm**.
* A **tag table** (all tags of all PLCs, live values, add/edit/copy/delete, search,
  filters, sorting, pagination, **Excel export/import**; one tag can be used on several
  screens; **Dummy PLC** for tags without a device) and an **alarm system**: tag conditions
  (=, ≠, >, ≥, <, ≤) with Critical/High/Low severity, a pop-up alarm window
  with *Open Screen* / *Ack*, an *Events* window for every user and a
  searchable event history.
* **Tag history and trend charts**: save tag values every *N* seconds (per tag) with the
  full date and time, view and filter them by tag and time, export them to **Excel**, and
  draw **multi-tag trend charts** with a colour per tag (live mode, zoom, two axes) that can
  be saved as a **PNG image**.
* **Automations** like Home Assistant: *When* (tag change or limit, time, interval, alarm,
  PLC offline/online, server start) *And if* (tag values, time window, active alarms, And/Or/Not)
  *Then do* (write tags, wait, wait until, if/else, repeat, notify operators, run other
  automations) – visual editor or YAML, run history with step-by-step traces; PLC writes need an
  administrator's approval.
* **UI templates** switched by the administrator: Classic, High-performance (ISA-101),
  Alarm focus, Workspaces (sidebar, plant overview, Ctrl+K search) and Engineering
  (side-panel tag editing, bulk actions) – and developers can **upload their own**.
* **Web devices** (energy meters, gateways, weather stations, IoT sensors): an *Add device*
  wizard tests the device, shows the received JSON / XML and turns the keys you tick into
  tags – HTTP, HTTPS, header-less HTTP/0.9, WS and WSS, with no extra libraries.
* **Modbus TCP server (SCADA gateway)**: other SCADA systems (WinCC, ControlMaestro, Ignition,
  Kepware…) read the values of the tags you pick over Modbus TCP – read only, or writable per
  tag (audited) – automatic addresses, INT16/32, FLOAT32/64, bits and strings, heartbeat and quality bits, client
  allow-list, and an Excel address list for the SCADA engineer.
* A built-in **PLC simulator** (`PLC_SIMULATION=1`) lets you develop and demo
  without hardware.
* Bootstrap is bundled locally, so the HMI works on networks without internet.

---

## 1. Quick start (Windows, PowerShell)

```powershell
git clone https://github.com/meshesha/pyPLCHMI.git
cd pyPLCHMI
py -3 -m venv venv
.\venv\Scripts\Activate.ps1          # cmd.exe: venv\Scripts\activate.bat
pip install -r requirements-dev.txt

copy .env.example .env               # then edit SECRET_KEY / PLC_SIMULATION

flask db upgrade                     # create the database (instance\dev.sqlite)
flask seed                           # create roles + permissions
flask create-admin                   # prompts for username / password
flask demo                           # optional: demo PLC + screen
flask library animations             # optional: built-in animated GIFs (Images → Animations)
flask run                            # http://127.0.0.1:5000
```

Linux/macOS: `python3 -m venv venv && source venv/bin/activate`, the rest is identical.

> If PowerShell refuses to run `Activate.ps1`, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## 2. Project structure

```
pyPLCHMI/
├── app/
│   ├── __init__.py          # create_app() – application factory
│   ├── extensions.py        # db, migrate, bcrypt, login_manager, csrf
│   ├── cli.py               # flask seed / create-admin / demo
│   ├── models/              # User, Role, Permission, Setting, PLC, Dashboard (= screen), DataPoint (= tag), ScreenTag, Asset, Alarm, AlarmEvent, TagSample, TrendChart
│   ├── plc/                 # PLC communication (S7, Modbus, BACnet, Dummy, web devices)
│   │   ├── datatypes.py     #   S7 type registry, encode/decode via snap7.util
│   │   ├── addressing.py    #   "DB1.DBD4:REAL" parser/formatter
│   │   ├── driver.py        #   connections, simulator, PLCConnectionManager
│   │   ├── modbus.py        #   Modbus addresses, codec, pymodbus/pyModbusTCP adapters
│   │   ├── bacnet.py        #   BACnet addresses, BAC0 stack thread, adapters
│   │   ├── dummy.py         #   Dummy PLC: internal values, tags without address
│   │   ├── web.py           #   web devices: key-path addresses, WebConnection, wizard probe
│   │   ├── web_client.py    #   HTTP/HTTPS (also header-less HTTP/0.9) and WebSocket clients
│   │   ├── web_data.py      #   JSON / XML: keys, lookup, type conversion
│   │   ├── errors.py        #   common error types of tag-based drivers
│   │   ├── protocols.py     #   protocol registry (s7, modbus_tcp, modbus_rtu, bacnet_ip)
│   │   └── service.py       #   screen read/write helpers
│   ├── auth/                # blueprint: login, logout, Settings page (password, theme, language)
│   ├── main/                # blueprint: home (screen list)
│   ├── admin/               # blueprint: users, roles/permissions, PLCs, web_devices.py (Add device wizard)
│   ├── dashboards/          # blueprint: HMI screens, data point editor, designer
│   │   ├── layout.py        #   graphic layout schema + server-side validation
│   │   ├── icons.py         #   Tabler icon index/shapes (pytablericons SVG files)
│   │   └── svg_import.py    #   Import SVG: restore exported screens / drawings as background
│   ├── tags/                # blueprint: tag table (admin), excel.py = Excel export/import
│   ├── alarms/              # blueprint: alarm definitions, event history
│   │   └── engine.py        #   alarm scanner (background thread) + evaluation
│   ├── documents/           # blueprint: information library (/library), PLC files, SHA-1 de-duplication
│   ├── audit/               # audit log: recorder.py (SQLAlchemy listener + explicit entries), page (/admin/audit)
│   ├── history/             # blueprint: tag history table, Excel export, trend charts, series API
│   │   ├── logger.py        #   history logger (background thread): samples, retention clean-up
│   │   └── data.py          #   filtered queries, chart series (min/max reduction), Excel writer
│   ├── api/                 # blueprint: JSON (live values, writes, layouts, tags, alarm events)
│   ├── assets/              # blueprint: image library (folders, upload, move, serve, delete)
│   │   └── folders.py       #   folder paths, store_asset (dedupe), built-in animations import
│   ├── library/animations/  # built-in animated GIFs (<Category>/*.gif + manifest.json)
│   ├── i18n/                # languages: catalog.py (_(), language choice), forms.py, routes.py (switcher, admin)
│   │   └── languages/       #   built-in language files: en.json (all texts), he.json (Hebrew)
│   ├── modbus_server/       # Modbus TCP server: encoding.py, layout.py (addresses), server.py, admin page
│   ├── automations/         # automations: schema.py (blocks, validation), engine.py (triggers, runs), documents.py (YAML)
│   ├── ui_templates/        # UI templates: registry.py (manifest, install/export), nav.py, routes.py
│   │   └── builtin/         #   classic, high-performance, alarm-focus, workspace, engineering
│   ├── utils/               # permission decorators, safe redirects, table helper, xlsx helper, time zones
│   ├── templates/           # Jinja2 templates (Bootstrap 5, dark theme)
│   └── static/              # hmi.css, hmi-dialogs.js (T(), SweetAlert2), hmi-*.js, Bootstrap (+RTL), SweetAlert2, Chart.js
├── migrations/              # Flask-Migrate / Alembic scripts
├── tests/                   # pytest suite
├── tools/make_animations.py # generator of the built-in animated GIFs (Pillow)
├── tools/i18n_extract.py    # collects every UI text into app/i18n/languages/en.json
├── Dockerfile / docker-compose.yml / docker/entrypoint.sh   # container image (see docs/DOCKER.md)
├── config.py                # Development / Testing / Production config
├── wsgi.py                  # entry point (FLASK_APP)
├── requirements.txt / requirements-dev.txt
└── .env.example / .flaskenv
```

See [docs/ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together.

## 3. Configuration

Environment variables (put them in `.env`):

| Variable | Default | Meaning |
|---|---|---|
| `FLASK_CONFIG` | `development` | `development`, `testing` or `production` |
| `SECRET_KEY` | dev value | **Required in production** – long random string |
| `DATABASE_URL` | `instance/plchmi.sqlite` | Production DB URL (SQLite, PostgreSQL…) |
| `DEV_DATABASE_URL` | `instance/dev.sqlite` | Development DB URL |
| `PLC_SIMULATION` | `0` | `1` = use the in-memory simulator instead of real PLCs |
| `PLC_RECONNECT_DELAY` | `5` | Longest wait between reconnection attempts to an unreachable PLC (first retries: 0.5, 1, 2, 4 s) |
| `PLC_MAX_READ_SPAN` | `200` | Max bytes per batched `read_area` call |
| `PLC_MAX_READ_GAP` | `32` | Max unused gap bridged when batching tags |
| `SESSION_COOKIE_SECURE` | `0` | Set `1` when served over HTTPS |
| `BACNET_LOCAL_IP` / `_PORT` / `BACNET_DEVICE_ID` | auto / 47808 / 3056 | Local BACnet stack (see 5c) |
| `UPLOAD_FOLDER` | `instance/uploads` | Where uploaded images are stored (back it up with the DB) |
| `UI_TEMPLATES_FOLDER` | `instance/ui_templates` | Where uploaded UI templates are stored |
| `AUTOMATIONS` | `1` | `0` = no automation engine thread (triggers are not watched) |
| `AUTOMATION_TICK` | `1` | Seconds between two checks of the automation triggers |
| `ASSET_MAX_BYTES` | `5242880` | Maximum size of one uploaded image |
| `ALARM_SCANNER` | `1` | `0` = no background alarm thread (alarms are then evaluated when a browser polls) |
| `ALARM_SCAN_INTERVAL` | `2` | Seconds between alarm evaluations |
| `HISTORY_LOGGER` | `1` | `0` = no background history logger (no tag samples are saved) |
| `HISTORY_TICK` | `1` | Seconds between two checks for tags that are due for a sample |
| `HISTORY_RETENTION_DAYS` | `90` | Default days samples are kept (changed in *Admin → System settings*; 0 = forever) |
| `DISPLAY_TIMEZONE` | server zone | Time zone for history dates when the browser has not reported its own (e.g. `Asia/Jerusalem`) |

Generate a secret key: `python -c "import secrets; print(secrets.token_hex(32))"`.

## 4. Roles and permissions

| Permission | Admin | Operator | Viewer | Guest |
|---|:-:|:-:|:-:|:-:|
| `manage_users` – create/edit/delete users | ✔ | | | |
| `manage_roles` – edit the permission matrix | ✔ | | | |
| `manage_plcs` – add/edit/delete/test PLCs | ✔ | | | |
| `manage_screens` – build screens & their tags | ✔ | | | |
| `view_screens` – open every screen | ✔ | ✔ | ✔ | |
| `view_public_screens` – open screens marked *public* | ✔ | ✔ | ✔ | ✔ |
| `write_values` – write to writable tags | ✔ | ✔ | | |
| `view_history` – tag history, trend charts, history Excel export | ✔ | ✔ | ✔ | |
| `view_audit` – audit log (who changed what and when) | ✔ | | | |
| `manage_automations` – create, edit, run and delete automations | ✔ | | | |

The four roles are **built-in**: they cannot be deleted, but their permissions can be
changed at **Admin → Roles & permissions**, and custom roles can be added. Existing
installations get the *Operator* role with the database upgrade (`flask db upgrade`).
Safeguards: the Admin role always keeps `manage_roles`, and the last active administrator
cannot be deleted, disabled or demoted. `flask seed --reset` restores the default
permissions.

**Who may write a tag.** Under *Operators may write this value* in the tag form, choose:

* **Everyone**: every user whose role has `write_values` (the default).
* **Based on user roles**: tick one or more roles. Only users of those roles may write the
  tag. This also applies to Admin, so tick *Admin* if administrators should write it too.

The rule is checked by the server on every write. Screens show the input field, switch or
button only to users who may write the tag; everyone else sees the value read-only. The
Details window (right-click on a value) shows *Who may write* and *You may write*. In the
Tag table a 🔒 badge lists the allowed roles, and the Excel export/import has a
**Write roles** column: empty means everyone, otherwise role names separated by commas.
Changes are recorded in the audit log.

## 5. Connecting real PLCs

1. **Admin → PLCs → Add PLC**: name, IP, rack/slot (S7-300: 0/2, S7-1200/1500: 0/1), port 102.
2. Click **Test connection** (plug icon) – shows CPU type and RUN/STOP state when the CPU allows it.
3. For **S7-1200/1500** in TIA Portal:
   * CPU → Protection & Security → Connection mechanisms → **Permit access with PUT/GET**.
   * Every DB you read → Properties → Attributes → untick **Optimized block access**,
     then use the offsets shown in the DB's *Offset* column.
4. Make sure TCP port 102 is reachable from the server (firewalls/VLANs).

### Address syntax

| Address | Meaning | Default type |
|---|---|---|
| `DB1.DBX0.0` | bit 0.0 of DB1 | BOOL |
| `DB1.DBB2` / `DB1.DBW4` / `DB1.DBD6` | byte / word / dword in DB1 | BYTE / WORD / DWORD |
| `M10.5`, `MB10`, `MW10`, `MD10` | memory (merker) | BOOL / BYTE / WORD / DWORD |
| `I0.0`, `IB0`, `IW64` (German `E…`) | process inputs | |
| `Q0.1`, `QB0`, `QW2` (German `A…`) | process outputs | |
| `T5`, `C3` (German `Z3`) | timers / counters (S7-300/400) | WORD |

Add `:TYPE` to override the type: `DB1.DBD4:REAL`, `MW20:INT`,
`DB2.DBB10:STRING[20]`, `DB3.DBB0:LREAL`. The size letter must match the type
(a `REAL` cannot live at `DBW`). The data point editor's **Test read** button
reads the address once so you can verify it before saving.

**Scaling:** displayed value = raw × *scale* + *offset* (writes apply the
inverse), e.g. an INT holding tenths of a degree → scale 0.1.

## 5b. Modbus devices (TCP and RTU)

*Admin → PLCs → Add PLC / device → Protocol*:

| Protocol | Library | Settings |
|---|---|---|
| **Modbus TCP** | **PyModbus** or **pyModbusTCP** (selectable per device) | IP / host, port (502), unit id, timeout |
| **Modbus RTU** | PyModbus over a serial port (USB-RS485 adapter) | COM port, baud rate, parity, data/stop bits, unit (slave) id |

Both: **32/64-bit value order** – ABCD (standard), CDAB (word swapped), BADC,
DCBA – for REAL/DINT/LREAL values that span several registers. If a REAL shows
nonsense, try CDAB. *Test connection* sends one request (read HR0); even an
"illegal address" answer proves the device is alive.

**Tag addresses** (the address help on the tag form switches automatically to
the selected device's protocol):

| Address | Meaning |
|---|---|
| `HR100` | holding register 100 (read/write) – default type UINT |
| `IR5` | input register 5 (read only) |
| `C12` / `CO12` | coil 12 – BOOL, read/write |
| `DI3` | discrete input 3 – BOOL, read only |
| `HR10.3` | bit 3 of holding register 10 – BOOL (write = read-modify-write) |
| `40001`, `400001`, `30010`, `10001`, `00001` | Modicon numbering (1-based): 40001 = HR0, 30010 = IR9 |
| `HR200:REAL`, `HR10:DINT`, `HR20:LREAL` | 2-register (REAL/DINT/UDINT/DWORD) and 4-register (LREAL) values |
| `HR300:STRING[16]` | 16 characters, 2 per register |

`HR`/`IR`/`C`/`DI` numbers are the **0-based protocol addresses** sent on the
wire; many manuals list 1-based numbers instead – use the Modicon form (40001…)
for those. Supported types: BOOL, INT, UINT, WORD, DINT, UDINT, DWORD, REAL,
LREAL, STRING. Input registers and discrete inputs cannot be marked writable.
Neighbouring tags are read together (up to 125 registers / 2000 bits per
request); an illegal address only fails its own tag.

With `PLC_SIMULATION=1` Modbus devices are simulated too.

## 5c. BACnet/IP devices (BAC0)

*Admin → PLCs → Add PLC / device → Protocol: BACnet/IP*: IP address, UDP port
(47808), **device instance** and the default **write priority** (8 = manual
operator). **Who-Is** on the form finds devices on the network (or asks only the
IP you typed) – click a result to fill in address and instance. After saving,
the **Browse objects** button (list icon) in the PLC list shows every object of
the device with its name, present value, units and the address to use.

The HMI runs **one local BACnet stack** (BAC0, asyncio) in a background thread.
Configure it in `.env` if needed:

| Variable | Default | Meaning |
|---|---|---|
| `BACNET_LOCAL_IP` | auto-detect | network card as `ip/mask`, e.g. `192.168.1.20/24` |
| `BACNET_LOCAL_PORT` | 47808 | local UDP port (must be free – no other BACnet tool on the same PC/port) |
| `BACNET_DEVICE_ID` | 3056 | this HMI's own device instance (must be unique on the network) |

Allow UDP 47808 through the Windows firewall for the Python process.

**Tag addresses** (the data-type drop-down is ignored – the object type decides):

| Address | Meaning |
|---|---|
| `AI1`, `AO2`, `AV3` | analog input / output / value – REAL |
| `BI1`, `BO2`, `BV3` | binary – BOOL (active = ON) |
| `MI1`, `MO2`, `MV3` (or `MSV3`) | multi-state – UINT (state number) |
| `CS4`, `IV5`, `PI6` | character-string / integer / positive-integer value |
| `analogValue:3`, `analog-value,3` | long forms are accepted too |
| `AI1.units`, `AV3.outOfService:BOOL` | any other property (read only) |
| `AV5@10` | write with priority 10 instead of the device default |
| `MV3:INT` | override the value type |

Outputs and values (AO/AV/BO/BV/MO/MV/CS/IV/PI) can be written – only their
presentValue; inputs and other properties are read only. Tags of one device are
read with a single ReadPropertyMultiple when possible; an unknown object fails
only its own tag. An offline device is given up after its timeout (default 3 s)
so screens stay responsive. With `PLC_SIMULATION=1` BACnet devices are simulated.

## 5d. Communication timeouts and short interruptions

Every PLC/device has two timeouts (*Admin → PLCs → edit → Communication monitoring*):

| Setting | Default | Meaning |
|---|---|---|
| **Request timeout** | 3 s | How long to wait for one answer before the request counts as failed (all protocols). |
| **Communication timeout** | 10 s | How long communication may fail before the PLC is reported as **disconnected**. `0` = at once. |

While communication fails for less than the communication timeout, the PLC is in the
**reconnecting** state:

* screens keep showing each tag's **last good value** – the tile gets an amber edge
  and a blinking amber dot, image screens show values in amber – and the PLC badge
  reads *reconnecting…* (amber) instead of *offline*;
* alarm conditions on those tags do not change (no false alarms or clears from a blip).

When the communication timeout expires, the PLC turns **offline**: tags become bad
(red), the badge turns red and the PLC's **communication alarm** fires.

**Reconnecting:** a connection that breaks is re-opened immediately, within the same
request (most resets – e.g. an S7-1500 closing a connection – are repaired this way
without anyone noticing). If the PLC still does not answer, the next attempts follow
after 0.5, 1, 2, 4 … seconds, at most `PLC_RECONNECT_DELAY` (5 s).

**Communication alarm:** *Raise an alarm when communication is lost* (on by default,
also for PLCs that existed before this update) creates an alarm named
*“&lt;PLC&gt; communication lost”* with the chosen severity. It appears in the alarm
window, the Events list and the Event history like any other alarm, and closes
automatically (*Automatic*) when communication is restored. Its name, description and
screen can be edited under *Admin → Alarms* (Trigger: *PLC communication lost*), where
you can also add one for a PLC that has none. The alarm engine contacts every watched
PLC on each scan, so the alarm also works when no screen is open.

The PLC list shows the state (*connected*, *reconnecting (n s)*, *offline (n s)*) and
each PLC's communication timeout. Every interruption and recovery is written to the
server log (`PLC Line1: communication error: …`, `… communication restored after 1.6 s`),
which helps to find the cause of recurring drops.

> **python-snap7 and timeouts.** python-snap7 has no “grace period” setting –
> that part is handled by the HMI as described above. The request timeout is applied
> to snap7 as far as the installed version allows it: with python-snap7 1.x/2.x
> (C library) through `set_param(PingTimeout/SendTimeout/RecvTimeout)`; with
> python-snap7 3.x (pure Python) those parameters are accepted but ignored, so the HMI
> sets the timeout on the client's socket after connecting (the connection attempt
> itself uses the library's fixed 5 s).

## 5e. Dummy PLC (no device)

*Admin → PLCs → Add PLC / device → Protocol: Dummy (no device – internal values)* creates
a PLC that needs no IP address. Its tags need **no address**: the address field is hidden
and the HMI gives each tag an internal slot (shown as `D1`, `D2`…). Choose the data type
(BOOL, integers, REAL, LREAL, STRING, CHAR).

* Values start at 0 / OFF / empty and can be written from screens like any writable tag.
* Every dummy tag is one of two **kinds** (*Dummy tag kind* in the tag form, in the
  designer's *New tag* dialog and as the *Dummy kind* column in Excel):

  | Kind | Value | Typical use |
  |---|---|---|
  | **General** (default) | one value for everybody – every browser and computer shows the same value; stored in the database (table `dummy_values`), so it survives a server restart | operator inputs, modes, values shared by all panels |
  | **Local** | each **browser window** has its own value, kept in the window's `sessionStorage`: it survives a reload and moving between screens in that window, other windows/tabs/computers are not affected, and it is gone when the window is closed | show/hide details, select a view, toggle a panel on one operator station only |

  Local tags start at 0 / OFF / empty in every new window. Changing them only affects the
  operator's own window, so **every user who may open the screen can set them** (also
  Guests without the *write values* permission) – the tag only has to be *writable*. The
  server never stores or reads their value: they **cannot be used by alarms** (the alarm form
  refuses them, and a tag used by alarms cannot be made local), and the tag table shows the
  value of the window it is opened in. Ideal partner for *Visibility → Depending on a tag*:
  a *toggle* button on a local BOOL tag shows/hides a group of objects for that operator only.
* Use it to build screens before the PLC exists, or for values no PLC needs.
* To connect a dummy tag to a real PLC later, edit the tag, choose the PLC and enter the
  address (the address is required again). Its screens and alarms stay attached.
* A dummy PLC is always *connected* and gets no communication alarm.

## 5f. Web devices – HTTP / HTTPS / WebSocket with JSON or XML

Many devices publish their values as a JSON or XML document: energy meters, weather
stations, IoT gateways, UPS cards, small controllers. *Admin → PLCs → **Add device*** opens
a wizard:

1. **Device** – name, protocol (*HTTP / HTTPS* or *WebSocket WS / WSS*), the **address (URL)**
   (`http://192.168.1.50/status.json`, `https://…`, `ws://192.168.1.50:8080/live`, `wss://…`),
   the **data format** (JSON or XML), the **request interval** and the **timeout**.
   *Advanced*: the HTTP **request mode**, switching off the **certificate check** for
   self-signed HTTPS/WSS certificates, and a **message sent after connecting** (WebSocket
   devices that send data only on request, e.g. `{"subscribe": "all"}`).
   `user:password@` in the URL sends HTTP *Basic* authentication.
   For HTTP, the **HTTP method** is *GET* or *POST*: devices that expect parameters in the
   request get a **request body** (`id=3&page=1`, `{"cmd": "read", "id": 3}`, an XML command…)
   and a **content type** (`application/x-www-form-urlencoded` by default, `application/json`,
   `application/xml`, `text/plain` or any other). **Extra headers** (*Advanced*, one
   `Name: value` per line, e.g. `X-API-Key: 1234` or `Authorization: Bearer …`) are sent with
   every HTTP request and with the WebSocket handshake.
2. **Test** – one exchange with the device; the received data is shown formatted, or the
   exact error (no answer, HTTP 404, invalid JSON, TLS certificate…).
3. **Process** – a table with one row per value found: a checkbox, the **key**, a suggested
   **tag name** (`<device>.<key>`, already valid and unique under the tag naming rules,
   editable, checked while typing), the **data type** (guessed from the value: BOOL, DINT,
   LREAL or STRING – can be changed) and the **value**.
4. **Save Selected** / **Save All** – creates the device (with its communication alarm) and
   one read-only tag per row in one step. If a name or key is invalid, nothing is saved and
   the row shows why.

For a saved device, the table icon in the PLC list (*Test and add tags*) opens the same
wizard: keys that already have a tag are marked, the others can be added. The device's
settings are changed with *Edit* like any PLC.

**HTTP.** Every request interval the HMI sends the `GET` or `POST` request (only when someone needs the values:
screens, alarms, history, automations). It accepts normal HTTP/1.x answers
(`Content-Length`, `chunked`, or "until the connection closes") **and answers without any
HTTP headers (HTTP/0.9)** – which `requests`/`urllib` refuse. For very old devices that do
not understand a modern request, choose the request mode *HTTP/0.9* (only `GET /path` is
sent). A device that sends its data but keeps the connection open is accepted too.

**WebSocket.** The HMI keeps the connection open, uses the latest message and keeps the
values of keys that are missing from a message (devices that send different values in
different messages). It answers pings, sends its own ping every 10 s and reports the device
offline when nothing comes back.

**Keys (tag addresses):**

| Key | Meaning |
|---|---|
| `meter.power` | JSON: object keys separated by dots |
| `meter.phases[0]` | JSON: list item (from 0) |
| `data["flow rate"]` | JSON: key with spaces or symbols |
| `$` | JSON: the whole document when it is a single value |
| `status/sensor[2]/temp` | XML: elements from the root; `[n]` from 1, no index = the first |
| `status/sensor/@id` | XML: attribute (XML name spaces are ignored) |

Text values are converted to the tag's type (`"21.5"` → REAL, `"on"`/`"true"`/`1` → BOOL);
`null`, a missing key or a value out of range makes only that tag bad. Web device tags are
**read only**. Communication timeout, communication alarm, history, alarms and automations
work as for every PLC. Everything uses the Python standard library – nothing to add to an
offline installation.

## 5g. Modbus TCP server – publish tags to other SCADA systems

*Admin → **Modbus TCP server*** turns pyPLCHMI into a Modbus TCP **server (slave)**: other
SCADA / HMI systems – Siemens WinCC, ControlMaestro, Ignition, Kepware, another PLC – connect
as Modbus TCP clients and read the values of the tags you publish – and, for the tags you
allow, write them (e.g. setpoints).

**Settings:** *Enable*, the IP address to listen on (`0.0.0.0` = every network card, or the
card of the OT network), the **TCP port** (502 is the standard – on Linux ports below 1024
need administrator rights, use e.g. 5020), the **unit id** (0 = answer every unit id), the
**value order** of 32/64-bit values (ABCD big endian, CDAB word swapped, BADC, DCBA – set the
same in the SCADA), the **update interval**, what tags **without a valid value** send (keep
the last good value, or 0), the **allowed clients** (IP addresses / networks; others are
disconnected at once), an optional **heartbeat register** (a counter that grows on every
update – alarm in the SCADA when it stops) and optional **quality bits** (one bit per tag,
ON = valid value).

**Published tags:** *Add tags* opens a picker; every tag gets the next free address and a
suggested type, both editable in the table (overlaps and reserved addresses are refused):

| Modbus type | Registers | Default for |
|---|---|---|
| `BIT` | 1 coil / discrete input | BOOL |
| `BOOL16` | 1 (0/1) | – (for SCADA drivers that read only registers) |
| `INT16` / `UINT16` | 1 | SINT, INT / BYTE, USINT, WORD, UINT |
| `INT32` / `UINT32` | 2 | DINT / DWORD, UDINT |
| `FLOAT32` / `FLOAT64` | 2 / 4 | REAL, LREAL and tags with scale/offset |
| `STRING` | 1 per 2 characters | STRING, CHAR |

The **engineering value** (after the tag's scale and offset) is published; an extra *Modbus
scale* multiplies it before encoding (e.g. 10 → 21.5 °C is sent as the INT16 215). Integers are
rounded and clamped to the type's range.

**Reading it from the SCADA:** registers with function 3 (*holding registers*, `4xxxx`) **or**
4 (*input registers*, `3xxxx`) – both return the same data; bits with function 1 (*coils*,
`0xxxx`) **or** 2 (*discrete inputs*, `1xxxx`). Addresses in the table are 0-based protocol
addresses, with the classic 1-based reference next to them (`40001` = register 0). Unused
addresses read as 0, so the SCADA may read whole blocks. **Address list (Excel / CSV)** gives
the SCADA engineer every tag with its address, type, references, scale and value order, plus
the heartbeat and quality bits. The status panel shows the clients, requests, writes (done /
refused / failed), the last writes and refused connections. Open the TCP port in the server's
firewall.

**Writes from the SCADA:** every published tag is **read only** for the other systems until you
switch on **Allow write** in its row – possible only for tags that are writable in pyPLCHMI
(*Tag table → Edit → Writable*). Then the SCADA writes registers with function 6 / 16 and bits
with function 5 / 15. The value goes through the same path as an operator's write: the Modbus
scale is divided out, the tag's own scale / offset and range checks apply, the PLC is written
and the value read back. Every write is in the **audit log** as user `Modbus TCP <client IP>`.
Answers to the SCADA:

| Situation | Modbus answer |
|---|---|
| Write accepted and written to the PLC | normal response |
| Address of a read-only tag, an unused address, or only part of a 32/64-bit value or STRING (write all its registers in one request) | exception 02 *illegal data address* – nothing is written |
| Value cannot be used (NaN, out of the tag's range…) | exception 03 *illegal data value* |
| The PLC refuses the write or does not answer | exception 04 *server device failure* |

A request is checked as a whole before anything is written. Function 23 (read/write multiple
registers) is not supported (exception 01). The *Writable* tag setting stays the master switch:
making a tag read only in the tag table stops Modbus writes at once. The tag's role limits
(*write access by role*) apply to operators in pyPLCHMI; for the SCADA, the *Allow write*
switch is the permission – keep the **allowed clients** list filled when writes are on.

The server runs inside the pyPLCHMI process (one server per installation; `MODBUS_SERVER=0`
switches the background thread off). It uses pyModbusTCP, already part of the installation.

## 6. Plane (graphic) screens

Create one with **Screens → New screen → Type: Plane**; the designer opens.

**Palette (left)** – drag an item onto the canvas, or click it to drop it in the middle:

| Section | Items |
|---|---|
| Building | Building, Floor / zone (dashed), Room, Round zone, **Wall (draw)**, Text, Door, Window, Stairs |
| Piping | **Pipe (draw)**, Pump, Valve, Tank (level), Flow arrow |
| HVAC / Electrical / Sensors | Fan, Boiler, Radiator, AC unit, Motor, Generator, Light, Breaker, Transformer, Solar panel, Battery (level), Temperature sensor, Alarm bell |
| PLC controls | PLC value, Lamp, Button, Level bar |
| PLC tags | the screen's tags – drag one onto the canvas to get a value box (or a lamp for BOOL); **＋ New tag** creates a tag without leaving the designer |
| Images | your uploaded images – **Upload image**, or simply drop image files from Windows Explorer onto the canvas |

**Walls and pipes**: click *Wall (draw)* / *Pipe (draw)*, click the corner points on
the canvas (Shift = straight horizontal/vertical), double-click or Enter to finish.
Double-click a selected pipe to add a bend, Alt+click a bend to remove it.

**Binding to the PLC** (properties panel, right):

| Element | Behaviour of the bound tag |
|---|---|
| Room / zone / wall | fill (or line) colour switches between *ON* and *OFF* colours – e.g. alarm or occupancy |
| Pipe | animated flow while the tag is ON |
| Symbol | ON → rotor spins (fan, pump, AC), flame/glow/blink appears, accent colour changes; Tank/Battery show a numeric level between *min* and *max* |
| PLC value | formatted value with unit; optional low/high alarm limits turn it red |
| Lamp | ON/OFF colour, optional blinking |
| Level bar | vertical/horizontal bar between min and max |
| Button | `toggle` (invert BOOL), `set` (write a fixed value), `input` (operator types a value), `momentary` (TRUE while pressed, FALSE on release), `link` |

Any element can also **open another screen** when clicked (*Click opens screen*) –
build a site map → building → floor → room drill-down that way. A floor plan can
be set as the canvas **background image**.

Editing: move by dragging, resize with the corner handles (Shift keeps
proportions), Del, Ctrl+Z/Ctrl+Y, Ctrl+C/Ctrl+X/Ctrl+V, Ctrl+D, Ctrl+A, arrows
(Shift ×10), R rotate 90°, `[` `]` order, Alt disables snapping, Ctrl+wheel zoom,
Ctrl+S save. **Live** shows real PLC values while you edit.

* **Several objects at once** – Ctrl+click or Shift+click objects (or rows in the
  *Layers* list) to add/remove them, Ctrl+A selects everything. Dragging, arrows,
  Delete, copy, duplicate, order, rotate, lock and the visibility settings then apply
  to all selected objects.
* **Right-click menu** – on an object: Cut, Copy, Paste here, Duplicate, Delete,
  Bring to front / forward, Send backward / to back, Rotate 90°, Lock/Unlock,
  Hide at run time / Always show, Visibility by tag…, Edit tag settings, Select all.
  On an empty spot: Paste here, Select all, Canvas settings, Zoom to fit, Export SVG.
* **Copy between screens** – the clipboard is kept in the browser, so objects copied
  in one screen's designer can be pasted into another screen's designer (also in
  another tab). Tags used by pasted objects are added to that screen automatically.

### Visibility

Every object has a **Visibility at run time** setting (properties panel, bottom):

| Show | Behaviour |
|---|---|
| Always | default |
| Never (hidden) | not shown on the screen (useful for notes or work in progress) |
| Depending on a tag | shown while the tag meets the condition: *tag is ON / not 0*, *tag is OFF / 0*, or `=` `≠` `>` `≥` `<` `≤` a value |

Objects bound to the same tag appear and disappear **together** – e.g. select a group of
objects, set *Depending on a tag* once for all of them, and add a *Button* (action
*toggle*) on that tag to show/hide the group. A Dummy PLC BOOL tag is ideal for this.
While the tag cannot be read the objects stay hidden. In the designer hidden objects
are only dimmed (so you can still select them); the Layers list marks them with an eye.

### Icons (Tabler / pytablericons)

The palette's **Icons** section searches the ~5,200 Tabler icons (outline and filled)
shipped with the `pytablericons` package – by name, category or keyword (fan, pump,
bolt, temperature, droplet…). Click or drag an icon onto the canvas. Icon properties:
colour, line width (outline icons), optional label, and an optional **tag** that switches
the colour between *Colour when ON* / *when OFF* and can blink while ON. Icons are vector
shapes, so they stay sharp at any size. The HMI reads the SVG files of the package
directly (`pip install pytablericons`, already in `requirements.txt`); without the package
the section shows an installation hint.

### SVG export and import

* **Export SVG** (designer toolbar, or right-click on the canvas) downloads the current
  drawing – including unsaved changes – as a standalone `.svg` file: images are embedded,
  objects hidden at run time are left out, and the complete screen (layout, descriptions of
  the tags used, images, names of linked screens) is stored inside the file.
* **Import SVG** (Screens page, several files at once) creates a new Plane screen per file:
  - a file from *Export SVG* becomes the same, fully editable screen again (also on another
    pyPLCHMI installation): tags are matched by label + PLC name; missing tags are created
    when their PLC exists, otherwise their bindings are removed (reported); identical images
    are reused; links to other screens are matched by screen name;
  - any other SVG drawing (Inkscape, CAD, Visio…) is added to the image library and used as
    the **background** of the new screen, with the canvas sized to the drawing – place tags,
    symbols and buttons on top.

  Existing screens are never changed. Files with scripts, event handlers, DOCTYPE or entity
  declarations are refused.

Buttons only write when the user has `write_values` **and** the tag is marked
*writable*; for others the buttons are greyed out. Uploads are checked by content;
SVGs with scripts, event handlers or entities are refused, and files are served
with a sandboxing Content-Security-Policy.

### Image library and folders (*Library → Images* in the menu)

The library is organised in **folders** (any depth, e.g. `Plant/Boiler room`):

* the left-hand **folder tree** shows every folder with its number of images (including
  sub-folders); click a folder to open it, the breadcrumb leads back up;
* **New folder** creates a sub-folder of the open folder (`a/b` creates two levels);
* **Upload** stores files in the open folder; **Rename / move folder** takes a full path,
  so `HVAC` → `Plant/HVAC` moves the folder with its sub-folders and images (an existing
  folder with that path is merged); **Delete folder** is offered for folders without images;
* select images (checkboxes, *Select all*) and **Move to** another folder, or simply
  **drag** an image (or the selected ones) onto a folder in the tree, a folder tile or the
  breadcrumb; the search box finds images by name in all folders;
* images in use by a screen cannot be deleted (*used by n* shows which screens).

In the screen designer the **Images** palette has a folder selector (the choice is
remembered); uploads from the designer go to the selected folder.

### Built-in animations

**Add built-in animations** (Image library) or `flask library animations` adds 38 animated
GIFs (128 × 128 px, transparent background) to the folders **Animations/&lt;category&gt;**:

| Folder | Animations |
|---|---|
| Animations/HVAC | axial fan, centrifugal blower, cooling tower, chiller, air damper, air duct flow, heater / radiator, thermometer |
| Animations/Electrical | high voltage, power flow, transformer, circuit breaker, battery charging / discharging, electric motor, solar panel, wind turbine, energy meter |
| Animations/Water | centrifugal pump, water pipe flow, gate valve, tank filling / draining, water drop, sprinkler |
| Animations/Generators | diesel generator set, alternator, generator AC output |
| Animations/Fuel | fuel tank, fuel dispenser, burner flame, fuel line flow, gas cylinder |
| Animations/Status | warning beacon, warning triangle, running, alarm bell, stopped / fault |

The animations are original drawings made for pyPLCHMI (no third-party copyright) by
`tools/make_animations.py`; run `python tools/make_animations.py --size 160` to regenerate
them in another size, or add your own drawing functions there. Importing again only adds
animations that are not yet in the library (identical files are recognised even after
renaming or moving them). Place them like any image; combine an animation with
*Visibility → Depending on a tag* (e.g. show the running pump only while the pump runs and a
static picture otherwise). `flask library folders` lists the folders from the command line.

## 7. Screen list, screen access, home screens and public display

**Screen list** (*Screens* in the top bar): two views, switched with the buttons on the
right – **cards** (default) or a **table** with the columns Name, Type, Description,
PLCs, Tags, Access and Created, plus *Open*, *Designer* and *Settings* buttons for
editors. Both views share the **search** (name, description, URL name, PLC names) and
the **filters** (type, public / login required, PLC); the table adds **sorting** by
clicking a column header and **pages** (10/25/50/100 rows). The chosen view is
remembered by the browser; search, sort and page are in the URL, so a filtered list can
be bookmarked.


**Per user** (*Admin → Users → edit*):

* **Screen access** – by default the role decides (Viewer: every screen, Guest:
  public screens). Tick *Limit this user to the selected screens* and select the
  screens: the user then sees **only** those (an explicit grant may include
  non-public screens, even for a Guest). Users who can manage screens always see all.
* **Home screen** – the screen opened right after login (must be one the user
  can open). Without one, users land on the screen list. The *Screens* menu in the top bar
  always lists the screens a user may open.

**Public display** (*Admin → System settings*): choose a **public home screen**.

* Visitors who are not logged in see it immediately (wall display / kiosk), and
  users land on it after **Log out**.
* Visitors can move only between screens marked **public** (the *Screens* menu
  lists them, and links on Plane screens work). Opening any other screen shows a
  **Restricted Area** page with a *Log in* button that returns to that screen.
* Visitors can never write to the PLC; buttons are greyed out.
* With “none” selected (the default) every page requires a login, as before.
  If the chosen screen is deleted or un-ticked as public, public access switches
  off automatically.

**Log in** sits in the top-right corner, exactly where **Log out** is when logged in.

On every screen the **screen name**, the **full screen** button and (for users who may build
screens) **Edit** are in the top bar; there is no separate screen header. The state of the live
values is shown by the dot of the clock (see 7f).

**Full screen** shows the screen alone; buttons and links that open another screen keep full
screen on (the screen runs in a full-screen frame, so the browser does not leave full screen when
the next screen loads). Leave with **Esc** or the small button in the top corner – the page then
stays on the screen that was open last.
Alarm pop-ups and other messages appear inside full screen too.

**Plane screens** start directly under the top bar, without a frame or border. In the designer
(*Canvas* properties, nothing selected) the section **Page around the drawing** sets a **page
background colour** and/or **page background image** (cover, contain, stretch, repeat, centre) for
the whole window of the screen – combined with *Background colour: none* for the drawing, the
drawing blends into its surroundings. The designer shows the page background around the canvas.

Old `/dashboards/...` bookmarks redirect to `/screens/...`. The permission codes say *screen*
too (`manage_screens`, `view_screens`, `view_public_screens`; the old `…_dashboards` codes are
renamed by `flask db upgrade`, and every role keeps its permissions). Only internal code names
(the `Dashboard` model, the `dashboards` blueprint) still use the old word.

## 7b. Tag table and alarms

### Tag table (*Admin → Tag table*, administrators only)

The tag table is the master list of tags. Screens *show* tags from it: one tag can
appear on any number of screens, and changing a tag changes it everywhere.

* Lists every tag of every PLC/device with its live value (refreshed every 3 s) and
  the screens that show it.
* **Search** label, address, type, unit, PLC or screen name; **filter** by PLC,
  screen (or *no screen*) and writable/read-only; **sort** by clicking a column
  header (click again to reverse); choose **10/25/50/100 rows** per page.
* **Add tag**: the tag form plus **Screens** check boxes (none is fine – such tags can
  still be used by alarms). Ticked screens show the tag at the end; unticking removes
  it from that screen only.
* Row buttons: 🔔 new alarm for the tag · ✎ edit · **⧉ Copy** – opens *Add tag* filled
  with the tag's settings (name “…_copy”); change the address and save · 🗑 delete
  (refused while alarms use the tag).
* **Tag names** work like identifiers (they are used in automation calculations such as
  `Tank_1.level = Tank_1.level + 1`): a name must be **unique** (upper/lower case is ignored),
  may contain only **letters** (any language), **digits**, `_` and `.` – **no spaces** and no
  mathematical or other symbols (`+ - * / % = ( ) [ ] ,` …) – and must **not start with a digit**;
  a dot must stand between two parts, as in an e-mail address. The form explains every problem
  while you type and refuses to save an invalid name (also the designer's *New tag* dialog and
  the Excel import). Names saved before these rules keep working; the tag table marks them with
  **rename**, and they must follow the rules once they are changed. SVG imports turn invalid
  names into valid ones (`Door open` → `Door_open`) and say so.

### Tags on screens

* **Grid screens** (*Edit screen*): **New tag** creates a tag in the tag table and shows
  it on the screen; **Add existing tag** opens a searchable list of the tag table – tick
  one or more tags and *Add to screen*. ✕ **Remove from screen** keeps the tag in the
  tag table. ↑/↓ order the tiles of this screen (each screen has its own order).
  Widget and tile width belong to the tag, so they are the same on every grid screen.
* **Plane screens** (designer, *PLC tags* palette): **New tag** or **Existing tag**
  (same searchable list); the *Tag* property of an element also offers both.
* Deleting a screen keeps its tags in the tag table.

### Excel export and import (Tag table and Alarms, administrators only)

* **Export ▾**: *All*, *Current search / filter* (exactly what the table shows, all
  pages) or *Selected* – tick rows with the check boxes (the header box ticks the whole
  page; the selection is kept while you page through the table). The file has a data
  sheet and a *Help* sheet that explains every column.
* **Import**: choose an .xlsx file (use an exported file as the template).
  - A row with the **ID** of an existing tag/alarm updates it; rows without an ID (or
    with an unknown one) create new ones.
  - Columns you delete from the sheet keep their current values; empty cells clear
    optional values.
  - Tags: *PLC* and *Screens* are given by name (screens separated by commas); dummy PLC
    tags need no address. Alarms: the tag by *Tag ID* or by *Tag* + *Tag PLC*,
    *Condition* as `=`, `≠`/`!=`, `>`, `≥`/`>=`, `<`, `≤`/`<=`, *Severity* Critical /
    High / Low, *Trigger* `tag` or `comm`.
  - Every row is checked like the web form. **All or nothing**: if any row is wrong,
    nothing is saved and the report lists every problem with its Excel row number.
  - **Check only** validates the file and shows what would be created / updated /
    left unchanged, without saving.

### Alarms (*Admin → Alarms*, administrators only)

Each alarm has a unique **name**, a **description**, **trigger logic**
(*tag* `=` `≠` `>` `≥` `<` `≤` *value*; BOOL tags only `=`/`≠` with
true/false; values are compared in engineering units after scale/offset),
a **severity** – **Critical** (red), **High** (orange), **Low** (green) –,
an optional **screen** and an **enabled** switch.

The alarm table (with **Add alarm** at the top) shows name, description,
condition, severity colour, status (enabled/disabled – click to toggle) and
state (*Active* / *Normal*) with **Details**, **Edit** and **Delete** buttons,
and supports search, filters (severity, status, state), sorting and paging.
*Details* shows the live tag value, whether the condition is currently true
and the last events. Deleting an alarm keeps its history.

### How alarms run

A background thread in the web process evaluates all enabled alarms every
`ALARM_SCAN_INTERVAL` seconds (tags are read in batches per PLC):

* condition becomes true → an **event** is opened (it stores a copy of the
  name, description, severity, condition and the tag value);
* condition no longer true → the event is closed **automatically**;
* tag unreadable (PLC offline) → nothing changes until it can be read again.

### Alarm window, Events and history (all logged-in users)

* When a new event appears, every logged-in browser pops up the **alarm
  window** (name, description, severity, value, time). **Open Screen** opens
  the alarm's screen with the alarm shown at the top (banner with its own
  *Ack*); **Ack** acknowledges the event and closes the window. Several
  unacknowledged alarms are shown one after another, most severe first.
* The **Events** button in the top bar shows the number of active events in
  the colour of the most severe one (it pulses while any is unacknowledged).
  It opens a window listing the active events: name, time occurred, ack time,
  **Details** (opens the alarm window) and – for administrators – **Remove**
  (click twice), which closes the event **manually**; the alarm then waits
  until its condition has cleared before it can fire again.
* **Event History** (button at the top of the Events window) lists all events:
  name, time occurred, ack time, closure time, closure type – *Automatic*
  (condition cleared) or *Manual* with the user's name – and *Details*.
  Search, filters (severity, closure type, date range), sorting and paging.
  Times are shown in the browser's local time zone.

> With several worker processes (e.g. gunicorn `-w 4`) each process runs its
> own scanner. Scans are serialised only within a process, so set
> `ALARM_SCANNER=0` on all but one process, or run a single worker with threads.

## 7c. Languages (English, Hebrew, and your own)

Every user chooses the user interface language on the **Settings** page (person icon in the top
bar); administrators can also set it per user (*Admin → Users*). Visitors who are not logged in
see the default language (*Admin → Languages*). English is the primary language and the
default; **Hebrew** (עברית, right-to-left) is included.

*Admin → Languages* (administrators):

* lists every language with the share of translated texts (coverage bar);
* **Upload** a language file (`.json`) – a new language appears in the menu at once; a file with
  the code of a built-in language (e.g. `he`) replaces it, deleting the upload restores the
  built-in file;
* **Translation template** – every English text with an empty translation, to start a new
  language; the ✎ button of a language downloads all texts with that language's translations
  (missing ones empty) to complete it;
* **Default language** – used for visitors and users who did not choose one.

Language file format (UTF-8):

```json
{
  "language": {"code": "fr", "name": "Français", "english_name": "French", "direction": "ltr"},
  "messages": {
    "Screens": "Écrans",
    "Tag '{0}' saved.": "Tag « {0} » enregistré."
  }
}
```

* keys are the English texts, unchanged; empty or missing translations show in English;
* placeholders such as `{0}` or `{name}` must stay in the translation (entries that lose or add a
  placeholder are ignored and reported on upload);
* `direction` is `rtl` for Hebrew, Arabic…: the pages then switch to right-to-left
  (Bootstrap RTL); drawings on Plane screens and the designer canvas keep their layout.

Uploaded files are stored in `instance/languages/` (`LANGUAGES_FOLDER`). Names you enter (screens,
tags, PLCs, images, alarm texts) are data and are not translated. After changing texts in the
code, run `python tools/i18n_extract.py` to update `en.json`; it also reports the texts each
language is missing.

## 7d. Dialogs and notifications (SweetAlert2)

Confirmations (delete, write a value to the PLC…), value entry on Plane screens, alerts and
messages use [SweetAlert2](https://sweetalert2.github.io/) dialogs in the dark HMI style, in the
user's language and direction. Messages after an action (*saved*, *deleted*, errors) appear as a
toast in the corner: success and information close by themselves (hover to keep them), warnings
and errors stay until closed. SweetAlert2 is bundled in `app/static/vendor/sweetalert2` (no
internet access needed). In templates a confirmation is added with
`data-confirm="Question?"` on a form (the `post_button` macro's `confirm=` does this); scripts use
`HMIDialog.confirm()`, `.alert()`, `.prompt()` and `.toast()`.

## 7e. Tag history and trend charts (*Trends* in the menu)

**Saving samples.** In a tag's settings (*Admin → Tag table → Edit*, section *History / trends*)
tick **Save history** and set **Sample every (seconds)** (1–86400). The same settings are the
*History* and *History interval (s)* columns of the tag Excel file. The background **history
logger** then reads the tag at that interval and stores the value with the full date and time
(UTC in the database). Samples are aligned to the interval (a 10 s tag is sampled at :00, :10,
:20…), so tags with the same interval share time stamps. Unreadable values are skipped (the gap
is visible in the charts). Local dummy tags cannot be logged – they have no value on the server.
Samples older than **Keep tag history (days)** (*Admin → System settings*, default 90, 0 = keep
forever) are deleted automatically once an hour. Deleting a tag deletes its samples.

**Tag history** (*Trends → Tag history*) lists the samples, newest first, with a tag picker
(search, select all / none), a From/To date-time range and quick ranges (last hour, 24 hours,
7 days). Dates and times are shown in the time zone of your browser. **Export to Excel**
downloads the filtered samples either as *one row per sample* (date & time, tag, PLC, value,
unit, address) or as *one column per tag* (one row per time stamp) – handy for Excel charts.
Excel files contain real date-time cells and an *Info* sheet (time zone, range, tags); at most
1,000,000 rows are exported.

**Trend charts** (*Trends → Trend charts*). Users who can build screens (`manage_screens`)
create named charts: choose tags with history logging (numeric and BOOL tags), give **each tag
its own colour** and put tags with very different ranges on the **right axis**. Anyone with
`view_history` can open the charts or draw a **Quick chart** of any logged tags without saving
it. The chart viewer offers ranges from 15 minutes to 30 days or a custom From/To range,
*earlier / later* buttons, **live mode** (follows new samples), **drag to zoom** (double-click
goes back), show/hide and temporary colour changes per tag, the newest value of each tag,
links to the table and the Excel export of the shown range, and **Save image** – a PNG with
the chart title and time range on a dark background. Long ranges are reduced for display to
the minimum and maximum of each time slot, so peaks remain visible. Chart.js and its date
adapter are bundled in `app/static/vendor/chartjs` (no internet access needed).

**On screens: right-click a value** (grid tile, or any element bound to a tag on an Plane screen;
a long press on touch screens) for a small menu:

* **Show graph** – for tags with history (users with `view_history`): a window with the trend of
  that tag over the last *N* days – 1 hour, 8 hours, 1, 7 or 30 days, or any number of **days
  back** you type (remembered by the browser). It refreshes every 15 s and has **Save image**
  (PNG) and *Open in Trends*. Tags without history show the item greyed out.
* **Details** – the tag's current value, quality and update time; address, data type, unit,
  scaling, decimals, range, TRUE/FALSE texts, write access, dummy kind and history summary; the
  PLC (name, protocol, connection address, live status); and the screens and alarms that use the
  tag. Visitors on public screens do not see the PLC's network address or the alarms.

Both windows also work while an Plane screen is in full-screen mode.

## 7f. Top bar, Settings and colour theme

* **Top bar** – left: logo and, on screens, the screen name with *Full screen* and *Edit*;
  centre: the **clock** (time and date); right: icon buttons – Screens, Trends, Images, PLCs,
  Admin, Events, Settings and Log out. A button's name appears when the pointer is over it (on
  narrow windows the ☰ menu lists the icons with their names).
* **Clock** – on screens its dot shows the live values: green = up to date, amber = a PLC is
  reconnecting, red = a PLC is offline or the update failed; the tooltip gives the details
  (last update time, PLC names, errors). Values are always refreshed – there is no pause button.
* **Log out** asks for confirmation.
* **Server connection monitor** – every page checks the server every 5 s
  (`SERVER_CHECK_INTERVAL`) from a **Web Worker**, so the check keeps its pace in
  background tabs and on wall displays (browsers slow down page timers there). After two
  failed checks a red banner says *Connection to the server lost*, with the time, the
  reason (no answer, server not reachable, this computer is offline, database not
  available) and the attempt count. The page title starts with ⚠ and the values are greyed
  out, because they are no longer current. While the server is lost the check runs every
  2 s. When it answers again the banner turns green for a few seconds: *Connection to the
  server restored*, with how long it was down, or *The server was restarted*. The banner is
  shown in full-screen mode too. The check uses `GET /api/health`, which is public and
  cheap: it runs `SELECT 1` and answers `503` when the database fails.
* **Settings** (person icon): account details, **colour theme** – *Dark mode* (default),
  *Light mode* or *Automatic* (follows the operating system's light/dark setting) –, the
  language and the password. Plane screens keep the colours of their drawing in both themes.

## 7g. Information library and PLC files (*Library* in the menu)

The **Library** menu holds *Images* (pictures for screens, see 6) and the **Information library**:
manuals, wiring diagrams, data sheets and photos – PDF, PNG, JPEG, GIF, WebP and BMP files (up to
25 MB, `LIBRARY_MAX_BYTES`). Every logged-in user can open and download them; users with
`manage_plcs` upload, rename, describe and delete files.

* **Attach files to a PLC / device** – *PLCs → Edit*, card *Files*: **Add from library** (searchable
  list) or **Upload new**. Any number of files per PLC, and one file can belong to several PLCs.
  The PLC list shows a 📎 count, and the *Details* window of a screen value lists the PLC's files.
* **No duplicates (SHA-1)** – each file's SHA-1 hash is stored in the database. Before uploading,
  the browser computes the hash and asks the server whether the library already has it (the
  server checks again when the file arrives; browsers on plain `http://` other than localhost
  cannot hash, so the server's check is used). If the file exists, it is **not uploaded**: a
  dialog names the existing file and offers to use it (attach it to the PLC / show it in the
  library) or to cancel.
* Files are stored in `instance/uploads/library/` named after their hash – back this folder up
  together with the database.

## 7h. Audit log (*Admin → Audit log*)

Every change users make is recorded: **who** (user and IP address), **when**, **what** (created,
changed, deleted, value written, logged in / out, failed login), the object (PLC, tag, screen,
alarm, user, role, setting, library file, image…) and for each changed field the **value before
and after** (many-to-many lists such as a role's permissions or a PLC's files by name; foreign
keys with the referenced name; passwords are never shown, long values such as screen layouts are
shortened). Values written to PLCs are recorded with the value before the write.

The page has search, filters (user, action, object type, date range), sorting, pagination and an
**Excel export** (one row per changed field). It needs the permission `view_audit`, which only the
Admin role has by default – grant it to other roles in *Admin → Roles & permissions*. Background
work (alarm scanner, history logger, CLI commands) is not recorded. Entries are kept forever.

## 7i. UI templates (*Admin → UI templates*)

The look and layout of the whole interface is a **UI template**. An administrator picks one for
all users; five are built in:

| Template | For | What changes |
|---|---|---|
| **Classic** (default) | everyone | The original look: icon top bar, alarm window, grid tiles; each user chooses dark/light |
| **High-performance (ISA-101)** | control rooms | Calm grey screens, colour only for abnormal states; range bars with the limits, one-hour trend per logged tag, alarm frame on the tile, *Change…* button with a dialog; alarm strip instead of popups |
| **Alarm focus** | shift work | Dark theme, permanent alarm strip under the top bar (counts per priority with shapes ▲ ■ ●, newest unacknowledged alarm, *Ack*) and a side alarm list that never blocks the page |
| **Workspaces** | mixed teams | Sidebar with *Operate* / *Engineer* workspaces, screen list with alarm badges, **Plant overview** as home page (live card per screen, PLC communication chips), quick search **Ctrl+K** |
| **Engineering** | commissioning | Tags are edited in a **side panel** next to the tag table, **bulk bar** for selected tags (export, add to a screen, delete), quick search **Ctrl+K** |

* **Preview** shows a template to you only (in your session) with a purple bar *Activate for
  everyone / End preview*; **Activate** switches all users. Changes are in the audit log.
* **Upload** – developers can build their own templates: a ZIP with `template.json`, CSS and
  optionally JavaScript, images and fonts. Templates change colours and shapes through the
  `--hmi-*` CSS variables and switch the built-in interface variants with feature flags
  (`nav`, `home`, `alarm_ui`, `tiles`, `tags_ui`, `command_palette`, `color_mode`). They
  cannot contain server code. **Download** any template as a starting point. The developer
  guide with the manifest schema, the CSS variables and a ready-made example is
  [docs/UI_TEMPLATES.md](docs/UI_TEMPLATES.md).
* The *Plant overview* (`/overview`) and the quick search work with every template; *Workspaces*
  only makes them the default.

## 7j. Automations (*Admin → Automations*)

Automations react to the plant without anybody watching a screen – like the automation editor
of Home Assistant. Each automation has three parts:

* **When** (triggers – any of them starts the automation): *Tag value changed* (optionally
  from / to a value, held *for* a time), *Tag above / below a limit* (fires when the value
  crosses into the range, optionally after it stayed there *for* a time), *Time of day* (with
  week days), *Every …* (interval), *Alarm event* (raised / acknowledged / cleared, any or one
  alarm or severity), *PLC communication* (offline / back online) and *Server started*.
* **And if** (optional conditions – all must be true): *Tag value is*, *Tag above / below*,
  *Time window* (may pass midnight, week days), *Alarm is active*, and the building blocks
  *And*, *Or*, *Not*.
* **Then do** (actions, in order): *Write tag*, *Calculate* (math on tags, see below), *Wait for a time*, *Wait until* (with timeout),
  *Condition* (stop unless true), *If – then – else*, *Repeat*, *Notification* (shown to the
  operators in the alarm list/strip and closed when acknowledged, or only written to the run
  trace; `{trigger}` is replaced by what started the run), *Other automation* (run / enable /
  disable) and *Stop*.

**Calculate** – one assignment per line, for example:

```
counter = counter + 1
[Flow total] = [Flow 1] * [Factor] + [Flow 2]
Setpoint = limit(Setpoint * 1.1, 0, 80)
```

Tags are written by label (in `[brackets]` when the label has spaces; `[#12]` is the tag with
id 12; *Insert tag…* adds one at the cursor). Operators `+ - * / %` and parentheses; functions
`abs`, `min`, `max`, `round`, `floor`, `ceil`, `sqrt`, `limit(x, low, high)`; `#` starts a comment.
The text is checked when saving: every tag must exist, sources must be numbers (or BOOL = 0/1) and
targets writable number tags – each problem is listed with its line. At run time all lines are
calculated first (later lines use the results of earlier ones) and **nothing is written unless
every line succeeds**: division by zero, a tag that cannot be read, a result that is not a finite
number, or a result outside the target's range (its data type in engineering units, e.g. INT
-32768 … 32767, and its Min/Max when both are set) stop the action. Choose *limit it to the
nearest allowed value* to clamp out-of-range results instead (the run trace says so). Results
for integer tags are rounded. Calculate writes tags, so it needs the administrator's approval
like *Write tag*.

Every block can be renamed, moved, duplicated, disabled or deleted (⋮ menu). **Edit in YAML**
shows the same automation as text (tags, alarms, PLCs and automations by name); automations can
be **exported** and **imported** as YAML (one or a list; all or nothing).

* **Modes** (when the automation is triggered while it still runs): *Single* (ignore),
  *Restart*, *Queued*, *Parallel* (with a maximum).
* **Runs** – the last 30 runs of each automation with a step-by-step trace: what triggered it,
  each condition (true/false) and each action (result, errors). *Run actions* starts the
  actions at once (triggers and conditions are skipped).
* **Safety of PLC writes** – *Write tag* and *Calculate* only work on writable tags, and only after an
  administrator has approved the automation (*Allowed to write PLC tags*). Whoever else changes
  these actions withdraws the approval; imported automations are never approved. Every
  write is in the audit log as **Automation: <name>**, with the value before and after.
* Triggers follow Home Assistant: they fire on a **change**, so a tag that is already above its
  limit when the server starts does not fire until it has left the range and crossed it again.
* The engine checks the triggers every second (`AUTOMATION_TICK`) in a background thread, also
  when no browser is open. Times use `DISPLAY_TIMEZONE` (or the server's clock).
* Permission: `manage_automations` (Admin by default).

## 8. Database migrations

The initial migration is included. After changing a model:

```powershell
flask db migrate -m "describe the change"
flask db upgrade
```

Migrations use Alembic *batch mode*, so column changes also work on SQLite.

## 9. Tests

```powershell
pytest                       # 652 tests
pytest --cov=app             # with coverage (~94 %)
```

The suite uses the `testing` config (in-memory SQLite, CSRF off, simulated
PLCs) and Flask's test client. `tests/test_plc_driver.py` additionally starts
a real S7 server (`snap7.server`) on localhost to test the genuine snap7
client path – batched reads, writes, bit writes and error isolation.

## 10. Production deployment

```powershell
$env:FLASK_CONFIG="production"; $env:SECRET_KEY="<random>"
flask db upgrade
waitress-serve --host 0.0.0.0 --port 8000 --threads 8 wsgi:app
```

On Linux you can use `gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 wsgi:app`.
Use **one worker process** (threads are fine): each process opens its own
connection to every PLC, and S7 CPUs allow only a limited number of
connections; with several processes each would also run its own alarm scanner and
history logger (duplicate samples). Put a reverse proxy with HTTPS in front (IIS, nginx, Caddy) and set
`SESSION_COOKIE_SECURE=1`.

### Offline (air-gapped) installation

pyPLCHMI runs without Internet access; every front-end library is bundled. To install it on a
computer without Internet (Windows or Linux, without Docker), download the source code and the
Python libraries (wheels) on a connected computer, transfer them, and install them with
`pip install --no-index`. The step-by-step guide with the system requirements is
[OFFLINE_INSTALLATION.md](OFFLINE_INSTALLATION.md).

### Docker

`Dockerfile`, `docker-compose.yml` and `docker/entrypoint.sh` run pyPLCHMI as one container:
`cp .env.example .env` (set `SECRET_KEY`, `HMI_ADMIN_USER`, `HMI_ADMIN_PASSWORD`), then
`docker compose up -d --build`. The container migrates the database on every start, keeps
its data in the `/app/instance` volume and has a health check on `/api/health`. The full
requirements checklist (host, configuration, volume, networking for S7, Modbus TCP/RTU and
BACnet broadcasts, operation, security) is in [docs/DOCKER.md](DOCKER.md).

## 11. Security notes

* Passwords are hashed with **bcrypt** (Flask-Bcrypt); plain text is never stored.
* All forms and AJAX POSTs are protected by **CSRF tokens** (Flask-WTF).
* Login uses a generic error message, `next=` redirects are restricted to this site,
  logout is POST-only, session cookies are `HttpOnly` / `SameSite=Lax`. Background value polls
  never rewrite the session cookie, so switching users while other tabs show screens is safe.
* Writes require the `write_values` permission **and** a data point marked
  writable; values are range-checked per data type, and the screen asks for
  confirmation before writing.
* Writing a single BOOL is done as read-modify-write of its byte under a lock.
  If the PLC program writes other bits of that same byte at the same moment, one
  change can be lost – keep HMI command bits in their own byte.
* Language files can only be uploaded by administrators; they are parsed as JSON, limited to
  2 MB, and every translation is shown as escaped text (never as HTML), so a language file
  cannot inject markup or scripts.
* UI templates can only be uploaded by administrators. A package is checked completely before
  it is installed: ZIP size and file count limits, no absolute paths or `..`, only static file
  types (CSS, JS, JSON, images, fonts, text) – no Python and no Jinja pages, so a template
  cannot run code on the server. Files are served with `X-Content-Type-Options: nosniff`, SVG
  files with a Content-Security-Policy that blocks scripts. A template's own JavaScript runs in
  every page, so the page warns about it: install templates only from developers you trust.
