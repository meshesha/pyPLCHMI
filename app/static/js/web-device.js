/**
 * "Add device" wizard for web devices (HTTP / HTTPS / HTTP/0.9 and WebSocket, JSON or XML).
 *
 * 1. Device settings (for HTTP also GET or POST with a body, and extra
 *    headers) → "Test" posts them to /admin/plcs/web/api/test and
 *    shows the received data (formatted) or the error.
 * 2. "Process" (shown after a successful test) fills a table with every key
 *    of the data: checkbox, key, suggested tag name (editable, checked live
 *    with /api/check-names), data type and value.
 * 3. "Save Selected" / "Save All" post the chosen rows to /api/save, which
 *    creates the device (when new) and the tags in one transaction.  Problems
 *    are shown on the fields and table rows; nothing is saved then.
 */
(function () {
  "use strict";

  const root = document.getElementById("wd");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const plcId = root.dataset.plcId || "";
  const examples = JSON.parse(root.dataset.examples || "{}");
  const maxString = parseInt(root.dataset.maxString || "254", 10);
  const typeOptions = $("wd-types").innerHTML;
  const tbody = document.querySelector("#wd-table tbody");
  let rows = [];          // rows of the last successful test
  let lastSettings = "";  // settings (JSON) of the last successful test
  let checkTimer = null;
  let checkSeq = 0;

  /* ---------- settings ---------- */

  /** The device settings currently entered. */
  function settings() {
    const fmt = document.querySelector('input[name="wd-format"]:checked');
    return {
      plc_id: plcId || null,
      name: $("wd-name").value.trim(),
      description: $("wd-description") ? $("wd-description").value.trim() : "",
      protocol: $("wd-protocol").value,
      url: $("wd-url").value.trim(),
      data_format: fmt ? fmt.value : "json",
      poll_interval: $("wd-interval").value,
      timeout: $("wd-timeout").value,
      http_mode: $("wd-mode").value,
      verify_tls: $("wd-verify").checked,
      ws_message: $("wd-message").value,
      http_method: $("wd-method").value,
      http_content_type: $("wd-ctype").value.trim(),
      http_body: $("wd-body").value,
      http_headers: $("wd-headers").value,
    };
  }

  /** Show the fields and help of the selected protocol. */
  function syncProtocol() {
    const proto = $("wd-protocol").value;
    const post = $("wd-method").value === "POST";
    document.querySelectorAll(".wd-only").forEach((el) => {
      el.hidden = el.dataset.protocol !== proto || (el.classList.contains("wd-post") && !post);
    });
    $("wd-url").placeholder = examples[proto] || "";
    $("wd-url-help").textContent = proto === "websocket"
      ? T("ws:// or wss:// (TLS), e.g. {0}", examples.websocket)
      : T("http:// or https:// (TLS), e.g. {0} · user:password@ in the URL = Basic authentication", examples.http);
    $("wd-interval-help").textContent = proto === "websocket"
      ? T("Time between two messages sent to the device (only with a message under Advanced).")
      : T("Time between two requests to the device.");
  }

  /** Remove the error marks of the device fields. */
  function clearFieldErrors() {
    root.querySelectorAll("[data-error-for]").forEach((box) => { box.textContent = ""; box.style.display = ""; });
    root.querySelectorAll(".is-invalid").forEach((el) => {
      if (!el.closest("#wd-table")) el.classList.remove("is-invalid");
    });
  }

  /** Show errors on the device fields ({field: message}). */
  function showFieldErrors(errors) {
    Object.entries(errors || {}).forEach(([field, message]) => {
      const box = root.querySelector(`[data-error-for="${field}"]`);
      if (!box) return;
      box.textContent = message;
      box.style.display = "block";
      const input = box.parentElement.querySelector("input:not([type=radio]), select, textarea");
      if (input) input.classList.add("is-invalid");
    });
  }

  async function post(url, body) {
    const resp = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await resp.json(); } catch (e) { data = { ok: false, error: "HTTP " + resp.status }; }
    data._status = resp.status;
    return data;
  }

  /* ---------- step 1 → 2: test ---------- */

  async function test() {
    clearFieldErrors();
    const s = settings();
    const button = $("wd-test");
    button.disabled = true;
    $("wd-test-status").textContent = T("connecting…");
    $("wd-process").hidden = true;
    try {
      const data = await post(root.dataset.testUrl, s);
      $("wd-result").hidden = false;
      if (data.errors) {
        showFieldErrors(data.errors);
        $("wd-result").hidden = true;
        $("wd-test-status").textContent = T("Correct the marked fields.");
        return;
      }
      $("wd-error").hidden = !!data.ok;
      $("wd-error").textContent = data.ok ? "" : (data.error || T("The test failed."));
      $("wd-summary").textContent = data.summary || "";
      $("wd-raw").textContent = data.raw || (data.ok ? "" : T("(no data received)"));
      $("wd-raw").hidden = !data.raw && !data.ok;
      rows = data.ok ? data.rows || [] : [];
      lastSettings = data.ok ? JSON.stringify(s) : "";
      $("wd-process-bar").hidden = !data.ok || !rows.length;
      $("wd-keys").textContent = data.ok
        ? (rows.length ? T("{0} key(s) with values found.", rows.length) + (data.truncated ? " " + T("(only the first ones are listed)") : "")
          : T("No values found in the data."))
        : "";
      $("wd-test-status").textContent = data.ok ? T("OK") : T("Failed");
      $("wd-result").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      $("wd-test-status").textContent = T("Failed: {0}", err.message);
    } finally {
      button.disabled = false;
    }
  }

  /* ---------- step 3: table ---------- */

  function cell(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  /** Build the key / tag table from the last test. */
  function process() {
    tbody.replaceChildren();
    rows.forEach((row, index) => {
      const tr = document.createElement("tr");
      tr.dataset.index = index;
      const existing = row.existing;
      if (existing) tr.classList.add("wd-existing");

      const tdCheck = cell("td", "text-center");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "form-check-input wd-check";
      check.checked = !existing;
      check.disabled = !!existing;
      check.setAttribute("aria-label", T("Create a tag for {0}", row.path));
      tdCheck.appendChild(check);

      const tdKey = cell("td", "wd-key");
      tdKey.appendChild(cell("code", "", row.path));

      const tdName = cell("td", "wd-name-cell");
      if (existing) {
        tdName.appendChild(cell("span", "badge hmi-badge-neutral", T("already a tag: {0}", existing)));
      } else {
        const input = document.createElement("input");
        input.className = "form-control form-control-sm font-monospace wd-name";
        input.value = row.name;
        input.maxLength = 100;
        input.spellcheck = false;
        input.setAttribute("aria-label", T("Tag name for {0}", row.path));
        input.addEventListener("input", () => { check.checked = true; scheduleCheck(); updateCount(); });
        tdName.appendChild(input);
        tdName.appendChild(cell("div", "invalid-feedback"));
      }

      const tdType = cell("td", "wd-type-cell");
      const select = document.createElement("select");
      select.className = "form-select form-select-sm wd-type";
      select.innerHTML = typeOptions;
      select.value = row.data_type;
      select.disabled = !!existing;
      select.setAttribute("aria-label", T("Data type for {0}", row.path));
      const len = document.createElement("input");
      len.type = "number";
      len.min = 1;
      len.max = maxString;
      len.className = "form-control form-control-sm wd-len";
      len.value = row.string_length || 40;
      len.title = T("STRING length");
      len.setAttribute("aria-label", T("STRING length"));
      len.hidden = row.data_type !== "STRING";
      len.disabled = !!existing;
      select.addEventListener("change", () => { len.hidden = select.value !== "STRING"; });
      if (!existing) tdType.append(select, len);

      const tdValue = cell("td", "wd-value font-monospace", row.display);
      tdValue.title = row.display;

      tr.append(tdCheck, tdKey, tdName, tdType, tdValue);
      tbody.appendChild(tr);
      check.addEventListener("change", updateCount);
    });
    $("wd-save-msg").hidden = true;
    $("wd-process").hidden = false;
    $("wd-filter").value = "";
    updateCount();
    scheduleCheck();
    $("wd-process").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** Rows that can still become tags. */
  const openRows = () => [...tbody.querySelectorAll("tr")].filter((tr) => !tr.classList.contains("wd-existing"));
  const selectedRows = () => openRows().filter((tr) => tr.querySelector(".wd-check").checked);

  function updateCount() {
    const open = openRows().length;
    const selected = selectedRows().length;
    $("wd-count").textContent = T("{0} of {1} selected", selected, open);
    $("wd-save-selected").disabled = selected === 0;
    $("wd-save-all").disabled = open === 0;
    $("wd-save-selected").querySelector("span").textContent = T("Save Selected") + (selected ? ` (${selected})` : "");
    const all = $("wd-check-all");
    all.checked = open > 0 && selected === open;
    all.indeterminate = selected > 0 && selected < open;
  }

  /** Show the problems of one row's name ([] = valid). */
  function markRow(tr, problems) {
    const input = tr.querySelector(".wd-name");
    if (!input) return;
    const box = tr.querySelector(".wd-name-cell .invalid-feedback");
    input.classList.toggle("is-invalid", problems.length > 0);
    input.setAttribute("aria-invalid", problems.length ? "true" : "false");
    box.replaceChildren(...problems.map((p) => cell("div", "", p)));
    box.style.display = problems.length ? "block" : "";
  }

  function scheduleCheck() {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(checkNames, 300);
  }

  /** Check every open name on the server (rules, uniqueness, duplicates in the table). */
  async function checkNames() {
    const trs = openRows();
    const mine = ++checkSeq;
    try {
      const data = await post(root.dataset.checkUrl, { names: trs.map((tr) => tr.querySelector(".wd-name").value) });
      if (mine !== checkSeq || !Array.isArray(data.problems)) return;
      trs.forEach((tr, i) => markRow(tr, data.problems[i] || []));
    } catch (e) { /* checked again when saving */ }
  }

  /* ---------- save ---------- */

  async function save(trs) {
    const msg = $("wd-save-msg");
    if (!trs.length) {
      msg.className = "alert alert-warning mt-3 mb-0";
      msg.textContent = T("Select at least one key.");
      msg.hidden = false;
      return;
    }
    if (!plcId && JSON.stringify(settings()) !== lastSettings) {
      msg.className = "alert alert-warning mt-3 mb-0";
      msg.textContent = T("The device settings changed after the test – click Test again first.");
      msg.hidden = false;
      return;
    }
    clearFieldErrors();
    trs.forEach((tr) => markRow(tr, []));
    const body = settings();
    body.rows = trs.map((tr) => {
      const row = rows[Number(tr.dataset.index)];
      const type = tr.querySelector(".wd-type").value;
      return { path: row.path, name: tr.querySelector(".wd-name").value.trim(), data_type: type,
               string_length: type === "STRING" ? Number(tr.querySelector(".wd-len").value) : 0 };
    });
    ["wd-save-selected", "wd-save-all"].forEach((id) => { $(id).disabled = true; });
    try {
      const data = await post(root.dataset.saveUrl, body);
      if (data.ok) {
        window.location.href = data.tags_url;
        return;
      }
      showFieldErrors(data.errors);
      let first = null;
      Object.entries(data.rows || {}).forEach(([i, problems]) => {
        const tr = trs[Number(i)];
        if (!tr) return;
        markRow(tr, problems);
        if (!first) first = tr;
      });
      msg.className = "alert alert-danger mt-3 mb-0";
      msg.textContent = data.error || T("Nothing was saved.");
      msg.hidden = false;
      if (first) {
        first.hidden = false;
        first.scrollIntoView({ behavior: "smooth", block: "center" });
        const input = first.querySelector(".wd-name");
        if (input) input.focus();
      } else if (data.errors && Object.keys(data.errors).length) {
        $("wd-step1").scrollIntoView({ behavior: "smooth" });
      }
    } catch (err) {
      msg.className = "alert alert-danger mt-3 mb-0";
      msg.textContent = T("Save failed: {0}", err.message);
      msg.hidden = false;
    } finally {
      updateCount();
    }
  }

  /* ---------- events ---------- */

  $("wd-protocol").addEventListener("change", syncProtocol);
  $("wd-method").addEventListener("change", syncProtocol);
  $("wd-test").addEventListener("click", test);
  $("wd-url").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); test(); } });
  $("wd-process-btn").addEventListener("click", process);
  $("wd-save-selected").addEventListener("click", () => save(selectedRows()));
  $("wd-save-all").addEventListener("click", () => save(openRows()));
  $("wd-select-all").addEventListener("click", () => {
    openRows().forEach((tr) => { if (!tr.hidden) tr.querySelector(".wd-check").checked = true; });
    updateCount();
  });
  $("wd-select-none").addEventListener("click", () => {
    openRows().forEach((tr) => { tr.querySelector(".wd-check").checked = false; });
    updateCount();
  });
  $("wd-check-all").addEventListener("change", (ev) => {
    openRows().forEach((tr) => { if (!tr.hidden) tr.querySelector(".wd-check").checked = ev.target.checked; });
    updateCount();
  });
  $("wd-filter").addEventListener("input", () => {
    const q = $("wd-filter").value.toLowerCase();
    tbody.querySelectorAll("tr").forEach((tr) => {
      const name = tr.querySelector(".wd-name");
      const text = tr.textContent + " " + (name ? name.value : "");
      tr.hidden = q && !text.toLowerCase().includes(q);
    });
  });
  syncProtocol();
})();
