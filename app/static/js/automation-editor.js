/**
 * Automation editor (templates/automations/edit.html) – "When / And if / Then do".
 *
 * The page embeds the automation document, the block descriptions (schema from
 * app/automations/schema.py) and the tags / alarms / PLCs / automations to pick
 * from.  Every block is a card built from its description; nested blocks
 * (And/Or/Not, If-then-else, Repeat, Wait until) contain their own lists.
 *
 * Typing updates the document in place (no re-render, so the focus stays);
 * adding, moving, deleting or changing the type of a block re-renders.
 * "Edit in YAML" converts the document on the server and back; Save posts the
 * document (or the YAML text) to /automations/save and shows the server's
 * validation errors.
 */
(function () {
  "use strict";

  const root = document.getElementById("automation-editor");
  if (!root) return;
  const data = JSON.parse(document.getElementById("auto-data").textContent);
  const S = data.schema;
  const R = data.refs;
  const doc = data.doc;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const canApprove = root.dataset.canApprove === "1";
  let automationId = root.dataset.automationId ? parseInt(root.dataset.automationId, 10) : null;
  let yamlMode = false;
  let dirty = false;
  let uid = 0;

  const KIND_OF_LIST = { triggers: "triggers", conditions: "conditions", actions: "actions" };
  const ADD_LABEL = { triggers: T("Add trigger"), conditions: T("Add condition"), actions: T("Add action") };

  // ------------------------------------------------------------------ helpers
  /**
   * Create an element.
   * @param {string} tag Tag name.
   * @param {string} [cls] Class names.
   * @param {string} [text] Text content.
   * @returns {HTMLElement}
   */
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  /** Mark the automation as changed. */
  function touch() {
    dirty = true;
    document.getElementById("auto-dirty").hidden = false;
  }

  /**
   * POST JSON.
   * @param {string} url URL.
   * @param {object} body Payload.
   * @returns {Promise<{ok:boolean, status:number, data:object}>}
   */
  async function post(url, body) {
    const resp = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, Accept: "application/json" },
      body: JSON.stringify(body),
    });
    const json = await resp.json().catch(() => ({}));
    return { ok: resp.ok && json.ok !== false, status: resp.status, data: json };
  }

  /** Seconds → "HH:MM:SS". */
  function fmtDuration(sec) {
    if (sec === undefined || sec === null || sec === "") return "";
    const s = Math.round(Number(sec));
    const p = (n) => String(n).padStart(2, "0");
    return p(Math.floor(s / 3600)) + ":" + p(Math.floor((s % 3600) / 60)) + ":" + p(s % 60);
  }

  /** Name of a referenced object. */
  function refName(kind, id) {
    const list = kind === "tag" || kind === "tag_writable" ? R.tags : R[kind + "s"] || [];
    const item = list.find((x) => x.id === Number(id));
    return item ? item.label : (id ? "#" + id : "");
  }

  /** Short summary of a block for its card header. */
  function summary(kind, block) {
    const spec = S[kind][block.type];
    if (!spec) return "";
    const parts = [];
    spec.fields.forEach((f) => {
      const v = block[f.key];
      if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) return;
      if (f.kind === "conditions" || f.kind === "actions") { parts.push(f.label + ": " + v.length); return; }
      if (f.kind === "calc") { const ls = String(v).split("\n").filter((x) => x.trim()); parts.push(ls[0] + (ls.length > 1 ? " (+" + (ls.length - 1) + ")" : "")); return; }
      if (["tag", "tag_writable", "alarm", "plc", "automation"].includes(f.kind)) { parts.push(refName(f.kind === "tag_writable" ? "tag" : f.kind, v)); return; }
      if (f.kind === "duration") { parts.push(f.label + " " + fmtDuration(v)); return; }
      if (f.kind === "select") { const o = (f.options || []).find((x) => x[0] === v); parts.push(o ? o[1] : v); return; }
      if (f.kind === "weekdays") { parts.push(v.map((d) => (S.weekdays.find((w) => w[0] === d) || [d, d])[1]).join(" ")); return; }
      if (f.kind === "bool") { if (v) parts.push(f.label); return; }
      if (f.key === "above") { parts.push("> " + v); return; }
      if (f.key === "below") { parts.push("< " + v); return; }
      parts.push((["to", "from", "state", "value"].includes(f.key) ? f.label.split(" (")[0] + " " : "") + v);
    });
    return parts.join(" · ");
  }

  // ------------------------------------------------------------------ field widgets
  /**
   * Options for a tag picker, grouped by PLC.
   * @param {HTMLSelectElement} select Target.
   * @param {boolean} writableOnly Only writable, non-local tags.
   * @param {*} current Selected id.
   */
  function fillTags(select, writableOnly, current, numericOnly) {
    select.append(new Option("—", ""));
    const groups = {};
    R.tags.forEach((t) => {
      if (t.local) return;
      if (writableOnly && !t.writable) return;
      if (numericOnly && !t.numeric) return;
      (groups[t.plc] = groups[t.plc] || []).push(t);
    });
    Object.keys(groups).sort().forEach((plc) => {
      const g = document.createElement("optgroup");
      g.label = plc || "—";
      groups[plc].forEach((t) => g.append(new Option(t.label + (t.unit ? " [" + t.unit + "]" : "") + " · " + t.type, t.id)));
      select.append(g);
    });
    select.value = current === undefined || current === null ? "" : String(current);
  }

  /**
   * Build the input for one field of a block.
   * @param {string} kind Block kind.
   * @param {object} block The block (edited in place).
   * @param {object} f Field description.
   * @param {Function} refresh Updates the card summary.
   * @returns {HTMLElement}
   */
  function fieldWidget(kind, block, f, refresh) {
    const id = "f" + (++uid);
    const wrap = el("div", "hmi-auto-field" + (["conditions", "actions"].includes(f.kind) ? " nested" : ""));
    const label = el("label", "form-label small mb-1", f.label + (f.required ? " *" : ""));
    label.htmlFor = id;
    const set = (v) => {
      if (v === "" || v === null || v === undefined || (Array.isArray(v) && !v.length)) delete block[f.key];
      else block[f.key] = v;
      touch();
      refresh();
    };
    let input;
    switch (f.kind) {
      case "conditions":
      case "actions": {
        const h = el("div", "hmi-auto-nested-title", f.label);
        wrap.append(h);
        block[f.key] = block[f.key] || [];
        wrap.append(renderList(f.kind, block[f.key], refresh));
        return wrap;
      }
      case "tag":
      case "tag_writable":
        input = el("select", "form-select form-select-sm");
        fillTags(input, f.kind === "tag_writable", block[f.key], Boolean(f.numeric));
        input.addEventListener("change", () => { set(input.value ? Number(input.value) : ""); updateValueHints(); });
        break;
      case "alarm":
      case "plc":
      case "automation": {
        input = el("select", "form-select form-select-sm");
        input.append(new Option(f.required ? "—" : T("(any)"), ""));
        (R[f.kind + "s"] || []).forEach((x) => { if (!(f.kind === "automation" && x.id === automationId)) input.append(new Option(x.label, x.id)); });
        input.value = block[f.key] ? String(block[f.key]) : "";
        input.addEventListener("change", () => set(input.value ? Number(input.value) : ""));
        break;
      }
      case "select":
        input = el("select", "form-select form-select-sm");
        if (!f.required) input.append(new Option(T("(any)"), ""));
        (f.options || []).forEach((o) => input.append(new Option(o[1], o[0])));
        if (block[f.key] === undefined && f.required && f.default) block[f.key] = f.default;
        input.value = block[f.key] || "";
        input.addEventListener("change", () => set(input.value));
        break;
      case "number":
        input = el("input", "form-control form-control-sm");
        input.type = "number";
        input.step = "any";
        if (f.min !== undefined) input.min = f.min;
        if (f.max !== undefined) input.max = f.max;
        input.value = block[f.key] === undefined ? "" : block[f.key];
        input.addEventListener("input", () => set(input.value === "" ? "" : Number(input.value)));
        break;
      case "duration": {
        input = el("div", "input-group input-group-sm hmi-auto-duration");
        const total = Number(block[f.key] || 0);
        const parts = [["h", Math.floor(total / 3600)], ["m", Math.floor((total % 3600) / 60)], ["s", +(total % 60).toFixed(3)]];
        const boxes = parts.map(([unit, val], i) => {
          const b = el("input", "form-control");
          b.type = "number";
          b.min = 0;
          b.step = unit === "s" ? "any" : 1;
          b.value = block[f.key] === undefined ? "" : val;
          b.placeholder = "0";
          b.setAttribute("aria-label", f.label + " (" + { h: T("hours"), m: T("minutes"), s: T("seconds") }[unit] + ")");
          if (i === 0) b.id = id;
          input.append(b, el("span", "input-group-text", unit));
          return b;
        });
        const update = () => {
          const empty = boxes.every((b) => b.value === "");
          const sec = boxes.reduce((acc, b, i) => acc + (Number(b.value) || 0) * [3600, 60, 1][i], 0);
          set(empty ? "" : sec);
        };
        boxes.forEach((b) => b.addEventListener("input", update));
        wrap.append(label, input);
        return wrap;
      }
      case "time":
        input = el("input", "form-control form-control-sm");
        input.type = "time";
        input.step = 1;
        input.value = block[f.key] || "";
        input.addEventListener("input", () => set(input.value ? (input.value.length === 5 ? input.value + ":00" : input.value) : ""));
        break;
      case "weekdays": {
        input = el("div", "hmi-auto-days");
        input.setAttribute("role", "group");
        input.setAttribute("aria-label", f.label);
        const chosen = new Set(block[f.key] || []);
        S.weekdays.forEach(([code, name]) => {
          const cid = id + code;
          const cb = el("input", "btn-check");
          cb.type = "checkbox";
          cb.id = cid;
          cb.checked = chosen.has(code);
          const lb = el("label", "btn btn-sm btn-outline-secondary", name);
          lb.htmlFor = cid;
          cb.addEventListener("change", () => {
            if (cb.checked) chosen.add(code); else chosen.delete(code);
            set(S.weekdays.map((w) => w[0]).filter((d) => chosen.has(d)));
          });
          input.append(cb, lb);
        });
        wrap.append(el("div", "form-label small mb-1", f.label), input);
        return wrap;
      }
      case "bool": {
        const sw = el("div", "form-check form-switch");
        input = el("input", "form-check-input");
        input.type = "checkbox";
        input.id = id;
        input.checked = Boolean(block[f.key]);
        input.addEventListener("change", () => set(input.checked ? true : ""));
        const lb = el("label", "form-check-label", f.label);
        lb.htmlFor = id;
        sw.append(input, lb);
        wrap.append(sw);
        return wrap;
      }
      case "calc": {
        // Calculation text + "Insert tag" picker (inserts [Label], or [#id] for labels used twice).
        wrap.className += " nested";
        input = el("textarea", "form-control form-control-sm hmi-auto-calc");
        input.rows = Math.max(3, String(block[f.key] || "").split("\n").length + 1);
        input.dir = "ltr";
        input.spellcheck = false;
        input.placeholder = "tag_1 = tag_1 + 1\n[Flow total] = [Flow 1] * [Factor]";
        input.value = block[f.key] || "";
        input.addEventListener("input", () => set(input.value));
        const pick = el("select", "form-select form-select-sm hmi-auto-calc-insert");
        pick.setAttribute("aria-label", T("Insert tag"));
        pick.append(new Option(T("Insert tag…"), ""));
        const counts = {};
        R.tags.forEach((t) => { counts[t.label] = (counts[t.label] || 0) + 1; });
        R.tags.filter((t) => !t.local && (t.numeric || t.bool)).forEach((t) => {
          const ref = counts[t.label] > 1 ? "[#" + t.id + "]" : "[" + t.label + "]";
          pick.append(new Option(t.label + " · " + t.plc + " · " + t.type + (t.writable ? "" : " · " + T("read only")), ref));
        });
        pick.addEventListener("change", () => {
          if (!pick.value) return;
          const start = input.selectionStart ?? input.value.length;
          const end = input.selectionEnd ?? start;
          input.value = input.value.slice(0, start) + pick.value + input.value.slice(end);
          input.focus();
          input.selectionStart = input.selectionEnd = start + pick.value.length;
          pick.value = "";
          set(input.value);
        });
        input.id = id;
        const bar = el("div", "d-flex flex-wrap gap-2 align-items-center mb-1");
        bar.append(label, pick);
        label.classList.add("mb-0", "me-auto");
        wrap.append(bar, input);
        return wrap;
      }
      case "textarea":
        input = el("textarea", "form-control form-control-sm");
        input.rows = 2;
        input.value = block[f.key] || "";
        input.addEventListener("input", () => set(input.value));
        break;
      default:
        input = el("input", "form-control form-control-sm");
        input.value = block[f.key] === undefined ? "" : block[f.key];
        input.dataset.valueOf = ["to", "from", "state", "value"].includes(f.key) ? "1" : "";
        input.addEventListener("input", () => set(input.value));
    }
    input.id = input.id || id;
    wrap.append(label, input);

    /** ON/OFF suggestions for value fields of BOOL tags. */
    function updateValueHints() {
      wrap.parentElement && wrap.parentElement.querySelectorAll("input[data-value-of='1']").forEach((box) => {
        const tag = R.tags.find((t) => t.id === Number(block.tag));
        const listId = box.id + "-dl";
        let dl = document.getElementById(listId);
        if (!dl) { dl = el("datalist"); dl.id = listId; box.after(dl); box.setAttribute("list", listId); }
        dl.replaceChildren();
        if (tag && tag.bool) [tag.true_text, tag.false_text].forEach((v) => dl.append(new Option(v, v)));
      });
    }
    if (f.key === "tag") setTimeout(updateValueHints, 0);
    return wrap;
  }

  // ------------------------------------------------------------------ cards and lists
  /**
   * One block card.
   * @param {string} kind triggers | conditions | actions.
   * @param {object[]} list The list holding the block.
   * @param {number} index Position in the list.
   * @param {Function} rerender Re-renders the list.
   * @returns {HTMLElement}
   */
  function card(kind, list, index, rerender) {
    const block = list[index];
    const spec = S[kind][block.type] || { label: block.type, icon: "question", help: "", fields: [] };
    const box = el("article", "hmi-auto-card" + (block.enabled === false ? " disabled" : ""));
    box.dataset.type = block.type;
    const head = el("div", "hmi-auto-card-head");
    const toggle = el("button", "hmi-auto-card-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", block.__open === false ? "false" : "true");
    const icon = el("i", "bi bi-" + spec.icon);
    icon.setAttribute("aria-hidden", "true");
    const title = el("span", "hmi-auto-card-title", block.alias || spec.label);
    const sum = el("span", "hmi-auto-card-sum");
    const refresh = () => { sum.textContent = summary(kind, block); title.textContent = block.alias || spec.label; };
    toggle.append(icon, title, sum);
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      body.hidden = !open;
      Object.defineProperty(block, "__open", { value: open, enumerable: false, writable: true, configurable: true });
    });
    if (block.enabled === false) head.append(el("span", "badge text-bg-secondary me-2", T("disabled")));

    const menu = el("div", "dropdown");
    const mbtn = el("button", "btn btn-sm btn-link text-reset");
    mbtn.type = "button";
    mbtn.dataset.bsToggle = "dropdown";
    mbtn.setAttribute("aria-label", T("More"));
    mbtn.append(el("i", "bi bi-three-dots-vertical"));
    const ul = el("ul", "dropdown-menu dropdown-menu-end");
    const item = (text, iconName, fn, danger) => {
      const li = el("li");
      const b = el("button", "dropdown-item" + (danger ? " text-danger" : ""));
      b.type = "button";
      b.append(el("i", "bi bi-" + iconName + " me-2"), document.createTextNode(text));
      b.addEventListener("click", fn);
      li.append(b);
      ul.append(li);
    };
    item(T("Rename"), "input-cursor-text", async () => {
      const name = await window.HMIDialog.prompt({ title: T("Rename"), value: block.alias || "", placeholder: spec.label });
      if (name === undefined) return;
      if (name.trim()) block.alias = name.trim(); else delete block.alias;
      touch();
      refresh();
    });
    if (index > 0) item(T("Move up"), "arrow-up", () => { list.splice(index - 1, 0, list.splice(index, 1)[0]); touch(); rerender(); });
    if (index < list.length - 1) item(T("Move down"), "arrow-down", () => { list.splice(index + 1, 0, list.splice(index, 1)[0]); touch(); rerender(); });
    item(T("Duplicate"), "copy", () => { list.splice(index + 1, 0, JSON.parse(JSON.stringify(block))); touch(); rerender(); });
    item(block.enabled === false ? T("Enable") : T("Disable"), block.enabled === false ? "toggle-on" : "toggle-off", () => {
      if (block.enabled === false) delete block.enabled; else block.enabled = false;
      touch();
      rerender();
    });
    const sep = el("li");
    sep.append(el("hr", "dropdown-divider"));
    ul.append(sep);
    item(T("Delete"), "trash", () => { list.splice(index, 1); touch(); rerender(); }, true);
    menu.append(mbtn, ul);
    head.append(toggle, menu);

    const body = el("div", "hmi-auto-card-body");
    body.hidden = block.__open === false;
    if (spec.help) body.append(el("p", "small text-secondary mb-2", spec.help));
    const grid = el("div", "hmi-auto-fields");
    spec.fields.forEach((f) => grid.append(fieldWidget(kind, block, f, refresh)));
    if (!spec.fields.length) grid.append(el("p", "small mb-0", T("No settings.")));
    body.append(grid);
    refresh();
    box.append(head, body);
    return box;
  }

  /**
   * A list of blocks with its "Add …" menu.
   * @param {string} kind triggers | conditions | actions.
   * @param {object[]} list Blocks (edited in place).
   * @param {Function} [parentRefresh] Updates the summary of the parent card.
   * @returns {HTMLElement}
   */
  function renderList(kind, list, parentRefresh) {
    const box = el("div", "hmi-auto-items");
    const draw = () => {
      box.replaceChildren();
      list.forEach((_b, i) => box.append(card(kind, list, i, draw)));
      box.append(addMenu(kind, list, draw));
      if (parentRefresh) parentRefresh();
    };
    draw();
    return box;
  }

  /** "+ Add trigger/condition/action" with the list of types. */
  function addMenu(kind, list, rerender) {
    const dd = el("div", "dropdown hmi-auto-add");
    const btn = el("button", "btn btn-primary rounded-pill");
    btn.type = "button";
    btn.dataset.bsToggle = "dropdown";
    btn.append(el("i", "bi bi-plus-lg me-1"), document.createTextNode(ADD_LABEL[kind]));
    const ul = el("ul", "dropdown-menu hmi-auto-type-menu");
    Object.entries(S[kind]).forEach(([type, spec]) => {
      const li = el("li");
      const b = el("button", "dropdown-item");
      b.type = "button";
      b.dataset.type = type;
      const i = el("i", "bi bi-" + spec.icon + " me-2");
      const txt = el("span", "", spec.label);
      const help = el("div", "small text-secondary text-wrap", spec.help);
      b.append(i, txt, help);
      b.addEventListener("click", () => {
        const block = { type: type };
        spec.fields.forEach((f) => { if (f.required && f.default !== undefined) block[f.key] = f.default; });
        list.push(block);
        touch();
        rerender();
      });
      li.append(b);
      ul.append(li);
    });
    dd.append(btn, ul);
    return dd;
  }

  /** Draw the three sections. */
  function renderAll() {
    ["triggers", "conditions", "actions"].forEach((kind) => {
      doc[kind] = doc[kind] || [];
      const host = root.querySelector(`[data-list="${kind}"]`);
      host.replaceChildren(renderList(KIND_OF_LIST[kind], doc[kind]));
    });
  }

  // ------------------------------------------------------------------ settings
  const f = {
    name: document.getElementById("auto-name"),
    desc: document.getElementById("auto-desc"),
    mode: document.getElementById("auto-mode"),
    max: document.getElementById("auto-max"),
    maxWrap: document.getElementById("auto-max-wrap"),
    enabled: document.getElementById("auto-enabled"),
    allow: document.getElementById("auto-allow-writes"),
  };

  /** Copy the settings of the document into the inputs. */
  function showSettings() {
    f.name.value = doc.name || "";
    f.desc.value = doc.description || "";
    f.mode.value = doc.mode || "single";
    f.max.value = doc.max_runs || 10;
    f.enabled.checked = doc.enabled !== false;
    f.maxWrap.hidden = !["queued", "parallel"].includes(f.mode.value);
  }
  f.name.addEventListener("input", () => { doc.name = f.name.value; document.getElementById("auto-title").textContent = f.name.value || T("New automation"); touch(); });
  f.desc.addEventListener("input", () => { doc.description = f.desc.value; touch(); });
  f.mode.addEventListener("change", () => { doc.mode = f.mode.value; f.maxWrap.hidden = !["queued", "parallel"].includes(doc.mode); touch(); });
  f.max.addEventListener("input", () => { doc.max_runs = Number(f.max.value) || 10; touch(); });
  f.enabled.addEventListener("change", () => { doc.enabled = f.enabled.checked; touch(); });
  if (f.allow) f.allow.addEventListener("change", touch);

  // ------------------------------------------------------------------ YAML mode
  const yamlBox = document.getElementById("auto-yaml-text");
  const modeBtn = document.getElementById("auto-mode-toggle");

  /** Copy the settings inputs into the document (before converting or saving). */
  function readSettings() {
    doc.name = f.name.value.trim();
    doc.description = f.desc.value;
    doc.mode = f.mode.value;
    doc.max_runs = Number(f.max.value) || 10;
    doc.enabled = f.enabled.checked;
  }

  /** Switch between the visual editor and the YAML text. */
  async function toggleMode() {
    hideErrors();
    if (!yamlMode) {
      readSettings();
      const res = await post(root.dataset.toYamlUrl, { doc: clean(doc) });
      yamlBox.value = res.data.yaml || "";
      setMode(true);
    } else {
      const res = await post(root.dataset.fromYamlUrl, { yaml: yamlBox.value, id: automationId });
      if (!res.ok) { showErrors(res.data.errors || [T("The YAML could not be read.")]); return; }
      Object.keys(doc).forEach((k) => delete doc[k]);
      Object.assign(doc, res.data.doc);
      showSettings();
      renderAll();
      setMode(false);
    }
  }

  /** Show one of the two editors. */
  function setMode(yaml) {
    yamlMode = yaml;
    document.getElementById("auto-visual").hidden = yaml;
    document.getElementById("auto-yaml").hidden = !yaml;
    root.querySelector("[aria-labelledby='auto-settings-title']").hidden = yaml;
    modeBtn.querySelector("span").textContent = yaml ? T("Edit in visual editor") : T("Edit in YAML");
    modeBtn.querySelector("i").className = yaml ? "bi bi-ui-checks" : "bi bi-code-slash";
  }
  modeBtn.addEventListener("click", toggleMode);
  yamlBox.addEventListener("input", touch);

  /** Copy of the document without editor-only state. */
  function clean(value) {
    return JSON.parse(JSON.stringify(value));
  }

  // ------------------------------------------------------------------ errors
  function showErrors(list) {
    const box = document.getElementById("auto-errors");
    const ul = document.getElementById("auto-error-list");
    ul.replaceChildren(...list.map((e) => el("li", "", e)));
    box.hidden = false;
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function hideErrors() { document.getElementById("auto-errors").hidden = true; }

  // ------------------------------------------------------------------ save / run
  async function save() {
    hideErrors();
    const body = { id: automationId };
    if (yamlMode) body.yaml = yamlBox.value;
    else { readSettings(); body.doc = clean(doc); }
    if (canApprove && f.allow) body.allow_writes = f.allow.checked;
    const btn = document.getElementById("auto-save");
    btn.disabled = true;
    try {
      const res = await post(root.dataset.saveUrl, body);
      if (!res.ok) { showErrors(res.data.errors || [T("Saving failed (HTTP {0}).", res.status)]); return; }
      dirty = false;
      document.getElementById("auto-dirty").hidden = true;
      (res.data.notes || []).forEach((n) => window.HMIDialog.toast(n, { icon: "warning", sticky: true }));
      window.HMIDialog.toast(T("Automation saved."), { icon: "success" });
      if (!automationId || res.data.created) {
        automationId = res.data.id;
        root.dataset.automationId = automationId;
        history.replaceState(null, "", res.data.url);
        document.getElementById("auto-run").disabled = false;
        const runs = document.getElementById("auto-runs");
        runs.classList.remove("disabled");
        runs.href = root.dataset.runsUrl.replace("/0/", "/" + automationId + "/");
      }
      if (yamlMode) {   // keep the visual document in sync with what was saved
        const back = await post(root.dataset.fromYamlUrl, { yaml: yamlBox.value, id: automationId });
        if (back.ok) { Object.keys(doc).forEach((k) => delete doc[k]); Object.assign(doc, back.data.doc); showSettings(); renderAll(); }
      }
    } catch (err) {
      showErrors([String(err)]);
    } finally {
      btn.disabled = false;
    }
  }
  document.getElementById("auto-save").addEventListener("click", save);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) { e.preventDefault(); save(); }
  });

  document.getElementById("auto-run").addEventListener("click", async () => {
    if (!automationId) return;
    if (dirty && !(await window.HMIDialog.confirm(T("The automation has unsaved changes. Run the saved version?")))) return;
    const res = await post(root.dataset.runUrl.replace("/0/", "/" + automationId + "/"), {});
    const text = { started: T("Started."), restarted: T("Restarted."), queued: T("Queued (still running)."), skipped: T("Already running – skipped.") }[res.data.result] || T("Failed");
    window.HMIDialog.toast(text, { icon: res.data.result === "skipped" ? "warning" : "success" });
  });

  window.addEventListener("beforeunload", (e) => {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  showSettings();
  renderAll();
  if (!doc.name) f.name.focus();
})();
