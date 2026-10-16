/**
 * Right-click menu of the values shown on screens (grid tiles and elements
 * bound to a tag on Image screens).
 *
 * - **Show graph** (tags with history, users with ``view_history``): a window
 *   with a trend of the tag over the last *N* days (1 hour … 30 days or any
 *   number of days), refreshed every 15 s, with *Save image* (PNG) and a link
 *   to the full trend viewer.  Chart.js is loaded on first use.
 * - **Details**: tag, PLC (protocol, connection), address, data type,
 *   scaling, limits, write access, history summary, screens, alarms and the
 *   current value / quality.
 *
 * Data comes from ``#tag-menu-data`` (see ``dashboards/_tag_menu.html``);
 * the screen runtimes publish each poll as a ``hmi:values`` event, so the
 * details show the value currently on the screen.  The menu and windows are
 * attached to the full-screen element when a screen is in full-screen mode.
 * A long press (touch) opens the menu too, because browsers send
 * ``contextmenu`` for it.
 */
(function () {
  "use strict";

  const dataEl = document.getElementById("tag-menu-data");
  if (!dataEl) return;
  const cfg = JSON.parse(dataEl.textContent);
  const mimicEl = document.getElementById("mimic-data");
  const mimic = mimicEl ? JSON.parse(mimicEl.textContent) : null;
  const elementPoint = {};
  if (mimic) mimic.layout.elements.forEach((e) => { if (e.point) elementPoint[e.id] = String(e.point); });

  /**
   * A colour of the current theme (CSS variable of hmi.css).
   * @param {string} name Variable name.
   * @param {string} fallback Value when the variable is missing.
   * @returns {string}
   */
  function themeColor(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }
  // colours of the theme in use when the page was opened
  const BG = themeColor("--hmi-chart-bg", "#12161c");
  const TEXT = themeColor("--hmi-chart-text", "#c9d1d9");
  const GRID = themeColor("--hmi-chart-grid", "rgba(255,255,255,0.08)");
  const TITLE = themeColor("--hmi-chart-title", "#ffffff");
  const RANGES = [[1 / 24, "1 hour"], [8 / 24, "8 hours"], [1, "1 day"], [7, "7 days"], [30, "30 days"]];
  const STORE_KEY = "hmi-graph-days";
  let latest = { values: {}, plcs: {}, timestamp: null };
  document.addEventListener("hmi:values", (ev) => { latest = ev.detail || latest; });

  // ------------------------------------------------------------------ helpers
  /**
   * Create an element.
   * @param {string} tag Tag name.
   * @param {Object} [attrs] Properties / attributes (``class``, ``text``, ``html``, others as attributes).
   * @param {Array<Node|string>} [children] Children.
   * @returns {HTMLElement}
   */
  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v === undefined || v === null) return;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "html") el.innerHTML = v;
      else el.setAttribute(k, v);
    });
    (children || []).forEach((c) => el.append(c));
    return el;
  }

  /** Where overlays go: the full-screen element if any, else the page body. */
  const host = () => document.fullscreenElement || document.body;

  /**
   * Local date-time text.
   * @param {number|string|Date} t Time.
   * @returns {string} ``YYYY-MM-DD HH:MM:SS``.
   */
  function fullDate(t) {
    const d = new Date(t);
    if (Number.isNaN(d.getTime())) return "—";
    const p = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" +
      p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  /**
   * Format a value of a tag.
   * @param {Object} meta Tag info from the menu data.
   * @param {?number} v Value.
   * @returns {string}
   */
  function fmt(meta, v) {
    if (v === null || v === undefined) return "—";
    if (meta.is_bool) return v ? meta.true_text : meta.false_text;
    const d = Number.isInteger(meta.decimals) ? meta.decimals : 2;
    return Number(v).toFixed(d) + (meta.unit ? " " + meta.unit : "");
  }

  /**
   * Text of a "days back" value.
   * @param {number} days Days.
   * @returns {string}
   */
  function daysText(days) {
    if (days < 1) return T("{0} hours", Math.round(days * 24 * 10) / 10);
    return days === 1 ? T("1 day") : T("{0} days", Math.round(days * 100) / 100);
  }

  // ------------------------------------------------------------------ overlay window
  /**
   * Open a window (own overlay, so it also works in full-screen mode).
   * @param {string} title Title.
   * @param {Node} body Body content.
   * @param {Node[]} buttons Footer buttons (a Close button is added).
   * @param {string} [size] ``lg`` for the wide graph window.
   * @returns {{root:HTMLElement, close:function()}}
   */
  function openWindow(title, body, buttons, size) {
    const closeBtn = h("button", { type: "button", class: "btn-close", "aria-label": T("Close") });
    const closeFooter = h("button", { type: "button", class: "btn btn-outline-secondary", text: T("Close") });
    const dialog = h("div", { class: "hmi-window hmi-window-" + (size || "md"), role: "dialog", "aria-modal": "true",
      "aria-label": title }, [
      h("div", { class: "hmi-window-head" }, [h("h2", { class: "h5 mb-0 text-truncate", text: title }), closeBtn]),
      h("div", { class: "hmi-window-body" }, [body]),
      h("div", { class: "hmi-window-foot" }, (buttons || []).concat([closeFooter])),
    ]);
    const root = h("div", { class: "hmi-window-backdrop", dir: document.documentElement.dir || "ltr" }, [dialog]);
    const handlers = [];
    function close() {
      root.remove();
      document.removeEventListener("keydown", onKey, true);
      handlers.forEach((fn) => fn());
    }
    function onKey(ev) { if (ev.key === "Escape") { ev.stopPropagation(); close(); } }
    closeBtn.addEventListener("click", close);
    closeFooter.addEventListener("click", close);
    root.addEventListener("mousedown", (ev) => { if (ev.target === root) close(); });
    document.addEventListener("keydown", onKey, true);
    host().appendChild(root);
    closeFooter.focus();
    return { root: root, close: close, onClose: (fn) => handlers.push(fn) };
  }

  // ------------------------------------------------------------------ context menu
  let menu = null;
  /** Remove the open menu. */
  function closeMenu() {
    if (menu) { menu.remove(); menu = null; }
  }

  /**
   * Show the menu for a tag at the pointer.
   * @param {MouseEvent} ev The contextmenu event.
   * @param {string} pointId Tag id.
   */
  function showMenu(ev, pointId) {
    closeMenu();
    const meta = cfg.points[pointId];
    if (!meta) return;
    const items = [h("div", { class: "hmi-ctx-title text-truncate", text: meta.label })];
    if (cfg.can_history) {
      const graph = h("button", { type: "button", class: "hmi-ctx-item", "data-action": "graph" },
        [h("i", { class: "bi bi-graph-up" }), " " + T("Show graph")]);
      if (!meta.history) {
        graph.disabled = true;
        graph.title = T("This tag has no history – enable “Save history” in the tag settings.");
      }
      graph.addEventListener("click", () => { closeMenu(); showGraph(pointId); });
      items.push(graph);
    }
    const details = h("button", { type: "button", class: "hmi-ctx-item", "data-action": "details" },
      [h("i", { class: "bi bi-info-circle" }), " " + T("Details")]);
    details.addEventListener("click", () => { closeMenu(); showDetails(pointId); });
    items.push(details);
    menu = h("div", { class: "hmi-ctx-menu", role: "menu" }, items);
    const parent = host();
    parent.appendChild(menu);
    const w = menu.offsetWidth, hgt = menu.offsetHeight;
    const x = Math.min(ev.clientX, window.innerWidth - w - 4);
    const y = Math.min(ev.clientY, window.innerHeight - hgt - 4);
    menu.style.left = Math.max(4, x) + "px";
    menu.style.top = Math.max(4, y) + "px";
    const first = menu.querySelector("button:not([disabled])");
    if (first) first.focus();
  }

  /**
   * Find the tag of the element under the pointer.
   * @param {EventTarget} target Event target.
   * @returns {?string} Tag id.
   */
  function pointOf(target) {
    if (!(target instanceof Element)) return null;
    const tile = target.closest(".hmi-tile[data-id]");
    if (tile) return tile.dataset.id;
    const g = target.closest("#mimic-svg .hmi-el[data-id]");
    if (g) return elementPoint[g.dataset.id] || null;
    return null;
  }

  document.addEventListener("contextmenu", (ev) => {
    const id = pointOf(ev.target);
    if (!id || !cfg.points[id]) { closeMenu(); return; }
    ev.preventDefault();
    showMenu(ev, id);
  });
  document.addEventListener("mousedown", (ev) => { if (menu && !menu.contains(ev.target)) closeMenu(); }, true);
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeMenu(); });
  window.addEventListener("blur", closeMenu);
  window.addEventListener("resize", closeMenu);
  document.addEventListener("scroll", closeMenu, true);
  document.addEventListener("fullscreenchange", closeMenu);

  // ------------------------------------------------------------------ details
  /**
   * One row of the details table.
   * @param {string} label Label (translated).
   * @param {*} value Value (``null``/``""`` rows are skipped).
   * @returns {?HTMLElement}
   */
  function row(label, value) {
    if (value === null || value === undefined || value === "") return null;
    return h("tr", {}, [h("th", { class: "text-secondary fw-normal", text: label }),
      h("td", { class: "hmi-detail-value", text: String(value) })]);
  }

  /**
   * Communication state of a PLC as text.
   * @param {{state:string, error:?string, down_for:?number}} p Status from the values poll.
   * @returns {string}
   */
  function plcState(p) {
    const texts = { online: T("online"), unstable: T("reconnecting…"), disabled: T("disabled"), unknown: T("unknown") };
    const text = texts[p.state] || T("offline");
    return text + (p.error ? " – " + p.error : "");
  }

  /**
   * Show the Details window of a tag.
   * @param {string} pointId Tag id.
   */
  async function showDetails(pointId) {
    const meta = cfg.points[pointId];
    const body = h("div", { class: "small", text: T("Loading…") });
    const win = openWindow(T("Details: {0}", meta.label), body, []);
    let d;
    try {
      const resp = await fetch(cfg.urls.details.replace("/0/", "/" + encodeURIComponent(pointId) + "/"),
        { credentials: "same-origin", headers: { Accept: "application/json" } });
      d = await resp.json();
      if (!resp.ok) throw new Error(d.error || resp.statusText);
    } catch (err) {
      body.textContent = T("Loading failed: {0}", err.message || err);
      return;
    }
    const v = latest.values[pointId];
    const plc = latest.plcs && latest.plcs[String(d.plc_id)];
    const quality = v ? (v.quality === "good" ? (v.stale ? T("last good value (PLC briefly unreachable)") : T("good"))
      : v.quality === "local" ? T("local (this browser window)") : T("bad")) : null;
    const yes = T("yes"), no = T("no");
    const scale = d.scale !== 1 || d.offset !== 0 ? T("raw × {0} + {1}", d.scale, d.offset) : null;
    const range = d.min !== null || d.max !== null ? (d.min ?? "—") + " … " + (d.max ?? "—") : null;
    let history = null;
    if (d.history) {
      history = d.history.enabled ? T("every {0} s", d.history.interval) : T("logging off");
      if (d.history.count) {
        history += " · " + T("{0} samples", d.history.count) + " · " + fullDate(d.history.first) + " → " +
          fullDate(d.history.last);
      }
    }
    const rows = [
      [T("Current value"), v ? (v.display ?? fmt(meta, v.value)) + (v.error ? " – " + v.error : "") : null],
      [T("Quality"), quality],
      [T("Updated"), latest.timestamp ? fullDate(latest.timestamp) : null],
      ["-", T("Tag")],
      [T("Tag"), d.label],
      [T("Address"), d.address],
      [T("Data type"), d.data_type],
      [T("Unit"), d.unit],
      [T("Scaling"), scale],
      [T("Decimals"), d.decimals],
      [T("Range (min … max)"), range],
      [T("Texts TRUE / FALSE"), d.true_text !== null ? d.true_text + " / " + d.false_text : null],
      [T("Writable"), d.writable ? yes : no],
      [T("Dummy tag"), d.dummy ? (d.dummy === "local" ? T("local (this browser window)") : T("general (stored in the database)")) : null],
      [T("History"), history],
      ["-", T("PLC")],
      [T("PLC"), d.plc + (d.plc_enabled ? "" : " (" + T("disabled") + ")")],
      [T("Description"), d.plc_description],
      [T("Protocol"), d.protocol],
      [T("Connection"), d.endpoint],
      [T("Status"), plc ? plcState(plc) : null],
      ["-", T("Usage")],
      [T("Screens"), d.screens.join(", ")],
      [T("Alarms"), d.alarms.length ? d.alarms.join(", ") : null],
    ];
    const tbody = h("tbody");
    rows.forEach(([label, value]) => {
      if (label === "-") {
        tbody.append(h("tr", { class: "hmi-detail-section" }, [h("th", { colspan: "2", text: value })]));
        return;
      }
      const tr = row(label, value);
      if (tr) tbody.append(tr);
    });
    if (d.files && d.files.length) {
      const links = h("td", { class: "hmi-detail-value" });
      d.files.forEach((f, i) => {
        if (i) links.append(" · ");
        links.append(h("a", { href: f.url, target: "_blank", rel: "noopener" },
          [h("i", { class: "bi " + (f.kind === "image" ? "bi-file-earmark-image" : "bi-file-earmark-pdf") }), " " + f.name]));
      });
      tbody.append(h("tr", {}, [h("th", { class: "text-secondary fw-normal", text: T("PLC files") }), links]));
    }
    body.textContent = "";
    body.append(h("table", { class: "table table-sm hmi-detail-table mb-0" }, [tbody]));
    if (cfg.can_history && meta.history) {
      const btn = h("button", { type: "button", class: "btn btn-outline-info" }, [h("i", { class: "bi bi-graph-up" }), " " + T("Show graph")]);
      btn.addEventListener("click", () => { win.close(); showGraph(pointId); });
      win.root.querySelector(".hmi-window-foot").prepend(btn);
    }
  }

  // ------------------------------------------------------------------ graph
  let chartLoader = null;
  /** Load Chart.js and its date adapter once. */
  function loadChartJs() {
    if (!chartLoader) {
      chartLoader = window.Chart ? Promise.resolve() : cfg.urls.chartjs.reduce((p, src) => p.then(() => new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = src;
        s.onload = resolve;
        s.onerror = () => reject(new Error(T("Could not load {0}", src)));
        document.head.appendChild(s);
      })), Promise.resolve());
      chartLoader.catch(() => { chartLoader = null; });
    }
    return chartLoader;
  }

  /** The remembered "days back" (default 1). */
  function storedDays() {
    try {
      const v = parseFloat(window.localStorage.getItem(STORE_KEY));
      if (v > 0 && v <= 3650) return v;
    } catch (e) { /* storage unavailable */ }
    return 1;
  }

  /** Remember the "days back". */
  function storeDays(days) {
    try { window.localStorage.setItem(STORE_KEY, String(days)); } catch (e) { /* storage unavailable */ }
  }

  /**
   * Show the graph window of a tag.
   * @param {string} pointId Tag id.
   */
  async function showGraph(pointId) {
    const meta = cfg.points[pointId];
    let days = storedDays();
    const buttons = RANGES.map(([d, label]) => h("button", { type: "button", class: "btn btn-outline-secondary",
      "data-days": String(d), text: T(label) }));
    const daysInput = h("input", { type: "number", class: "form-control form-control-sm", min: "0.01", max: "3650",
      step: "any", id: "graph-days", "aria-label": T("Days back"), value: String(days) });
    const apply = h("button", { type: "button", class: "btn btn-sm btn-primary", text: T("Show") });
    const status = h("span", { class: "small text-secondary ms-auto graph-status" });
    const canvas = h("canvas", { role: "img", "aria-label": meta.label });
    const body = h("div", {}, [
      h("div", { class: "d-flex flex-wrap gap-2 align-items-center mb-2" }, [
        h("div", { class: "btn-group btn-group-sm graph-ranges", role: "group", "aria-label": T("Time range") }, buttons),
        h("label", { class: "small text-secondary", for: "graph-days", text: T("Days back") }),
        h("div", { class: "graph-days-box" }, [daysInput]),
        apply, status,
      ]),
      h("div", { class: "hmi-graph-box" }, [canvas]),
    ]);
    const saveBtn = h("button", { type: "button", class: "btn btn-outline-info", id: "graph-save" },
      [h("i", { class: "bi bi-image" }), " " + T("Save image")]);
    const trendsLink = h("a", { class: "btn btn-outline-secondary", href: cfg.urls.trends + "?tags=" + pointId + "&range=24h" },
      [h("i", { class: "bi bi-graph-up" }), " " + T("Open in Trends")]);
    const win = openWindow(meta.label + (meta.unit ? " [" + meta.unit + "]" : ""), body, [saveBtn, trendsLink], "lg");

    try {
      await loadChartJs();
    } catch (err) {
      status.textContent = err.message;
      return;
    }
    const chart = new window.Chart(canvas, {
      type: "line",
      data: { datasets: [{ label: meta.label, data: [], parsing: false, borderColor: "#0dcaf0", backgroundColor: "#0dcaf0",
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, stepped: meta.is_bool ? "before" : false, spanGaps: false }] },
      options: {
        animation: false, maintainAspectRatio: false, normalized: true,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        scales: {
          x: { type: "time", time: { tooltipFormat: "yyyy-MM-dd HH:mm:ss", displayFormats: { second: "HH:mm:ss",
            minute: "HH:mm", hour: "MM-dd HH:mm", day: "yyyy-MM-dd", week: "yyyy-MM-dd", month: "yyyy-MM" } },
            ticks: { color: TEXT, maxRotation: 0, autoSkipPadding: 16 }, grid: { color: GRID } },
          y: { ticks: { color: TEXT }, grid: { color: GRID },
            title: { display: Boolean(meta.unit && !meta.is_bool), text: meta.unit || "", color: TEXT } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (c) => " " + fmt(meta, c.parsed.y) } },
        },
      },
    });
    let range = [0, 0];
    let timer = null;

    /** Mark the active preset and fill the input. */
    function showDays() {
      buttons.forEach((b) => b.classList.toggle("active", Math.abs(parseFloat(b.dataset.days) - days) < 1e-6));
      daysInput.value = String(Math.round(days * 1000) / 1000);
    }

    /** Read the series and redraw. */
    async function load() {
      clearTimeout(timer);
      const end = Date.now();
      const start = end - days * 86400000;
      range = [start, end];
      const points = Math.max(200, Math.min(3000, Math.round(canvas.clientWidth * 2) || 1500));
      try {
        const resp = await fetch(cfg.urls.series + "?tags=" + pointId + "&start=" + Math.round(start) + "&end=" +
          Math.round(end) + "&points=" + points, { credentials: "same-origin", headers: { Accept: "application/json" } });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || resp.statusText);
        const res = data.series[pointId] || { points: [], count: 0 };
        chart.data.datasets[0].data = res.points.map((p) => ({ x: p[0], y: p[1] }));
        chart.data.datasets[0].pointRadius = res.points.length < 60 ? 2 : 0;
        chart.options.scales.x.min = data.start;
        chart.options.scales.x.max = data.end;
        chart.update("none");
        status.textContent = res.count ? T("{0} samples", res.count) + (res.reduced ? " · " + T("reduced for display") : "") +
          " · " + T("last {0}", daysText(days)) : T("No samples in this range.");
      } catch (err) {
        status.textContent = T("Loading failed: {0}", err.message || err);
      }
      timer = setTimeout(load, 15000);
    }

    /**
     * Change the range.
     * @param {number} d Days back.
     */
    function setDays(d) {
      if (!(d > 0) || d > 3650) {
        status.textContent = T("Enter a number of days between 0.01 and 3650.");
        return;
      }
      days = d;
      storeDays(d);
      showDays();
      load();
    }
    buttons.forEach((b) => b.addEventListener("click", () => setDays(parseFloat(b.dataset.days))));
    apply.addEventListener("click", () => setDays(parseFloat(daysInput.value)));
    daysInput.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); apply.click(); } });

    saveBtn.addEventListener("click", () => {
      const ratio = canvas.width / canvas.clientWidth || 1;
      const header = Math.round(52 * ratio);
      const out = document.createElement("canvas");
      out.width = canvas.width;
      out.height = canvas.height + header;
      const c2 = out.getContext("2d");
      c2.fillStyle = BG;
      c2.fillRect(0, 0, out.width, out.height);
      const rtl = document.documentElement.dir === "rtl";
      c2.direction = rtl ? "rtl" : "ltr";
      c2.textAlign = rtl ? "right" : "left";
      c2.textBaseline = "top";
      const x = rtl ? out.width - 12 * ratio : 12 * ratio;
      c2.fillStyle = TITLE;
      c2.font = "600 " + Math.round(16 * ratio) + "px system-ui, -apple-system, 'Segoe UI', Arial, sans-serif";
      c2.fillText(meta.label + (meta.unit ? " [" + meta.unit + "]" : ""), x, 8 * ratio);
      c2.fillStyle = TEXT;
      c2.font = Math.round(12 * ratio) + "px system-ui, -apple-system, 'Segoe UI', Arial, sans-serif";
      c2.fillText(fullDate(range[0]) + "  →  " + fullDate(range[1]), x, 30 * ratio);
      c2.drawImage(canvas, 0, header);
      const stamp = fullDate(Date.now()).replace(/[-:]/g, "").replace(" ", "-").slice(0, 13);
      const name = meta.label.replace(/[\\/:*?"<>|]+/g, "_").trim() + "-" + stamp + ".png";
      out.toBlob((blob) => {
        if (!blob) return;
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        if (window.HMIDialog && !document.fullscreenElement) window.HMIDialog.toast(T("Image saved: {0}", name));
        else status.textContent = T("Image saved: {0}", name);
      }, "image/png");
    });

    win.onClose(() => { clearTimeout(timer); chart.destroy(); });
    showDays();
    load();
  }

  window.HMITagMenu = { showGraph: showGraph, showDetails: showDetails };
})();
