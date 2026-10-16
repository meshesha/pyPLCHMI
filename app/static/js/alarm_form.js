/**
 * Alarm form helpers.
 *
 * - For BOOL tags only "=" and "≠" make sense: the other operators are
 *   disabled and the value field offers true/false.
 * - A one-line preview shows the resulting condition, e.g. "Tank level > 80".
 * - The "Trigger" select shows either the tag fields or the PLC field
 *   (communication alarm).
 * The server validates everything again (app/alarms/forms.py).
 */
(function () {
  "use strict";

  const kinds = JSON.parse(document.getElementById("tag-kinds").textContent);
  const tag = document.getElementById("datapoint_id");
  const op = document.getElementById("operator");
  const setpoint = document.getElementById("setpoint");
  const preview = document.getElementById("condition-preview");

  /** Enable/disable operators and set hints according to the tag's type. */
  function syncKind() {
    const kind = kinds[tag.value] || "number";
    const isBool = kind === "bool";
    Array.from(op.options).forEach((o) => { o.disabled = isBool && o.value !== "eq" && o.value !== "ne"; });
    if (isBool && op.selectedOptions[0] && op.selectedOptions[0].disabled) op.value = "eq";
    setpoint.placeholder = isBool ? "true or false" : (kind === "text" ? "text to compare" : "e.g. 80");
    setpoint.setAttribute("list", isBool ? "bool-values" : "");
    updatePreview();
  }

  /** Show "<tag label> <operator> <value>" under the fields. */
  function updatePreview() {
    if (!tag.value || tag.value === "0" || !setpoint.value.trim()) {
      preview.hidden = true;
      return;
    }
    const label = tag.selectedOptions[0].textContent.split(" — ")[0];
    const sym = op.selectedOptions[0].textContent.trim().split(/\s+/)[0];
    preview.textContent = T("Active while: {0}", label + " " + sym + " " + setpoint.value.trim());
    preview.hidden = false;
  }

  const list = document.createElement("datalist");
  list.id = "bool-values";
  ["true", "false"].forEach((v) => { const o = document.createElement("option"); o.value = v; list.append(o); });
  document.body.append(list);

  /** Show the fields of the selected trigger (tag condition or communication). */
  const trigger = document.getElementById("kind");
  function syncTrigger() {
    document.querySelectorAll(".kind-group").forEach((g) => { g.hidden = g.dataset.kind !== trigger.value; });
  }
  trigger.addEventListener("change", syncTrigger);
  syncTrigger();

  tag.addEventListener("change", syncKind);
  op.addEventListener("change", updatePreview);
  setpoint.addEventListener("input", updatePreview);
  syncKind();
})();
