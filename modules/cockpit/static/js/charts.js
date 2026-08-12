// ═══════════════════════════════════════════════════════════════════════════
// charts.js — a thin Chart.js wrapper that is theme-aware BY CONSTRUCTION.
//
// Chart.js paints to a canvas, so it cannot inherit CSS. Colours baked in at
// construction survive a theme flip as the wrong colours — dark bars on a dark
// background. So: nothing here hardcodes a colour. Every chart is described by
// a FACTORY that receives the resolved palette, and the wrapper re-invokes that
// factory (and re-reads the CSS custom properties) whenever the theme changes.
//
// Chart.js is VENDORED at /static/vendor/chart.umd.js — the cockpit works with
// the wifi off. index.html loads it as a classic script before the module
// graph, so `window.Chart` is present by the time a section renders.
//
// USAGE
//   import { chart, palette } from "../charts.js";
//   const c = chart(container, (p) => ({
//     type: "bar",
//     data: { labels, datasets: [{ data, backgroundColor: p.series[0] }] },
//     options: { plugins: { legend: { display: false } } },
//   }), { height: 220 });
//   // …later: c.setFactory(nextFactory) to re-draw with new data; c.destroy().
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

import { el } from "./ui.js";

export const THEME_EVENT = "ew:themechange";

const live = new Set();

/** Read the current resolved palette from CSS custom properties. */
export function palette() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (cs.getPropertyValue(name) || fallback || "").trim();
  const p = {
    fg:        v("--foreground"),
    muted:     v("--muted-foreground"),
    border:    v("--border"),
    card:      v("--card"),
    popover:   v("--popover"),
    grid:      v("--chart-grid"),
    axis:      v("--chart-axis"),
    target:    v("--chart-target"),
    success:   v("--success"),
    warning:   v("--warning"),
    destructive: v("--destructive"),
    info:      v("--info"),
    series:     [v("--chart-1"), v("--chart-2"), v("--chart-3"), v("--chart-4"), v("--chart-5")],
    seriesSoft: [v("--chart-1-soft"), v("--chart-2-soft"), v("--chart-3-soft"),
                 v("--chart-4-soft"), v("--chart-5-soft")],
    font: v("--font-sans", "system-ui"),
  };
  p.color = (i) => p.series[i % p.series.length];
  p.soft = (i) => p.seriesSoft[i % p.seriesSoft.length];
  return p;
}

/** Defaults every chart inherits: our type scale, our neutrals, our motion. */
function applyDefaults(p) {
  const C = window.Chart;
  if (!C) return;
  C.defaults.font.family = p.font;
  C.defaults.font.size = 12;
  C.defaults.color = p.muted;
  C.defaults.borderColor = p.grid;
  C.defaults.animation.duration = 160;
  C.defaults.animation.easing = "easeOutQuart";
  C.defaults.maintainAspectRatio = false;
  C.defaults.plugins.legend.labels.boxWidth = 10;
  C.defaults.plugins.legend.labels.boxHeight = 10;
  C.defaults.plugins.legend.labels.usePointStyle = true;
  C.defaults.plugins.tooltip.backgroundColor = p.popover;
  C.defaults.plugins.tooltip.titleColor = p.fg;
  C.defaults.plugins.tooltip.bodyColor = p.fg;
  C.defaults.plugins.tooltip.borderColor = p.border;
  C.defaults.plugins.tooltip.borderWidth = 1;
  C.defaults.plugins.tooltip.padding = 10;
  C.defaults.plugins.tooltip.cornerRadius = 8;
  C.defaults.plugins.tooltip.displayColors = false;
}

/**
 * Axis/scale defaults so every chart in the cockpit reads the same.
 * Merge it into your options: `options: { scales: axes(p) }`.
 */
export function axes(p, opts = {}) {
  return {
    x: {
      grid: { display: false, drawBorder: false },
      border: { color: p.border },
      ticks: { color: p.axis, maxRotation: 0, autoSkipPadding: 12 },
      ...(opts.x || {}),
    },
    y: {
      beginAtZero: true,
      grid: { color: p.grid, drawBorder: false, drawTicks: false },
      border: { display: false },
      ticks: { color: p.axis, padding: 8, ...(opts.yTicks || {}) },
      ...(opts.y || {}),
    },
  };
}

/**
 * chart(container, factory, opts) → { instance, setFactory, refresh, destroy }
 *
 *   container  an element to render into (a <canvas> is created inside it)
 *   factory(p) returns a full Chart.js config, given the live palette
 *   opts       { height = 220 }
 *
 * The returned handle survives theme flips: the factory is re-run and the
 * chart updated in place. Sections must not keep their own Chart instance.
 */
export function chart(container, factory, opts = {}) {
  const frame = el("div", { class: "chartframe", style: { height: (opts.height || 220) + "px" } });
  const canvas = el("canvas");
  frame.appendChild(canvas);
  container.appendChild(frame);

  if (!window.Chart) {
    frame.replaceChildren(el("div", "chart-empty",
      "Chart library did not load — /static/vendor/chart.umd.js is missing."));
    return { instance: null, setFactory() {}, refresh() {}, destroy() {} };
  }

  let fac = factory;
  const p0 = palette();
  applyDefaults(p0);
  const instance = new window.Chart(canvas.getContext("2d"), fac(p0));

  const handle = {
    instance,
    /** swap in new data/config (e.g. a range filter changed) */
    setFactory(next) { fac = next; handle.refresh(); },
    /** re-read the palette and redraw — called automatically on theme change */
    refresh() {
      const p = palette();
      applyDefaults(p);
      const cfg = fac(p);
      instance.data = cfg.data;
      instance.options = cfg.options || {};
      if (cfg.type && instance.config) instance.config.type = cfg.type;
      instance.update("none");
    },
    destroy() { live.delete(handle); instance.destroy(); frame.remove(); },
  };
  live.add(handle);
  return handle;
}

/** Called by app.js after it flips data-theme. Sections never call this. */
export function retheme() {
  live.forEach((h) => { try { h.refresh(); } catch (e) { /* a dead chart must not break the flip */ } });
}

/** Drop every chart owned by a section that is being unmounted. */
export function destroyAll() {
  [...live].forEach((h) => { try { h.destroy(); } catch (e) { /* ignore */ } });
  live.clear();
}

window.addEventListener(THEME_EVENT, retheme);

export default { chart, palette, axes, retheme, destroyAll, THEME_EVENT };
