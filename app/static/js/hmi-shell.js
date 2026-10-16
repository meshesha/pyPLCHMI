/**
 * Page shell: the clock in the middle of the top bar and the full-screen buttons.
 *
 * - The clock shows the local time (24 h) and date, updated every second.
 *   On screens it also shows the state of the live values with a coloured
 *   dot (``HMIClock.reportPoll`` / ``HMIClock.reportError``, called by
 *   ``dashboard.js`` and ``hmi-mimic.js``): green – values up to date,
 *   amber – a PLC is reconnecting, red – a PLC is offline or the update
 *   failed.  The details are in the clock's tooltip.
 * - Buttons with ``data-fullscreen-target="<selector>"`` switch that element
 *   to full screen and back; with ``data-fullscreen-frame="1"`` (screens) the
 *   screen runs in a full-screen frame so moving to another screen keeps
 *   full screen on.
 */
(function () {
  "use strict";

  const clock = document.getElementById("hmi-clock");
  const timeEl = document.getElementById("hmi-clock-time");
  const dateEl = document.getElementById("hmi-clock-date");
  const p2 = (n) => String(n).padStart(2, "0");
  let statusText = "";

  /** Show the current time and date. */
  function tick() {
    if (!clock) return;
    const d = new Date();
    timeEl.textContent = p2(d.getHours()) + ":" + p2(d.getMinutes()) + ":" + p2(d.getSeconds());
    dateEl.textContent = d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate());
    clock.title = dateEl.textContent + " " + timeEl.textContent + (statusText ? "\n" + statusText : "");
  }

  /**
   * Set the state dot of the clock.
   * @param {?string} state ``ok``, ``warn``, ``error`` or ``null`` (no dot).
   * @param {string} [text] Explanation for the tooltip.
   */
  function setStatus(state, text) {
    if (!clock) return;
    clock.dataset.state = state || "";
    statusText = text || "";
    tick();
  }

  /**
   * Communication problems of the PLCs of a screen.
   * @param {Object<string, {name:string, state:string, connected:boolean, error:?string, down_for:?number}>} plcs
   * @returns {{state:string, lines:string[]}}
   */
  function plcProblems(plcs) {
    let state = "ok";
    const lines = [];
    Object.values(plcs || {}).forEach((p) => {
      const s = p.state || (p.connected ? "online" : "offline");
      if (s === "online" || s === "unknown") return;
      if (s === "disabled") { lines.push(T("{0} disabled", p.name)); return; }
      if (s === "unstable") {
        lines.push(T("{0} reconnecting…", p.name));
        if (state === "ok") state = "warn";
        return;
      }
      lines.push(T("{0} offline", p.name) + (p.error ? ": " + p.error : ""));
      state = "error";
    });
    return { state: state, lines: lines };
  }

  window.HMIClock = {
    setStatus: setStatus,
    /**
     * Report a successful value poll.
     * @param {{timestamp:string, plcs:Object}} json Response of the values API.
     */
    reportPoll(json) {
      const problems = plcProblems(json.plcs);
      const updated = T("updated {0}", new Date(json.timestamp).toLocaleTimeString());
      setStatus(problems.state, [updated].concat(problems.lines).join("\n"));
    },
    /**
     * Report a failed value poll.
     * @param {Error|string} err The error.
     */
    reportError(err) {
      setStatus("error", T("update failed: {0}", (err && err.message) || err));
    },
  };

  tick();
  setInterval(tick, 1000);

  // Screens opened inside the full-screen frame (?embed=1) – see below.
  window.HMIEmbed = document.body.classList.contains("hmi-embed");
  if (window.HMIEmbed && window.parent !== window) {
    // A small "exit full screen" button in the corner of the frame (Esc works too).
    const exit = document.createElement("button");
    exit.type = "button";
    exit.className = "hmi-embed-exit";
    exit.title = T("Exit full screen (Esc)");
    exit.setAttribute("aria-label", T("Exit full screen (Esc)"));
    exit.innerHTML = '<i class="bi bi-fullscreen-exit"></i>';
    exit.addEventListener("click", () => {
      try { window.parent.document.exitFullscreen(); } catch (e) { /* not in full screen */ }
    });
    document.body.appendChild(exit);
  }

  /**
   * Full screen for screens: the screen is shown in a frame (``?embed=1``)
   * inside a full-screen overlay.  Buttons on the screen that open another
   * screen navigate inside the frame, so full screen stays on (a browser
   * always leaves full screen when the page itself is replaced).  When full
   * screen ends, the page follows to the screen that was open in the frame.
   * @param {HTMLElement} btn The full-screen button.
   */
  function screenFullscreen(btn) {
    const url = new URL(window.location.href);
    url.searchParams.set("embed", "1");
    const overlay = document.createElement("div");
    overlay.className = "hmi-fs-overlay";
    const frame = document.createElement("iframe");
    frame.className = "hmi-fs-frame";
    frame.title = btn.getAttribute("aria-label") || "";
    frame.src = url.pathname + url.search;
    overlay.appendChild(frame);
    document.body.appendChild(overlay);
    const request = overlay.requestFullscreen ? overlay.requestFullscreen() : null;
    /** Leave frame mode: remove the overlay and follow the frame's screen. */
    function done() {
      if (document.fullscreenElement) return;
      document.removeEventListener("fullscreenchange", done);
      let target = null;
      try {
        const inner = new URL(frame.contentWindow.location.href);
        inner.searchParams.delete("embed");
        if (inner.origin === window.location.origin && inner.pathname !== window.location.pathname) {
          target = inner.pathname + inner.search;
        }
      } catch (e) { /* frame not readable */ }
      overlay.remove();
      if (target) window.location.href = target;
    }
    document.addEventListener("fullscreenchange", done);
    if (request && request.catch) request.catch(() => { overlay.remove(); });
    frame.addEventListener("load", () => frame.focus());
  }

  // Full-screen buttons
  document.querySelectorAll("[data-fullscreen-target]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (document.fullscreenElement) { document.exitFullscreen(); return; }
      if (btn.dataset.fullscreenFrame === "1") { screenFullscreen(btn); return; }
      const target = document.querySelector(btn.dataset.fullscreenTarget);
      if (target && target.requestFullscreen) target.requestFullscreen();
    });
  });
})();
