/**
 * pyPLCHMI graphic renderer – shared by the designer and the runtime viewer.
 *
 * Turns a layout document (see app/dashboards/layout.py) into SVG nodes and
 * applies live PLC values to them.  All user-supplied strings are inserted
 * with textContent / setAttribute only, never as markup.
 *
 * Public API (window.HMIRender):
 *   renderLayout(svg, layout, ctx)        -> {nodes: {id: <g>}, layer: <g>}
 *   createNode(element, ctx)              -> <g>
 *   applyValues(nodes, layout, values, ctx)
 *   urlFor(template, id)                  -> URL with the trailing /0 replaced
 *
 * ctx = {points: {id: pointMeta}, urls: {asset: "/assets/0/file", ...},
 *        designer: bool, icons: {"outline/home": shape}, loadIcon(style, name)?}
 *
 * Visibility: elements with vis = "hidden" or vis = "tag" get the class
 * "hmi-vis-off" while they are hidden; the runtime CSS hides them, the
 * designer only dims them.  applyValues() evaluates the tag rule
 * (visPoint, visOp, visValue) on every update.
 */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";

  /**
   * Replace the placeholder id 0 in a URL template built by url_for(..., id=0).
   * @param {string} template e.g. "/assets/0/file"
   * @param {number|string} id Real id.
   * @returns {string}
   */
  function urlFor(template, id) {
    return template.replace(/\/0(?=\/|$)(?!.*\/0(?=\/|$))/, "/" + encodeURIComponent(id));
  }

  /**
   * Create an SVG element with attributes.
   * @param {string} tag Element name.
   * @param {Object} [attrs] Attributes (null/undefined values are skipped).
   * @param {Element} [parent] Optional parent to append to.
   * @returns {SVGElement}
   */
  function svgEl(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    if (attrs) {
      Object.entries(attrs).forEach(([k, v]) => {
        if (v !== null && v !== undefined) node.setAttribute(k, v);
      });
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  /** @returns {string} SVG fill/stroke value ("none" for transparent). */
  function paint(color) {
    return !color || color === "transparent" ? "none" : color;
  }

  const FONTS = { sans: "system-ui, Segoe UI, Arial, sans-serif", mono: "ui-monospace, Consolas, monospace", serif: "Georgia, serif" };

  /**
   * Add a multi-line <text> node.
   * @param {Element} parent Parent group.
   * @param {string} text Text (\n separates lines).
   * @param {Object} o Options {x, y, size, color, anchor, bold, italic, font, baseline}.
   * @returns {SVGTextElement}
   */
  function addText(parent, text, o) {
    const t = svgEl("text", {
      x: o.x, y: o.y, "font-size": o.size, fill: paint(o.color), "text-anchor": o.anchor || "middle",
      "font-weight": o.bold ? "700" : "400", "font-style": o.italic ? "italic" : "normal",
      "font-family": FONTS[o.font || "sans"], "dominant-baseline": o.baseline || "auto",
    }, parent);
    const lines = String(text).split("\n");
    if (lines.length === 1) {
      t.textContent = lines[0];
    } else {
      lines.forEach((line, i) => {
        const span = svgEl("tspan", { x: o.x, dy: i === 0 ? 0 : "1.2em" }, t);
        span.textContent = line || " ";
      });
    }
    return t;
  }

  /**
   * Draw the optional label of a rect/ellipse/symbol.
   * @param {Element} g Element group.
   * @param {Object} e Element.
   */
  function addLabel(g, e) {
    if (!e.label || e.labelPos === "none") return;
    const size = e.labelSize || 16;
    let y = e.h / 2, baseline = "central";
    if (e.labelPos === "top") { y = -size * 0.4; baseline = "auto"; }
    if (e.labelPos === "bottom") { y = e.h + size * 1.1; baseline = "auto"; }
    const t = addText(g, e.label, { x: e.w / 2, y: y, size: size, color: e.labelColor, bold: true, baseline: baseline });
    t.classList.add("hmi-label-text");
  }

  /** @returns {string} "x,y x,y ..." for a polyline. */
  function pointsAttr(points) {
    return points.map((p) => p[0] + "," + p[1]).join(" ");
  }

  /**
   * Text shown in a value box while no live value is available.
   * @param {Object} e Value element.
   * @param {Object} ctx Render context.
   * @returns {string}
   */
  function valuePlaceholder(e, ctx) {
    const p = e.point && ctx.points[e.point];
    if (!p) return (e.prefix || "") + (ctx.designer ? "⟨no tag⟩" : "—");
    return (e.prefix || "") + (ctx.designer ? "⟨" + p.label + "⟩" : "—");
  }

  const ICON_SHAPES = new Set(["path", "circle", "ellipse", "rect", "line", "polyline", "polygon"]);

  /** Element type → function(group, element, ctx) drawing it in local coordinates. */
  const DRAW = {
    rect(g, e) {
      svgEl("rect", { class: "hmi-shape", width: e.w, height: e.h, rx: e.rx || 0, fill: paint(e.fill),
        stroke: paint(e.stroke), "stroke-width": e.strokeWidth, "stroke-dasharray": e.dash ? "10 6" : null }, g);
      addLabel(g, e);
    },
    ellipse(g, e) {
      svgEl("ellipse", { class: "hmi-shape", cx: e.w / 2, cy: e.h / 2, rx: e.w / 2, ry: e.h / 2, fill: paint(e.fill),
        stroke: paint(e.stroke), "stroke-width": e.strokeWidth, "stroke-dasharray": e.dash ? "10 6" : null }, g);
      addLabel(g, e);
    },
    line(g, e) {
      svgEl("polyline", { class: "hmi-shape", points: pointsAttr(e.points), fill: "none", stroke: paint(e.stroke),
        "stroke-width": e.strokeWidth, "stroke-linecap": "round", "stroke-linejoin": "round",
        "stroke-dasharray": e.dash ? (e.strokeWidth * 2) + " " + (e.strokeWidth * 1.5) : null }, g);
    },
    pipe(g, e) {
      const common = { points: pointsAttr(e.points), fill: "none", "stroke-linecap": "round", "stroke-linejoin": "round" };
      svgEl("polyline", { ...common, stroke: "#0d1015", "stroke-width": e.strokeWidth + 4 }, g);
      svgEl("polyline", { ...common, class: "hmi-shape", stroke: paint(e.stroke), "stroke-width": e.strokeWidth }, g);
      svgEl("polyline", { ...common, stroke: "rgba(255,255,255,.18)", "stroke-width": Math.max(1, e.strokeWidth * 0.25), transform: "translate(0," + (-e.strokeWidth * 0.2) + ")" }, g);
      svgEl("polyline", { ...common, class: "hmi-flow", stroke: paint(e.flowColor), "stroke-width": Math.max(2, e.strokeWidth * 0.45), "stroke-dasharray": "12 12" }, g);
    },
    text(g, e) {
      if (e.bg && e.bg !== "none") svgEl("rect", { width: e.w, height: e.h, fill: paint(e.bg), rx: 4 }, g);
      const x = e.align === "middle" ? e.w / 2 : e.align === "end" ? e.w - 4 : 4;
      addText(g, e.text, { x: x, y: e.size, size: e.size, color: e.color, anchor: e.align, bold: e.bold, italic: e.italic, font: e.font });
    },
    image(g, e, ctx) {
      if (!e.asset) {
        svgEl("rect", { width: e.w, height: e.h, fill: "#0d1015", stroke: "#8b98a8", "stroke-dasharray": "6 4" }, g);
        addText(g, "image", { x: e.w / 2, y: e.h / 2, size: 14, color: "#8b98a8", baseline: "central" });
        return;
      }
      const par = { contain: "xMidYMid meet", stretch: "none", cover: "xMidYMid slice" }[e.fit] || "xMidYMid meet";
      svgEl("image", { href: urlFor(ctx.urls.asset, e.asset), width: e.w, height: e.h, preserveAspectRatio: par }, g);
    },
    symbol(g, e) {
      const def = (window.HMI_SYMBOLS || {})[e.symbol];
      g.style.setProperty("--sym-line", e.color);
      g.style.setProperty("--sym-accent", def && def.mode === "level" ? e.onColor : e.offColor);
      const inner = svgEl("svg", { class: "hmi-sym", width: e.w, height: e.h, viewBox: "0 0 100 100", preserveAspectRatio: "xMidYMid meet", overflow: "visible" }, g);
      // Trusted constant markup from hmi-symbols.js (never user input).
      inner.innerHTML = def ? def.svg : '<rect x="0" y="0" width="100" height="100" fill="none" stroke="red"/>';
      addLabel(g, e);
    },
    value(g, e, ctx) {
      svgEl("rect", { class: "hmi-value-box", width: e.w, height: e.h, rx: 4, fill: paint(e.bg), stroke: paint(e.border), "stroke-width": 1.5 }, g);
      const x = e.align === "middle" ? e.w / 2 : e.align === "end" ? e.w - 8 : 8;
      const t = addText(g, valuePlaceholder(e, ctx), { x: x, y: e.h / 2, size: e.size, color: e.color, anchor: e.align, bold: e.bold, font: "mono", baseline: "central" });
      t.classList.add("hmi-value-text");
    },
    lamp(g, e) {
      svgEl("ellipse", { class: "hmi-lamp-shape", cx: e.w / 2, cy: e.h / 2, rx: e.w / 2, ry: e.h / 2, fill: paint(e.offColor), stroke: paint(e.stroke), "stroke-width": 2 }, g);
    },
    button(g, e) {
      g.classList.add("hmi-btn");
      svgEl("rect", { class: "hmi-btn-face", width: e.w, height: e.h, rx: Math.min(8, e.h / 4), fill: paint(e.color) }, g);
      addText(g, e.text, { x: e.w / 2, y: e.h / 2, size: e.size, color: e.textColor, bold: true, baseline: "central" });
    },
    icon(g, e, ctx) {
      const key = (e.iconStyle || "outline") + "/" + e.icon;
      const data = ctx.icons && ctx.icons[key];
      svgEl("rect", { width: e.w, height: e.h, fill: "transparent" }, g);  // hit area for clicks
      if (!data) {
        svgEl("rect", { width: e.w, height: e.h, fill: "none", stroke: "#8b98a8", "stroke-dasharray": "4 3" }, g);
        addText(g, "…", { x: e.w / 2, y: e.h / 2, size: Math.min(e.w, e.h) / 3, color: "#8b98a8", baseline: "central" });
        if (ctx.loadIcon) ctx.loadIcon(e.iconStyle || "outline", e.icon);
        addLabel(g, e);
        return;
      }
      const inner = svgEl("svg", { class: "hmi-icon", width: e.w, height: e.h, viewBox: data.viewBox, preserveAspectRatio: "xMidYMid meet", overflow: "visible" }, g);
      const color = paint(e.point ? e.offColor : e.color);
      const attrs = data.mode === "fill" ? { fill: color, stroke: "none" }
        : { fill: "none", stroke: color, "stroke-width": e.strokeWidth || 2, "stroke-linecap": "round", "stroke-linejoin": "round" };
      const paintGroup = svgEl("g", { class: "hmi-icon-paint", color: color, ...attrs }, inner);
      paintGroup.dataset.mode = data.mode;
      // Shapes come from the server's icon index (geometry attributes only), set with setAttribute.
      data.nodes.forEach((n) => { if (ICON_SHAPES.has(n[0])) svgEl(n[0], n[1], paintGroup); });
      addLabel(g, e);
    },
    bar(g, e) {
      svgEl("rect", { width: e.w, height: e.h, fill: paint(e.bg), stroke: paint(e.stroke), "stroke-width": 1.5, rx: 3 }, g);
      const horizontal = e.orientation === "horizontal";
      svgEl("rect", { class: "hmi-bar-level", x: 0, y: horizontal ? 0 : e.h, width: horizontal ? 0 : e.w, height: horizontal ? e.h : 0, fill: paint(e.color), rx: 3 }, g);
    },
  };

  /**
   * Build the SVG group for one element.
   * @param {Object} e Layout element.
   * @param {Object} ctx Render context.
   * @returns {SVGGElement}
   */
  function createNode(e, ctx) {
    const g = svgEl("g", { class: "hmi-el hmi-t-" + e.type, "data-id": e.id });
    if (e.type !== "line" && e.type !== "pipe") {
      let tf = "translate(" + e.x + " " + e.y + ")";
      if (e.rot) tf += " rotate(" + e.rot + " " + e.w / 2 + " " + e.h / 2 + ")";
      g.setAttribute("transform", tf);
    }
    if (e.opacity !== undefined && e.opacity < 1) g.setAttribute("opacity", e.opacity);
    if (e.vis === "hidden" || e.vis === "tag") g.classList.add("hmi-vis-off");  // tag rule: hidden until a value arrives
    if (e.vis === "hidden") g.classList.add("hmi-vis-fixed");
    if (e.link) { g.classList.add("hmi-link"); g.setAttribute("data-link", e.link); }
    const draw = DRAW[e.type];
    if (draw) draw(g, e, ctx);
    const p = e.point && ctx.points[e.point];
    const tip = [e.name, p ? p.label + " (" + p.plc + " " + p.address + ")" : ""].filter(Boolean).join(" – ");
    if (tip) svgEl("title", {}, g).textContent = tip;
    return g;
  }

  /**
   * Render a whole layout into an <svg> (clears it first).
   * @param {SVGSVGElement} svg Target element.
   * @param {Object} layout Layout document.
   * @param {Object} ctx Render context.
   * @returns {{nodes: Object<string, SVGGElement>, layer: SVGGElement, background: SVGGElement}}
   */
  function renderLayout(svg, layout, ctx) {
    const c = layout.canvas;
    svg.replaceChildren();
    svg.setAttribute("viewBox", "0 0 " + c.width + " " + c.height);
    const background = svgEl("g", { class: "hmi-background" }, svg);
    svgEl("rect", { width: c.width, height: c.height, fill: paint(c.background) }, background);
    if (c.backgroundImage) {
      const par = { contain: "xMidYMid meet", stretch: "none", cover: "xMidYMid slice" }[c.backgroundFit] || "xMidYMid meet";
      svgEl("image", { href: urlFor(ctx.urls.asset, c.backgroundImage), width: c.width, height: c.height, preserveAspectRatio: par }, background);
    }
    const layer = svgEl("g", { class: "hmi-layer" }, svg);
    const nodes = {};
    layout.elements.forEach((e) => {
      nodes[e.id] = createNode(e, ctx);
      layer.appendChild(nodes[e.id]);
    });
    return { nodes: nodes, layer: layer, background: background };
  }

  /**
   * Interpret a live value as ON/OFF.
   * @param {*} value Engineering value.
   * @returns {boolean}
   */
  function isOn(value) {
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value !== 0;
    return !!value;
  }

  /**
   * Fraction (0..1) of value between min and max.
   * @returns {number}
   */
  function fraction(value, min, max) {
    if (typeof value !== "number" || !(max > min)) return 0;
    return Math.min(1, Math.max(0, (value - min) / (max - min)));
  }

  /**
   * Apply live values to rendered nodes.
   * @param {Object<string, SVGGElement>} nodes Map id -> group from renderLayout.
   * @param {Object} layout Layout document.
   * @param {Object<string, {value:*, display:string, quality:string, error:?string}>} values From /api/.../values.
   * @param {Object} ctx Render context.
   */
  function applyValues(nodes, layout, values, ctx) {
    layout.elements.forEach((e) => {
      if (e.vis === "tag" && nodes[e.id]) {
        nodes[e.id].classList.toggle("hmi-vis-off", !isVisible(e, values[String(e.visPoint)]));
      }
      if (!e.point) return;
      const g = nodes[e.id];
      const v = values[String(e.point)];
      if (!g || !v) return;
      const bad = v.quality !== "good";
      g.classList.toggle("hmi-bad", bad);
      g.classList.toggle("hmi-stale", Boolean(v.stale));
      const p = ctx.points[e.point];
      const title = g.querySelector(":scope > title");
      if (title) title.textContent = (e.name ? e.name + " – " : "") + (p ? p.label + ": " : "") + (bad ? "ERROR " + (v.error || "") : v.display)
        + (v.stale ? " (last value – PLC reconnecting)" : "");
      const on = !bad && isOn(v.value);
      g.dataset.value = JSON.stringify(v.value);

      switch (e.type) {
        case "rect":
        case "ellipse":
          g.querySelector(".hmi-shape").setAttribute("fill", paint(on ? e.onColor : e.offColor));
          break;
        case "line":
          g.querySelector(".hmi-shape").setAttribute("stroke", paint(on ? e.onColor : e.offColor));
          break;
        case "pipe":
          g.classList.toggle("hmi-on", on);
          break;
        case "symbol": {
          const def = (window.HMI_SYMBOLS || {})[e.symbol] || {};
          if (def.mode === "level") {
            const f = bad ? 0 : fraction(v.value, e.min, e.max);
            g.querySelectorAll(".sv").forEach((lv) => {
              const top = parseFloat(lv.dataset.top), bottom = parseFloat(lv.dataset.bottom);
              const h = (bottom - top) * f;
              lv.setAttribute("y", bottom - h);
              lv.setAttribute("height", h);
            });
          } else {
            g.classList.toggle("hmi-on", on);
            g.style.setProperty("--sym-accent", on ? e.onColor : e.offColor);
          }
          break;
        }
        case "value": {
          const t = g.querySelector(".hmi-value-text");
          t.textContent = (e.prefix || "") + (bad ? "ERR" : v.display);
          const alarm = typeof v.value === "number" && ((e.lo !== null && e.lo !== undefined && v.value < e.lo) || (e.hi !== null && e.hi !== undefined && v.value > e.hi));
          t.setAttribute("fill", paint(alarm ? e.alarmColor : e.color));
          g.classList.toggle("hmi-alarm", !!alarm);
          break;
        }
        case "lamp":
          g.querySelector(".hmi-lamp-shape").setAttribute("fill", paint(on ? e.onColor : e.offColor));
          g.classList.toggle("hmi-blink", on && !!e.blink);
          break;
        case "button":
          g.classList.toggle("hmi-on", on);
          break;
        case "icon": {
          const pg = g.querySelector(".hmi-icon-paint");
          if (pg) {
            const c = paint(on ? e.onColor : e.offColor);
            pg.setAttribute("color", c);
            pg.setAttribute(pg.dataset.mode === "fill" ? "fill" : "stroke", c);
          }
          g.classList.toggle("hmi-blink-el", on && !!e.blink);
          break;
        }
        case "bar": {
          const lvl = g.querySelector(".hmi-bar-level");
          const f = bad ? 0 : fraction(v.value, e.min, e.max);
          if (e.orientation === "horizontal") lvl.setAttribute("width", e.w * f);
          else { lvl.setAttribute("y", e.h * (1 - f)); lvl.setAttribute("height", e.h * f); }
          break;
        }
        default:
          break;
      }
    });
  }

  /**
   * Evaluate an element's visibility rule against a tag value.
   * Unknown or bad values hide the element.
   * @param {Object} e Element with visOp / visValue.
   * @param {?Object} v Value object {value, quality} of the visPoint tag.
   * @returns {boolean} True when the element should be shown.
   */
  function isVisible(e, v) {
    if (!v || v.quality !== "good" || v.value === null || v.value === undefined) return false;
    const op = e.visOp || "on";
    if (op === "on") return isOn(v.value);
    if (op === "off") return !isOn(v.value);
    const raw = String(e.visValue === undefined || e.visValue === null ? "" : e.visValue).trim();
    let a = v.value, b = raw;
    if (typeof a === "boolean") b = ["1", "true", "on", "yes"].includes(raw.toLowerCase());
    else if (typeof a === "number") { b = parseFloat(raw); if (Number.isNaN(b)) return false; }
    else a = String(a);
    switch (op) {
      case "eq": return a === b;
      case "ne": return a !== b;
      case "gt": return a > b;
      case "ge": return a >= b;
      case "lt": return a < b;
      case "le": return a <= b;
      default: return true;
    }
  }

  window.HMIRender = { renderLayout, createNode, applyValues, urlFor, svgEl, isOn, isVisible, paint };
})();
