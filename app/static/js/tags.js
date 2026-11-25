/**
 * Tag table: live "Value" column.
 *
 * Polls GET /api/tags/values?ids=<tags on this page> and writes the
 * formatted value (or the read error) into each row.  The next request is
 * planned only when the previous one has finished (never two at once), so a
 * slow PLC cannot pile up requests on the server; a request that takes
 * longer than ``TIMEOUT_MS`` is cancelled.  Polling pauses while the browser
 * tab is hidden and resumes at once when it is shown again.
 */
(function () {
  "use strict";

  const table = document.getElementById("tag-table");
  if (!table) return;
  const cells = {};
  table.querySelectorAll("[data-tag-value]").forEach((td) => { cells[td.dataset.tagValue] = td; });
  const ids = Object.keys(cells);
  if (!ids.length) return;
  const url = table.dataset.valuesUrl + "?ids=" + ids.join(",");
  const PERIOD_MS = 3000;
  const TIMEOUT_MS = 20000;
  let timer = null;
  let inFlight = false;

  /**
   * Show one reading in its cell.
   * @param {HTMLElement} td - The value cell.
   * @param {object|undefined} v - Reading {display, quality, error}.
   */
  function show(td, v) {
    td.replaceChildren();
    const span = document.createElement("span");
    if (!v) {
      span.className = "text-secondary";
      span.textContent = "—";
    } else if (v.local) {
      const own = window.HMILocal ? window.HMILocal.overlay({ [td.dataset.tagValue]: v })[td.dataset.tagValue] : v;
      span.className = "font-monospace text-info";
      span.textContent = own.display;
      span.title = T("Local dummy tag – value of this browser window (every window has its own)");
    } else if (v.quality === "good") {
      span.className = "font-monospace" + (v.stale ? " text-warning" : "");
      span.textContent = v.display;
      if (v.stale) span.title = T("PLC reconnecting – last good value");
    } else {
      span.className = "text-danger small";
      span.innerHTML = '<i class="bi bi-exclamation-triangle"></i> ';
      span.append(document.createTextNode(v.error || T("bad quality")));
    }
    td.append(span);
  }

  /** Fetch the values once, update the table, then plan the next request. */
  async function poll() {
    clearTimeout(timer);
    if (document.hidden || inFlight) return;   // resumed by visibilitychange / the running request
    inFlight = true;
    const ctrl = new AbortController();
    const watchdog = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    try {
      const resp = await fetch(url, { credentials: "same-origin", signal: ctrl.signal });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      ids.forEach((id) => show(cells[id], data.values[id]));
    } catch (err) {
      ids.forEach((id) => {
        cells[id].innerHTML = '<span class="text-secondary small"></span>';
        cells[id].firstChild.textContent = T("no data");
      });
    } finally {
      clearTimeout(watchdog);
      inFlight = false;
      if (!document.hidden) timer = setTimeout(poll, PERIOD_MS);
    }
  }

  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  poll();
})();
