// ════════════════════════════════════════════════════════════════════════════
// shapes.js — the cockpit's diagrams: funnel, ring, bands, hbars.
//
// WHY THIS FILE EXISTS (2026-11-02). Abhijeet, on the Pipeline tab: "I like
// seeing more diagrams than seeing the squares and the text inside of it … it
// was a really good funnel-like shape … now you made it like a horizontal line
// of boxes, which I don't really like … I would always prefer a diagram over a
// lot of text with boxes." Every stage strip, KPI row and count table that
// describes a SHAPE — things narrowing, a share of a target, parts of a whole —
// is drawn here, once, so the whole cockpit reads the same way and a section
// cannot hand-roll a worse one.
//
// RULES (the same ones the rest of the UI lives by):
//   · numbers stay honest: a funnel band is drawn on a sqrt scale with a
//     visible floor, so 404 → 27 → 2 → 1 → 0 all show, and the TEXT carries the
//     exact count and conversion — the shape is for the eye, the figure is the
//     fact. A band never reads wider than the one above it.
//   · every band / ring is a real <button> when it drills (brief §0 rule 2);
//     the SVG is decorative (aria-hidden) and the figures are in the DOM text.
//   · colours come from tokens.css variables so light/dark just work; no
//     Chart.js here (charts.js owns the time series).
//   · `—` is for "not measured"; 0 is a real zero and is drawn as a thin stem.
// ════════════════════════════════════════════════════════════════════════════
"use strict";
import { el, append, fmt } from "./ui.js";

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}, children = []) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, String(v));
  append(n, children);
  return n;
}

function pctOf(a, b) {
  if (!b) return null;
  return Math.round((1000 * a) / b) / 10;
}

function pctText(p) {
  if (p == null) return "—";
  return (p >= 10 ? Math.round(p) : p) + "%";
}

/* ── FUNNEL ────────────────────────────────────────────────────────────────── */

/**
 * funnel(stages, opts) → element
 *
 *   stages: [{ label, n, sub?, note?, onClick?, tone?, short? }]  top → bottom
 *           `short` is how THIS stage is named by the one below it
 *           ("27 · 6.7% of the hot jobs"); default "the step above".
 *   opts:   { drops?: [{ label, n?, onClick? }]  one per GAP (stages.length - 1),
 *             compact?: bool, floor = 0.06, height?: px per band,
 *             title?: string (sr-only summary prefix) }
 *
 * Layout: [drops column] [funnel svg] [stage column]. Each stage row is one
 * band tall, so the labels line up with the shape without any measuring. The
 * drops sit on the gap between two bands and say what fell out there.
 */
export function funnel(stages, opts = {}) {
  const H = opts.height || (opts.compact ? 44 : 64);
  const floor = opts.floor == null ? 0.06 : opts.floor;
  const n0 = Math.max(1, ...stages.map((s) => +s.n || 0));
  const W = 320, PADX = 6;
  const widths = stages.map((s) => {
    const n = Math.max(0, +s.n || 0);
    const r = Math.sqrt(n / n0);
    return (W - 2 * PADX) * Math.max(floor, r);
  });
  const total = H * stages.length;
  const pic = svg("svg", {
    class: "fun-svg", viewBox: `0 0 ${W} ${total}`, width: "100%", height: total,
    preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false",
  });
  stages.forEach((s, i) => {
    const wTop = widths[i];
    // each band is its own trapezoid: it tapers towards the next band's width
    // but never below ~45% of its own, so a 404 → 2 step reads as a drop at
    // the boundary rather than a knife-edge inside one band (the Leads ladder,
    // 12 → 0, drew a flat triangle otherwise). Classic funnel charts do this.
    const next = i + 1 < stages.length ? widths[i + 1] : Math.max(widths[i] * 0.72, (W - 2 * PADX) * floor);
    const wBot = Math.max(next, wTop * 0.45);
    const y0 = i * H + 2, y1 = (i + 1) * H - 2;
    const x0 = (W - wTop) / 2, x1 = (W + wTop) / 2;
    const x2 = (W + wBot) / 2, x3 = (W - wBot) / 2;
    const last = i === stages.length - 1;
    const tone = s.tone || (last ? "good" : null);
    const g = svg("g", { class: "fun-band" + (tone ? " " + tone : "") + (+s.n ? "" : " zero"),
      "data-i": i });
    g.appendChild(svg("polygon", {
      points: `${x0},${y0} ${x1},${y0} ${x2},${y1} ${x3},${y1}`,
      style: `opacity:${(1 - i * (0.5 / Math.max(1, stages.length - 1))).toFixed(2)}`,
    }));
    // no text inside the SVG: it is stretched to the column (preserveAspectRatio
    // none) so the bands line up with the stage rows exactly, and stretched
    // glyphs would be ugly. The figures sit in the stage column beside each band.
    pic.appendChild(g);
  });

  const stageCol = el("div", "fun-stages", stages.map((s, i) => {
    const prev = i ? +stages[i - 1].n : null;
    const conv = i ? pctOf(+s.n || 0, prev) : null;
    const body = [
      el("div", "fun-l", s.label),
      el("div", "fun-v", [fmt.num(s.n),
        conv != null ? el("span", "fun-conv", ` · ${pctText(conv)} of ${stages[i - 1].short || "the step above"}`) : null]),
      s.sub ? el("div", "fun-s", s.sub) : null,
    ];
    const props = { class: "fun-stage" + (+s.n ? "" : " dim"), style: { height: H + "px" } };
    if (s.onClick) {
      return el("button", Object.assign(props, { type: "button", on: { click: s.onClick },
        attrs: { title: s.note || `${s.label}: ${fmt.num(s.n)} — click for the rows` } }), body);
    }
    return el("div", props, body);
  }));

  const drops = opts.drops || [];
  const dropCol = el("div", "fun-drops", stages.map((s, i) => {
    const d = drops[i];                        // the gap BELOW band i
    const cell = el("div", "fun-drop-cell", null);
    cell.style.height = H + "px";
    if (d && i < stages.length - 1) {
      const txt = [d.n != null ? el("b", null, fmt.num(d.n)) : null, d.n != null ? " " : "", d.label];
      cell.appendChild(d.onClick
        ? el("button", { class: "fun-drop", type: "button", on: { click: d.onClick },
            attrs: { title: d.title || "what we know about this drop" } }, txt)
        : el("div", "fun-drop", txt));
    }
    return cell;
  }));

  // hover sync: a stage row lights its band
  const root = el("div", "fun" + (opts.compact ? " compact" : "") + (drops.length ? "" : " no-drops"));
  append(root, drops.length ? dropCol : null, el("div", "fun-pic", pic), stageCol);
  root.addEventListener("mouseover", (e) => {
    const st = e.target.closest(".fun-stage");
    if (!st) return;
    const i = Array.prototype.indexOf.call(stageCol.children, st);
    pic.querySelectorAll(".fun-band").forEach((b, j) => b.classList.toggle("hot", i === j));
  });
  root.addEventListener("mouseleave", () => pic.querySelectorAll(".fun-band").forEach((b) => b.classList.remove("hot")));
  // accessible summary of the shape, once
  root.appendChild(el("p", "sr-only", (opts.title ? opts.title + ": " : "")
    + stages.map((s) => `${s.label} ${fmt.num(s.n)}`).join(" → ")));
  return root;
}

/* ── RING (a share of a target) ───────────────────────────────────────────── */

/**
 * ring({ label, value, target, note, onClick, tone, unit, size })
 * A donut with value / target in the middle. tone: "good" | "warn" | "bad" |
 * null (auto: ≥100% good, ≥50% neutral, else warn when a target exists).
 * `format` renders the centre figure (default fmt.num).
 */
export function ring({ label, value, target, note, onClick, tone, size = 72, format }) {
  const v = +value || 0;
  const t = target == null ? null : +target;
  const p = t ? Math.max(0, Math.min(1, v / t)) : null;
  const auto = p == null ? null : p >= 1 ? "good" : p >= 0.5 ? null : "warn";
  const tn = tone || auto;
  const r = 30, c = 2 * Math.PI * r;
  const dash = p == null ? 0 : c * p;
  const pic = svg("svg", { class: "ring-svg", viewBox: "0 0 72 72", width: size, height: size,
    "aria-hidden": "true", focusable: "false" }, [
    svg("circle", { cx: 36, cy: 36, r, class: "ring-track" }),
    svg("circle", { cx: 36, cy: 36, r, class: "ring-arc", transform: "rotate(-90 36 36)",
      "stroke-dasharray": `${dash.toFixed(2)} ${(c - dash).toFixed(2)}` }),
    svg("text", { x: 36, y: t == null ? 36 : 33, class: "ring-v", "text-anchor": "middle",
      "dominant-baseline": "central" }, [(format || fmt.num)(v)]),
    t != null ? svg("text", { x: 36, y: 46, class: "ring-t", "text-anchor": "middle",
      "dominant-baseline": "central" }, [`of ${(format || fmt.num)(t)}`]) : null,
  ]);
  const body = [pic, el("div", "ring-txt", [
    el("div", "ring-l", label),
    note ? el("div", "ring-n", note) : null,
  ])];
  const cls = "ring" + (tn ? " " + tn : "");
  if (onClick) {
    return el("button", { class: cls, type: "button", on: { click: onClick },
      attrs: { title: `${label}: ${fmt.num(v)}${t != null ? ` of ${fmt.num(t)}` : ""} — click for the rows` } }, body);
  }
  return el("div", cls, body);
}

/** a row of rings */
export function rings(items) {
  return el("div", "rings", items.filter(Boolean).map((it) => ring(it)));
}

/* ── BANDS (parts of a whole, side by side) ───────────────────────────────── */

/**
 * bands(segments, opts) → one proportional bar + a legend with the counts.
 *   segments: [{ label, n, tone?, onClick?, sub? }]
 *   opts: { minPct = 4 (a zero still shows as a sliver), title }
 */
export function bands(segments, opts = {}) {
  const total = segments.reduce((s, x) => s + Math.max(0, +x.n || 0), 0);
  const minPct = opts.minPct == null ? 4 : opts.minPct;
  const F = opts.format || fmt.num;
  const bar = el("div", { class: "bands-bar", attrs: { role: "img",
    "aria-label": segments.map((s) => `${s.label} ${fmt.num(s.n)}`).join(", ") } });
  segments.forEach((s, i) => {
    const n = Math.max(0, +s.n || 0);
    const pct = total ? Math.max(n ? minPct : 1.5, (100 * n) / total) : 100 / segments.length;
    bar.appendChild(el("span", { class: "bands-seg t" + (i % 5) + (s.tone ? " " + s.tone : "") + (n ? "" : " zero"),
      style: { flexBasis: pct + "%" }, attrs: { title: `${s.label}: ${F(n)}` } },
      pct > 9 && n ? F(n) : null));
  });
  const legend = el("div", "bands-legend", segments.map((s, i) => {
    const body = [el("i", "bands-dot t" + (i % 5) + (s.tone ? " " + s.tone : "")),
      el("span", "bands-k", s.label), el("b", "bands-v", F(s.n)),
      s.sub ? el("span", "muted small", s.sub) : null];
    return s.onClick
      ? el("button", { class: "bands-item", type: "button", on: { click: s.onClick } }, body)
      : el("div", "bands-item", body);
  }));
  return el("div", "bands", [opts.title ? el("div", "bands-title", opts.title) : null, bar, legend]);
}

/* ── HBARS (a ranked list drawn, not tabled) ──────────────────────────────── */

/**
 * hbars(rows, opts) → horizontal bars with the label on the left and the
 * count at the bar's end. rows: [{ label, n, onClick?, tone? }]. opts: { max }
 */
export function hbars(rows, opts = {}) {
  const max = Math.max(1, opts.max || Math.max(...rows.map((r) => +r.n || 0)));
  const F = opts.format || fmt.num;
  return el("div", "hbars", rows.map((r) => {
    const n = Math.max(0, +r.n || 0);
    const body = [
      el("span", "hbars-l", r.label),
      el("span", "hbars-track", el("i", { class: "hbars-fill" + (r.tone ? " " + r.tone : ""),
        style: { width: Math.max(n ? 1.5 : 0, (100 * n) / max) + "%" } })),
      el("b", "hbars-v", F(n)),
    ];
    return r.onClick
      ? el("button", { class: "hbars-row", type: "button", on: { click: r.onClick } }, body)
      : el("div", "hbars-row", body);
  }));
}

/* ── RUNWAY (where we are on a fixed span: day N of 90) ───────────────────── */

/**
 * runway({ label, at, of, marks?: [{ at, label }], note }) — a horizontal span
 * with a position marker; for "day 24 of 90" and the like.
 */
export function runway({ label, at, of, marks = [], note, sub }) {
  const p = of ? Math.max(0, Math.min(100, (100 * at) / of)) : 0;
  return el("div", "runway", [
    el("div", "runway-head", [el("span", "runway-l", label),
      el("span", "runway-pos", [el("b", null, fmt.num(at)), ` of ${fmt.num(of)}`]),
      sub ? el("span", "muted small", sub) : null]),
    el("div", { class: "runway-track", attrs: { role: "img", "aria-label": `${label}: ${at} of ${of}` } }, [
      el("i", { class: "runway-fill", style: { width: p + "%" } }),
      el("i", { class: "runway-now", style: { left: p + "%" } }),
      ...marks.map((m) => el("span", { class: "runway-mark", style: { left: (of ? (100 * m.at) / of : 0) + "%" },
        attrs: { title: m.label } }, el("em", null, m.label))),
    ]),
    note ? el("div", "muted small", note) : null,
  ]);
}
