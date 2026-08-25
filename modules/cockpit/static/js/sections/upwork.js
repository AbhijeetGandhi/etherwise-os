// ════════════════════════════════════════════════════════════════════════════
// sections/upwork.js — the channel, end to end, in four views (2026-08-25 night,
// redesigned on Abhijeet's verdict on the first cut: "too complicated for me
// to understand anything … it all should be intuitive, not a lot of data laid
// out without any structure"). Spec: docs/upwork/PHASE1_TAB_SPEC.md; data:
// /api/upwork (+ /api/upwork/rows). The four views are sub-tabs of ONE page —
// one fetch, switched client-side, remembered in the hash (#/upwork?view=…):
//
//   Inbox     the people waiting on him: one conversation row per person (see
//             ../conversation.js — the same row Today renders), reply owed →
//             gone quiet → probably resolved, longest wait first, the first
//             row open. Everything else on the Upwork queue sits under it.
//   Jobs      this week's (or month's) jobs as six plain numbers in a flow,
//             "usually N%" in small grey instead of statistics, the contracts
//             that started called out by name, then the job lists as a table.
//   Health    every rail in plain words: what it does, when it last ran, OK
//             or "needs a look" with the reason, and Run now where allowed.
//   Analysis  the former Pipeline charts (pipeline.renderAnalysis).
//
// COPY RULES (the whole point of the redesign): no statistics vocabulary on
// the screen — no "n ≥ 30", "pp", "baseline", "cohort", "anecdote". A number
// gets a plain label; a comparison reads "usually 44%"; an arrow appears only
// when the server says the change is real (stage.signal). "—" still means not
// measured, never zero. Every figure still drills to its rows.
//
// CONTRACT (enforced by app.js): meta + async render(root, ctx). Own nothing
// global. ui.js for primitives, drill.js for drills. No send route.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, clear, fmt, icon, pill, emptyState, notice, toast, qstr, expander, btn,
  extLink, table, kpi,
} from "../ui.js";
import { openDrill } from "../drill.js";
import { queueCard } from "./today.js";
import { conversationRow } from "../conversation.js";
import { renderAnalysis } from "./pipeline.js";

export const meta = { title: "Upwork", hint: "the channel, end to end" };

const TAB_KEY = "ew_cockpit_upwork_tab";            // inbox | jobs | health | analysis
const VIEW_KEY = "ew_cockpit_upwork_view";          // week | month
const LIST_KEY = "ew_cockpit_upwork_list";          // which job list is open
const ROW_CAP = 300;
const TABLE_PEEK = 12;                              // rows shown inline before "show all"

const TABS = [
  { id: "inbox", label: "Inbox" },
  { id: "jobs", label: "Jobs" },
  { id: "health", label: "Health" },
  { id: "analysis", label: "Analysis" },
];

function remember(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
}
function recall(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
}

/* ── the ONE drill path ───────────────────────────────────────────────────── */

async function fetchRows(ctx, params) {
  let res;
  try {
    res = await ctx.api("/api/upwork/rows?" + qstr(params));
  } catch (e) {
    toast("Could not read the rows behind that figure: " + String((e && e.message) || e), true);
    return null;
  }
  if (res && res.error) { toast(String(res.error), true); return null; }
  return res;
}

async function openRows(ctx, title, params, columns, opts = {}) {
  const res = await fetchRows(ctx, params);
  if (!res) return;
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

/* ── columns ──────────────────────────────────────────────────────────────── */

const jobLink = (r) => (r.job_url
  ? extLink(r.job_url, r.title || r.id, { title: "open on Upwork" })
  : el("span", null, r.title || r.id));

function clientCell(r) {
  const bits = [];
  if (r.client_country) bits.push(r.client_country === "United States" ? "US" : r.client_country === "United Kingdom" ? "UK" : r.client_country);
  if (r.client_total_spent != null && Number(r.client_total_spent) > 0) {
    const k = Number(r.client_total_spent);
    bits.push((k >= 1000 ? "$" + Math.round(k / 1000) + "k" : "$" + Math.round(k)) + " spent");
  }
  if (r.client_hires != null && Number(r.client_hires) > 0) bits.push(fmt.num(r.client_hires) + " hires");
  if (bits.length === 1 && r.client_country) bits.push("new client");
  return bits.length ? el("span", "muted", bits.join(" · ")) : null;
}

function budgetCell(r) {
  if (r.fixed_budget != null && Number(r.fixed_budget) > 0) return fmt.usd(r.fixed_budget) + " fixed";
  if (r.hourly_min != null || r.hourly_max != null) {
    const lo = r.hourly_min != null ? "$" + Math.round(Number(r.hourly_min)) : null;
    const hi = r.hourly_max != null ? "$" + Math.round(Number(r.hourly_max)) : null;
    return (lo && hi ? `${lo}–${hi}` : (lo || hi)) + "/h";
  }
  return el("span", "muted", "not stated");
}

const yes = (cond, label) => (cond
  ? el("span", "pos", ["✓ ", label || ""]) : el("span", "muted", "—"));

const JOB_COLS = [
  { key: "title", label: "Job", render: (r) => el("div", "stack-1", [
    jobLink(r), el("span", "small muted", `scored ${fmt.dateShort(r.d)}`)]) },
  { key: "score", label: "Score", num: true,
    render: (r) => el("span", "score-cell", [el("i", "dot-" + (r.band || "x")), r.score == null ? "—" : String(r.score)]) },
  { key: "client", label: "Client", render: clientCell },
  { key: "budget", label: "Budget", render: budgetCell },
  { key: "has_draft", label: "Draft",
    render: (r) => yes(r.has_draft || r.draft_word_count, r.draft_word_count ? `${fmt.num(r.draft_word_count)} words` : "written") },
  { key: "clickup_task_url", label: "Card",
    render: (r) => (r.clickup_task_url ? extLink(r.clickup_task_url, "ClickUp") : el("span", "muted", "—")) },
  { key: "applied_raw", label: "Applied",
    render: (r) => (r.applied_on ? el("span", "pos", fmt.dateShort(r.applied_on))
      : r.applied_raw ? el("span", "pos", "yes")
        : r.inferred ? el("span", "muted", `yes (${r.inferred})`) : el("span", "muted", "—")) },
];

const OUTCOME_COLS = [
  { key: "title", label: "Job", render: jobLink },
  { key: "score", label: "Score", num: true,
    render: (r) => el("span", null, r.score == null ? "—" : String(r.score)) },
  { key: "applied_on", label: "Applied",
    render: (r) => el("span", r.applied_on ? null : "muted",
      r.applied_on ? fmt.dateShort(r.applied_on) : (r.inferred ? `yes — proven by the ${r.inferred === "won" ? "contract" : "interview"}` : "—")) },
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
  { key: "cohort", label: "Came through",
    render: (r) => pill(r.cohort === "outside" ? "not the job feed"
      : r.cohort === "this" ? "this period's jobs" : `jobs of ${r.cohort_key || "an earlier period"}`,
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
  const s = d.summary || {};
  const wanted = ctx.params.get("view");
  let tab = TABS.some((t) => t.id === wanted) ? wanted : recall(TAB_KEY, "inbox");
  if (!TABS.some((t) => t.id === tab)) tab = "inbox";

  root.append(el("div", "up-head", [
    el("div", "hstack", [el("h2", "up-title", "Upwork"),
      el("span", "muted small", `as of ${fmt.dateShort(d.as_of)}`)]),
    lede(s, d, (id) => switchTo(id)),
  ]));

  const bar = el("div", { class: "subtabs", attrs: { role: "tablist" } });
  const host = el("div", "up-host");
  const buttons = {};
  TABS.forEach((t) => {
    const b = el("button", {
      class: "subtab", type: "button",
      attrs: { role: "tab", "aria-selected": "false", "data-tab": t.id },
      on: { click: () => switchTo(t.id) },
    }, [t.label, badge(t.id, d)]);
    buttons[t.id] = b;
    bar.appendChild(b);
  });
  root.append(bar, host);

  const views = {
    inbox: () => inboxView(d, ctx),
    jobs: () => jobsView(d, ctx),
    health: () => healthView(d, ctx),
    analysis: async () => { const box = el("div"); await renderAnalysis(box, ctx); return box; },
  };

  async function switchTo(id) {
    tab = id;
    remember(TAB_KEY, id);
    Object.entries(buttons).forEach(([k, b]) => b.setAttribute("aria-selected", String(k === id)));
    // keep the hash honest without a re-render: Refresh and a bookmark land here
    try { history.replaceState({}, "", "#/upwork?view=" + id); } catch (e) { /* ignore */ }
    clear(host);
    const view = await views[id]();
    clear(host).appendChild(view);
  }
  await switchTo(tab);
}

/* ── the sentence under the heading ───────────────────────────────────────── */

function lede(s, d, go) {
  const jump = (text, id) => el("button", { class: "linktext", type: "button", on: { click: () => go(id) } }, text);
  const people = s.people_waiting || 0;
  const parts = [];
  parts.push(people
    ? jump(`${fmt.num(people)} ${people === 1 ? "person is" : "people are"} waiting on you`, "inbox")
    : jump("Nobody is waiting on you", "inbox"));
  if (s.probably_resolved) parts.push(el("span", "muted", ` (+${s.probably_resolved} probably resolved)`));
  parts.push(el("span", "muted", " · "));
  parts.push(jump(`${fmt.num(s.hot_week)} hot jobs`, "jobs"),
    el("span", "muted", ` this week, ${fmt.num(s.applied_week)} applied, ${fmt.num(s.interviewing_week)} interviewing`
      + (s.contracts_started_week ? `, ${s.contracts_started_week} contract${s.contracts_started_week === 1 ? "" : "s"} started` : "")));
  parts.push(el("span", "muted", " · "));
  parts.push(s.rails_attention
    ? jump(`${s.rails_attention} rail${s.rails_attention === 1 ? "" : "s"} need${s.rails_attention === 1 ? "s" : ""} attention`, "health")
    : jump("every rail is fine", "health"));
  return el("p", "up-lede", parts);
}

function badge(id, d) {
  const s = d.summary || {};
  if (id === "inbox") {
    const n = s.people_waiting || 0;
    return n ? el("span", "n " + (s.replies_owed ? "hot" : ""), String(n)) : null;
  }
  if (id === "jobs") return s.hot_week != null ? el("span", "n", `${fmt.num(s.hot_week)} hot`) : null;
  if (id === "health") {
    return s.rails_attention ? el("span", "n warn", String(s.rails_attention)) : el("span", "n ok", "✓");
  }
  return null;
}

/* ── 1. Inbox ─────────────────────────────────────────────────────────────── */

function inboxView(d, ctx) {
  const ib = d.inbox || { conversations: [], other: [], counts: {} };
  const c = ib.counts || {};
  const out = el("div", "stack up-view");
  const convos = ib.conversations || [];
  const state = { filter: "all" };

  const head = el("div", "secrow");
  const n = convos.length;
  head.append(
    el("h3", "sec-title", n ? `${fmt.num(n)} conversation${n === 1 ? "" : "s"} need${n === 1 ? "s" : ""} you` : "Nothing is waiting on you"),
    el("span", "muted small", n ? "longest wait first" : ""),
  );
  const chips = el("div", "fchips right");
  const CHIP = [["all", "All", c.all], ["owed", "Reply owed", c.owed], ["quiet", "Gone quiet", c.quiet], ["maybe", "Probably resolved", c.maybe]];
  const list = el("div", "cv-list");
  const draw = () => {
    clear(list);
    const shown = convos.filter((e) => state.filter === "all" || e.conversation.state.kind === state.filter);
    shown.forEach((e, i) => list.appendChild(conversationRow(e, ctx, { open: i === 0 && state.filter === "all" })));
    if (!shown.length) list.appendChild(emptyState("No conversation in this state.", { icon: "circle-check" }));
    chips.querySelectorAll(".fchip").forEach((ch) => ch.classList.toggle("on", ch.dataset.k === state.filter));
  };
  CHIP.forEach(([k, label, count]) => {
    if (k !== "all" && !count) return;
    chips.appendChild(el("button", {
      class: "fchip" + (k === state.filter ? " on" : ""), type: "button", attrs: { "data-k": k },
      on: { click: () => { state.filter = k; draw(); } },
    }, [label, el("span", "c", String(count || 0))]));
  });
  const sn = ib.snoozed || {};
  if (sn.count) chips.appendChild(el("span", "muted small", `${sn.count} snoozed${sn.next_back ? ", back " + fmt.dateShort(sn.next_back) : ""}`));
  if (n) head.appendChild(chips);
  out.append(head);

  if (!n) {
    const rails = d.rails || {};
    const ok = rails.worst === "ok";
    out.append(emptyState(ok
      ? "Nobody on Upwork is waiting for a reply — and every rail is fresh, so that is a real quiet."
      : "No conversation is open — but a rail is not fresh (see Health), so this may be a feed gap rather than a clear desk.",
    { icon: ok ? "circle-check" : "alert-triangle" }));
  } else {
    draw();
    out.append(list);
  }
  const cleared = ib.cleared || [];
  if (cleared.length) {
    const done = cleared.filter((c) => c.how === "done").map((c) => c.name);
    const confirmed = cleared.filter((c) => c.how !== "done").map((c) => c.name);
    out.append(el("div", "muted small", [
      "Cleared since yesterday: ",
      done.length ? el("span", null, [el("b", null, done.join(", ")), " (done)"]) : null,
      done.length && confirmed.length ? " · " : "",
      confirmed.length ? el("span", null, [el("b", null, confirmed.join(", ")), " (confirmed resolved)"]) : null,
      " — each comes back on its own the moment they write again.",
    ]));
  }
  if (ib.auto_closed) {
    out.append(el("div", "muted small", `${ib.auto_closed} conversation${ib.auto_closed === 1 ? " was" : "s were"} closed automatically — you had already answered on another channel.`));
  }
  if (ib.hidden_no_provenance) {
    out.append(notice("warn", `${ib.hidden_no_provenance} card${ib.hidden_no_provenance === 1 ? " is" : "s are"} hidden: client-facing with no evidence link. Listed under Activity.`));
  }

  // everything else on the Upwork queue — escalations, Loom flags — as the
  // plain cards Today uses, never dropped
  const other = ib.other || [];
  if (other.length) {
    const box = el("div", "stack");
    box.append(el("h3", "sec-title small-title", `Also on your Upwork queue (${fmt.num(other.length)})`));
    const cards = el("div", "card flush");
    other.slice(0, 5).forEach((it) => cards.appendChild(queueCard(it, ctx)));
    if (other.length > 5) {
      cards.appendChild(expander(`show all ${fmt.num(other.length)}`, (body) => {
        other.slice(5).forEach((it) => body.appendChild(queueCard(it, ctx)));
      }));
    }
    box.append(cards);
    out.append(box);
  }
  return out;
}

/* ── 2. Jobs ──────────────────────────────────────────────────────────────── */

const LIST_DEFS = [
  { id: "hot", label: "Hot", period: true, cols: JOB_COLS, count: (f) => stageN(f, "hot") },
  { id: "drafts_waiting", label: "Drafts to review", period: false, cols: JOB_COLS, count: (f, d) => ((d.needs_me || {}).drafts_waiting || {}).count },
  { id: "loom_flags", label: "Loom-worthy", period: false, cols: JOB_COLS, count: (f, d) => ((d.needs_me || {}).loom_flags || {}).count },
  { id: "applied", label: "Applied", period: true, cols: OUTCOME_COLS, count: (f) => stageN(f, "applied") },
  { id: "interviewed", label: "Interviewing", period: true, cols: OUTCOME_COLS, count: (f) => stageN(f, "interviewed") },
  { id: "won", label: "Won", period: true, cols: OUTCOME_COLS, count: (f) => stageN(f, "won") },
];

function stageN(f, k) {
  const s = (f.stages || []).find((x) => x.k === k);
  return s ? s.n : null;
}

function jobsView(d, ctx) {
  const out = el("div", "stack up-view");
  const state = { view: recall(VIEW_KEY, "week") === "month" ? "month" : "week", list: recall(LIST_KEY, "hot") };
  const flowHost = el("div");
  const listHost = el("div");
  const connectsHost = el("div");
  const redraw = () => {
    clear(flowHost).append(...flowBlock(d, ctx, state, redraw));
    clear(listHost).append(listsBlock(d, ctx, state));
    clear(connectsHost).append(connectsBlock(d, ctx, state));
  };
  out.append(flowHost, listHost, connectsHost);
  redraw();
  return out;
}

/** "39% of fetched · usually 44% ▼" — the arrow only when the change is real */
function stageSub(s, prevLabel, f) {
  const op = s.of_prev;
  if (!op || op.pct == null) return null;
  const parts = [el("b", null, `${Math.round(op.pct)}%`), ` of ${prevLabel}`];
  const bl = s.baseline || {};
  if (bl.pct != null) {
    parts.push(` · usually ${Math.round(bl.pct)}%`);
    if (s.signal && s.delta_pp != null && Math.abs(s.delta_pp) >= 1) {
      parts.push(el("span", s.delta_pp > 0 ? "pos" : "neg", s.delta_pp > 0 ? " ▲" : " ▼"));
    }
  } else if (bl.n_den === 0 && bl.covered_since === null) {
    parts.push(el("span", "muted", " · no history yet"));
  }
  return parts;
}

function flowBlock(d, ctx, state, redraw) {
  const view = state.view;
  const f = (d.funnel || {})[view] || {};
  const stages = (f.stages || []).filter((s) => s.measured);
  const unmeasured = (f.stages || []).filter((s) => !s.measured);
  const period = f.period || {};
  const key = period.key;
  const periodParam = view === "week" ? { week: key } : { month: key };
  const noun = view === "week" ? "week" : "month";

  const seg = el("div", "segmented", [["week", "This week"], ["month", "This month"]].map(([v, label]) => el("button", {
    class: "segbtn" + (view === v ? " on" : ""), type: "button",
    attrs: { "aria-pressed": String(view === v) },
    on: { click: () => { state.view = v; remember(VIEW_KEY, v); redraw(); } },
  }, label)));

  const head = el("div", "secrow", [
    el("h3", "sec-title", `This ${noun}'s jobs`),
    el("span", "muted small", `${period.label || key}${period.so_far ? ", so far" : ""}`),
    el("div", "right", [seg]),
  ]);

  const flow = el("div", { class: "flow", style: { gridTemplateColumns: `repeat(${stages.length}, 1fr)` } });
  const fetched = stageN(f, "fetched") || 0;
  const labels = { fetched: "Fetched", passed: "Passed the rules", hot: `Hot (score ${d.bands && d.bands.hot_cut != null ? d.bands.hot_cut + "+" : ""})`, applied: "Applied", interviewed: "Interviewing", won: "Won" };
  const prevWord = { passed: "fetched", hot: "passed", applied: "hot", interviewed: "applied", won: "interviewing" };
  stages.forEach((s, i) => {
    let sub;
    if (s.k === "fetched") sub = [`new jobs seen this ${noun}`];
    else if (s.k === "interviewed") {
      const ap = stageN(f, "applied") || 0;
      sub = s.n ? [`${fmt.num(s.n)} of the ${fmt.num(ap)} applied`] : [ap ? "none of the applied yet" : "nothing applied yet"];
    } else if (s.k === "won") {
      const iv = stageN(f, "interviewed") || 0;
      sub = s.n ? [`${fmt.num(s.n)} of ${fmt.num(iv)} interviewing`] : [`none yet from this ${noun}`];
    } else sub = stageSub(s, prevWord[s.k], f) || [""];
    const pct = fetched ? Math.max(1.5, (100 * (s.n || 0)) / fetched) : 0;
    const card = el("button", {
      class: "stage" + (s.k === "won" && !s.n ? " dim" : ""), type: "button",
      attrs: { title: `${labels[s.k] || s.label}: ${fmt.num(s.n)} — click for the jobs` },
      on: { click: () => openRows(ctx, `${labels[s.k] || s.label} — ${period.label || key}`,
        Object.assign({ stage: s.k }, periodParam),
        ["applied", "interviewed", "won"].includes(s.k) ? OUTCOME_COLS : JOB_COLS,
        { subtitle: `jobs fetched this ${noun} that reached "${labels[s.k] || s.label}"`,
          facets: [{ label: "band", allLabel: "All bands", of: (r) => r.band },
            { label: "feed", allLabel: "All feeds", of: (r) => r.feed_source }] }) },
    }, [
      el("div", "l", labels[s.k] || s.label),
      el("div", "v", fmt.num(s.n)),
      el("div", "s", sub),
      el("div", "bar", el("i", { style: { width: pct + "%" } })),
      i < stages.length - 1 ? el("span", "arrow", "›") : null,
    ]);
    flow.appendChild(card);
  });

  const blocks = [head, flow];

  // the contracts that started — the wins the flow would hide
  const c = f.contracts_started || {};
  if (c.total) {
    const where = [];
    if (c.this) where.push(`${c.this} from this ${noun}'s jobs`);
    if (c.earlier) where.push(`${c.earlier} from an earlier ${noun}'s jobs`);
    if (c.outside) where.push(`${c.outside} not through the job feed at all (an invite, a direct hire, or a client conversation)`);
    blocks.push(el("div", "callout ok", [
      icon("circle-check", { size: 16 }),
      el("div", null, [
        el("button", { class: "linktext strong", type: "button",
          on: { click: () => openRows(ctx, `Contracts started — ${period.label || key}`,
            Object.assign({ stage: "contracts_started" }, periodParam), CONTRACT_COLS,
            { subtitle: "each contract and where it came from", time: (r) => r.d }) } },
        `${fmt.num(c.total)} contract${c.total === 1 ? "" : "s"} started this ${noun}`),
        el("span", "muted", ` — ${where.join(" · ")}. Click to see which.`),
      ]),
    ]));
  } else {
    blocks.push(el("div", "muted small", `No contract started this ${noun} yet.`));
  }

  const notes = [];
  if (unmeasured.length) {
    notes.push(`Not tracked yet: ${unmeasured.map((s) => s.k === "viewed" ? "whether clients opened your proposals" : s.k === "offer" ? "offers" : s.label.toLowerCase()).join(", and ")} — both arrive with the proposals sync (the next build step).`);
  }
  const bl = f.baseline || {};
  if (bl.periods) notes.push(`"Usually" compares with the last ${bl.periods} ${noun}s; an arrow shows only when there is enough data to call it a real change.`);
  if (notes.length) blocks.push(el("p", "muted small foot", notes.join(" ")));
  return blocks;
}

function listsBlock(d, ctx, state) {
  const view = state.view;
  const f = (d.funnel || {})[view] || {};
  const period = f.period || {};
  const periodParam = view === "week" ? { week: period.key } : { month: period.key };
  const noun = view === "week" ? "week" : "month";
  const out = el("div", "stack");
  const chips = el("div", "fchips right");
  const host = el("div", "card flush up-table");
  const defs = LIST_DEFS;
  if (!defs.some((x) => x.id === state.list)) state.list = "hot";

  const load = async () => {
    const def = defs.find((x) => x.id === state.list) || defs[0];
    chips.querySelectorAll(".fchip").forEach((ch) => ch.classList.toggle("on", ch.dataset.k === def.id));
    clear(host).appendChild(el("div", "muted small pad", "reading…"));
    const params = Object.assign({ stage: def.id }, def.period ? periodParam : {});
    const res = await fetchRows(ctx, params);
    clear(host);
    if (!res) { host.appendChild(emptyState("Could not read this list.")); return; }
    const rows = res.rows || [];
    const total = res.total == null ? rows.length : res.total;
    const title = def.period ? `${def.label} this ${noun}` : def.label;
    if (!rows.length) {
      host.appendChild(emptyState(`Nothing in "${title}" right now.`, { icon: "circle-check" }));
      return;
    }
    host.appendChild(table({ columns: def.cols, rows: rows.slice(0, TABLE_PEEK), wrapClass: "flush" }));
    host.appendChild(el("div", "card-foot hstack", [
      el("span", "muted small", `${fmt.num(Math.min(TABLE_PEEK, rows.length))} of ${fmt.num(total)} · sorted by score`),
      total > TABLE_PEEK ? btn(`show all ${fmt.num(total)}`, () => openRows(ctx, title, params, def.cols,
        { subtitle: def.period ? `${period.label || period.key}` : null,
          facets: [{ label: "band", allLabel: "All bands", of: (r) => r.band },
            { label: "ICP", allLabel: "All tiers", of: (r) => r.icp_tier || "none" }] }),
      { sm: true, variant: "ghost", icon: "arrow-up-right" }) : null,
    ]));
  };

  defs.forEach((def) => {
    const count = def.count(f, d);
    chips.appendChild(el("button", {
      class: "fchip" + (def.id === state.list ? " on" : ""), type: "button", attrs: { "data-k": def.id },
      on: { click: () => { state.list = def.id; remember(LIST_KEY, def.id); load(); } },
    }, [def.period ? `${def.label} this ${noun}` : def.label, el("span", "c", count == null ? "—" : fmt.num(count))]));
  });
  out.append(el("div", "secrow", [el("h3", "sec-title", "Job lists"), chips]), host);
  load();
  return out;
}

function connectsBlock(d, ctx, state) {
  const c = d.connects || {};
  const per = state.view === "month" ? (c.month || {}) : (c.week || {});
  const noun = state.view === "month" ? "this month" : "the last 7 days";
  const src = c.source || {};
  const stale = !!(src.status && src.status !== "ok");
  const drill = (title) => () => openRows(ctx, title, { stage: "connects", frm: per.lo, to: per.hi },
    CONNECTS_COLS, { subtitle: `${fmt.dateShort(per.lo)} → ${fmt.dateShort(per.hi)}`,
      amount: (r) => r.connects_delta, time: (r) => r.d,
      facets: [{ label: "kind", allLabel: "All kinds", of: (r) => r.kind }] });
  const strip = el("div", "strip", [
    el("button", { class: "strip-item", type: "button", on: { click: drill(`Connects — ${noun}`) } }, [
      el("span", "k", `Connects used, ${noun}`), el("span", "v", fmt.num(per.spent_net))]),
    el("div", "strip-item", [el("span", "k", "Applications"), el("span", "v", fmt.num(per.applications))]),
    el("div", "strip-item", [el("span", "k", "Per application"),
      el("span", "v", per.per_application == null ? "—" : fmt.num(Math.round(per.per_application)))]),
    el("div", "strip-item", [el("span", "k", "Interviews"), el("span", "v", fmt.num(per.interviews))]),
    el("span", "muted small grow-right", stale
      ? `the connects rail is ${src.status} — these may not be current`
      : "counts only — the money is on the Money tab"),
  ]);
  return strip;
}

/* ── 3. Health ────────────────────────────────────────────────────────────── */

// what each rail DOES, in the words he would use — the registry names the
// task; this names the job
const RAIL_WHAT = {
  scanner: "pulls new jobs every hour at :15, scores them, writes the drafts",
  mcp_feed: "the Upwork API feed, hourly at :40 — becomes the job feed at the flip",
  proposals_sync: "your proposals, rooms and messages, every hour",
  messages_sync: "who is waiting on you, at 10:15 and 19:15",
  offers_sync: "offers you receive",
  applied: "which jobs you applied to and what they cost, daily at 05:35",
  outcome_email: "asks you what happened to last week's proposals, Sundays",
  clickup_push: "one card per hot job, after every scan",
  cards_sweep: "closes cards that are done, every 2 hours",
};

function agoWords(hours) {
  if (hours == null) return null;
  const h = Number(hours);
  if (h < 1) return "under an hour ago";
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

function statusWords(r) {
  if (r.built === false) return { cls: "off", text: "Not built yet", why: r.reason };
  const st = r.status || "unknown";
  if (st === "ok") return { cls: "ok", text: "OK", why: null };     // the run's metrics live in Activity
  if (st === "stale") {
    const expected = String(r.cadence || "").replace(/^fresh within /, "expected every ");
    return { cls: "warn", text: "Late",
      why: (r.age_hours != null ? `last ran ${agoWords(r.age_hours)}${expected ? ", " + expected : ""}` : "has not run when expected")
        + (r.reason ? ` — ${r.reason}` : "") };
  }
  if (st === "degraded") return { cls: "warn", text: "Needs a look", why: r.reason || r.result || "ran, but the result is off" };
  if (st === "down") return { cls: "bad", text: "Not running", why: r.reason || r.result || "no successful run" };
  return { cls: "warn", text: st, why: r.reason || null };
}

function healthView(d, ctx) {
  const rails = d.rails || {};
  const items = rails.items || [];
  const out = el("div", "stack up-view");
  out.append(el("div", "secrow", [el("h3", "sec-title", "Is the machinery working?"),
    el("span", "muted small", "every rail, in plain words")]));

  const attention = items.filter((r) => r.built !== false && r.status && r.status !== "ok");
  if (attention.length) {
    out.append(notice("warn", [
      el("b", null, `${attention.length} thing${attention.length === 1 ? "" : "s"} need${attention.length === 1 ? "s" : ""} a look: `),
      attention.map((r) => `${r.label} — ${statusWords(r).why || statusWords(r).text}`).join(" · "),
    ]));
  } else {
    out.append(notice("ok", "Every rail ran when it should and finished cleanly."));
  }

  const rows = el("div", "card flush");
  const t = el("table", "rails-table");
  t.appendChild(el("thead", null, el("tr", null, [el("th", null, "Rail"), el("th", null, "Last ran"), el("th", null, "Status"), el("th", null, "")])));
  const body = el("tbody");
  items.forEach((r) => {
    const w = statusWords(r);
    const when = r.started_at || r.last_success;
    const lastCell = r.built === false ? el("span", "muted", "—")
      : when ? el("div", "stack-1", [el("span", null, fmt.dateTimeShort(when)), el("span", "small muted", fmt.relative(when))])
        : el("span", "muted", "never");
    const name = el("button", { class: "linktext rail-name", type: "button",
      attrs: { title: r.built === false ? (r.reason || "not built yet") : "open its last run in Activity" },
      on: { click: () => {
        if (r.built === false) { toast(r.reason || "not built yet"); return; }
        if (r.run_id && r.day) ctx.navigate("activity", { day: r.day, run: String(r.run_id) });
        else toast(`${r.label}: no run recorded yet`);
      } } }, [el("span", "dot " + w.cls), r.label]);
    const action = el("div", "hstack-tight");
    if (r.rerun && r.task_name && r.built !== false) {
      action.appendChild(btn(attention.includes(r) ? "Run now" : "Re-run",
        (ev) => ctx.kick("/api/rerun", { task: r.task_name }, ev.currentTarget),
        { sm: true, variant: attention.includes(r) ? "primary" : "ghost", icon: "refresh-cw" }));
    }
    body.appendChild(el("tr", attention.includes(r) ? "attn" : "", [
      el("td", null, el("div", "stack-1", [name, el("span", "small muted", RAIL_WHAT[r.key] || r.why || "")])),
      el("td", null, lastCell),
      el("td", null, el("div", "stack-1", [el("span", "st " + w.cls, w.text), w.why ? el("span", "small muted", w.why) : null])),
      el("td", "num", action),
    ]));
  });
  t.appendChild(body);
  rows.append(el("div", "tablewrap flush", t),
    el("div", "card-foot muted small", "Click a rail to see its last run in Activity. “OK” means it ran on time and finished; “Late” means it has not run when expected; “Needs a look” means it ran but something in the result is off."));
  out.append(rows);
  return out;
}
