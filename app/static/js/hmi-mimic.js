/**
 * pyPLCHMI graphic (Image) dashboard runtime.
 *
 * Renders the stored layout with HMIRender, polls live values and handles
 * operator interaction: buttons (toggle / set / input / momentary / link) and
 * navigation links on any element.  The update state is shown by the clock
 * in the top bar (``HMIClock``, hmi-shell.js).  Local dummy tags
 * (value per browser window) are read and written through HMILocal.
 */
(function () {
  "use strict";

  const dataEl = document.getElementById("mimic-data");
  if (!dataEl) return;
  const R = window.HMIRender;
  const data = JSON.parse(dataEl.textContent);
  const svg = document.getElementById("mimic-svg");
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
  const ctx = { points: data.points, urls: data.urls, designer: false, icons: data.icons || {} };
  const layout = data.layout;
  const elementsById = {};
  layout.elements.forEach((e) => { elementsById[e.id] = e; });
  const { nodes } = R.renderLayout(svg, layout, ctx);
  const refreshMs = Math.max(250, data.dashboard.refresh_ms || 1000);
  let lastValues = {};
  let timer = null, inFlight = false;

  /**
   * Tell whether the operator may press a button bound to a point.
   * The server decides per tag (``may_write``: writable, the "write values"
   * permission – not needed for local dummy tags – and the tag's allowed roles);
   * the API checks the same rule again on every write.
   * @param {?Object} point Point metadata.
   * @returns {boolean}
   */
  function mayWrite(point) {
    if (!point) return false;
    if (point.may_write !== undefined) return Boolean(point.may_write);
    return Boolean(data.can_write || point.local);
  }

  layout.elements.forEach((e) => {
    if (e.type === "button" && e.action !== "link" && !mayWrite(e.point && data.points[e.point])) {
      nodes[e.id].classList.add("hmi-disabled");
    }
  });

  /**
   * Show an operator message (write refused / failed) as a toast.
   * @param {string} msg Message.
   * @param {boolean} isError Error or information.
   */
  function flash(msg, isError) {
    if (window.HMIDialog) window.HMIDialog.toast(msg, { icon: isError ? "error" : "info", ms: 4000 });
  }

  /** Fetch values once, apply them, and schedule the next poll. */
  async function poll() {
    if (inFlight) return schedule();
    inFlight = true;
    try {
      const resp = await fetch(data.urls.values, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (resp.status === 401) { window.location.reload(); return; }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const json = await resp.json();
      lastValues = window.HMILocal.overlay(json.values);
      R.applyValues(nodes, layout, lastValues, ctx);
      document.dispatchEvent(new CustomEvent("hmi:values", { detail: { values: lastValues, plcs: json.plcs, timestamp: json.timestamp } }));
      if (window.HMIClock) window.HMIClock.reportPoll(json);
    } catch (err) {
      if (window.HMIClock) window.HMIClock.reportError(err);
    } finally {
      inFlight = false;
      schedule();
    }
  }

  /** Schedule the next poll (slower while the tab is hidden). */
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(poll, document.hidden ? refreshMs * 5 : refreshMs);
  }

  /**
   * Write a value to a data point.
   * @param {number} pointId Data point id.
   * @param {*} value Engineering value.
   * @returns {Promise<boolean>} true on success.
   */
  async function write(pointId, value) {
    const point = data.points[String(pointId)];
    if (point && point.local) {  // local dummy tag: only this browser window
      try {
        lastValues[String(pointId)] = window.HMILocal.write(pointId, point.local, value);
        R.applyValues(nodes, layout, lastValues, ctx);
        return true;
      } catch (err) {
        flash(T("invalid value: {0}", err.message), true);
        return false;
      }
    }
    try {
      const resp = await fetch(R.urlFor(data.urls.write, pointId), {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ value: value }),
      });
      const json = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(json.error || "HTTP " + resp.status);
      lastValues[String(pointId)] = json;
      R.applyValues(nodes, layout, lastValues, ctx);
      return true;
    } catch (err) {
      flash(T("write failed: {0}", err.message), true);
      return false;
    }
  }

  /**
   * Convert a button's configured text value to the point's type.
   * @param {Object} point Point metadata.
   * @param {string} text Configured value.
   * @returns {*}
   */
  function typedValue(point, text) {
    if (point.is_bool) return ["1", "true", "on", "yes"].includes(String(text).trim().toLowerCase());
    if (point.is_numeric) return parseFloat(text);
    return text;
  }

  /**
   * Ask the operator for a value (SweetAlert2 dialog).
   * @param {Object} el Button element.
   * @param {Object} point Point metadata.
   * @returns {Promise<*>} Entered value, or undefined if cancelled.
   */
  async function askValue(el, point) {
    const current = lastValues[String(point.id)];
    const now = current && current.value !== null && current.value !== undefined ? current.value : "";
    const info = point.label + " · " + point.address + (point.unit ? " · " + point.unit : "");
    if (point.is_bool) {
      const v = await window.HMIDialog.prompt({
        title: el.text || point.label, text: info, input: "select",
        options: { "true": point.true_text || T("ON"), "false": point.false_text || T("OFF") },
        value: R.isOn(now) ? "true" : "false", confirmText: T("Write"),
      });
      return v === undefined ? undefined : v === "true";
    }
    const v = await window.HMIDialog.prompt({
      title: el.text || point.label, text: info, input: point.is_numeric ? "number" : "text",
      value: now, confirmText: T("Write"),
    });
    if (v === undefined) return undefined;
    return point.is_numeric ? parseFloat(v) : v;
  }

  /**
   * Execute a button click.
   * @param {Object} el Button element.
   */
  async function pressButton(el) {
    if (el.action === "link") return;
    const point = el.point && data.points[el.point];
    if (!point) { flash(T("Button has no tag."), true); return; }
    if (!point.writable) { flash(T("{0} is read-only.", point.label), true); return; }
    if (!mayWrite(point)) { flash(T("Your role may not write {0}.", point.label), true); return; }
    let value;
    if (el.action === "toggle") {
      const cur = lastValues[String(point.id)];
      value = !(cur && R.isOn(cur.value));
    } else if (el.action === "set") {
      value = typedValue(point, el.value);
    } else if (el.action === "input") {
      value = await askValue(el, point);
      if (value === undefined || (typeof value === "number" && Number.isNaN(value))) return;
      return write(point.id, value);
    } else {
      return;
    }
    if (el.confirm && !point.local
        && !(await window.HMIDialog.confirm(T("Write {0} to {1}?", JSON.stringify(value), point.label)))) return;
    write(point.id, value);
  }

  // Momentary push buttons: TRUE while pressed, FALSE on release.
  let pressed = null;
  svg.addEventListener("pointerdown", (ev) => {
    const g = ev.target.closest(".hmi-el");
    const el = g && elementsById[g.dataset.id];
    if (!el || el.type !== "button" || el.action !== "momentary") return;
    const point = data.points[el.point];
    if (!point || !point.writable || !mayWrite(point)) return;
    pressed = point.id;
    g.classList.add("hmi-pressed");
    svg.setPointerCapture(ev.pointerId);
    write(point.id, true);
  });
  /** Release a momentary button. */
  function release() {
    if (pressed === null) return;
    write(pressed, false);
    pressed = null;
    svg.querySelectorAll(".hmi-pressed").forEach((n) => n.classList.remove("hmi-pressed"));
  }
  svg.addEventListener("pointerup", release);
  svg.addEventListener("pointercancel", release);

  svg.addEventListener("click", (ev) => {
    const g = ev.target.closest(".hmi-el");
    const el = g && elementsById[g.dataset.id];
    if (!el) return;
    if (el.type === "button" && el.action !== "link") { pressButton(el); return; }
    if (el.link) {
      const target = R.urlFor(data.urls.go, el.link);
      // inside the full-screen frame: stay in frame mode (hmi-shell.js)
      window.location.href = window.HMIEmbed ? target + (target.includes("?") ? "&" : "?") + "embed=1" : target;
    }
  });

  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  poll();
})();
