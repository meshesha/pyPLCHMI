/**
 * pyPLCHMI dialogs and translations for the browser.
 *
 * - ``T(text, ...values)`` translates an English UI text with the language
 *   catalog loaded as ``window.I18N`` (see /i18n/<code>.js); ``{0}`` / ``{name}``
 *   placeholders are filled from the values.  Missing texts stay English.
 * - ``HMIDialog`` wraps SweetAlert2: ``confirm``, ``alert``, ``prompt`` and
 *   ``toast`` with the HMI's dark look, translated buttons and RTL support.
 * - Forms (or submit buttons) with a ``data-confirm="question"`` attribute ask
 *   with a SweetAlert2 dialog before they are submitted.
 * - Flash messages rendered by the server in ``#hmi-flashes`` are shown as one
 *   toast (errors stay until closed, the others close by themselves).
 * - Colour theme "auto" follows the operating system (``data-hmi-theme``).
 * - ``data-confirm-button`` / ``data-confirm-icon`` on a ``data-confirm`` form set
 *   the confirm button text and the icon of the question.
 * - The browser's time zone is stored in the ``hmi_tz`` cookie (server-side
 *   dates of the tag history, see app/utils/timezones.py).
 */
(function () {
  "use strict";

  // Colour theme "auto": follow the operating system (light / dark), also when it changes.
  const root = document.documentElement;
  const lightQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  /** Apply the operating system's colour scheme for users who chose "auto". */
  function applyAutoTheme() {
    if (root.dataset.hmiTheme !== "auto" || !lightQuery) return;
    const next = lightQuery.matches ? "light" : "dark";
    if (root.getAttribute("data-bs-theme") !== next) {
      root.setAttribute("data-bs-theme", next);
      document.dispatchEvent(new CustomEvent("hmi:theme", { detail: next }));
    }
  }
  applyAutoTheme();
  if (lightQuery && lightQuery.addEventListener) lightQuery.addEventListener("change", applyAutoTheme);

  // Tell the server the browser's time zone (dates of the tag history and its Excel export).
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (tz) document.cookie = "hmi_tz=" + encodeURIComponent(tz) + "; path=/; max-age=31536000; SameSite=Lax";
  } catch (e) { /* very old browser: the server's zone is used */ }

  const catalog = (window.I18N && window.I18N.messages) || {};
  const rtl = Boolean(window.I18N && window.I18N.dir === "rtl");

  /**
   * Translate and format a UI text.
   * @param {string} text English text (message key).
   * @param {...*} values Values for {0}, {1}…; a single object fills {name} placeholders.
   * @returns {string} The translated text.
   */
  function T(text, ...values) {
    if (typeof text !== "string") return text;
    const translated = Object.prototype.hasOwnProperty.call(catalog, text) ? catalog[text] : text;
    if (!values.length) return translated;
    const named = values.length === 1 && values[0] && typeof values[0] === "object" ? values[0] : null;
    return translated.replace(/\{([A-Za-z0-9_]+)\}/g, (m, key) => {
      if (named && Object.prototype.hasOwnProperty.call(named, key)) return String(named[key]);
      const i = Number(key);
      return Number.isInteger(i) && i < values.length ? String(values[i]) : m;
    });
  }
  window.T = T;

  const Swal = window.Swal;

  /**
   * Escape text for SweetAlert2 parameters that are parsed as HTML (select option labels).
   * @param {*} text Text.
   * @returns {string} HTML-escaped text.
   */
  function esc(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /** Common SweetAlert2 options (dark theme, Bootstrap-like buttons, RTL). */
  function base(extra) {
    return Object.assign({
      theme: root.getAttribute("data-bs-theme") === "light" ? "light" : "dark",
      buttonsStyling: false,
      reverseButtons: !rtl,
      customClass: {
        popup: "hmi-swal" + (rtl ? " hmi-swal-rtl" : ""),
        confirmButton: "btn btn-primary mx-1",
        cancelButton: "btn btn-outline-secondary mx-1",
        denyButton: "btn btn-outline-danger mx-1",
        input: "form-control hmi-swal-input",
      },
      confirmButtonText: T("OK"),
      cancelButtonText: T("Cancel"),
    }, extra || {});
  }

  /**
   * Ask a yes/no question.
   * @param {string} text Question (already translated).
   * @param {{title?:string, danger?:boolean, confirmText?:string, icon?:string}} [opts]
   * @returns {Promise<boolean>} true when confirmed.
   */
  async function confirmDialog(text, opts) {
    const o = opts || {};
    if (!Swal) return window.confirm(text);
    const res = await Swal.fire(base({
      titleText: o.title || T("Please confirm"),
      text: text,
      icon: o.icon || (o.danger ? "warning" : "question"),
      showCancelButton: true,
      focusCancel: Boolean(o.danger),
      confirmButtonText: o.confirmText || (o.danger ? T("Delete") : T("Yes")),
      customClass: Object.assign(base().customClass, { confirmButton: "btn mx-1 " + (o.danger ? "btn-danger" : "btn-primary") }),
    }));
    return Boolean(res.isConfirmed);
  }

  /**
   * Show a message that must be acknowledged.
   * @param {string} text Message (already translated).
   * @param {{title?:string, icon?:string}} [opts] icon: success | error | warning | info.
   * @returns {Promise<void>}
   */
  async function alertDialog(text, opts) {
    const o = opts || {};
    if (!Swal) { window.alert(text); return; }
    await Swal.fire(base({ titleText: o.title || "", text: text, icon: o.icon || "info" }));
  }

  /**
   * Ask for a value.
   * @param {{title?:string, text?:string, input?:string, value?:*, options?:Object<string,string>,
   *          placeholder?:string, validate?:function(*):?string}} opts
   *   input: "text" | "number" | "select" (options: value → label).
   * @returns {Promise<*>} The entered value (string), or undefined when cancelled.
   */
  async function promptDialog(opts) {
    const o = opts || {};
    if (!Swal) {
      const v = window.prompt(o.title || o.text || "", o.value === undefined ? "" : o.value);
      return v === null ? undefined : v;
    }
    const res = await Swal.fire(base({
      titleText: o.title || "",
      text: o.text || "",
      input: o.input || "text",
      inputValue: o.value === undefined || o.value === null ? "" : String(o.value),
      inputOptions: o.options ? Object.fromEntries(Object.entries(o.options).map(([k, v]) => [k, esc(v)])) : undefined,
      inputPlaceholder: o.placeholder || "",
      inputAttributes: o.input === "number" ? { step: "any" } : {},
      showCancelButton: true,
      confirmButtonText: o.confirmText || T("Set"),
      inputValidator: (value) => {
        if (o.input === "number" && (value === "" || Number.isNaN(Number(value)))) return T("Enter a number.");
        return o.validate ? o.validate(value) : undefined;
      },
    }));
    return res.isConfirmed ? res.value : undefined;
  }

  /**
   * Show a small notification in the corner.
   * @param {string|Node} message Text or DOM node.
   * @param {{icon?:string, sticky?:boolean, ms?:number}} [opts]
   */
  function toast(message, opts) {
    const o = opts || {};
    if (!Swal) return;
    let content = message;
    if (typeof message === "string") {  // SweetAlert2 accepts elements, not text nodes
      content = document.createElement("span");
      content.textContent = message;
    }
    Swal.fire(base({
      toast: true,
      position: rtl ? "top-start" : "top-end",
      icon: o.icon || "success",
      html: content,
      showConfirmButton: false,
      showCloseButton: true,
      timer: o.sticky ? undefined : (o.ms || 4500),
      timerProgressBar: !o.sticky,
      customClass: Object.assign(base().customClass, { popup: "hmi-swal hmi-swal-toast" + (rtl ? " hmi-swal-rtl" : "") }),
      didOpen: (el) => {
        el.addEventListener("mouseenter", Swal.stopTimer);
        el.addEventListener("mouseleave", Swal.resumeTimer);
      },
    }));
  }

  window.HMIDialog = { confirm: confirmDialog, alert: alertDialog, prompt: promptDialog, toast: toast, T: T };

  // ---------------------------------------------------------------------
  // data-confirm on forms and submit buttons
  // ---------------------------------------------------------------------
  document.addEventListener("submit", async (ev) => {
    const form = ev.target;
    if (!(form instanceof HTMLFormElement)) return;
    const submitter = ev.submitter || null;
    const question = (submitter && submitter.dataset.confirm) || form.dataset.confirm;
    if (!question || form.dataset.confirmed === "1") return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const btn = submitter || form.querySelector("[type=submit]");
    const danger = Boolean(btn && /\bbtn-(outline-)?danger\b/.test(btn.className)) || form.dataset.confirmDanger === "1";
    const confirmText = (submitter && submitter.dataset.confirmButton) || form.dataset.confirmButton;
    if (await confirmDialog(question, { danger: danger, confirmText: confirmText, icon: form.dataset.confirmIcon })) {
      form.dataset.confirmed = "1";
      if (form.requestSubmit) form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
      else form.submit();
      setTimeout(() => { delete form.dataset.confirmed; }, 0);
    }
  }, true);

  // ---------------------------------------------------------------------
  // Flash messages → one toast
  // ---------------------------------------------------------------------
  const ICONS = { danger: "error", warning: "warning", success: "success", info: "info", message: "info" };
  const RANK = { error: 3, warning: 2, info: 1, success: 0 };

  /** Convert the server's flash alerts into a toast. */
  function showFlashes() {
    const box = document.getElementById("hmi-flashes");
    if (!box || !Swal) return;
    const items = Array.from(box.querySelectorAll("[data-flash]"));
    if (!items.length) { box.remove(); return; }
    let icon = "success";
    const list = document.createElement("div");
    list.className = "hmi-swal-flashes";
    items.forEach((el) => {
      const kind = ICONS[el.dataset.flash] || "info";
      if (RANK[kind] > RANK[icon]) icon = kind;
      const p = document.createElement("div");
      p.className = "hmi-swal-flash hmi-swal-flash-" + kind;
      p.textContent = el.dataset.text || el.textContent.trim();
      list.appendChild(p);
    });
    box.remove();
    toast(list, { icon: icon, sticky: icon === "error" || icon === "warning", ms: 5000 });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", showFlashes);
  else showFlashes();
})();
