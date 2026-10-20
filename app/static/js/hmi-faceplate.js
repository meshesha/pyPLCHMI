/**
 * Device windows (faceplates): a template screen shown for one device in a
 * pop-up window over the current page.
 *
 * ``HMIFaceplate.open(instanceId)`` loads ``/faceplates/api/instances/<id>``
 * (the template layout with the device's tags already substituted) and runs
 * it with the screen runtime (``HMIMimic.create``) – values, buttons, writes,
 * visibility rules and right-click *Show graph* / *Details* work as on a screen.
 *
 * Windows can be dragged by their title bar, resized at the lower corner,
 * several can be open at once (a click brings one to the front; opening an
 * open device again just brings it to the front), and Escape closes the
 * focused one.  In full-screen mode the windows move into the full-screen
 * element.  Each window polls its own values and stops when it is closed.
 *
 * Needs: hmi-symbols.js, hmi-local.js, hmi-render.js, hmi-mimic.js and the
 * ``#faceplate-config`` JSON (``faceplates/_runtime.html``).
 */
(function () {
  "use strict";

  const cfgEl = document.getElementById("faceplate-config");
  if (!cfgEl) return;
  const cfg = JSON.parse(cfgEl.textContent);
  const NS = "http://www.w3.org/2000/svg";
  const MAX_WINDOWS = 10;
  const open = new Map();          // instance id -> window record
  let zTop = 1050;
  let cascade = 0;

  const host = () => document.fullscreenElement || document.body;

  /**
   * Create an element.
   * @param {string} tag Tag name.
   * @param {string} [cls] Class names.
   * @param {string} [text] Text content.
   * @returns {HTMLElement}
   */
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function toast(msg, icon) {
    if (window.HMIDialog) window.HMIDialog.toast(msg, { icon: icon || "error", ms: 4000 });
  }

  /** Bring a window to the front and focus it. */
  function front(rec) {
    zTop += 1;
    rec.root.style.zIndex = zTop;
    rec.root.focus({ preventScroll: true });
  }

  /** Keep a window inside the visible area. */
  function clamp(rec) {
    const r = rec.root.getBoundingClientRect();
    const maxX = Math.max(0, window.innerWidth - Math.min(r.width, 120));
    const maxY = Math.max(0, window.innerHeight - 40);
    rec.root.style.left = Math.min(Math.max(0, r.left), maxX) + "px";
    rec.root.style.top = Math.min(Math.max(0, r.top), maxY) + "px";
  }

  /**
   * Make a window draggable by its title bar.
   * @param {Object} rec Window record.
   * @param {HTMLElement} handle Title bar.
   */
  function draggable(rec, handle) {
    let start = null;
    handle.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 || ev.target.closest("button")) return;
      const r = rec.root.getBoundingClientRect();
      start = { x: ev.clientX, y: ev.clientY, left: r.left, top: r.top };
      handle.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    handle.addEventListener("pointermove", (ev) => {
      if (!start) return;
      rec.root.style.left = start.left + ev.clientX - start.x + "px";
      rec.root.style.top = start.top + ev.clientY - start.y + "px";
    });
    const end = () => { if (start) { start = null; clamp(rec); } };
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  }

  /** Close a window. */
  function close(rec) {
    if (rec.runtime) rec.runtime.stop();
    rec.root.remove();
    open.delete(rec.id);
  }

  /**
   * Initial size: the template's proportions, at most 70 % of the viewport.
   * @param {Object} canvas Layout canvas.
   * @returns {{w:number, h:number}}
   */
  function initialSize(canvas) {
    const cw = canvas.width || 800, ch = canvas.height || 600;
    const maxW = Math.max(280, window.innerWidth * 0.7), maxH = Math.max(220, window.innerHeight * 0.7 - 40);
    const k = Math.min(1, maxW / cw, maxH / ch);
    return { w: Math.round(cw * k), h: Math.round(ch * k) };
  }

  /**
   * Open the window of a device (or bring it to the front when it is already open).
   * @param {number|string} instanceId Device id.
   */
  async function openWindow(instanceId) {
    const id = String(instanceId);
    if (open.has(id)) { front(open.get(id)); return; }
    if (open.size >= MAX_WINDOWS) { toast(T("At most {0} device windows can be open – close one first.", MAX_WINDOWS)); return; }
    let data;
    try {
      const resp = await fetch(cfg.instance.replace(/\/0$/, "/" + encodeURIComponent(id)),
        { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (resp.status === 403 || resp.status === 401) throw new Error(T("You may not open this device window."));
      if (resp.status === 404) throw new Error(T("This device no longer exists."));
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      data = await resp.json();
    } catch (err) {
      toast(T("Device window: {0}", err.message));
      return;
    }
    if (open.has(id)) { front(open.get(id)); return; }

    const rec = { id: id };
    const root = el("div", "hmi-fp-win");
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-label", data.title);
    root.tabIndex = -1;
    root.dir = document.documentElement.dir || "ltr";
    const head = el("div", "hmi-fp-head");
    const title = el("div", "hmi-fp-title text-truncate");
    title.append(el("span", "hmi-fp-type", data.faceplate), el("span", "hmi-fp-name", data.name));
    title.title = data.title + (data.description ? "\n" + data.description : "");
    const state = el("span", "hmi-fp-state");
    state.setAttribute("aria-live", "polite");
    const closeBtn = el("button", "btn-close btn-close-white hmi-fp-close");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", T("Close"));
    head.append(title, state, closeBtn);
    const body = el("div", "hmi-fp-body");
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "hmi-fp-svg");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("dir", "ltr");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", data.title);
    body.appendChild(svg);
    root.append(head, body);
    if (data.missing && data.missing.length) {
      const foot = el("div", "hmi-fp-foot", T("No tag for: {0}", data.missing.join(", ")));
      foot.title = T("This device has no tag for these parameters – check its tag prefix (Admin → Faceplates).");
      root.appendChild(foot);
    }
    rec.root = root;
    const size = initialSize(data.layout.canvas || {});
    root.style.width = size.w + "px";
    root.style.height = size.h + 40 + (data.missing && data.missing.length ? 26 : 0) + "px";
    const offset = (cascade++ % 8) * 28;
    root.style.left = Math.max(8, Math.min(window.innerWidth - size.w - 8, window.innerWidth / 2 - size.w / 2 + offset - 98)) + "px";
    root.style.top = Math.max(60, Math.min(window.innerHeight - size.h - 48, 80 + offset)) + "px";
    host().appendChild(root);
    open.set(id, rec);
    front(rec);
    clamp(rec);

    rec.runtime = window.HMIMimic.create(svg, data, {
      onValues: () => { state.textContent = ""; state.classList.remove("text-danger"); },
      onError: () => { state.textContent = T("no connection"); state.classList.add("text-danger"); },
    });
    // Right-click menu: tag ids on the elements, tag data for the menu.
    data.layout.elements.forEach((e) => {
      const node = rec.runtime.nodes[e.id];
      if (node && e.point) node.dataset.point = String(e.point);
    });
    if (window.HMITagMenu) window.HMITagMenu.addPoints(data.menu_points);

    draggable(rec, head);
    closeBtn.addEventListener("click", () => close(rec));
    root.addEventListener("pointerdown", () => front(rec), true);
    root.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !document.querySelector(".hmi-window-backdrop, .swal2-container")) { ev.stopPropagation(); close(rec); } });
  }

  // Full screen: move the windows into (and out of) the full-screen element.
  document.addEventListener("fullscreenchange", () => {
    open.forEach((rec) => { host().appendChild(rec.root); clamp(rec); });
  });
  window.addEventListener("resize", () => open.forEach(clamp));

  // Plain HTML buttons/links: <button data-faceplate="12">.
  document.addEventListener("click", (ev) => {
    const btn = ev.target instanceof Element && ev.target.closest("[data-faceplate]");
    if (!btn || btn.closest(".hmi-fp-win svg")) return;
    ev.preventDefault();
    openWindow(btn.dataset.faceplate);
  });

  window.HMIFaceplate = { open: openWindow, close: (id) => { const r = open.get(String(id)); if (r) close(r); } };
})();
