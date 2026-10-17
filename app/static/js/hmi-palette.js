/**
 * Quick search ("command palette") of UI templates with "command_palette": true.
 *
 * Ctrl+K (or ⌘K, or the search button in the top bar) opens a box that finds
 * pages, screens, tags and PLCs through /api/search (only what the user may
 * open).  Arrow keys choose, Enter opens, Esc closes.
 */
(function () {
  "use strict";

  const modalEl = document.getElementById("hmi-palette");
  if (!modalEl || !window.bootstrap) return;
  const input = document.getElementById("hmi-palette-input");
  const list = document.getElementById("hmi-palette-list");
  const empty = document.getElementById("hmi-palette-empty");
  const url = modalEl.dataset.searchUrl;
  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  let results = [];
  let index = 0;
  let seq = 0;
  let timer = null;

  /** Highlight the chosen result. */
  function mark() {
    list.querySelectorAll("[role=option]").forEach((li, i) => {
      li.setAttribute("aria-selected", i === index ? "true" : "false");
      if (i === index) {
        li.scrollIntoView({ block: "nearest" });
        input.setAttribute("aria-activedescendant", li.id);
      }
    });
  }

  /** Draw the result list. */
  function draw() {
    list.replaceChildren();
    empty.hidden = results.length > 0;
    results.forEach((r, i) => {
      const li = document.createElement("li");
      li.id = "hmi-palette-opt-" + i;
      li.setAttribute("role", "option");
      li.className = "hmi-palette-item";
      const icon = document.createElement("i");
      icon.className = "bi bi-" + (r.icon || "arrow-right");
      icon.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.className = "hmi-palette-label";
      label.textContent = r.label;
      const detail = document.createElement("span");
      detail.className = "hmi-palette-detail";
      detail.textContent = r.detail || "";
      li.append(icon, label, detail);
      li.addEventListener("mousemove", () => { if (index !== i) { index = i; mark(); } });
      li.addEventListener("click", () => go(r));
      list.append(li);
    });
    index = Math.min(index, Math.max(0, results.length - 1));
    mark();
  }

  /** Ask the server for results (debounced). */
  function search() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++seq;
      try {
        const resp = await fetch(url + "?q=" + encodeURIComponent(input.value.trim()), { credentials: "same-origin" });
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        const data = await resp.json();
        if (mine !== seq) return;          // an older answer arrived late
        results = data.results || [];
        index = 0;
        draw();
      } catch (err) {
        console.warn("search:", err);
      }
    }, 120);
  }

  /**
   * Open a result.
   * @param {{url:string}} r Result.
   */
  function go(r) {
    if (!r) return;
    modal.hide();
    window.location.assign(r.url);
  }

  /** Open the palette. */
  function open() {
    if (modalEl.classList.contains("show")) return;
    if (document.querySelector(".modal.show")) return;   // e.g. an alarm window: it comes first
    input.value = "";
    modal.show();
    search();
  }

  modalEl.addEventListener("shown.bs.modal", () => input.focus());
  input.addEventListener("input", search);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); index = Math.min(results.length - 1, index + 1); mark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); index = Math.max(0, index - 1); mark(); }
    else if (e.key === "Enter") { e.preventDefault(); go(results[index]); }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      open();
    }
  });
  const btn = document.getElementById("hmi-palette-btn");
  if (btn) btn.addEventListener("click", open);
  window.HMIPalette = { open: open };
})();
