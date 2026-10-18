/**
 * Live check of tag names (tag form, screen tag form, designer "New tag" dialog).
 *
 * The <script> tag carries:
 *   data-url    – /api/tags/check-name
 *   data-input  – CSS selector of the name input
 *   data-tag-id – id of the tag being edited (empty for new tags)
 *
 * While typing, the server applies exactly the rules used when saving
 * (unique, letters/digits/_/. only, no spaces or symbols, not starting with a
 * digit) and the problems are shown under the field with instructions.  The
 * form cannot be submitted while the name is invalid (the server refuses it
 * anyway).
 */
(function () {
  "use strict";

  const script = document.currentScript;
  const input = document.querySelector(script.dataset.input);
  if (!input) return;
  const url = script.dataset.url;
  const tagId = script.dataset.tagId || "";
  const form = input.form;
  const box = document.createElement("div");
  box.className = "invalid-feedback hmi-name-feedback";
  box.setAttribute("aria-live", "polite");
  input.insertAdjacentElement("afterend", box);
  const original = input.value;
  let state = { name: null, ok: true, problems: [] };
  let timer = null;
  let seq = 0;

  /** Show the result under the field. */
  function show() {
    const bad = !state.ok;
    input.classList.toggle("is-invalid", bad);
    input.setAttribute("aria-invalid", bad ? "true" : "false");
    box.replaceChildren(...state.problems.map((p) => { const d = document.createElement("div"); d.textContent = p; return d; }));
    box.style.display = bad ? "block" : "";
  }

  /** Ask the server about the current name. */
  async function check() {
    const name = input.value.trim();
    if (name === state.name) return state;
    const mine = ++seq;
    try {
      const resp = await fetch(url + "?name=" + encodeURIComponent(name) + "&id=" + encodeURIComponent(tagId),
        { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!resp.ok) return state;              // the server checks again when saving
      const data = await resp.json();
      if (mine !== seq) return state;
      state = { name: name, ok: data.ok, problems: data.problems || [] };
      show();
    } catch (err) {
      /* offline: the save is checked by the server */
    }
    return state;
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(check, 250);
  });
  input.addEventListener("blur", check);
  if (form) {
    form.addEventListener("submit", async (ev) => {
      if (state.name === input.value.trim() && state.ok) return;
      if (input.value.trim() === original && tagId) return;   // unchanged name of an existing tag
      ev.preventDefault();
      ev.stopImmediatePropagation();
      const result = await check();
      if (result.ok) {
        if (form.requestSubmit) form.requestSubmit(ev.submitter || undefined); else form.submit();
      } else {
        input.focus();
        if (window.HMIDialog) window.HMIDialog.toast(result.problems.join("\n"), { icon: "warning" });
      }
    }, true);
  }
  if (input.value.trim() && input.value.trim() !== original) check();
})();
