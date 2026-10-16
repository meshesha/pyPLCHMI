/**
 * "Existing tag" picker for the screen editors (grid editor and image designer).
 *
 * Searches the tag table through GET /api/tags/search (label, address, PLC,
 * type) and lets the user tick one or more tags.  Tags already on the screen
 * are shown but cannot be ticked again.
 *
 * Usage:
 *   HMITagPicker.open({ screenId: 3, multiple: true, onPick: (tags) => {...} });
 * ``tags`` is an array of {id, label, plc, address, data_type, unit, screens}.
 */
(function () {
  "use strict";

  const modalEl = document.getElementById("tag-picker");
  if (!modalEl) return;
  const q = document.getElementById("tag-picker-q");
  const rows = document.getElementById("tag-picker-rows");
  const info = document.getElementById("tag-picker-info");
  const count = document.getElementById("tag-picker-count");
  const ok = document.getElementById("tag-picker-ok");

  let options = {};
  let found = [];
  const selected = new Map();
  let timer = null;
  let seq = 0;

  /** Update the "n selected" text and the OK button. */
  function syncCount() {
    count.textContent = T("{0} selected", selected.size);
    ok.disabled = selected.size === 0;
  }

  /**
   * Create a table cell with text.
   * @param {string} text - Cell text.
   * @param {string} [cls] - Classes.
   * @returns {HTMLTableCellElement}
   */
  function cell(text, cls) {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    return td;
  }

  /** Render the search results. */
  function render() {
    rows.replaceChildren();
    if (!found.length) {
      const tr = document.createElement("tr");
      const td = cell(T("No tags found."), "text-secondary");
      td.colSpan = 6;
      tr.append(td);
      rows.append(tr);
      return;
    }
    found.forEach((t) => {
      const tr = document.createElement("tr");
      tr.dataset.tagId = t.id;
      const box = document.createElement("input");
      box.type = options.multiple ? "checkbox" : "radio";
      box.name = "tag-picker-choice";
      box.className = "form-check-input";
      box.disabled = t.on_screen;
      box.checked = selected.has(t.id);
      box.setAttribute("aria-label", T("Select {0}", t.label));
      const toggle = () => {
        if (t.on_screen) return;
        if (!options.multiple) selected.clear();
        if (selected.has(t.id) && options.multiple) selected.delete(t.id); else selected.set(t.id, t);
        render();
        syncCount();
      };
      box.addEventListener("click", (e) => { e.stopPropagation(); toggle(); });
      tr.addEventListener("click", toggle);
      tr.addEventListener("dblclick", () => { if (!t.on_screen && !options.multiple) { selected.clear(); selected.set(t.id, t); finish(); } });
      const first = document.createElement("td");
      first.append(box);
      const label = cell("", "");
      const strong = document.createElement("strong");
      strong.textContent = t.label;
      label.append(strong);
      if (t.on_screen) {
        const badge = document.createElement("span");
        badge.className = "badge text-bg-secondary ms-2";
        badge.textContent = T("on this screen");
        label.append(badge);
      }
      tr.classList.toggle("table-active", selected.has(t.id));
      tr.classList.toggle("opacity-50", t.on_screen);
      tr.append(first, label, cell(t.plc), cell(t.address, "font-monospace small"), cell(t.data_type + (t.unit ? " [" + t.unit + "]" : ""), "small"),
        cell(t.screens || "—", "small text-secondary"));
      rows.append(tr);
    });
  }

  /** Query the tag table for the current search text. */
  async function search() {
    const my = ++seq;
    const url = new URL(modalEl.dataset.searchUrl, window.location.origin);
    url.searchParams.set("q", q.value.trim());
    url.searchParams.set("limit", "100");
    if (options.screenId) url.searchParams.set("screen", options.screenId);
    info.textContent = T("searching…");
    try {
      const resp = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      if (my !== seq) return;
      found = data.tags;
      info.textContent = data.more ? T("{0}+ tags – refine the search to see more", found.length) : T("{0} tag(s)", found.length);
      render();
    } catch (err) {
      info.textContent = T("Search failed: {0}", err.message);
    }
  }

  /** Hand the selection to the caller and close the dialog. */
  function finish() {
    const picked = Array.from(selected.values());
    bootstrap.Modal.getOrCreateInstance(modalEl).hide();
    if (picked.length && options.onPick) options.onPick(picked);
  }

  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 250); });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); clearTimeout(timer); search(); } });
  ok.addEventListener("click", finish);
  modalEl.addEventListener("shown.bs.modal", () => q.focus());

  window.HMITagPicker = {
    /**
     * Open the picker.
     * @param {{screenId?: number, multiple?: boolean, title?: string, okText?: string, onPick: function(Object[])}} opts
     */
    open(opts) {
      options = Object.assign({ multiple: true }, opts || {});
      selected.clear();
      found = [];
      q.value = "";
      document.getElementById("tag-picker-title").lastChild.textContent = " " + (options.title || T("Add existing tags"));
      ok.textContent = options.okText || T("Add to screen");
      syncCount();
      render();
      bootstrap.Modal.getOrCreateInstance(modalEl).show();
      search();
    },
  };
})();
