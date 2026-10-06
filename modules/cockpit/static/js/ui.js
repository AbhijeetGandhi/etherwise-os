// ═══════════════════════════════════════════════════════════════════════════
// ui.js — the shared UI kit. THIS IS A CONTRACT. Sections import from here and
// never hand-roll an equivalent.
//
// ESCAPING DISCIPLINE (carried from the v1 cockpit, do not relax it):
//   * `el(tag, props, children)` sets TEXT by default. A string child becomes a
//     text node. You cannot accidentally inject markup.
//   * `props.html` is the ONLY way to set markup, and every interpolated value
//     inside it must go through `esc()`. If you find yourself writing
//     `html: someServerString` — stop, and pass it as a child instead.
//
// THE TWO NUMBER LAWS (brief §0), enforced by these helpers:
//   1. `—` means NOT MEASURED. `0` means a real zero and must carry a note.
//      Use `dash(v, fmtFn)`: null/undefined → an em dash, never "0" or "$0".
//      On a tile, pass `unmeasured: true` to `kpi()` / `metricCard()` — they
//      own the em dash AND the greying. Never hand-build a `.dash` span.
//   2. Every figure drills. Render it with `linkNum()` / `kpi()` so it is a
//      real <button>, not a div with a click handler.
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

/* ── escaping ─────────────────────────────────────────────────────────────── */

const _escDiv = document.createElement("div");

/** HTML-escape any value. null/undefined → "". */
export function esc(s) {
  _escDiv.textContent = s == null ? "" : String(s);
  return _escDiv.innerHTML;
}

/* ── DOM builder ──────────────────────────────────────────────────────────── */

/**
 * el(tag, props?, children?)
 *
 *   el("div")                                  → <div>
 *   el("div", "card")                          → <div class="card">
 *   el("span", "muted", "12 rows")             → text child, escaped by construction
 *   el("button", { class: "btn primary", on: { click: fn }, attrs: { "aria-label": "x" } })
 *   el("div", { html: `<b>${esc(name)}</b>` }) → explicit, audited markup
 *
 * props (object form) keys:
 *   class | text | html | attrs {} | on {} | style {} | dataset {} | title
 *   plus any other key is set as a direct property (id, type, value, disabled…)
 * children: a Node, a string (→ text node), or a (nested) array of those.
 */
export function el(tag, props, children) {
  const node = document.createElement(tag);
  if (typeof props === "string") {
    node.className = props;
  } else if (props && typeof props === "object") {
    for (const [k, v] of Object.entries(props)) {
      if (v == null) continue;
      if (k === "class" || k === "className") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;           // audited callers only
      else if (k === "attrs") for (const [a, b] of Object.entries(v)) { if (b != null) node.setAttribute(a, b); }
      else if (k === "on") for (const [e, f] of Object.entries(v)) node.addEventListener(e, f);
      else if (k === "style") Object.assign(node.style, v);
      else if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
  }
  append(node, children);
  return node;
}

/**
 * append(parent, ...children) — Node / string / nested array, nulls dropped.
 *
 * THE FOOTGUN THIS EXISTS FOR: native `Element.append(null)` does NOT skip the
 * null — it stringifies it and writes the literal text "null" into the page.
 * This codebase writes `cond ? node : null` on nearly every line, so a single
 * `parent.append(a, b, maybeNull)` prints "null" in the UI. ALWAYS use this
 * helper (it is variadic, so it is a drop-in for `parent.append(a, b, c)`);
 * native `.append` / `.appendChild` are only safe with values you can prove
 * are non-null. `false` is dropped too, so `flag && node` is safe as well.
 */
export function append(parent, ...children) {
  children.forEach(function add(c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) { c.forEach(add); return; }
    parent.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  });
  return parent;
}

/** Replace a node's contents in one call. */
export function clear(node) { node.replaceChildren(); return node; }

/* ── icons (inline lucide sprite; no font, no network) ────────────────────── */

/**
 * icon(name, opts?) → <svg> referencing the sprite injected by index.html.
 * opts: { size = 16, class, title }
 * Names: see the <symbol id="i-*"> list in index.html.
 */
export function icon(name, opts = {}) {
  const size = opts.size || 16;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "ico" + (opts.class ? " " + opts.class : ""));
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.appendChild(use);
  if (opts.title) svg.setAttribute("aria-label", opts.title);
  return svg;
}

/* ── formatters ───────────────────────────────────────────────────────────── */

const _num = (n, opts) => Number(n).toLocaleString("en-US", opts);

export const fmt = {
  /** $1,234 — whole dollars, minus sign as a real "−". */
  usd(n) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    const sign = Number(n) < 0 ? "−" : "";
    return sign + "$" + _num(Math.abs(Number(n)), { maximumFractionDigits: 0 });
  },
  /** $1,234.56 — used wherever rows must sum to a figure TO THE CENT. */
  usd2(n) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    const sign = Number(n) < 0 ? "−" : "";
    return sign + "$" + _num(Math.abs(Number(n)),
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  },
  /** 1,234 */
  num(n) { return n == null || Number.isNaN(Number(n)) ? "—" : _num(n); },
  /** 12.3% */
  pct(n, digits = 0) {
    return n == null || Number.isNaN(Number(n)) ? "—"
      : Number(n).toFixed(digits) + "%";
  },
  /** 2026-08-12 → "12 Aug 2026" (never a locale surprise) */
  date(iso) {
    if (!iso) return "—";
    const d = new Date(String(iso).slice(0, 10) + "T00:00:00");
    if (Number.isNaN(+d)) return String(iso).slice(0, 10);
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  },
  /** short form for dense tables: "12 Aug" */
  dateShort(iso) {
    if (!iso) return "—";
    const d = new Date(String(iso).slice(0, 10) + "T00:00:00");
    if (Number.isNaN(+d)) return String(iso).slice(0, 10);
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
  },
  /**
   * "2026-08-12 14:03" — the stored wall clock to the minute, from any of the
   * three timestamp shapes this DB holds ("…T14:03:00Z", "… 14:03:00",
   * "…T14:03:00+05:30"). It does NOT convert timezones: what you see is what
   * the row says.
   *
   * The docblock used to claim "12 Aug 14:03", which this has never returned.
   * A caller wrote `fmt.dateTime(last).slice(3)` against that claim, meaning
   * to drop a two-digit day, and shipped a health chip reading
   * "sync 6-08-13 03:15" — `.slice(3)` ate "202". If you want the short form,
   * it is `dateTimeShort` below; do not slice this one.
   */
  dateTime(iso) {
    if (!iso) return "—";
    return String(iso).replace("T", " ").slice(0, 16);
  },
  /** "12 Aug 14:03" — dateTime without the year, for chips. Same wall clock. */
  dateTimeShort(iso) {
    const s = fmt.dateTime(iso);
    if (s === "—") return s;
    const [d, t] = s.split(" ");
    const day = fmt.dateShort(d);
    return t ? `${day} ${t}` : day;
  },
  /** seconds → "1m 12s" / "340ms" */
  duration(secs) {
    if (secs == null || Number.isNaN(Number(secs))) return "—";
    const s = Number(secs);
    if (s < 1) return Math.round(s * 1000) + "ms";
    if (s < 60) return (s < 10 ? s.toFixed(1) : Math.round(s)) + "s";
    const m = Math.floor(s / 60), r = Math.round(s % 60);
    if (m < 60) return `${m}m ${r}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  },
  /**
   * Relative time from a timestamp: "3 minutes ago", "yesterday".
   * Always ends in "ago" / "in" — a bare "3w" gets misread (v1 redline).
   */
  relative(iso) {
    if (!iso) return "—";
    const t = new Date(String(iso).includes("T") ? iso : String(iso).replace(" ", "T"));
    if (Number.isNaN(+t)) return String(iso);
    const secs = (Date.now() - t.getTime()) / 1000;
    const abs = Math.abs(secs), fut = secs < 0;
    const put = (n, unit) => {
      const v = Math.round(n), u = unit + (Math.round(n) === 1 ? "" : "s");
      return fut ? `in ${v} ${u}` : `${v} ${u} ago`;
    };
    if (abs < 45) return fut ? "in a moment" : "just now";
    if (abs < 3600) return put(abs / 60, "minute");
    if (abs < 86400) return put(abs / 3600, "hour");
    if (abs < 86400 * 7) return put(abs / 86400, "day");
    if (abs < 86400 * 60) return put(abs / (86400 * 7), "week");
    return put(abs / (86400 * 30), "month");
  },
  /** whole days → "today" / "4d ago" / "3w ago". Never a bare "3w". */
  ago(days) {
    if (days == null) return "";
    if (days <= 0) return "today";
    if (days < 7) return days + "d ago";
    return Math.floor(days / 7) + "w ago";
  },
  /** a due date rendered LIVE (no relative string is ever stored) */
  due(due) {
    if (!due) return "";
    const d = new Date(String(due).slice(0, 10) + "T00:00:00");
    if (Number.isNaN(+d)) return "due " + String(due).slice(0, 10);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const days = Math.round((d - today) / 86400000);
    if (days < 0) return "overdue " + (-days) + "d";
    if (days === 0) return "due today";
    if (days === 1) return "due tomorrow";
    return "due in " + days + "d";
  },
};

/**
 * dash(value, formatter?) — the not-measured guard.
 *
 *   dash(null)                → "—"      (NOT MEASURED)
 *   dash(0)                   → "0"      (a real zero: give it a note)
 *   dash(x, fmt.usd)          → "$1,204" / "—"
 *
 * Never write `value || 0`. That is how an unmeasured thing becomes a fake
 * zero, which is the single failure this cockpit exists to avoid.
 */
export function dash(v, formatter) {
  if (v == null || v === "" || (typeof v === "number" && Number.isNaN(v))) return "—";
  return formatter ? formatter(v) : String(v);
}

/** true when the value is genuinely absent (so callers can pick a note) */
export function isUnmeasured(v) {
  return v == null || v === "" || (typeof v === "number" && Number.isNaN(v));
}

/** query-string builder; null/"" params drop out entirely. */
export function qstr(params) {
  return Object.entries(params || {})
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join("&");
}

/* ── components ───────────────────────────────────────────────────────────── */

/** A section / sub-section heading with the greyed explanatory hint. */
export function heading(title, hint, level = "h2") {
  return el("div", "section-head", [
    el(level, null, title),
    hint ? el("span", "hint", hint) : null,
  ]);
}

/** Plain card. `title` is optional; body is any child. */
export function card(title, body, opts = {}) {
  const c = el("div", { class: "card" + (opts.class ? " " + opts.class : "") });
  if (title) {
    append(c, el("div", "card-head", [
      // `plainTitle` opts out of the uppercase stat-label treatment — a client
      // name is a name, not a metric label (see .card-title.plain).
      el("h3", "card-title" + (opts.plainTitle ? " plain" : ""), title),
      opts.aside,
    ]));
  }
  append(c, body);
  return c;
}

/**
 * metricCard({label, value, note, tone, bar}) — the 4-up "at a glance" tile.
 * `value` may be a string or a Node. Pass `dash()`-guarded values.
 * `note` is where the zero explains itself; it is NOT optional for a zero.
 */
export function metricCard({ label, value, note, tone, bar, unmeasured }) {
  const v = el("div", { class: "metric" + (unmeasured ? " dash-value" : "") + (tone ? " " + tone : "") });
  append(v, value);
  return card(label, [
    v,
    bar != null ? el("div", "bar", el("span", { style: { width: Math.max(0, Math.min(100, bar)) + "%" } })) : null,
    note ? el("div", "metric-note", note) : null,
  ]);
}

/**
 * kpi({label, value, sub, onClick, warn, stale, unmeasured, plainLabel})
 * — a clickable figure. Renders a real <button>: every figure drills
 * (brief §0 rule 2).
 *
 * `unmeasured: true` is the §0 rule-1 flag, the twin of metricCard's: it greys
 * the figure and, when no `value` is supplied, renders the em dash itself. Do
 * NOT hand-build `<span class="dash">—</span>` in a section — `—` and `0` are
 * different things and exactly one helper should decide how each one looks.
 *
 * `plainLabel: true` drops the uppercase stat-label treatment (see the
 * `.caps` modifier in components.css) — for labels that are names, not metrics.
 */
export function kpi({ label, value, sub, onClick, warn, stale, unmeasured, plainLabel }) {
  const b = el("button", {
    class: "kpi" + (onClick ? "" : " static") + (stale ? " kpi-stale" : ""),
    attrs: onClick ? {} : { tabindex: "-1", "aria-disabled": "true" },
  }, [
    el("div", "lbl" + (plainLabel ? "" : " caps"), label),
    el("div", { class: "val" + (unmeasured ? " dash-value" : "") },
      unmeasured && value == null ? "—" : value),
    sub ? el("div", "sub2", sub) : null,
    onClick ? el("div", "tag", "details →") : null,
    warn ? el("div", "kpiwarn", warn) : null,
  ]);
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

/**
 * A number rendered as a drill trigger.
 *
 * `opts.label` is not decoration. Two figures on the same screen are routinely
 * the same digit ("0 queued · 0 held"), and the default label collapses both to
 * "show the rows behind 0" — a screen reader then offers two identical buttons
 * that do different things. Pass a label whenever the number alone does not say
 * WHICH figure this is.
 */
export function linkNum(text, onClick, opts = {}) {
  return el("button", {
    class: "linknum", type: "button",
    attrs: { "aria-label": opts.label || `show the rows behind ${text}` },
    on: { click: onClick },
  }, String(text));
}

/**
 * srOnly("…") — visible to a screen reader, invisible on screen.
 *
 * For the explanation a `title` tooltip cannot give: a tooltip needs a pointer
 * hover, so a keyboard user tabbing onto a DISABLED control gets the label and
 * nothing else. Pair it with `describe()` below rather than relying on `title`.
 */
export function srOnly(text) {
  return el("span", "sr-only", String(text == null ? "" : text));
}

/**
 * describe(control, node) — wire `aria-describedby` between a control and the
 * node that explains it, minting an id if the node has none. Returns the node
 * so it can be appended inline.
 */
let _descSeq = 0;
export function describe(control, node) {
  if (!node.id) node.id = "desc-" + (++_descSeq);
  const prev = control.getAttribute("aria-describedby");
  control.setAttribute("aria-describedby", prev ? prev + " " + node.id : node.id);
  return node;
}

/**
 * withBusy(trigger, fn) — run an async click handler with the trigger visibly
 * and programmatically busy.
 *
 * Every figure drills, and some drills have to FETCH their rows first. Opening
 * an empty drill and refilling it flashes "No rows behind this figure", which
 * is the one thing this cockpit must never say when it simply has not looked
 * yet. So the trigger holds the wait instead.
 */
export async function withBusy(trigger, fn) {
  if (trigger) {
    if (trigger.dataset.busy === "1") return undefined;   // no double-fire
    trigger.dataset.busy = "1";
    trigger.classList.add("is-busy");
    trigger.setAttribute("aria-busy", "true");
  }
  try {
    return await fn();
  } finally {
    if (trigger) {
      delete trigger.dataset.busy;
      trigger.classList.remove("is-busy");
      trigger.removeAttribute("aria-busy");
    }
  }
}

/** pill("owed", "info") — tone is any of the .pill modifiers in components.css */
export function pill(text, tone) {
  if (text == null || text === "") return document.createTextNode("");
  return el("span", "pill" + (tone ? " " + tone : ""), String(text));
}

/** chip("revenue") — the compact bucket tag used in money tables */
export function chip(text, tone) {
  return el("span", "chip" + (tone ? " " + tone : ""), String(text));
}

/**
 * btn("Done", fn, {variant:"primary"|"ghost"|"danger", icon:"check", sm:true})
 * `iconRight` puts a glyph AFTER the label — for "Next ›" style pairs, so a
 * back/forward pair can use the real chevrons instead of literal "←"/"→" text.
 */
/**
 * A labelled button. `opts.aria` overrides the accessible NAME — reach for it
 * whenever the visible label is not unique on the screen (six "Upload"
 * buttons whose only distinguishing text lives in a sibling element).
 */
export function btn(label, onClick, opts = {}) {
  const sz = opts.sm ? 13 : 14;
  const b = el("button", {
    class: "btn" + (opts.variant ? " " + opts.variant : "") + (opts.sm ? " sm" : "")
      + (opts.class ? " " + opts.class : ""),
    type: "button",
    attrs: Object.assign({}, opts.title ? { title: opts.title } : {},
      opts.aria ? { "aria-label": opts.aria } : {}),
    disabled: !!opts.disabled,
  }, [
    opts.icon ? icon(opts.icon, { size: sz }) : null,
    label,
    opts.iconRight ? icon(opts.iconRight, { size: sz }) : null,
  ]);
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

/** an icon-only button — always give it an aria-label */
export function iconBtn(name, label, onClick, opts = {}) {
  const b = el("button", {
    class: "btn-icon" + (opts.ghost ? " ghost" : "") + (opts.class ? " " + opts.class : ""),
    type: "button",
    attrs: { "aria-label": label, title: opts.title || label },
  }, icon(name, { size: opts.size || 16 }));
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

/** an external link that is safe by construction */
export function extLink(href, text, opts = {}) {
  if (!href) return document.createTextNode("—");
  return el("a", {
    href, target: "_blank", rel: "noopener",
    class: opts.class || "",
  }, [text, opts.noIcon ? null : icon("arrow-up-right", { size: 12 })]);
}

/**
 * table({columns, rows, empty}) → a bordered, scrollable table.
 * columns: [{ key, label, num?, render?(row) → Node|string, cls? }]
 * A missing value renders "—" (not measured), never a blank cell.
 */
export function table({ columns, rows, empty, wrapClass }) {
  if (!rows || !rows.length) return emptyState(empty || "No rows.");
  const t = el("table");
  const thead = el("thead", null, el("tr", null, columns.map((c) =>
    el("th", { class: c.num ? "num" : "" }, c.label))));
  const tbody = el("tbody");
  rows.forEach((r) => {
    const tr = el("tr");
    columns.forEach((c) => {
      const td = el("td", { class: (c.num ? "num " : "") + (c.cls || "") });
      const v = c.render ? c.render(r) : r[c.key];
      append(td, v == null || v === "" ? el("span", "dash", "—") : v);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  t.append(thead, tbody);
  return el("div", "tablewrap" + (wrapClass ? " " + wrapClass : ""), t);
}

/** emptyState("Nothing needs you right now. ✓", {icon, detail}) */
export function emptyState(text, opts = {}) {
  return el("div", "emptystate", [
    opts.icon ? icon(opts.icon, { size: 22 }) : null,
    opts.title ? el("div", "title", opts.title) : null,
    el("div", null, text),
    opts.detail ? el("div", "small", opts.detail) : null,
    opts.action || null,
  ]);
}

/** notice("warn", "…") — inline explanation lines (health, provenance, caveats) */
export function notice(tone, text, opts = {}) {
  const ic = { ok: "circle-check", warn: "alert-triangle", bad: "alert-triangle", info: "info" }[tone];
  return el("div", "notice " + (tone || ""), [
    ic ? icon(ic, { size: 15 }) : null,
    el("div", "grow", [text, opts.children || null]),
    opts.action || null,
  ]);
}

/**
 * skeleton({lines, width}) / skeletonCards(n) — load state. Never a spinner:
 * the shape of what is coming is itself information.
 */
export function skeleton({ lines = 3, height = 12, widths } = {}) {
  const box = el("div", "stack-2");
  for (let i = 0; i < lines; i++) {
    const w = widths ? widths[i % widths.length] : [92, 74, 58][i % 3] + "%";
    box.appendChild(el("div", { class: "skel", style: { height: height + "px", width: typeof w === "number" ? w + "%" : w } }));
  }
  return box;
}

export function skeletonCards(n = 4, opts = {}) {
  const g = el("div", opts.class || "grid-4");
  for (let i = 0; i < n; i++) {
    g.appendChild(el("div", "skel-card", [
      el("div", { class: "skel", style: { width: "40%", height: "10px" } }),
      el("div", { class: "skel", style: { width: "62%", height: "22px" } }),
      el("div", { class: "skel", style: { width: "85%", height: "10px" } }),
    ]));
  }
  return g;
}

/** the whole-section load state used by the router */
export function skeletonSection() {
  return el("div", "view fade-in", [
    el("div", { class: "skel", style: { width: "180px", height: "20px" } }),
    skeletonCards(4),
    el("div", "skel-card", [skeleton({ lines: 5 })]),
  ]);
}

/**
 * expander(label, loader) — a "+N more" that opens IN PLACE.
 * `loader(body)` is called once, the first time it opens; it may be async.
 */
export function expander(label, loader, opts = {}) {
  const body = el("div", { class: "expander-body", style: { display: "none" } });
  const b = el("button", {
    class: "expander-btn", type: "button",
    attrs: { "aria-expanded": "false" },
  }, [icon("chevron-right", { size: 15 }), el("span", null, label)]);
  let loaded = false;
  b.addEventListener("click", async () => {
    const open = body.style.display === "none";
    body.style.display = open ? "block" : "none";
    b.setAttribute("aria-expanded", String(open));
    if (open && !loaded) {
      loaded = true;
      clear(body).appendChild(skeleton({ lines: 3 }));
      try { clear(body); await loader(body); }
      catch (e) { clear(body).appendChild(el("div", "errstate", "Could not load: " + String(e && e.message || e))); }
    }
  });
  const node = el("div", "expander" + (opts.class ? " " + opts.class : ""), [b, body]);
  // `open: true` arrives already expanded — for the one place a collapsed
  // expander would hide the only thing the page is for (the import card on a
  // blocked Money page). It goes through the same click path, so the lazy
  // loader and aria-expanded cannot drift from a hand-rolled copy.
  if (opts.open) b.click();
  return node;
}

/* ── toast ────────────────────────────────────────────────────────────────── */

export function toast(msg, isError) {
  let host = document.getElementById("toasts");
  if (!host) { host = el("div", { class: "toasts", id: "toasts" }); document.body.appendChild(host); }
  const t = el("div", "toast" + (isError ? " toast-err" : ""), msg);
  host.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 300); }, 3800);
  return t;
}

/**
 * Copy text to the clipboard and confirm on the button that asked.
 *
 * TWO THINGS THIS GUARDS (2026-08-13, both observed live):
 *
 * 1. `navigator.clipboard.writeText` can hang. When the document is not
 *    focused — and it is not, if the click landed while a permission chip or
 *    a devtools pane had focus — Chrome neither resolves nor rejects the
 *    promise. Observed: >2.5s with no settle, so NEITHER the success path nor
 *    the catch ran and the button silently did nothing at all. A bare `await`
 *    on that promise has no timeout, so it is raced against one.
 * 2. There is a synchronous fallback that works in exactly those cases
 *    (`document.execCommand("copy")` over a hidden textarea). Deprecated, but
 *    it is the difference between a receipt you can paste and a dead button.
 *
 * Always resolves — true if the text is on the clipboard, false if it is not,
 * and false is always accompanied by something the user can see.
 */
export async function copyText(text, button, okLabel = "Copied ✓") {
  const s = text == null ? "" : String(text);
  const done = (ok) => {
    if (!ok) { toast("Could not copy — select the text and copy it by hand", true); return false; }
    if (button) flashCopied(button, okLabel); else toast("Copied to clipboard");
    return true;
  };

  let ok = false;
  try {
    const write = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(s)
      : Promise.reject(new Error("no clipboard API"));
    // TIMEOUT, the whole point: a promise that never settles must not be able
    // to leave the button in "nothing happened".
    ok = await Promise.race([
      write.then(() => true, () => false),
      new Promise((res) => setTimeout(() => res(false), 800)),
    ]);
  } catch (e) {
    ok = false;
  }
  if (ok) return done(true);
  return done(copyFallback(s));
}

/**
 * Confirm ON the control that was pressed, then put it back exactly as it was.
 *
 * `button.textContent = okLabel` was wrong for an ICON button: its text is ""
 * (the content is an <svg>), so the swap deleted the icon and "restoring" it
 * restored the empty string — a button that vanishes on success. Children are
 * saved and replaced, and an icon-only button flashes a tick instead of text.
 */
function flashCopied(button, okLabel) {
  const prev = Array.from(button.childNodes);
  const iconOnly = !String(button.textContent || "").trim();
  button.replaceChildren(iconOnly
    ? icon("check", { size: 13 })
    : document.createTextNode(okLabel));
  const said = button.getAttribute("aria-label");
  if (iconOnly && said) button.setAttribute("aria-label", "Copied");
  setTimeout(() => {
    button.replaceChildren(...prev);
    if (iconOnly && said) button.setAttribute("aria-label", said);
  }, 1400);
}

/** the synchronous path — works while the async API is hung or unavailable */
function copyFallback(s) {
  try {
    const ta = el("textarea", {
      value: s,
      attrs: { readonly: "", "aria-hidden": "true", tabindex: "-1" },
      style: { position: "fixed", top: "0", left: "-9999px", opacity: "0" },
    });
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, s.length);
    const ok = document.execCommand && document.execCommand("copy");
    ta.remove();
    return !!ok;
  } catch (e) {
    return false;
  }
}
