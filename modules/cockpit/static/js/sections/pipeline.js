// ════════════════════════════════════════════════════════════════════════════
// sections/pipeline.js — the scanner funnel, end to end.
//
// WHAT IS ON THIS PAGE, AND WHY (the copy is load-bearing; each line exists
// because a number was once misread):
//
//   · KPI strip        four figures, every one a drill. Each carries the
//                      verdict of the SOURCE behind it: a stale connects rail
//                      may not render a live-looking count, because a stale
//                      rail's 0 is indistinguishable from a real 0 (2026-07-14).
//   · win-rate line    won over every decided proposal (use case C, 26 Aug).
//   · range bar        ONE scope state (localStorage.ew_cockpit_pipeline_filters).
//                      Everything below re-scopes off it, and every chart
//                      aggregates the SAME fact rows the drills query — so a
//                      bar can never disagree with the rows behind it.
//   · funnel           topology from js/funnel.js (EW_FUNNEL.computeFunnel),
//                      which is a pure, separately unit-tested module
//                      (tests/test_pipeline_funnel.py require()s it under node).
//                      DO NOT re-implement it here: a hand-rolled version once
//                      rendered a 131% conversion rate. This file only draws.
//   · outcomes         Applied / Client replied / Hired sit OUTSIDE the funnel
//                      on purpose — three sources, three coverage windows. A
//                      conversion % between them would be a lie.
//   · score / skips /  Chart.js (charts.js), so they re-theme on the toggle.
//     weekly           Every band drills with the predicate that drew it.
//   · funnel by week   "—" = not measured that week; the funnel never rewrites
//                      the past (the outage and shadow weeks are real history).
//
// DROPPED per brief §4 — structurally dead since the live ClickUp cutover, so
// they can no longer be non-zero and a metric that cannot move is a dead metric:
//   pipeline.bands · bands.backlog_uncarded · pipeline.to_triage ·
//   pipeline_rows(stage="triage").  The server still computes them; nothing here
//   reads them, and ICP / Prime is the KPI that replaced "to triage".
//
// CONTRACT (enforced by app.js): meta + async render(root, ctx). Own nothing
// global. ui.js for primitives, drill.js for drills, charts.js for charts.
// `—` = not measured · `0` = a real zero and it says why. No send route.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, clear, dash, fmt, icon, heading, kpi, pill, linkNum, extLink,
  table, emptyState, notice, toast, qstr,
} from "../ui.js";
import { openDrill } from "../drill.js";
import { chart, axes } from "../charts.js";

export const meta = { title: "Pipeline", hint: "the scanner funnel, end to end" };

const FILTERS_KEY = "ew_cockpit_pipeline_filters";

/* ── small maths ──────────────────────────────────────────────────────────── */

const pctOf = (n, dn) => (dn ? Math.round((1000 * n) / dn) / 10 : 0);

/** "never" / "today" / "yesterday" / "4d ago" — never a bare number of days. */
const ago = (n) => (n == null ? "never"
  : n === 0 ? "today" : n === 1 ? "yesterday" : `${n}d ago`);

/** the row cap /api/pipeline/rows applies to every stage (data.pipeline_rows) */
const ROW_CAP = 300;

/** "2026-08-13" + k days, or null if the input is not an ISO day */
function dAdd(iso, k) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(iso || ""))) return null;
  return new Date(Date.parse(iso + "T00:00:00Z") + k * 864e5)
    .toISOString().slice(0, 10);
}

/**
 * A visually-hidden label INSIDE a graphical button. The funnel bars and the
 * stackbar segments are `<button>`s whose only content is colour — the number
 * lives in a sibling span — so their entire accessible name came from an
 * `aria-label`. Real text content is the more robust name (it survives
 * translation, it is what a "list all buttons" AT command reads out, and it
 * does not vanish if the attribute is dropped), so the text goes in the button
 * and the attribute goes away.
 */
function srLabel(text) {
  return el("span", {
    style: {
      position: "absolute", width: "1px", height: "1px", padding: "0",
      margin: "-1px", overflow: "hidden", clip: "rect(0 0 0 0)",
      clipPath: "inset(50%)", whiteSpace: "nowrap", border: "0",
    },
  }, text);
}

/** "2026-04" + k months */
function mAdd(ym, k) {
  let [y, m] = String(ym || "").split("-").map(Number);
  if (!y || !m) return ym;
  m += k;
  while (m < 1) { m += 12; y -= 1; }
  while (m > 12) { m -= 12; y += 1; }
  return `${y}-${String(m).padStart(2, "0")}`;
}

/** the Monday of the week an ISO date falls in */
function mondayOf(iso) {
  const dt = new Date(String(iso).slice(0, 10) + "T00:00:00");
  if (Number.isNaN(+dt)) return null;
  dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7));
  return dt.toISOString().slice(0, 10);
}

/* ── drill column sets ────────────────────────────────────────────────────────
   One definition per row SHAPE, so two figures over the same table can never
   describe it differently. */

const jobCols = [
  { key: "d", label: "Date", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "title", label: "Job",
    render: (r) => (r.job_url ? extLink(r.job_url, r.title || r.id) : (r.title || r.id)) },
  { key: "score", label: "Score", num: true },
  { key: "status", label: "Status", render: (r) => (r.status ? pill(r.status, "outline") : null) },
  { key: "clickup_task_url", label: "Card",
    render: (r) => (r.clickup_task_url ? extLink(r.clickup_task_url, "card") : null) },
];

/** the CHART drill — carries the fields a chart band is actually about */
const chartCols = [
  { key: "d", label: "Seen", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "title", label: "Job",
    render: (r) => (r.job_url ? extLink(r.job_url, r.title || r.id) : (r.title || r.id)) },
  { key: "score", label: "Score", num: true },
  { key: "budget", label: "Budget", num: true,
    render: (r) => (r.hourly_max ? "$" + r.hourly_max + "/hr"
      : (r.fixed_budget ? fmt.usd(r.fixed_budget) : null)) },
  { key: "client_total_spent", label: "Client spent", num: true,
    render: (r) => (r.client_total_spent ? fmt.usd(r.client_total_spent) : null) },
  { key: "feed_source", label: "Feed", cls: "mono" },
  { key: "icp_tier", label: "Tier",
    render: (r) => (r.icp_tier ? pill(r.icp_tier, r.icp_tier === "icp" ? "icp" : "outline") : null) },
  { key: "hard_rule_skip", label: "Skipped for",
    render: (r) => (r.hard_rule_skip ? pill(SKIP_LABEL[r.hard_rule_skip] || r.hard_rule_skip, "fail") : null) },
  { key: "clickup_task_url", label: "Card",
    render: (r) => (r.clickup_task_url ? extLink(r.clickup_task_url, "card") : null) },
];

const appliedCols = [
  { key: "d", label: "Date", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "title", label: "Job",
    render: (r) => (r.job_id
      ? extLink("https://www.upwork.com/jobs/~02" + r.job_id, r.title || r.job_id)
      : (r.title || null)) },
  { key: "label", label: "Rail entry", cls: "muted" },
  { key: "connects_delta", label: "Connects", num: true },
];

const threadCols = [
  { key: "d", label: "Started", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "modified", label: "Updated", render: (r) => el("span", "muted", fmt.dateShort(r.modified)) },
  { key: "title", label: "Job" },
  { key: "client_company", label: "Client" },
  { key: "status", label: "Status", render: (r) => (r.status ? pill(r.status, "outline") : null) },
];

const hiredCols = [
  { key: "d", label: "Started", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "title", label: "Contract" },
  { key: "client_company", label: "Client" },
  { key: "contract_type", label: "Type" },
  { key: "hourly_rate", label: "Rate", num: true,
    render: (r) => (r.hourly_rate ? "$" + r.hourly_rate + "/hr" : null) },
  { key: "status", label: "Status", render: (r) => (r.status ? pill(r.status, "outline") : null) },
];

/**
 * Human labels for hard-rule skip reasons. The REASONS THEMSELVES are always
 * derived from the data (never from this list): the strings drifted across the
 * v2/v3 scanners — 18 distinct values are live in the table right now — and a
 * hardcoded list would silently swallow the ones it forgot. Anything unmapped
 * still renders, under its raw key.
 */
const SKIP_LABEL = {
  no_rate: "hourly — no rate (pre-fix, scored as $0)",
  rate_unstated_low_client: "hourly — no rate + unproven client",
  hourly_below_30: "hourly < $30 (stated)",
  low_hourly_rate: "hourly < $30 (stated, v2 rule)",
  low_hourly: "hourly < $30 (stated, v2 rule)",
  fixed_below_500: "fixed < $500",
  fixed_below_floor: "fixed below floor (v2 rule)",
  fixed_5_placeholder: "fixed $5 placeholder",
  fixed_5_low_quality: "fixed $5, low quality",
  fixed_low_weak_client: "fixed low + weak client",
  n8n_exclusion: "n8n-only",
  outside_domain: "outside our domain",
  out_of_domain: "outside our domain (v2 rule)",
  no_asia_timezone: "no-Asia timezone",
  no_asia_tz: "no-Asia timezone (v2 rule)",
  onsite_required: "onsite required",
  already_hired: "already hired",
  already_applied: "already applied",
  client_filled: "client filled the role",
  spam: "spam",
};

/** the hourly-floor reasons whose no-rate case is split out as its own bar */
const HOURLY_SKIPS = ["hourly_below_30", "low_hourly_rate", "low_hourly"];

/* ── render ───────────────────────────────────────────────────────────────── */

/**
 * The Upwork tab's "Analysis" fold (2026-08-25, Upwork brief decision 10:
 * Pipeline BECOMES the Upwork tab). Same page, minus the blocks the new tab
 * now owns — the KPI strip, the win-rate line, the conversion funnel and the
 * outcomes tiles — so no figure lives in two places.
 */
export async function renderAnalysis(root, ctx) {
  return render(root, ctx, { analysisOnly: true });
}

export async function render(root, ctx, opts = {}) {
  const analysisOnly = !!opts.analysisOnly;
  const [d, F] = await Promise.all([
    ctx.api("/api/pipeline"),
    ctx.api("/api/pipeline/facts"),
  ]);

  /* the ONE drill path. Every figure on this page goes through it, so every
     drill states the TRUE total and admits the 300-row cap.
     ─────────────────────────────────────────────────────────────────────────
     Three of this page's figures cannot be expressed as a /api/pipeline/rows
     query, and the fix for each was previously to just... not scope the drill,
     which is how a tile reading 188 opened 2,152 rows. Two extra hooks close
     that, and neither one is allowed to soften what the drill claims:

       figure   the count the TILE arrived at, computed over the complete fact
                set. When given it becomes the drill's stated total — the true
                answer — instead of whatever this narrower endpoint could see.
       narrow   a predicate applied to the returned rows, for a cut the server
                has no filter for ("hot AND no ICP tier", "within 30 days" when
                the endpoint only takes whole months). The rows must carry the
                field being cut on, so the cut is the SAME cut, just performed
                one hop later.

     When the server truncated BEFORE the narrowing, the rows below are a
     recency-ordered SAMPLE of the figure and the drill says exactly that. When
     it did not truncate, the narrowing is exact and must reproduce `figure` —
     if it does not, the two disagree and the drill says THAT instead of
     silently showing whichever number it happens to hold. */
  async function openRows(title, params, columns, opts = {}) {
    let res;
    try {
      res = await ctx.api("/api/pipeline/rows?" + qstr(params));
    } catch (e) {
      toast("Could not read the rows behind that figure: "
        + String((e && e.message) || e), true);
      return;
    }
    if (res && res.error) { toast(String(res.error), true); return; }

    let rows = res.rows || [];
    const served = rows.length;
    const serverTotal = res.total == null ? served : res.total;
    const truncated = served >= ROW_CAP && serverTotal > served;
    const notes = [];
    if (opts.note) notes.push(opts.note);
    let capNote = opts.capNote || null;
    let total = opts.figure == null ? serverTotal : opts.figure;

    if (typeof opts.narrow === "function") {
      rows = rows.filter(opts.narrow);
      if (opts.narrowNote) notes.push(opts.narrowNote);
      if (opts.figure == null) total = rows.length;
      if (truncated) {
        capNote = `This figure's predicate cannot be sent to the rows endpoint,`
          + ` so it was asked the closest question it can answer`
          + ` (${fmt.num(serverTotal)} rows, of which it returns the`
          + ` ${fmt.num(served)} most recent) and the ${fmt.num(rows.length)}`
          + ` rows above are the ones inside the figure. The`
          + ` ${fmt.num(total)} in the header is the true count, taken over`
          + ` every scanned job; what is missing below is the OLDER members of`
          + ` that set, not a different kind of row.`;
      } else if (opts.figure != null && rows.length !== opts.figure) {
        notes.push(`These ${fmt.num(rows.length)} rows do not reproduce the`
          + ` figure of ${fmt.num(opts.figure)}. The read was not truncated, so`
          + ` the tile and the rows genuinely disagree — trust neither until`
          + ` that is resolved.`);
      }
    } else if (opts.figure != null && !truncated && opts.figure !== serverTotal) {
      notes.push(`The figure above counts ${fmt.num(opts.figure)} and this read`
        + ` finds ${fmt.num(serverTotal)}. They are not the same set.`);
    }

    openDrill({
      title,
      subtitle: opts.subtitle,
      note: notes.length ? notes.join(" ") : null,
      capNote,
      rows,
      total,
      columns,
      time: (r) => r.d,
      search: (r) => `${r.title || ""} ${r.id || ""} ${r.client_company || ""}`
        + ` ${r.feed_source || ""} ${r.hard_rule_skip || ""} ${r.status || ""}`,
      facets: opts.facets,
      emptyText: opts.emptyText || "No rows behind this figure.",
    });
  }

  const jobs = F.jobs || [];
  const HOT = F.hot_threshold || 16;
  const FLOOR = F.score_floor || 8;
  const railSince = F.rail_since;

  // the server's OWN reference day. Every window the KPI strip renders ("last
  // 7 days", "last 30 days") is measured from this, so the drills that scope
  // themselves to those windows must be measured from it too — not from the
  // browser's clock, which is in a different timezone from the IST day the
  // server counts in.
  const asOf = /^\d{4}-\d{2}-\d{2}$/.test(String(F.as_of || "")) ? F.as_of : null;

  const thisM = String(F.as_of || "").slice(0, 7)
    || [...new Set(jobs.map((j) => j.m).filter(Boolean))].sort().pop() || "";
  const lastM = mAdd(thisM, -1);

  if (!analysisOnly) {
    root.append(
      heading("Pipeline",
        "— every figure is clickable and the rows behind it are the same rows the"
        + " bar counted · applied comes from the connects rail, never from a guess"),
      kpiStrip(d, openRows, { asOf, thisM }),
      winRateLine(d),
    );
  }

  /* ══ the range-scoped half of the page ══════════════════════════════════ */

  const PRESETS = [
    ["all", "All time", () => [null, null]],
    ["this", "This month", () => [thisM, thisM]],
    ["last", "Last month", () => [lastM, lastM]],
    ["l3", "Last 3 mo", () => [mAdd(thisM, -2), thisM]],
    ["t12", "Trailing 12", () => [mAdd(thisM, -11), thisM]],
    ["ytd", "YTD", () => [String(thisM).slice(0, 4) + "-01", thisM]],
    ["custom", "Custom", null],
  ];

  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(FILTERS_KEY) || "{}"); }
  catch (e) { saved = {}; }
  let range = { preset: saved.preset || "all", from: saved.from || null, to: saved.to || null };
  if (!PRESETS.some(([k]) => k === range.preset)) {
    range = { preset: "all", from: null, to: null };
  }

  /**
   * Move one end of the custom window, and never let it end before it starts.
   *
   * THE §0 VIOLATION THIS PREVENTS (2026-08-13): the month pickers took
   * `from > to` unvalidated. Every chart below reads `inRange`, which is
   * `x.m >= from && x.m <= to` — unsatisfiable — so the page went quiet and
   * the Outcomes tiles rendered "0 — a true zero — the rail was running and
   * recorded no application". Nothing was measured. That is rule 1 asserted
   * backwards: an impossible window is not a zero, and the honest thing is to
   * make it unreachable rather than to describe its emptiness as a finding.
   *
   * The end he did NOT just touch follows the one he did (dragging the start
   * past the end collapses the window to that month, rather than silently
   * swapping his two inputs around behind him). drawSeg() repaints both
   * inputs from `range`, so the correction is visible in the pickers.
   */
  const setEdge = (edge, value) => {
    range.preset = "custom";
    range[edge] = value || null;
    if (range.from && range.to && range.from > range.to) {
      range[edge === "from" ? "to" : "from"] = range[edge];
    }
    redraw();
  };
  // …and a window restored inverted from localStorage gets the same rule
  if (range.from && range.to && range.from > range.to) range.to = range.from;

  const bounds = () => {
    const pr = PRESETS.find(([k]) => k === range.preset);
    return pr && pr[2] ? pr[2]() : [range.from, range.to];
  };
  const isAll = () => { const [f, t] = bounds(); return !f && !t; };
  const inRange = (x) => {
    const [f, t] = bounds();
    return (!f || x.m >= f) && (!t || x.m <= t);
  };
  const rangeLabel = () => {
    if (isAll()) return "all time";
    const [f, t] = bounds();
    return f === t ? f : `${f || "…"} → ${t || "…"}`;
  };

  // the scoped fact sets — every chart below reads ONLY these
  const J = () => jobs.filter(inRange);
  const A = () => (F.applies || []).filter(inRange);
  const RP = () => (F.replies || []).filter(inRange);
  const H = () => (F.hires || []).filter(inRange);

  /** the filter object a chart band hands to the drill — identical semantics */
  const scopeQ = (extra) => {
    const [f, t] = bounds();
    return Object.assign({ stage: "jobs", frm: f, to: t }, extra || {});
  };

  const jobFacets = [
    { label: "feed", allLabel: "All feeds", of: (r) => r.feed_source },
    { label: "skip", allLabel: "All reasons", of: (r) => r.hard_rule_skip,
      labelOf: (k) => SKIP_LABEL[k] || k },
  ];

  /* ── the range bar ──────────────────────────────────────────────────────── */
  const segHost = el("div", "segmented");
  const fromIn = el("input", { type: "month", attrs: { "aria-label": "range from month" } });
  const toIn = el("input", { type: "month", attrs: { "aria-label": "range to month" } });
  const customWrap = el("div", { class: "hstack-tight", style: { display: "none" } },
    [fromIn, el("span", "muted small", "→"), toIn]);
  fromIn.addEventListener("change", () => setEdge("from", fromIn.value));
  toIn.addEventListener("change", () => setEdge("to", toIn.value));

  function drawSeg() {
    clear(segHost);
    PRESETS.forEach(([key, label]) => {
      segHost.appendChild(el("button", {
        class: "segbtn" + (range.preset === key ? " on" : ""), type: "button",
        attrs: { "aria-pressed": String(range.preset === key) },
        on: {
          click: () => {
            range.preset = key;
            if (key === "custom") {
              range.from = fromIn.value || range.from || lastM;
              range.to = toIn.value || range.to || thisM;
            }
            redraw();
          },
        },
      }, label));
    });
    customWrap.style.display = range.preset === "custom" ? "flex" : "none";
    fromIn.value = range.from || "";
    toIn.value = range.to || "";
  }

  root.appendChild(el("div", "hstack", [
    segHost, customWrap,
    el("span", "muted small", analysisOnly
      ? "every chart below is scoped to this range · the Flow view has its own period"
      : "everything below is scoped to this range · the KPI strip above is not"
        + " (it is the live 7/30-day board)"),
  ]));

  const charts = el("div", { class: "stack", style: { gap: "var(--space-6)" } });
  root.appendChild(charts);

  // charts.js keeps every live handle for the theme flip, so a redraw MUST
  // destroy the ones it is replacing — an orphan whose canvas has been cleared
  // out of the DOM would be refreshed forever. The detached-container problem
  // is charts.js's (it defers construction a frame); this only tracks handles.
  let myCharts = [];

  function mountChart(host, factory, opts) {
    myCharts.push(chart(host, factory, opts));
  }

  function redraw() {
    // materialise the preset's window into `range` before anything reads it.
    // bounds() was the only thing that knew what a preset meant, while the
    // month pickers below render straight off range.from/range.to — so the
    // charts and the pickers showed different windows, and switching to Custom
    // adopted the pickers' stale one. (Same defect as money.js's; fixed there
    // as item 24.) A no-op for "custom", where bounds() returns these values.
    const [bf, bt] = bounds();
    range.from = bf || null;
    range.to = bt || null;
    // backstop for every path setEdge() does not own (the Custom segment
    // button adopts the pickers' values; a preset could in principle be
    // mis-ordered). An inverted window is empty by construction and the tiles
    // below would report that emptiness as a measured zero — see setEdge().
    if (range.from && range.to && range.from > range.to) range.to = range.from;
    localStorage.setItem(FILTERS_KEY, JSON.stringify(range));
    drawSeg();
    myCharts.forEach((h) => { try { h.destroy(); } catch (e) { /* already gone */ } });
    myCharts = [];
    clear(charts);

    const js = J();
    const rl = rangeLabel();

    charts.append(
      ...(analysisOnly ? [] : funnelRegion(js, rl)),
      ...(analysisOnly ? [] : outcomesRegion(js, rl)),
      ...scoreRegion(js, rl),
      ...skipRegion(js, rl),
      ...feedRegion(js, rl),
      ...weeklyRegion(js, rl),
      ...qualityRegion(js, rl),
    );
  }

  /* ── 1. CONVERSION FUNNEL ─────────────────────────────────────────────────
     Topology AND counts come from EW_FUNNEL.computeFunnel — every band is
     derived by narrowing the one above, so the subset law holds by
     construction and no step can read >100%. This file only renders. */
  function funnelRegion(js, rl) {
    const out = [heading("Conversion funnel",
      `— ${rl} · the scanner stages, where each band IS a subset of the one`
      + " above · click a band for its rows, or a drop-off for the jobs that"
      + " died there", "h3")];

    const EWF = window.EW_FUNNEL;
    if (!EWF || typeof EWF.computeFunnel !== "function") {
      // Never hand-roll a replacement: the 131% bug is exactly what that costs.
      out.push(el("div", "errstate",
        "funnel.js did not load, so the funnel topology is unavailable."
        + " /static/js/funnel.js is the single unit-tested definition of the"
        + " band nesting and this page will not approximate it."));
      return out;
    }

    const fnl = EWF.computeFunnel(js, { FLOOR, HOT });
    const scanned = fnl.scanned;
    const dropsAfter = {};
    (fnl.drops || []).forEach((dp) => { (dropsAfter[dp.after] ||= []).push(dp); });

    const c = el("div", "card");
    const stack = el("div", "funnel");

    (fnl.steps || []).forEach((s, i) => {
      const prev = i ? fnl.steps[i - 1] : null;
      const conv = prev && prev.n ? pctOf(s.n, prev.n) : null;
      const w = scanned ? Math.max(0.6, (100 * (s.n || 0)) / scanned) : 0;
      const last = i === fnl.steps.length - 1;

      // only the DATA is inline: how wide this band is and how far down the
      // funnel it sits. Everything else is .funnel-bar in components.css.
      // the accessible name is real (hidden) TEXT, not an attribute — the bar
      // itself carries no characters at all
      const bar = el("button", {
        class: "funnel-bar" + (last ? " last" : ""),
        type: "button",
        attrs: { title: s.note },
        style: { width: w + "%", opacity: String(1 - i * 0.08) },
        on: { click: () => openRows(`${s.lbl} — ${rl}`, scopeQ(s.scope), chartCols,
          { subtitle: s.note, facets: jobFacets }) },
      }, srLabel(`show the ${fmt.num(s.n)} rows behind ${s.lbl}`));

      append(stack, el("div", "funnel-step", [
        el("div", "funnel-head", [
          el("span", "lbl", s.lbl),
          el("span", "num", fmt.num(s.n)),
          scanned ? el("span", "muted small", pctOf(s.n, scanned) + "% of scanned") : null,
          conv != null
            ? el("span", { class: "small " + (conv < 50 ? "neg" : "muted") }, conv + "% step")
            : null,
        ]),
        bar,
      ]));

      (dropsAfter[s.k] || []).forEach((dp) => {
        if (!dp.n) return;
        append(stack, el("button", {
          class: "funnel-drop",
          type: "button",
          on: { click: () => openRows(`${dp.lbl} — ${rl}`, scopeQ(dp.scope), chartCols,
            { facets: jobFacets }) },
        }, [
          icon("chevron-down", { size: 13 }),
          el("span", "grow", `${fmt.num(dp.n)} ${dp.lbl}`),
          el("span", null, "see them →"),
        ]));
      });
    });

    append(c, stack, el("div", "muted small",
      "Every band is computed by narrowing the band above it, so a step can"
      + " never read over 100%. Carded sits ABOVE Hot on purpose: the scanner"
      + ` cards every job it scores ≥${FLOOR}, so "hot" is a property of the`
      + " carded set, not its parent."));
    out.push(c);
    return out;
  }

  /* ── OUTCOMES — deliberately NOT part of the funnel above ───────────────── */
  function outcomesRegion(js, rl) {
    const [rf] = bounds();
    const rangeStart = isAll() || !rf ? null : rf + "-01";
    // the rail only measures from its first scrape; a range that starts before
    // that is NOT MEASURED, which is not the same thing as zero.
    const railCovers = !!railSince && (isAll() ? false : rangeStart >= railSince);

    const out = [heading("Outcomes",
      `— ${rl} · what actually came back · these are NOT a subset of the funnel`
      + " above, so there is no conversion % between them", "h3")];

    const g = el("div", "grid");
    const [of_, ot] = bounds();

    const cards = [
      {
        lbl: "Applied", n: railCovers ? A().length : null,
        src: railCovers
          ? `connects-history rail · live since ${railSince || ""}`
          : "NOT MEASURED for this range — the rail only counts from "
            + (railSince || "—")
            + (railSince ? ` (${fmt.num((F.applies || []).length)} recorded since then)` : ""),
        zero: "a true zero — the rail was running and recorded no application",
        stage: "applied", cols: appliedCols,
      },
      {
        lbl: "Client replied", n: RP().length,
        src: "room-sourced threads · reaches back to 2024",
        zero: "a true zero — no proposal room was opened in this range",
        stage: "replied", cols: threadCols,
      },
      {
        lbl: "Hired", n: H().length,
        src: "contracts started · most predate the scanner",
        zero: "a true zero — no contract started in this range",
        stage: "hired", cols: hiredCols,
      },
    ];

    cards.forEach((o) => {
      append(g, kpi({
        label: o.lbl,
        value: dash(o.n, fmt.num),
        unmeasured: o.n == null,
        sub: o.n === 0 ? o.zero : o.src,
        // the drill inherits the SAME month bounds the card counted with
        onClick: o.n == null ? null : () => openRows(`${o.lbl} — ${rl}`,
          { stage: o.stage, frm: of_, to: ot }, o.cols,
          { subtitle: o.src }),
      }));
    });

    const c = el("div", "card");
    c.append(g, el("div", { class: "muted small", style: { marginTop: "var(--space-4)" } },
      "Three sources, three coverage windows — that is why they are not stacked"
      + " into the funnel. The connects rail only measures applications"
      + (railSince ? ` from ${railSince}` : "")
      + "; client replies and contracts reach back to 2024, long before the"
      + " scanner existed. A blank is “not measured”, never a zero."));
    out.push(c);
    return out;
  }

  /* ── 2. SCORE DISTRIBUTION ──────────────────────────────────────────────── */
  function scoreRegion(js, rl) {
    const out = [heading("Score distribution",
      `— ${rl} · where the model actually lands · the hot bar sits at ${HOT}`, "h3")];

    const BW = 4;
    const scoredJs = js.filter((j) => j.s != null);
    // never silently drop a job off the right-hand edge: the top bin is derived
    // from the data, with v1's 40 as the floor so the axis stays comparable.
    const maxScore = scoredJs.length ? Math.max(...scoredJs.map((j) => j.s)) : 0;
    const TOP = Math.max(40, (Math.floor(maxScore / BW) + 1) * BW);
    const hist = [];
    for (let lo = 0; lo < TOP; lo += BW) {
      hist.push({
        lo, hi: lo + BW,
        n: scoredJs.filter((j) => j.s >= lo && j.s < lo + BW).length,
      });
    }

    const c = el("div", "card");
    if (!scoredJs.length) {
      c.appendChild(emptyState("Nothing was scored in this range.", { icon: "inbox" }));
      out.push(c);
      return out;
    }

    // The hot bar, drawn as a labelled dashed line. It takes the palette its
    // factory was called with: charts.js re-creates the inline plugins from the
    // factory on every refresh, so this closure is replaced on a theme flip and
    // does not need to re-read the CSS variables at draw time.
    const hotBar = (p) => ({
      id: "ew-hotbar",
      afterDatasetsDraw(ch) {
        const idx = hist.findIndex((h) => h.lo >= HOT);
        if (idx < 1 || !ch.chartArea) return;
        const xs = ch.scales.x;
        const x = (xs.getPixelForValue(idx - 1) + xs.getPixelForValue(idx)) / 2;
        const { top, bottom } = ch.chartArea;
        const g2 = ch.ctx;
        g2.save();
        g2.setLineDash([4, 4]);
        g2.lineWidth = 1;
        g2.strokeStyle = p.target;
        g2.beginPath(); g2.moveTo(x, top); g2.lineTo(x, bottom); g2.stroke();
        g2.setLineDash([]);
        g2.fillStyle = p.target;
        g2.font = "600 11px " + (p.font || "system-ui");
        g2.textAlign = "left";
        g2.fillText("hot bar " + HOT, x + 5, top + 11);
        g2.restore();
      },
    });

    mountChart(c, (p) => ({
      type: "bar",
      data: {
        labels: hist.map((h) => String(h.lo)),
        datasets: [{
          label: "Jobs",
          data: hist.map((h) => h.n),
          backgroundColor: hist.map((h) => (h.lo >= HOT ? p.color(3) : p.soft(0))),
          borderColor: hist.map((h) => (h.lo >= HOT ? p.color(3) : p.color(0))),
          borderWidth: 1,
          borderRadius: 3,
          maxBarThickness: 46,
        }],
      },
      options: {
        scales: axes(p, { x: { title: { display: false } } }),
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => {
                const h = hist[items[0].dataIndex];
                return `scored ${h.lo}–${h.hi - 1}`;
              },
              label: (item) => [
                `${fmt.num(item.raw)} job${item.raw === 1 ? "" : "s"}`,
                hist[item.dataIndex].lo >= HOT ? "at or above the hot bar" : "below the hot bar",
                item.raw ? "click to drill down" : "a true zero for this band",
              ],
            },
          },
        },
        onClick: (evt, els) => {
          if (!els || !els.length) return;
          const h = hist[els[0].index];
          if (!h.n) return;
          openRows(`Scored ${h.lo}–${h.hi - 1} — ${rl}`,
            scopeQ({ score_min: h.lo, score_max: h.hi }), chartCols,
            { facets: jobFacets });
        },
        onHover: (evt, els) => {
          const t = evt.native && evt.native.target;
          if (t) t.style.cursor = els.length ? "pointer" : "default";
        },
      },
      plugins: [hotBar(p)],
    }), { height: 230 });

    const nScored = scoredJs.length;
    const pHot = pctOf(scoredJs.filter((j) => j.s >= HOT).length, nScored);
    c.appendChild(el("div", "muted small",
      `${fmt.num(nScored)} scored · ${pHot}% clear the hot bar of ${HOT}`
      + (pHot > 60
        ? " — the bar is a rubber stamp at this rate; ICP is the tier that"
          + " actually discriminates"
        : "")
      + ". Bars at or above the bar are filled; click any bar for its jobs."));
    out.push(c);
    return out;
  }

  /* ── 3. WHY WE SKIP ──────────────────────────────────────────────────────── */
  function skipRegion(js, rl) {
    const out = [heading("Why we skip",
      `— ${rl} · the jobs the hard floors killed before scoring · click any bar`
      + " to read what we threw away", "h3")];

    const skipped = js.filter((j) => j.k);
    const byReason = {};
    skipped.forEach((j) => {
      // an hourly-floor skip with NO stated rate is its OWN reason: the client
      // left the rate blank, Upwork ships it as $0, and the floor reads $0 as
      // cheap. That is an unknown, not a reject — it must not hide inside the
      // "too cheap" bar (2026-07-13 finding).
      const key = (j.nr && HOURLY_SKIPS.includes(j.k)) ? "no_rate" : j.k;
      byReason[key] = (byReason[key] || 0) + 1;
    });
    const reasons = Object.entries(byReason)
      .map(([key, n]) => ({ key, n, lbl: SKIP_LABEL[key] || key, flag: key === "no_rate" }))
      .sort((x, y) => y.n - x.n);

    const c = el("div", "card");
    if (!reasons.length) {
      c.appendChild(emptyState("Nothing was skipped in this range.", { icon: "circle-check" }));
      out.push(c);
      return out;
    }

    mountChart(c, (p) => ({
      type: "bar",
      data: {
        labels: reasons.map((r) => r.lbl + (r.flag ? "  ⚠ rule misfire?" : "")),
        datasets: [{
          label: "Skipped",
          data: reasons.map((r) => r.n),
          backgroundColor: reasons.map((r) => (r.flag ? p.warning : p.destructive)),
          borderRadius: 3,
          maxBarThickness: 22,
        }],
      },
      options: {
        indexAxis: "y",
        scales: {
          x: {
            beginAtZero: true,
            grid: { color: p.grid, drawBorder: false, drawTicks: false },
            border: { display: false },
            ticks: { color: p.axis, padding: 6 },
          },
          y: {
            grid: { display: false, drawBorder: false },
            border: { color: p.border },
            ticks: { color: p.axis, autoSkip: false, crossAlign: "far" },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => {
                const r = reasons[item.dataIndex];
                return [
                  `${fmt.num(r.n)} job${r.n === 1 ? "" : "s"}`
                  + ` (${pctOf(r.n, skipped.length)}% of everything skipped)`,
                  r.flag
                    ? "no rate was stated — the floor read a blank as $0, which"
                      + " is an unknown, not a reject"
                    : "raw key: " + r.key,
                  "click to drill down",
                ];
              },
            },
          },
        },
        onClick: (evt, els) => {
          if (!els || !els.length) return;
          const r = reasons[els[0].index];
          openRows(`Skipped — ${r.lbl} · ${rl}`, scopeQ({ skip: r.key }), chartCols,
            { subtitle: "raw hard_rule_skip key: " + r.key, facets: jobFacets });
        },
        onHover: (evt, els) => {
          const t = evt.native && evt.native.target;
          if (t) t.style.cursor = els.length ? "pointer" : "default";
        },
      },
    }), { height: Math.max(200, reasons.length * 30 + 44) });

    const nNoRate = byReason.no_rate || 0;
    const nUnproven = byReason.rate_unstated_low_client || 0;
    c.appendChild(el("div", "muted small",
      `${fmt.num(skipped.length)} of ${fmt.num(js.length)} scanned jobs were`
      + ` killed before scoring (${pctOf(skipped.length, js.length)}%).`
      + " The reasons are read out of the data, never from a fixed list — the"
      + " scanner's reason strings drifted across v2 and v3, and anything"
      + " unmapped shows under its raw key."
      + (nNoRate
        ? ` The amber bar is the rule misfiring: ${fmt.num(nNoRate)} historical`
          + " rows failed ONLY because the client never stated a rate — the old"
          + " $30 floor read a blank as $0. FIXED 2026-07-16 (option B): a"
          + " rate-unstated job now scores when the client is a proven spender,"
          + " and only skips (as “no rate + unproven client”"
          + (nUnproven ? `, ${fmt.num(nUnproven)} here` : "") + ") otherwise."
        : "")));
    out.push(c);
    return out;
  }

  /* ── 4. FEED ROI ─────────────────────────────────────────────────────────── */
  function feedRegion(js, rl) {
    const out = [heading("Feed ROI",
      `— ${rl} · which feed actually produces work · every count opens its`
      + " jobs", "h3")];

    const feeds = [...new Set(js.map((j) => j.f).filter(Boolean))];
    const rows = feeds.map((f) => {
      const sub = js.filter((j) => j.f === f);
      return {
        f, n: sub.length,
        hot: sub.filter((j) => j.s != null && j.s >= HOT).length,
        carded: sub.filter((j) => j.c).length,
        icp: sub.filter((j) => j.t === "icp").length,
      };
    }).sort((x, y) => y.n - x.n);

    const cell = (r, v, q, what) => (v
      ? linkNum(fmt.num(v), () => openRows(`${r.f} — ${what} · ${rl}`,
        scopeQ(Object.assign({ feed: r.f }, q)), chartCols, { facets: jobFacets }))
      : el("span", {
        attrs: { title: `a true zero — ${r.f} produced no ${what.toLowerCase()} in this range` },
      }, "0"));

    const c = el("div", "card flush");
    c.appendChild(table({
      columns: [
        { key: "f", label: "Feed", render: (r) => el("code", null, r.f) },
        { key: "n", label: "Scanned", num: true, render: (r) => cell(r, r.n, {}, "Scanned") },
        { key: "hot", label: `Hot ≥${HOT}`, num: true,
          render: (r) => cell(r, r.hot, { score_min: HOT }, `Hot ≥${HOT}`) },
        { key: "carded", label: "Carded", num: true,
          render: (r) => cell(r, r.carded, { carded: "1" }, "Carded") },
        { key: "icp", label: "ICP", num: true,
          render: (r) => cell(r, r.icp, { icp: "icp" }, "ICP") },
        { key: "rate", label: "Carded rate", num: true,
          render: (r) => el("span", "muted", pctOf(r.carded, r.n) + "%") },
      ],
      rows,
      empty: "No feed returned a job in this range.",
    }));
    append(c, el("div", "card-foot", "A 0 here is a real zero — that feed returned nothing of that kind in"
      + " this range. Carded rate is carded ÷ scanned for the feed, so a big"
      + " feed with a low rate is volume without fit."));
    out.push(c);
    return out;
  }

  /* ── 5. WEEKLY THROUGHPUT ────────────────────────────────────────────────── */
  function weeklyRegion(js, rl) {
    const byWeek = {};
    js.forEach((j) => {
      if (!j.d) return;
      const w = mondayOf(j.d);
      if (!w) return;
      const g = (byWeek[w] = byWeek[w] || { n: 0, c: 0, hot: 0 });
      g.n += 1;
      if (j.c) g.c += 1;
      if (j.s != null && j.s >= HOT) g.hot += 1;
    });
    const wks = Object.keys(byWeek).sort().slice(-13);
    if (!wks.length) return [];

    const out = [heading("Weekly throughput",
      `— ${rl} · scanned volume (bars) vs the share that reached the board`
      + ` (line) · last ${wks.length} week${wks.length === 1 ? "" : "s"} in`
      + " range", "h3")];

    const c = el("div", "card");
    mountChart(c, (p) => ({
      type: "bar",
      data: {
        labels: wks.map((w) => w.slice(5)),
        datasets: [
          {
            type: "bar", label: "Scanned", yAxisID: "y",
            data: wks.map((w) => byWeek[w].n),
            backgroundColor: p.soft(0), borderColor: p.color(0), borderWidth: 1,
            borderRadius: 3, maxBarThickness: 34,
          },
          {
            type: "line", label: "Carded %", yAxisID: "y1",
            data: wks.map((w) => pctOf(byWeek[w].c, byWeek[w].n)),
            borderColor: p.color(1), backgroundColor: p.color(1),
            borderWidth: 2, tension: 0.3, pointRadius: 2, pointHoverRadius: 4,
            order: 0,
          },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: Object.assign(axes(p), {
          y1: {
            position: "right", beginAtZero: true, suggestedMax: 100,
            grid: { display: false, drawBorder: false },
            border: { display: false },
            ticks: { color: p.axis, padding: 6, callback: (v) => v + "%" },
          },
        }),
        plugins: {
          legend: { display: true, position: "bottom" },
          tooltip: {
            callbacks: {
              title: (items) => "week of " + wks[items[0].dataIndex],
              label: (item) => {
                const g = byWeek[wks[item.dataIndex]];
                return item.dataset.yAxisID === "y1"
                  ? `${pctOf(g.c, g.n)}% reached the board`
                  : `${fmt.num(g.n)} scanned · ${fmt.num(g.hot)} hot · ${fmt.num(g.c)} carded`;
              },
            },
          },
        },
        onClick: (evt, els) => {
          if (!els || !els.length) return;
          const w = wks[els[0].index];
          openRows(`Scanned — week of ${w}`, { stage: "scanned", week: w }, jobCols,
            { subtitle: `${fmt.num(byWeek[w].n)} scanned · ${fmt.num(byWeek[w].c)} carded` });
        },
        onHover: (evt, els) => {
          const t = evt.native && evt.native.target;
          if (t) t.style.cursor = els.length ? "pointer" : "default";
        },
      },
    }), { height: 240 });

    c.appendChild(el("div", "muted small",
      "Bars = jobs scanned. Line = the share that reached the ClickUp board."
      + " A line that sinks while the bars hold means the feed got worse, not"
      + " quieter. Click a bar for that week's jobs."));
    out.push(c);
    return out;
  }

  /* ── 6. LEAD QUALITY (ICP tier) ──────────────────────────────────────────── */
  function qualityRegion(js, rl) {
    const out = [heading("Lead quality",
      `— ${rl} · the ICP overlay above the hot bar · deterministic,`
      + " recomputable, drillable", "h3")];

    const scoredJs = js.filter((j) => j.s != null);
    const tiers = [
      { k: "icp", lbl: "ICP / Prime", colour: "var(--chart-3)",
        n: scoredJs.filter((j) => j.t === "icp").length, q: { icp: "icp" },
        note: "Airtable/Make + $51/hr + rich client + long-term" },
      { k: "icp_near", lbl: "Near-miss", colour: "var(--chart-2)",
        n: scoredJs.filter((j) => j.t === "icp_near").length, q: { icp: "icp_near" },
        note: "must-haves hold, one qualifier short" },
      // "(NOT PRIME)" IS PART OF THE COUNT AND MUST BE PART OF THE QUERY. The
      // segment excludes the ICP and near-miss jobs; `score_min` alone does
      // not, so the drill used to return the segment PLUS both tiers (260 →
      // 277). The rows endpoint has no "no ICP tier" filter, so the exclusion
      // is applied to the returned rows instead — same cut, one hop later —
      // and `figure` keeps the header on the segment's own count.
      { k: "hot", lbl: `Hot ≥${HOT} (not prime)`, colour: "var(--chart-4)",
        n: scoredJs.filter((j) => j.s >= HOT && !j.t).length, q: { score_min: HOT },
        narrow: (r) => !r.icp_tier,
        narrowNote: `The rows endpoint cannot filter on "has no ICP tier", so`
          + ` the hot rows were read and the ICP and near-miss ones removed`
          + ` here — the same exclusion the "(not prime)" in this segment's`
          + ` label already makes.`,
        note: "cleared the bar, not the profile" },
      { k: "rest", lbl: `Scored < ${HOT}`, colour: "var(--chart-1)",
        n: scoredJs.filter((j) => j.s < HOT).length, q: { score_max: HOT },
        note: "below the hot bar" },
    ];
    /* every segment states its own count as the drill's total, so a segment
       and its rows can only ever disagree out loud */
    const tierOpts = (t) => ({
      subtitle: t.note, facets: jobFacets, figure: t.n,
      narrow: t.narrow, narrowNote: t.narrowNote,
    });
    const tot = Math.max(1, scoredJs.length);

    const c = el("div", "card");
    if (!scoredJs.length) {
      c.appendChild(emptyState("Nothing was scored in this range.", { icon: "inbox" }));
      out.push(c);
      return out;
    }

    // .stackbar / .stack-legend own the layout; only the DATA is inline —
    // each segment's share (flex) and which chart colour it carries.
    const stack = el("div", "stackbar");
    tiers.filter((t) => t.n).forEach((t) => {
      append(stack, el("button", {
        class: "stackbar-seg",
        type: "button",
        attrs: {
          title: `${t.lbl}: ${fmt.num(t.n)} (${pctOf(t.n, tot)}%) — ${t.note}`,
        },
        style: { flex: String(t.n), background: t.colour },
        on: { click: () => openRows(`${t.lbl} — ${rl}`, scopeQ(t.q), chartCols,
          tierOpts(t)) },
        // a bare coloured block: the name has to be text inside the button
      }, srLabel(`show the ${fmt.num(t.n)} ${t.lbl} jobs`)));
    });

    const legend = el("div", "stack-legend");
    tiers.forEach((t) => {
      append(legend, el("button", {
        class: "legend-item",
        type: "button",
        disabled: !t.n,
        attrs: { title: t.note },
        on: { click: () => { if (t.n) openRows(`${t.lbl} — ${rl}`, scopeQ(t.q), chartCols, tierOpts(t)); } },
      }, [
        el("span", { class: "legend-swatch", style: { background: t.colour } }),
        el("span", "name", t.lbl),
        el("b", "num", fmt.num(t.n)),
        el("span", null, pctOf(t.n, tot) + "%"),
      ]));
    });

    append(c, stack, legend, el("div", { class: "muted small", style: { marginTop: "var(--space-3)" } },
      "ICP is recomputed from stored fields (bin/icp-backfill) — the tier in the"
      + " DB is always the tier today's profile would award. The four segments"
      + " partition everything scored in this range; click a segment or a legend"
      + " entry for its jobs."));
    out.push(c);
    return out;
  }

  redraw();

  /* ── Funnel by week (every non-zero cell drills) ─────────────────────────── */
  root.append(
    heading("Funnel by week",
      `— scanned → scored ≥${FLOOR} → carded → applied → replied → hired · click`
      + " a cell for its rows · “—” means not measured that week", "h3"),
    funnelByWeek(d, openRows),
    heading("Threads by status",
      `— the ${fmt.num((d.client_replied || {}).total)} room-sourced proposals ·`
      + " click a status for its threads", "h3"),
    threadsByStatus(d, openRows),
  );
}

/* ── the KPI strip ────────────────────────────────────────────────────────── */

function kpiStrip(d, openRows, when) {
  const c = d.carded || {}, icp = d.icp || {}, a = d.applied || {},
    cr = d.client_replied || {};
  const asOf = (when || {}).asOf || null;
  const thisM = (when || {}).thisM || "";
  // the exact windows the server counted these tiles over (cockpit/data.py:
  // carded_between(day−6, day) and the ICP cut at day−29)
  const week7 = dAdd(asOf, -6);
  const cut30 = dAdd(asOf, -29);
  const g = el("div", "grid-4");

  // ── source-health stamp: a degraded scanner must never look live ─────────
  const sh = d.scanner_health || {};
  const scannerBad = sh.status && sh.status !== "ok";
  const scanWarn = scannerBad
    ? `⚠ scanner ${sh.status}`
      + (sh.status === "stale" && sh.age_hours != null ? ` — last run ${sh.age_hours}h ago` : "")
      + (sh.status === "degraded" ? " — feed thin, may undercount" : "")
      + "; recent numbers may be low"
    : null;

  // 1) CARDED — the 7-day window, not "today": the scanner runs all day and he
  //    reads this at 3am, when today is structurally near-zero and says nothing.
  //    The drill used to pass no window at all: the tile said 188 for seven
  //    days and opened 2,152 rows for all time. `week=<day−6>` reproduces the
  //    server's own `BETWEEN day−6 AND day` exactly (pipeline_rows turns a week
  //    anchor into anchor..anchor+6 on first_scored_at, the same column).
  g.appendChild(kpi({
    label: "Carded to board",
    value: fmt.num(c.week),
    warn: scanWarn,
    sub: `last 7 days · ${fmt.num(c.today)} today · ${fmt.num(c.last_week)} the week before`,
    onClick: () => (week7
      ? openRows(`Carded — last 7 days (${week7} → ${asOf})`,
        { stage: "carded", week: week7 }, jobCols, {
          figure: c.week,
          subtitle: "the jobs the scanner pushed to the ClickUp board in the"
            + " same seven days the tile counts",
        })
      : openRows("Carded — all recent", { stage: "carded" }, jobCols, {
        subtitle: "every job the scanner has pushed to the ClickUp board",
        note: "The server reported no usable reference day, so this could not"
          + " be narrowed to the tile's seven-day window — these rows are ALL"
          + " TIME and are not the rows the figure above counted.",
      })),
  }));

  // 2) ICP / PRIME — the tier that discriminates now ~80% clear the hot bar.
  //    (This is the KPI that replaced 'to triage', which is structurally 0
  //    since the live ClickUp cutover — see the DROPPED note at the top.)
  //    The tile counts a ROLLING 30 DAYS; the rows endpoint filters by calendar
  //    month and cannot express that, so the drill used to open all-time (19
  //    rows behind a tile reading 2). It now reads the all-time ICP set — which
  //    is small and never truncated — and applies the tile's own date cut here.
  g.appendChild(kpi({
    label: "ICP / Prime",
    value: fmt.num(icp.last_30),
    sub: `last 30 days · ${fmt.num(icp.near_30)} near-miss ·`
      + ` ${fmt.num(icp.total)} all-time · last ${ago(icp.days_since)}`
      + (icp.last_30 === 0
        ? " — a true zero: nothing matched the ICP profile in 30 days"
        : ""),
    onClick: () => (cut30
      ? openRows(`ICP / Prime — last 30 days (since ${cut30})`,
        { stage: "jobs", icp: "icp" }, chartCols, {
          figure: icp.last_30,
          narrow: (r) => (r.d || "") >= cut30,
          narrowNote: `The rows endpoint filters by calendar month and this`
            + ` tile counts a rolling 30 days, so all ${fmt.num(icp.total)}`
            + ` ICP jobs were read and cut here to first seen on or after`
            + ` ${cut30} — the same predicate, one hop later.`,
          subtitle: "recomputed from stored fields by bin/icp-backfill",
        })
      : openRows("ICP / Prime leads (all-time)",
        { stage: "jobs", icp: "icp" }, chartCols, {
          figure: icp.total,
          subtitle: "recomputed from stored fields by bin/icp-backfill",
          note: "The server reported no usable reference day, so these rows"
            + " could not be cut to the tile's 30-day window — this is the"
            + " all-time ICP set, not the figure above.",
        })),
  }));

  // 3) APPLIED — three distinct renderings, because "we did not apply" and "we
  //    were not counting" are different facts and a 0 cannot say which.
  const rs = a.source || {};
  const railStale = !!(rs.status && rs.status !== "ok");
  const appliedKpi = kpi({
    label: "Applied" + (a.available ? (railStale ? " ⚠" : "") : " (rail pending)"),
    // no rail = NOT MEASURED, and kpi() owns what that looks like
    value: a.available ? (railStale ? "stale" : fmt.num(a.week)) : null,
    unmeasured: !a.available,
    stale: railStale,
    sub: !a.available
      ? (a.rail || a.reason || "the applied-truth rail has not produced data yet")
      : railStale
        ? `NOT CURRENT — rail last ran ${rs.age_hours != null ? rs.age_hours + "h ago" : "unknown"}`
          + `${rs.max_age_hours != null ? ` (max ${rs.max_age_hours}h)` : ""};`
          + " its 0 can't be trusted until it scrapes"
        : (a.week
          ? `last 7 days · ${fmt.num(a.today)} today`
            + ` · ${fmt.num(a.last_week)} the week before`
          : "last 7 days — a TRUE zero (applications paused; rail live since"
            + ` ${a.since || "—"})`),
    // The tile counts SEVEN DAYS (data.py's _rail_applied_windows: day−6..day).
    // The drill used to pass `{stage:"applied"}` with no window at all, so a
    // tile reading 6 opened all 22 rows the rail has ever recorded — and with
    // no `figure:` either, openRows' mismatch guard could not even say so.
    // `week=<day−6>` is the SAME window: pipeline_rows turns a week anchor into
    // anchor..anchor+6 on event_date, the identical column and predicate the
    // tile's COUNT(*) used. (The Carded tile 60 lines above and the
    // Client-replies tile below were both fixed this way; this one was missed.)
    onClick: !a.available ? null
      : () => (week7
        ? openRows(`Applications — last 7 days (${week7} → ${asOf})`,
          { stage: "applied", week: week7 }, appliedCols, {
            figure: a.week,
            subtitle: a.note || "connects-history rail (daily scrape)",
            note: railStale
              ? "The rail behind these rows is stale, so the newest applications"
                + " may be missing. The count above is deliberately not shown as"
                + " a number."
              : null,
          })
        : openRows("Applications (connects rail) — all time",
          { stage: "applied" }, appliedCols, {
            subtitle: a.note || "connects-history rail (daily scrape)",
            note: "The server reported no usable reference day, so these rows"
              + " could not be cut to the tile's seven-day window — this is"
              + " EVERY application the rail has recorded, not the figure above."
              + (railStale
                ? " The rail is also stale, so the newest may be missing."
                : ""),
          })),
  });
  // a DASHED outline, on top of .kpi-stale's amber border: this tile is not
  // reporting a number, it is reporting that the number cannot be trusted, and
  // that has to be visible before you read the value.
  if (railStale) {
    appliedKpi.style.borderStyle = "dashed";
    appliedKpi.style.borderWidth = "1px";
  }
  g.appendChild(appliedKpi);

  // 4) CLIENT REPLIES — the trailing clause is RAIL-derived (server: cr.note),
  //    never a pause assumption.
  //    THREE NUMBERS FOR ONE FIGURE, until now: the tile read 0 (this month),
  //    its own sub-line read 81 (all-time) and the drill opened 73 (all-time,
  //    minus the rooms with no parseable created date). The tile's figure is
  //    the month, so the drill is the month; the all-time count stays as what
  //    it always was — context — and the all-time rooms are reachable, listed
  //    by status, from "Threads by status" at the foot of this page.
  g.appendChild(kpi({
    label: "Client replies",
    value: fmt.num(cr.this_month),
    sub: `${thisM || "this month"} · ${fmt.num(cr.total)} rooms all-time`
      + ` · last reply ${ago(cr.days_since)}`
      + (cr.note ? ` — ${cr.note}` : ""),
    onClick: () => (thisM
      ? openRows(`Client replies — ${thisM}`,
        { stage: "replied", frm: thisM, to: thisM }, threadCols, {
          figure: cr.this_month,
          subtitle: "proposal rooms the client opened this month — submissions"
            + " with no reply are invisible here",
          note: cr.this_month === 0
            ? `Empty because the figure is zero, not because the read failed:`
              + ` no client opened a room in ${thisM}. The`
              + ` ${fmt.num(cr.total)} all-time rooms named in the tile are a`
              + ` different set — "Threads by status" at the foot of this page`
              + ` lists every one of them.`
            : null,
          emptyText: `No client opened a proposal room in ${thisM}.`,
        })
      : openRows("Client replies (room-sourced proposals)",
        { stage: "replied" }, threadCols, {
          subtitle: "proposals we can see because the client opened a room —"
            + " submissions with no reply are invisible here",
          note: "No reference month was available, so these rows are ALL TIME"
            + " and are not the rows the figure above counted.",
        })),
  }));

  return g;
}

function winRateLine(d) {
  const wr = d.win_rate || {};
  return notice("info", [
    el("b", null, `Win rate ${wr.pct != null ? wr.pct : "—"}%`),
    ` (${fmt.num(wr.won)} won of ${fmt.num(wr.decided)} decided proposals)`
    + (wr.caveat ? " — " + wr.caveat : ""),
  ]);
}

/* ── funnel by week ───────────────────────────────────────────────────────── */

function funnelByWeek(d, openRows) {
  const stages = [
    ["scanned", "Scanned", "scanned", jobCols],
    ["scored", "Scored", "scored", jobCols],
    ["carded", "Carded", "carded", jobCols],
    ["applied", "Applied", "applied", appliedCols],
    ["replied", "Client replied", "replied", threadCols],
    ["hired", "Hired", "hired", hiredCols],
  ];
  const weeks = (d.funnel_weeks || []).slice().reverse();

  const cell = (w, key, label, stage, cols) => {
    const v = w[key];
    if (v == null) {
      return el("span", {
        class: "dash",
        attrs: {
          title: key === "applied"
            ? "NOT MEASURED — the connects rail was not running for this week,"
              + " so nobody was counting applications"
            : "not measured for this week",
        },
      }, "—");
    }
    if (!v) {
      return el("span", {
        attrs: { title: `measured, and it was zero — no ${label.toLowerCase()} in the week of ${w.week}` },
      }, "0");
    }
    return linkNum(fmt.num(v), () => openRows(`${label} — week of ${w.week}`,
      { stage, week: w.week }, cols));
  };

  const c = el("div", "card flush");
  c.appendChild(table({
    columns: [
      { key: "week", label: "Week",
        render: (w) => el("span", "hstack-tight", [
          el("span", null, w.week),
          w.current ? pill("current", "outline") : null,
        ]) },
      ...stages.map(([key, label, stage, cols]) => ({
        key, label, num: true, render: (w) => cell(w, key, label, stage, cols),
      })),
    ],
    rows: weeks,
    empty: "No week has been recorded yet.",
  }));
  append(c, el("div", "card-foot", "The outage (w/06-22) and the shadow weeks (carded 0) are real history —"
    + " the funnel never rewrites the past. “—” under Applied means the"
    + " connects rail was not running that week: not measured, not zero."));
  return c;
}

/* ── threads by status ────────────────────────────────────────────────────── */

function threadsByStatus(d, openRows) {
  const rows = Object.entries(d.by_status || {})
    .map(([status, n]) => ({ status, n }))
    .sort((a, b) => b.n - a.n);
  const total = rows.reduce((s, r) => s + r.n, 0);

  const c = el("div", "card flush");
  c.appendChild(table({
    columns: [
      { key: "status", label: "Status", render: (r) => pill(r.status, "outline") },
      // `stage=threads` is the ONE stage that computes no total of its own:
      // pipeline_rows falls back to `len(rows)`, which its own LIMIT 300 has
      // already truncated. A status holding more than 300 threads would drill
      // with no truncation notice at all — 300 rows presented as the whole
      // answer. The tally in this very row IS the true total, so it is what the
      // drill states, and the cap is spelled out rather than left to drill.js's
      // default (which would be silent, because the server "reported" 300 of
      // 300).
      { key: "n", label: "Threads", num: true,
        render: (r) => linkNum(fmt.num(r.n), () => openRows(`Threads — ${r.status}`,
          { stage: "threads", status: r.status }, threadCols, {
            figure: r.n,
            subtitle: `every proposal room whose status is ${r.status}`,
            capNote: r.n > ROW_CAP
              ? `The threads read is capped at ${fmt.num(ROW_CAP)} rows and`
                + ` returns no total of its own, so the ${fmt.num(r.n)} in the`
                + ` header comes from the status tally on this page — the same`
                + ` count the row you clicked shows. The rows below are the`
                + ` ${fmt.num(ROW_CAP)} most recently updated of them.`
              : null,
          })) },
      { key: "share", label: "Share", num: true,
        render: (r) => el("span", "muted", pctOf(r.n, total) + "%") },
    ],
    rows,
    empty: "No proposal thread has been recorded.",
  }));
  append(c, el("div", "card-foot", "Status falls back to the raw Upwork status when ours is blank, so nothing"
    + " reads as “Unknown” when the API gave a real state. Since 26 Aug every"
    + " proposal Upwork holds is here with its verdict (the hourly sync reads"
    + " them through the MCP), submitted-and-never-answered ones included."));
  return c;
}
