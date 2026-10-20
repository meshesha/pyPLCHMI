/**
 * Modbus TCP server page (Admin → Modbus TCP server).
 *
 * - Table of published tags: type, address and scale are edited in place
 *   (POST /api/<id>; the server checks overlaps and reserved addresses).
 * - Live status every 2 s (GET /api/status): server state, clients, and for
 *   every tag its current value and the registers / bit actually published.
 * - "Add tags" dialog: picks tags (GET /api/tags) and publishes them with the
 *   next free addresses (POST /api/add).
 */
(function () {
  "use strict";

  const root = document.getElementById("mbs");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const tbody = document.querySelector("#mbs-table tbody");
  const typeOptions = $("mbs-types").innerHTML;
  const maxString = parseInt(root.dataset.maxString || "254", 10);
  let exports = JSON.parse($("mbs-data").textContent || "[]");
  let points = {};

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  async function post(url, body) {
    const resp = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await resp.json(); } catch (e) { data = { ok: false, error: "HTTP " + resp.status }; }
    return data;
  }

  function notify(message, icon) {
    if (window.HMIDialog) window.HMIDialog.toast(message, { icon: icon || "info" });
  }

  /* ---------- table ---------- */

  function render() {
    exports.sort((a, b) => (a.area === b.area ? a.address - b.address : (a.area === "reg" ? -1 : 1)));
    tbody.replaceChildren(...exports.map(row));
    $("mbs-count").textContent = exports.length;
    $("mbs-empty").hidden = exports.length > 0;
    updateSelection();
    showPoints();
  }

  /** One table row. */
  function row(x) {
    const tr = el("tr");
    tr.dataset.id = x.id;

    const tdCheck = el("td", "text-center");
    const check = el("input", "form-check-input mbs-check");
    check.type = "checkbox";
    check.setAttribute("aria-label", T("Select {0}", x.label));
    check.addEventListener("change", updateSelection);
    tdCheck.appendChild(check);

    const tdTag = el("td");
    tdTag.appendChild(el("div", "fw-semibold", x.label));
    tdTag.appendChild(el("div", "small text-secondary", `${x.plc} · ${x.tag_type}`));

    const tdValue = el("td", "font-monospace mbs-value", "—");

    const tdType = el("td", "text-nowrap");
    const select = el("select", "form-select form-select-sm d-inline-block w-auto");
    select.innerHTML = typeOptions;
    select.value = x.mb_type;
    select.setAttribute("aria-label", T("Modbus type of {0}", x.label));
    const len = el("input", "form-control form-control-sm d-inline-block mbs-len ms-1");
    len.type = "number";
    len.min = 1;
    len.max = maxString;
    len.value = x.string_length || 20;
    len.title = T("STRING length");
    len.setAttribute("aria-label", T("STRING length"));
    // Bootstrap's d-inline-block would override the hidden attribute: toggle d-none instead.
    len.classList.toggle("d-none", x.mb_type !== "STRING");
    select.addEventListener("change", () => {
      len.classList.toggle("d-none", select.value !== "STRING");
      // Bits and registers are separate areas: a new area gets the next free address.
      const area = select.value === "BIT" ? "bit" : "reg";
      update(x, { mb_type: select.value, string_length: Number(len.value),
                  address: area === x.area ? x.address : "" });
    });
    len.addEventListener("change", () => update(x, { string_length: Number(len.value) }));
    tdType.append(select, len);

    const tdAddr = el("td");
    const addr = el("input", "form-control form-control-sm mbs-address");
    addr.type = "number";
    addr.min = 0;
    addr.max = 65535;
    addr.value = x.address;
    addr.setAttribute("aria-label", T("Address of {0}", x.label));
    addr.addEventListener("change", () => update(x, { address: addr.value === "" ? "" : Number(addr.value) }));
    tdAddr.appendChild(addr);
    if (x.count > 1) tdAddr.appendChild(el("div", "small text-secondary", T("to {0}", x.last)));

    const tdRef = el("td", "small text-nowrap");
    tdRef.appendChild(el("div", "font-monospace", x.ref));
    tdRef.appendChild(el("div", "font-monospace text-secondary", x.ref_ro));
    tdRef.title = x.area === "bit" ? T("coil / discrete input") : T("holding register / input register");

    const tdScale = el("td");
    const scale = el("input", "form-control form-control-sm mbs-scale");
    scale.type = "number";
    scale.step = "any";
    scale.value = x.scale;
    scale.disabled = ["BIT", "BOOL16", "STRING"].includes(x.mb_type);
    scale.setAttribute("aria-label", T("Scale of {0}", x.label));
    scale.addEventListener("change", () => update(x, { scale: Number(scale.value) }));
    tdScale.appendChild(scale);

    const tdPub = el("td", "font-monospace small mbs-published", "—");

    // Allow write: only for tags that are writable in pyPLCHMI.
    const tdWrite = el("td", "text-center");
    const wrap = el("div", "form-check form-switch d-inline-block mb-0");
    const sw = el("input", "form-check-input mbs-write");
    sw.type = "checkbox";
    sw.role = "switch";
    sw.checked = !!x.writable;
    sw.disabled = !x.tag_writable && !x.writable;
    sw.setAttribute("aria-label", T("Other SCADA systems may write {0}", x.label));
    sw.title = !x.tag_writable ? T("The tag is read only in pyPLCHMI (Tag table → Edit → Writable).")
      : T("Other SCADA systems may write this tag");
    sw.addEventListener("change", () => update(x, { writable: sw.checked }));
    wrap.appendChild(sw);
    tdWrite.appendChild(wrap);
    if (x.writable && !x.write_ok) {
      tdWrite.appendChild(el("div", "small text-warning", T("tag read only")));
    }
    tr.append(tdCheck, tdTag, tdValue, tdType, tdAddr, tdRef, tdScale, tdPub, tdWrite);
    return tr;
  }

  /** Save a change of one row; the server answers with the updated row or the problems. */
  async function update(x, change) {
    const url = root.dataset.updateUrl.replace(/\/0$/, "/" + x.id);
    const data = await post(url, Object.assign({ mb_type: x.mb_type, address: x.address, scale: x.scale,
                                                 string_length: x.string_length, writable: x.writable }, change));
    if (data.ok) {
      Object.assign(x, data.export);
    } else {
      notify((data.errors || [data.error || T("Not saved.")]).join("\n"), "error");
    }
    render();
  }

  function updateSelection() {
    const checks = [...tbody.querySelectorAll(".mbs-check")];
    const selected = checks.filter((c) => c.checked).length;
    $("mbs-remove").disabled = selected === 0;
    $("mbs-check-all").checked = checks.length > 0 && selected === checks.length;
    $("mbs-check-all").indeterminate = selected > 0 && selected < checks.length;
  }

  /** Registers as hex words, e.g. 41AC 0000. */
  const hex = (words) => words.map((w) => w.toString(16).toUpperCase().padStart(4, "0")).join(" ");

  function showPoints() {
    tbody.querySelectorAll("tr").forEach((tr) => {
      const p = points[tr.dataset.id] || {};
      const value = tr.querySelector(".mbs-value");
      value.textContent = p.display === undefined ? "—" : p.display;
      value.classList.toggle("text-danger", p.quality === "bad");
      value.title = p.quality === "bad" ? T("no valid value") : "";
      const pub = tr.querySelector(".mbs-published");
      if ("bit" in p) pub.textContent = p.bit ? "1 (ON)" : "0 (OFF)";
      else if (p.words) pub.textContent = hex(p.words);
      else pub.textContent = "—";
    });
  }

  /* ---------- status ---------- */

  const STATES = {
    running: ["text-bg-success", T("running")],
    disabled: ["hmi-badge-neutral", T("disabled")],
    error: ["text-bg-danger", T("error")],
    starting: ["text-bg-warning", T("starting…")],
  };
  const fmtTime = (epoch) => (epoch ? new Date(epoch * 1000).toLocaleTimeString() : "—");

  async function poll() {
    try {
      const resp = await fetch(root.dataset.statusUrl, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!resp.ok) return;
      const data = await resp.json();
      const s = data.status;
      const [cls, label] = STATES[s.state] || STATES.starting;
      const badge = $("mbs-state");
      badge.className = "badge fs-6 " + cls;
      badge.textContent = label;
      $("mbs-error").hidden = !s.error;
      $("mbs-error").textContent = s.error || "";
      $("mbs-endpoint").textContent = s.state === "running" ? `${s.host}:${s.port}` : "—";
      $("mbs-updated").textContent = fmtTime(s.updated);
      $("mbs-quality").textContent = s.updated ? `${s.good} / ${s.bad}` : "—";
      $("mbs-requests").textContent = s.requests;
      $("mbs-writes").textContent = `${s.writes_done} / ${s.writes_refused} / ${s.writes_failed}`;
      const writes = (s.recent_writes || []).map((w) => {
        const tr = el("tr", w.error ? "table-danger" : "");
        tr.append(el("td", "", fmtTime(w.time)), el("td", "font-monospace", w.client), el("td", "", w.tag),
                  el("td", "font-monospace", w.error ? w.error : JSON.stringify(w.value)));
        return tr;
      });
      $("mbs-recent").hidden = writes.length === 0;
      if (writes.length) $("mbs-recent-rows").replaceChildren(...writes);
      $("mbs-refused").textContent = s.refused;
      $("mbs-heartbeat").textContent = s.heartbeat === null ? T("off") : s.heartbeat;
      const rows = s.clients.map((c) => {
        const tr = el("tr");
        tr.append(el("td", "font-monospace", c.address), el("td", "", c.connections), el("td", "", c.requests),
                  el("td", "", fmtTime(c.last)));
        return tr;
      });
      if (rows.length) $("mbs-clients").replaceChildren(...rows);
      points = data.points || {};
      showPoints();
    } catch (e) { /* server busy or offline: next poll */ }
  }

  /* ---------- add / remove ---------- */

  let candidates = [];
  const pickerList = $("mbs-picker-list");

  function pickerCount() {
    const n = pickerList.querySelectorAll("input:checked").length;
    $("mbs-picker-count").textContent = T("{0} selected", n);
    $("mbs-picker-add").disabled = n === 0;
  }

  async function loadCandidates() {
    pickerList.replaceChildren(el("div", "text-secondary", T("loading…")));
    const resp = await fetch(root.dataset.tagsUrl, { credentials: "same-origin", headers: { Accept: "application/json" } });
    candidates = resp.ok ? await resp.json() : [];
    pickerList.replaceChildren(...candidates.map((t) => {
      const label = el("label", "list-group-item d-flex gap-2 align-items-center");
      const box = el("input", "form-check-input m-0");
      box.type = "checkbox";
      box.value = t.id;
      box.addEventListener("change", pickerCount);
      const text = el("span", "flex-grow-1");
      text.append(el("span", "fw-semibold", t.label), el("span", "text-secondary", ` · ${t.plc} · ${t.data_type}`));
      label.dataset.search = `${t.label} ${t.plc} ${t.data_type}`.toLowerCase();
      label.append(box, text, el("span", "badge hmi-badge-neutral", t.mb_type));
      return label;
    }));
    if (!candidates.length) pickerList.replaceChildren(el("div", "text-secondary", T("Every tag is already published.")));
    $("mbs-picker-filter").value = "";
    pickerCount();
  }

  $("mbs-picker").addEventListener("show.bs.modal", loadCandidates);
  $("mbs-picker-filter").addEventListener("input", () => {
    const q = $("mbs-picker-filter").value.toLowerCase();
    pickerList.querySelectorAll("label").forEach((l) => { l.hidden = q && !l.dataset.search.includes(q); });
  });
  $("mbs-picker-all").addEventListener("click", () => {
    pickerList.querySelectorAll("label:not([hidden]) input").forEach((b) => { b.checked = true; });
    pickerCount();
  });
  $("mbs-picker-add").addEventListener("click", async () => {
    const ids = [...pickerList.querySelectorAll("input:checked")].map((b) => Number(b.value));
    const data = await post(root.dataset.addUrl, { ids: ids });
    if (!data.ok) { notify(data.error || T("Not saved."), "error"); return; }
    exports = data.exports;
    render();
    notify(T("{0} tag(s) published.", data.added), "success");
    bootstrap.Modal.getOrCreateInstance($("mbs-picker")).hide();
    poll();
  });

  $("mbs-check-all").addEventListener("change", (ev) => {
    tbody.querySelectorAll(".mbs-check").forEach((c) => { c.checked = ev.target.checked; });
    updateSelection();
  });
  $("mbs-remove").addEventListener("click", async () => {
    const ids = [...tbody.querySelectorAll("tr")].filter((tr) => tr.querySelector(".mbs-check").checked)
      .map((tr) => Number(tr.dataset.id));
    const ok = window.HMIDialog
      ? await window.HMIDialog.confirm(T("Stop publishing {0} tag(s)? SCADA systems reading them will get 0.", ids.length), { danger: true, confirmText: T("Remove") })
      : window.confirm(T("Stop publishing {0} tag(s)?", ids.length));
    if (!ok) return;
    const data = await post(root.dataset.removeUrl, { ids: ids });
    if (data.ok) {
      exports = exports.filter((x) => !ids.includes(x.id));
      render();
    }
  });

  render();
  poll();
  setInterval(poll, 2000);
})();
