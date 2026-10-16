/**
 * Server connection monitor: a red banner when the HMI server cannot be reached.
 *
 * The checks run in a Web Worker (``hmi-heartbeat.js``) so they keep their
 * pace in background tabs and on wall displays.  This script shows the
 * result:
 *
 * - **lost** – a red banner at the top ("Connection to the server lost"),
 *   with the time it was lost, the number of attempts and the reason; the
 *   page title starts with ⚠ and ``<body>`` gets ``hmi-server-offline``
 *   (values on screen are greyed out – they are not current any more);
 * - **back** – the banner turns green for a few seconds ("Connection to the
 *   server restored", or "The server was restarted" when its process
 *   changed).
 *
 * The banner is placed inside the full-screen element when full screen is
 * on, so it is visible there too.  Screens inside the full-screen frame
 * (``?embed=1``) do not run their own monitor – the page around the frame
 * does.  Other scripts can listen to the ``hmi:server`` window event
 * (``detail`` = the worker's message) or call ``HMIConnection.check()``.
 */
(function () {
  "use strict";

  const script = document.getElementById("hmi-connection-js");
  if (!script) return;
  if (document.body.classList.contains("hmi-embed") && window.parent !== window) return;

  const healthUrl = new URL(script.dataset.healthUrl, window.location.href).href;
  const interval = Math.max(1, parseFloat(script.dataset.interval) || 5) * 1000;
  const p2 = (n) => String(n).padStart(2, "0");
  const baseTitle = document.title;
  let banner = null;
  let hideTimer = null;
  let state = "unknown";

  /**
   * Format a time of day.
   * @param {number} ms Epoch milliseconds.
   * @returns {string} ``HH:MM:SS``.
   */
  function clock(ms) {
    const d = new Date(ms);
    return p2(d.getHours()) + ":" + p2(d.getMinutes()) + ":" + p2(d.getSeconds());
  }

  /**
   * Human-readable length of an outage.
   * @param {number} ms Duration in milliseconds.
   * @returns {string} e.g. ``2 min 5 s``.
   */
  function duration(ms) {
    const s = Math.round(ms / 1000);
    if (s < 60) return T("{0} s", s);
    const m = Math.floor(s / 60);
    if (m < 60) return T("{0} min {1} s", m, s % 60);
    return T("{0} h {1} min", Math.floor(m / 60), m % 60);
  }

  /**
   * Text for the reason of a failed check.
   * @param {string} reason Reason from the worker.
   * @returns {string}
   */
  function reasonText(reason) {
    if (reason === "timeout") return T("no answer");
    if (reason === "network") return navigator.onLine === false ? T("this computer is offline") : T("server not reachable");
    if (reason === "database") return T("database not available");
    return T("server error ({0})", String(reason || "").replace("http ", ""));
  }

  /**
   * Where the banner must live: the full-screen element (when it can hold
   * a child element) or the page body.
   * @returns {HTMLElement}
   */
  function host() {
    const fs = document.fullscreenElement;
    if (fs instanceof HTMLElement && !/^(IFRAME|IMG|VIDEO|CANVAS)$/.test(fs.tagName)) return fs;
    return document.body;
  }

  /**
   * Create the banner (once) and put it in the right place.
   * @returns {HTMLElement}
   */
  function ensureBanner() {
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "hmi-server-banner";
      banner.className = "hmi-server-banner";
      banner.setAttribute("role", "alert");
      banner.setAttribute("aria-live", "assertive");
      banner.innerHTML = '<i class="bi" aria-hidden="true"></i><div><strong class="hmi-server-title"></strong>'
        + '<div class="hmi-server-detail small"></div></div>';
    }
    const target = host();
    if (banner.parentNode !== target) target.appendChild(banner);
    return banner;
  }

  /**
   * Show the banner.
   * @param {string} kind ``offline`` (red) or ``online`` (green).
   * @param {string} title Main line.
   * @param {string} detail Second line.
   */
  function show(kind, title, detail) {
    const b = ensureBanner();
    clearTimeout(hideTimer);
    b.dataset.state = kind;
    b.querySelector("i").className = "bi " + (kind === "offline" ? "bi-wifi-off" : "bi-check-circle-fill");
    b.querySelector(".hmi-server-title").textContent = title;
    b.querySelector(".hmi-server-detail").textContent = detail;
    b.hidden = false;
  }

  /** Hide the banner. */
  function hide() {
    if (banner) banner.hidden = true;
  }

  /**
   * Mark the page (and a screen in the full-screen frame) as offline or not.
   * @param {boolean} offline ``true`` while the server is lost.
   */
  function markOffline(offline) {
    document.body.classList.toggle("hmi-server-offline", offline);
    document.title = (offline ? "⚠ " : "") + baseTitle;
    document.querySelectorAll("iframe.hmi-fs-frame").forEach((frame) => {
      try { frame.contentDocument.body.classList.toggle("hmi-server-offline", offline); } catch (e) { /* not loaded */ }
    });
  }

  /**
   * React to a message of the heartbeat.
   * @param {{type:string, since?:number, reason?:string, attempts?:number,
   *          wasOffline?:boolean, downMs?:number, restarted?:boolean}} msg
   */
  function onMessage(msg) {
    if (!msg || !msg.type) return;
    if (msg.type === "offline" || msg.type === "retry") {
      state = "offline";
      markOffline(true);
      show("offline", T("Connection to the server lost"),
           T("Values are not updated. Since {0} · {1} · retrying (attempt {2})…",
             clock(msg.since), reasonText(msg.reason), msg.attempts));
    } else if (msg.type === "online") {
      const was = state;
      state = "online";
      markOffline(false);
      if (msg.wasOffline || was === "offline") {
        show("online", msg.restarted ? T("The server was restarted – connection restored")
                                     : T("Connection to the server restored"),
             T("Interrupted for {0}.", duration(msg.downMs || 0)));
        hideTimer = setTimeout(hide, 6000);
      } else if (msg.restarted) {
        show("online", T("The server was restarted – connection restored"), "");
        hideTimer = setTimeout(hide, 6000);
      }
    }
    window.dispatchEvent(new CustomEvent("hmi:server", { detail: msg }));
  }

  // Start the heartbeat: in a Web Worker if possible, else on the page.
  const start = { type: "start", url: healthUrl, interval: interval, offlineInterval: Math.min(2000, interval),
                  timeout: Math.min(4000, interval), threshold: 2 };
  let send = null;
  try {
    const worker = new Worker(script.dataset.workerUrl);
    worker.onmessage = (ev) => onMessage(ev.data);
    worker.onerror = () => { fallback(); };
    worker.postMessage(start);
    send = (m) => worker.postMessage(m);
  } catch (e) {
    fallback();
  }

  /** Run the heartbeat on the page itself (no Web Worker support). */
  function fallback() {
    if (send && send.isFallback) return;
    const tag = document.createElement("script");
    tag.src = script.dataset.workerUrl;
    tag.onload = () => {
      if (!window.HMIHeartbeat) return;
      const hb = window.HMIHeartbeat(onMessage);
      send = (m) => hb.handle(m);
      send.isFallback = true;
      hb.handle(start);
    };
    document.head.appendChild(tag);
  }

  /** Ask for an immediate check (e.g. after the browser reports a network change). */
  function checkNow() {
    if (send) send({ type: "check" });
  }

  window.addEventListener("online", checkNow);
  window.addEventListener("offline", checkNow);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkNow(); });
  document.addEventListener("fullscreenchange", () => { if (banner && !banner.hidden) ensureBanner(); });

  window.HMIConnection = {
    /** @returns {string} ``unknown``, ``online`` or ``offline``. */
    get state() { return state; },
    check: checkNow,
  };
})();
