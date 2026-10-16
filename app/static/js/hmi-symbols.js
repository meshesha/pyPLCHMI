/**
 * pyPLCHMI built-in SVG symbol library.
 *
 * Every symbol is drawn in a 100 × 100 view box and uses CSS classes that the
 * renderer and stylesheet (hmi.css) bring to life:
 *
 *   .sb   body      – fill: --sym-fill, stroke: --sym-line
 *   .sl   line      – stroke only (--sym-line)
 *   .sa   accent    – fill: --sym-accent (on/off colour of the bound tag)
 *   .sr   rotor     – spins while the bound tag is ON
 *   .sf   flame/glow– only visible while ON
 *   .sk   blink     – blinks while ON
 *   .s-on / .s-off  – alternative drawings for ON / OFF
 *   .sv   level     – rectangle whose height follows a numeric tag (mode "level");
 *                     data-top / data-bottom give the fill range in view-box units.
 *
 * Keys must match SYMBOL_KEYS in app/dashboards/layout.py (a test checks this).
 * The markup is constant and trusted; user text is never inserted here.
 */
(function () {
  "use strict";

  /** Four fan blades around (cx, cy). */
  function blades(cx, cy, r) {
    const b = `M${cx} ${cy} C ${cx - r * 0.25} ${cy - r * 0.55}, ${cx - r * 0.1} ${cy - r}, ${cx + r * 0.25} ${cy - r} C ${cx + r * 0.35} ${cy - r * 0.6}, ${cx + r * 0.2} ${cy - r * 0.25}, ${cx} ${cy} Z`;
    return [0, 90, 180, 270].map((a) => `<path class="sa" d="${b}" transform="rotate(${a} ${cx} ${cy})"/>`).join("");
  }

  window.HMI_SYMBOLS = {
    fan: {
      name: "Fan", category: "HVAC", mode: "state",
      svg: `<circle class="sb" cx="50" cy="50" r="46"/>
            <g class="sr" style="transform-origin:50px 50px">${blades(50, 50, 40)}</g>
            <circle class="sb" cx="50" cy="50" r="7"/>`,
    },
    pump: {
      name: "Pump", category: "Piping", mode: "state",
      svg: `<path class="sb" d="M20 92 L32 70 L68 70 L80 92 Z"/>
            <rect class="sb" x="58" y="10" width="34" height="18"/>
            <circle class="sb" cx="50" cy="50" r="34"/>
            <g class="sr" style="transform-origin:50px 50px">
              <path class="sa" d="M50 50 L50 22 A28 28 0 0 1 74 36 Z"/>
              <path class="sa" d="M50 50 L74 64 A28 28 0 0 1 50 78 Z"/>
              <path class="sa" d="M50 50 L26 64 A28 28 0 0 1 26 36 Z"/>
            </g>
            <circle class="sb" cx="50" cy="50" r="6"/>`,
    },
    motor: {
      name: "Motor", category: "Electrical", mode: "state",
      svg: `<rect class="sb" x="10" y="25" width="64" height="50" rx="6"/>
            <path class="sl" d="M22 25 V75 M34 25 V75 M46 25 V75 M58 25 V75"/>
            <rect class="sb" x="74" y="44" width="18" height="12"/>
            <rect class="sb" x="16" y="75" width="52" height="10"/>
            <circle class="sa" cx="88" cy="20" r="8"/>`,
    },
    generator: {
      name: "Generator", category: "Electrical", mode: "state",
      svg: `<circle class="sb" cx="50" cy="50" r="44"/>
            <circle class="sa sk" cx="50" cy="50" r="34"/>
            <text x="50" y="50" text-anchor="middle" dominant-baseline="central" font-size="34" font-weight="700" class="st">G</text>
            <path class="sl" d="M30 74 q5 -8 10 0 t10 0 t10 0 t10 0"/>`,
    },
    valve: {
      name: "Valve", category: "Piping", mode: "state",
      svg: `<path class="sa" d="M8 30 L50 55 L8 80 Z"/>
            <path class="sa" d="M92 30 L50 55 L92 80 Z"/>
            <path class="sl" d="M50 55 V22"/>
            <rect class="sb" x="34" y="8" width="32" height="14" rx="3"/>`,
    },
    tank: {
      name: "Tank", category: "Piping", mode: "level",
      svg: `<rect class="sb" x="18" y="6" width="64" height="88" rx="14"/>
            <rect class="sv" x="22" y="90" width="56" height="0" data-top="12" data-bottom="90"/>
            <path class="sl" d="M18 30 h8 M18 50 h8 M18 70 h8" style="stroke-width:2"/>`,
    },
    boiler: {
      name: "Boiler", category: "HVAC", mode: "state",
      svg: `<rect class="sb" x="15" y="8" width="70" height="84" rx="8"/>
            <rect class="sb" x="27" y="18" width="46" height="14" rx="2"/>
            <path class="sa sf" d="M50 86 C30 80 32 62 42 52 C42 62 48 64 50 58 C52 50 46 44 54 36 C56 48 70 56 64 72 C62 80 56 86 50 86 Z"/>
            <path class="sl s-off" d="M34 80 H66"/>`,
    },
    heater: {
      name: "Radiator", category: "HVAC", mode: "state",
      svg: `<rect class="sb" x="8" y="30" width="12" height="56" rx="5"/>
            <rect class="sb" x="25" y="30" width="12" height="56" rx="5"/>
            <rect class="sb" x="42" y="30" width="12" height="56" rx="5"/>
            <rect class="sb" x="59" y="30" width="12" height="56" rx="5"/>
            <rect class="sb" x="76" y="30" width="12" height="56" rx="5"/>
            <path class="sl sf" d="M24 22 q-6 -6 0 -12 q6 -6 0 -10 M48 22 q-6 -6 0 -12 q6 -6 0 -10 M72 22 q-6 -6 0 -12 q6 -6 0 -10" style="stroke:var(--sym-accent)"/>`,
    },
    ac_unit: {
      name: "AC unit", category: "HVAC", mode: "state",
      svg: `<rect class="sb" x="4" y="16" width="92" height="68" rx="6"/>
            <circle class="sb" cx="62" cy="50" r="26"/>
            <g class="sr" style="transform-origin:62px 50px">${blades(62, 50, 22)}</g>
            <path class="sl" d="M12 30 H28 M12 42 H28 M12 54 H28 M12 66 H28"/>`,
    },
    light: {
      name: "Light", category: "Electrical", mode: "state",
      svg: `<g class="sf"><path class="sl" d="M50 2 V10 M14 18 L20 24 M86 18 L80 24 M2 50 H10 M98 50 H90" style="stroke:var(--sym-accent)"/></g>
            <path class="sa" d="M50 14 C30 14 20 30 22 46 C24 58 34 62 36 74 H64 C66 62 76 58 78 46 C80 30 70 14 50 14 Z"/>
            <rect class="sb" x="36" y="76" width="28" height="8" rx="2"/>
            <rect class="sb" x="40" y="86" width="20" height="8" rx="2"/>`,
    },
    thermometer: {
      name: "Temp. sensor", category: "Sensors", mode: "state",
      svg: `<rect class="sb" x="40" y="6" width="20" height="64" rx="10"/>
            <circle class="sa" cx="50" cy="78" r="16"/>
            <rect class="sa" x="46" y="30" width="8" height="40"/>
            <path class="sl" d="M62 20 h8 M62 34 h8 M62 48 h8" style="stroke-width:2"/>`,
    },
    alarm: {
      name: "Alarm", category: "Sensors", mode: "state",
      svg: `<path class="sa sk" d="M50 10 C32 10 24 26 24 42 V62 L14 76 H86 L76 62 V42 C76 26 68 10 50 10 Z"/>
            <circle class="sb" cx="50" cy="84" r="9"/>`,
    },
    breaker: {
      name: "Breaker", category: "Electrical", mode: "state",
      svg: `<path class="sl" d="M50 4 V30 M50 70 V96"/>
            <circle class="sb" cx="50" cy="30" r="5"/><circle class="sb" cx="50" cy="70" r="5"/>
            <path class="sl s-off" d="M50 70 L76 36" style="stroke-width:5"/>
            <path class="sl s-on" d="M50 70 L50 30" style="stroke-width:5;stroke:var(--sym-accent)"/>`,
    },
    transformer: {
      name: "Transformer", category: "Electrical", mode: "state",
      svg: `<path class="sl" d="M50 2 V16 M50 84 V98"/>
            <circle class="sb" cx="50" cy="38" r="22" style="fill-opacity:.6"/>
            <circle class="sb" cx="50" cy="62" r="22" style="fill-opacity:.6"/>
            <circle class="sa" cx="88" cy="12" r="7"/>`,
    },
    solar_panel: {
      name: "Solar panel", category: "Electrical", mode: "state",
      svg: `<path class="sb" d="M20 20 H92 L80 70 H8 Z"/>
            <path class="sl" d="M14 45 H86 M44 20 L32 70 M68 20 L56 70" style="stroke-width:2"/>
            <path class="sl" d="M44 70 V90 M28 90 H60"/>
            <circle class="sa" cx="12" cy="10" r="7"/>`,
    },
    battery: {
      name: "Battery", category: "Electrical", mode: "level",
      svg: `<rect class="sb" x="38" y="4" width="24" height="10" rx="2"/>
            <rect class="sb" x="22" y="12" width="56" height="84" rx="8"/>
            <rect class="sv" x="28" y="90" width="44" height="0" data-top="18" data-bottom="90"/>`,
    },
    door: {
      name: "Door", category: "Building", mode: "static",
      svg: `<path class="sl" d="M4 96 H96" style="stroke-width:6"/>
            <path class="sl" d="M8 96 V8"/>
            <path class="sl" d="M8 8 A88 88 0 0 1 96 96" style="stroke-dasharray:6 5;stroke-width:2"/>`,
    },
    window: {
      name: "Window", category: "Building", mode: "static",
      svg: `<rect class="sb" x="2" y="38" width="96" height="24"/>
            <path class="sl" d="M2 50 H98 M34 38 V62 M66 38 V62" style="stroke-width:2"/>`,
    },
    stairs: {
      name: "Stairs", category: "Building", mode: "static",
      svg: `<rect class="sb" x="6" y="6" width="88" height="88"/>
            <path class="sl" d="M6 22 H94 M6 38 H94 M6 54 H94 M6 70 H94 M6 86 H94" style="stroke-width:2"/>
            <path class="sl" d="M50 84 V16 M40 28 L50 16 L60 28"/>`,
    },
    arrow: {
      name: "Flow arrow", category: "Piping", mode: "state",
      svg: `<path class="sa" d="M6 36 H60 V18 L96 50 L60 82 V64 H6 Z"/>`,
    },
  };
})();
