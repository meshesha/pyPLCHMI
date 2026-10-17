/**
 * Sidebar layout of UI templates with "nav": "sidebar" (templates/_sidebar.html).
 *
 * - Operate / Engineer buttons show the pages of one workspace; the choice is
 *   remembered in this browser (localStorage, ignored when unavailable).
 * - The link of the current page is marked (aria-current="page").
 * - Screen links get a badge with the number of active alarms of that screen
 *   (from the "hmi:alarms" event of alarms.js).
 */
(function () {
  "use strict";

  const bar = document.getElementById("hmi-sidebar");
  if (!bar) return;
  const KEY = "hmi-workspace";

  /** Read the remembered workspace. */
  function load() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }

  /**
   * Show the links of a workspace.
   * @param {string} ws "operate" or "engineer".
   * @param {boolean} [remember] Store the choice.
   */
  function setWorkspace(ws, remember) {
    bar.dataset.workspace = ws;
    bar.querySelectorAll(".hmi-ws-switch [data-ws]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.ws === ws ? "true" : "false"));
    if (remember) {
      try { localStorage.setItem(KEY, ws); } catch (e) { /* storage unavailable */ }
    }
  }

  // Current page: the longest link path that the page path starts with.
  const here = window.location.pathname;
  let best = null;
  bar.querySelectorAll("a.hmi-side-link").forEach((a) => {
    const path = new URL(a.href, window.location.href).pathname;
    if ((here === path || (path !== "/" && here.startsWith(path))) && (!best || path.length > best.path.length)) best = { a: a, path: path };
  });
  let initial = load() || "operate";
  if (best) {
    best.a.setAttribute("aria-current", "page");
    initial = best.a.dataset.ws || initial;       // a page of the other workspace switches to it
  }
  if (!bar.querySelector(".hmi-ws-switch")) initial = "operate";
  setWorkspace(initial, false);

  bar.querySelectorAll(".hmi-ws-switch [data-ws]").forEach((b) => b.addEventListener("click", () => setWorkspace(b.dataset.ws, true)));

  // Alarm badges on the screen links.
  document.addEventListener("hmi:alarms", (e) => {
    const counts = {};
    (e.detail.events || []).forEach((ev) => {
      if (!ev.screen_url) return;
      const path = new URL(ev.screen_url, window.location.href).pathname;
      const rank = { critical: 3, high: 2, low: 1 };
      const c = counts[path] = counts[path] || { n: 0, color: ev.color, sev: 0, unacked: 0 };
      c.n += 1;
      if ((rank[ev.severity] || 0) > c.sev) { c.sev = rank[ev.severity] || 0; c.color = ev.color; }
      if (!ev.acked_at) counts[path].unacked += 1;
    });
    bar.querySelectorAll("[data-screen-path]").forEach((a) => {
      const badge = a.querySelector(".hmi-side-badge");
      const c = counts[a.dataset.screenPath];
      badge.hidden = !c;
      if (c) {
        badge.textContent = c.n;
        badge.style.background = c.color;
        badge.title = T("{0} active alarm(s), {1} unacknowledged", c.n, c.unacked);
      }
    });
  });
})();
