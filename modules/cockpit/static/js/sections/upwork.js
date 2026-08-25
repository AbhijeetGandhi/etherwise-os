// ════════════════════════════════════════════════════════════════════════════
// sections/upwork.js — the channel, end to end (Phase 1, 2026-08-25).
// Spec: docs/upwork/PHASE1_TAB_SPEC.md. Data: /api/upwork (+ /api/upwork/rows).
//
// WHAT IS ON THIS PAGE, TOP TO BOTTOM (the order is the Upwork brief's):
//
//   · Needs me now      the Upwork subset of the ONE act-now queue Today also
//                       reads (same builder, same Done, same snooze), Loom
//                       flags included here and hidden there; plus the counts
//                       that are counts and not cards (drafts waiting, Loom
//                       population, offers "—" until 0.5).
//   · Funnel            a JOB COHORT: jobs fetched in the period, and how far
//                       each of THOSE jobs got, joined on the one job id every
//                       table carries. Server-computed — this file only draws.
//                       "—" is a stage with no spine row yet (viewed: 0.2,
//                       offer: 0.5); it is skipped in the conversion chain,
//                       never inferred, never a fabricated 0. A % is SIGNAL
//                       only when both sides of the comparison clear the
//                       small-N line; below it the number is typeset as an
//                       anecdote with its n and no arrow (brief law 6).
//   · contracts line    the wins the cohort funnel would hide: contracts that
//                       started this period whose job was fetched earlier, or
//                       never fetched at all. Printed every time, zero or not.
//   · Rails             every Upwork rail with its source-health verdict and
//                       latest run; a rail that is not built yet says so.
//   · Connects          counts only (dollars live on Money): applications +
//                       boosts net of boost refunds, per application, per
//                       interview.
//   · Learning          the Phase 2 digest slot — an explicit not-built state.
//   · Analysis          the former Pipeline charts, below the fold.
//
// CONTRACT (enforced by app.js): meta + async render(root, ctx). Own nothing
// global. ui.js for primitives, drill.js for drills, today.js for the card.
// `—` = not measured · `0` = a real zero and it says why. No send route.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, clear, fmt, icon, heading, kpi, pill, emptyState, notice, toast,
  qstr, expander, btn, extLink,
} from "../ui.js";
import { openDrill } from "../drill.js";
import { queueCard } from "./today.js";
import { renderAnalysis } from "./pipeline.js";

export const meta = { title: "Upwork", hint: "the channel, end to end" };

const VIEW_KEY = "ew_cockpit_upwork_view";          // "week" | "month"
const ANALYSIS_KEY = "ew_cockpit_upwork_analysis";  // "open" | "closed"
const ROW_CAP = 300;

const STATUS_TONE = { ok: "ok", degraded: "degraded", stale: "stale", down: "down" };

function readView() {
  try { return localStorage.getItem(VIEW_KEY) === "month" ? "month" : "week"; }
  catch (e) { return "week"; }
}

function srLabel(text) {
  return el("span", "sr-only", text);
}

/* ── the ONE drill path ───────────────────────────────────────────────────── */

async function openRows(ctx, title, params, columns, opts = {}) {
  let res;
  try {
    res = await ctx.api("/api/upwork/rows?" + qstr(params));
  } catch (e) {
    toast("Could not read the rows behind that figure: "
      + String((e && e.message) || e), true);
    return;
  }
  if (res && res.error) { toast(String(res.error), true); return; }
  const rows = res.rows || [];
  const total = res.total == null ? rows.length : res.total;
  openDrill({
    title,
    subtitle: opts.subtitle,
    note: opts.note || null,
    rows, total, columns,
    time: opts.time || ((r) => r.d),
    search: (r) => `${r.title || ""} ${r.id || ""} ${r.client_company || ""}`
      + ` ${r.feed_source || ""} ${r.kind || ""} ${r.label || ""} ${r.status || ""}`,
    facets: opts.facets,
    amount: opts.amount,
    capNote: total > rows.length
      ? `${fmt.num(total)} rows behind this figure; the ${fmt.num(ROW_CAP)} newest are shown.`
      : null,
    emptyText: opts.emptyText || "No rows behind this figure.",
  });
}

const jobLink = (r) => (r.job_url
  ? extLink(r.job_url, r.title || r.id, { title: "open on Upwork" })
  : el("span", null, r.title || r.id));

const JOB_COLS = [
  { key: "title", label: "Job", render: jobLink },
  { key: "score", label: "Score", num: true,
    render: (r) => el("span", null, r.score == null ? "—" : String(r.score)) },
  { key: "band", label: "Band", render: (r) => pill(r.band || "?", "outline") },
  { key: "feed_source", label: "Feed", cls: "muted" },
  { key: "d", label: "Fetched", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "clickup_task_url", label: "Card",
    render: (r) => (r.clickup_task_url ? extLink(r.clickup_task_url, "ClickUp") : el("span", "muted", "—")) },
];

const OUTCOME_COLS = [
  { key: "title", label: "Job", render: jobLink },
  { key: "score", label: "Score", num: true,
    render: (r) => el("span", null, r.score == null ? "—" : String(r.score)) },
  { key: "applied_on", label: "Applied",
    render: (r) => el("span", r.applied_on ? null : "muted",
      r.applied_on ? fmt.dateShort(r.applied_on) : (r.inferred ? `inferred from ${r.inferred}` : "—")) },
  { key: "upwork_status", label: "Proposal", cls: "muted",
    render: (r) => el("span", "muted", r.upwork_status || "—") },
  { key: "contract_start", label: "Contract",
    render: (r) => el("span", r.contract_start ? null : "muted",
      r.contract_start ? fmt.dateShort(r.contract_start) : "—") },
  { key: "d", label: "Fetched", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
];

const CONTRACT_COLS = [
  { key: "title", label: "Contract", render: (r) => el("span", null, r.title || r.id) },
  { key: "client_company", label: "Client", cls: "muted" },
  { key: "cohort", label: "Job",
    render: (r) => pill(r.cohort === "outside" ? "never fetched"
      : r.cohort === "this" ? "this cohort" : `cohort ${r.cohort_key || r.cohort}`,
    r.cohort === "outside" ? "warn" : "outline") },
  { key: "contract_type", label: "Type", cls: "muted" },
  { key: "d", label: "Started", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
];

const CONNECTS_COLS = [
  { key: "d", label: "Date", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "kind", label: "Kind", render: (r) => pill(r.kind, "outline") },
  { key: "label", label: "Action" },
  { key: "title", label: "Job", cls: "muted" },
  { key: "connects_delta", label: "Connects", num: true,
    render: (r) => el("span", r.connects_delta >= 0 ? "pos" : "neg", String(r.connects_delta)) },
];

/* ── render ───────────────────────────────────────────────────────────────── */

export async function render(root, ctx) {
  const d = await ctx.api("/api/upwork");
  const state = { view: readView() };

  root.append(heading("Upwork",
    `— as of ${fmt.dateShort(d.as_of)} · every figure drills · "—" means not`
    + " measured yet, never zero"));

  root.append(needsMe(d, ctx));

  // the week/month toggle re-scopes the funnel AND the per-application /
  // per-interview tiles; the two hosts redraw together, nothing refetches
  const funnelHost = el("div");
  const connectsHost = el("div");
  const redraw = () => {
    clear(funnelHost).append(...funnelBlock(d, ctx, state, redraw));
    clear(connectsHost).append(connectsBlock(d, ctx, state));
  };
  root.append(funnelHost, railsBlock(d, ctx), connectsHost, digestBlock(d), analysisFold(ctx));
  redraw();
}

/* ── 1. Needs me now ──────────────────────────────────────────────────────── */

function needsMe(d, ctx) {
  const nm = d.needs_me || {};
  const cards = nm.cards || [];
  const out = el("div", "stack");
  out.append(heading("Needs me now",
    "— every Upwork card, Loom flags included (Today hides those) · one Done, one snooze,"
    + " honoured on both screens", "h3"));

  // counts first (brief law 5): the cluster line + the tiles that are counts
  const clusters = (nm.clusters || []).map((c) => `${fmt.num(c.count)} ${c.label}`);
  const sn = nm.snoozed || {};
  out.append(el("div", "q-clusters", [
    el("span", null, clusters.length ? clusters.join(" · ") : "no Upwork cards"),
    sn.count ? el("span", "muted", ` · ${sn.count} snoozed${sn.next_back ? ", back " + fmt.dateShort(sn.next_back) : ""}`) : null,
    nm.hidden_no_provenance ? el("span", "muted", ` · ${nm.hidden_no_provenance} hidden (no evidence)`) : null,
  ]));

  const dw = nm.drafts_waiting || {};
  const lf = nm.loom_flags || {};
  const offers = nm.offers || {};
  out.append(el("div", "grid", [
    kpi({
      label: "Drafts waiting", value: fmt.num(dw.count),
      sub: dw.note || "",
      onClick: () => openRows(ctx, "Drafted proposals waiting on the board", { stage: "drafts_waiting" },
        JOB_COLS, { subtitle: dw.note, facets: [
          { label: "band", allLabel: "All bands", of: (r) => r.band },
          { label: "ICP", allLabel: "All tiers", of: (r) => r.icp_tier || "none" }] }),
    }),
    kpi({
      label: "Loom-worthy, open", value: fmt.num(lf.count),
      sub: (lf.note || "") + (lf.icp ? ` · ${lf.icp} ICP` : ""),
      onClick: () => openRows(ctx, "Loom-flagged jobs still open", { stage: "loom_flags" },
        JOB_COLS, { subtitle: lf.note, facets: [
          { label: "ICP", allLabel: "All tiers", of: (r) => r.icp_tier || "none" },
          { label: "band", allLabel: "All bands", of: (r) => r.band }] }),
    }),
    kpi({
      label: "Offers awaiting you", value: null, unmeasured: true,
      sub: offers.reason || "not measured",
    }),
  ]));

  if (!cards.length) {
    const rails = d.rails || {};
    const ok = rails.worst === "ok";
    out.append(emptyState(ok
      ? "Nothing on Upwork needs you right now — and every rail is fresh, so that is a real quiet."
      : `No Upwork card is open, but the rails are ${rails.worst || "unknown"} — quiet is not yet proven.`,
    { icon: ok ? "circle-check" : "alert-triangle" }));
    return out;
  }

  // a person waiting beats a Loom the scorer would like (spec §5.2 order):
  // replies owed and escalations first, Loom flags after — each group keeps
  // the engine's urgency order inside it
  const isLoom = (it) => it.nudge_kind === "hotlead_no_loom";
  const ordered = [...cards.filter((it) => !isLoom(it)), ...cards.filter(isLoom)];
  const shown = nm.shown_default || 5;
  const list = el("div", "qlist");
  ordered.slice(0, shown).forEach((it) => list.appendChild(queueCard(it, ctx)));
  if (ordered.length > shown) {
    list.appendChild(expander(`show all ${fmt.num(ordered.length)} Upwork cards`, async (body) => {
      ordered.slice(shown).forEach((it) => body.appendChild(queueCard(it, ctx)));
    }));
  }
  out.append(list);
  return out;
}

/* ── 2. Funnel ────────────────────────────────────────────────────────────── */

function stepText(s) {
  /* "60% of hot (n 43) · +1.5 pp vs 8-wk" — SIGNAL gets the tone and the
     arrow; ANECDOTE gets its n and nothing dressed up as a trend */
  const op = s.of_prev;
  if (!op || op.pct == null) return null;
  const parts = [el("span", null, `${op.pct}% of ${op.k} (n ${fmt.num(op.n_den)})`)];
  const bl = s.baseline || {};
  if (bl.pct != null) {
    const delta = s.delta_pp;
    if (s.signal && delta != null) {
      parts.push(el("span", { class: "small " + (delta >= 0 ? "pos" : "neg") },
        ` ${delta >= 0 ? "▲" : "▼"} ${Math.abs(delta)} pp vs baseline ${bl.pct}%`));
    } else {
      parts.push(el("span", "small anecdote",
        ` · baseline ${bl.pct}% (n ${fmt.num(bl.n_den)}) — anecdote, not a trend`));
    }
  } else if (bl.covered_since === null && bl.n_den === 0) {
    parts.push(el("span", "small anecdote", " · no measured baseline"));
  }
  return el("span", "small muted", parts);
}

function funnelBlock(d, ctx, state, redraw) {
  const view = state.view;
  const f = (d.funnel || {})[view] || {};
  const stages = f.stages || [];
  const period = f.period || {};
  const bl = f.baseline || {};
  const key = period.key;
  const periodParam = view === "week" ? { week: key } : { month: key };

  const seg = el("div", "segmented", ["week", "month"].map((v) => el("button", {
    class: "segbtn" + (view === v ? " on" : ""), type: "button",
    attrs: { "aria-pressed": String(view === v) },
    on: { click: () => {
      state.view = v;
      try { localStorage.setItem(VIEW_KEY, v); } catch (e) { /* private mode */ }
      redraw();
    } },
  }, v)));

  const out = [
    heading("Funnel",
      `— ${period.label || key}${period.so_far ? " (so far)" : ""} · the jobs fetched in this`
      + ` ${view}, and how far each of them got · baseline = the ${bl.periods} completed`
      + ` ${view}s before it, pooled${bl.rail_since ? ` (outcome steps since the rail began ${fmt.dateShort(bl.rail_since)})` : ""}`,
      "h3"),
    el("div", "hstack", [seg, el("span", "muted small",
      "a step is a trend only when both sides have n ≥ " + fmt.num(f.signal_min_n || 30)
      + " — otherwise it is printed as what it is: an anecdote")]),
  ];

  const card = el("div", "card");
  const stack = el("div", "funnel");
  const fetched = (stages[0] && stages[0].n) || 0;
  stages.forEach((s, i) => {
    const measured = !!s.measured;
    const w = measured && fetched ? Math.max(0.6, (100 * (s.n || 0)) / fetched) : 0;
    const head = el("div", "funnel-head", [
      el("span", "lbl", s.label),
      el("span", measured ? "num" : "num dash-value", measured ? fmt.num(s.n) : "—"),
      measured ? stepText(s) : el("span", "small muted anecdote", s.reason || "not measured"),
    ]);
    const children = [head];
    if (measured) {
      const bar = el("button", {
        class: "funnel-bar" + (s.k === "won" ? " last" : ""), type: "button",
        attrs: { title: `${s.label}: ${fmt.num(s.n)} — click for the rows` },
        style: { width: w + "%", opacity: String(1 - i * 0.06) },
        on: { click: () => openRows(ctx, `${s.label} — ${period.label || key}`,
          Object.assign({ stage: s.k }, periodParam),
          ["applied", "interviewed", "won"].includes(s.k) ? OUTCOME_COLS : JOB_COLS,
          { subtitle: `jobs fetched in this ${view} that reached "${s.label}"`,
            facets: [{ label: "band", allLabel: "All bands", of: (r) => r.band },
              { label: "feed", allLabel: "All feeds", of: (r) => r.feed_source }] }) },
      }, srLabel(`show the ${fmt.num(s.n)} jobs behind ${s.label}`));
      children.push(bar);
    } else {
      children.push(el("div", "funnel-bar-unmeasured", srLabel(`${s.label} is not measured yet`)));
    }
    // the sub-lines that keep the numbers honest
    if (s.k === "hot" && measured) {
      children.push(el("div", "small muted funnel-sub", [
        `Loom-band ${fmt.num(s.loom)} · carded ${fmt.num(s.carded)}`,
        s.missed_push ? el("button", { class: "linknum", type: "button",
          on: { click: () => openRows(ctx, "Hot jobs that missed the ClickUp push",
            Object.assign({ stage: "hot_missed_push" }, periodParam), JOB_COLS,
            { subtitle: "the next scan retries these" }) } },
        ` · ${fmt.num(s.missed_push)} missed the push →`) : null,
      ]));
    }
    if (s.k === "applied" && measured) {
      const hs = s.hot_split || {};
      children.push(el("div", "small muted funnel-sub",
        `${fmt.num(hs.hot)} hot · ${fmt.num(hs.not_hot)} below the hot bar`
        + (s.inferred ? ` · ${fmt.num(s.inferred)} inferred from a later stage (the rail missed the application)` : "")));
    }
    if (s.k === "interviewed" && measured && s.inferred) {
      children.push(el("div", "small muted funnel-sub",
        `${fmt.num(s.inferred)} inferred from a contract (no proposal row)`));
    }
    append(stack, el("div", "funnel-step", children));
  });
  append(card, stack);

  // the contracts context line — the wins a cohort funnel would hide
  const c = f.contracts_started || {};
  append(card, el("div", "funnel-contracts", [
    el("button", { class: "linknum", type: "button",
      on: { click: () => openRows(ctx, `Contracts started — ${period.label || key}`,
        Object.assign({ stage: "contracts_started" }, periodParam), CONTRACT_COLS,
        { subtitle: c.note, time: (r) => r.d }) } },
    `contracts started this ${view}: ${fmt.num(c.total)}`),
    el("span", "muted small",
      ` — ${fmt.num(c.this)} from this cohort · ${fmt.num(c.earlier)} from earlier cohorts`
      + ` · ${fmt.num(c.outside)} outside the funnel (job never fetched)`),
  ]));
  append(card, el("div", "muted small",
    "Every later stage is a subset of the one before it by construction (a contract proves an"
    + " interview proves an application), so no step can read over 100 %. A stage marked — has"
    + " no data source yet and is skipped in the chain, never inferred."));
  out.push(card);
  return out;
}

/* ── 3. Rails ─────────────────────────────────────────────────────────────── */

function railsBlock(d, ctx) {
  const rails = d.rails || {};
  const items = rails.items || [];
  const out = el("div", "stack");
  out.append(heading("Rails",
    `— ${rails.worst === "ok" ? "all fresh" : "worst: " + (rails.worst || "unknown")}`
    + " · click a rail for its latest run · re-run where the registry allows it", "h3"));
  const strip = el("div", "rail-strip");
  items.forEach((r) => {
    const tone = r.built === false ? "nobuild" : (STATUS_TONE[r.status] || "down");
    const when = r.last_success ? fmt.dateShort(r.last_success) + " " + String(r.last_success).slice(11, 16) : "never";
    const title = r.built === false ? r.reason
      : `${r.label}: ${r.status || "?"} · last success ${when}`
        + (r.age_hours != null ? ` (${r.age_hours}h ago, ${r.cadence || ""})` : "")
        + (r.result ? ` · ${r.result}` : "") + (r.reason ? ` · ${r.reason}` : "");
    const b = el("button", {
      class: "rail-btn " + tone, type: "button", attrs: { title },
      on: { click: () => {
        if (r.built === false) { toast(r.reason || "not built yet"); return; }
        if (r.run_id && r.day) ctx.navigate("activity", { day: r.day, run: String(r.run_id) });
        else toast(`${r.label}: no run recorded yet`);
      } },
    }, [
      el("span", "dot2"),
      el("span", "lbl", r.label),
      el("span", "st", r.built === false ? "not built" : (r.status || "—")),
      r.derived ? el("span", "muted", "derived") : null,
    ]);
    const wrap = el("div", "rail-item", [b]);
    if (r.rerun && r.task_name) {
      const rb = btn("re-run", (ev) => ctx.kick("/api/rerun", { task: r.task_name }, ev.currentTarget),
        { sm: true, class: "ghost", title: `launchctl kickstart ${r.task_name}` });
      wrap.appendChild(rb);
    }
    strip.appendChild(wrap);
  });
  out.append(strip);
  const bad = items.filter((r) => r.built !== false && r.status && r.status !== "ok");
  if (bad.length) {
    out.append(notice("warn", bad.map((r) => `${r.label} is ${r.status}`
      + (r.reason ? ` — ${r.reason}` : "")).join(" · ")));
  }
  return out;
}

/* ── 4. Connects ──────────────────────────────────────────────────────────── */

function connectsBlock(d, ctx, state) {
  const c = d.connects || {};
  const wk = c.week || {};
  const mo = c.month || {};
  const src = c.source || {};
  const stale = !!(src.status && src.status !== "ok");
  const out = el("div", "stack");
  out.append(heading("Connects",
    "— counts only, dollars live on Money · spent = applications + boosts, net of boost refunds"
    + (stale ? ` · the connects rail is ${src.status} — these are not current` : ""), "h3"));
  const drill = (w, title) => () => openRows(ctx, title, { stage: "connects", frm: w.lo, to: w.hi },
    CONNECTS_COLS, { subtitle: `${fmt.dateShort(w.lo)} → ${fmt.dateShort(w.hi)}`,
      amount: (r) => r.connects_delta, time: (r) => r.d,
      facets: [{ label: "kind", allLabel: "All kinds", of: (r) => r.kind }] });
  const per = state.view === "month" ? mo : wk;
  const perLabel = state.view === "month" ? "this month" : "last 7 days";
  out.append(el("div", "grid-4", [
    kpi({ label: "Spent, last 7 days", value: fmt.num(wk.spent_net), stale,
      sub: `${fmt.num(wk.applications)} applications · boosts ${fmt.num(wk.boost_cost)} − refunds ${fmt.num(wk.boost_refunds)} · badge ${fmt.num(wk.badge)}`,
      onClick: drill(wk, "Connects — last 7 days") }),
    kpi({ label: "Spent, this month", value: fmt.num(mo.spent_net), stale,
      sub: `${fmt.num(mo.applications)} applications · boosts ${fmt.num(mo.boost_cost)} − refunds ${fmt.num(mo.boost_refunds)} · +${fmt.num(mo.inflows)} bought/renewed`,
      onClick: drill(mo, "Connects — this month") }),
    kpi({ label: `Per application, ${perLabel}`,
      value: per.per_application == null ? null : fmt.num(per.per_application),
      unmeasured: per.per_application == null, stale,
      sub: per.applications ? `n = ${fmt.num(per.applications)}` : "no applications in the window",
      onClick: drill(per, `Connects — ${perLabel}`) }),
    kpi({ label: `Per interview, ${perLabel}`,
      value: per.per_interview == null ? null : fmt.num(per.per_interview),
      unmeasured: per.per_interview == null, stale,
      sub: per.interviews
        ? `n = ${fmt.num(per.interviews)}${per.interviews < (per.signal_min_n || 30) ? " — anecdote" : ""}`
        : "no interview started in the window",
      onClick: drill(per, `Connects — ${perLabel}`) }),
  ]));
  return out;
}

/* ── 5. Learning digest slot ──────────────────────────────────────────────── */

function digestBlock(d) {
  const dg = d.digest || {};
  const out = el("div", "stack");
  out.append(heading("This week's learning", "— the Loop cards render here", "h3"));
  out.append(emptyState(dg.built ? "" : "Nothing is measured here yet.",
    { detail: dg.reason || "Phase 2", icon: "book-open" }));
  return out;
}

/* ── 6. Analysis (the former Pipeline page) ───────────────────────────────── */

function analysisFold(ctx) {
  const ex = expander("Analysis — score distribution · why we skip · feed ROI · weekly throughput · lead quality",
    async (body) => { await renderAnalysis(body, ctx); });
  const b = ex.querySelector(".expander-btn");
  if (b) {
    b.addEventListener("click", () => {
      try { localStorage.setItem(ANALYSIS_KEY, b.getAttribute("aria-expanded") === "true" ? "open" : "closed"); }
      catch (e) { /* private mode */ }
    });
    let remembered = "closed";
    try { remembered = localStorage.getItem(ANALYSIS_KEY) || "closed"; } catch (e) { /* ignore */ }
    if (remembered === "open") setTimeout(() => b.click(), 0);
  }
  return ex;
}
