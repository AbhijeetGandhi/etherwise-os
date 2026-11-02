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
 *             before?: { label, n?, onClick? }   a drop row ABOVE the first band
 *             compact?: bool, title?: string }
 *
 * THE LOOK IS THE OLD SCANNER FUNNEL'S, ON PURPOSE (Abhijeet, 2026-11-02 22:40:
 * "the UI is shit for the funnel, prev one was very simple cool"): one row per
 * band — the label, the count and the step % on a line, a plain left-aligned
 * bar under it whose width is the count against the top band, a dashed row
 * between bands for what fell out. Linear scale: the bar IS the proportion.
 * The SVG trapezoid version lived for three hours.
 */
export function funnel(stages, opts = {}) {
  const n0 = Math.max(1, ...stages.map((s) => +s.n || 0));
  const stack = el("div", "funnel" + (opts.compact ? " compact" : ""));
  const dropRow = (d) => {
    if (!d) return null;
    const body = [el("span", "fd-arrow", "↓"),
      el("span", "grow", [d.n != null ? el("b", null, fmt.num(d.n)) : null, d.n != null ? " " : "", d.label]),
      d.onClick ? el("span", null, d.cta || "why →") : null];
    return d.onClick
      ? el("button", { class: "funnel-drop", type: "button", on: { click: d.onClick },
          attrs: { title: d.title || "what we know about this drop" } }, body)
      : el("div", "funnel-drop", body);
  };
  append(stack, dropRow(opts.before));
  const drops = opts.drops || [];
  stages.forEach((s, i) => {
    const n = Math.max(0, +s.n || 0);
    const prev = i ? +stages[i - 1].n : null;
    const conv = i ? pctOf(n, prev) : null;
    const last = i === stages.length - 1;
    const tone = s.tone || (last ? "good" : null);
    const w = Math.max(0.6, (100 * n) / n0);
    const barProps = {
      class: "funnel-bar" + (tone ? " " + tone : "") + (n ? "" : " zero"),
      style: { width: w + "%", opacity: String(Math.max(0.55, 1 - i * 0.08)) },
      attrs: { title: s.note || `${s.label}: ${fmt.num(n)}${s.onClick ? " — click for the rows" : ""}` },
    };
    const bar = s.onClick
      ? el("button", Object.assign(barProps, { type: "button", on: { click: s.onClick } }),
          el("span", "sr-only", `show the ${fmt.num(n)} rows behind ${s.label}`))
      : el("div", barProps);
    append(stack, el("div", "funnel-step", [
      el("div", "funnel-head", [
        el("span", "lbl", s.label),
        el("span", "num", fmt.num(n)),
        conv != null ? el("span", { class: "small " + (conv < 50 ? "neg" : "muted") },
          `${pctText(conv)} of ${stages[i - 1].short || "the step above"}`) : null,
        s.sub ? el("span", "muted small", s.sub) : null,
      ]),
      bar,
    ]));
    if (!last) append(stack, dropRow(drops[i]));
  });
  stack.appendChild(el("p", "sr-only", (opts.title ? opts.title + ": " : "")
    + stages.map((s) => `${s.label} ${fmt.num(s.n)}`).join(" → ")));
  return stack;
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
