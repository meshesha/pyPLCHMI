/**
 * Data point form helpers: shows the STRING length field only for STRING,
 * switches the address help between Siemens S7, Modbus, BACnet and web device syntax according to
 * the selected PLC (and hides the address for Dummy PLCs), and implements the "Test read" button
 * (POST /api/plcs/<id>/read).
 */
(function () {
  "use strict";

  const dtype = document.getElementById("data_type");
  const lenRow = document.getElementById("string-length-row");
  const testBtn = document.getElementById("test-read");
  const result = document.getElementById("test-result");
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

  /** Show the STRING length input only when relevant. */
  function toggleLength() {
    const addr = document.getElementById("address").value.toUpperCase();
    lenRow.style.display = dtype.value === "STRING" || addr.includes(":STRING") ? "" : "none";
  }

  /** Read the configured address once and show the value or error. */
  async function testRead() {
    const plcId = document.getElementById("plc_id").value;
    const url = testBtn.dataset.urlTemplate.replace("/0/", "/" + encodeURIComponent(plcId) + "/");
    result.className = "small text-secondary";
    result.textContent = T("reading…");
    try {
      const resp = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({
          address: document.getElementById("address").value,
          data_type: dtype.value,
          string_length: parseInt(document.getElementById("string_length").value, 10) || null,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
      result.className = "small text-success";
      result.textContent = data.address + " = " + JSON.stringify(data.value);
    } catch (err) {
      result.className = "small text-danger";
      result.textContent = err.message;
    }
  }

  const protocols = JSON.parse((document.getElementById("plc-protocols") || { textContent: "{}" }).textContent);
  const plcSelect = document.getElementById("plc_id");
  const addressInput = document.getElementById("address");
  const HINTS = {
    s7: T("e.g. {0}", "DB1.DBD4, DB1.DBX0.0, M10.5, MW20, IW64"),
    modbus: T("e.g. {0}", "HR100, HR100:REAL, HR10.3, IR5, C12, DI3, 40001"),
    bacnet: T("e.g. {0}", "AV3, AI1, BO12, MV2, AI1.units, AV5@10"),
    web: T("e.g. {0}", "meter.power, phases[0], status/sensor[2]/temp"),
  };

  const addressBox = addressInput.closest(".mb-3");
  const dummyNote = document.createElement("div");
  dummyNote.className = "alert alert-secondary small py-2";
  dummyNote.id = "dummy-note";
  dummyNote.textContent = T("Dummy PLC: this tag needs no address. It starts at 0 / OFF / empty. A general dummy tag keeps one value for everybody (stored in the database); a local one has its own value in each browser window. To connect it to a real PLC later, edit the tag, choose that PLC and enter the address.");
  dummyNote.hidden = true;
  addressBox.parentNode.insertBefore(dummyNote, addressBox.parentNode.firstChild);

  /** Show the address syntax and placeholder of the selected PLC's protocol (no address for dummy PLCs). */
  function syncProtocol() {
    const raw = protocols[plcSelect.value] || "s7";
    const dummy = raw === "dummy";
    const proto = raw.startsWith("modbus") ? "modbus" : (raw === "bacnet_ip" ? "bacnet"
      : (raw === "http" || raw === "websocket" ? "web" : "s7"));
    addressInput.placeholder = HINTS[proto];
    addressBox.hidden = dummy;
    dummyNote.hidden = !dummy;
    const scopeRow = document.getElementById("dummy-scope-row");
    if (scopeRow) scopeRow.hidden = !dummy;
    testBtn.parentElement.classList.toggle("d-none", dummy);
    const help = document.querySelector("details.small");
    if (help) help.hidden = dummy;
    document.querySelectorAll(".syntax-table").forEach((t) => { t.hidden = t.dataset.syntax !== proto; });
  }

  /** History settings: interval only when logging is on; local dummy tags cannot be logged. */
  function syncHistory() {
    const logBox = document.getElementById("log_enabled");
    const interval = document.getElementById("log_interval");
    if (!logBox || !interval) return;
    const scope = document.getElementById("dummy_scope");
    const local = (protocols[plcSelect.value] || "") === "dummy" && scope && scope.value === "local";
    const note = document.getElementById("log-local-note");
    if (note) note.hidden = !local;
    interval.disabled = !logBox.checked;
  }
  ["log_enabled", "dummy_scope"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", syncHistory);
  });
  plcSelect.addEventListener("change", syncHistory);
  syncHistory();

  plcSelect.addEventListener("change", syncProtocol);
  syncProtocol();
  dtype.addEventListener("change", toggleLength);
  document.getElementById("address").addEventListener("input", toggleLength);
  testBtn.addEventListener("click", testRead);
  toggleLength();
})();
