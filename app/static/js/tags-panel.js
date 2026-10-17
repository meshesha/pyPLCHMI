/**
 * Side-panel tag table (UI templates with "tags_ui": "panel").
 *
 * - "Edit" (or a click on a row) opens the tag form in a side panel, so the
 *   table stays visible.  When the form is saved the panel closes and the
 *   table reloads; the saved message is shown as a toast.
 * - The bulk bar appears when rows are ticked: export, add to a screen or
 *   delete the selected tags (POST /tags/bulk).
 */
(function () {
  "use strict";

  const panelEl = document.getElementById("tag-panel");
  if (!panelEl || !window.bootstrap) return;
  const frame = document.getElementById("tag-panel-frame");
  const full = document.getElementById("tag-panel-full");
  const title = panelEl.querySelector("#tag-panel-title span");
  const panel = bootstrap.Offcanvas.getOrCreateInstance(panelEl);
  const indexPath = panelEl.dataset.indexPath;
  const FLASH_KEY = "hmi-panel-flash";

  // A message saved before the reload.
  try {
    const msg = sessionStorage.getItem(FLASH_KEY);
    if (msg && window.HMIDialog) window.HMIDialog.toast(msg, { icon: "success" });
    sessionStorage.removeItem(FLASH_KEY);
  } catch (e) { /* storage unavailable */ }

  /**
   * Open the tag form in the panel.
   * @param {HTMLAnchorElement} link The row's edit link.
   */
  function openPanel(link) {
    const row = link.closest("tr");
    title.textContent = row ? row.querySelector("strong").textContent : "";
    full.href = link.getAttribute("href");
    frame.src = link.dataset.panelUrl;
    document.querySelectorAll("#tag-table tr.hmi-row-open").forEach((r) => r.classList.remove("hmi-row-open"));
    if (row) row.classList.add("hmi-row-open");
    panel.show();
  }

  document.querySelectorAll("#tag-table a.tag-edit").forEach((link) => {
    link.addEventListener("click", (e) => {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;  // new tab etc.
      e.preventDefault();
      openPanel(link);
    });
  });
  document.querySelectorAll("#tag-table tbody tr[data-tag-id]").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("a, button, input, label, form, select")) return;
      const link = row.querySelector("a.tag-edit");
      if (link) openPanel(link);
    });
  });

  // After saving, the form sends the frame to the tag table: close and reload.
  frame.addEventListener("load", () => {
    let loc;
    try { loc = frame.contentWindow.location; } catch (e) { return; }
    if (!loc || loc.href === "about:blank") return;
    try { frame.contentDocument.documentElement.classList.add("hmi-in-panel"); } catch (e) { /* ignore */ }
    if (loc.pathname === indexPath) {
      try {
        const flash = frame.contentDocument.querySelector("#hmi-flashes .alert");
        if (flash) sessionStorage.setItem(FLASH_KEY, flash.textContent.trim());
      } catch (e) { /* ignore */ }
      frame.src = "about:blank";
      panel.hide();
      window.location.reload();
    }
  });
  panelEl.addEventListener("hidden.bs.offcanvas", () => {
    frame.src = "about:blank";
    document.querySelectorAll("#tag-table tr.hmi-row-open").forEach((r) => r.classList.remove("hmi-row-open"));
  });

  // ------------------------------------------------------------------ bulk bar
  const bar = document.getElementById("bulk-bar");
  if (!bar || !window.HMISelection) return;
  const count = document.getElementById("bulk-count");

  /**
   * Show the bar while something is selected.
   * @param {string[]} ids Selected tag ids.
   */
  function sync(ids) {
    bar.hidden = ids.length === 0;
    count.textContent = ids.length;
    document.body.classList.toggle("hmi-bulk-open", ids.length > 0);
  }
  document.addEventListener("hmi:selection", (e) => sync(e.detail.ids));
  sync(window.HMISelection.ids());

  document.getElementById("bulk-clear").addEventListener("click", () => window.HMISelection.clear());
  document.getElementById("bulk-export").addEventListener("click", () => document.getElementById("export-selected").click());

  bar.addEventListener("submit", async (e) => {
    e.preventDefault();
    const action = e.submitter ? e.submitter.value : "";
    if (action === "screen" && !document.getElementById("bulk-screen").value) {
      document.getElementById("bulk-screen").focus();
      return;
    }
    if (action === "delete" && !(await window.HMIDialog.confirm(bar.dataset.confirmDelete, { danger: true }))) return;
    bar.querySelectorAll("input[name=ids], input[name=action]").forEach((n) => n.remove());
    window.HMISelection.ids().forEach((id) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "ids";
      input.value = id;
      bar.append(input);
    });
    const act = document.createElement("input");
    act.type = "hidden";
    act.name = "action";
    act.value = action;
    bar.append(act);
    window.HMISelection.clear();
    HTMLFormElement.prototype.submit.call(bar);
  });
})();
