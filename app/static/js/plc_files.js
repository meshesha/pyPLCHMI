/**
 * Files of a PLC / device (PLC edit page).
 *
 * - Lists the attached files (open, download, remove from this PLC).
 * - "Add from library": a searchable list of the information library;
 *   clicking a file attaches it.
 * - "Upload new": uploads through HMILibrary.upload (SHA-1 duplicate check).
 *   A new file is added to the library and attached; when the file already
 *   exists the user can attach the existing file instead or cancel – the
 *   duplicate is never uploaded.
 */
(function () {
  "use strict";

  const list = document.getElementById("plc-files");
  if (!list) return;
  const listUrl = list.dataset.url;
  const attachUrl = document.getElementById("plc-files-urls").dataset.attach;
  const upload = document.getElementById("plc-files-upload");
  const pickerList = document.getElementById("file-picker-list");
  const pickerSearch = document.getElementById("file-picker-search");
  let attached = [];

  /**
   * Create an element.
   * @param {string} tag Tag.
   * @param {Object} [attrs] Attributes (``class``, ``text``…).
   * @param {Array<Node|string>} [children] Children.
   * @returns {HTMLElement}
   */
  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v);
    });
    (children || []).forEach((c) => el.append(c));
    return el;
  }

  /**
   * Send a JSON request.
   * @param {string} url URL.
   * @param {string} method HTTP method.
   * @param {Object} [body] JSON body.
   * @returns {Promise<Object>}
   */
  async function api(url, method, body) {
    const resp = await fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": window.HMILibrary.csrf() },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    return data;
  }

  /**
   * Icon of a file kind.
   * @param {Object} f File JSON.
   * @returns {HTMLElement}
   */
  function icon(f) {
    return f.kind === "image"
      ? h("img", { src: f.url, alt: "", class: "plc-file-thumb", loading: "lazy" })
      : h("i", { class: "bi bi-file-earmark-pdf plc-file-icon" });
  }

  /** Show the attached files. */
  function render() {
    list.replaceChildren();
    if (!attached.length) {
      list.append(h("li", { class: "small text-secondary", text: T("No files attached yet.") }));
      return;
    }
    attached.forEach((f) => {
      const remove = h("button", { type: "button", class: "btn btn-sm btn-outline-danger", title: T("Remove from this PLC"),
        "aria-label": T("Remove from this PLC") }, [h("i", { class: "bi bi-x-lg" })]);
      remove.addEventListener("click", async () => {
        if (!(await window.HMIDialog.confirm(T("Remove {0} from this PLC? The file stays in the library.", f.name)))) return;
        try {
          attached = (await api(attachUrl + "/" + f.id, "DELETE")).files;
          render();
        } catch (err) { window.HMIDialog.toast(err.message, { icon: "error" }); }
      });
      list.append(h("li", { class: "plc-file d-flex align-items-center gap-2 mb-2", "data-file": String(f.id) }, [
        h("a", { href: f.url, target: "_blank", rel: "noopener", class: "plc-file-preview" }, [icon(f)]),
        h("div", { class: "flex-grow-1 min-w-0" }, [
          h("a", { href: f.url, target: "_blank", rel: "noopener", class: "d-block text-truncate", text: f.name }),
          h("div", { class: "small text-secondary text-truncate", text: f.original_name + " · " + f.size_text }),
        ]),
        h("a", { href: f.download, class: "btn btn-sm btn-outline-secondary", title: T("Download"), "aria-label": T("Download") },
          [h("i", { class: "bi bi-download" })]),
        remove,
      ]));
    });
  }

  /**
   * Attach a library file.
   * @param {Object} f File JSON.
   */
  async function attach(f) {
    try {
      attached = (await api(attachUrl, "POST", { file_id: f.id })).files;
      render();
      window.HMIDialog.toast(T("{0} attached.", f.name));
    } catch (err) { window.HMIDialog.toast(err.message, { icon: "error" }); }
  }

  /** Load the attached files. */
  async function load() {
    try {
      attached = (await api(listUrl, "GET")).files;
    } catch (err) { attached = []; }
    render();
  }

  // ---- picker
  let timer = null;
  /** Fill the picker with library files matching the search. */
  async function fillPicker() {
    const data = await api(window.HMILibrary.urls.files + "?q=" + encodeURIComponent(pickerSearch.value.trim()), "GET");
    pickerList.replaceChildren();
    if (!data.files.length) {
      pickerList.append(h("div", { class: "text-secondary small", text: T("No files found. Upload new files with “Upload new”.") }));
      return;
    }
    const ids = new Set(attached.map((f) => f.id));
    data.files.forEach((f) => {
      const done = ids.has(f.id);
      const item = h("button", { type: "button", class: "list-group-item list-group-item-action d-flex align-items-center gap-2" +
        (done ? " disabled" : ""), "data-file": String(f.id) }, [
        icon(f),
        h("div", { class: "flex-grow-1 text-start min-w-0" }, [
          h("div", { class: "text-truncate", text: f.name }),
          h("div", { class: "small text-secondary text-truncate",
            text: [f.original_name, f.size_text, f.plcs.length ? T("Attached to: {0}.", f.plcs.join(", ")) : ""].filter(Boolean).join(" · ") }),
        ]),
        h("span", { class: "small " + (done ? "text-success" : "text-primary"), text: done ? T("attached") : T("Add") }),
      ]);
      if (!done) {
        item.addEventListener("click", async () => {
          await attach(f);
          item.classList.add("disabled");
          item.lastChild.textContent = T("attached");
          item.lastChild.className = "small text-success";
        });
      }
      pickerList.append(item);
    });
  }
  document.getElementById("file-picker").addEventListener("show.bs.modal", () => { pickerSearch.value = ""; fillPicker(); });
  pickerSearch.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(fillPicker, 250); });

  // ---- upload
  upload.addEventListener("change", async () => {
    const files = Array.from(upload.files || []);
    upload.value = "";
    for (const file of files) {
      const res = await window.HMILibrary.upload(file, { useText: T("Attach the existing file") });
      if (res.error) {
        await window.HMIDialog.alert(file.name + ": " + res.error, { icon: "error", title: T("Upload failed") });
      } else if (res.file) {
        await attach(res.file);
      }
    }
  });

  load();
})();
