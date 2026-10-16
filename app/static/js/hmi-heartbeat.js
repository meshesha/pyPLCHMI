/**
 * Server heartbeat – runs as a dedicated Web Worker (see hmi-connection.js).
 *
 * The worker calls ``GET /api/health`` every few seconds and tells the page
 * when the server stops answering and when it is back.  A worker is used
 * because browsers slow down timers of background tabs heavily (Chrome:
 * once a minute), while a worker keeps its own pace, so a wall display or a
 * minimised operator window still notices a lost server within seconds.
 *
 * Rules:
 * - a check fails on a network error, a timeout or any answer other than
 *   ``200 {"status": "ok"}`` (``503`` = database unavailable);
 * - the server counts as lost after ``threshold`` failed checks in a row
 *   (default 2), so a single slow answer does not raise the alert;
 * - while lost, checks run more often (``offlineInterval``) to report the
 *   recovery quickly;
 * - a changed ``boot`` id means the server process was restarted.
 *
 * Messages to the worker: ``{type: "start", url, interval, offlineInterval,
 * timeout, threshold}``, ``{type: "check"}`` (check now), ``{type: "stop"}``.
 *
 * Messages from the worker:
 * - ``{type: "offline", since, reason, attempts}`` – the server was lost;
 * - ``{type: "retry", since, reason, attempts}`` – still lost after another check;
 * - ``{type: "online", wasOffline, downMs, restarted, latency}`` – first
 *   success, recovery, or a restart noticed between two checks.
 *
 * When Web Workers are not available, the same code runs on the page as
 * ``window.HMIHeartbeat(post)``.
 */
(function (scope) {
  "use strict";

  /**
   * Create a heartbeat.
   * @param {function(Object):void} post Receives the messages listed above.
   * @returns {{handle: function(Object):void}} Feed it the messages to the worker.
   */
  function createHeartbeat(post) {
    const cfg = { url: "", interval: 5000, offlineInterval: 2000, timeout: 4000, threshold: 2 };
    let failures = 0;
    let online = null;       // null = not known yet, true / false
    let since = null;        // time the server was lost (ms)
    let boot = null;         // boot id of the server process
    let timer = null;
    let running = false;
    let busy = false;

    /**
     * Plan the next check.
     * @param {number} ms Delay in milliseconds.
     */
    function schedule(ms) {
      clearTimeout(timer);
      if (running) timer = setTimeout(check, ms);
    }

    /**
     * A successful check.
     * @param {{boot:string}} body Answer of the health endpoint.
     * @param {number} latency Round-trip time in ms.
     */
    function succeeded(body, latency) {
      failures = 0;
      const restarted = boot !== null && body.boot !== boot;
      boot = body.boot;
      if (online !== true || restarted) {
        post({ type: "online", wasOffline: online === false, downMs: since ? Date.now() - since : 0,
               restarted: restarted, latency: latency });
      }
      online = true;
      since = null;
    }

    /**
     * A failed check.
     * @param {string} reason ``timeout``, ``network``, ``database`` or ``http <code>``.
     */
    function failed(reason) {
      failures += 1;
      if (online === false) {
        post({ type: "retry", since: since, reason: reason, attempts: failures });
      } else if (failures >= cfg.threshold) {
        online = false;
        since = Date.now() - (failures - 1) * cfg.interval;  // first failed check
        post({ type: "offline", since: since, reason: reason, attempts: failures });
      }
    }

    /** Call the health endpoint once and plan the next call. */
    async function check() {
      if (busy || !cfg.url) return;
      busy = true;
      const ctrl = typeof AbortController === "function" ? new AbortController() : null;
      const watchdog = setTimeout(() => { if (ctrl) ctrl.abort(); }, cfg.timeout);
      const t0 = Date.now();
      try {
        const res = await fetch(cfg.url, { cache: "no-store", credentials: "same-origin",
                                           headers: { Accept: "application/json" },
                                           signal: ctrl ? ctrl.signal : undefined });
        let body = null;
        try { body = await res.json(); } catch (e) { /* not JSON – e.g. a proxy error page */ }
        if (res.ok && body && body.status === "ok") succeeded(body, Date.now() - t0);
        else failed(res.status === 503 ? "database" : "http " + res.status);
      } catch (err) {
        failed(err && err.name === "AbortError" ? "timeout" : "network");
      } finally {
        clearTimeout(watchdog);
        busy = false;
      }
      schedule(online === false ? cfg.offlineInterval : cfg.interval);
    }

    /**
     * Handle a message from the page.
     * @param {Object} msg ``start`` / ``check`` / ``stop`` (see the file header).
     */
    function handle(msg) {
      const m = msg || {};
      if (m.type === "start") {
        ["url", "interval", "offlineInterval", "timeout", "threshold"].forEach((k) => {
          if (m[k]) cfg[k] = m[k];
        });
        running = true;
        schedule(0);
      } else if (m.type === "check") {
        if (running && !busy) schedule(0);
      } else if (m.type === "stop") {
        running = false;
        clearTimeout(timer);
      }
    }

    return { handle: handle };
  }

  const inWorker = typeof WorkerGlobalScope !== "undefined" && scope instanceof WorkerGlobalScope;
  if (inWorker) {
    const hb = createHeartbeat((msg) => scope.postMessage(msg));
    scope.onmessage = (ev) => hb.handle(ev.data);
  } else {
    scope.HMIHeartbeat = createHeartbeat;
  }
})(self);
