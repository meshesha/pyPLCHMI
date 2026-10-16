# Architecture

## Request flow

```
Browser ──HTML──▶ blueprint view (auth/main/admin/dashboards)
   │                    │  Flask-Login session + @permission_required
   │                    ▼
   │              SQLAlchemy models  ◀──▶  SQLite / PostgreSQL (Flask-Migrate)
   │
   └──fetch() every refresh_ms──▶ /api/dashboards/<id>/values
                                     │
                                     ▼
                         plc.service.read_dashboard()
                                     │ groups data points per PLC
                                     ▼
                         PLCConnectionManager.read(plc, {id: S7Address})
                                     │ plan_reads(): merge nearby tags
                                     ▼
                         Snap7Connection.read() → snap7.client.Client.read_area()
                                     │
                                     ▼
                         datatypes.decode() → snap7.util.get_real/get_int/…
```

## Application factory

`app/__init__.py::create_app(config_name)`:

1. Loads a class from `config.py` (`DevelopmentConfig`, `TestingConfig`, `ProductionConfig`).
2. Calls `init_app` on every extension defined in `app/extensions.py`.
3. Registers the blueprints: `main` (/), `auth` (/auth), `admin` (/admin),
   `dashboards` (/screens), `tags` (/tags), `alarms` (/alarms), `api` (/api),
   `assets` (/assets), `i18n` (/language, /i18n/<code>.js, /admin/languages), `history` (/history),
   and `before_request` hooks that start the alarm scanner thread (`ALARM_SCANNER`) and the
   history logger thread (`HISTORY_LOGGER`) on the first request (and again if one has stopped).
4. Registers error pages, template globals (`Perm`, `plc_simulation`, `_`, `current_language`,
   `available_languages`, `default_language`) and CLI commands.
5. Stores one `PLCConnectionManager` in `app.extensions["plc_manager"]`.

Because nothing is created at import time, tests can build as many isolated
apps as they need.

## Data model

```
Permission ─┐ (role_permissions)
            ├── Role ──< User
            │
PLC ──< DataPoint ──< ScreenTag (position) >── Screen (kind: grid | image, layout JSON)
            │                  ▲
            └──< Alarm ────────┘ (screen to open, optional)
                   └──< AlarmEvent (snapshot; alarm_id set NULL on delete) >── User (acked_by, closed_by)
DataPoint ──< TagSample (ts UTC, value_num | value_text; index (datapoint_id, ts))
TrendChart (name, series_json [{tag_id, color, axis}], range_key)  ── tag ids ──▶ DataPoint

Asset (uploaded image; file in UPLOAD_FOLDER)  ◀── referenced by id from layouts
  └── category = folder path ──▶ AssetFolder (path unique; one row per folder and parent)
```

* **Tags and screens** – `ScreenTag` links a screen and a tag (unique pair) with the
  tile order of that screen. `Dashboard.tag_links` / `DataPoint.screen_links` own the
  links (deleted with the screen or the tag); `Dashboard.datapoints` and
  `DataPoint.screens` are read-only lists through the link table. Use
  `Dashboard.add_tag()` / `remove_tag()` to change links. Removing a tag from a
  screen or deleting a screen never deletes tags.
* `DataPoint` stores the location decomposed (`area`, `db_number`, `start`,
  `bit`, `data_type`, `string_length`) plus presentation settings (widget,
  unit, scale/offset, decimals, min/max, texts, writable, width, position).
* `DataPoint.address` / `s7_address` rebuild the Siemens address from those fields.

## PLC layer (`app/plc`)

| Module | Responsibility |
|---|---|
| `datatypes.py` | Registry of S7 types (size, range, writability); `decode`/`encode` delegate to `snap7.util`. |
| `addressing.py` | `parse_address("DB1.DBD4:REAL")` → `S7Address`; `S7Address.format()`; mapping to `snap7.type.Area`. |
| `driver.py` | `Snap7Connection` (real), `SimulatedConnection` (in-memory), `PLCConnectionManager` (pooling, locking, reconnect back-off, batching). |
| `service.py` | `read_dashboard`, `read_datapoints`, `write_datapoint` – bridge between models and the manager. |

### Connection manager behaviour

* **One persistent connection per PLC**, guarded by a lock (snap7 clients are not thread-safe).
* **Communication states** (`PLCConnectionManager.status`): `online`, `unstable`
  (failing for less than the PLC's `offline_delay`), `offline` (failing for at
  least `offline_delay`), `unknown` (never contacted) and `disabled`.
  `connected` is true for `online` and `unstable`.
* **Grace period**: the manager keeps the last good raw value of every tag
  (`_Entry.cache`, keyed by caller key + address). While `unstable`, reads return
  those values as `ReadResult(stale=True)`; the API passes `stale` on, the screens
  draw such values in amber, and the alarm engine keeps evaluating them, so a
  blip neither raises nor clears tag alarms. The cache is dropped when the PLC's
  connection settings change.
* **Reconnect**: a connection that breaks during a request is closed and re-opened
  at once and the request is repeated (once). Failed *connection attempts* are
  retried after 0.5, 1, 2, 4 … seconds, capped at `PLC_RECONNECT_DELAY`, so a
  recovered PLC is noticed quickly while an offline one is not hammered. A change of
  `PLC.connection_signature` (address, rack/slot, timeout…) reconnects.
* **Timeouts**: `PLC.timeout` (request timeout) is part of the S7 signature and is
  applied by `Snap7Connection._apply_timeouts` – `set_param` for the snap7 C library
  (python-snap7 1.x/2.x) and the socket timeout of `client.connection` for the pure
  Python python-snap7 3.x, which ignores those parameters.
* **Batching** (`plan_reads`): tags in the same area/DB are merged into blocks
  up to `PLC_MAX_READ_SPAN` bytes when the gap between them is ≤
  `PLC_MAX_READ_GAP`. If a block fails but the connection is still alive
  (e.g. one tag points past the end of a DB), its tags are re-read
  individually so one bad address does not blank its neighbours.
* **Quality**: every value carries `quality: good|bad`, `stale` and an `error`
  message; the screen shows bad tiles with a red edge, stale tiles with an amber
  edge, and PLC badges show online / reconnecting / offline.
* **Logging**: the start (`communication error`) and end (`communication restored
  after n s`) of every interruption are logged at WARNING level.

### Simulator

`PLC_SIMULATION=1` swaps `Snap7Connection` for `SimulatedConnection`.
Memory is kept per PLC; values never written from the HMI are animated (sine
waves for numbers, toggling bits, a clock string). IP addresses ending in
`.254` simulate an unreachable PLC.

## Security

* `utils/decorators.permission_required(code)` → redirect to login (HTML) or
  401 (API) for anonymous users, 403 without the permission.
* `User.can_view_dashboard()` implements the Viewer/Guest visibility rule.
* CSRF: `CSRFProtect` covers all POSTs; JavaScript sends `X-CSRFToken` from
  the `<meta name="csrf-token">` tag.
* Sessions (`app/extensions.py`): `HMILoginManager` applies Flask-Login's "strong" session
  protection only to sessions with a logged-in user, and `HMISessionInterface` never sends the
  session cookie for read-only API polls (`GET /api/…`, `/history/api/series`). Together they
  stop a slow request that carries an *old* cookie (e.g. the value poll of a public screen in
  another tab, waiting for a PLC timeout) from overwriting a fresh login or logout cookie.

## Image (graphic) screens

```
designer.html ── hmi-designer.js ──┐            hmi-mimic.js ── mimic_view.html
                                   ▼                  ▼
                       hmi-render.js (shared SVG renderer) + hmi-symbols.js
                                   │
      PUT /api/dashboards/<id>/layout          GET /api/dashboards/<id>/values
                                   ▼                  ▼
              dashboards/layout.py: sanitize_layout()   plc/service.py
                                   ▼
                       Dashboard.layout (JSON text)
```

* **Layout document** – `{"version", "canvas": {...}, "elements": [...]}`; element
  types `rect`, `ellipse`, `line`, `pipe`, `text`, `image`, `symbol`, `value`,
  `lamp`, `button`, `bar`. Box elements use `x, y, w, h, rot`; `line`/`pipe` use
  `points`. `point` references a data point of the same screen, `asset` an
  uploaded image, `link` another screen (by id, so renames don't break it).
* **Server-side validation** (`ELEMENT_SPECS`): unknown types/properties dropped,
  numbers clamped, colours must be hex/`none`, strings truncated, references must
  exist, ≤ 2000 elements. The renderer only ever uses `textContent`/`setAttribute`
  for user data; symbol markup is constant code.
* **Rendering** – one `<g>` per element in local coordinates (translate + rotate),
  symbols as nested `<svg viewBox="0 0 100 100">` with CSS-driven animation
  (`.sr` rotor, `.sf` flame, `.sk` blink, `.sv` level). `applyValues()` toggles
  classes/colours from the same `/values` JSON used by grid dashboards.
* **Assets** – content-sniffed (PNG/JPEG/GIF/WebP/SVG), random file names,
  served with `nosniff` + sandbox CSP; deletion is refused while a layout uses them.
* **Library folders** (`app/assets/folders.py`) – a folder is a normalised `/`-separated
  path (no `..`, no `\ < > : " | ? *`, ≤ 60 chars per name, ≤ 200 per path) stored in
  `Asset.category`; `AssetFolder` rows (created for every parent by `ensure_folder`) let
  empty folders exist. `rename_folder` rewrites the path prefix of folders and images
  (merging into an existing target), `delete_folder` only removes folders without images.
  `store_asset` is the single way to add a file (validation via `save_upload`, optional
  SHA-256 re-use of identical files) and is shared by uploads, SVG import and the
  built-in animations. Migration `64d88aba5b79` widens `category` to 200 characters and
  creates a folder for every existing category.
* **Built-in animations** – `tools/make_animations.py` draws each frame with Pillow at 4×
  size, reduces it (anti-aliasing) and writes GIFs with one shared palette whose index 255
  is transparent (`disposal=2`), plus `manifest.json`. `import_builtin_animations` copies
  them into `Animations/<category>`; identical files already in the library are skipped,
  which makes the button / `flask library animations` idempotent.
* SVG was chosen over `<canvas>` because elements stay individually clickable,
  scale crisply on any screen, and can be animated with CSS.

## Protocols (S7 and Modbus)

`PLC.protocol` selects the driver: `s7`, `modbus_tcp` or `modbus_rtu`
(`app/plc/protocols.py`). Data points store the location in the same columns
for every protocol (`area`, `start`, `bit`, `data_type`, `string_length`;
Modbus areas are `HR`, `IR`, `CO`, `DI`); `DataPoint.tag_address` rebuilds an
`S7Address` or a `ModbusAddress` depending on the PLC's protocol, and the tag
form parses addresses with the matching syntax.

```
PLCConnectionManager ─┬─ S7:     Snap7Connection / SimulatedConnection  (read_area + snap7.util)
                      ├─ BACnet: BACnetConnection.read_tags / write_tag
                      │            ├─ BAC0Adapter → BACnetNetwork (one BAC0 stack, own asyncio thread)
                      │            └─ SimulatedBACnetAdapter
                      └─ Modbus: ModbusConnection.read_tags / write_tag
                                   ├─ PyModbusAdapter      (pymodbus: TCP or RTU)
                                   ├─ PyModbusTCPAdapter   (pyModbusTCP: TCP)
                                   └─ SimulatedModbusAdapter (PLC_SIMULATION)
```

* The adapters share one tiny interface (`connect`, `connected`, `close`,
  `read(area, start, count)`, `write_coil`, `write_registers`) and translate
  library errors into `ModbusDeviceError` (exception response – the tag fails,
  the connection stays) or `ModbusConnectionLost` (timeout/broken link – the
  manager drops the connection and applies the reconnect back-off).
* `plan_modbus_reads` merges tags per area within the protocol limits
  (125 registers / 2000 bits); `decode_registers` / `encode_registers` apply
  the device's byte and word order.
* The pymodbus unit keyword differs between versions (`device_id`, `slave`,
  `unit`) and is detected at runtime.
* `tests/test_modbus.py` runs both client libraries against a real pymodbus
  TCP server started in a background thread.

### BACnet specifics

* BAC0 (≥ 2023) is asyncio-only; Flask is threaded. `BACnetNetwork` therefore
  runs a single BAC0 `lite` application in a dedicated event-loop thread and
  each request is submitted with `asyncio.run_coroutine_threadsafe` and a hard
  timeout (the pending request is cancelled), so an offline device cannot stall
  a web request for longer than its configured timeout.
* Data points reuse the generic columns (`area` = object code such as `AV`,
  `start` = instance) plus `property_name` and `priority`.
* `read_many` tries one ReadPropertyMultiple per device and falls back to
  concurrent ReadProperty calls when the device answers with an error
  (BAC0 raises `TypeError('apdu')` for an unknown property in an RPM).
* Error mapping (`app/plc/errors.py`): no answer → `ConnectionLostError`
  (manager drops the connection, back-off); error answer (unknown object,
  write-access-denied) → `DeviceError` (only that tag/write fails).
* `tests/test_bacnet.py` starts the HMI's stack and a second BAC0 device
  (instance 1001 with AV/BV/MSV/AI/CSV objects) on 127.0.0.1 and exercises
  reads, prioritised writes, error isolation, object browsing and Who-Is.

## Tag table and alarms

```
  AlarmScanner thread (every ALARM_SCAN_INTERVAL s)    GET /api/alarms/active (every 3 s, alarms.js)
             │                                                │ scan_if_due() fallback
             ▼                                                ▼
  alarms/engine.scan_alarms()  ── _scan_lock ──  read_datapoints(tags of enabled alarms)
             │
  condition true & no open event & not suppressed → new AlarmEvent
  condition false → suppressed = False; open event closed (close_type="auto")
  bad quality → skipped
```

* **Tables** – `utils/tables.py` (`parse_state`, `table_page`) plus the
  `_table.html` macros give the Tags, Alarms and Event history pages the same
  server-side search (`ILIKE` over several columns), filters, column sorting
  and pagination; all state is in the query string. The screen list
  (`main.screens`) filters in Python, because screen access is decided per screen
  (`User.can_view_dashboard`). It sorts with `SCREEN_SORT` and pages with
  `ListPagination`, which has the interface of Flask-SQLAlchemy's `Pagination`, so the same
  `pager` macro works. The cards/table choice is stored in the `hmi_screens_view` cookie.
* **Tags** – `DataPoint.address_text` (the formatted address, kept in sync by
  `set_address`) makes addresses searchable/sortable in SQL. Tags may have no
  screen; `point_delete` and screen deletion detach (not delete) tags used by
  alarms, and clear `screen_id` on alarms/events explicitly (SQLite does not
  enforce `ON DELETE`).
* **Communication alarms** – `Alarm.kind == "comm"` with `plc_id` (instead of
  `datapoint_id`). The PLC form keeps one per PLC in sync (`Alarm.sync_comm_alarm`).
  Each scan reads one tag of every watched PLC that no tag alarm covers (or calls
  `manager.probe` for PLCs without tags) and the alarm is active while
  `manager.status(plc)["state"] == "offline"`; the event value is the last error.
  Deleting a PLC releases its alarms' events (`Alarm.release_events`).
* **Events** keep a snapshot of the alarm so the history survives edits and
  deletion. `acknowledge()` sets `acked_at/acked_by`; `close()` sets
  `cleared_at`, `close_type` (`auto`/`manual`) and `closed_by`. A manual
  *Remove* sets `Alarm.suppressed` so the alarm does not re-fire until its
  condition has been false once.
* **Browser** – `static/js/alarms.js` (every page) polls the active events,
  updates the *Events* badge and window, opens the alarm window for the most
  severe unacknowledged event (static backdrop: only *Ack* or *Open Screen*
  leave it) and keeps the screen banner (`?alarm=<event id>`) current. The
  event shown in a screen's banner is not popped up again on that page.
  Times are sent as ISO 8601 UTC and rendered in the browser's time zone.
* **API** – `GET /api/alarms/active`, `GET /api/alarms/events/<id>`,
  `POST …/ack` (any logged-in user), `POST …/remove` (admin), and
  `GET /api/tags/values?ids=` (admin) for the tag table.

## Dummy PLC

`protocol = "dummy"` (`app/plc/dummy.py`): `DummyAddress` (area `D`, slot number in
`start`, data type) is built by `parse_tag_address(…, "dummy")` from the data type
alone – the address text is ignored and `DataPointForm.populate_datapoint` assigns
the next free slot (`next_slot`), keeping the slot when a tag stays on the same dummy
PLC. `DataPoint.dummy_scope` makes a dummy tag **general** or **local**:

* *general* – `DummyConnection` (a tag-based driver with `read_tags` / `write_tag`)
  reads and writes the table `dummy_values` (`DummyValue`: plc id + slot unique, data
  type, JSON value) on every request, so all browsers and all server processes agree and
  values survive restarts. A value stored with another data type is ignored (type change
  = back to the default); `next_slot` deletes rows of slots above the highest used slot,
  so a new tag never inherits a deleted tag's value. A process-wide dict is only a
  fallback outside an application context or when the table cannot be read.
* *local* – the server never touches the value. `read_datapoints` returns a placeholder
  (`quality: "local"`, start value, `local: DataPoint.local_spec()` = type, integer range,
  string length, decimals, unit, BOOL texts) and the write API answers 400.
  `static/js/hmi-local.js` (`HMILocal`) keeps the value per browser window in
  `sessionStorage` (`pyplchmi-local:<id>`), `overlay()`s it onto every values response
  (grid runtime, Image runtime, designer live preview, tag table) and `write()`s it after
  the same type/range checks as the server. Local writes need no `write_values`
  permission (they only change the operator's own window). The alarm form rejects local
  tags, the tag form refuses to make a tag with alarms local, and the alarm engine skips
  alarms on local tags.

The connection signature is constant, so the PLC is always online; the PLC form never
creates a communication alarm for it. Migration `a3f6c2d8e91b` adds `dummy_scope`
(default `general`) and `dummy_values`.

## Excel export / import

`app/utils/xlsx.py` writes workbooks (data sheet + *Help* sheet; openpyxl) and reads
uploads by header name (aliases, required columns, 5 MB / 5000 rows).
`app/tags/excel.py` and `app/alarms/excel.py` define the columns and the row logic:
each row is turned into form data and validated by the same WTForms class as the web
form (`DataPointForm`, `AlarmForm`), so the rules are identical. Rows with a known ID
update, others create; missing columns keep current values. Every row is flushed
(so later rows see earlier ones, e.g. unique names and dummy slots); at the end the
session is committed only if no row failed and *check only* is off, otherwise rolled
back. Exports reuse the table queries (`tag_query`, `alarm_query` + `table_query`),
so *Current search / filter* matches the table exactly.

## Graphic screens: visibility, icons, SVG export/import

* **Visibility** – every element may carry `vis` (`always` | `hidden` | `tag`),
  `visPoint` (tag id, validated like `point`), `visOp` (`on` `off` `eq` `ne` `gt` `ge`
  `lt` `le`) and `visValue`. `hmi-render.js` adds the class `hmi-vis-off` to hidden
  elements (`createNode` for `hidden`/`tag`, `applyValues` re-evaluates `tag` rules with
  `isVisible()` on each poll; unreadable values hide). Runtime CSS uses `display: none`,
  the designer only dims. `referenced_points()` includes `visPoint`.
* **Icons** – `app/dashboards/icons.py` locates the `pytablericons` package with
  `importlib.util.find_spec` (the package itself imports pygame, so it is never imported),
  indexes `icons/{outline,filled}/*.svg` (name, category and Tabler tags from the header
  comment) and converts an icon into `{viewBox, mode, nodes: [[tag, attrs]]}` keeping only
  shape elements and geometry attributes. The browser builds the nodes with
  `setAttribute`. Element type `icon` (`icon`, `iconStyle`, `color`, `strokeWidth`,
  `point`/`onColor`/`offColor`, `blink`, label). Runtime data contains the shapes of the
  icons used (`icons` in `_runtime_data`); the designer loads others from
  `GET /api/icons` (search, with shapes) and `GET /api/icons/<style>/<name>`.
* **Designer** – `state.selection` (all ids) and `state.selected` (primary, shows the
  property panel/handles). Multi-selection panel = proxy object with the common values of
  opacity, lock and visibility. Context menu = custom `.dz-menu`. The clipboard
  (`{elements, points}`) is mirrored to `localStorage` so it works across screens;
  pasting links missing tags via `POST /api/dashboards/<id>/tags`.
* **SVG export** (client side) – renders the current layout into a detached `<svg>` with
  `HMIRender.renderLayout`, removes `hidden` elements, embeds images as data URIs, adds a
  small stylesheet for symbols, and stores `{format: "pyplchmi-screen", layout, tags,
  assets (base64), screens}` in `<metadata id="pyplchmi-screen">`.
* **SVG import** (`app/dashboards/svg_import.py`, `POST /screens/import`) – rejects
  DOCTYPE/entities before parsing; with metadata: creates the screen, stores images through
  the normal upload validation (reusing identical files by SHA-256), resolves tags
  (id+label+PLC, label+PLC, or creates them on an existing PLC), maps links by screen name,
  remaps all ids and runs `sanitize_layout`; without metadata: the file (validated as an
  image asset) becomes the background of a new screen sized from width/height/viewBox.
  Each file is committed separately; asset files of a failed import are deleted.

## Languages (`app/i18n`)

* **Catalogs** – one JSON file per language (`language` meta + `messages`: English text →
  translation). Built-in files live in `app/i18n/languages` (`en.json` lists every key and is
  generated by `tools/i18n_extract.py`; `he.json` translates all of them); uploads go to
  `LANGUAGES_FOLDER` and override a built-in file with the same code. Files are parsed and
  validated by `parse_language` (code, name, direction, text values, identical `str.format`
  placeholders) and cached by path + mtime + size.
* **Language of a request** – `select_language()`: `User.language` (migration `0af7bdbd629e`),
  else the `default_language` setting (always for visitors – nothing is kept in the session, so a
  language chosen before logout does not stick to the login page), else `en`. The result is
  cached in `request.environ` for the request.
* **Translating** – `gettext(text, *args, **kwargs)` (`_` in templates) looks the English text up
  and formats it; a translation that cannot be formatted falls back to English, outside a request
  English is used (alarm scanner, CLI). Python messages (flashes, validation errors, address and
  file errors, API errors) are written as `gettext("Tag '{0}' saved.", label)`. Forms derive from
  `app.i18n.forms.FlaskForm`, which translates labels, descriptions, static choices and errors on
  creation/validation. The `_()` in `base.html` also translates stored flash messages.
* **Browser** – `/i18n/<code>.js?v=<version>` returns `window.I18N = {code, dir, messages}`
  (cached for a day, the version changes with the file); `hmi-dialogs.js` defines `T(text,
  ...values)`. Runtime and designer scripts translate at the place where a text is displayed, so
  definitions (palette items, property fields, menu entries, symbol names) stay English keys.
* **Right-to-left** – `<html dir="rtl">` switches to `bootstrap.rtl.min.css`; `hmi.css` uses
  logical properties (`border-inline-start`, `inset-inline-*`); SVG screens and the designer
  stage are `dir="ltr"`; values and addresses use `unicode-bidi: plaintext` so `174 rpm` keeps
  its order.

## Tag history and trend charts (`app/history`)

```
HistoryLogger thread (wakes every HISTORY_TICK s, just after each whole second)
  └─ run_once() ─ sample_due_tags(now)
        tags with log_enabled (not local) whose next aligned time has come
        → read_datapoints() per PLC (one failing PLC only skips its own tags)
        → TagSample(ts = whole second UTC, value) for good, non-stale values
     └─ purge_old_samples() once an hour (setting history_retention_days)
```

* **Schedule** – `logger._due` keeps `tag id → (interval, next epoch second)`; the next time is
  the next multiple of the interval, so equal intervals share time stamps. A changed interval
  takes effect at once; tags that stop being logged are dropped. Tests call
  `sample_due_tags(now=…)` with fixed times (`HISTORY_LOGGER` is off in the testing config).
* **Values** – numbers and BOOL in `value_num` (BOOL as 0/1), STRING in `value_text`.
  `TagSample.display()` formats like the tag (decimals, TRUE/FALSE texts).
* **Deleting a tag** – a `before_delete` listener on `DataPoint` deletes its samples and removes
  it from every chart's `series_json` (SQLite neither enforces `ON DELETE CASCADE` here nor
  avoids reusing a deleted row id).
* **Time zones** – stored UTC. Every page writes the browser zone to the `hmi_tz` cookie
  (`hmi-dialogs.js`); `app/utils/timezones.py` uses it (fallback `DISPLAY_TIMEZONE`, then the
  server zone) to show dates, to parse the From/To filters and for the Excel date cells
  (`tzdata` provides the zone database on Windows).
* **Pages** (`routes.py`, permission `view_history`; chart create/edit/delete need
  `manage_dashboards`): `/history/` (table, filter `tags=1,2` or repeated `t`, `from`, `to`,
  sort by time, pagination), `/history/export.xlsx` (`layout=list|wide`, openpyxl write-only
  workbook, ≤ 1,000,000 rows, *Info* sheet), `/history/charts`, `/charts/<id>`,
  `/charts/quick?tags=`, `/charts/new|<id>/edit|<id>/delete`.
* **Series API** – `GET /history/api/series?tags=&start=&end=&points=` (epoch ms) returns
  `[[ms, value], …]` per tag. Above `points` samples (`data.series_points`) each time slot keeps
  its minimum and maximum, so peaks stay visible; `null` points break the line where samples
  are missing (longer than three intervals and three times the previous spacing).
* **Browser** – `trend_chart.js` (Chart.js 4 + date-fns adapter, vendored in
  `static/vendor/chartjs`): presets/custom range, live refresh, drag-zoom with a back stack,
  per-series colour/visibility, *Save image* draws the chart canvas onto a new canvas with a
  dark background, title and range and downloads it as PNG. `chart_form.js` edits the series
  list (posted as JSON in the hidden `series` field, validated by `ChartForm`); `history.js`
  drives the tag pickers and quick ranges.
* **Value menu on screens** – `static/js/hmi-tagmenu.js` (included by `dashboards/_tag_menu.html`
  on grid and Image screens) handles `contextmenu` on `.hmi-tile[data-id]` and on Image screen
  elements bound to a tag. `dashboards.routes.tag_menu_data()` provides labels, a per-tag
  `history` flag (`history.data.tags_with_history`: logging on or samples saved) and URLs.
  *Show graph* loads Chart.js on demand and reads `/history/api/series` for the last *N* days;
  *Details* reads `GET /api/datapoints/<id>/details` (allowed where one of the tag's screens may be
  opened; PLC endpoint and alarms only for logged-in users, history summary only with
  `view_history`) and adds the live value and PLC status from the `hmi:values` event that the
  screen runtimes (`dashboard.js`, `hmi-mimic.js`) dispatch after every poll. Its menu and
  windows are plain overlays appended to `document.fullscreenElement` when a screen is full screen.
* Migration `e5b7c1f04a92` adds the columns and tables and grants `view_history` to the Admin
  and Viewer roles.

## Top bar, clock and colour themes

* `base.html` – the top bar has three parts: `hmi-navbar-start` (brand + `{% block screen_header %}`,
  filled by the screen views with `dashboards/_screen_header.html`), the centred `#hmi-clock`, and
  the icon list `hmi-icon-nav`. Each icon has an `aria-label` and a `.hmi-nav-label` span that
  `hmi.css` turns into a hover bubble on wide screens (inline text in the collapsed menu).
* `static/js/hmi-shell.js` – ticks the clock and exposes `HMIClock.reportPoll(json)` /
  `reportError(err)`; `dashboard.js` and `hmi-mimic.js` call them after every poll, so the dot
  colour and tooltip show the update and PLC state. It also handles `data-fullscreen-target`
  buttons. On screens (`data-fullscreen-frame="1"`) full screen is a fixed overlay with an
  `<iframe>` of the same screen with `?embed=1` (no top bar, `body.hmi-embed`); screen links
  (`/screens/go/<id>?embed=1` keeps the flag) navigate inside the frame, so the browser does not
  leave full screen – it always does when the top page is replaced. When full screen ends the
  page follows the frame's URL. `alarms.js` in the frame shows the alarm window (it works without the top bar); the
  parent page does not pop up alarms while the overlay exists. Image screens use
  `{% block main_class %}p-0 hmi-page-mimic{% endblock %}` and an inline style from
  `dashboards.layout.page_style()` (canvas `pageBackground`, `pageBackgroundImage`, `pageBackgroundFit`).
* Themes – `users.theme` (`dark` | `light` | `auto`, migration `f2c8a4e6b1d3`), saved by
  `POST /auth/theme`; the context processor passes `ui_theme`, `base.html` sets
  `data-bs-theme` (Bootstrap 5.3 colour modes) and `data-hmi-theme`. For `auto`, `hmi-dialogs.js`
  (loaded in `<head>`) applies `prefers-color-scheme` before the page is painted and follows
  changes. All colours of `hmi.css` are CSS variables redefined under `[data-bs-theme="light"]`;
  charts read `--hmi-chart-*`, SweetAlert2 uses its matching theme.

* **Server connection monitor** – `static/js/hmi-connection.js` (every page, not inside
  the full-screen frame) starts `static/js/hmi-heartbeat.js` as a dedicated Web Worker.
  The worker calls `GET /api/health` every `SERVER_CHECK_INTERVAL` s (with a 4 s timeout),
  and reports `offline` after 2 failures in a row, `retry` while the server is lost
  (checks every 2 s), and `online` with `wasOffline`/`downMs`/`restarted`. `restarted`
  means the `boot` token of `api.health` changed (`BOOT_ID`, random per process). The page
  shows `#hmi-server-banner` inside `document.fullscreenElement` (else `<body>`), sets
  `body.hmi-server-offline` (also on the frame's body), prefixes the title with ⚠ and fires
  the `hmi:server` window event. Without Worker support the same file runs on the page as
  `window.HMIHeartbeat`. `/api/health` belongs to the `api` blueprint, so it never sets
  a session cookie (see `HMISessionInterface`).

## Docker

`Dockerfile` (python:3.12-slim, non-root UID 1000 in `dialout`, `VOLUME /app/instance`,
`HEALTHCHECK` on `/api/health`) → `docker/entrypoint.sh` (checks `SECRET_KEY`, then
`flask db upgrade`, `flask seed`, `flask create-admin --if-missing` from
`HMI_ADMIN_USER`/`HMI_ADMIN_PASSWORD`, optional `flask demo`) → `waitress-serve` with
`HMI_THREADS` threads. It is always one process (see *Production deployment*). See
[DOCKER.md](DOCKER.md).

## Information library and audit log

* **Library** (`app/documents`, `/library`) – `InfoFile` (name, original name, stored file
  `<sha1>.<ext>`, MIME type, size, unique `sha1`) and the association table `plc_files`
  (`PLC.files` ⇄ `InfoFile.plcs`). `storage.store_file()` checks the content type by magic bytes
  (PDF / PNG / JPEG / GIF / WebP / BMP) and raises `DuplicateFile(existing)` when the SHA-1 is known –
  nothing is written. `library_upload.js` hashes in the browser with Web Crypto and calls
  `GET /library/api/check?sha1=` first; `POST /library/api/files` answers 409 with the existing file
  as the server-side check. `plc_files.js` (PLC edit page) attaches/detaches with
  `POST/DELETE /library/api/plcs/<id>/files`. Files are served with `X-Content-Type-Options: nosniff`.
* **Audit** (`app/audit`) – `recorder.install()` registers an `after_flush` listener on
  `sqlalchemy.orm.Session`. During a request (and unless `recorder.paused()` is active – the alarm
  scan uses it) it inspects `session.new / dirty / deleted` for the audited models, takes column
  and many-to-many history from `sqlalchemy.inspect(obj).attrs[...].history` and inserts
  `AuditLog` rows through the flush's connection (same transaction, so a rolled-back change leaves
  no entry). `recorder.record()` adds explicit entries (login, failed login, logout, value write).
  Migration `a7d3e9c2b4f6` creates `info_files`, `plc_files`, `audit_log` and grants `view_audit`
  to Admin.

## Dialogs (SweetAlert2)

`app/static/vendor/sweetalert2` (v11, MIT) is loaded by `base.html`. `hmi-dialogs.js` wraps it:
`HMIDialog.confirm/alert/prompt/toast` with the dark theme (`.hmi-swal` styles in `hmi.css`),
translated buttons and RTL. A capturing `submit` listener intercepts forms (or submit buttons)
with `data-confirm`, asks, then re-submits with `requestSubmit()`. Flash messages are rendered
as Bootstrap alerts inside `#hmi-flashes` (visible without JavaScript) and converted into one
toast (sticky when it contains a warning or error).
