/**
 * pyPLCHMI dashboard runtime.
 *
 * Polls /api/dashboards/<id>/values every `refresh_ms` milliseconds and
 * updates each tile (the update state is shown by the clock in the top bar,
 * ``HMIClock`` in hmi-shell.js); sends operator writes to /api/datapoints/<id>/write.
 * Local dummy tags (tiles with data-local) keep their value in this browser
 * window through HMILocal and never reach the server.
 * All POST requests carry the CSRF token from the <meta name="csrf-token"> tag.
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


  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  poll();
})();
