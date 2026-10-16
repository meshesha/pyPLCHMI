/**
 * Trend chart editor: the list of series (tag, colour, axis).
 *
 * The rows are built from the hidden ``series`` field (JSON) and written back
 * to it before the form is submitted.  Only tags with history logging are
 * offered (``#chart-tags``); each new row gets the next unused palette colour.
 */
(function () {
  "use strict";

  const form = document.getElementById("chart-form");
  if (!form) return;
  const read = (id) => JSON.parse(document.getElementById(id).textContent);
  const tags = read("chart-tags");
  const palette = read("chart-palette");
  const axes = read("chart-axes");
  const maxSeries = read("chart-max");
  const hidden = form.querySelector("input[name=series]");
  const body = document.querySelector("#series-table tbody");
  const addBtn = document.getElementById("add-series");

  /**
   * Create an <option>.
   * @param {string|number} value Value.
   * @param {string} text Text.
   * @returns {HTMLOptionElement}
   */
  function option(value, text) {
    const o = document.createElement("option");
    o.value = String(value);
    o.textContent = text;
    return o;
  }

  /** Colours already used by the rows. */
  function usedColors() {
    return Array.from(body.querySelectorAll("input[type=color]")).map((i) => i.value.toLowerCase());
  }

  /** A palette colour not used yet (or the next one in turn). */
  function nextColor() {
    const used = usedColors();
    return palette.find((c) => !used.includes(c.toLowerCase())) || palette[body.rows.length % palette.length];
  }

  /** Enable/disable the "Add tag" button and mark tags chosen twice. */
  function refresh() {
    if (addBtn) addBtn.disabled = body.rows.length >= maxSeries;
    const chosen = Array.from(body.querySelectorAll("select.series-tag")).map((s) => s.value);
    body.querySelectorAll("select.series-tag").forEach((s) => {
      s.classList.toggle("is-invalid", Boolean(s.value) && chosen.filter((v) => v === s.value).length > 1);
    });
  }

  /**
   * Add a row.
   * @param {{tag_id?:number, color?:string, axis?:string}} [item] Initial values.
   */
  function addRow(item) {
    const it = item || {};
    const tr = body.insertRow();
    const tagCell = tr.insertCell();
    const select = document.createElement("select");
    select.className = "form-select form-select-sm series-tag";
    select.setAttribute("aria-label", T("Tag"));
    select.appendChild(option("", T("— choose a tag —")));
    tags.forEach((t) => {
      const extra = [t.plc, t.unit].filter(Boolean).join(" · ");
      select.appendChild(option(t.id, t.label + (extra ? "  (" + extra + ")" : "")));
    });
    if (it.tag_id !== undefined && !tags.some((t) => t.id === it.tag_id)) {
      select.appendChild(option(it.tag_id, T("tag #{0} (no history logging)", it.tag_id)));
    }
    select.value = it.tag_id !== undefined ? String(it.tag_id) : "";
    tagCell.appendChild(select);

    const colorCell = tr.insertCell();
    const color = document.createElement("input");
    color.type = "color";
    color.className = "form-control form-control-sm form-control-color";
    color.value = it.color || nextColor();
    color.setAttribute("aria-label", T("Colour"));
    colorCell.appendChild(color);

    const axisCell = tr.insertCell();
    const axis = document.createElement("select");
    axis.className = "form-select form-select-sm series-axis";
    axis.setAttribute("aria-label", T("Axis"));
    Object.entries(axes).forEach(([k, v]) => axis.appendChild(option(k, T(v))));
    axis.value = it.axis || "left";
    axisCell.appendChild(axis);

    const delCell = tr.insertCell();
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn btn-sm btn-outline-danger";
    del.title = T("Remove");
    del.setAttribute("aria-label", T("Remove"));
    del.innerHTML = '<i class="bi bi-x-lg"></i>';
    del.addEventListener("click", () => { tr.remove(); refresh(); });
    delCell.appendChild(del);
    select.addEventListener("change", refresh);
    refresh();
  }

  let initial = [];
  try { initial = JSON.parse(hidden.value || "[]"); } catch (e) { initial = []; }
  initial.forEach(addRow);
  if (!initial.length && tags.length) addRow();
  if (addBtn) addBtn.addEventListener("click", () => addRow());

  form.addEventListener("submit", () => {
    const rows = Array.from(body.rows).map((tr) => ({
      tag_id: Number(tr.querySelector("select.series-tag").value) || null,
      color: tr.querySelector("input[type=color]").value,
      axis: tr.querySelector("select.series-axis").value,
    }));
    hidden.value = JSON.stringify(rows);
  });
})();
