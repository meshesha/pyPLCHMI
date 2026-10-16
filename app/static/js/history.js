/**
 * Tag history pages (History table and the chart list).
 *
 * - Tag pickers (``.hmi-tag-picker``): search box, select all / none and a
 *   button label that shows how many tags are chosen.
 * - Quick range buttons (``[data-last]``): fill the "From" field with
 *   "now − N seconds" (browser time), clear "to", and apply the filter.
 * - The quick-chart form needs at least one tag.
 */
(function () {
  "use strict";

  /**
   * Format a Date for a datetime-local input (local time, seconds).
   * @param {Date} d The date.
   * @returns {string} ``YYYY-MM-DDTHH:MM:SS``.
   */
  function localInput(d) {
    const p = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" +
      p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }
  window.HMIHistory = { localInput: localInput };

  /**
   * Wire one tag picker.
   * @param {HTMLElement} root The ``.hmi-tag-picker`` element.
   */
  function setupPicker(root) {
    const label = root.querySelector(".picker-label");
    const boxes = () => Array.from(root.querySelectorAll("input[type=checkbox]"));

    /** Update the button text from the checked boxes. */
    function refresh() {
      const checked = boxes().filter((b) => b.checked);
      if (!checked.length) label.textContent = root.dataset.empty;
      else if (checked.length === 1) label.textContent = checked[0].dataset.label;
      else label.textContent = T("{0} tags", checked.length);
    }

    root.addEventListener("change", refresh);
    const search = root.querySelector(".picker-search");
    if (search) {
      search.addEventListener("input", () => {
        const q = search.value.trim().toLowerCase();
        root.querySelectorAll(".picker-item").forEach((item) => {
          item.classList.toggle("d-none", Boolean(q) && !item.dataset.search.includes(q));
        });
      });
      search.addEventListener("keydown", (ev) => { if (ev.key === "Enter") ev.preventDefault(); });
    }
    const visible = () => boxes().filter((b) => !b.closest(".picker-item").classList.contains("d-none"));
    const all = root.querySelector(".picker-all");
    const none = root.querySelector(".picker-none");
    if (all) all.addEventListener("click", () => { visible().forEach((b) => { b.checked = true; }); refresh(); });
    if (none) none.addEventListener("click", () => { visible().forEach((b) => { b.checked = false; }); refresh(); });
    refresh();
  }

  document.querySelectorAll(".hmi-tag-picker").forEach(setupPicker);

  // Quick ranges on the History page.
  const filter = document.getElementById("history-filter");
  if (filter) {
    filter.querySelectorAll("[data-last]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const seconds = Number(btn.dataset.last);
        filter.querySelector("[name=from]").value = localInput(new Date(Date.now() - seconds * 1000));
        filter.querySelector("[name=to]").value = "";
        filter.requestSubmit ? filter.requestSubmit() : filter.submit();
      });
    });
  }

  // Quick chart: at least one tag.
  const quick = document.getElementById("quick-chart");
  if (quick) {
    quick.addEventListener("submit", (ev) => {
      if (!quick.querySelector("input[name=t]:checked")) {
        ev.preventDefault();
        HMIDialog.alert(T("Choose at least one tag with history logging."), { icon: "info" });
      }
    });
  }
})();
