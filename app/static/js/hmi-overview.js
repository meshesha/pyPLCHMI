/**
 * Plant overview (main/overview.html).
 *
 * Polls /api/overview and updates every screen card: the status badge
 * (worst open alarm, or "Comm. lost" when a PLC of the screen is offline,
 * else "Normal"), the coloured top border and the first tag values.  A row
 * of chips shows the communication state of every PLC.  Status is never
 * shown by colour alone: each badge has text and the chips have a shape.
 */
(function () {
  "use strict";

  const root = document.getElementById("hmi-overview");
  if (!root) return;
  const chips = document.getElementById("ov-plcs");
  const refreshMs = Math.max(2000, parseInt(root.dataset.refreshMs, 10) || 5000);
  const cards = {};
  root.querySelectorAll("[data-screen]").forEach((c) => { cards[c.dataset.screen] = c; });

  /**
   * Create an element with a class and text.
   * @param {string} tag Tag name.
   * @param {string} [cls] Class names.
   * @param {string} [text] Text.
   * @returns {HTMLElement}
   */
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  /**
   * Draw the PLC chips.
   * @param {object} plcs PLC id -> status.
   */
  function renderPlcs(plcs) {
    chips.replaceChildren();
    Object.values(plcs).sort((a, b) => a.name.localeCompare(b.name)).forEach((p) => {
      const ok = p.connected;
      const chip = el("span", "hmi-ov-chip" + (ok ? "" : " down"));
      chip.append(el("span", "hmi-ov-shape " + (ok ? "ok" : "warn")), el("strong", "", p.name),
        el("span", "hmi-ov-chip-info", ok ? T("online") : (p.error || T("offline"))));
      chips.append(chip);
    });
  }

  /**
   * Update one screen card.
   * @param {HTMLElement} card Card element.
   * @param {object} s Screen status from the API.
   * @param {object} plcs PLC states.
   */
  function renderCard(card, s, plcs) {
    const status = card.querySelector("[data-role=status]");
    const down = s.plcs.some((id) => plcs[id] && !plcs[id].connected);
    let text = T("Normal");
    let state = "normal";
    if (s.alarms) {
      text = s.unacked ? T("{0} unacknowledged", s.unacked) : T("{0} active alarm(s)", s.alarms);
      state = "alarm";
    } else if (down) {
      text = T("Comm. lost");
      state = "comm";
    }
    status.textContent = text;
    card.dataset.state = state;
    card.style.setProperty("--ov-accent", s.color || "");
    const dl = card.querySelector("[data-role=values]");
    dl.replaceChildren();
    s.values.forEach((v) => {
      const box = el("div", "hmi-ov-kpi" + (v.quality === "bad" ? " bad" : ""));
      box.append(el("dt", "", v.label), el("dd", "", (v.display || "—") + (v.unit && v.display !== "—" && !String(v.display).includes(v.unit) ? " " + v.unit : "")));
      dl.append(box);
    });
  }

  let timer = null;
  /** Fetch the status once and schedule the next refresh. */
  async function poll() {
    try {
      const resp = await fetch(root.dataset.url, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (resp.status === 401) { window.location.reload(); return; }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      renderPlcs(data.plcs);
      data.screens.forEach((s) => { if (cards[s.id]) renderCard(cards[s.id], s, data.plcs); });
      if (window.HMIClock) window.HMIClock.reportPoll(data);
    } catch (err) {
      if (window.HMIClock) window.HMIClock.reportError(err);
    } finally {
      clearTimeout(timer);
      timer = setTimeout(poll, document.hidden ? refreshMs * 4 : refreshMs);
    }
  }
  poll();
})();
