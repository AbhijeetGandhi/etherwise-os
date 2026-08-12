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
//     plugins: [targetLine(p)],          // built FROM the palette, see below
//   }), { height: 220 });
//   // …later: c.setFactory(nextFactory) to re-draw with new data; c.destroy().
//
// TWO THINGS THIS MODULE OWNS SO NO SECTION HAS TO:
//   1. The container is DETACHED while a section renders, so construction is
//      deferred one frame. Never wrap chart() in your own rAF.
//   2. Inline `plugins` are re-created from the factory on every refresh, so a
//      custom plugin may close over its `p` — write
//      `const line = (p) => ({ id, afterDatasetsDraw(ch) { …p.target… } })`
//      and pass `plugins: [line(p)]`. Calling palette() again at draw time is
//      no longer necessary (it was the workaround for this bug).
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

import { el, append } from "./ui.js";

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
 * Run `fn` on the next animation frame (or as soon as possible where there is
 * no rAF, e.g. a jsdom test harness). Returns a cancel function.
 */
function nextFrame(fn) {
  if (typeof requestAnimationFrame === "function") {
    const id = requestAnimationFrame(fn);
    return () => cancelAnimationFrame(id);
  }
  const id = setTimeout(fn, 0);
  return () => clearTimeout(id);
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
 *
 * DETACHED-CONTAINER RULE (owned here, never by a section):
 *   A canvas measured while its container is outside the document comes out
 *   0px wide, and app.js inserts a section's root only AFTER render() resolves
 *   — so almost every chart is built detached. Construction is therefore
 *   deferred to the next frame, by which time the root is in the page and
 *   Chart.js measures it correctly. The handle is returned SYNCHRONOUSLY and
 *   is fully usable before the instance exists: destroy() cancels a pending
 *   build, setFactory()/refresh() update the factory it will be built from.
 *   Sections must NOT wrap chart() in their own requestAnimationFrame.
 */
export function chart(container, factory, opts = {}) {
  const frame = el("div", { class: "chartframe", style: { height: (opts.height || 220) + "px" } });
  const canvas = el("canvas");
  append(frame, canvas);
  append(container, frame);

  if (!window.Chart) {
    frame.replaceChildren(el("div", "chart-empty",
      "Chart library did not load — /static/vendor/chart.umd.js is missing."));
    return { instance: null, setFactory() {}, refresh() {}, destroy() {} };
  }

  let fac = factory;
  let config = null;              // the live config object Chart.js holds
  let dead = false;
  let cancel = null;

  const handle = {
    instance: null,
    /** swap in new data/config (e.g. a range filter changed) */
    setFactory(next) { fac = next; handle.refresh(); },
    /** re-read the palette and redraw — called automatically on theme change */
    refresh() {
      if (dead || !handle.instance) return;      // a pending build uses `fac`
      const p = palette();
      applyDefaults(p);
      const cfg = fac(p);
      handle.instance.data = cfg.data;
      handle.instance.options = cfg.options || {};
      if (cfg.type && handle.instance.config) handle.instance.config.type = cfg.type;
      // INLINE PLUGINS ARE PART OF THE CONFIG, NOT OF options — and a custom
      // plugin closes over the palette its factory was called with. Reassigning
      // data+options alone leaves an ew-target / ew-hotbar line painting last
      // theme's colour onto this theme's canvas. Chart.js exposes `plugins` as
      // a getter only, but Config keeps the very object we passed in, so
      // mutating it here IS the supported swap; update() then invalidates the
      // plugin-descriptor cache and the new closures take over.
      if (config) config.plugins = cfg.plugins || [];
      handle.instance.update("none");
    },
    destroy() {
      dead = true;
      live.delete(handle);
      if (cancel) { cancel(); cancel = null; }
      if (handle.instance) { handle.instance.destroy(); handle.instance = null; }
      frame.remove();
    },
  };

  function build() {
    cancel = null;
    if (dead || handle.instance) return;
    // superseded before it ever reached the page (a range change / a section
    // switch): building it now would only measure a detached 0px canvas.
    if (!frame.isConnected) { live.delete(handle); return; }
    const p = palette();
    applyDefaults(p);
    config = fac(p);
    handle.instance = new window.Chart(canvas.getContext("2d"), config);
  }

  live.add(handle);
  if (frame.isConnected) build();          // already in the page: no flicker
  else cancel = nextFrame(build);
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
