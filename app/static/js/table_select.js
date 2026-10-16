/**
 * Row selection for the Tags and Alarms tables and "Export → Selected".
 *
 * Rows carry <input class="row-select" value="<id>">; the header box
 * #select-all ticks every row on the page.  "Export selected" posts the ids
 * (with the CSRF token) to the export URL, which answers with the .xlsx file.
 * The selection is remembered per page in sessionStorage so moving between
 * pages keeps it.
 */
(function () {
  "use strict";

  const exportBtn = document.getElementById("export-selected");
  if (!exportBtn) return;
  const all = document.getElementById("select-all");
  const boxes = Array.from(document.querySelectorAll("input.row-select"));
  const countEl = document.getElementById("selected-count");
  const key = "hmi-selection:" + window.location.pathname;

  /** Read the remembered selection. */
  function load() {
    try { return new Set(JSON.parse(sessionStorage.getItem(key) || "[]")); } catch (e) { return new Set(); }
  }

  /** Remember the selection. */
  function save(set) {
    try { sessionStorage.setItem(key, JSON.stringify(Array.from(set))); } catch (e) { /* storage unavailable */ }
  }

  const selected = load();
  boxes.forEach((b) => { b.checked = selected.has(b.value); });

  /** Update the counter, the button and the header box. */
  function sync() {
    countEl.textContent = selected.size;
    exportBtn.disabled = selected.size === 0;
    const ticked = boxes.filter((b) => b.checked).length;
    if (all) {
      all.checked = boxes.length > 0 && ticked === boxes.length;
      all.indeterminate = ticked > 0 && ticked < boxes.length;
    }
    boxes.forEach((b) => b.closest("tr").classList.toggle("table-active", b.checked));
  }

  boxes.forEach((b) => b.addEventListener("change", () => {
    if (b.checked) selected.add(b.value); else selected.delete(b.value);
    save(selected);
    sync();
  }));
  if (all) {
    all.addEventListener("change", () => {
      boxes.forEach((b) => { b.checked = all.checked; if (all.checked) selected.add(b.value); else selected.delete(b.value); });
      save(selected);
      sync();
    });
  }

  exportBtn.addEventListener("click", () => {
    const form = document.createElement("form");
    form.method = "post";
    form.action = exportBtn.dataset.url;
    form.hidden = true;
    const csrf = document.createElement("input");
    csrf.type = "hidden";
    csrf.name = "csrf_token";
    csrf.value = document.querySelector('meta[name="csrf-token"]').content;
    form.append(csrf);
    selected.forEach((id) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "ids";
      input.value = id;
      form.append(input);
    });
    document.body.append(form);
    form.submit();
    form.remove();
  });

  const clear = document.getElementById("clear-selection");
  if (clear) {
    clear.addEventListener("click", () => {
      selected.clear();
      save(selected);
      boxes.forEach((b) => { b.checked = false; });
      sync();
    });
  }
  sync();
})();
