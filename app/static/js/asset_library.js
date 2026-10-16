/**
 * Image library page: image selection and drag-and-drop moving of images
 * onto folders (tree entries, folder tiles and breadcrumb links that carry a
 * ``data-drop-folder`` attribute).
 */
(function () {
  "use strict";

  const moveForm = document.getElementById("move-form");
  const checks = () => Array.from(document.querySelectorAll(".asset-check"));
  const selectAll = document.getElementById("asset-select-all");
  const counter = document.getElementById("asset-selected-count");
  const moveBtn = document.getElementById("move-btn");

  /** Update the "n selected" label, the Move button and the card highlight. */
  function refresh() {
    const all = checks();
    const n = all.filter((c) => c.checked).length;
    if (counter) counter.textContent = T("{0} selected", n);
    if (moveBtn) moveBtn.disabled = n === 0;
    if (selectAll) {
      selectAll.checked = n > 0 && n === all.length;
      selectAll.indeterminate = n > 0 && n < all.length;
    }
    all.forEach((c) => c.closest(".asset-card").classList.toggle("asset-selected", c.checked));
  }

  checks().forEach((c) => c.addEventListener("change", refresh));
  if (selectAll) {
    selectAll.addEventListener("change", () => {
      checks().forEach((c) => { c.checked = selectAll.checked; });
      refresh();
    });
  }

  /**
   * Move images to a folder with an AJAX request, then reload the page.
   * @param {string[]} ids Asset ids.
   * @param {string} folder Target folder path ("" = not allowed, ignored).
   */
  async function moveTo(ids, folder) {
    if (!moveForm || !folder || !ids.length) return;
    const body = new FormData();
    body.append("csrf_token", moveForm.querySelector("[name=csrf_token]").value);
    body.append("folder", folder);
    ids.forEach((id) => body.append("asset_ids", id));
    const resp = await fetch(moveForm.action, { method: "POST", body: body, headers: { Accept: "application/json" } });
    if (!resp.ok) {
      const json = await resp.json().catch(() => ({}));
      window.HMIDialog.alert(json.error || T("Move failed ({0})", resp.status), { icon: "error" });
      return;
    }
    window.location.reload();
  }

  document.querySelectorAll(".asset-card[draggable=true]").forEach((card) => {
    card.addEventListener("dragstart", (ev) => {
      const own = card.dataset.assetId;
      const selected = checks().filter((c) => c.checked).map((c) => c.value);
      const ids = selected.includes(own) ? selected : [own];
      ev.dataTransfer.setData("application/x-pyplchmi-assets", JSON.stringify(ids));
      ev.dataTransfer.effectAllowed = "move";
      document.body.classList.add("asset-dragging");
    });
    card.addEventListener("dragend", () => document.body.classList.remove("asset-dragging"));
  });

  document.querySelectorAll("[data-drop-folder]").forEach((target) => {
    const folder = target.dataset.dropFolder;
    if (!folder) return; // the library root holds no images
    target.addEventListener("dragover", (ev) => {
      if (!ev.dataTransfer.types.includes("application/x-pyplchmi-assets")) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
      target.classList.add("asset-drop");
    });
    target.addEventListener("dragleave", () => target.classList.remove("asset-drop"));
    target.addEventListener("drop", (ev) => {
      target.classList.remove("asset-drop");
      const raw = ev.dataTransfer.getData("application/x-pyplchmi-assets");
      if (!raw) return;
      ev.preventDefault();
      moveTo(JSON.parse(raw), folder);
    });
  });

  refresh();
})();
