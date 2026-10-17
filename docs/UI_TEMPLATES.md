# UI templates – developer guide

A **UI template** changes the look and the layout of the whole pyPLCHMI interface. The
administrator chooses the active template in **Admin → UI templates**, can **preview** one
(visible only in their own session) before activating it, and can **upload** templates made
by other developers.

pyPLCHMI ships five templates: `classic` (default), `high-performance` (ISA-101 style),
`alarm-focus`, `workspace` and `engineering`. Download any of them from the admin page: it is
the quickest way to start your own. A complete example is in
[examples/ui-templates/example-plant](examples/ui-templates/example-plant).

---

## 1. What a template can and cannot do

| Can | Cannot |
|---|---|
| Set colours, fonts, spacing and shapes with CSS (`--hmi-*` variables and the classes below) | Contain server code: Python files and Jinja/HTML pages are rejected |
| Switch the interface variants built into pyPLCHMI (sidebar, plant overview, alarm strip, high-performance tiles, side-panel tag editing, quick search) | Add new pages or API endpoints |
| Force the light or dark colour mode | Change permissions or what a user may see |
| Add small JavaScript enhancements, images and fonts | Load files from other servers when the plant network is offline (bundle fonts and images in the package) |

Because a template is only static files plus a manifest, it works the same on Windows, Linux
and Docker, and survives updates of pyPLCHMI as long as the documented classes and variables
stay (they are part of this guide).

## 2. Package layout

```
my-plant/                  ← folder name = template id
├── template.json          ← manifest (required)
├── theme.css              ← any number of stylesheets (listed in "css")
├── theme.js               ← optional scripts (listed in "js")
├── preview.svg            ← optional preview image shown on the admin page
├── fonts/…  img/…         ← optional assets, referenced from your CSS with relative URLs
└── README.md              ← optional notes (kept in the package, not served)
```

Zip the folder (the ZIP may contain the folder itself or only its files) and upload it:

```bash
python -m zipfile -c my-plant.zip my-plant/
```

### Limits and checks

* ZIP at most **10 MB**, at most **200 files**, at most 30 MB unpacked.
* Allowed file types: `.json .css .js .png .jpg .jpeg .gif .webp .svg .woff .woff2 .ttf .otf .md .txt`.
* No absolute paths and no `..`; `__MACOSX` and hidden files (`.DS_Store`…) are skipped.
* The id must be new, or tick **Replace** to update your own uploaded template. Built-in ids
  (`classic`, `high-performance`, …) can never be replaced.
* Everything is validated in a temporary folder first; a package with any error installs nothing.

## 3. `template.json`

```json
{
  "pyplchmi_template": 1,
  "id": "my-plant",
  "name": "My plant",
  "version": "1.0.0",
  "author": "ACME Automation",
  "description": "Dark theme with the alarm strip and a sidebar.",
  "color_mode": "user",
  "features": {
    "nav": "sidebar",
    "home": "overview",
    "alarm_ui": "banner",
    "tiles": "hp",
    "tags_ui": "panel",
    "command_palette": true
  },
  "css": ["theme.css"],
  "js": ["theme.js"],
  "preview": "preview.svg",
  "i18n": {"he": {"name": "המתקן שלי", "description": "…"}}
}
```

| Key | Required | Meaning |
|---|---|---|
| `pyplchmi_template` | yes | Manifest format, always `1` |
| `id` | yes | 2–40 characters: `a-z`, `0-9`, `-`, `_`; starts with a letter or digit. Equals the folder name |
| `name` | yes | Display name (max 80 characters) |
| `version`, `author`, `description` | no | Shown on the admin page |
| `color_mode` | no | `user` (default: each user's Dark / Light / Automatic choice), `light` or `dark` (forced) |
| `features` | no | Interface variants, see below. Missing features keep their default |
| `css` | no | Stylesheets, loaded in this order **after** pyPLCHMI's own `hmi.css` |
| `js` | no | Scripts, loaded at the end of every page **after** pyPLCHMI's scripts |
| `preview` | no | Image for the admin page (16:9 looks best, e.g. 320×180) |
| `i18n` | no | Translated `name` / `description` per language code |

### Features

| Feature | Values (first = default) | Effect |
|---|---|---|
| `nav` | `topbar`, `sidebar` | Icon top bar, or a labelled sidebar with *Operate* / *Engineer* workspaces and the screen list with alarm badges (off-canvas on phones) |
| `home` | `screens`, `overview` | Where users land without a home screen: the screen list or the **plant overview** (live card per screen: alarms, PLC state, first three values) |
| `alarm_ui` | `popup`, `banner` | Alarm window that pops up, or the **alarm strip** under the top bar plus a side alarm list that never blocks the page |
| `tiles` | `classic`, `hp` | Grid screen tiles, or **high-performance** tiles: no PLC addresses, range bar with pointer (tags with min/max), one-hour trend for logged tags, alarm frame and badge, *Change…* button with a dialog instead of inline inputs |
| `tags_ui` | `classic`, `panel` | Tag table, or editing in a **side panel** next to the table plus a **bulk bar** (export, add to screen, delete) |
| `command_palette` | `false`, `true` | **Ctrl+K** quick search for screens, tags, PLCs and pages |

## 4. Styling

### Colour variables

Override them on `:root[data-bs-theme]` (more specific than the defaults, so it always wins).
To support both colour modes, use `:root[data-bs-theme="dark"]` and
`:root[data-bs-theme="light"]` separately.

| Variable | Used for |
|---|---|
| `--hmi-bg` | Page background |
| `--hmi-panel` | Cards, tiles, dialogs |
| `--hmi-border`, `--hmi-border-hover` | Borders |
| `--hmi-nav-bg` | Top bar and sidebar |
| `--hmi-inset` | Wells: bars, inputs, code |
| `--hmi-raised`, `--hmi-hover` | Raised areas, hover backgrounds |
| `--hmi-stage` | Background around image screens |
| `--hmi-text`, `--hmi-text-strong`, `--hmi-subtle`, `--hmi-muted`, `--hmi-dimmed` | Text levels |
| `--hmi-section` | Section headings |
| `--hmi-accent` | Accent (selected items, progress bars) |
| `--hmi-good`, `--hmi-bad`, `--hmi-warn` | Quality: good, bad / offline, reconnecting |
| `--hmi-lamp-off` | Lamps that are off |
| `--hmi-shadow`, `--hmi-tip-bg`, `--hmi-tip-fg` | Shadows and tooltips |
| `--hmi-chart-bg`, `--hmi-chart-text`, `--hmi-chart-grid`, `--hmi-chart-title` | Trend charts |

Bootstrap 5.3 variables (`--bs-body-font-family`, `--bs-link-color-rgb`, `--bs-btn-*` on
`.btn-primary`…) can be overridden too.

### Attributes for targeting

`<html>` carries the active template and its features, so one stylesheet can adapt:

```css
[data-ui-template="my-plant"] …
[data-ui-nav="sidebar"] …       [data-ui-alarms="banner"] …
[data-ui-tiles="hp"] …          [data-ui-tags="panel"] …       [data-ui-home="overview"] …
[data-bs-theme="light"] …       body.hmi-embed …  (screen in the full-screen frame)
```

### Stable classes

| Area | Classes |
|---|---|
| Top bar | `.hmi-navbar`, `.hmi-clock`, `.hmi-icon-nav`, `.hmi-screen-head` |
| Sidebar | `.hmi-sidebar`, `.hmi-sidebar-head`, `.hmi-ws-switch`, `.hmi-side-group`, `.hmi-side-link`, `.hmi-side-badge`, `.hmi-sidebar-user` |
| Grid screens | `.hmi-grid`, `.hmi-grid-hp`, `.hmi-tile` (`.bad`, `.stale`, `.in-alarm`, `.alarm-unacked`), `.hmi-label`, `.hmi-value`, `.hmi-big`, `.hmi-bar`, `.hmi-hp-range`, `.hmi-hp-pointer`, `.hmi-spark`, `.hmi-change` |
| Alarms | `.hmi-alarm-strip` (`[data-state="critical|high|low|acked|none"]`), `.hmi-strip-count`, `.hmi-alarm-drawer`, `.hmi-drawer-card`, `.alarm-window`, `.alarm-banner` |
| Overview | `.hmi-ov-grid`, `.hmi-ov-card` (`[data-state="normal|alarm|comm"]`), `.hmi-ov-chip`, `.hmi-ov-kpi` |
| Tags | `.hmi-tags-panel`, `.hmi-tag-panel`, `.hmi-bulk-bar` |
| Quick search | `.hmi-palette`, `.hmi-palette-item` |
| General | `.hmi-panel`, `.hmi-table` |

Reference your own files with relative URLs, e.g. `url("fonts/Inter.woff2")` – they are served
from `/ui-templates/<id>/…`. Bundle fonts: plant networks often have no internet access.

### Good HMI practice

* Keep colour for meaning: red/amber only for alarms and communication problems (ISA-101).
* Never show state by colour alone – the built-in strip and chips also use shapes and text.
* Test both colour modes (if `color_mode` is `user`), a phone width, right-to-left (Hebrew) and
  the full-screen mode of a screen.

## 5. JavaScript (optional)

Scripts run on every page, so keep them small and defensive (elements may be missing on the
login page or in full-screen frames). Useful hooks:

| Event / object | Detail |
|---|---|
| `document` event `hmi:alarms` | After every alarm poll: `{detail: {events: [...]}}` (id, name, severity, color, acked_at, screen_url, tag_id…) |
| `document` event `hmi:values` | After every value poll of a grid screen: `{detail: {values, plcs, timestamp}}` |
| `document` event `hmi:selection` | Tag/alarm table selection changed: `{detail: {ids}}` |
| `window.HMIDialog` | `confirm`, `alert`, `prompt`, `toast` (themed SweetAlert2 dialogs) |
| `window.T(text, …)` | Translate a text into the user's language |
| `window.HMIPalette.open()` | Open the quick search (when enabled) |

The admin page warns when a template contains JavaScript. Never send data to other servers.

## 6. Security model

* Only administrators (`manage_roles`) can upload, preview, activate, download or delete templates;
  every install, update, activation and deletion is in the audit log.
* Packages are validated before anything is written (paths, types, sizes, manifest).
* Template files are public (the login page is themed too) and served with
  `X-Content-Type-Options: nosniff`; SVG files get a Content-Security-Policy that blocks scripts.
  `template.json`, `.md` and `.txt` files are not served.
* Template JavaScript runs with the rights of the logged-in user, like pyPLCHMI's own scripts.
  Install templates only from developers you trust, and review their `.js` files.
* Uploaded templates are stored in `UI_TEMPLATES_FOLDER` (default `instance/ui_templates`) –
  back it up with the database and the uploads folder.

## 7. Checklist before you publish a template

1. `template.json` passes the upload (the page shows the exact error otherwise).
2. Preview it on: screen list, a grid screen, an image screen, the tag table, the alarm list,
   the login page, and a screen in full screen.
3. Try dark and light (if `color_mode` is `user`), a phone, and Hebrew (right to left).
4. Bump `version` for every release and upload with **Replace**.
