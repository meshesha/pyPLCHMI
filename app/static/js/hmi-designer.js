/**
 * pyPLCHMI graphic dashboard designer (admin only).
 *
 * An SVG editor for "Image" dashboards:
 *   - palette of shapes (building, floor, room, wall, zone, text), built-in
 *     symbols (hmi-symbols.js), uploaded images, PLC controls (value, lamp,
 *     button, bar) and the dashboard's PLC tags – add by drag & drop or click;
 *   - drawing tool for walls and pipes (click points, double-click/Enter to finish);
 *   - select / move / resize / rotate, vertex editing, snapping, zoom;
 *   - properties panel, layers list, undo/redo, copy/paste, keyboard shortcuts;
 *   - upload images (button or drop files on the canvas), create PLC tags inline
 *     or pick existing ones from the tag table (static/js/tag_picker.js);
 *   - Tabler icons (pytablericons) searched in the "Icons" palette;
 *   - multi-selection (Ctrl/Shift+click, Ctrl+A), right-click context menu,
 *     cut/copy/paste also between screens (clipboard kept in localStorage);
 *   - visibility rules per element (always / hidden / by tag);
 *   - export of the drawing as a standalone SVG file (with the screen data
 *     embedded so it can be imported again on the Screens page);
 *   - optional live preview of PLC values;
 *   - saves the layout JSON to /api/dashboards/<id>/layout (validated server-side).
 */
(function () {
  "use strict";

  const R = window.HMIRender;
  const SYMBOLS = window.HMI_SYMBOLS;
  const data = JSON.parse(document.getElementById("designer-data").textContent);
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

  const stage = document.getElementById("dz-stage");
  const svg = document.getElementById("dz-svg");
  const propsBox = document.getElementById("dz-props");
  const layersBox = document.getElementById("dz-layers");
  const statusBox = document.getElementById("dz-status");

  const state = {
    layout: data.layout,
    selected: null,       // primary selected element id (properties panel, handles)
    selection: [],        // all selected element ids (multi-selection)
    tool: null,           // {kind: "line"|"pipe", points: [[x,y],...]}
    drag: null,
    history: [],
    future: [],
    dirty: false,
    zoom: 1,
    snap: true,
    live: false,
    liveTimer: null,
    values: {},
    clipboard: null,
    nodes: {},
  };
  const ctx = { points: data.points, urls: data.urls, designer: true, icons: data.icons || {}, loadIcon: loadIcon };
  const iconRequests = {};

  /**
   * Fetch the shape of an icon once and redraw the elements that use it.
   * @param {string} style "outline" or "filled".
   * @param {string} name Icon name.
   * @returns {Promise<void>}
   */
  function loadIcon(style, name) {
    const key = style + "/" + name;
    if (ctx.icons[key]) return Promise.resolve();
    if (iconRequests[key]) return iconRequests[key];
    const url = data.urls.icon_shape.replace("__style__", encodeURIComponent(style)).replace("__name__", encodeURIComponent(name));
    iconRequests[key] = fetch(url, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((shape) => {
        if (!shape) return;
        ctx.icons[key] = shape;
        state.layout.elements.filter((e) => e.type === "icon" && (e.iconStyle || "outline") + "/" + e.icon === key)
          .forEach((e) => renderOne(e.id));
      })
      .catch(() => { /* placeholder stays */ });
    return iconRequests[key];
  }

  // ---------------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------------
  /** @returns {string} A new random element id. */
  function newId() {
    return "e" + Math.random().toString(36).slice(2, 10);
  }

  /** @returns {Object} Deep copy of a JSON-compatible value. */
  function clone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  /** @returns {Object|undefined} The layout element with the given id. */
  function find(id) {
    return state.layout.elements.find((e) => e.id === id);
  }

  /** @returns {boolean} true for polyline-based element types. */
  function isPoly(e) {
    return e.type === "line" || e.type === "pipe";
  }

  /**
   * Show a message in the status bar.
   * @param {string} msg Message.
   * @param {string} [kind] "ok" | "error" | "info".
   */
  function status(msg, kind) {
    statusBox.textContent = T(msg);
    statusBox.className = "dz-status " + (kind ? "dz-" + kind : "");
  }

  /**
   * Round a coordinate to the grid when snapping is active.
   * @param {number} v Coordinate.
   * @param {Event} [ev] Pointer event (Alt disables snapping).
   * @returns {number}
   */
  function snap(v, ev) {
    const g = state.layout.canvas.grid || 10;
    if (!state.snap || (ev && ev.altKey)) return Math.round(v * 10) / 10;
    return Math.round(v / g) * g;
  }

  /**
   * Convert a pointer event to canvas coordinates.
   * @param {MouseEvent} ev Event.
   * @returns {{x:number, y:number}}
   */
  function toCanvas(ev) {
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX;
    pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    return { x: p.x, y: p.y };
  }

  /** @returns {number} Screen pixels per canvas unit. */
  function pxPerUnit() {
    return svg.getBoundingClientRect().width / state.layout.canvas.width;
  }

  /**
   * POST/PUT JSON with the CSRF header.
   * @param {string} url URL.
   * @param {string} method HTTP method.
   * @param {Object} body JSON body.
   * @returns {Promise<{ok:boolean, status:number, json:Object}>}
   */
  async function sendJson(url, method, body) {
    const resp = await fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify(body),
    });
    const json = await resp.json().catch(() => ({}));
    return { ok: resp.ok, status: resp.status, json: json };
  }

  // ---------------------------------------------------------------------
  // History
  // ---------------------------------------------------------------------
  /** @returns {string} Serialised layout for undo/redo. */
  function snapshot() {
    return JSON.stringify(state.layout);
  }

  /**
   * Record a change: if the layout differs from `before`, push it to the undo stack.
   * @param {string} before Snapshot taken before the change.
   */
  function commit(before) {
    if (before === snapshot()) return;
    state.history.push(before);
    if (state.history.length > 200) state.history.shift();
    state.future = [];
    setDirty(true);
    renderLayers();
  }

  /** Mark the layout as (un)saved and update the Save button. */
  function setDirty(flag) {
    state.dirty = flag;
    document.getElementById("dz-save").classList.toggle("btn-warning", flag);
    document.getElementById("dz-save").classList.toggle("btn-primary", !flag);
  }

  /** Undo the last change. */
  function undo() {
    if (!state.history.length) return;
    state.future.push(snapshot());
    state.layout = JSON.parse(state.history.pop());
    state.selection = state.selection.filter((id) => find(id));
    if (state.selected && !find(state.selected)) state.selected = state.selection[state.selection.length - 1] || null;
    setDirty(true);
    renderAll();
  }

  /** Redo the last undone change. */
  function redo() {
    if (!state.future.length) return;
    state.history.push(snapshot());
    state.layout = JSON.parse(state.future.pop());
    setDirty(true);
    renderAll();
  }

  // ---------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------
  let contentSvg, gridRect, overlay, preview;

  /** Build the static structure of the editor SVG. */
  function buildSvg() {
    svg.replaceChildren();
    const defs = R.svgEl("defs", {}, svg);
    const pattern = R.svgEl("pattern", { id: "dz-grid", patternUnits: "userSpaceOnUse" }, defs);
    R.svgEl("path", { class: "dz-grid-line", fill: "none" }, pattern);
    contentSvg = R.svgEl("svg", { x: 0, y: 0, overflow: "visible" }, svg);
    gridRect = R.svgEl("rect", { x: 0, y: 0, fill: "url(#dz-grid)", "pointer-events": "none", class: "dz-grid" }, svg);
    overlay = R.svgEl("g", { class: "dz-overlay" }, svg);
    preview = R.svgEl("polyline", { class: "dz-preview", fill: "none" }, svg);
  }

  /**
   * Re-render canvas, selection, properties and layers.
   * @param {boolean} [keepPanels] Skip rebuilding the properties/layers panels
   *        (keeps keyboard focus while typing canvas settings).
   */
  /**
   * Show the page background of the screen (colour / image around the drawing) behind the stage.
   * @param {Object} c Canvas settings.
   */
  function applyPageBackground(c) {
    const color = c.pageBackground && c.pageBackground !== "none" ? c.pageBackground : "";
    stage.style.backgroundColor = color;
    if (c.pageBackgroundImage) {
      const fit = { cover: ["cover", "no-repeat", "center"], contain: ["contain", "no-repeat", "center"],
        stretch: ["100% 100%", "no-repeat", "0 0"], repeat: ["auto", "repeat", "0 0"], center: ["auto", "no-repeat", "center"] }[c.pageBackgroundFit || "cover"]
        || ["cover", "no-repeat", "center"];
      stage.style.backgroundImage = "url('" + R.urlFor(data.urls.asset, c.pageBackgroundImage) + "')";
      [stage.style.backgroundSize, stage.style.backgroundRepeat, stage.style.backgroundPosition] = fit;
    } else {
      stage.style.backgroundImage = "";
    }
  }

  function renderAll(keepPanels) {
    const c = state.layout.canvas;
    svg.setAttribute("viewBox", "0 0 " + c.width + " " + c.height);
    contentSvg.setAttribute("width", c.width);
    contentSvg.setAttribute("height", c.height);
    gridRect.setAttribute("width", c.width);
    gridRect.setAttribute("height", c.height);
    const g = c.grid || 10;
    const pattern = svg.querySelector("#dz-grid");
    const step = g < 8 ? g * Math.ceil(8 / g) : g;
    pattern.setAttribute("width", step);
    pattern.setAttribute("height", step);
    pattern.firstChild.setAttribute("d", "M " + step + " 0 L 0 0 0 " + step);
    gridRect.style.display = state.snap ? "" : "none";
    applyPageBackground(c);
    state.nodes = R.renderLayout(contentSvg, state.layout, ctx).nodes;
    if (state.live) R.applyValues(state.nodes, state.layout, state.values, ctx);
    applyZoom();
    drawSelection();
    if (keepPanels === true) return;
    renderProps();
    renderLayers();
  }

  /**
   * Re-render a single element after a property change.
   * @param {string} id Element id.
   */
  function renderOne(id) {
    const e = find(id);
    const old = state.nodes[id];
    if (!e || !old) return renderAll();
    const node = R.createNode(e, ctx);
    old.replaceWith(node);
    state.nodes[id] = node;
    if (state.live) R.applyValues({ [id]: node }, { elements: [e] }, state.values, ctx);
    drawSelection();
  }

  /** Draw the selection frame and handles (frames only when several elements are selected). */
  function drawSelection() {
    overlay.replaceChildren();
    if (state.selection.length > 1) {
      selectedEls().forEach((el) => {
        if (isPoly(el)) {
          R.svgEl("polyline", { class: "dz-sel-line", points: el.points.map((p) => p.join(",")).join(" "), fill: "none", "stroke-width": 1.5 / pxPerUnit() }, overlay);
          return;
        }
        let t = "translate(" + el.x + " " + el.y + ")";
        if (el.rot) t += " rotate(" + el.rot + " " + el.w / 2 + " " + el.h / 2 + ")";
        const gg = R.svgEl("g", { transform: t }, overlay);
        R.svgEl("rect", { class: "dz-sel" + (el.id === state.selected ? " dz-sel-primary" : ""), x: 0, y: 0, width: el.w, height: el.h, fill: "none", "stroke-width": 1.5 / pxPerUnit() }, gg);
      });
      return;
    }
    const e = state.selected && find(state.selected);
    if (!e) return;
    const hs = 9 / pxPerUnit();
    if (isPoly(e)) {
      R.svgEl("polyline", { class: "dz-sel-line", points: e.points.map((p) => p.join(",")).join(" "), fill: "none", "stroke-width": 1.5 / pxPerUnit() }, overlay);
      if (!e.locked) {
        e.points.forEach((p, i) => R.svgEl("circle", { class: "dz-handle", "data-v": i, cx: p[0], cy: p[1], r: hs * 0.6 }, overlay));
      }
      return;
    }
    let tf = "translate(" + e.x + " " + e.y + ")";
    if (e.rot) tf += " rotate(" + e.rot + " " + e.w / 2 + " " + e.h / 2 + ")";
    const g = R.svgEl("g", { transform: tf }, overlay);
    R.svgEl("rect", { class: "dz-sel", x: 0, y: 0, width: e.w, height: e.h, fill: "none", "stroke-width": 1.5 / pxPerUnit() }, g);
    if (e.locked) return;
    [["nw", 0, 0], ["ne", e.w, 0], ["sw", 0, e.h], ["se", e.w, e.h]].forEach(([h, x, y]) => {
      R.svgEl("rect", { class: "dz-handle dz-h-" + h, "data-h": h, x: x - hs / 2, y: y - hs / 2, width: hs, height: hs }, g);
    });
  }

  /** Apply the zoom factor to the editor SVG size. */
  function applyZoom() {
    const c = state.layout.canvas;
    svg.setAttribute("width", Math.round(c.width * state.zoom));
    svg.setAttribute("height", Math.round(c.height * state.zoom));
    document.getElementById("dz-zoom-label").textContent = Math.round(state.zoom * 100) + "%";
  }

  /**
   * Change the zoom level.
   * @param {number} z New zoom factor (clamped 0.1 … 5).
   */
  function setZoom(z) {
    state.zoom = Math.min(5, Math.max(0.1, z));
    applyZoom();
    drawSelection();
  }

  /** Zoom so the whole canvas fits in the stage. */
  function zoomFit() {
    const c = state.layout.canvas;
    const w = stage.clientWidth - 32, h = stage.clientHeight - 32;
    setZoom(Math.min(w / c.width, h / c.height));
  }

  // ---------------------------------------------------------------------
  // Element factories & palette
  // ---------------------------------------------------------------------
  const BOX_DEFAULTS = { rot: 0, opacity: 1, locked: false, name: "", link: null, faceplate: null };

  /**
   * Shape presets offered in the palette.
   * Each make(x, y) returns a new element centred on (x, y).
   */
  const ITEMS = {
    building: { label: "Building", icon: "bi-building", section: "Building",
      make: (x, y) => box("rect", x, y, 800, 500, { fill: "#1f2833", stroke: "#cfd8e3", strokeWidth: 6, rx: 0, label: T("Building"), labelPos: "top", labelSize: 22, labelColor: "#e6edf3", dash: false, point: null, onColor: "#c0392b", offColor: "#1f2833" }) },
    floor: { label: "Floor / zone", icon: "bi-layers", section: "Building",
      make: (x, y) => box("rect", x, y, 500, 300, { fill: "none", stroke: "#8b98a8", strokeWidth: 3, rx: 0, label: T("Floor"), labelPos: "top", labelSize: 18, labelColor: "#cfd8e3", dash: true, point: null, onColor: "#c0392b", offColor: "none" }) },
    room: { label: "Room", icon: "bi-square", section: "Building",
      make: (x, y) => box("rect", x, y, 240, 160, { fill: "#243040", stroke: "#8b98a8", strokeWidth: 3, rx: 0, label: T("Room"), labelPos: "center", labelSize: 16, labelColor: "#cfd8e3", dash: false, point: null, onColor: "#c0392b", offColor: "#243040" }) },
    zone: { label: "Round zone", icon: "bi-circle", section: "Building",
      make: (x, y) => box("ellipse", x, y, 180, 120, { fill: "#243040", stroke: "#8b98a8", strokeWidth: 2, label: "", labelPos: "center", labelSize: 16, labelColor: "#cfd8e3", dash: false, point: null, onColor: "#c0392b", offColor: "#243040" }) },
    wall: { label: "Wall (draw)", icon: "bi-slash-lg", section: "Building", tool: "line" },
    text: { label: "Text", icon: "bi-fonts", section: "Building",
      make: (x, y) => box("text", x, y, 200, 40, { text: T("Text"), color: "#e6edf3", size: 20, bold: false, italic: false, align: "start", font: "sans", bg: "none" }) },
    pipe: { label: "Pipe (draw)", icon: "bi-bezier2", section: "Piping", tool: "pipe" },
    value: { label: "PLC value", icon: "bi-123", section: "PLC controls",
      make: (x, y) => box("value", x, y, 160, 44, { point: null, prefix: "", color: "#e6edf3", size: 22, bold: true, bg: "#0d1015", border: "#2b3440", align: "middle", lo: null, hi: null, alarmColor: "#e74c3c" }) },
    lamp: { label: "Lamp", icon: "bi-circle-fill", section: "PLC controls",
      make: (x, y) => box("lamp", x, y, 36, 36, { point: null, onColor: "#2ecc71", offColor: "#3a434f", blink: false, stroke: "#0d1015" }) },
    button: { label: "Button", icon: "bi-hand-index", section: "PLC controls",
      make: (x, y) => box("button", x, y, 120, 44, { text: T("Start"), point: null, action: "toggle", value: "1", confirm: true, color: "#2fa4e7", textColor: "#ffffff", size: 16 }) },
    bar: { label: "Level bar", icon: "bi-bar-chart-fill", section: "PLC controls",
      make: (x, y) => box("bar", x, y, 40, 160, { point: null, min: 0, max: 100, color: "#2fa4e7", bg: "#0d1015", stroke: "#2b3440", orientation: "vertical" }) },
  };
  Object.entries(SYMBOLS).forEach(([key, def]) => {
    ITEMS["sym:" + key] = {
      label: def.name, symbol: key, section: def.category,
      make: (x, y) => box("symbol", x, y, 80, 80, { symbol: key, color: "#cfd8e3", point: null, onColor: "#2ecc71", offColor: "#5b6b7d",
        min: 0, max: 100, label: def.mode === "static" ? "" : T(def.name), labelPos: "bottom", labelSize: 14, labelColor: "#cfd8e3" }),
    };
  });

  /**
   * Create a box element centred on (cx, cy).
   * @param {string} type Element type.
   * @param {number} cx Centre x.
   * @param {number} cy Centre y.
   * @param {number} w Width.
   * @param {number} h Height.
   * @param {Object} props Type-specific properties.
   * @returns {Object} Element.
   */
  function box(type, cx, cy, w, h, props) {
    return { id: newId(), type: type, x: snap(cx - w / 2), y: snap(cy - h / 2), w: w, h: h, ...BOX_DEFAULTS, ...props };
  }

  /**
   * Create an image element for an asset, sized from the image's natural size.
   * @param {Object} asset Asset metadata.
   * @param {number} cx Centre x.
   * @param {number} cy Centre y.
   * @returns {Object} Element.
   */
  function makeImage(asset, cx, cy) {
    const el = box("image", cx, cy, 200, 150, { asset: asset.id, fit: "contain", name: asset.name });
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, 300 / Math.max(img.naturalWidth, img.naturalHeight));
      const w = Math.max(10, Math.round(img.naturalWidth * scale)), h = Math.max(10, Math.round(img.naturalHeight * scale));
      if (find(el.id) && el.w === 200 && el.h === 150) {
        el.x = snap(cx - w / 2); el.y = snap(cy - h / 2); el.w = w; el.h = h;
        renderOne(el.id);
        renderProps();
      }
    };
    img.src = asset.url;
    return el;
  }

  /**
   * Create a value display bound to a tag.
   * @param {Object} point Point metadata.
   * @param {number} cx Centre x.
   * @param {number} cy Centre y.
   * @returns {Object} Element.
   */
  function makeTagElement(point, cx, cy) {
    if (point.is_bool) return { ...ITEMS.lamp.make(cx, cy), point: point.id, name: point.label };
    return { ...ITEMS.value.make(cx, cy), point: point.id, name: point.label };
  }

  /**
   * Create a Tabler icon element.
   * @param {string} name Icon name.
   * @param {string} style "outline" or "filled".
   * @param {number} cx Centre x.
   * @param {number} cy Centre y.
   * @returns {Object} Element.
   */
  function makeIcon(name, style, cx, cy) {
    loadIcon(style || "outline", name);
    return box("icon", cx, cy, 64, 64, { icon: name, iconStyle: style || "outline", color: "#cfd8e3", strokeWidth: 2, blink: false,
      point: null, onColor: "#2ecc71", offColor: "#cfd8e3", label: "", labelPos: "bottom", labelSize: 14, labelColor: "#cfd8e3", name: name });
  }

  /**
   * Small SVG preview of an icon shape.
   * @param {Object} shape {viewBox, mode, nodes}.
   * @returns {SVGSVGElement}
   */
  function iconPreview(shape) {
    const s = R.svgEl("svg", { viewBox: shape.viewBox, class: "dz-item-icon dz-icon-preview" });
    const g = R.svgEl("g", shape.mode === "fill" ? { fill: "currentColor" }
      : { fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }, s);
    shape.nodes.forEach((n) => R.svgEl(n[0], n[1], g));
    return s;
  }

  /** Build the "Icons" palette section (search Tabler icons). */
  function buildIconSection(pal) {
    const sec = section("Icons", false);
    sec.body.className = "dz-icons";
    pal.appendChild(sec.root);
    if (!data.icons_available) {
      const note = document.createElement("div");
      note.className = "dz-help";
      note.textContent = T("Icons need the pytablericons package: pip install pytablericons");
      sec.body.appendChild(note);
      return;
    }
    const bar = document.createElement("div");
    bar.className = "d-flex gap-1 mb-1";
    const q = document.createElement("input");
    q.type = "search";
    q.id = "dz-icon-q";
    q.className = "form-control form-control-sm";
    q.placeholder = T("Search icons (fan, pump, bolt…)");
    const styleSel = document.createElement("select");
    styleSel.id = "dz-icon-style";
    styleSel.className = "form-select form-select-sm";
    styleSel.style.maxWidth = "5.5rem";
    [["", "all"], ["outline", "outline"], ["filled", "filled"]].forEach(([v, l]) => styleSel.appendChild(option(v, T(l), v === "")));
    bar.append(q, styleSel);
    const grid = document.createElement("div");
    grid.className = "dz-grid-items dz-icon-grid";
    grid.id = "dz-icon-results";
    const info = document.createElement("div");
    info.className = "dz-help mt-1";
    sec.body.append(bar, grid, info);
    let timer = null, seq = 0;
    async function run() {
      const my = ++seq;
      const url = new URL(data.urls.icons, window.location.origin);
      url.searchParams.set("q", q.value.trim());
      if (styleSel.value) url.searchParams.set("style", styleSel.value);
      url.searchParams.set("limit", "60");
      try {
        const resp = await fetch(url, { credentials: "same-origin" });
        const json = await resp.json();
        if (my !== seq) return;
        grid.replaceChildren();
        json.icons.forEach((ic) => {
          const key = ic.style + "/" + ic.name;
          ctx.icons[key] = ic.shape;
          const tile = document.createElement("div");
          tile.className = "dz-item dz-icon-tile";
          tile.title = ic.name + " (" + ic.style + (ic.category ? ", " + ic.category : "") + ")";
          tile.dataset.icon = key;
          const label = document.createElement("span");
          label.textContent = ic.name;
          tile.append(iconPreview(ic.shape), label);
          wireTile(tile, { icon: ic.name, style: ic.style }, () => { const c = viewCenter(); addElement(makeIcon(ic.name, ic.style, c.x, c.y)); });
          grid.appendChild(tile);
        });
        info.textContent = json.total > json.icons.length ? T("{0} of {1} – refine the search", json.icons.length, json.total) : T("{0} icon(s)", json.total);
      } catch (err) {
        info.textContent = T("Icon search failed: {0}", err.message);
      }
    }
    q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 250); });
    styleSel.addEventListener("change", run);
    sec.root.addEventListener("toggle", () => { if (sec.root.open && !grid.children.length) run(); });
  }

  /**
   * Add a new element to the layout and select it.
   * @param {Object} el Element.
   */
  function addElement(el) {
    const before = snapshot();
    state.layout.elements.push(el);
    state.selected = el.id;
    state.selection = [el.id];
    renderAll();
    commit(before);
  }

  /** @returns {{x:number, y:number}} Canvas coordinates of the visible stage centre. */
  function viewCenter() {
    const r = stage.getBoundingClientRect();
    return toCanvas({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
  }

  /**
   * Place a palette item (or start its drawing tool).
   * @param {string} key ITEMS key.
   * @param {{x:number,y:number}} [at] Canvas position; defaults to the view centre.
   */
  function placeItem(key, at) {
    const item = ITEMS[key];
    if (!item) return;
    if (item.tool) { startTool(item.tool); return; }
    const p = at || viewCenter();
    addElement(item.make(p.x, p.y));
  }

  /**
   * Small preview icon for a palette entry.
   * @param {Object} item ITEMS entry.
   * @returns {Element}
   */
  function itemIcon(item) {
    if (item.symbol) {
      const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      s.setAttribute("viewBox", "-4 -4 108 108");
      s.setAttribute("class", "dz-item-icon hmi-sym");
      s.style.setProperty("--sym-line", "#cfd8e3");
      s.style.setProperty("--sym-accent", "#2ecc71");
      s.innerHTML = SYMBOLS[item.symbol].svg; // trusted constant markup
      return s;
    }
    const i = document.createElement("i");
    i.className = "bi " + item.icon + " dz-item-icon";
    return i;
  }

  /**
   * Make a palette tile draggable and clickable.
   * @param {HTMLElement} tile Tile.
   * @param {Object} payload Drag payload.
   * @param {Function} onClick Click handler.
   */
  function wireTile(tile, payload, onClick) {
    tile.draggable = true;
    tile.addEventListener("dragstart", (ev) => {
      ev.dataTransfer.setData("application/x-hmi", JSON.stringify(payload));
      ev.dataTransfer.effectAllowed = "copy";
    });
    tile.addEventListener("click", onClick);
  }

  /**
   * Create a collapsible palette section.
   * @param {string} title Section title.
   * @param {boolean} open Initially open.
   * @returns {{root: HTMLElement, body: HTMLElement}}
   */
  function section(title, open) {
    const det = document.createElement("details");
    det.className = "dz-section";
    det.open = open;
    const sum = document.createElement("summary");
    sum.textContent = T(title);
    det.appendChild(sum);
    const body = document.createElement("div");
    body.className = "dz-grid-items";
    det.appendChild(body);
    return { root: det, body: body };
  }

  /** Build the palette (shapes, symbols, controls, tags, images). */
  function buildPalette() {
    const pal = document.getElementById("dz-palette");
    pal.replaceChildren();
    const order = ["Building", "Piping", "HVAC", "Electrical", "Sensors", "PLC controls"];
    const sections = {};
    order.forEach((name) => {
      sections[name] = section(name, true);
      pal.appendChild(sections[name].root);
    });
    Object.entries(ITEMS).forEach(([key, item]) => {
      const sec = sections[item.section] || sections.Building;
      const tile = document.createElement("div");
      tile.className = "dz-item";
      tile.title = item.tool ? T("{0} – click, then click points on the canvas; double-click to finish", T(item.label)) : T(item.label);
      tile.appendChild(itemIcon(item));
      const span = document.createElement("span");
      span.textContent = T(item.label);
      tile.appendChild(span);
      wireTile(tile, { item: key }, () => placeItem(key));
      sec.body.appendChild(tile);
    });

    buildIconSection(pal);

    // PLC tags
    const tags = section("PLC tags", true);
    tags.body.className = "dz-list";
    tags.body.id = "dz-tags";
    pal.appendChild(tags.root);
    renderTags();

    // Images
    const imgs = section("Images", true);
    imgs.body.id = "dz-images";
    pal.appendChild(imgs.root);
    renderImages();
  }

  /** Fill the "PLC tags" palette list. */
  function renderTags() {
    const box = document.getElementById("dz-tags");
    box.replaceChildren();
    Object.values(data.points).forEach((p) => {
      const row = document.createElement("div");
      row.className = "dz-tag";
      row.title = T("Drag onto the canvas to show this tag");
      const name = document.createElement("strong");
      name.textContent = p.label;
      const addr = document.createElement("small");
      addr.textContent = p.plc + " · " + p.address + " · " + p.data_type + (p.writable ? " · W" : "");
      row.append(name, addr);
      wireTile(row, { point: p.id }, () => addElement(makeTagElement(p, viewCenter().x, viewCenter().y)));
      box.appendChild(row);
    });
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-primary w-100 mt-1";
    btn.innerHTML = '<i class="bi bi-plus-lg"></i> ';
    btn.append(T("New tag"));
    btn.title = T("Create a new tag in the tag table and use it on this screen");
    btn.addEventListener("click", () => openTagModal(null));
    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "btn btn-sm btn-outline-info w-100 mt-1";
    pick.id = "dz-existing-tag";
    pick.innerHTML = '<i class="bi bi-tags"></i> ';
    pick.append(T("Existing tag"));
    pick.title = T("Use tags from the tag table on this screen");
    pick.addEventListener("click", () => pickExistingTags(null));
    box.append(btn, pick);
  }

  /**
   * Choose tags from the tag table and link them to this screen.
   * With a callback, one tag is chosen and passed to it (e.g. to bind it to
   * the selected element's Tag or Visibility tag).
   * @param {?function(number)} onChosen Receives the chosen tag id.
   */
  function pickExistingTags(onChosen) {
    if (!window.HMITagPicker) return;
    window.HMITagPicker.open({
      screenId: data.dashboard.id,
      multiple: !onChosen,
      title: onChosen ? T("Choose a tag") : T("Add existing tags"),
      okText: onChosen ? T("Use tag") : T("Add to screen"),
      onPick: async (tags) => {
        const added = [];
        for (const t of tags) {
          const res = await sendJson(data.urls.add_tag, "POST", { datapoint_id: t.id });
          if (!res.ok) { status(T("Could not add '{0}': {1}", t.label, res.json.error || "HTTP " + res.status), "error"); continue; }
          data.points[String(res.json.id)] = res.json;
          added.push(res.json);
        }
        renderTags();
        if (onChosen && added.length) {
          const before = snapshot();
          onChosen(added[0].id);
          commit(before);
        }
        renderProps();
        if (added.length) status(T("{0} tag(s) added to this screen – drag them onto the canvas", added.length), "ok");
      },
    });
  }

  /** Image library folder shown in the palette ("" = all folders). */
  let imageFolder = (() => { try { return localStorage.getItem("pyplchmi-image-folder") || ""; } catch (e) { return ""; } })();

  /**
   * List the library folders that contain images (parents included).
   * @returns {string[]} Sorted folder paths.
   */
  function imageFolders() {
    const set = new Set();
    data.assets.forEach((a) => {
      const parts = String(a.folder || a.category || "Images").split("/");
      parts.forEach((_, i) => set.add(parts.slice(0, i + 1).join("/")));
    });
    return Array.from(set).sort((x, y) => x.localeCompare(y, undefined, { sensitivity: "base" }));
  }

  /** Fill the "Images" palette: folder selector and the images of that folder (and its sub-folders). */
  function renderImages() {
    const box = document.getElementById("dz-images");
    box.replaceChildren();
    const folders = imageFolders();
    if (imageFolder && !folders.includes(imageFolder)) imageFolder = "";
    const sel = document.createElement("select");
    sel.className = "form-select form-select-sm dz-image-folder";
    sel.id = "dz-image-folder";
    sel.title = T("Image library folder");
    sel.appendChild(option("", T("All folders ({0})", data.assets.length), imageFolder === ""));
    folders.forEach((f) => {
      const depth = f.split("/").length - 1;
      const n = data.assets.filter((a) => { const c = a.folder || a.category; return c === f || c.startsWith(f + "/"); }).length;
      sel.appendChild(option(f, "\u00a0\u00a0".repeat(depth) + f.split("/").pop() + " (" + n + ")", imageFolder === f));
    });
    sel.addEventListener("change", () => {
      imageFolder = sel.value;
      try { localStorage.setItem("pyplchmi-image-folder", imageFolder); } catch (e) { /* storage unavailable */ }
      renderImages();
    });
    box.appendChild(sel);
    data.assets.forEach((a) => {
      const folder = a.folder || a.category || "";
      if (imageFolder && folder !== imageFolder && !folder.startsWith(imageFolder + "/")) return;
      const tile = document.createElement("div");
      tile.className = "dz-item";
      tile.title = a.name + " (" + folder + ")";
      const img = document.createElement("img");
      img.src = a.url;
      img.alt = a.name;
      img.className = "dz-item-icon";
      img.draggable = false;
      const span = document.createElement("span");
      span.textContent = a.name;
      tile.append(img, span);
      wireTile(tile, { asset: a.id }, () => { const c = viewCenter(); addElement(makeImage(a, c.x, c.y)); });
      box.appendChild(tile);
    });
    const up = document.createElement("button");
    up.type = "button";
    up.className = "btn btn-sm btn-outline-primary dz-upload-btn";
    up.innerHTML = '<i class="bi bi-upload"></i> Upload image';
    up.title = T("Upload to {0}", imageFolder || "Images");
    up.addEventListener("click", () => document.getElementById("dz-file").click());
    box.appendChild(up);
  }

  // ---------------------------------------------------------------------
  // Uploads and tags
  // ---------------------------------------------------------------------
  /**
   * Upload image files to the asset library.
   * @param {FileList|File[]} files Files.
   * @returns {Promise<Object[]>} Created assets.
   */
  async function upload(files) {
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("file", f));
    form.append("folder", imageFolder || "Images");
    status("Uploading…", "info");
    const resp = await fetch(data.urls.upload, {
      method: "POST", credentials: "same-origin", body: form,
      headers: { "X-CSRFToken": csrfToken, Accept: "application/json" },
    });
    const json = await resp.json().catch(() => ({ errors: ["HTTP " + resp.status] }));
    (json.assets || []).forEach((a) => data.assets.push(a));
    renderImages();
    if (json.errors && json.errors.length) status(json.errors.join(" | "), "error");
    else status(T("{0} image(s) uploaded", (json.assets || []).length), "ok");
    renderProps();
    return json.assets || [];
  }

  let tagTarget = null;

  /**
   * Open the "New tag" dialog.
   * @param {?function(number)} onCreated Receives the new tag id (e.g. to bind it to an element).
   */
  function openTagModal(onCreated) {
    if (!data.plcs.length) { status("No enabled PLC – add or enable one first (Admin → PLCs).", "error"); return; }
    tagTarget = onCreated;
    const form = document.getElementById("tag-form");
    form.reset();
    syncTagProtocol();
    document.getElementById("tag-errors").textContent = "";
    bootstrap.Modal.getOrCreateInstance(document.getElementById("tag-modal")).show();
  }

  /** Update the "New tag" address placeholder for the selected PLC's protocol. */
  function syncTagProtocol() {
    const form = document.getElementById("tag-form");
    const plc = data.plcs.find((p) => String(p.id) === form.plc_id.value);
    const proto = plc ? String(plc.protocol || "s7") : "s7";
    form.address.placeholder = proto.startsWith("modbus") ? "HR100, HR10.3, C5, DI2, IR7, 40001…"
      : proto === "bacnet_ip" ? "AV3, AI1, BO12, MV2, AV5@10…" : "DB1.DBD4, M10.5, IW64…";
    // Dummy PLC tags have no address, but a kind (general / local).
    form.address.parentElement.hidden = proto === "dummy";
    form.address.required = proto !== "dummy";
    if (proto === "dummy") form.address.value = "";
    document.getElementById("tag-scope-box").hidden = proto !== "dummy";
  }
  document.getElementById("tag-form").plc_id.addEventListener("change", syncTagProtocol);
  syncTagProtocol();

  /** Submit the "New tag" dialog to the API. */
  async function submitTag(ev) {
    ev.preventDefault();
    const f = ev.target;
    const body = {
      label: f.label.value, plc_id: f.plc_id.value, address: f.address.value, data_type: f.data_type.value,
      string_length: f.string_length.value, unit: f.unit.value, decimals: f.decimals.value,
      scale: f.scale.value, offset: f.offset.value, writable: f.writable.checked,
      true_text: f.true_text.value, false_text: f.false_text.value, dummy_scope: f.dummy_scope.value,
    };
    const res = await sendJson(data.urls.create_point, "POST", body);
    if (!res.ok) {
      const errs = res.json.errors ? Object.entries(res.json.errors).map(([k, v]) => k + ": " + v.join(", ")) : [res.json.error || "HTTP " + res.status];
      document.getElementById("tag-errors").textContent = errs.join(" · ");
      return;
    }
    const p = res.json;
    data.points[String(p.id)] = p;
    renderTags();
    bootstrap.Modal.getInstance(document.getElementById("tag-modal")).hide();
    if (tagTarget) {
      const before = snapshot();
      tagTarget(p.id);
      commit(before);
      tagTarget = null;
    }
    renderProps();
    status(T("Tag '{0}' created ({1})", p.label, p.address), "ok");
  }

  // ---------------------------------------------------------------------
  // Drawing tool (walls, pipes)
  // ---------------------------------------------------------------------
  /**
   * Start drawing a polyline.
   * @param {string} kind "line" or "pipe".
   */
  function startTool(kind) {
    state.tool = { kind: kind, points: [] };
    state.selected = null;
    state.selection = [];
    drawSelection();
    renderProps();
    stage.classList.add("dz-drawing");
    status(kind === "pipe" ? T("Drawing pipe: click to add points, double-click or Enter to finish, Esc to cancel.") : T("Drawing wall: click to add points, double-click or Enter to finish, Esc to cancel."), "info");
  }

  /** Finish the polyline being drawn (needs ≥ 2 points). */
  function finishTool() {
    const t = state.tool;
    if (!t) return;
    state.tool = null;
    stage.classList.remove("dz-drawing");
    preview.setAttribute("points", "");
    const pts = t.points.filter((p, i, arr) => i === 0 || p[0] !== arr[i - 1][0] || p[1] !== arr[i - 1][1]);
    if (pts.length < 2) { status("Drawing cancelled", "info"); return; }
    const el = t.kind === "pipe"
      ? { id: newId(), type: "pipe", points: pts, stroke: "#7f8c8d", strokeWidth: 14, flowColor: "#3498db", point: null, ...BOX_DEFAULTS }
      : { id: newId(), type: "line", points: pts, stroke: "#cfd8e3", strokeWidth: 8, dash: false, point: null, onColor: "#2ecc71", offColor: "#cfd8e3", ...BOX_DEFAULTS };
    addElement(el);
    status(t.kind === "pipe" ? T("Pipe added") : T("Wall added"), "ok");
  }

  /** Cancel the drawing tool. */
  function cancelTool() {
    state.tool = null;
    stage.classList.remove("dz-drawing");
    preview.setAttribute("points", "");
    status("", "");
  }

  /**
   * Update the drawing preview line.
   * @param {{x:number,y:number}} [cursor] Current cursor position.
   */
  function updatePreview(cursor) {
    const t = state.tool;
    const pts = t.points.slice();
    if (cursor) pts.push([cursor.x, cursor.y]);
    preview.setAttribute("points", pts.map((p) => p.join(",")).join(" "));
    preview.setAttribute("stroke-width", t.kind === "pipe" ? 14 : 8);
  }

  /**
   * Snap a drawing point; with Shift, force horizontal/vertical segments.
   * @param {{x:number,y:number}} p Raw point.
   * @param {MouseEvent} ev Event.
   * @returns {{x:number,y:number}}
   */
  function drawPoint(p, ev) {
    let x = snap(p.x, ev), y = snap(p.y, ev);
    const pts = state.tool.points;
    if (ev.shiftKey && pts.length) {
      const last = pts[pts.length - 1];
      if (Math.abs(x - last[0]) > Math.abs(y - last[1])) y = last[1]; else x = last[0];
    }
    return { x: x, y: y };
  }

  // ---------------------------------------------------------------------
  // Pointer interaction
  // ---------------------------------------------------------------------
  svg.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    const p = toCanvas(ev);
    if (state.tool) {
      const dp = drawPoint(p, ev);
      state.tool.points.push([dp.x, dp.y]);
      updatePreview();
      return;
    }
    const handle = ev.target.closest("[data-h],[data-v]");
    const sel = state.selected && find(state.selected);
    if (handle && sel) {
      state.drag = { mode: handle.dataset.h ? "resize" : "vertex", handle: handle.dataset.h, vertex: Number(handle.dataset.v),
        start: p, orig: clone(sel), before: snapshot() };
      svg.setPointerCapture(ev.pointerId);
      return;
    }
    const g = ev.target.closest(".hmi-el");
    if (g) {
      const id = g.dataset.id;
      if (ev.ctrlKey || ev.metaKey || ev.shiftKey) { select(id, true); return; }
      if (state.selection.includes(id)) { state.selected = id; renderProps(); drawSelection(); }
      else select(id);
      const movable = selectedEls().filter((el) => !el.locked);
      if (movable.length) {
        state.drag = { mode: "move", start: p, origs: movable.map(clone), before: snapshot() };
        svg.setPointerCapture(ev.pointerId);
      }
      return;
    }
    select(null);
  });

  svg.addEventListener("pointermove", (ev) => {
    const p = toCanvas(ev);
    document.getElementById("dz-coords").textContent = Math.round(p.x) + ", " + Math.round(p.y);
    if (state.tool) { updatePreview(drawPoint(p, ev)); return; }
    const d = state.drag;
    if (!d) return;
    const dx = p.x - d.start.x, dy = p.y - d.start.y;
    if (d.mode === "move") {
      // Move every selected element by the snapped offset of the primary one.
      const prim = d.origs.find((o) => o.id === state.selected) || d.origs[0];
      let mx, my;
      if (isPoly(prim)) { mx = snap(dx, ev); my = snap(dy, ev); }
      else { mx = snap(prim.x + dx, ev) - prim.x; my = snap(prim.y + dy, ev) - prim.y; }
      d.origs.forEach((o) => {
        const el = find(o.id);
        if (!el) return;
        if (isPoly(o)) { el.points = o.points.map((pt) => [pt[0] + mx, pt[1] + my]); updateBBox(el); }
        else { el.x = o.x + mx; el.y = o.y + my; }
        renderOne(el.id);
      });
      return;
    }
    const e = find(state.selected);
    if (!e) return;
    if (d.mode === "vertex") {
      e.points[d.vertex] = [snap(d.orig.points[d.vertex][0] + dx, ev), snap(d.orig.points[d.vertex][1] + dy, ev)];
      updateBBox(e);
    } else if (d.mode === "resize") {
      resize(e, d, dx, dy, ev);
    }
    renderOne(e.id);
  });

  /** End a drag and record it in the history. */
  function endDrag() {
    if (!state.drag) return;
    const before = state.drag.before;
    state.drag = null;
    commit(before);
    renderProps();
  }
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);

  svg.addEventListener("dblclick", (ev) => {
    if (state.tool) { finishTool(); return; }
    const e = state.selected && find(state.selected);
    if (e && isPoly(e) && !e.locked) insertVertex(e, toCanvas(ev));
  });

  /**
   * Resize a box element by dragging a corner handle.
   * @param {Object} e Element (modified in place).
   * @param {Object} d Drag state.
   * @param {number} dx Pointer delta x (canvas units).
   * @param {number} dy Pointer delta y.
   * @param {MouseEvent} ev Event (Shift keeps aspect ratio, Alt disables snap).
   */
  function resize(e, d, dx, dy, ev) {
    const o = d.orig, h = d.handle;
    const rad = -(o.rot || 0) * Math.PI / 180;
    const lx = dx * Math.cos(rad) - dy * Math.sin(rad);
    const ly = dx * Math.sin(rad) + dy * Math.cos(rad);
    let w = o.w + (h.includes("e") ? lx : -lx);
    let hh = o.h + (h.includes("s") ? ly : -ly);
    if (!o.rot) {
      // Snap the moving edges to the grid when not rotated.
      if (h.includes("e")) w = snap(o.x + w, ev) - o.x; else w = o.x + o.w - snap(o.x + o.w - w, ev);
      if (h.includes("s")) hh = snap(o.y + hh, ev) - o.y; else hh = o.y + o.h - snap(o.y + o.h - hh, ev);
    }
    if (ev.shiftKey) {
      const ratio = o.w / o.h;
      if (Math.abs(w / o.w) > Math.abs(hh / o.h)) hh = w / ratio; else w = hh * ratio;
    }
    w = Math.max(4, w);
    hh = Math.max(4, hh);
    e.w = Math.round(w * 10) / 10;
    e.h = Math.round(hh * 10) / 10;
    e.x = h.includes("w") ? o.x + (o.w - e.w) : o.x;
    e.y = h.includes("n") ? o.y + (o.h - e.h) : o.y;
  }

  /**
   * Recompute x/y/w/h of a polyline from its points.
   * @param {Object} e Polyline element.
   */
  function updateBBox(e) {
    const xs = e.points.map((p) => p[0]), ys = e.points.map((p) => p[1]);
    e.x = Math.min(...xs); e.y = Math.min(...ys);
    e.w = Math.max(1, Math.max(...xs) - e.x); e.h = Math.max(1, Math.max(...ys) - e.y);
  }

  /**
   * Insert a vertex into the polyline segment closest to p.
   * @param {Object} e Polyline element.
   * @param {{x:number,y:number}} p Canvas point.
   */
  function insertVertex(e, p) {
    let best = -1, bestDist = Infinity;
    for (let i = 0; i < e.points.length - 1; i++) {
      const [x1, y1] = e.points[i], [x2, y2] = e.points[i + 1];
      const len2 = (x2 - x1) ** 2 + (y2 - y1) ** 2 || 1;
      const t = Math.max(0, Math.min(1, ((p.x - x1) * (x2 - x1) + (p.y - y1) * (y2 - y1)) / len2));
      const dist = Math.hypot(p.x - (x1 + t * (x2 - x1)), p.y - (y1 + t * (y2 - y1)));
      if (dist < bestDist) { bestDist = dist; best = i; }
    }
    const before = snapshot();
    e.points.splice(best + 1, 0, [snap(p.x), snap(p.y)]);
    updateBBox(e);
    renderOne(e.id);
    commit(before);
    status("Point added – Alt+click a point to remove it", "info");
  }

  // Alt+click on a vertex handle removes it.
  svg.addEventListener("click", (ev) => {
    const handle = ev.target.closest("[data-v]");
    const e = state.selected && find(state.selected);
    if (!handle || !ev.altKey || !e || !isPoly(e) || e.points.length <= 2) return;
    const before = snapshot();
    e.points.splice(Number(handle.dataset.v), 1);
    updateBBox(e);
    renderOne(e.id);
    commit(before);
  });

  // Drag & drop from palette or from the file system
  stage.addEventListener("dragover", (ev) => { ev.preventDefault(); ev.dataTransfer.dropEffect = "copy"; });
  stage.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    const p = toCanvas(ev);
    if (ev.dataTransfer.files && ev.dataTransfer.files.length) {
      const assets = await upload(ev.dataTransfer.files);
      assets.forEach((a, i) => addElement(makeImage(a, p.x + i * 30, p.y + i * 30)));
      return;
    }
    let payload;
    try { payload = JSON.parse(ev.dataTransfer.getData("application/x-hmi")); } catch (err) { return; }
    if (payload.item) placeItem(payload.item, p);
    else if (payload.point) addElement(makeTagElement(data.points[String(payload.point)], p.x, p.y));
    else if (payload.asset) addElement(makeImage(data.assets.find((a) => a.id === payload.asset), p.x, p.y));
    else if (payload.icon) addElement(makeIcon(payload.icon, payload.style, p.x, p.y));
  });

  stage.addEventListener("wheel", (ev) => {
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    setZoom(state.zoom * (ev.deltaY < 0 ? 1.1 : 1 / 1.1));
  }, { passive: false });

  // ---------------------------------------------------------------------
  // Selection & editing commands
  // ---------------------------------------------------------------------
  /**
   * Select an element (or nothing).
   * @param {?string} id Element id.
   * @param {boolean} [additive] Add/remove the element to/from the selection (Ctrl/Shift+click).
   */
  function select(id, additive) {
    if (additive && id) {
      const i = state.selection.indexOf(id);
      if (i >= 0) state.selection.splice(i, 1); else state.selection.push(id);
      state.selected = state.selection.includes(id) ? id : (state.selection[state.selection.length - 1] || null);
    } else {
      state.selection = id ? [id] : [];
      state.selected = id;
    }
    drawSelection();
    renderProps();
    renderLayers();
  }

  /** @returns {Object[]} The selected elements in drawing order. */
  function selectedEls() {
    return state.layout.elements.filter((e) => state.selection.includes(e.id));
  }

  /** Select every element. */
  function selectAll() {
    state.selection = state.layout.elements.map((e) => e.id);
    state.selected = state.selection[state.selection.length - 1] || null;
    drawSelection();
    renderProps();
    renderLayers();
  }

  /** Delete the selected elements. */
  function removeSelected() {
    if (!state.selection.length) return;
    const before = snapshot();
    state.layout.elements = state.layout.elements.filter((e) => !state.selection.includes(e.id));
    state.selected = null;
    state.selection = [];
    renderAll();
    commit(before);
  }

  const CLIP_KEY = "pyplchmi-clipboard";

  /** Copy the selection to the clipboard (also kept in localStorage for other screens). */
  function copySelection() {
    const els = selectedEls();
    if (!els.length) return false;
    const points = {};
    els.forEach((e) => ["point", "visPoint"].forEach((k) => { if (e[k] && data.points[e[k]]) points[e[k]] = data.points[e[k]]; }));
    state.clipboard = { elements: clone(els), points: points, screen: data.dashboard.id };
    try { localStorage.setItem(CLIP_KEY, JSON.stringify(state.clipboard)); } catch (err) { /* storage unavailable */ }
    status(T("{0} element(s) copied", els.length), "ok");
    return true;
  }

  /** Cut = copy + delete. */
  function cutSelection() {
    if (copySelection()) removeSelected();
  }

  /** @returns {?Object} Clipboard content (this tab, or copied in another designer tab). */
  function readClipboard() {
    try {
      const stored = JSON.parse(localStorage.getItem(CLIP_KEY) || "null");
      if (stored && stored.elements) return stored;
    } catch (err) { /* ignore */ }
    return state.clipboard;
  }

  /**
   * Insert copies of elements; keeps their relative positions.
   * @param {Object[]} elements Elements to copy.
   * @param {?{x:number,y:number}} at Top-left of the pasted group (default: offset by two grid steps).
   * @returns {Object[]} The inserted elements.
   */
  function insertCopies(elements, at) {
    if (!elements.length) return [];
    const before = snapshot();
    const boxes = elements.map((e) => (isPoly(e) ? { x: Math.min(...e.points.map((p) => p[0])), y: Math.min(...e.points.map((p) => p[1])) } : e));
    const minX = Math.min(...boxes.map((b) => b.x)), minY = Math.min(...boxes.map((b) => b.y));
    const off = (state.layout.canvas.grid || 10) * 2;
    const dx = at ? snap(at.x) - minX : off, dy = at ? snap(at.y) - minY : off;
    const copies = elements.map((src) => {
      const el = clone(src);
      el.id = newId();
      if (isPoly(el)) { el.points = el.points.map((p) => [p[0] + dx, p[1] + dy]); updateBBox(el); }
      else { el.x += dx; el.y += dy; }
      el.locked = false;
      return el;
    });
    state.layout.elements.push(...copies);
    state.selection = copies.map((e) => e.id);
    state.selected = state.selection[state.selection.length - 1];
    renderAll();
    commit(before);
    return copies;
  }

  /**
   * Paste the clipboard.  Tags of elements copied on another screen are
   * added to this screen first (or the binding is removed if that fails).
   * @param {?{x:number,y:number}} at Paste position (context menu) or null.
   */
  async function pasteClipboard(at) {
    const clip = readClipboard();
    if (!clip || !clip.elements || !clip.elements.length) { status("Nothing to paste", "info"); return; }
    const elements = clone(clip.elements);
    const needed = new Set();
    elements.forEach((e) => ["point", "visPoint"].forEach((k) => { if (e[k] && !data.points[e[k]]) needed.add(e[k]); }));
    for (const id of needed) {
      const res = await sendJson(data.urls.add_tag, "POST", { datapoint_id: id });
      if (res.ok) data.points[String(res.json.id)] = res.json;
    }
    if (needed.size) renderTags();
    elements.forEach((e) => ["point", "visPoint"].forEach((k) => { if (e[k] && !data.points[e[k]]) e[k] = null; }));
    insertCopies(elements, at);
    status(needed.size ? T("{0} element(s) pasted – {1} tag(s) added to this screen", elements.length, needed.size) : T("{0} element(s) pasted", elements.length), "ok");
  }

  /** Duplicate the selection. */
  function duplicateSelection() {
    insertCopies(selectedEls(), null);
  }

  /** Backward-compatible single-element paste (used by older callers). */
  function pasteElement(src) {
    insertCopies([src], null);
  }

  /**
   * Change the drawing order of the selected elements.
   * @param {string} where "front" | "back" | "forward" | "backward".
   */
  function reorder(where) {
    if (!state.selection.length) return;
    const before = snapshot();
    const els = state.layout.elements;
    const chosen = els.filter((e) => state.selection.includes(e.id));
    const rest = els.filter((e) => !state.selection.includes(e.id));
    if (where === "front") state.layout.elements = rest.concat(chosen);
    else if (where === "back") state.layout.elements = chosen.concat(rest);
    else {
      const list = els.slice();
      const order = where === "forward" ? chosen.slice().reverse() : chosen;
      order.forEach((el) => {
        const i = list.indexOf(el);
        const j = where === "forward" ? Math.min(list.length - 1, i + 1) : Math.max(0, i - 1);
        if (i !== j && !state.selection.includes(list[j].id)) { list.splice(i, 1); list.splice(j, 0, el); }
      });
      state.layout.elements = list;
    }
    renderAll();
    commit(before);
  }

  /**
   * Move the selected elements with the keyboard.
   * @param {number} dx Delta x.
   * @param {number} dy Delta y.
   */
  function nudge(dx, dy) {
    const els = selectedEls().filter((e) => !e.locked);
    if (!els.length) return;
    const before = snapshot();
    els.forEach((e) => {
      if (isPoly(e)) { e.points = e.points.map((p) => [p[0] + dx, p[1] + dy]); updateBBox(e); }
      else { e.x += dx; e.y += dy; }
      renderOne(e.id);
    });
    commit(before);
    renderProps();
  }

  /** Rotate the selected box elements by 90°. */
  function rotate90() {
    const els = selectedEls().filter((e) => !isPoly(e));
    if (!els.length) return;
    const before = snapshot();
    els.forEach((e) => {
      e.rot = ((e.rot || 0) + 90) % 360;
      if (e.rot > 180) e.rot -= 360;
      renderOne(e.id);
    });
    commit(before);
    renderProps();
  }

  /**
   * Set a property on every selected element.
   * @param {string} prop Property.
   * @param {*} value New value.
   */
  function setOnSelection(prop, value) {
    const els = selectedEls();
    if (!els.length) return;
    const before = snapshot();
    els.forEach((e) => { e[prop] = value; renderOne(e.id); });
    commit(before);
    renderProps();
    renderLayers();
  }

  document.addEventListener("keydown", (ev) => {
    const tag = (ev.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select"].includes(tag) || document.querySelector(".modal.show")) return;
    const mod = ev.ctrlKey || ev.metaKey;
    const step = ev.shiftKey ? 10 : 1;
    if (mod && ev.key.toLowerCase() === "s") { ev.preventDefault(); save(); }
    else if (mod && ev.key.toLowerCase() === "z") { ev.preventDefault(); ev.shiftKey ? redo() : undo(); }
    else if (mod && ev.key.toLowerCase() === "y") { ev.preventDefault(); redo(); }
    else if (mod && ev.key.toLowerCase() === "c") copySelection();
    else if (mod && ev.key.toLowerCase() === "x") cutSelection();
    else if (mod && ev.key.toLowerCase() === "v") pasteClipboard(null);
    else if (mod && ev.key.toLowerCase() === "a") { ev.preventDefault(); selectAll(); }
    else if (mod && ev.key.toLowerCase() === "d") { ev.preventDefault(); duplicateSelection(); }
    else if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); removeSelected(); }
    else if (ev.key === "Escape") { closeMenu(); if (state.tool) cancelTool(); else select(null); }
    else if (ev.key === "Enter" && state.tool) finishTool();
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); nudge(-step, 0); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); nudge(step, 0); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); nudge(0, -step); }
    else if (ev.key === "ArrowDown") { ev.preventDefault(); nudge(0, step); }
    else if (ev.key.toLowerCase() === "r" && !mod) rotate90();
    else if (ev.key === "]") reorder(ev.shiftKey ? "front" : "forward");
    else if (ev.key === "[") reorder(ev.shiftKey ? "back" : "backward");
  });

  // ---------------------------------------------------------------------
  // Properties panel
  // ---------------------------------------------------------------------
  /** Display names of the element types (properties panel title, layers list). */
  const TYPE_NAMES = { rect: "Rectangle", ellipse: "Ellipse", line: "Line", pipe: "Pipe", text: "Text", image: "Image",
    symbol: "Symbol", value: "Value", lamp: "Lamp", button: "Button", bar: "Bar", icon: "Icon" };
  const GEOM = [["x", "num", "X"], ["y", "num", "Y"], ["w", "num", "Width"], ["h", "num", "Height"], ["rot", "num", "Rotation °"]];
  const LABEL = [["label", "text", "Label"], ["labelPos", "select", "Label position", ["center", "top", "bottom", "none"]],
    ["labelSize", "num", "Label size"], ["labelColor", "color", "Label colour"]];
  const TAIL = [["opacity", "range", "Opacity"], ["name", "text", "Name / tooltip"], ["link", "dashboard", "Click opens screen"],
    ["faceplate", "faceplate", "Click opens device window (faceplate)"], ["locked", "check", "Locked (cannot be moved)"]];
  const STATE_HDR = ["-", "PLC state (optional)"];
  const VIS_OPS = [["on", "tag is ON / not 0"], ["off", "tag is OFF / 0"], ["eq", "= value"], ["ne", "≠ value"],
    ["gt", "> value"], ["ge", "≥ value"], ["lt", "< value"], ["le", "≤ value"]];

  /**
   * Visibility fields for an element (or the multi-selection proxy).
   * @param {Object} obj Element or proxy.
   * @returns {Array} Field definitions.
   */
  function visFields(obj) {
    const f = [["-", "Visibility at run time"],
      ["vis", "select", "Show", [["always", "Always"], ["hidden", "Never (hidden)"], ["tag", "Depending on a tag"]]]];
    if (obj.vis === "tag") {
      f.push(["visPoint", "point", "Tag"], ["visOp", "select", "Visible when", VIS_OPS]);
      if (!["on", "off"].includes(obj.visOp || "on")) f.push(["visValue", "text", "Value"]);
      f.push(["-", "Elements bound to the same tag appear and disappear together – e.g. switch the tag with a toggle button."]);
    }
    return f;
  }

  /** Property field definitions per element type: [prop, kind, label, options]. */
  const FIELDS = {
    rect: [...GEOM, ...LABEL, ["fill", "colorNone", "Fill"], ["stroke", "colorNone", "Border"], ["strokeWidth", "num", "Border width"],
      ["rx", "num", "Corner radius"], ["dash", "check", "Dashed border"], STATE_HDR, ["point", "point", "Tag"], ["onColor", "colorNone", "Fill when ON"], ["offColor", "colorNone", "Fill when OFF"]],
    ellipse: [...GEOM, ...LABEL, ["fill", "colorNone", "Fill"], ["stroke", "colorNone", "Border"], ["strokeWidth", "num", "Border width"],
      ["dash", "check", "Dashed border"], STATE_HDR, ["point", "point", "Tag"], ["onColor", "colorNone", "Fill when ON"], ["offColor", "colorNone", "Fill when OFF"]],
    line: [["stroke", "color", "Colour"], ["strokeWidth", "num", "Thickness"], ["dash", "check", "Dashed"], STATE_HDR,
      ["point", "point", "Tag"], ["onColor", "color", "Colour when ON"], ["offColor", "color", "Colour when OFF"], ["-", "Double-click the line to add a point; Alt+click a point to remove it."]],
    pipe: [["stroke", "color", "Pipe colour"], ["strokeWidth", "num", "Diameter"], ["flowColor", "color", "Flow colour"], STATE_HDR,
      ["point", "point", "Animate flow when tag is ON"], ["-", "Double-click the pipe to add a bend; Alt+click a bend to remove it."]],
    text: [...GEOM, ["text", "textarea", "Text"], ["size", "num", "Font size"], ["color", "color", "Colour"], ["bold", "check", "Bold"], ["italic", "check", "Italic"],
      ["align", "select", "Align", ["start", "middle", "end"]], ["font", "select", "Font", ["sans", "mono", "serif"]], ["bg", "colorNone", "Background"]],
    image: [...GEOM, ["asset", "asset", "Image"], ["fit", "select", "Fit", ["contain", "stretch", "cover"]]],
    symbol: [...GEOM, ["symbol", "symbol", "Symbol"], ["color", "color", "Line colour"], ...LABEL, STATE_HDR, ["point", "point", "Tag"],
      ["onColor", "color", "Colour when ON / level"], ["offColor", "color", "Colour when OFF"], ["min", "num", "Level min (tank/battery)"], ["max", "num", "Level max"]],
    value: [...GEOM, ["point", "point", "Tag"], ["prefix", "text", "Prefix text"], ["size", "num", "Font size"], ["color", "color", "Colour"],
      ["bold", "check", "Bold"], ["align", "select", "Align", ["start", "middle", "end"]], ["bg", "colorNone", "Background"], ["border", "colorNone", "Border"],
      ["-", "Alarm limits (optional)"], ["lo", "optnum", "Low limit"], ["hi", "optnum", "High limit"], ["alarmColor", "color", "Alarm colour"]],
    lamp: [...GEOM, ["point", "point", "Tag"], ["onColor", "color", "Colour when ON"], ["offColor", "color", "Colour when OFF"], ["blink", "check", "Blink when ON"], ["stroke", "colorNone", "Border"]],
    button: [...GEOM, ["text", "text", "Caption"], ["action", "select", "Action", data.button_actions], ["point", "point", "Tag to write"],
      ["value", "text", "Value (for 'set')"], ["confirm", "check", "Ask for confirmation"], ["color", "color", "Button colour"], ["textColor", "color", "Text colour"], ["size", "num", "Font size"],
      ["-", "toggle = invert a BOOL · set = write the value above · input = operator types a value · momentary = TRUE while pressed · link = open the screen chosen below · faceplate = open the device window chosen below"]],
    bar: [...GEOM, ["point", "point", "Tag"], ["min", "num", "Min"], ["max", "num", "Max"], ["orientation", "select", "Orientation", ["vertical", "horizontal"]],
      ["color", "color", "Fill colour"], ["bg", "colorNone", "Background"], ["stroke", "colorNone", "Border"]],
    icon: [...GEOM, ["icon", "text", "Icon name (Tabler)"], ["iconStyle", "select", "Style", ["outline", "filled"]], ["color", "color", "Colour"],
      ["strokeWidth", "num", "Line width"], ...LABEL, STATE_HDR, ["point", "point", "Tag"], ["onColor", "color", "Colour when ON"],
      ["offColor", "color", "Colour when OFF"], ["blink", "check", "Blink when ON"]],
  };

  const CANVAS_FIELDS = [["width", "num", "Width"], ["height", "num", "Height"], ["background", "colorNone", "Background colour"],
    ["backgroundImage", "asset", "Background image (e.g. floor plan)"], ["backgroundFit", "select", "Background fit", ["contain", "stretch", "cover"]], ["grid", "num", "Grid size"],
    ["-", "Page around the drawing (whole window at run time)"],
    ["pageBackground", "colorNone", "Page background colour"],
    ["pageBackgroundImage", "asset", "Page background image"],
    ["pageBackgroundFit", "select", "Page background fit", ["cover", "contain", "stretch", "repeat", "center"]]];

  let editBefore = null;
  propsBox.addEventListener("focusin", () => { editBefore = snapshot(); });
  propsBox.addEventListener("change", () => {
    if (editBefore) { commit(editBefore); editBefore = snapshot(); }
  });

  /**
   * Build an <option>.
   * @returns {HTMLOptionElement}
   */
  function option(value, label, selected) {
    const o = document.createElement("option");
    o.value = value === null || value === undefined ? "" : value;
    o.textContent = label;
    if (selected) o.selected = true;
    return o;
  }

  /**
   * Build one property input bound to obj[prop].
   * @param {Object} obj Element or canvas object.
   * @param {Array} def Field definition.
   * @param {Function} onChange Called after the value changed.
   * @returns {HTMLElement}
   */
  function field(obj, def, onChange) {
    const [prop, kind, label, opts] = def;
    const row = document.createElement("div");
    row.className = "dz-field" + (["num", "optnum", "color", "colorNone"].includes(kind) ? " dz-half" : "");
    if (prop === "-") {
      row.className = "dz-field dz-sep";
      row.textContent = T(kind);
      return row;
    }
    const lab = document.createElement("label");
    lab.textContent = T(label);
    row.appendChild(lab);
    const set = (v) => { obj[prop] = v; onChange(prop); };
    let input;
    switch (kind) {
      case "num":
      case "optnum":
        input = document.createElement("input");
        input.type = "number";
        input.step = "any";
        input.className = "form-control form-control-sm";
        input.value = obj[prop] === null || obj[prop] === undefined ? "" : obj[prop];
        input.addEventListener("input", () => {
          if (input.value === "") { if (kind === "optnum") set(null); return; }
          const n = parseFloat(input.value);
          if (!Number.isNaN(n)) set(n);
        });
        break;
      case "range":
        input = document.createElement("input");
        input.type = "range"; input.min = 0.1; input.max = 1; input.step = 0.05;
        input.className = "form-range";
        input.value = obj[prop] === undefined ? 1 : obj[prop];
        input.addEventListener("input", () => set(parseFloat(input.value)));
        break;
      case "text":
        input = document.createElement("input");
        input.type = "text";
        input.className = "form-control form-control-sm";
        input.value = obj[prop] || "";
        input.addEventListener("input", () => set(input.value));
        break;
      case "textarea":
        input = document.createElement("textarea");
        input.rows = 3;
        input.className = "form-control form-control-sm";
        input.value = obj[prop] || "";
        input.addEventListener("input", () => set(input.value));
        break;
      case "check":
        input = document.createElement("input");
        input.type = "checkbox";
        input.className = "form-check-input ms-2";
        input.checked = !!obj[prop];
        input.addEventListener("change", () => set(input.checked));
        lab.appendChild(input);
        return row;
      case "color":
      case "colorNone": {
        input = document.createElement("div");
        input.className = "d-flex align-items-center gap-1";
        const color = document.createElement("input");
        color.type = "color";
        color.className = "form-control form-control-color form-control-sm";
        const isNone = obj[prop] === "none" || obj[prop] === "transparent";
        color.value = !isNone && /^#[0-9a-f]{6}$/i.test(obj[prop] || "") ? obj[prop] : (/^#[0-9a-f]{3}$/i.test(obj[prop] || "") ? "#" + obj[prop].slice(1).split("").map((c) => c + c).join("") : "#000000");
        color.disabled = isNone;
        color.addEventListener("input", () => set(color.value));
        input.appendChild(color);
        if (kind === "colorNone") {
          const none = document.createElement("input");
          none.type = "checkbox";
          none.className = "form-check-input";
          none.title = T("No colour (transparent)");
          none.checked = isNone;
          const nl = document.createElement("small");
          nl.textContent = T("none");
          none.addEventListener("change", () => { color.disabled = none.checked; set(none.checked ? "none" : color.value); });
          input.append(none, nl);
        }
        break;
      }
      case "select":
        input = document.createElement("select");
        input.className = "form-select form-select-sm";
        opts.forEach((o) => {
          const [v, l] = Array.isArray(o) ? o : [o, o];
          input.appendChild(option(v, T(l), obj[prop] === v));
        });
        input.addEventListener("change", () => set(input.value));
        break;
      case "symbol":
        input = document.createElement("select");
        input.className = "form-select form-select-sm";
        Object.entries(SYMBOLS).forEach(([k, s]) => input.appendChild(option(k, T(s.category) + " – " + T(s.name), obj[prop] === k)));
        input.addEventListener("change", () => set(input.value));
        break;
      case "point": {
        input = document.createElement("select");
        input.className = "form-select form-select-sm";
        input.appendChild(option("", T("— none —"), !obj[prop]));
        Object.values(data.points).forEach((p) => input.appendChild(option(p.id, p.label + " (" + p.address + ")" + (p.writable ? " ✎" : ""), obj[prop] === p.id)));
        input.appendChild(option("__existing", T("＋ Existing tag from the tag table…"), false));
        input.appendChild(option("__new", T("＋ New tag…"), false));
        input.addEventListener("change", () => {
          if (input.value === "__new") { input.value = obj[prop] || ""; openTagModal((id) => set(id)); return; }
          if (input.value === "__existing") { input.value = obj[prop] || ""; pickExistingTags((id) => set(id)); return; }
          set(input.value ? Number(input.value) : null);
        });
        const p = obj[prop] && data.points[obj[prop]];
        if (p && obj.type === "button" && !["link", "faceplate"].includes(obj.action) && !p.writable) {
          const warn = document.createElement("div");
          warn.className = "dz-warn";
          warn.textContent = T("This tag is read-only – mark it writable in the tag settings.");
          row.appendChild(input);
          row.appendChild(warn);
          return row;
        }
        break;
      }
      case "dashboard":
        input = document.createElement("select");
        input.className = "form-select form-select-sm";
        input.appendChild(option("", T("— none —"), !obj[prop]));
        data.dashboards.forEach((d) => input.appendChild(option(d.id, d.name, obj[prop] === d.id)));
        input.addEventListener("change", () => set(input.value ? Number(input.value) : null));
        break;
      case "faceplate":
        input = document.createElement("select");
        input.className = "form-select form-select-sm";
        input.appendChild(option("", T("— none —"), !obj[prop]));
        (data.faceplates || []).forEach((d) => input.appendChild(option(d.id, d.name, obj[prop] === d.id)));
        input.addEventListener("change", () => set(input.value ? Number(input.value) : null));
        if (!(data.faceplates || []).length) {
          input.disabled = true;
          input.title = T("No devices yet – create them under Admin → Faceplates.");
        }
        break;
      case "asset":
        input = document.createElement("select");
        input.className = "form-select form-select-sm";
        input.appendChild(option("", T("— none —"), !obj[prop]));
        data.assets.forEach((a) => input.appendChild(option(a.id, a.category + " – " + a.name, obj[prop] === a.id)));
        input.addEventListener("change", () => set(input.value ? Number(input.value) : null));
        break;
      default:
        input = document.createElement("span");
    }
    row.appendChild(input);
    return row;
  }

  /** Render the properties panel for the selection (or canvas settings). */
  function renderProps() {
    propsBox.replaceChildren();
    const e = state.selected && find(state.selected);
    const title = document.createElement("h6");
    const body = document.createElement("div");
    body.className = "dz-fields";
    if (!e) {
      title.textContent = T("Canvas");
      const c = state.layout.canvas;
      CANVAS_FIELDS.forEach((def) => body.appendChild(field(c, def, () => {
        c.width = Math.max(200, Math.min(10000, c.width || 1600));
        c.height = Math.max(200, Math.min(10000, c.height || 900));
        c.grid = Math.max(1, Math.min(200, c.grid || 10));
        renderAll(true);
        setDirty(true);
      })));
      const help = document.createElement("div");
      help.className = "dz-help";
      const strong = document.createElement("strong");
      strong.textContent = T("Shortcuts");
      help.append(strong, document.createElement("br"), T("Drag items from the left · Del delete · Ctrl+Z / Ctrl+Y undo/redo · Ctrl+C / Ctrl+X / Ctrl+V copy/cut/paste (also into another screen) · Ctrl+D duplicate · Ctrl+A select all · Ctrl/Shift+click multi-select · right-click menu · arrows move (Shift ×10) · R rotate 90° · [ ] order · Shift while resizing keeps proportions · Alt disables snapping · Ctrl+wheel zoom · Ctrl+S save"));
      propsBox.append(title, body, help);
      return;
    }
    if (state.selection.length > 1) {
      renderMultiProps(title, body);
      propsBox.append(title, body);
      return;
    }
    const def = SYMBOLS[e.symbol];
    title.textContent = e.type === "symbol" && def ? T("Symbol – {0}", T(def.name)) : T(TYPE_NAMES[e.type] || e.type);
    (FIELDS[e.type] || []).concat(TAIL, visFields(e)).forEach((fd) => {
      if (isPoly(e) && fd[0] === "rot") return;
      body.appendChild(field(e, fd, (prop) => {
        renderOne(e.id);
        if (["point", "action", "symbol", "vis", "visOp", "visPoint"].includes(prop)) renderProps();
        if (["name", "label", "text"].includes(prop)) renderLayers();
      }));
    });
    const actions = document.createElement("div");
    actions.className = "d-flex flex-wrap gap-1 mt-2";
    const p = e.point && data.points[e.point];
    if (p) {
      const a = document.createElement("a");
      a.className = "btn btn-sm btn-outline-secondary";
      a.href = R.urlFor(data.urls.point_edit, p.id);
      a.target = "_blank";
      a.textContent = T("Edit tag settings");
      actions.appendChild(a);
    }
    propsBox.append(title, body, actions);
  }

  /**
   * Properties shared by a multi-selection: visibility, opacity, lock.
   * Changing a field applies it to every selected element.
   * @param {HTMLElement} title Panel title.
   * @param {HTMLElement} body Field container.
   */
  function renderMultiProps(title, body) {
    const els = selectedEls();
    title.textContent = T("{0} elements selected", els.length);
    const common = (prop, dflt) => {
      const vals = new Set(els.map((x) => (x[prop] === undefined ? dflt : x[prop])));
      return vals.size === 1 ? [...vals][0] : undefined;
    };
    const proxy = { vis: common("vis", "always"), visPoint: common("visPoint", null), visOp: common("visOp", "on"),
      visValue: common("visValue", ""), opacity: common("opacity", 1), locked: common("locked", false) };
    if (proxy.vis === undefined) proxy.vis = "";
    const defs = [["opacity", "range", "Opacity"], ["locked", "check", "Locked (cannot be moved)"], ...visFields(proxy)];
    defs.forEach((fd) => body.appendChild(field(proxy, fd, (prop) => {
      const before = snapshot();
      els.forEach((x) => { x[prop] = proxy[prop]; if (prop === "vis" && proxy.vis === "tag" && !x.visOp) x.visOp = "on"; renderOne(x.id); });
      commit(before);
      if (["vis", "visOp", "visPoint"].includes(prop)) renderProps();
    })));
    const help = document.createElement("div");
    help.className = "dz-help";
    help.textContent = T("Ctrl/Shift+click adds or removes elements, Ctrl+A selects all. Drag to move them together; right-click for copy, paste, order and delete.");
    body.appendChild(help);
  }

  /** Render the layers list (top-most first). */
  function renderLayers() {
    layersBox.replaceChildren();
    const icons = { rect: "bi-square", ellipse: "bi-circle", line: "bi-slash-lg", pipe: "bi-bezier2", text: "bi-fonts", image: "bi-image",
      symbol: "bi-gear-wide-connected", value: "bi-123", lamp: "bi-circle-fill", button: "bi-hand-index", bar: "bi-bar-chart-fill", icon: "bi-stars" };
    state.layout.elements.slice().reverse().forEach((e) => {
      const row = document.createElement("div");
      row.className = "dz-layer" + (state.selection.includes(e.id) ? " active" : "");
      const i = document.createElement("i");
      i.className = "bi " + (icons[e.type] || "bi-dot");
      const s = document.createElement("span");
      const p = e.point && data.points[e.point];
      s.textContent = e.name || e.label || (e.type === "text" ? e.text : "") || (e.type === "symbol" && SYMBOLS[e.symbol] ? T(SYMBOLS[e.symbol].name) : "")
        || (e.type === "icon" ? T("icon {0}", e.icon) : "") || (p ? p.label : "") || T(TYPE_NAMES[e.type] || e.type);
      row.append(i, s);
      if (e.vis === "hidden" || e.vis === "tag") {
        const v = document.createElement("i");
        v.className = "bi " + (e.vis === "hidden" ? "bi-eye-slash" : "bi-eye") + (e.locked ? "" : " ms-auto") + " text-secondary";
        v.title = e.vis === "hidden" ? T("Hidden at run time") : T("Visible depending on a tag");
        row.appendChild(v);
      }
      if (e.locked) { const l = document.createElement("i"); l.className = "bi bi-lock-fill ms-auto"; row.appendChild(l); }
      row.addEventListener("click", (ev) => select(e.id, ev.ctrlKey || ev.metaKey || ev.shiftKey));
      layersBox.appendChild(row);
    });
    document.getElementById("dz-count").textContent = T("{0} elements", state.layout.elements.length);
  }

  // ---------------------------------------------------------------------
  // Save, live preview, toolbar
  // ---------------------------------------------------------------------
  /** Send the layout to the server (validated there) and adopt the result. */
  async function save() {
    status("Saving…", "info");
    const res = await sendJson(data.urls.layout, "PUT", state.layout);
    if (!res.ok) { status(T("Save failed: {0}", res.json.error || "HTTP " + res.status), "error"); return; }
    const indexes = state.selection.map((id) => state.layout.elements.findIndex((e) => e.id === id));
    const primary = state.layout.elements.findIndex((e) => e.id === state.selected);
    state.layout = res.json.layout;
    state.selection = indexes.filter((i) => i >= 0 && state.layout.elements[i]).map((i) => state.layout.elements[i].id);
    state.selected = primary >= 0 && state.layout.elements[primary] ? state.layout.elements[primary].id : null;
    setDirty(false);
    renderAll();
    status(T("Saved {0}", new Date().toLocaleTimeString()), "ok");
  }

  /** Fetch values once for the live preview. */
  async function pollLive() {
    if (!state.live) return;
    try {
      const resp = await fetch(data.urls.values, { credentials: "same-origin" });
      const json = await resp.json();
      state.values = window.HMILocal.overlay(json.values);
      R.applyValues(state.nodes, state.layout, state.values, ctx);
    } catch (err) {
      status(T("Live preview: {0}", err.message), "error");
    }
    state.liveTimer = setTimeout(pollLive, Math.max(500, data.dashboard.refresh_ms));
  }

  /** Toggle the live preview. */
  function toggleLive() {
    state.live = !state.live;
    document.getElementById("dz-live").classList.toggle("active", state.live);
    clearTimeout(state.liveTimer);
    if (state.live) pollLive(); else renderAll();
  }

  /**
   * Attach a click handler to a toolbar button.
   * @param {string} id Element id.
   * @param {Function} fn Handler.
   */
  function on(id, fn) {
    document.getElementById(id).addEventListener("click", fn);
  }

  on("dz-save", save);
  on("dz-undo", undo);
  on("dz-redo", redo);
  on("dz-delete", removeSelected);
  on("dz-dup", duplicateSelection);
  on("dz-export", exportSvg);
  on("dz-front", () => reorder("front"));
  on("dz-back", () => reorder("back"));
  on("dz-rotate", rotate90);
  on("dz-zoom-in", () => setZoom(state.zoom * 1.2));
  on("dz-zoom-out", () => setZoom(state.zoom / 1.2));
  on("dz-zoom-fit", zoomFit);
  on("dz-live", toggleLive);
  document.getElementById("dz-snap").addEventListener("change", (ev) => {
    state.snap = ev.target.checked;
    gridRect.style.display = state.snap ? "" : "none";
  });
  document.getElementById("dz-file").addEventListener("change", async (ev) => {
    if (ev.target.files.length) await upload(ev.target.files);
    ev.target.value = "";
  });
  document.getElementById("tag-form").addEventListener("submit", submitTag);
  window.addEventListener("beforeunload", (ev) => {
    if (state.dirty) { ev.preventDefault(); ev.returnValue = ""; }
  });
  window.addEventListener("resize", drawSelection);

  // ---------------------------------------------------------------------
  // Context menu (right-click)
  // ---------------------------------------------------------------------
  const menu = document.createElement("div");
  menu.className = "dropdown-menu dz-menu shadow";
  menu.id = "dz-menu";
  menu.setAttribute("role", "menu");
  document.body.appendChild(menu);

  /** Close the context menu. */
  function closeMenu() {
    menu.classList.remove("show");
  }

  /**
   * Show the context menu.
   * @param {number} x Client x.
   * @param {number} y Client y.
   * @param {Array} items Entries: [label, shortcut, action, disabled, icon] or "-" for a divider.
   */
  function openMenu(x, y, items) {
    menu.replaceChildren();
    items.forEach((it) => {
      if (it === "-") { const hr = document.createElement("div"); hr.className = "dropdown-divider"; menu.appendChild(hr); return; }
      const [label, keys, action, disabled, icon] = it;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "dropdown-item d-flex align-items-center gap-2";
      b.disabled = !!disabled;
      b.setAttribute("role", "menuitem");
      const i = document.createElement("i");
      i.className = "bi " + (icon || "bi-dot");
      const t = document.createElement("span");
      t.textContent = T(label);
      b.append(i, t);
      if (keys) { const k = document.createElement("small"); k.className = "ms-auto text-secondary ps-3"; k.textContent = keys; b.appendChild(k); }
      b.addEventListener("click", () => { closeMenu(); action(); });
      menu.appendChild(b);
    });
    menu.classList.add("show");
    menu.style.left = "0px";
    menu.style.top = "0px";
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.max(4, Math.min(x, window.innerWidth - r.width - 4)) + "px";
    menu.style.top = Math.max(4, Math.min(y, window.innerHeight - r.height - 4)) + "px";
    const first = menu.querySelector("button:not([disabled])");
    if (first) first.focus();
  }

  /**
   * Menu entries for the selection.
   * @param {{x:number,y:number}} at Canvas position of the click.
   * @returns {Array}
   */
  function elementMenu(at) {
    const els = selectedEls();
    const single = els.length === 1 ? els[0] : null;
    const allLocked = els.every((e) => e.locked);
    const allHidden = els.every((e) => e.vis === "hidden");
    const p = single && single.point && data.points[single.point];
    const clip = readClipboard();
    const items = [
      ["Cut", "Ctrl+X", cutSelection, false, "bi-scissors"],
      ["Copy", "Ctrl+C", copySelection, false, "bi-clipboard"],
      ["Paste here", "Ctrl+V", () => pasteClipboard(at), !(clip && clip.elements && clip.elements.length), "bi-clipboard-plus"],
      ["Duplicate", "Ctrl+D", duplicateSelection, false, "bi-copy"],
      ["Delete", "Del", removeSelected, false, "bi-trash"],
      "-",
      ["Bring to front", "Shift+]", () => reorder("front"), false, "bi-front"],
      ["Bring forward", "]", () => reorder("forward"), false, "bi-chevron-up"],
      ["Send backward", "[", () => reorder("backward"), false, "bi-chevron-down"],
      ["Send to back", "Shift+[", () => reorder("back"), false, "bi-back"],
      "-",
      ["Rotate 90°", "R", rotate90, els.every(isPoly), "bi-arrow-repeat"],
      [allLocked ? "Unlock" : "Lock", "", () => setOnSelection("locked", !allLocked), false, allLocked ? "bi-unlock" : "bi-lock"],
      [allHidden ? "Always show" : "Hide at run time", "", () => setOnSelection("vis", allHidden ? "always" : "hidden"), false, allHidden ? "bi-eye" : "bi-eye-slash"],
      ["Visibility by tag…", "", () => { setOnSelection("vis", "tag"); const v = propsBox.querySelector(".dz-sep:last-of-type"); if (v) v.scrollIntoView({ block: "center" }); }, false, "bi-toggles"],
    ];
    if (p) items.push("-", ["Edit tag settings", "", () => window.open(R.urlFor(data.urls.point_edit, p.id), "_blank"), false, "bi-tag"]);
    items.push("-", ["Select all", "Ctrl+A", selectAll, false, "bi-check2-all"]);
    return items;
  }

  /**
   * Menu entries for an empty canvas spot.
   * @param {{x:number,y:number}} at Canvas position of the click.
   * @returns {Array}
   */
  function canvasMenu(at) {
    const clip = readClipboard();
    return [
      ["Paste here", "Ctrl+V", () => pasteClipboard(at), !(clip && clip.elements && clip.elements.length), "bi-clipboard-plus"],
      ["Select all", "Ctrl+A", selectAll, !state.layout.elements.length, "bi-check2-all"],
      "-",
      ["Canvas settings", "", () => select(null), false, "bi-sliders"],
      ["Zoom to fit", "", zoomFit, false, "bi-arrows-fullscreen"],
      ["Export as SVG file", "", exportSvg, false, "bi-filetype-svg"],
    ];
  }

  svg.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    if (state.tool) { finishTool(); return; }
    const at = toCanvas(ev);
    const g = ev.target.closest(".hmi-el");
    if (g) {
      if (!state.selection.includes(g.dataset.id)) select(g.dataset.id);
      openMenu(ev.clientX, ev.clientY, elementMenu(at));
    } else {
      openMenu(ev.clientX, ev.clientY, canvasMenu(at));
    }
  });
  document.addEventListener("pointerdown", (ev) => { if (!menu.contains(ev.target)) closeMenu(); }, true);
  stage.addEventListener("scroll", closeMenu);
  window.addEventListener("blur", closeMenu);

  // ---------------------------------------------------------------------
  // SVG export
  // ---------------------------------------------------------------------
  /** Stylesheet embedded in exported SVG files (static look of symbols, icons and pipes). */
  const EXPORT_CSS = [
    ".hmi-sym { overflow: visible; }",
    ".hmi-sym .sb { fill: var(--sym-fill, #2c3846); stroke: var(--sym-line, #cfd8e3); stroke-width: 3; }",
    ".hmi-sym .sl { fill: none; stroke: var(--sym-line, #cfd8e3); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }",
    ".hmi-sym .sa { fill: var(--sym-accent, #5b6b7d); stroke: var(--sym-line, #cfd8e3); stroke-width: 2; }",
    ".hmi-sym .sv { fill: var(--sym-accent, #2fa4e7); }",
    ".hmi-sym .st { fill: var(--sym-line, #cfd8e3); }",
    ".hmi-sym .sf, .hmi-sym .s-on { display: none; }",
    ".hmi-t-pipe .hmi-flow { display: none; }",
    "text { font-family: system-ui, 'Segoe UI', Arial, sans-serif; }",
  ].join("\n");

  /**
   * Read a URL as a data: URI.
   * @param {string} url Same-origin URL.
   * @returns {Promise<{dataUri: string, mime: string, base64: string}>}
   */
  async function fetchDataUri(url) {
    const resp = await fetch(url, { credentials: "same-origin" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const blob = await resp.blob();
    const dataUri = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
    return { dataUri: dataUri, mime: blob.type, base64: String(dataUri).split(",")[1] || "" };
  }

  /**
   * Export the current drawing (including unsaved changes) as a standalone
   * SVG file.  Images are embedded, elements hidden at run time are left
   * out, and the screen data (layout, tag descriptions, images, linked
   * screen names) is stored in <metadata> so the file can be imported again.
   */
  async function exportSvg() {
    status("Preparing SVG…", "info");
    const layout = clone(state.layout);
    await Promise.all(layout.elements.filter((e) => e.type === "icon").map((e) => loadIcon(e.iconStyle || "outline", e.icon)));
    const NS = "http://www.w3.org/2000/svg";
    const out = document.createElementNS(NS, "svg");
    out.setAttribute("xmlns", NS);
    out.setAttribute("width", layout.canvas.width);
    out.setAttribute("height", layout.canvas.height);
    R.renderLayout(out, layout, { points: data.points, urls: data.urls, designer: false, icons: ctx.icons });
    out.querySelectorAll(".hmi-vis-fixed").forEach((n) => n.remove());
    out.querySelectorAll(".hmi-vis-off").forEach((n) => n.classList.remove("hmi-vis-off"));
    out.querySelectorAll("title").forEach((n) => n.remove());

    const assetIds = new Set();
    if (layout.canvas.backgroundImage) assetIds.add(layout.canvas.backgroundImage);
    if (layout.canvas.pageBackgroundImage) assetIds.add(layout.canvas.pageBackgroundImage);
    layout.elements.forEach((e) => { if (e.type === "image" && e.asset) assetIds.add(e.asset); });
    const assets = {};
    for (const id of assetIds) {
      const url = R.urlFor(data.urls.asset, id);
      try {
        const file = await fetchDataUri(url);
        const meta = data.assets.find((a) => a.id === id) || {};
        assets[id] = { name: meta.name || "image " + id, category: meta.category || "Images", mime: file.mime, data: file.base64 };
        out.querySelectorAll("image").forEach((img) => { if (img.getAttribute("href") === url) img.setAttribute("href", file.dataUri); });
      } catch (err) {
        status(T("Could not embed image {0}: {1}", id, err.message), "error");
      }
    }
    const tags = {};
    layout.elements.forEach((e) => ["point", "visPoint"].forEach((k) => { if (e[k] && data.points[e[k]]) tags[e[k]] = data.points[e[k]]; }));
    const screens = {};
    layout.elements.forEach((e) => { const d = e.link && data.dashboards.find((x) => x.id === e.link); if (d) screens[d.id] = d.name; });
    const meta = {
      format: "pyplchmi-screen", version: 1, exported: new Date().toISOString(),
      screen: { name: data.dashboard.name, description: data.dashboard.description || "", refresh_ms: data.dashboard.refresh_ms },
      layout: layout, tags: tags, assets: assets, screens: screens,
    };
    const style = document.createElementNS(NS, "style");
    style.textContent = EXPORT_CSS;
    const md = document.createElementNS(NS, "metadata");
    md.setAttribute("id", "pyplchmi-screen");
    md.textContent = JSON.stringify(meta);
    const title = document.createElementNS(NS, "title");
    title.textContent = data.dashboard.name;
    out.insertBefore(style, out.firstChild);
    out.insertBefore(md, out.firstChild);
    out.insertBefore(title, out.firstChild);
    const text = '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(out);
    const blob = new Blob([text], { type: "image/svg+xml" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (data.dashboard.slug || "screen") + ".svg";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    status(state.dirty ? T("Exported {0} (including unsaved changes)", a.download) : T("Exported {0}", a.download), "ok");
    return text;
  }

  // ---------------------------------------------------------------------
  // Start
  // ---------------------------------------------------------------------
  buildSvg();
  buildPalette();
  renderAll();
  zoomFit();
  status("Drag items from the left onto the canvas. Click an item to select it.", "info");

  // Exposed for debugging and automated tests.
  window.HMIDesigner = { state: state, save: save, addElement: addElement, placeItem: placeItem, select: select,
    selectAll: selectAll, exportSvg: exportSvg, makeIcon: makeIcon, pasteClipboard: pasteClipboard, copySelection: copySelection };
})();
