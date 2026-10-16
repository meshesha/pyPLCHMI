/**
 * pyPLCHMI local dummy tags.
 *
 * A *local* dummy tag has its own value in every browser window: the value is
 * kept in the window's ``sessionStorage`` (key ``pyplchmi-local:<tag id>``),
 * so it survives a page reload and moving between screens in the same window,
 * is independent of other windows, tabs and computers, and disappears when the
 * window is closed.  The server only sends the tag's description (``local``
 * spec, see ``DataPoint.local_spec``) and never stores the value.
 *
 * Used by the grid runtime (dashboard.js), the Image screen runtime
 * (hmi-mimic.js), the designer's live preview and the tag table.
 */
window.HMILocal = (function () {
  "use strict";

  const PREFIX = "pyplchmi-local:";
  /** Fallback store when sessionStorage is not available (private mode, sandbox). */
  const memory = {};

  /**
   * Read the stored value of a tag in this window.
   * @param {string|number} id Tag id.
   * @returns {*} The value, or undefined when none is stored.
   */
  function load(id) {
    try {
      const raw = window.sessionStorage.getItem(PREFIX + id);
      return raw === null ? undefined : JSON.parse(raw);
    } catch (e) {
      return memory[id];
    }
  }

  /**
   * Store a tag value for this window.
   * @param {string|number} id Tag id.
   * @param {*} value Value (JSON-serialisable).
   */
  function save(id, value) {
    memory[id] = value;
    try { window.sessionStorage.setItem(PREFIX + id, JSON.stringify(value)); } catch (e) { /* memory only */ }
  }

  /**
   * Forget the value of a tag in this window (back to its start value).
   * @param {string|number} id Tag id.
   */
  function clear(id) {
    delete memory[id];
    try { window.sessionStorage.removeItem(PREFIX + id); } catch (e) { /* ignore */ }
  }

  /**
   * Convert and check a value for a local tag (same rules as the server applies to dummy tags).
   * @param {Object} spec Local spec ({bool, numeric, integer, range, string_length, data_type}).
   * @param {*} value Raw input (boolean, number or text).
   * @returns {*} The converted value.
   * @throws {Error} When the value does not fit the data type.
   */
  function coerce(spec, value) {
    if (spec.bool) {
      if (typeof value === "string") return ["1", "true", "on", "yes"].includes(value.trim().toLowerCase());
      return Boolean(value);
    }
    if (spec.numeric) {
      const n = typeof value === "string" ? Number(value.trim().replace(",", ".")) : Number(value);
      if (value === "" || value === null || Number.isNaN(n) || !Number.isFinite(n)) throw new Error("not a number: " + value);
      if (!spec.integer) return n;
      const i = Math.round(n);
      if (spec.range && (i < spec.range[0] || i > spec.range[1])) {
        throw new Error(i + " out of range for " + spec.data_type + " (" + spec.range[0] + ".." + spec.range[1] + ")");
      }
      return i;
    }
    const text = value === null || value === undefined ? "" : String(value);
    return spec.string_length ? text.slice(0, spec.string_length) : text;
  }

  /**
   * Format a value like the server does (texts for BOOL, decimals and unit for numbers).
   * @param {Object} spec Local spec.
   * @param {*} value Value.
   * @returns {string} Display text.
   */
  function format(spec, value) {
    if (value === null || value === undefined) return "—";
    if (spec.bool) return value ? spec.true_text : spec.false_text;
    let text;
    if (spec.numeric) text = spec.integer ? String(Math.round(value)) : Number(value).toFixed(spec.decimals);
    else text = String(value);
    return (text + " " + (spec.unit || "")).trim();
  }

  /**
   * Build a value object shaped like the API's ``values`` entries.
   * @param {Object} spec Local spec.
   * @param {*} value Value.
   * @returns {Object} {value, raw, display, quality, error, stale, local}
   */
  function entry(spec, value) {
    return { value: value, raw: value, display: format(spec, value), quality: "good", error: null, stale: false,
      local: spec, localTag: true };
  }

  /**
   * Current value of a local tag in this window (its start value if never set or no longer valid).
   * @param {string|number} id Tag id.
   * @param {Object} spec Local spec.
   * @returns {*} Value.
   */
  function current(id, spec) {
    const stored = load(id);
    if (stored === undefined) return spec.initial;
    try { return coerce(spec, stored); } catch (e) { return spec.initial; }
  }

  /**
   * Replace the server's placeholder entries of local tags by this window's values (in place).
   * @param {Object<string, Object>} values Values map from the API.
   * @returns {Object<string, Object>} The same map.
   */
  function overlay(values) {
    Object.keys(values || {}).forEach((id) => {
      const v = values[id];
      if (v && v.local) values[id] = entry(v.local, current(id, v.local));
    });
    return values;
  }

  /**
   * Set a local tag's value for this window.
   * @param {string|number} id Tag id.
   * @param {Object} spec Local spec.
   * @param {*} value New value.
   * @returns {Object} Value entry (as returned by the write API for server tags).
   * @throws {Error} When the value does not fit the data type.
   */
  function write(id, spec, value) {
    const v = coerce(spec, value);
    save(id, v);
    return entry(spec, v);
  }

  return { load: load, save: save, clear: clear, coerce: coerce, format: format, entry: entry, current: current,
    overlay: overlay, write: write };
})();
