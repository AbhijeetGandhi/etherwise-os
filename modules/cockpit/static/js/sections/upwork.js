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
//
// Funnel and Analysis MOVED to the Pipeline tab on 2026-10-09 (his answer:
// "New top-level Pipeline tab" — the Funnel and the scanner charts fold into
// it, nothing parallel). An old #/upwork?view=funnel|analysis link lands on
// Pipeline › Proposals | Scanner instead of a blank view.
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
import { funnel } from "../shapes.js";
import { openDrill } from "../drill.js";
import { queueCard } from "./today.js";
import { conversationRow } from "../conversation.js";

export const meta = { title: "Upwork", hint: "the channel, end to end" };

const TAB_KEY = "ew_cockpit_upwork_tab";            // inbox | jobs | health
const VIEW_KEY = "ew_cockpit_upwork_view";          // week | month
const LIST_KEY = "ew_cockpit_upwork_list";          // which job list is open
const ROW_CAP = 300;
const TABLE_PEEK = 12;                              // rows shown inline before "show all"

const TABS = [
  { id: "inbox", label: "Inbox" },
  { id: "jobs", label: "Jobs" },
  // Health left this tab on 2026-11-01 (his call, the cockpit audit): it was
  // Activity › Health rail-for-rail, and it still said "Offers rail — not
  // built" two months after the heartbeat started reading offers. The header
  // keeps its one-line verdict and links to the one health table.
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

/** where an interview or a contract came from, in words */
const cameThrough = (noun) => (r) => {
  if (r.via === "repeat") return pill("a repeat client", "ok");
  if (r.cohort === "outside") return pill("came to us (no job of ours behind it)", "warn");
  const where = r.cohort === "this" ? `this ${noun}'s jobs` : `jobs of ${r.cohort_key || "an earlier " + noun}`;
  return pill(r.via === "client" ? `${where}, hired on a new posting` : where, "outline");
};

const jobBehind = (r) => (r.job_title
  ? (r.job_url ? extLink(r.job_url, r.job_title, { title: "the job we applied to" }) : el("span", null, r.job_title))
  : el("span", "muted", "—"));

const contractCols = (noun) => [
  { key: "title", label: "Contract",
    render: (r) => (r.url ? extLink(r.url, r.title || r.id, { title: "open the room on Upwork" }) : el("span", null, r.title || r.id)) },
  { key: "client_company", label: "Client", cls: "muted" },
  { key: "cohort", label: "Came through", render: cameThrough(noun) },
  { key: "job_title", label: "The job behind it", render: jobBehind },
  { key: "contract_type", label: "Type", cls: "muted" },
  { key: "d", label: "Started", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
];

const peopleCols = (noun) => [
  { key: "who", label: "Who",
    render: (r) => (r.url ? extLink(r.url, r.who, { title: "open the room on Upwork" }) : el("span", null, r.who)) },
  { key: "d", label: "Started talking", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "cohort", label: "Came through", render: cameThrough(noun) },
  { key: "job_title", label: "The job behind it", render: jobBehind },
  { key: "then", label: "Then",
    render: (r) => (r.contract_start ? el("span", "pos", `hired ${fmt.dateShort(r.contract_start)}`)
      : el("span", "muted", r.upwork_status === "Archived" ? "went quiet (proposal archived)" : "still talking")) },
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

// the views that moved to the Pipeline tab, and where they live now
const MOVED = { funnel: "proposals", analysis: "scanner" };

export async function render(root, ctx) {
  const moved = MOVED[ctx.params.get("view")];
  if (moved) { ctx.navigate("pipeline", { view: moved }); return; }
  const d = await ctx.api("/api/upwork");
  const s = d.summary || {};
  const wanted = ctx.params.get("view");
  let tab = TABS.some((t) => t.id === wanted) ? wanted : recall(TAB_KEY, "inbox");
  if (!TABS.some((t) => t.id === tab)) tab = "inbox";

  root.append(el("div", "up-head", [
    el("div", "hstack", [el("h2", "up-title", "Upwork"),
      el("span", "muted small", `as of ${fmt.dateShort(d.as_of)}`)]),
    lede(s, d, (id) => switchTo(id), ctx),
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

function lede(s, d, go, ctx) {
  const jump = (text, id) => el("button", { class: "linktext", type: "button", on: { click: () => go(id) } }, text);
  const people = s.people_waiting || 0;
  const parts = [];
  parts.push(people
    ? jump(`${fmt.num(people)} ${people === 1 ? "person is" : "people are"} waiting on you`, "inbox")
    : jump("Nobody is waiting on you", "inbox"));
  if (s.probably_resolved) parts.push(el("span", "muted", ` (+${s.probably_resolved} probably resolved)`));
  parts.push(el("span", "muted", " · "));
  parts.push(jump(`${fmt.num(s.hot_week)} hot jobs`, "jobs"),
    el("span", "muted", ` this week, ${fmt.num(s.applied_week)} applied, ${fmt.num(s.interviewing_week)} interview${s.interviewing_week === 1 ? "" : "s"} started`
      + (s.won_week ? `, ${s.won_week} contract${s.won_week === 1 ? "" : "s"} won` : "")));
  parts.push(el("span", "muted", " · "));
  const toHealth = (text) => el("button", { class: "linktext", type: "button",
    attrs: { title: "every rail, in Activity › Health" },
    on: { click: () => ctx.navigate("activity", { view: "health" }) } }, text);
  parts.push(s.rails_attention
    ? toHealth(`${s.rails_attention} rail${s.rails_attention === 1 ? "" : "s"} need${s.rails_attention === 1 ? "s" : ""} attention`)
    : toHealth("every rail is fine"));
  return el("p", "up-lede", parts);
}

function badge(id, d) {
  const s = d.summary || {};
  if (id === "inbox") {
    const n = s.people_waiting || 0;
    return n ? el("span", "n " + (s.replies_owed ? "hot" : ""), String(n)) : null;
  }
  if (id === "jobs") return s.hot_week != null ? el("span", "n", `${fmt.num(s.hot_week)} hot`) : null;
  return null;
}

/* ── "Run now": one button per system, polled to completion ───────────────── */

const RUN_POLL_MS = 4000;
const RUN_POLL_MAX_MS = 6 * 60 * 1000;      // a paced MCP scan is ~3–4 min

function clock(iso) {
  // "HH:MM" from any of the DB's timestamp shapes — the same slice fmt.dateTime takes
  const s = fmt.dateTime(iso);
  return s === "—" ? "" : s.slice(11);
}

/**
 * runButton(ctx, d, task, label) — "Check Upwork now" etc. Kicks the
 * allowlisted task through /api/rerun, then polls /api/upwork/run until a NEW
 * run has finished (or the one already in flight has), and re-reads the tab.
 * If the task is already running (a scheduled run), it does not kick — a
 * kickstart would kill that run — it just waits for it.
 */
export function runButton(ctx, d, task, label, opts = {}) {
  const rail = ((d.rails || {}).items || []).find((r) => r.task_name === task);
  const allowed = !rail || rail.rerun !== false;
  const wrap = el("span", "runbtn");
  // Health has its own "last ran" column (opts.quiet): the line then shows
  // only while a run is in flight
  const status = el("span", "muted small runbtn-status",
    !opts.quiet && rail && (rail.started_at || rail.last_success)
      ? `last ran ${clock(rail.started_at || rail.last_success)}`
        + (rail.run_status && rail.run_status !== "completed" ? ` · ${rail.run_status}` : "")
      : "");
  let busy = false;
  const b = btn(label, async () => {
    if (busy) return;
    busy = true;
    const before = await ctx.api("/api/upwork/run?" + qstr({ task })).catch(() => null);
    const beforeId = before && before.run ? before.run.id : 0;
    let waitingOnScheduled = false;
    if (before && before.running) {
      waitingOnScheduled = true;
      status.textContent = `already running since ${clock(before.run.started_at)} — waiting for it`;
    } else {
      b.disabled = true; b.textContent = "Starting…";
      let res;
      try { res = await ctx.post("/api/rerun", { task }); } catch (e) { res = { ok: false, error: String(e.message || e) }; }
      if (!res || !res.ok) {
        toast(`${label} could not start: ${(res && res.error) || "see the logs"}`, true);
        b.disabled = false; b.textContent = label; busy = false;
        return;
      }
      status.textContent = "started — running…";
    }
    b.disabled = true; b.textContent = "Running…";
    const t0 = Date.now();
    const poll = async () => {
      let now = null;
      try { now = await ctx.api("/api/upwork/run?" + qstr({ task })); } catch (e) { now = null; }
      const run = now && now.run;
      const fresh = run && (waitingOnScheduled ? run.id >= beforeId : run.id > beforeId);
      if (fresh && !now.running) {
        const ok = run.status === "completed" || run.status === "skipped_empty";
        const what = now.words || label;
        toast(ok
          ? `${what}${run.summary ? " — " + run.summary : ""}${run.status === "skipped_empty" ? " — nothing new" : ""}`
          : `${what}: ${run.status}${run.error ? " — " + run.error : ""}`, !ok);
        ctx.refresh();                       // re-read the tab; it lands on this same view
        return;
      }
      if (fresh && run.started_at) status.textContent = `running since ${clock(run.started_at)}…`;
      if (Date.now() - t0 > RUN_POLL_MAX_MS) {
        toast(`${label}: still running after 6 minutes — check Health later`, true);
        b.disabled = false; b.textContent = label; busy = false;
        return;
      }
      setTimeout(poll, RUN_POLL_MS);
    };
    setTimeout(poll, RUN_POLL_MS);
  }, { sm: true, variant: opts.variant || "primary", icon: "refresh-cw",
    title: allowed ? `runs ${task} now` : "not re-runnable from the cockpit" });
  if (!allowed) b.disabled = true;
  wrap.append(b, status);
  return wrap;
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
  const right = el("div", "right");
  if (n) right.appendChild(chips);
  right.appendChild(runButton(ctx, d, "upwork_inbox", "Check Upwork now"));
  head.appendChild(right);
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
  { id: "interviewed", label: "Interviewing", period: true, cols: peopleCols, count: (f) => stageN(f, "interviewed") },
  { id: "won", label: "Won", period: true, cols: contractCols, count: (f) => stageN(f, "won") },
];

/** "1 from this week's jobs · 1 a repeat client" — the split under a tile */
function sourceWords(src, noun) {
  const parts = [];
  if (!src) return parts;
  if (src.this) parts.push(`${fmt.num(src.this)} from this ${noun}'s jobs`);
  if (src.earlier) parts.push(`${fmt.num(src.earlier)} from earlier jobs`);
  if (src.repeat) parts.push(`${fmt.num(src.repeat)} ${src.repeat === 1 ? "a repeat client" : "repeat clients"}`);
  if (src.outside) parts.push(`${fmt.num(src.outside)} came to us`);
  return parts;
}

const firstName = (who) => String(who || "").split(",")[0].trim();

/** "Won this month: Royan Stomberg (17 Aug, this month's jobs) · Rocky Choy (24 Aug, repeat client)" */
function namesLine(label, names, noun) {
  if (!names || !names.length) return null;
  const items = [];
  names.forEach((n, i) => {
    const how = n.via === "repeat" ? "a repeat client"
      : n.cohort === "outside" ? "came to us"
        : n.cohort === "this" ? `this ${noun}'s jobs` : "earlier jobs";
    const bits = [fmt.dateShort(n.d), how];
    if (n.via === "client") bits.push("hired on a new posting");
    if (n.contract_start) bits.push(`hired ${fmt.dateShort(n.contract_start)}`);
    if (i) items.push(el("span", "muted", " · "));
    items.push(n.url ? extLink(n.url, firstName(n.who), { title: n.who }) : el("b", null, firstName(n.who)));
    items.push(el("span", "muted", ` (${bits.join(", ")})`));
  });
  return el("p", "small names-line", [el("b", null, `${label}: `), ...items]);
}

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
    el("div", "right", [seg, runButton(ctx, d, "upwork_scan", "Scan for new jobs now")]),
  ]);

  // A FUNNEL, not a row of boxes (Abhijeet, 2026-11-02). Each band drills to
  // its rows; the drops on the gaps say what fell out between two steps.
  const labels = { fetched: "Fetched", passed: "Passed the rules", hot: `Hot (score ${d.bands && d.bands.hot_cut != null ? d.bands.hot_cut + "+" : ""})`, applied: "Applied", interviewed: "Interviewing", won: "Won" };
  const prevWord = { passed: "fetched", hot: "passed", applied: "hot", interviewed: "applied", won: "interviewing" };
  const shortOf = { fetched: "fetched", passed: "those that passed", hot: "the hot ones", applied: "those applied to", interviewed: "interviews" };
  // the last two tiles count what HAPPENED this period, from wherever it came
  // (an invite, a repeat client, a hire on a new posting) — the split says so
  const happened = { interviewed: "started talking", won: "contracts started" };
  const dropWord = { passed: "failed the rules", hot: "scored below the hot bar", applied: "hot, not applied to",
    interviewed: "applied, no interview yet", won: "interviewing, no contract yet" };
  const funStages = stages.map((s) => {
    let sub;
    if (s.k === "fetched") sub = [`new jobs seen this ${noun}`];
    else if (happened[s.k]) {
      const words = sourceWords(s.sources, noun);
      sub = s.n ? [words.join(" · ") || `${happened[s.k]} this ${noun}`] : [`none this ${noun} yet`];
    } else {
      // the funnel already prints "N · x% of <step above>"; keep only the
      // "usually" comparison here so the same figure is not said twice
      const op = s.of_prev || {}, bl = s.baseline || {};
      if (bl.pct != null && op.pct != null) {
        sub = [`usually ${Math.round(bl.pct)}%`];
        if (s.signal && s.delta_pp != null && Math.abs(s.delta_pp) >= 1) {
          sub.push(el("span", s.delta_pp > 0 ? "pos" : "neg", s.delta_pp > 0 ? " ▲" : " ▼"));
        }
      } else if (bl.n_den === 0 && bl.covered_since === null) sub = [el("span", "muted", "no history yet")];
    }
    const isPeople = s.k === "interviewed";
    const isContracts = s.k === "won";
    return {
      label: labels[s.k] || s.label, short: shortOf[s.k], n: s.n, sub,
      tone: s.k === "won" ? "good" : null,
      note: `${labels[s.k] || s.label}: ${fmt.num(s.n)} — click for ${isPeople ? "who" : isContracts ? "which" : "the jobs"}`,
      onClick: () => openRows(ctx, `${labels[s.k] || s.label} — ${period.label || key}`,
        Object.assign({ stage: s.k }, periodParam),
        isPeople ? peopleCols(noun) : isContracts ? contractCols(noun) : s.k === "applied" ? OUTCOME_COLS : JOB_COLS,
        isPeople ? { subtitle: `people who started talking to us this ${noun}, and where each came from`, time: (r) => r.d }
          : isContracts ? { subtitle: `contracts that started this ${noun}, and where each came from`, time: (r) => r.d }
            : { subtitle: `jobs fetched this ${noun} that reached "${labels[s.k] || s.label}"`,
              facets: [{ label: "band", allLabel: "All bands", of: (r) => r.band },
                { label: "feed", allLabel: "All feeds", of: (r) => r.feed_source }] }),
    };
  });
  const drops = stages.slice(1).map((s, i) => {
    const prev = stages[i];
    const n = Math.max(0, (prev.n || 0) - (s.n || 0));
    return { n, label: dropWord[s.k] || `did not reach ${labels[s.k] || s.label}` };
  });
  const flow = funnel(funStages, { drops, title: `This ${noun}'s jobs` });

  const blocks = [head, flow];

  // the names behind the last two numbers — who, when, where from
  const won = stages.find((s) => s.k === "won") || {};
  const iv = stages.find((s) => s.k === "interviewed") || {};
  const wonLine = namesLine(`Won this ${noun}`, won.names, noun);
  const ivLine = namesLine(`Interviews this ${noun}`, iv.names, noun);
  if (wonLine || ivLine) {
    blocks.push(el("div", "callout ok names", [
      icon("circle-check", { size: 16 }),
      el("div", "stack-1", [wonLine, ivLine].filter(Boolean)),
    ]));
  } else {
    blocks.push(el("div", "muted small", `No interview or contract started this ${noun} yet.`));
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
    const cols = typeof def.cols === "function" ? def.cols(noun) : def.cols;
    host.appendChild(table({ columns: cols, rows: rows.slice(0, TABLE_PEEK), wrapClass: "flush" }));
    host.appendChild(el("div", "card-foot hstack", [
      el("span", "muted small", `${fmt.num(Math.min(TABLE_PEEK, rows.length))} of ${fmt.num(total)}${typeof def.cols === "function" ? ", newest first" : " · sorted by score"}`),
      total > TABLE_PEEK ? btn(`show all ${fmt.num(total)}`, () => openRows(ctx, title, params, cols,
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
    runButton(ctx, d, "upwork_connects", "Update connects", { variant: "ghost" }),
  ]);
  return strip;
}

/* ── 3. Health ────────────────────────────────────────────────────────────── */

// what each rail DOES, in the words he would use — the registry names the
// task; this names the job
// (healthView lived here until 2026-11-01 — see TABS)
