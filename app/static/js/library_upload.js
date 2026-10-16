/**
 * Upload to the information library with a SHA-1 duplicate check.
 *
 * ``HMILibrary.upload(file, opts)``:
 *
 * 1. The SHA-1 of the file is computed in the browser (Web Crypto; only
 *    available on https:// or localhost) and ``/library/api/check`` is asked
 *    whether the library already has it.  Without Web Crypto the file is sent
 *    and the server performs the same check.
 * 2. If the file already exists, nothing is uploaded or stored: a dialog
 *    names the existing file and offers to use it (``opts.useText``) or to
 *    cancel.  The promise resolves to ``{file, reused: true}`` or
 *    ``{cancelled: true}``.
 * 3. Otherwise the file is uploaded and the promise resolves to
 *    ``{file, reused: false}``.  Errors resolve to ``{error}``.
 *
 * Also ``HMILibrary.formatSize(bytes)`` and ``HMILibrary.urls`` (from
 * ``data-*`` attributes of ``#library-urls``).
 */
(function () {
  "use strict";

  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const urlsEl = document.getElementById("library-urls");
  const urls = urlsEl ? { check: urlsEl.dataset.check, upload: urlsEl.dataset.upload, files: urlsEl.dataset.files } : {};

  /**
   * SHA-1 of a file as hex, or null when Web Crypto is not available.
   * @param {File} file The file.
   * @returns {Promise<?string>}
   */
  async function sha1(file) {
    if (!(window.crypto && window.crypto.subtle && file.arrayBuffer)) return null;
    try {
      const digest = await window.crypto.subtle.digest("SHA-1", await file.arrayBuffer());
      return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
    } catch (e) {
      return null;
    }
  }

  /**
   * Human-readable size.
   * @param {number} bytes Size.
   * @returns {string}
   */
  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  /**
   * Ask what to do with a file that is already in the library.
   * @param {File} file The chosen file.
   * @param {Object} existing The existing library file (JSON).
   * @param {{useText?:string}} opts Options.
   * @returns {Promise<boolean>} true = use the existing file.
   */
  function askDuplicate(file, existing, opts) {
    const where = existing.plcs && existing.plcs.length ? " " + T("Attached to: {0}.", existing.plcs.join(", ")) : "";
    const text = T("“{0}” is already in the information library as “{1}” ({2}, uploaded {3}).",
      file.name, existing.name, existing.size_text, existing.created_at ? new Date(existing.created_at).toLocaleString() : "?") +
      where + " " + T("The file will not be uploaded again.");
    return window.HMIDialog.confirm(text, {
      title: T("File already in the library"),
      icon: "info",
      confirmText: (opts && opts.useText) || T("Use the existing file"),
    });
  }

  /**
   * Upload one file (see the module description).
   * @param {File} file The file.
   * @param {{useText?:string}} [opts] Options.
   * @returns {Promise<{file?:Object, reused?:boolean, cancelled?:boolean, error?:string}>}
   */
  async function upload(file, opts) {
    const o = opts || {};
    try {
      const hash = await sha1(file);
      if (hash) {
        const resp = await fetch(urls.check + "?sha1=" + hash, { credentials: "same-origin", headers: { Accept: "application/json" } });
        const data = await resp.json();
        if (resp.ok && data.exists) {
          return (await askDuplicate(file, data.file, o)) ? { file: data.file, reused: true } : { cancelled: true };
        }
      }
      const form = new FormData();
      form.append("file", file);
      if (hash) form.append("sha1", hash);
      const resp = await fetch(urls.upload, { method: "POST", body: form, credentials: "same-origin",
        headers: { "X-CSRFToken": csrf(), Accept: "application/json" } });
      let data = {};
      try { data = await resp.json(); } catch (e) { data = { error: resp.status === 413 ? T("The file is too large.") : resp.statusText }; }
      if (resp.status === 409 && data.duplicate) {
        return (await askDuplicate(file, data.duplicate, o)) ? { file: data.duplicate, reused: true } : { cancelled: true };
      }
      if (!resp.ok) return { error: data.error || resp.statusText };
      return { file: data.file, reused: false };
    } catch (err) {
      return { error: err.message || String(err) };
    }
  }

  window.HMILibrary = { upload: upload, sha1: sha1, formatSize: formatSize, urls: urls, csrf: csrf };
})();
