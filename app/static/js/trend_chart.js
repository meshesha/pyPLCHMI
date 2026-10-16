/**
 * Trend chart viewer (Chart.js with the date-fns time adapter).
 *
 * Configuration comes from ``#chart-config``: the series (tag, label, unit,
 * colour, axis, BOOL texts…), the default range key, the range presets and
 * the URL of the series API (``/history/api/series``).
 *
 * Features: range presets and a custom From/To range, earlier/later
 * buttons, live mode (re-reads the range periodically), drag-to-zoom with
 * double-click to go back, a colour picker and show/hide switch per tag, the
 * newest value of each tag, links to the history table and the Excel export
 * of the shown range, and "Save image" (PNG with a dark background, the title
 * and the time range).
 */
(function () {
  "use strict";

  const cfg = JSON.parse(document.getElementById("chart-config").textContent);
  const canvas = document.getElementById("trend-canvas");
  const box = canvas.parentElement;
  const statusEl = document.getElementById("chart-status");
  const fromInput = document.getElementById("c-from");
  const toInput = document.getElementById("c-to");
  const liveBox = document.getElementById("live");
  const localInput = (window.HMIHistory && window.HMIHistory.localInput) || function (d) {
    const p = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" +
      p(d.getMinutes()) + ":" + p(d.getSeconds());
  };
  /**
   * A colour of the current theme (CSS variable of hmi.css).
   * @param {string} name Variable name.
   * @param {string} fallback Value when the variable is missing.
   * @returns {string}
   */
  function themeColor(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }
  // colours of the theme in use when the page was opened
  const BG = themeColor("--hmi-chart-bg", "#12161c");
  const TEXT = themeColor("--hmi-chart-text", "#c9d1d9");
  const GRID = themeColor("--hmi-chart-grid", "rgba(255,255,255,0.08)");
  const TITLE = themeColor("--hmi-chart-title", "#ffffff");

  const series = cfg.series.map((s) => Object.assign({ visible: true, latest: null }, s));
  const state = { key: cfg.range, start: null, end: null, live: true, stack: [], timer: null, busy: false };

  /**
   * Format a value of a series like the tag shows it.
   * @param {Object} s Series.
   * @param {?number} v Value.
   * @returns {string}
   */
  function fmt(s, v) {
    if (v === null || v === undefined) return "—";
    if (s.is_bool) return v ? s.true_text : s.false_text;
    const d = Number.isInteger(s.decimals) ? s.decimals : 2;
    return Number(v).toFixed(d) + (s.unit ? " " + s.unit : "");
  }

  /**
   * Full local date and time.
   * @param {number} ms Epoch milliseconds.
   * @returns {string} ``YYYY-MM-DD HH:MM:SS``.
   */
  function fullDate(ms) {
    return localInput(new Date(ms)).replace("T", " ");
  }

  /** Units shown on an axis title. */
  function axisTitle(axis) {
    const units = Array.from(new Set(series.filter((s) => s.axis === axis && s.visible && !s.is_bool && s.unit)
      .map((s) => s.unit)));
    return units.join(" / ");
  }

  const hasRight = () => series.some((s) => s.axis === "right");

  const chart = new Chart(canvas, {
    type: "line",
    data: {
      datasets: series.map((s) => ({
        label: s.label + (s.unit ? " [" + s.unit + "]" : ""),
        data: [],
        parsing: false,
        borderColor: s.color,
        backgroundColor: s.color,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        stepped: s.is_bool ? "before" : false,
        tension: 0,
        spanGaps: false,
        yAxisID: s.axis === "right" ? "y2" : "y",
      })),
    },
    options: {
      animation: false,
      maintainAspectRatio: false,
      normalized: true,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: {
          type: "time",
          time: { tooltipFormat: "yyyy-MM-dd HH:mm:ss",
            displayFormats: { millisecond: "HH:mm:ss.SSS", second: "HH:mm:ss", minute: "HH:mm", hour: "MM-dd HH:mm",
              day: "yyyy-MM-dd", week: "yyyy-MM-dd", month: "yyyy-MM", quarter: "yyyy-MM", year: "yyyy" } },
          ticks: { color: TEXT, maxRotation: 0, autoSkipPadding: 16 },
          grid: { color: GRID },
        },
        y: { position: "left", ticks: { color: TEXT }, grid: { color: GRID }, title: { display: false, color: TEXT } },
        y2: { position: "right", display: hasRight(), ticks: { color: TEXT }, grid: { drawOnChartArea: false },
          title: { display: false, color: TEXT } },
      },
      plugins: {
        legend: { labels: { color: TEXT, usePointStyle: true, pointStyle: "line" },
          onClick: (ev, item) => { toggle(item.datasetIndex, !series[item.datasetIndex].visible); } },
        tooltip: {
          callbacks: {
            label: (ctx) => " " + series[ctx.datasetIndex].label + ": " + fmt(series[ctx.datasetIndex], ctx.parsed.y),
          },
        },
      },
    },
  });

  /** Refresh axis titles and the right axis visibility. */
  function updateAxes() {
    const left = axisTitle("left");
    const right = axisTitle("right");
    chart.options.scales.y.title = { display: Boolean(left), text: left, color: TEXT };
    chart.options.scales.y2.title = { display: Boolean(right), text: right, color: TEXT };
    chart.options.scales.y2.display = series.some((s) => s.axis === "right" && s.visible);
  }

  /**
   * Show or hide a series.
   * @param {number} i Series index.
   * @param {boolean} visible New state.
   */
  function toggle(i, visible) {
    series[i].visible = visible;
    chart.setDatasetVisibility(i, visible);
    const box = document.querySelector('[data-series="' + i + '"] input[type=checkbox]');
    if (box) box.checked = visible;
    updateAxes();
    chart.update("none");
  }

  /** Build the side list (colour, show/hide, newest value). */
  function buildList() {
    const list = document.getElementById("series-list");
    list.innerHTML = "";
    series.forEach((s, i) => {
      const li = document.createElement("li");
      li.className = "d-flex align-items-center gap-2 mb-2";
      li.dataset.series = String(i);
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "form-check-input mt-0";
      check.checked = s.visible;
      check.title = T("Show / hide");
      check.setAttribute("aria-label", T("Show / hide") + " " + s.label);
      check.addEventListener("change", () => toggle(i, check.checked));
      const color = document.createElement("input");
      color.type = "color";
      color.className = "form-control form-control-color form-control-sm p-0 border-0 hmi-series-color";
      color.value = s.color;
      color.setAttribute("aria-label", T("Colour") + " " + s.label);
      color.addEventListener("input", () => {
        s.color = color.value;
        chart.data.datasets[i].borderColor = s.color;
        chart.data.datasets[i].backgroundColor = s.color;
        chart.update("none");
      });
      const text = document.createElement("div");
      text.className = "flex-grow-1 small lh-sm";
      const name = document.createElement("div");
      name.textContent = s.label;
      const meta = document.createElement("div");
      meta.className = "text-secondary";
      meta.textContent = [s.plc, s.axis === "right" ? T("right axis") : "", s.logged ? T("every {0} s", s.interval) : T("logging off")]
        .filter(Boolean).join(" · ");
      text.append(name, meta);
      const value = document.createElement("span");
      value.className = "hmi-value small text-nowrap series-latest";
      value.textContent = fmt(s, s.latest);
      li.append(check, color, text, value);
      list.appendChild(li);
    });
  }

  /** Current range in epoch ms. */
  function currentRange() {
    if (state.live && state.key) {
      const seconds = cfg.ranges[state.key][0];
      const end = Date.now();
      return [end - seconds * 1000, end];
    }
    return [state.start, state.end];
  }

  /** Show the range in the From/To inputs and mark the active preset. */
  function showRange(start, end) {
    fromInput.value = localInput(new Date(start));
    toInput.value = localInput(new Date(end));
    document.querySelectorAll("#range-buttons [data-range]").forEach((b) => {
      b.classList.toggle("active", state.live && b.dataset.range === state.key);
    });
    liveBox.checked = state.live;
  }

  /** Delay between live refreshes (ms). */
  function refreshDelay() {
    const shortest = Math.min.apply(null, series.map((s) => s.interval || 60).concat([60]));
    const seconds = state.key ? cfg.ranges[state.key][0] : 3600;
    const base = Math.max(5000, shortest * 1000);
    return Math.min(seconds > 86400 ? 60000 : 30000, base);
  }

  /** (Re)start or stop the live timer. */
  function schedule() {
    if (state.timer) clearTimeout(state.timer);
    state.timer = null;
    if (state.live) state.timer = setTimeout(load, refreshDelay());
  }

  /** Read the series for the current range and redraw. */
  async function load() {
    if (state.busy) return;
    const [start, end] = currentRange();
    if (!(start < end)) {
      statusEl.textContent = T("The start of the range must be before its end.");
      return;
    }
    state.busy = true;
    showRange(start, end);
    const points = Math.max(200, Math.min(4000, Math.round(canvas.clientWidth * 2)));
    const url = cfg.api + "?tags=" + series.map((s) => s.tag_id).join(",") + "&start=" + Math.round(start) +
      "&end=" + Math.round(end) + "&points=" + points;
    try {
      const resp = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || resp.statusText);
      let total = 0;
      let reduced = false;
      series.forEach((s, i) => {
        const res = data.series[String(s.tag_id)] || { points: [], count: 0 };
        total += res.count;
        reduced = reduced || res.reduced;
        chart.data.datasets[i].data = res.points.map((p) => ({ x: p[0], y: p[1] }));
        chart.data.datasets[i].pointRadius = res.points.length < 60 ? 2 : 0;
        const last = res.points.filter((p) => p[1] !== null).pop();
        s.latest = last ? last[1] : null;
      });
      chart.options.scales.x.min = data.start;
      chart.options.scales.x.max = data.end;
      updateAxes();
      chart.update("none");
      document.querySelectorAll(".series-latest").forEach((el, i) => { el.textContent = fmt(series[i], series[i].latest); });
      statusEl.textContent = total
        ? T("{0} samples", total) + (reduced ? " · " + T("reduced for display") : "") + " · " +
          T("updated {0}", new Date().toLocaleTimeString())
        : T("No samples in this range.");
    } catch (err) {
      statusEl.textContent = T("Loading failed: {0}", err.message || err);
    } finally {
      state.busy = false;
      schedule();
    }
  }

  /**
   * Switch to a fixed range (not live).
   * @param {number} start Epoch ms.
   * @param {number} end Epoch ms.
   * @param {boolean} [remember] Push the previous range for double-click "back".
   */
  function setCustom(start, end, remember) {
    if (remember) state.stack.push({ key: state.key, live: state.live, start: state.start, end: state.end });
    state.live = false;
    state.start = start;
    state.end = end;
    load();
  }

  // Range presets
  document.querySelectorAll("#range-buttons [data-range]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.key = btn.dataset.range;
      state.live = true;
      state.stack = [];
      load();
    });
  });

  document.getElementById("apply-range").addEventListener("click", () => {
    const start = fromInput.value ? new Date(fromInput.value).getTime() : NaN;
    const end = toInput.value ? new Date(toInput.value).getTime() : Date.now();
    if (Number.isNaN(start) || Number.isNaN(end) || start >= end) {
      HMIDialog.alert(T("The start of the range must be before its end."), { icon: "warning" });
      return;
    }
    setCustom(start, end, true);
  });

  liveBox.addEventListener("change", () => {
    if (liveBox.checked) {
      const [start, end] = currentRange();
      const span = (end - start) / 1000;
      // the preset closest to the shown span
      let best = "1h";
      Object.entries(cfg.ranges).forEach(([k, v]) => {
        if (Math.abs(v[0] - span) < Math.abs(cfg.ranges[best][0] - span)) best = k;
      });
      state.key = state.key || best;
      state.live = true;
    } else {
      const [start, end] = currentRange();
      state.live = false;
      state.start = start;
      state.end = end;
    }
    load();
  });

  /**
   * Move the window by its own length.
   * @param {number} dir -1 earlier, +1 later.
   */
  function shift(dir) {
    const [start, end] = currentRange();
    const len = end - start;
    let s = start + dir * len;
    let e = end + dir * len;
    if (e > Date.now()) { e = Date.now(); s = e - len; }
    setCustom(s, e, false);
  }
  document.getElementById("shift-back").addEventListener("click", () => shift(-1));
  document.getElementById("shift-fwd").addEventListener("click", () => shift(1));

  // Drag to zoom, double-click to go back.
  const band = document.createElement("div");
  band.className = "hmi-zoom-band d-none";
  box.appendChild(band);
  let dragX = null;
  canvas.addEventListener("mousedown", (ev) => {
    const area = chart.chartArea;
    if (ev.button !== 0 || !area || ev.offsetX < area.left || ev.offsetX > area.right) return;
    dragX = ev.offsetX;
    band.style.left = dragX + "px";
    band.style.width = "0px";
    band.style.top = area.top + "px";
    band.style.height = (area.bottom - area.top) + "px";
    band.classList.remove("d-none");
  });
  canvas.addEventListener("mousemove", (ev) => {
    if (dragX === null) return;
    const area = chart.chartArea;
    const x = Math.max(area.left, Math.min(area.right, ev.offsetX));
    band.style.left = Math.min(dragX, x) + "px";
    band.style.width = Math.abs(x - dragX) + "px";
  });
  window.addEventListener("mouseup", (ev) => {
    if (dragX === null) return;
    const area = chart.chartArea;
    const rect = canvas.getBoundingClientRect();
    const x = Math.max(area.left, Math.min(area.right, ev.clientX - rect.left));
    const x0 = dragX;
    dragX = null;
    band.classList.add("d-none");
    if (Math.abs(x - x0) < 6) return;
    const a = chart.scales.x.getValueForPixel(Math.min(x0, x));
    const b = chart.scales.x.getValueForPixel(Math.max(x0, x));
    if (b - a >= 1000) setCustom(a, b, true);
  });
  canvas.addEventListener("dblclick", () => {
    const prev = state.stack.pop();
    if (!prev) return;
    Object.assign(state, prev);
    load();
  });

  /** Query string of the shown range for the table and the Excel export. */
  function rangeQuery() {
    const [start, end] = currentRange();
    return "tags=" + series.map((s) => s.tag_id).join(",") + "&from=" + encodeURIComponent(localInput(new Date(start))) +
      "&to=" + encodeURIComponent(localInput(new Date(end)));
  }
  document.getElementById("show-table").addEventListener("click", (ev) => {
    ev.preventDefault();
    window.location.href = ev.currentTarget.dataset.url + "?" + rangeQuery();
  });
  document.getElementById("export-xlsx").addEventListener("click", (ev) => {
    ev.preventDefault();
    window.location.href = ev.currentTarget.dataset.url + "?layout=wide&" + rangeQuery();
  });
  const saveQuick = document.getElementById("save-quick");
  if (saveQuick) {
    saveQuick.addEventListener("click", (ev) => {
      ev.preventDefault();
      window.location.href = saveQuick.dataset.url + "?tags=" + series.map((s) => s.tag_id).join(",") +
        "&colors=" + encodeURIComponent(series.map((s) => s.color).join(",")) + "&range=" + (state.key || "1h");
    });
  }

  /** Download the chart as a PNG (dark background, title and range). */
  function saveImage() {
    const ratio = canvas.width / canvas.clientWidth || 1;
    const header = Math.round(52 * ratio);
    const out = document.createElement("canvas");
    out.width = canvas.width;
    out.height = canvas.height + header;
    const ctx = out.getContext("2d");
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.fillStyle = TITLE;
    ctx.font = "600 " + Math.round(16 * ratio) + "px system-ui, -apple-system, 'Segoe UI', Arial, sans-serif";
    ctx.textBaseline = "top";
    const rtl = document.documentElement.dir === "rtl";
    ctx.direction = rtl ? "rtl" : "ltr";
    ctx.textAlign = rtl ? "right" : "left";
    const x = rtl ? out.width - 12 * ratio : 12 * ratio;
    ctx.fillText(cfg.title, x, 8 * ratio);
    const [start, end] = currentRange();
    ctx.fillStyle = TEXT;
    ctx.font = Math.round(12 * ratio) + "px system-ui, -apple-system, 'Segoe UI', Arial, sans-serif";
    ctx.fillText(fullDate(start) + "  →  " + fullDate(end), x, 30 * ratio);
    ctx.drawImage(canvas, 0, header);
    const stamp = localInput(new Date()).replace(/[-:]/g, "").replace("T", "-").slice(0, 13);
    const name = (cfg.title || "chart").replace(/[\\/:*?"<>|]+/g, "_").trim() + "-" + stamp + ".png";
    out.toBlob((blob) => {
      if (!blob) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      HMIDialog.toast(T("Image saved: {0}", name));
    }, "image/png");
  }
  document.getElementById("save-image").addEventListener("click", saveImage);

  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.live) load(); });

  buildList();
  updateAxes();
  load();
})();
