/**
 * pyPLCHMI dashboard runtime.
 *
 * Polls /api/dashboards/<id>/values every `refresh_ms` milliseconds and
 * updates each tile (the update state is shown by the clock in the top bar,
 * ``HMIClock`` in hmi-shell.js); sends operator writes to /api/datapoints/<id>/write.
 * Local dummy tags (tiles with data-local) keep their value in this browser
 * window through HMILocal and never reach the server.
 * All POST requests carry the CSRF token from the <meta name="csrf-token"> tag.
 *
 * High-performance tiles (UI templates with "tiles": "hp", grid class
 * hmi-grid-hp) additionally get: a pointer on the range bar, a one-hour
 * trend line for logged tags (refreshed every minute), alarm highlighting
 * from the "hmi:alarms" event and a "Change…" button that asks for the new
 * value in a dialog.
 */
(function () {
  "use strict";

  const grid = document.getElementById("hmi-grid");
  if (!grid) return;

  const valuesUrl = grid.dataset.valuesUrl;
  const writeUrlTemplate = grid.dataset.writeUrlTemplate;
  const refreshMs = Math.max(250, parseInt(grid.dataset.refreshMs, 10) || 1000);
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

  /** Map of data point id -> tile element. */
  const tiles = {};
  grid.querySelectorAll(".hmi-tile").forEach((el) => { tiles[el.dataset.id] = el; });

  let timer = null;
  let inFlight = false;

  /**
   * Build the write URL for a data point id from the server-side template.
   * @param {string} id Data point id.
   * @returns {string} URL such as /api/datapoints/12/write
   */
  function writeUrl(id) {
    return writeUrlTemplate.replace("/0/", "/" + encodeURIComponent(id) + "/");
  }

  /**
   * Update one tile with a value object from the API.
   * @param {HTMLElement} tile The tile element.
   * @param {{value:*, display:string, quality:string, error:?string}} v Value object.
   */
  function renderTile(tile, v) {
    const valueEl = tile.querySelector(".hmi-value");
    const errEl = tile.querySelector(".hmi-error");
    tile.classList.toggle("bad", v.quality !== "good");
    tile.classList.toggle("stale", Boolean(v.stale));
    tile.title = v.stale ? T("PLC briefly unreachable – showing the last good value") : "";
    if (valueEl) valueEl.textContent = v.display;
    errEl.textContent = v.error || "";

    const lamp = tile.querySelector(".hmi-lamp");
    if (lamp) {
      lamp.classList.toggle("on", v.value === true);
      tile.dataset.state = v.value === true ? "1" : "0";
    }

    const fill = tile.querySelector(".hmi-bar-fill");
    if (fill) {
      const min = parseFloat(tile.dataset.min);
      const max = parseFloat(tile.dataset.max);
      let pct = 0;
      if (typeof v.value === "number" && max > min) {
        pct = Math.min(100, Math.max(0, ((v.value - min) / (max - min)) * 100));
      }
      fill.style.width = pct + "%";
    }

    const pointer = tile.querySelector(".hmi-hp-pointer");
    if (pointer) {
      const min = parseFloat(tile.dataset.min);
      const max = parseFloat(tile.dataset.max);
      const ok = typeof v.value === "number" && max > min;
      pointer.hidden = !ok;
      if (ok) {
        pointer.style.insetInlineStart = Math.min(100, Math.max(0, ((v.value - min) / (max - min)) * 100)) + "%";
        tile.classList.toggle("out-of-range", v.value < min || v.value > max);
      }
    }
  }

  /** Fetch all values once and update the screen, then schedule the next poll. */
  async function poll() {
    if (inFlight) return schedule();
    inFlight = true;
    try {
      const resp = await fetch(valuesUrl, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      if (resp.status === 401) { window.location.reload(); return; }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      const values = window.HMILocal.overlay(data.values);
      Object.entries(values).forEach(([id, v]) => { if (tiles[id]) renderTile(tiles[id], v); });
      document.dispatchEvent(new CustomEvent("hmi:values", { detail: { values: values, plcs: data.plcs, timestamp: data.timestamp } }));
      if (window.HMIClock) window.HMIClock.reportPoll(data);
    } catch (err) {
      if (window.HMIClock) window.HMIClock.reportError(err);
    } finally {
      inFlight = false;
      schedule();
    }
  }

  /** Schedule the next poll (pauses automatically while the tab is hidden). */
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(poll, document.hidden ? refreshMs * 5 : refreshMs);
  }

  /**
   * Send a value to the PLC through the API.
   * @param {HTMLElement} tile Tile of the data point.
   * @param {*} value Engineering value.
   */
  async function writeValue(tile, value) {
    const errEl = tile.querySelector(".hmi-error");
    if (tile.dataset.local) {  // local dummy tag: only this browser window
      try {
        renderTile(tile, window.HMILocal.write(tile.dataset.id, JSON.parse(tile.dataset.local), value));
      } catch (err) {
        errEl.textContent = T("invalid value: {0}", err.message);
      }
      return;
    }
    errEl.textContent = T("writing…");
    try {
      const resp = await fetch(writeUrl(tile.dataset.id), {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ value: value }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
      renderTile(tile, data);
    } catch (err) {
      errEl.textContent = T("write failed: {0}", err.message);
    }
  }

  // Write forms (numeric / text / bool select)
  grid.querySelectorAll("form.hmi-write").forEach((form) => {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const tile = form.closest(".hmi-tile");
      const field = form.querySelector("input, select");
      let value = field.value;
      if (field.tagName === "SELECT") value = value === "true";
      else if (field.type === "number") value = parseFloat(value);
      if (!tile.dataset.local && !(await window.HMIDialog.confirm(
        T("Write {0} to {1}?", field.tagName === "SELECT" ? field.options[field.selectedIndex].text : value,
          tile.querySelector(".hmi-label").textContent)))) return;
      writeValue(tile, value);
      if (field.tagName === "INPUT") field.value = "";
    });
  });

  // Toggle buttons for switch widgets
  grid.querySelectorAll(".hmi-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tile = btn.closest(".hmi-tile");
      writeValue(tile, tile.dataset.state !== "1");
    });
  });

  // ------------------------------------------------------------ high-performance tiles
  // "Change…" buttons: ask for the value in a dialog, confirm, write.
  grid.querySelectorAll(".hmi-change").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const tile = btn.closest(".hmi-tile");
      const label = tile.querySelector(".hmi-label").textContent;
      const kind = btn.dataset.input;
      const current = (tile.querySelector(".hmi-value") || {}).textContent || "";
      const opts = { title: label, text: T("Current value: {0}", current), input: kind === "bool" ? "select" : kind };
      if (kind === "bool") opts.options = { "true": btn.dataset.true, "false": btn.dataset.false };
      const raw = await window.HMIDialog.prompt(opts);
      if (raw === undefined || raw === "") return;
      let value = raw;
      let shown = raw;
      if (kind === "bool") { value = raw === "true"; shown = value ? btn.dataset.true : btn.dataset.false; }
      else if (kind === "number") { value = parseFloat(raw); shown = raw + (btn.dataset.unit ? " " + btn.dataset.unit : ""); }
      if (!tile.dataset.local && !(await window.HMIDialog.confirm(T("Write {0} to {1}?", shown, label)))) return;
      writeValue(tile, value);
    });
  });

  // Alarm highlighting: tiles whose tag has an active alarm.
  document.addEventListener("hmi:alarms", (e) => {
    const byTag = {};
    (e.detail.events || []).forEach((ev) => {
      if (ev.tag_id == null) return;
      const prev = byTag[ev.tag_id];
      const rank = { critical: 3, high: 2, low: 1 };
      if (!prev || (rank[ev.severity] || 0) > (rank[prev.severity] || 0)) byTag[ev.tag_id] = ev;
    });
    Object.entries(tiles).forEach(([id, tile]) => {
      const ev = byTag[id];
      tile.classList.toggle("in-alarm", Boolean(ev));
      tile.classList.toggle("alarm-unacked", Boolean(ev && !ev.acked_at));
      tile.style.setProperty("--sev", ev ? ev.color : "");
      const badge = tile.querySelector(".hmi-hp-alarm");
      if (badge) {
        badge.hidden = !ev;
        badge.textContent = ev ? T(ev.severity_label) : "";
        badge.title = ev ? ev.name : "";
      }
    });
  });

  // One-hour trend lines of logged tags.
  const seriesUrl = grid.dataset.seriesUrl;
  const sparks = Array.from(grid.querySelectorAll(".hmi-spark"));
  /** Fetch the last hour of the logged tags and draw the trend lines. */
  async function drawSparks() {
    if (!seriesUrl || !sparks.length) return;
    const ids = sparks.map((svg) => svg.closest(".hmi-tile").dataset.id);
    try {
      const resp = await fetch(seriesUrl + "?points=60&tags=" + ids.join(","), { credentials: "same-origin" });
      if (!resp.ok) return;
      const data = await resp.json();
      sparks.forEach((svg) => {
        const id = svg.closest(".hmi-tile").dataset.id;
        const pts = ((data.series[id] || {}).points || []).filter((p) => p[1] !== null);
        const line = svg.querySelector("polyline");
        if (pts.length < 2) { line.setAttribute("points", ""); return; }
        const ys = pts.map((p) => p[1]);
        const lo = Math.min.apply(null, ys);
        const hi = Math.max.apply(null, ys);
        // x axis: from the first to the last sample (a short history still fills the width)
        const t0 = pts[0][0];
        const span = Math.max(1, pts[pts.length - 1][0] - t0);
        line.setAttribute("points", pts.map((p) => (((p[0] - t0) / span) * 100).toFixed(1) + "," +
          (hi > lo ? 22 - ((p[1] - lo) / (hi - lo)) * 20 : 12).toFixed(1)).join(" "));
      });
    } catch (err) {
      console.warn("trend:", err);
    }
  }
  drawSparks();
  if (sparks.length) setInterval(drawSparks, 60000);

  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  poll();
})();
