/**
 * Alarm client (loaded on every page).
 *
 * For logged-in users (when #hmi-alarms exists) it:
 *  - polls GET /api/alarms/active every few seconds;
 *  - updates the navbar "Events" button (count, colour of the highest severity);
 *  - pops up the alarm window for every new, unacknowledged event
 *    ("Open Screen" opens the alarm's screen with the alarm on top,
 *    "Ack" acknowledges the event and closes the window);
 *  - fills the Events window (active events; "Details" and, for
 *    administrators, "Remove");
 *  - keeps the alarm banner of a screen opened from an alarm up to date;
 *  - with a UI template using "alarm_ui": "banner" (<html data-ui-alarms="banner">)
 *    no window pops up: the alarm strip under the top bar shows the counts and
 *    the newest unacknowledged alarm, and the alarm drawer lists all of them
 *    without blocking the page.
 *
 * After every poll it fires the document event "hmi:alarms" with
 * {detail: {events}} (used by the sidebar and the high-performance tiles).
 *
 * On every page it also converts <time class="js-local-time"> elements
 * (rendered in UTC by the server) to the browser's local time.
 *
 * Exposes window.HMIAlarms.showEvent(id) for "Details" buttons; any element
 * with a data-alarm-event="<id>" attribute opens that event when clicked.
 */
(function () {
  "use strict";

  /**
   * Format an ISO 8601 timestamp in the browser's locale and time zone.
   * @param {string|null} iso - Timestamp or null.
   * @returns {string} Local date/time, or "—".
   */
  function fmt(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString();
  }

  /** Convert all server-rendered UTC times on the page to local time. */
  function localizeTimes(root) {
    (root || document).querySelectorAll("time.js-local-time").forEach((t) => {
      t.textContent = fmt(t.getAttribute("datetime"));
      t.title = t.getAttribute("datetime");
    });
  }
  localizeTimes();

  const cfg = document.getElementById("hmi-alarms");
  if (!cfg || !window.bootstrap) return;

  const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const POLL_MS = Math.max(1000, parseInt(cfg.dataset.pollMs, 10) || 3000);
  const RANK = { critical: 3, high: 2, low: 1 };
  const bannerEventId = parseInt(cfg.dataset.bannerEvent, 10) || null;

  const els = {
    btn: document.getElementById("events-btn"),
    count: document.getElementById("events-count"),
    eventsModal: document.getElementById("events-modal"),
    eventsBody: document.getElementById("events-body"),
    modal: document.getElementById("alarm-modal"),
    title: document.getElementById("alarm-modal-title"),
    severity: document.getElementById("alarm-modal-severity"),
    queue: document.getElementById("alarm-modal-queue"),
    desc: document.getElementById("alarm-modal-desc"),
    cond: document.getElementById("alarm-modal-cond"),
    value: document.getElementById("alarm-modal-value"),
    time: document.getElementById("alarm-modal-time"),
    ack: document.getElementById("alarm-modal-ack"),
    state: document.getElementById("alarm-modal-state"),
    open: document.getElementById("alarm-modal-open"),
    ackBtn: document.getElementById("alarm-modal-ack-btn"),
    closeBtn: document.getElementById("alarm-modal-close"),
    closeX: document.getElementById("alarm-modal-x"),
    banner: document.getElementById("alarm-banner"),
    strip: document.getElementById("alarm-strip"),
    stripLatest: document.getElementById("alarm-strip-latest"),
    stripAck: document.getElementById("alarm-strip-ack"),
    stripTotal: document.getElementById("alarm-strip-total"),
    drawerList: document.getElementById("alarm-drawer-list"),
    drawerFilter: document.getElementById("alarm-drawer-filter"),
  };
  /** Alarm strip mode: alarms are shown in the strip and drawer, never as a popup. */
  const STRIP = document.documentElement.dataset.uiAlarms === "banner";
  let drawerFilter = "all";

  let active = [];              // events from the last poll
  let canRemove = false;
  let popup = null;             // {id, mode: "alert" | "details"} while the alarm window is open
  let stopped = false;

  /**
   * Build an API URL from a template ending in /0 or containing /0/.
   * @param {string} template - URL built by url_for with id 0.
   * @param {number} id - Event id.
   * @returns {string} URL.
   */
  function url(template, id) {
    return template.replace(/\/0(\/|$)/, "/" + encodeURIComponent(id) + "$1");
  }

  /**
   * POST to an event endpoint.
   * @param {string} template - URL template.
   * @param {number} id - Event id.
   * @returns {Promise<object>} The updated event.
   */
  async function post(template, id) {
    const resp = await fetch(url(template, id), {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/json" }, body: "{}",
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
    return data;
  }

  // ------------------------------------------------------------------ alarm window
  /**
   * Fill the alarm window with an event.
   * @param {object} ev - Event JSON.
   * @param {string} mode - "alert" (auto popup) or "details".
   * @param {number} queued - Number of other unacknowledged events.
   */
  function fillWindow(ev, mode, queued) {
    els.modal.style.setProperty("--sev", ev.color);
    els.title.textContent = ev.name;
    els.severity.textContent = T(ev.severity_label);
    els.queue.textContent = queued > 0 ? T("+{0} more unacknowledged", queued) : "";
    els.desc.textContent = ev.description || "";
    els.desc.hidden = !ev.description;
    els.cond.textContent = ev.condition;
    els.value.textContent = ev.value || "—";
    els.time.textContent = fmt(ev.occurred_at);
    els.ack.textContent = ev.acked_at ? fmt(ev.acked_at) + (ev.acked_by ? " (" + ev.acked_by + ")" : "") : "—";
    els.state.textContent = ev.cleared_at ? T("Closed {0} – {1}", fmt(ev.cleared_at), ev.close_text) : T("Active");
    els.open.hidden = !ev.screen_url;
    if (ev.screen_url) {
      // inside the full-screen frame the screen opens in frame mode too (hmi-shell.js)
      els.open.href = window.HMIEmbed ? ev.screen_url + (ev.screen_url.includes("?") ? "&" : "?") + "embed=1" : ev.screen_url;
    }
    els.ackBtn.hidden = Boolean(ev.acked_at || ev.cleared_at);
    els.closeBtn.hidden = mode === "alert";
    els.closeX.hidden = mode === "alert";
  }

  /**
   * Open the alarm window.  Alert windows can only be closed with "Ack"
   * (or by following "Open Screen").
   * @param {object} ev - Event JSON.
   * @param {string} mode - "alert" or "details".
   * @param {number} [queued] - Other unacknowledged events.
   */
  function openWindow(ev, mode, queued) {
    if (els.eventsModal.classList.contains("show")) {
      // One modal at a time: close the Events window first, then open this one.
      els.eventsModal.addEventListener("hidden.bs.modal", () => openWindow(ev, mode, queued), { once: true });
      bootstrap.Modal.getOrCreateInstance(els.eventsModal).hide();
      return;
    }
    fillWindow(ev, mode, queued || 0);
    popup = { id: ev.id, mode: mode };
    if (els.modal.classList.contains("show")) return;   // already open: content replaced
    const existing = bootstrap.Modal.getInstance(els.modal);
    if (existing) existing.dispose();
    bootstrap.Modal.getOrCreateInstance(els.modal, mode === "alert"
      ? { backdrop: "static", keyboard: false } : { backdrop: true, keyboard: true }).show();
  }

  /** Close the alarm window. */
  function closeWindow() {
    const inst = bootstrap.Modal.getInstance(els.modal);
    if (inst) inst.hide();
  }

  els.modal.addEventListener("hidden.bs.modal", () => {
    popup = null;
    // Show the next unacknowledged alarm, if any.
    setTimeout(() => maybePopup(), 300);
  });

  /**
   * Open the alarm's screen.  In full-screen mode (hmi-shell.js frame) the
   * screen opens inside the frame, so full screen stays on – also when this
   * window belongs to the page underneath the frame.
   * @param {MouseEvent} ev Click on "Open Screen".
   */
  function openScreen(ev) {
    const href = (ev.currentTarget && ev.currentTarget.getAttribute("href")) || els.open.getAttribute("href");
    if (!href || href === "#") return;
    ev.preventDefault();
    const withEmbed = (url) => url + (url.includes("embed=1") ? "" : (url.includes("?") ? "&" : "?") + "embed=1");
    closeWindow();
    const frame = document.querySelector(".hmi-fs-frame");
    if (frame) {                       // page underneath a full-screen frame
      frame.src = withEmbed(href);
    } else if (window.HMIEmbed) {      // inside the frame
      window.location.assign(withEmbed(href));
    } else {
      window.location.assign(href);
    }
  }
  els.open.addEventListener("click", openScreen);

  els.ackBtn.addEventListener("click", async () => {
    if (!popup) return;
    const id = popup.id;
    els.ackBtn.disabled = true;
    try {
      const ev = await post(cfg.dataset.ackUrl, id);
      active = active.map((e) => (e.id === id ? ev : e));
      render();
      closeWindow();
    } catch (err) {
      els.queue.textContent = T("Ack failed: {0}", err.message);
    } finally {
      els.ackBtn.disabled = false;
    }
  });

  /**
   * Open the details window for any event (active or historical).
   * @param {number|string} id - Event id.
   */
  async function showEvent(id) {
    try {
      const resp = await fetch(url(cfg.dataset.eventUrl, id), { credentials: "same-origin" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      openWindow(await resp.json(), "details");
    } catch (err) {
      console.warn("alarm details:", err);
    }
  }

  /** Unacknowledged events, most severe and oldest first (banner event excluded). */
  function unacked() {
    return active
      .filter((e) => !e.acked_at && e.id !== bannerEventId)
      .sort((a, b) => (RANK[b.severity] || 0) - (RANK[a.severity] || 0) || a.occurred_at.localeCompare(b.occurred_at));
  }

  /** Pop up the alarm window for the next unacknowledged event, or close a stale one. */
  function maybePopup() {
    if (STRIP) return;                 // alarm strip mode: never pop up
    const pending = unacked();
    if (popup && document.querySelector(".hmi-fs-overlay")) {
      closeWindow();                   // the full-screen frame shows its own alarm window
      return;
    }
    if (popup && popup.mode === "alert") {
      const still = pending.find((e) => e.id === popup.id);
      if (!still) { closeWindow(); return; }          // acked elsewhere or cleared
      fillWindow(still, "alert", pending.length - 1);
      return;
    }
    if (popup || !pending.length) return;
    if (els.eventsModal.classList.contains("show")) return; // don't interrupt the Events window
    // A screen is in full screen (hmi-shell.js frame): the frame shows the alarm window there.
    if (document.querySelector(".hmi-fs-overlay")) return;
    openWindow(pending[0], "alert", pending.length - 1);
  }

  // ------------------------------------------------------------------ events window
  /**
   * Create an element with classes and text.
   * @param {string} tag - Tag name.
   * @param {string} [cls] - Class names.
   * @param {string} [text] - Text content.
   * @returns {HTMLElement}
   */
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  /** Render the Events window table. */
  function renderEvents() {
    els.eventsBody.replaceChildren();
    if (!active.length) {
      const tr = el("tr");
      const td = el("td", "text-secondary", T("No active alarms."));
      td.colSpan = 4;
      tr.append(td);
      els.eventsBody.append(tr);
      return;
    }
    active.forEach((ev) => {
      const tr = el("tr", "alarm-row" + (ev.acked_at ? "" : " unacked"));
      tr.style.setProperty("--sev", ev.color);
      tr.dataset.eventId = ev.id;
      const name = el("td");
      const badge = el("span", "badge severity-badge me-2", T(ev.severity_label));
      badge.style.background = ev.color;
      name.append(badge, el("strong", "", ev.name));
      if (ev.description) name.append(el("div", "small text-secondary", ev.description));
      const ack = el("td", "text-nowrap small", ev.acked_at ? fmt(ev.acked_at) : "—");
      if (ev.acked_by) ack.append(el("div", "text-secondary", ev.acked_by));
      const actions = el("td", "text-end text-nowrap");
      const details = el("button", "btn btn-sm btn-outline-info me-1", T("Details"));
      details.type = "button";
      details.addEventListener("click", () => showEvent(ev.id));
      actions.append(details);
      if (canRemove) {
        const rm = el("button", "btn btn-sm btn-outline-danger event-remove", T("Remove"));
        rm.type = "button";
        rm.title = T("Close this event manually");
        rm.addEventListener("click", () => removeEvent(ev.id, rm));
        actions.append(rm);
      }
      tr.append(name, el("td", "text-nowrap small", fmt(ev.occurred_at)), ack, actions);
      els.eventsBody.append(tr);
    });
  }

  /**
   * Remove (manually close) an event – two clicks: "Remove" then "Confirm".
   * @param {number} id - Event id.
   * @param {HTMLButtonElement} btn - The clicked button.
   */
  async function removeEvent(id, btn) {
    if (btn.dataset.armed !== "1") {
      btn.dataset.armed = "1";
      btn.textContent = T("Confirm");
      btn.classList.replace("btn-outline-danger", "btn-danger");
      setTimeout(() => {
        if (btn.isConnected) { btn.dataset.armed = ""; btn.textContent = T("Remove"); btn.classList.replace("btn-danger", "btn-outline-danger"); }
      }, 4000);
      return;
    }
    btn.disabled = true;
    try {
      await post(cfg.dataset.removeUrl, id);
      active = active.filter((e) => e.id !== id);
      render();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = T("Failed");
    }
  }

  // ------------------------------------------------------------------ banner
  /** Keep the "Open Screen" alarm banner in sync with the event state. */
  function renderBanner() {
    if (!els.banner) return;
    const id = parseInt(els.banner.dataset.eventId, 10);
    const ev = active.find((e) => e.id === id);
    const state = document.getElementById("alarm-banner-state");
    const ackBtn = document.getElementById("alarm-banner-ack");
    state.textContent = !ev ? T("Closed") : (ev.acked_at ? T("Acknowledged") : T("Active"));
    els.banner.classList.toggle("closed", !ev);
    ackBtn.hidden = !ev || Boolean(ev.acked_at);
  }

  if (els.banner) {
    const bannerAck = document.getElementById("alarm-banner-ack");
    bannerAck.addEventListener("click", async () => {
      const id = parseInt(els.banner.dataset.eventId, 10);
      bannerAck.disabled = true;
      try {
        const ev = await post(cfg.dataset.ackUrl, id);
        active = active.map((x) => (x.id === id ? ev : x));
        render();
      } catch (err) {
        console.warn("ack:", err);
      } finally {
        bannerAck.disabled = false;
      }
    });
    document.getElementById("alarm-banner-close").addEventListener("click", () => { els.banner.hidden = true; });
  }

  // ------------------------------------------------------------------ alarm strip + drawer
  /**
   * Acknowledge an event and update the views.
   * @param {number} id Event id.
   * @param {HTMLButtonElement} [btn] Button to disable meanwhile.
   */
  async function ackEvent(id, btn) {
    if (btn) btn.disabled = true;
    try {
      const ev = await post(cfg.dataset.ackUrl, id);
      active = active.map((x) => (x.id === id ? ev : x));
      render();
    } catch (err) {
      if (window.HMIDialog) window.HMIDialog.toast(T("Ack failed: {0}", err.message), { icon: "error" });
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /** Newest unacknowledged event (most severe first), or null. */
  function newestUnacked() {
    const list = active.filter((e) => !e.acked_at)
      .sort((a, b) => (RANK[b.severity] || 0) - (RANK[a.severity] || 0) || b.occurred_at.localeCompare(a.occurred_at));
    return list[0] || null;
  }

  /** Update the alarm strip: counts per priority and the newest unacknowledged alarm. */
  function renderStrip() {
    if (!els.strip) return;
    const counts = { critical: 0, high: 0, low: 0 };
    active.forEach((e) => { if (e.severity in counts) counts[e.severity] += 1; });
    els.strip.querySelectorAll("[data-sev]").forEach((n) => {
      const c = counts[n.dataset.sev] || 0;
      n.querySelector("b").textContent = c;
      n.classList.toggle("zero", c === 0);
    });
    const top = newestUnacked();
    els.strip.dataset.state = top ? top.severity : (active.length ? "acked" : "none");
    els.stripTotal.textContent = active.length;
    els.stripLatest.replaceChildren();
    if (top) {
      const link = el("button", "hmi-strip-event", "");
      link.type = "button";
      link.append(el("strong", "", new Date(top.occurred_at).toLocaleTimeString() + " · " + top.name));
      const detail = [top.description, top.value ? T("value {0}", top.value) : ""].filter(Boolean).join(" · ");
      if (detail) link.append(el("span", "hmi-strip-detail", " " + detail));
      link.title = T("Details");
      link.addEventListener("click", () => showEvent(top.id));
      els.stripLatest.append(link);
      els.stripAck.hidden = false;
      els.stripAck.dataset.eventId = top.id;
    } else {
      els.stripLatest.textContent = active.length ? T("All alarms acknowledged · {0} still active", active.length) : T("No active alarms.");
      els.stripAck.hidden = true;
    }
  }

  /** Fill the alarm drawer with one card per active event. */
  function renderDrawer() {
    if (!els.drawerList) return;
    const unackedCount = active.filter((e) => !e.acked_at).length;
    els.drawerFilter.querySelector('[data-n="all"]').textContent = active.length;
    els.drawerFilter.querySelector('[data-n="unacked"]').textContent = unackedCount;
    const list = active.filter((e) => drawerFilter === "all" || !e.acked_at)
      .sort((a, b) => (RANK[b.severity] || 0) - (RANK[a.severity] || 0) || b.occurred_at.localeCompare(a.occurred_at));
    els.drawerList.replaceChildren();
    if (!list.length) {
      els.drawerList.append(el("p", "text-secondary small", T("No active alarms.")));
      return;
    }
    list.forEach((ev) => {
      const card = el("article", "hmi-drawer-card" + (ev.acked_at ? " acked" : ""));
      card.style.setProperty("--sev", ev.color);
      card.dataset.eventId = ev.id;
      const head = el("div", "hmi-drawer-head");
      const badge = el("span", "badge severity-badge", T(ev.severity_label));
      badge.style.background = ev.color;
      head.append(badge, el("strong", "flex-grow-1", ev.name), el("span", "small font-monospace", new Date(ev.occurred_at).toLocaleTimeString()));
      card.append(head);
      const info = [ev.description, ev.condition, ev.value ? T("value {0}", ev.value) : ""].filter(Boolean).join(" · ");
      if (info) card.append(el("div", "small hmi-drawer-info", info));
      const actions = el("div", "d-flex flex-wrap gap-2");
      if (!ev.acked_at) {
        const ack = el("button", "btn btn-sm btn-warning", T("Ack"));
        ack.type = "button";
        ack.addEventListener("click", () => ackEvent(ev.id, ack));
        actions.append(ack);
      } else {
        actions.append(el("span", "small text-secondary align-self-center",
          T("Acknowledged") + (ev.acked_by ? " · " + ev.acked_by : "")));
      }
      if (ev.screen_url) {
        const open = el("a", "btn btn-sm btn-outline-secondary", T("Open Screen"));
        open.href = ev.screen_url;
        open.addEventListener("click", openScreen);
        actions.append(open);
      }
      const details = el("button", "btn btn-sm btn-outline-info", T("Details"));
      details.type = "button";
      details.addEventListener("click", () => showEvent(ev.id));
      actions.append(details);
      card.append(actions);
      els.drawerList.append(card);
    });
  }

  if (els.stripAck) {
    els.stripAck.addEventListener("click", () => ackEvent(parseInt(els.stripAck.dataset.eventId, 10), els.stripAck));
  }
  if (els.drawerFilter) {
    els.drawerFilter.addEventListener("click", (e) => {
      const b = e.target.closest("[data-filter]");
      if (!b) return;
      drawerFilter = b.dataset.filter;
      els.drawerFilter.querySelectorAll("[data-filter]").forEach((x) => {
        x.classList.toggle("active", x === b);
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      renderDrawer();
    });
  }

  // ------------------------------------------------------------------ polling
  /**
   * Update the navbar button, the Events window and the banner.
   * @param {object} [data] - Poll response (for the badge colour).
   */
  function render(data) {
    const pending = active.filter((e) => !e.acked_at).length;
    renderStrip();
    renderDrawer();
    document.dispatchEvent(new CustomEvent("hmi:alarms", { detail: { events: active } }));
    if (!els.count || !els.btn) {  // screen in the full-screen frame: no top bar (no Events button)
      renderBanner();
      return;
    }
    els.count.hidden = !active.length;
    els.count.textContent = active.length;
    let highest = null;
    active.forEach((e) => { if (!highest || (RANK[e.severity] || 0) > (RANK[highest.severity] || 0)) highest = e; });
    els.count.style.background = highest ? highest.color : "";
    els.btn.classList.toggle("has-unacked", pending > 0);
    els.btn.title = active.length ? T("{0} active alarm(s), {1} unacknowledged", active.length, pending) : T("No active alarms");
    renderEvents();
    renderBanner();
  }

  /** Poll the active events once. */
  async function poll() {
    if (stopped) return;
    try {
      const resp = await fetch(cfg.dataset.activeUrl, { credentials: "same-origin" });
      if (resp.status === 401) { stopped = true; return; }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      active = data.events;
      canRemove = Boolean(data.can_remove);
      render(data);
      maybePopup();
    } catch (err) {
      console.warn("alarm poll:", err);
    }
  }

  document.addEventListener("click", (e) => {
    const trigger = e.target.closest("[data-alarm-event]");
    if (trigger) {
      e.preventDefault();
      showEvent(trigger.dataset.alarmEvent);
    }
  });
  els.eventsModal.addEventListener("show.bs.modal", renderEvents);

  window.HMIAlarms = { showEvent: showEvent, poll: poll, localizeTimes: localizeTimes };
  poll();
  setInterval(poll, POLL_MS);
})();
