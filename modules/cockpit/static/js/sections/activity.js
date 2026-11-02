// ════════════════════════════════════════════════════════════════════════════
// sections/activity.js — "where can I see everything the system is doing on a
// daily basis, and every run, with details."  (brief §3 — the surface Abhijeet
// asked for by name.)
//
// THREE STACKED REGIONS, top to bottom:
//
//   1 DAILY DIGEST   plain language, one line per real thing, with the
//                    previous-day comparison ONLY where the payload actually
//                    carries a prev_* (never a computed guess). Fathom
//                    transcripts lead, because they are the thing he named.
//                    Registry changes come from `registry` — git commits on
//                    config/clients.yaml, each with sha/subject/insertions —
//                    which is the only record that "2 clients added" happened.
//                    `empty: true` renders "nothing ran", not a shell of zeros.
//
//   2 RUN LOG        every row in runs, newest first, filterable by task and
//                    status, one row expands IN PLACE into the full detail:
//                    readable metrics, the anomalies that run raised, its
//                    model calls, and on a failure the error + Re-run.
//
//   3 SYSTEM HEALTH  v1's System tab in full — Run-now, the four cards, and
//                    the Jobs / Source health / Anomalies / Doctor checks /
//                    Shadow ledger tables. The per-status copy there is
//                    VERBATIM from v1 on purpose: each phrase was written
//                    after a specific misread ("never produced a successful
//                    run" is not the same fault as "ran but output was thin").
//
// THE COST HONESTY RULE (this file's one hard-won rule):
//   `claude_usage.run_id` has never been written by any task. So a run's cost
//   is almost always a TIME-WINDOW match on (task, started..completed), which
//   is an inference, not a ledger link. Every windowed figure here is prefixed
//   `≈` and says so; `usage.attribution` ("run_id" | "window" | "none") drives
//   the wording, and `spend.unattributed_usd` — the spend no run can claim —
//   gets its OWN line and is never folded into a total.
//
// CONTRACT (enforced by app.js): meta + async render(root, ctx). Own nothing
// global. ui.js for primitives, drill.js for drills, charts.js for charts.
// `—` = not measured · `0` = a real zero and it says why. No send route.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, clear, fmt, dash, icon, heading, card, kpi, pill, btn, linkNum,
  table, emptyState, notice, expander, skeleton, toast, extLink, copyText,
  iconBtn, describe, withBusy,
} from "../ui.js";
import { openDrill } from "../drill.js";
import { chart, axes } from "../charts.js";
import { subtabs } from "../subtabs.js";
import { runButton } from "./upwork.js";

export const meta = { title: "Activity", hint: "everything the system did, run by run" };

const TREND_KEY = "ew_cockpit_activity_trend";     // "7" | "14" | "30"
const TREND_DEFAULT = 14;

/* ── day helpers ──────────────────────────────────────────────────────────────
   The server's day is an IST CALENDAR DATE. The browser may sit in any zone, so
   "today" is computed as UTC+5:30 explicitly rather than from the local clock —
   otherwise the date picker and the payload disagree either side of midnight. */

function istToday() {
  return new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10);
}

function shiftDay(iso, n) {
  const d = new Date(String(iso).slice(0, 10) + "T00:00:00Z");
  if (Number.isNaN(+d)) return iso;
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function normDay(raw) {
  const s = String(raw || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : null;
}

function daysEndingAt(day, n) {
  const out = [];
  for (let i = n - 1; i >= 0; i--) out.push(shiftDay(day, -i));
  return out;
}

/** "16:56" — the log is already scoped to one day, so the date is noise. */
const hhmm = (ts) => (ts ? String(ts).slice(11, 16) : null);

/* ── small shared bits ────────────────────────────────────────────────────── */

const STATUS_TONE = { completed: "completed", failed: "failed", running: "info" };

function statusPill(s) {
  const name = String(s || "unknown");
  return pill(name.replace(/_/g, " "), STATUS_TONE[name] || "outline");
}

function doctorTone(worst) {
  const w = String(worst || "").toUpperCase();
  if (w === "PASS" || w === "GREEN") return "ok";
  if (w === "WARN") return "warn";
  if (w === "?" || w === "") return "outline";
  return "fail";
}

/**
 * "yesterday 4 (+1)" — rendered ONLY from a prev_* the server sent. A missing
 * comparison prints nothing rather than implying a zero baseline.
 */
function vsPrev(cur, prev, f = fmt.num) {
  if (cur == null || prev == null) return null;
  const diff = Number(cur) - Number(prev);
  const sign = diff > 0 ? "+" : "−";
  return el("span", "muted small nowrap",
    `yesterday ${f(prev)}` + (diff ? ` (${sign}${f(Math.abs(diff))})` : " · no change"));
}

/** one digest line: an icon, a sentence, an optional sub-line, a right slot. */
function digestLine(iconName, tone, body, sub, right) {
  return el("div", "lead-row", [
    el("div", "grow", [
      el("div", "hstack", [
        icon(iconName, { size: 15, class: tone || "muted" }),
        el("div", "grow", body),
      ]),
      sub ? el("div", { class: "muted small", style: { marginTop: "3px", paddingLeft: "23px" } }, sub) : null,
    ]),
    right ? el("div", "lead-right", right) : null,
  ]);
}

/* ── render ───────────────────────────────────────────────────────────────── */

export async function render(root, ctx) {
  const day = normDay(ctx.params.get("day")) || istToday();
  const focusRun = String(ctx.params.get("run") || "").trim();

  const act = await ctx.api("/api/activity?day=" + encodeURIComponent(day));

  // a malformed ?day= returns {error}, not a 500 — say so and keep the picker
  if (act && act.error) {
    root.append(
      heading("Activity", "— everything the system did, run by run"),
      dayBar(day, ctx),
      el("div", "errstate", String(act.error)),
    );
    return;
  }

  // System is a second read: if it fails the daily digest must still render.
  const sys = await ctx.api("/api/system").catch((e) => {
    if (e && e.unauthorized) throw e;               // let the router unlock
    return { __error: String((e && e.message) || e) };
  });

  // REDESIGNED 2026-08-26 in the Upwork tab's style (Abhijeet, 05:20): one
  // 7,700-pixel page became four views — Today (the day in words, run by
  // run), Health (every system in words with Run now, the Upwork Health
  // table for the whole machine), Every system (the latest run of each
  // task), Spend (the model bill). Nothing that was on the page is gone.
  const srcs = ((sys.sources || {}).sources) || [];
  const attention = srcs.filter((x) => x.status && x.status !== "ok");
  const r = act.runs || {}, sp = act.spend || {};
  root.append(el("div", "up-head", [
    el("div", "hstack", [el("h2", "up-title", "Activity"), el("span", "muted small", fmt.date(day))]),
    el("p", "up-lede", [
      el("b", null, `${fmt.num(r.total || 0)} run${(r.total || 0) === 1 ? "" : "s"}`),
      ` ${day === istToday() ? "today" : "that day"}`,
      r.failed ? [", ", el("b", "neg", `${r.failed} failed`)] : (r.total ? ", all fine" : ""),
      " · ", el("b", null, fmt.usd2(sp.usd || 0)), " spent on the model",
      " · ", attention.length
        ? el("b", "neg", `${attention.length} system${attention.length === 1 ? "" : "s"} need${attention.length === 1 ? "s" : ""} a look`)
        : el("b", "pos", "every system is fine"),
    ]),
  ]));

  await subtabs(root, {
    section: "activity", ctx,
    tabs: [{ id: "today", label: day === istToday() ? "Today" : "The day" }, { id: "health", label: "Health" },
      { id: "systems", label: "Every system" }, { id: "spend", label: "Spend" }],
    badge: (id) => {
      if (id === "health" && attention.length) return el("span", "n warn", String(attention.length));
      if (id === "systems") return el("span", "n", String((sys.jobs || []).length));
      return null;
    },
    views: {
      today: () => el("div", "stack", [dayBar(day, ctx), ...digestRegion(act, ctx, day), ...runLogRegion(act, ctx, focusRun)]),
      health: () => el("div", "stack", healthView(sys, ctx, attention)),
      systems: () => el("div", "stack", systemsView(sys, ctx)),
      spend: () => el("div", "stack", spendView(sys, act, ctx, day)),
    },
  });
}

/* ══ HEALTH — every system in words, with Run now ═════════════════════════ */

const AGO = (h) => {
  if (h == null) return "never";
  if (h < 1) return `${Math.round(h * 60)} min ago`;
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
};

function statusWords(s) {
  if (s.status === "ok") return ["OK", "ok", ""];
  if (s.status === "down") return ["Not running", "bad", "has never produced a run"];
  if (s.status === "degraded") return ["Needs a look", "warn", "ran, but its output was thin"];
  const late = s.hours_overdue || 0;
  const by = late >= 48 ? `${Math.round(late / 24)} days` : `${Math.round(late)} h`;
  const every = s.max_age_hours >= 48 ? `${Math.round(s.max_age_hours / 24)} days` : `${s.max_age_hours} h`;
  return ["Late", "warn", `expected every ${every} — ${by} overdue`];
}

function healthView(sys, ctx, attention) {
  const out = [];
  if (sys.__error) {
    out.push(el("div", "errstate", "System health could not be read: " + sys.__error));
    return out;
  }
  const srcs = ((sys.sources || {}).sources) || [];
  const rerun = new Set(sys.rerun_jobs || []);
  if (attention.length) {
    out.push(el("div", "srcline bad", [icon("alert-triangle", { size: 15 }),
      el("div", "grow", [el("b", null, `${attention.length} system${attention.length === 1 ? "" : "s"} need${attention.length === 1 ? "s" : ""} a look`),
        ` — ${attention.map((x) => x.label).join(", ")}.`])]));
  } else {
    out.push(el("div", "srcline ok", [icon("circle-check", { size: 15 }), el("div", "grow", "Every system has run on time.")]));
  }
  // Two tables, not one (Abhijeet, 2026-11-02: "all the systems that I can't
  // trigger manually should have a separate section and the ones I can
  // trigger should be separate"). The split is the SERVER's rerun allowlist —
  // the same fact the Run now button already obeyed — so the two cannot
  // disagree. Within each, anything not OK floats to the top.
  const rows = srcs.slice().sort((a, b) => (a.status === "ok") - (b.status === "ok"));
  const canRunNow = rows.filter((s) => s.task_name && rerun.has(s.task_name));
  const scheduledOnly = rows.filter((s) => !(s.task_name && rerun.has(s.task_name)));
  const railTable = (list, withRun) => {
    const tbl = el("table", "rails-table");
    tbl.appendChild(el("thead", null, el("tr", null, ["System", "What it does", "Last ran", "Status", withRun ? "" : "Runs"].map((h) => el("th", null, h)))));
    const tb = el("tbody");
    list.forEach((s) => {
      const [word, tone, why] = statusWords(s);
      const every = s.max_age_hours == null ? "" : s.max_age_hours >= 48 ? `every ${Math.round(s.max_age_hours / 24)} days` : `every ${s.max_age_hours} h`;
      tb.appendChild(el("tr", tone === "ok" ? "" : "attn", [
        el("td", null, [el("i", "dot " + tone), el("b", null, s.label)]),
        el("td", "muted small", s.why || ""),
        el("td", null, s.last_success ? [fmt.dateTime(s.last_success), el("span", "muted small", ` · ${AGO(s.age_hours)}`)] : el("span", "muted", "never")),
        el("td", null, [el("span", "st " + tone, word), why ? el("span", "muted small", ` ${why}`) : null]),
        el("td", "right", withRun
          ? runButton(ctx, {}, s.task_name, "Run now", { quiet: true, variant: "ghost" })
          : el("span", "muted small", s.manual ? "fed by hand" : every ? `on its own, ${every}` : "on its own")),
      ]));
    });
    tbl.appendChild(tb);
    return el("div", "card flush up-table", tbl);
  };
  const nOff = (list) => list.filter((s) => s.status !== "ok").length;
  out.push(heading("You can run these now", `— ${canRunNow.length} systems${nOff(canRunNow) ? ` · ${nOff(canRunNow)} need a look` : " · all fine"}`, "h3"));
  out.push(railTable(canRunNow, true));
  out.push(el("p", "muted small foot", "Run now starts the system and waits for it to finish, then says what happened. Nothing on this page sends anything to a client."));
  out.push(heading("These run on their own", `— ${scheduledOnly.length} systems${nOff(scheduledOnly) ? ` · ${nOff(scheduledOnly)} need a look` : " · all fine"} · launchd starts them; a kick from here is not allowed for them (they send, or they are the cockpit itself)`, "h3"));
  out.push(railTable(scheduledOnly, false));
  out.push(heading("The machine itself", null, "h3"));
  out.push(healthCards(sys));
  const an = sys.anomalies || {};
  const anoms = (an.recent || []).filter((a) => !a.recovered);
  if (anoms.length) {
    out.push(heading("Open problems", `— ${anoms.length} · newest first`, "h3"));
    out.push(table({
      columns: [
        { key: "last", label: "When", render: (a) => el("span", "muted", fmt.dateTime(a.last)) },
        { key: "task_name", label: "System", render: (a) => (sys.task_words || {})[a.task_name] || a.task_name },
        { key: "kind", label: "What", render: (a) => String(a.kind || "").replace(/_/g, " ") },
        { key: "n", label: "Times", num: true, render: (a) => fmt.num(a.n) },
        { key: "severity", label: "How bad", render: (a) => pill(a.severity === "critical" ? "serious" : "a warning", a.severity === "critical" ? "red" : "amber") },
      ],
      rows: anoms,
      empty: "None.",
    }));
  }
  out.push(expander("Every self-check (the doctor)", (body) => {
    body.appendChild(table({
      columns: [
        { key: "name", label: "Check" },
        { key: "status", label: "Status", render: (c) => pill(c.status, String(c.status || "").toLowerCase()) },
        { key: "detail", label: "Detail", cls: "muted" },
      ],
      rows: (sys.doctor || {}).checks || [],
      empty: "The doctor reported no checks.",
    }));
  }));
  return out;
}

/* ══ EVERY SYSTEM — the latest run of each task, in words ═════════════════ */

function systemsView(sys, ctx) {
  if (sys.__error) return [el("div", "errstate", "Could not be read: " + sys.__error)];
  const words = sys.task_words || {};
  const rerun = new Set(sys.rerun_jobs || []);
  return [
    heading("Every system", "— the latest run of each, newest first", "h3"),
    table({
      columns: [
        { key: "task_name", label: "System", render: (j) => el("span", null, [el("b", null, words[j.task_name] || j.task_name.replace(/_/g, " ")), el("span", "muted small mono", ` ${j.task_name}`)]) },
        { key: "status", label: "Last run", render: (j) => (j.status === "completed" ? el("span", "pos", "finished") : j.status === "skipped_empty" ? el("span", "muted", "nothing to do") : j.status === "running" ? el("span", null, "running now") : el("span", "neg", j.status)) },
        { key: "started_at", label: "When", render: (j) => el("span", "muted", fmt.dateTime(j.started_at)) },
        { key: "duration_ms", label: "Took", num: true, render: (j) => dash(j.duration_ms == null ? null : j.duration_ms / 1000, fmt.duration) },
        { key: "run", label: "", render: (j) => (rerun.has(j.task_name) ? runButton(ctx, {}, j.task_name, "Run now", { quiet: true, variant: "ghost" }) : null) },
      ],
      rows: sys.jobs || [],
      empty: "No system has ever run.",
    }),
    expander("Writes held while shadowed", (body) => {
      const byTask = (sys.shadow || {}).by_task || {};
      const shRows = Object.entries(byTask).map(([task, n]) => ({ task, n }));
      body.appendChild(table({
        columns: [
          { key: "task", label: "System", render: (x) => words[x.task] || x.task },
          { key: "n", label: "Held writes", num: true, render: (x) => fmt.num(x.n) },
        ],
        rows: shRows.sort((a, b) => b.n - a.n), empty: "None.",
      }));
    }),
  ];
}

/* ══ SPEND — the model bill ═══════════════════════════════════════════════ */

function spendView(sys, act, ctx, day) {
  const sp = sys.spend || {};
  const words = sys.task_words || {};
  const over = sp.today_usd != null && sp.soft_limit_usd != null && sp.today_usd > sp.soft_limit_usd;
  const flow = el("div", { class: "flow", style: { gridTemplateColumns: "repeat(3, 1fr)" } }, [
    el("div", "stage", [el("div", "l", "Today"), el("div", "v" + (over ? " neg" : ""), fmt.usd2(sp.today_usd || 0)),
      el("div", "s", `of ${fmt.usd2(sp.soft_limit_usd)} a day${over ? " — over the line" : ""} · hard stop ${fmt.usd2(sp.hard_limit_usd)}`)]),
    el("div", "stage", [el("div", "l", "This month"), el("div", "v", fmt.usd2(sp.mtd_usd || 0)),
      el("div", "s", "every model call, exact")]),
    el("div", "stage", [el("div", "l", "Held writes"), el("div", "v", fmt.num((sys.shadow || {}).pending || 0)),
      el("div", "s", "things the system would have written to Airtable, ClickUp or email while shadowed")]),
  ]);
  const byTask = (sp.by_task || []).slice().sort((a, b) => (b.usd || 0) - (a.usd || 0));
  return [
    flow,
    heading("Today, by system", byTask.length ? `— ${byTask.length}` : "— nothing spent today", "h3"),
    table({
      columns: [
        { key: "task", label: "System", render: (x) => words[x.task || x.task_name] || x.task || x.task_name },
        { key: "calls", label: "Calls", num: true, render: (x) => fmt.num(x.calls || x.n || 0) },
        { key: "usd", label: "Cost", num: true, render: (x) => fmt.usd2(x.usd || 0) },
      ],
      rows: byTask, empty: "No model call was billed today.",
    }),
    trendCard(ctx, day),
  ];
}

/* ── the date bar ─────────────────────────────────────────────────────────── */

function dayBar(day, ctx) {
  const today = istToday();
  const go = (d) => ctx.navigate("activity", { day: d });

  const picker = el("input", {
    type: "date", value: day, class: "input",
    attrs: { max: today, "aria-label": "show the day" },
    on: { change: (e) => { const d = normDay(e.target.value); if (d) go(d); } },
  });

  const label = day === today ? "today"
    : day === shiftDay(today, -1) ? "yesterday"
      : fmt.date(day);

  return el("div", "card", el("div", "between", [
    el("div", "hstack", [
      icon("calendar", { size: 15, class: "muted" }),
      el("strong", null, fmt.date(day)),
      el("span", "muted small", "· " + label + " · IST calendar day"),
    ]),
    el("div", "hstack-tight", [
      btn("Previous", () => go(shiftDay(day, -1)), { sm: true, icon: "chevron-left" }),
      picker,
      btn("Next", () => go(shiftDay(day, 1)),
        { sm: true, iconRight: "chevron-right", disabled: day >= today }),
      btn("Today", () => go(today), { sm: true, disabled: day === today }),
    ]),
  ]));
}

/* ══ 1. DAILY DIGEST ══════════════════════════════════════════════════════ */

function digestRegion(act, ctx, day) {
  const out = [heading("What the system did", "— " + fmt.date(day), "h3")];

  if (act.empty) {
    out.push(el("div", "card", emptyState(
      "Nothing ran on " + fmt.date(day) + ".",
      {
        icon: "moon",
        title: "Nothing ran",
        detail: "No run started, nothing was ingested, no fact was extracted,"
          + " the client registry did not change, no model call was billed and"
          + " no card was opened or closed. This is a real empty day, not a"
          + " failed read — walk back with the date picker above.",
      })));
    out.push(trendCard(ctx, day));
    return out;
  }

  out.push(glanceCards(act, ctx));
  out.push(digestCard(act, ctx));
  out.push(trendCard(ctx, day));
  return out;
}

/** five figures, each a real button, each drilling to the rows behind it. */
function glanceCards(act, ctx) {
  const g = el("div", "grid");
  const ing = act.ingest || {}, f = act.facts || {}, r = act.runs || {},
    sp = act.spend || {}, reg = act.registry || {};

  // 1) FATHOM — the thing he asked for by name, so it leads. And it is the one
  //    figure whose drill FETCHES: /api/activity/sources reconstructs each
  //    call's story (attribution, facts, dossier, cost, registry).
  const fathom = ing.fathom_transcripts;
  g.appendChild(kpi({
    label: "Fathom transcripts",
    value: dash(fathom, fmt.num),
    sub: fathom === 0
      ? "a true zero — the Fathom poll runs inside kb_daily; "
        + (ranToday(r, "kb_daily")
          ? "kb_daily ran today and found no new call to pull"
          : "kb_daily has not run yet on this day")
      : `pulled into the knowledge base · ${dash(ing.extracted, fmt.num)} of `
        + `${dash(ing.total, fmt.num)} sources today reached extraction`
        + " · which calls, and how each was attributed →",
    onClick: (e) => drillSources(ctx, act, "fathom", e),
  }));

  // 2) FACTS
  g.appendChild(kpi({
    label: "Facts extracted",
    value: dash(f.total, fmt.num),
    sub: f.total === 0
      ? "a true zero — nothing was extracted from any source on this day"
      : `across ${dash(f.clients, fmt.num)} client${f.clients === 1 ? "" : "s"}`
        + ` · yesterday ${fmt.num(f.prev_total)} · every fact, with its source →`,
    onClick: (e) => drillFactRows(ctx, act, null, e),
  }));

  // 3) REGISTRY — invisible until now; every approval writes a git commit
  const regUnmeasured = !!reg.note || reg.count == null;
  append(g, kpi({
    label: "Registry changes",
    value: regUnmeasured ? null : fmt.num(reg.count),
    unmeasured: regUnmeasured,
    stale: !!reg.note,
    sub: reg.note ? reg.note
      : reg.count === 0
        ? "a true zero — config/clients.yaml was not touched on this day"
        : `commits on ${reg.file} · +${fmt.num(reg.insertions)} −${fmt.num(reg.deletions)} lines`,
    onClick: reg.count ? () => drillRegistry(act) : null,
  }));

  // 4) RUNS
  g.appendChild(kpi({
    label: "Runs",
    value: dash(r.total, fmt.num),
    warn: r.failed ? `${fmt.num(r.failed)} failed` : null,
    sub: `${fmt.num(r.completed)} completed · ${fmt.num(r.skipped)} skipped`
      + (r.running ? ` · ${fmt.num(r.running)} still running` : "")
      + ` · yesterday ${fmt.num(r.prev_total)}`,
    onClick: () => drillRuns(act),
  }));

  // 5) SPEND — exact (claude_usage.ist_date is authoritative for the DAY; only
  //    the per-RUN split below is inferred)
  g.appendChild(kpi({
    label: "Model spend",
    value: dash(sp.usd, fmt.usd2),
    sub: sp.usd === 0
      ? "a true zero — no model call was billed on this day"
      : `${fmt.num(sp.calls)} calls · yesterday ${fmt.usd2(sp.prev_usd)}`,
    onClick: () => drillSpendByTask(act),
  }));

  return g;
}

/** did this task run at all on this day? (drives the honest zero-notes) */
function ranToday(runs, task) {
  return (runs.by_task || []).some((t) => t.task === task);
}

function digestCard(act, ctx) {
  const ing = act.ingest || {}, f = act.facts || {}, r = act.runs || {},
    sp = act.spend || {}, reg = act.registry || {}, th = act.threads || {},
    nd = act.nudges || {}, sh = act.shadow || {};
  const box = el("div", "card flush");

  // ── FATHOM, unmissable ──────────────────────────────────────────────────
  const nF = ing.fathom_transcripts;
  box.appendChild(el("div", { style: { padding: "var(--space-4) var(--space-5) 0" } },
    nF
      ? notice("ok", `${fmt.num(nF)} Fathom call transcript${nF === 1 ? " was" : "s were"}`
        + ` transcribed and added to the knowledge base today.`, {
        children: el("div", { class: "small", style: { marginTop: "4px" } },
          "Fathom is polled inside the kb_daily task. Open each call below for"
          + " who was on it, which client it was filed under and how that was"
          + " decided, what came out of it, and what it cost."),
        action: btn(nF === 1 ? "Show the call" : `Show all ${fmt.num(nF)} calls`,
          (e) => drillSources(ctx, act, "fathom", e), { sm: true, icon: "arrow-up-right" }),
      })
      : notice("info", "No Fathom transcript came in today.", {
        children: el("div", { class: "small", style: { marginTop: "4px" } },
          ranToday(r, "kb_daily")
            ? "kb_daily ran and found no new call to pull — a real zero, not a gap."
            : "kb_daily has not run yet on this day, so nothing has been polled."),
      })));

  // ── sources ingested ────────────────────────────────────────────────────
  const byType = ing.by_source_type || {};
  const typeBits = Object.entries(byType)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${fmt.num(n)} ${k}`).join(" · ");
  box.appendChild(digestLine("inbox", "muted", [
    linkNum(fmt.num(ing.total), (e) => drillSources(ctx, act, null, e),
      { label: `show the ${fmt.num(ing.total)} sources ingested on this day` }),
    ing.total === 1 ? " source was ingested" : " sources were ingested",
    typeBits ? " — " + typeBits + "." : ".",
  ],
    ing.total
      ? `${fmt.num(ing.extracted)} of them reached extraction`
        + (ing.extracted === 0 ? " — a true zero: nothing has been read for facts yet" : "")
      : "a true zero — no source landed on this day",
    vsPrev(ing.total, ing.prev_total)));

  // ── facts ───────────────────────────────────────────────────────────────
  box.appendChild(digestLine("book-open", "muted", [
    linkNum(fmt.num(f.total), (e) => drillFactRows(ctx, act, null, e),
      { label: `show every fact extracted on this day` }),
    f.total === 1 ? " fact was extracted across " : " facts were extracted across ",
    linkNum(fmt.num(f.clients), () => drillFacts(ctx, act),
      { label: "show the per-client fact counts" }),
    f.clients === 1 ? " client." : " clients.",
  ],
    (f.by_client || []).length
      ? "most on " + f.by_client.slice(0, 3)
        .map((c) => `${c.client} (${fmt.num(c.n)})`).join(", ")
      : "a true zero — no source produced a fact on this day",
    vsPrev(f.total, f.prev_total)));

  // ── registry (git commits on clients.yaml) ──────────────────────────────
  if (reg.note) {
    box.appendChild(digestLine("file-text", "neg",
      "Registry history could not be read for this day.", reg.note));
  } else if (reg.count) {
    box.appendChild(digestLine("file-text", "pos", [
      "The client registry changed — ",
      linkNum(fmt.num(reg.count), () => drillRegistry(act)),
      reg.count === 1 ? " audited commit on " : " audited commits on ",
      el("code", null, reg.file),
      ` (+${fmt.num(reg.insertions)} −${fmt.num(reg.deletions)} lines).`,
    ], (reg.commits || []).slice(0, 3).map((c) => c.subject).join(" · ")));
  } else {
    box.appendChild(digestLine("file-text", "muted",
      "The client registry did not change today.",
      `a true zero — no commit touched ${reg.file || "config/clients.yaml"};`
      + " every approval writes one, so no commit means no approval landed"));
  }

  // ── threads / attribution ───────────────────────────────────────────────
  box.appendChild(digestLine("message-square", "muted", [
    el("b", null, fmt.num(th.new)),
    th.new === 1 ? " new thread was seen · " : " new threads were seen · ",
    el("b", null, fmt.num(th.mapping_resolved)),
    " event" + (th.mapping_resolved === 1 ? " was" : "s were") + " resolved to a client.",
  ], (th.note ? th.note + " · " : "")
    + `${fmt.num(th.attributed_total)} threads attributed in total,`
    + ` ${fmt.num(th.unattributed_total)} still unattributed`,
  vsPrev(th.new, th.prev_new)));

  // ── runs ────────────────────────────────────────────────────────────────
  box.appendChild(digestLine("activity", r.failed ? "neg" : "muted", [
    linkNum(fmt.num(r.total), () => drillRuns(act)),
    r.total === 1 ? " run started — " : " runs started — ",
    `${fmt.num(r.completed)} completed, ${fmt.num(r.skipped)} skipped, `,
    r.failed
      ? el("b", "neg", `${fmt.num(r.failed)} failed`)
      : el("span", null, "0 failed"),
    r.running ? `, ${fmt.num(r.running)} still running` : "",
    ` · ${fmt.duration(r.wall_seconds)} of wall time.`,
  ],
  r.failed === 0
    ? "the 0 is a true zero — every run on this day ended completed, skipped, or is still going"
    : "open the failed runs in the log below for the error and a re-run",
  vsPrev(r.total, r.prev_total)));

  // ── spend, and then the part of it no run can claim ─────────────────────
  box.appendChild(digestLine("wallet", "muted", [
    linkNum(fmt.usd2(sp.usd), () => drillSpendByTask(act)),
    ` was billed across ${fmt.num(sp.calls)} model call${sp.calls === 1 ? "" : "s"}.`,
  ],
  sp.calls === 0 ? "a true zero — no model call was made on this day" : null,
  vsPrev(sp.usd, sp.prev_usd, fmt.usd2)));

  if (sp.calls) {
    const un = sp.unattributed_usd;
    box.appendChild(digestLine("alert-triangle", un ? "neg" : "pos", [
      el("b", null, fmt.usd2(un)),
      un
        ? " of that spend could not be tied to any run."
        : " of that spend is unattributed — every call fell inside a run's window.",
    ], un
      ? "cost is matched to a run by task + time window (claude_usage.run_id has"
        + " never been written), so a call logged under a different task name than"
        + " the run — kb_extract inside kb_daily, for example — matches nothing."
        + " It is shown on its own line and is never folded into a per-run figure."
      : "a true zero — nothing was left over after the window match"));
  }

  // ── nudges ──────────────────────────────────────────────────────────────
  box.appendChild(digestLine("list-checks", "muted", [
    linkNum(fmt.num(nd.created), () => drillNudges(act)),
    nd.created === 1 ? " card was opened, " : " cards were opened, ",
    el("b", null, fmt.num(nd.closed)),
    " closed",
    nd.closed ? ` (${fmt.num(nd.done)} done, ${fmt.num(nd.dismissed)} dismissed).` : ".",
  ],
  nd.created === 0 ? "a true zero — the sweep opened nothing new on this day" : null,
  vsPrev(nd.created, nd.prev_created)));

  // ── shadow ledger ───────────────────────────────────────────────────────
  box.appendChild(digestLine("eye-off", "muted", [
    sh.total
      ? linkNum(fmt.num(sh.total), () => drillShadowDay(act))
      : el("b", null, "0"),
    sh.total === 1 ? " write intent was held (not sent)." : " write intents were held (not sent).",
  ], sh.total
    ? "shadow mode captured these instead of writing them out — cutover evidence"
    : "a true zero — no module tried an external write on this day, or the module is live"));

  return box;
}

/* ── the trend chart (loads after first paint; re-themes by construction) ─── */

function trendCard(ctx, day) {
  const host = el("div", "card");
  const body = el("div");
  const stored = parseInt(localStorage.getItem(TREND_KEY), 10);
  let days = [7, 14, 30].includes(stored) ? stored : TREND_DEFAULT;

  // The visual `.on` and `aria-pressed` are set by ONE function. They used to
  // be set in two places — the class in the click handler, aria-pressed only at
  // construction — so after the first click the screen said "30d" and the
  // accessibility tree still said 14d was the pressed one. A stale
  // aria-pressed is worse than none: it is a confident wrong answer.
  const seg = el("div", {
    class: "segmented",
    attrs: { role: "group", "aria-label": "trend range" },
  });
  function paintSeg() {
    seg.querySelectorAll(".segbtn").forEach((b) => {
      const on = Number(b.dataset.days) === days;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", String(on));
    });
  }
  [7, 14, 30].forEach((n) => seg.appendChild(el("button", {
    class: "segbtn", type: "button",
    dataset: { days: String(n) },
    attrs: { "aria-pressed": String(n === days), "aria-label": `show ${n} days` },
    on: {
      click: () => {
        days = n;
        localStorage.setItem(TREND_KEY, String(n));
        paintSeg();
        load();
      },
    },
  }, n + "d")));
  paintSeg();

  host.append(
    el("div", "card-head", [
      el("h3", "card-title", "Runs and spend, day by day"),
      seg,
    ]),
    body,
  );

  // the chart handle is kept so a range change DESTROYS the previous chart —
  // charts.js keeps every live handle for the theme flip, and an orphan whose
  // canvas has been cleared out of the DOM would be refreshed forever.
  let handle = null;
  function load() {
    if (handle) { try { handle.destroy(); } catch (e) { /* already gone */ } handle = null; }
    clear(body).appendChild(skeleton({ lines: 4, height: 16 }));
    drawTrend(body, ctx, day, days).then((h) => { handle = h; });
  }
  load();
  return host;
}

async function drawTrend(body, ctx, day, days) {
  const list = daysEndingAt(day, days);
  let payloads;
  try {
    payloads = await Promise.all(list.map((d) =>
      ctx.api("/api/activity?day=" + encodeURIComponent(d)).catch(() => null)));
  } catch (e) {
    clear(body).appendChild(el("div", "errstate",
      "Could not read the trend: " + String((e && e.message) || e)));
    return null;
  }
  if (!body.isConnected) return null;            // the section was navigated away

  const pt = list.map((d, i) => {
    const p = payloads[i];
    // a day we could NOT read stays null (a gap in the line) — never a 0
    if (!p || p.error) return { d, ok: false };
    return {
      d, ok: true,
      completed: p.runs.completed, failed: p.runs.failed,
      skipped: p.runs.skipped, running: p.runs.running,
      total: p.runs.total, usd: p.spend.usd,
    };
  });
  const missing = pt.filter((x) => !x.ok).length;
  const val = (k) => pt.map((x) => (x.ok ? x[k] : null));
  const any = (k) => pt.some((x) => x.ok && x[k]);

  clear(body);
  const wrap = el("div");
  body.appendChild(wrap);

  const series = [
    ["completed", "Completed", 0],
    ["skipped", "Skipped", 3],
    ["running", "Running", 2],
    ["failed", "Failed", null],
  ].filter(([k]) => any(k));

  const handle = chart(wrap, (p) => ({
    type: "bar",
    data: {
      labels: pt.map((x) => x.d.slice(5)),
      datasets: [
        ...series.map(([k, label, idx]) => ({
          type: "bar",
          label,
          data: val(k),
          backgroundColor: pt.map((x) => {
            const base = idx == null ? p.destructive : p.color(idx);
            return x.d === day ? base : (idx == null ? base : p.soft(idx));
          }),
          stack: "runs",
          borderRadius: 2,
          maxBarThickness: 26,
          yAxisID: "y",
        })),
        {
          type: "line",
          label: "Model spend",
          data: val("usd"),
          borderColor: p.color(4),
          backgroundColor: p.color(4),
          borderWidth: 2,
          tension: 0.3,
          pointRadius: 2,
          pointHoverRadius: 4,
          spanGaps: false,
          yAxisID: "y1",
          order: 0,
        },
      ],
    },
    options: {
      onClick: (evt, els) => {
        if (!els || !els.length) return;
        const d = pt[els[0].index];
        if (d) ctx.navigate("activity", { day: d.d });
      },
      interaction: { mode: "index", intersect: false },
      scales: Object.assign(axes(p, { x: { stacked: true }, y: { stacked: true } }), {
        y1: {
          position: "right", beginAtZero: true,
          grid: { display: false, drawBorder: false },
          border: { display: false },
          // daily spend is cents-scale: whole-dollar ticks would print $0/$1/$1
          ticks: {
            color: p.axis, padding: 6,
            callback: (v) => (Number(v) >= 10 ? fmt.usd(v) : fmt.usd2(v)),
          },
        },
      }),
      plugins: {
        legend: { display: true, position: "bottom" },
        tooltip: {
          callbacks: {
            title: (items) => fmt.date(pt[items[0].dataIndex].d),
            label: (item) => (item.dataset.yAxisID === "y1"
              ? "spend " + fmt.usd2(item.raw)
              : `${item.dataset.label.toLowerCase()} ${fmt.num(item.raw)}`),
          },
        },
      },
    },
  }), { height: 230 });

  wrap.appendChild(el("div", "muted small", [
    `Runs per day, stacked by outcome, with model spend on the right axis.`
    + ` Click a bar to open that day.`,
    missing
      ? ` ${missing} day${missing === 1 ? "" : "s"} in this window could not be read`
        + ` and are drawn as a gap, not as a zero.`
      : "",
  ]));
  return handle;
}

/* ══ 2. RUN LOG ═══════════════════════════════════════════════════════════ */

function runLogRegion(act, ctx, focusRun) {
  const r = act.runs || {};
  const rows = r.rows || [];
  const linkedRun = focusRun;                     // survives the one-shot below
  const out = [heading("Run log", "— newest first · click a run for everything it did", "h3")];

  const box = el("div", "card flush");
  const tasks = [...new Set(rows.map((x) => x.task))].sort();
  const statuses = [...new Set(rows.map((x) => x.status))].sort();
  let fTask = "", fStatus = "";

  const count = el("span", "muted small");
  const host = el("div");

  const sel = (label, values, onChange) => el("select", {
    class: "input", attrs: { "aria-label": label },
    on: { change: (e) => { onChange(e.target.value); paint(); } },
  }, [el("option", { value: "" }, label)].concat(
    values.map((v) => el("option", { value: v }, String(v).replace(/_/g, " ")))));

  const controls = el("div", "card-controls", [
    icon("filter", { size: 14, class: "muted" }),
    sel("All tasks", tasks, (v) => { fTask = v; }),
    sel("All statuses", statuses, (v) => { fStatus = v; }),
    el("span", "grow"),
    count,
  ]);

  function visible() {
    return rows.filter((x) => (!fTask || x.task === fTask)
      && (!fStatus || x.status === fStatus));
  }

  function paint() {
    const list = visible();
    clear(count).append(
      `${fmt.num(list.length)} of ${fmt.num(r.rows_shown)} shown`,
      r.rows_capped
        ? el("span", { class: "neg" },
          ` · the server capped this day at ${fmt.num(r.rows_shown)} of ${fmt.num(r.total)} runs`)
        : "",
    );
    // the deep-linked run auto-opens ONCE. Re-opening it on every filter change
    // would fight whoever is using the filters.
    const focus = focusRun;
    focusRun = "";
    clear(host).appendChild(list.length
      ? runTable(list, ctx, focus)
      : emptyState(rows.length
        ? "No run matches that filter — clear it above."
        : "No run started on this day.", { icon: "inbox" }));
  }

  box.append(controls, host,
    el("div", "card-foot",
      "Cost is prefixed ≈ because it is INFERRED: claude_usage.run_id has never"
      + " been written, so a model call is matched to a run by task + time"
      + " window. A run with no matched call shows — (not $0), and the spend"
      + " that matched no run at all is on its own line in the digest above."));
  paint();
  out.push(box);

  // deep link from Review (#/activity?day=…&run=N) for a run not in this day
  if (linkedRun && !rows.some((x) => String(x.id) === linkedRun)) {
    out.push(standaloneRun(linkedRun, ctx));
  }
  return out;
}

/**
 * The log itself. A real <table> (this is tabular data and the digits must
 * line up), with a detail <tr> inserted directly beneath the row you open —
 * the same expand-in-place idea as the Today queue, so two runs can be read
 * side by side without losing your place.
 */
function runTable(rows, ctx, focusRun) {
  const t = el("table");
  const cols = ["Time", "Task", "Status", "Duration", "What it did", "Cost"];
  t.appendChild(el("thead", null, el("tr", null,
    cols.map((c, i) => el("th", { class: i === 5 ? "num" : "" }, c)))));
  const tb = el("tbody");

  rows.forEach((run) => {
    const open = el("button", {
      class: "linkname", type: "button",
      attrs: { "aria-expanded": "false", "aria-label": `open run ${run.id}` },
    });

    const tr = el("tr", null, [
      el("td", { style: { whiteSpace: "nowrap" } }, open),
      el("td", null, el("span", "mono", run.task)),
      el("td", null, statusPill(run.status)),
      el("td", { class: "num" }, dash(run.duration_ms == null ? null : run.duration_ms / 1000, fmt.duration)),
      el("td", null, run.summary
        ? el("span", "small", run.summary)
        : el("span", "dash", run.status === "running" ? "still running" : "—")),
      el("td", { class: "num" }, run.usd == null
        ? el("span", { class: "dash", title: "no model call could be matched to this run" }, "—")
        : el("span", { title: "inferred by task + time window, not a ledger link" },
          "≈" + fmt.usd2(run.usd))),
    ]);

    // the chevron is swapped rather than rotated: the CSS rotation rule is
    // scoped to .q-title (the Today card), and this is a table row.
    const chevron = (name) => clear(open).append(
      icon(name, { size: 13 }), " ", dash(hhmm(run.started_at)));
    chevron("chevron-right");

    const toggle = () => {
      const nxt = tr.nextElementSibling;
      if (nxt && nxt.classList.contains("run-detail")) {
        nxt.remove();
        open.setAttribute("aria-expanded", "false");
        chevron("chevron-right");
        return;
      }
      const cell = el("td", { colSpan: cols.length });
      const holder = el("tr", "run-detail", cell);
      tr.after(holder);
      open.setAttribute("aria-expanded", "true");
      chevron("chevron-down");
      cell.appendChild(skeleton({ lines: 3 }));
      ctx.api("/api/activity/run?run_id=" + encodeURIComponent(run.id))
        .then((d) => {
          if (!cell.isConnected) return;
          clear(cell).appendChild(readFailed(d)
            ? el("div", "errstate", String((d && d.error) || "unknown run"))
            : runDetail(d, ctx));
        })
        .catch((e) => {
          if (!cell.isConnected) return;
          clear(cell).appendChild(el("div", "errstate",
            "Could not read that run: " + String((e && e.message) || e)));
        });
    };
    open.addEventListener("click", toggle);
    tb.appendChild(tr);

    if (focusRun && String(run.id) === focusRun) {
      // deep-linked from Review: open it, and put it on screen
      setTimeout(() => { toggle(); tr.scrollIntoView({ block: "center" }); }, 0);
    }
  });

  t.appendChild(tb);
  return el("div", "tablewrap", t);
}

/**
 * Did the READ fail, as opposed to the run failing?
 *
 * activity_run() puts the RUN's own error text in `error` — that is the whole
 * point of opening a failed run — and reports a bad/unknown run_id with an
 * `error` and NO `run`. Keying off `error` alone therefore hides the detail of
 * exactly the runs you opened the panel to read. The presence of `run` is the
 * only honest discriminator.
 */
function readFailed(d) {
  return !d || !d.run;
}

/** a run that is not on the day being shown (deep link) — fetched on its own */
function standaloneRun(runId, ctx) {
  const box = card("Linked run #" + runId, skeleton({ lines: 3 }), { class: "" });
  ctx.api("/api/activity/run?run_id=" + encodeURIComponent(runId))
    .then((d) => {
      if (!box.isConnected) return;
      const body = readFailed(d)
        ? el("div", "errstate", String((d && d.error) || "unknown run"))
        : runDetail(d, ctx);
      clear(box).append(
        el("div", "card-head", [
          el("h3", "card-title", `Linked run #${runId}`),
          el("span", "card-sub", d && d.run
            ? `${d.run.task_name} · ${fmt.dateTime(d.run.started_at)}` : ""),
        ]),
        body);
    })
    .catch((e) => {
      if (!box.isConnected) return;
      clear(box).appendChild(el("div", "errstate",
        "Could not read run " + runId + ": " + String((e && e.message) || e)));
    });
  return box;
}

/* ── one run, in full ─────────────────────────────────────────────────────── */

function runDetail(d, ctx) {
  const run = d.run || {};
  const wrap = el("div", "q-detail-inner");
  const kv = (k, v) => {
    if (v == null || v === "") return;
    wrap.appendChild(el("div", "q-kv", [el("span", "k", k), el("span", "v", v)]));
  };

  kv("Run", el("span", "hstack", [
    el("span", "mono", "#" + run.id),
    el("b", null, run.task_name || "—"),
    statusPill(run.status),
    run.shadow ? pill("shadow", "outline") : null,
  ]));
  kv("Started", fmt.dateTime(run.started_at));
  kv("Finished", run.completed_at
    ? fmt.dateTime(run.completed_at)
    : el("span", "dash", run.status === "running" ? "still running" : "—"));
  kv("Duration", dash(run.duration_ms == null ? null : run.duration_ms / 1000, fmt.duration));
  if (d.summary) kv("In one line", d.summary);

  // ── failure: the error, then the only button that can act on it ─────────
  if (d.failed || d.error) {
    const errBox = el("div", { class: "errstate", style: { marginTop: "var(--space-3)" } }, [
      el("div", "strong", "This run failed."),
      el("div", {
        class: "pre mono",
        style: { marginTop: "6px", maxHeight: "220px", overflow: "auto" },
      }, String(d.error || "(no error text was recorded)")),
    ]);
    const actions = el("div", { class: "hstack", style: { marginTop: "var(--space-3)" } });
    if (d.rerunnable) {
      // the allowlist is SERVER-side; `rerunnable` is the server's own answer,
      // so this file never keeps a copy of it that could drift.
      const b = btn("Re-run " + run.task_name, () =>
        ctx.kick("/api/rerun", { task: run.task_name }, b), { variant: "primary" });
      actions.append(b, el("span", "muted small",
        "starts the launchd job again from scratch — it does not replay this"
        + " run's inputs, and nothing is sent anywhere."));
    } else {
      actions.appendChild(el("span", "muted small",
        `${run.task_name} is not in the server's re-run allowlist, so the cockpit`
        + " cannot start it. Run it from the terminal, or wait for its schedule."));
    }
    errBox.appendChild(actions);
    wrap.appendChild(errBox);
  }

  // ── cost, and how honestly it is attached to this run ───────────────────
  const u = d.usage || {};
  const attribution = u.attribution;
  if (attribution === "run_id") {
    wrap.appendChild(el("div", { style: { marginTop: "var(--space-3)" } },
      notice("ok", `${fmt.num(u.calls)} model call${u.calls === 1 ? "" : "s"} · `
        + `${fmt.usd2(u.usd)} — linked to this run by run_id (an exact link).`)));
  } else if (attribution === "window") {
    wrap.appendChild(el("div", { style: { marginTop: "var(--space-3)" } },
      notice("warn", `≈${fmt.usd2(u.usd)} across ${fmt.num(u.calls)} model call`
        + `${u.calls === 1 ? "" : "s"} — INFERRED, not an exact per-run figure.`, {
        children: el("div", { class: "small", style: { marginTop: "4px" } },
          "claude_usage.run_id has never been written by any task, so these calls"
          + " are attached to this run because they carry the same task name and"
          + " fall between its start and finish. Another run of the same task"
          + " overlapping this window would claim them too."),
      })));
  } else {
    wrap.appendChild(el("div", { style: { marginTop: "var(--space-3)" } },
      notice("info", "No model call could be matched to this run.", {
        children: el("div", { class: "small", style: { marginTop: "4px" } },
          "That is a — , not a $0: the run may genuinely have made no model call,"
          + " or its calls were logged under a different task name (kb_extract"
          + " logs separately from the kb_daily run that invokes it) and are"
          + " counted in the digest's unattributed line."),
      })));
  }

  if (u.calls) {
    const rowsShown = (u.rows || []).slice(0, 5);
    // ui.js append(), not the native one: the last child is `… : null` and
    // Element.append(null) writes the literal string "null" into the page.
    append(wrap,
      el("div", { class: "hstack", style: { marginTop: "var(--space-3)" } }, [
        el("span", "muted small",
          `${fmt.num(u.input_tokens)} in / ${fmt.num(u.output_tokens)} out tokens ·`),
        linkNum(`all ${fmt.num(u.calls)} calls`, () => drillUsage(d)),
      ]),
      el("div", { style: { marginTop: "var(--space-2)" } }, table({
        columns: [
          { key: "called_at", label: "At", render: (x) => el("span", "muted", hhmm(x.called_at)) },
          { key: "purpose", label: "Purpose" },
          { key: "model", label: "Model", cls: "mono" },
          { key: "billing_route", label: "Route" },
          {
            key: "total_cost_usd", label: "USD", num: true,
            render: (x) => fmt.usd2(x.total_cost_usd),
          },
        ],
        rows: rowsShown,
        empty: "No model call rows.",
      })),
      u.calls > rowsShown.length
        ? el("div", "muted small", `showing ${rowsShown.length} of ${fmt.num(u.calls)} —`
          + " open the drill above for all of them, with the exact sum")
        : null,
    );
  }

  // ── metrics ─────────────────────────────────────────────────────────────
  const flat = flattenMetrics(d.metrics);
  if (d.metrics_error) {
    wrap.appendChild(el("div", { class: "errstate", style: { marginTop: "var(--space-3)" } },
      String(d.metrics_error)));
  } else if (flat.length) {
    wrap.append(
      el("div", { class: "muted small", style: { marginTop: "var(--space-4)" } },
        `Metrics this run wrote (${fmt.num(flat.length)} values)`),
      el("div", { style: { marginTop: "var(--space-2)" } }, table({
        columns: [
          { key: "k", label: "Key", cls: "mono" },
          {
            key: "v", label: "Value", num: true,
            render: (x) => (x.v == null
              ? el("span", "dash", "—")
              : (typeof x.v === "number" ? fmt.num(x.v) : String(x.v))),
          },
        ],
        rows: flat,
        empty: "No metrics.",
      })),
      expander("Raw metrics JSON", (body) => {
        body.appendChild(el("div", {
          class: "pre mono",
          style: { maxHeight: "320px", overflow: "auto" },
        }, JSON.stringify(d.metrics, null, 2)));
      }),
    );
  } else {
    wrap.appendChild(el("div", { class: "muted small", style: { marginTop: "var(--space-4)" } },
      "This run wrote no metrics — the task records none, or it died before it could."));
  }

  // ── anomalies this run raised ───────────────────────────────────────────
  const an = d.anomalies || [];
  wrap.appendChild(el("div", { class: "muted small", style: { marginTop: "var(--space-4)" } },
    an.length
      ? `Anomalies this run raised (${fmt.num(an.length)})`
      : "This run raised no anomaly."));
  if (an.length) {
    wrap.appendChild(el("div", { style: { marginTop: "var(--space-2)" } }, table({
      columns: [
        { key: "detected_at", label: "Detected", render: (x) => el("span", "muted", fmt.dateTime(x.detected_at)) },
        { key: "kind", label: "Kind", cls: "mono" },
        {
          key: "severity", label: "Severity",
          render: (x) => pill(x.severity, x.severity === "critical" ? "fail" : "warn"),
        },
        {
          key: "detail_json", label: "Detail",
          render: (x) => el("span", { class: "small mono", title: x.detail_json || "" },
            String(x.detail_json || "—").slice(0, 160)),
        },
        {
          key: "resolved_at", label: "Resolved",
          render: (x) => (x.resolved_at ? fmt.dateTime(x.resolved_at) : el("span", "dash", "—")),
        },
      ],
      rows: an,
      empty: "None.",
    })));
  }
  if (d.anomalies_inline) {
    wrap.append(
      el("div", { class: "muted small", style: { marginTop: "var(--space-3)" } },
        "Anomalies recorded inline on the run row"),
      el("div", "pre mono", typeof d.anomalies_inline === "string"
        ? d.anomalies_inline : JSON.stringify(d.anomalies_inline, null, 2)),
    );
  }

  // ── shadow intents this run captured ────────────────────────────────────
  const sh = d.shadow || {};
  if (sh.total) {
    wrap.append(
      el("div", { class: "muted small", style: { marginTop: "var(--space-4)" } },
        `${fmt.num(sh.total)} write intent${sh.total === 1 ? "" : "s"} captured (not sent)`),
      el("div", { style: { marginTop: "var(--space-2)" } }, table({
        columns: [
          { key: "target", label: "Target" },
          { key: "operation", label: "Operation" },
          { key: "n", label: "Intents", num: true, render: (x) => fmt.num(x.n) },
        ],
        rows: sh.rows || [],
        empty: "None.",
      })),
    );
  }

  return wrap;
}

/** metrics → [{k: "feeds.my_feed.new", v: 4}] in the order the task wrote them */
function flattenMetrics(m, path = "", out = []) {
  if (m == null) return out;
  if (typeof m !== "object" || Array.isArray(m)) {
    out.push({ k: path || "(value)", v: Array.isArray(m) ? JSON.stringify(m) : String(m) });
    return out;
  }
  Object.entries(m).forEach(([k, v]) => {
    const p = path ? path + "." + k : k;
    if (v && typeof v === "object" && !Array.isArray(v)) flattenMetrics(v, p, out);
    else if (Array.isArray(v)) out.push({ k: p, v: v.length ? JSON.stringify(v) : "[]" });
    else if (typeof v === "boolean") out.push({ k: p, v: v ? "yes" : "no" });
    else out.push({ k: p, v });
  });
  return out;
}

/* ══ 3. SYSTEM HEALTH (v1's System tab, ported in full) ═══════════════════ */

function systemRegion(d, ctx) {
  const out = [heading("System health", "— the machinery behind those runs", "h3")];
  if (d.__error) {
    out.push(el("div", "errstate",
      "System health could not be read: " + d.__error
      + " — the daily digest above is unaffected."));
    return out;
  }

  out.push(runNow(ctx));
  out.push(healthCards(d));

  // ── Jobs ────────────────────────────────────────────────────────────────
  out.push(heading("Jobs", "— the latest run of every task, by start time", "h3"));
  out.push(table({
    columns: [
      { key: "task_name", label: "Job", cls: "mono" },
      { key: "status", label: "Status", render: (j) => statusPill(j.status) },
      {
        key: "started_at", label: "Last run",
        render: (j) => el("span", "muted", fmt.dateTime(j.started_at)),
      },
      {
        key: "duration_ms", label: "Took", num: true,
        render: (j) => dash(j.duration_ms == null ? null : j.duration_ms / 1000, fmt.duration),
      },
    ],
    rows: d.jobs || [],
    empty: "No job has ever run.",
  }));

  // ── Source health (v1 copy, verbatim) ───────────────────────────────────
  const srcs = ((d.sources || {}).sources) || [];
  if (srcs.length) {
    out.push(heading("Source health",
      "— freshness + degradation of each number-backing source · a critical one"
      + " in trouble turns the health chip amber", "h3"));
    out.push(table({
      columns: [
        {
          key: "label", label: "Source",
          render: (s) => el("span", null, [
            s.label,
            s.critical ? null : el("span", "muted small", " (non-critical)"),
          ]),
        },
        {
          key: "status", label: "Status",
          render: (s) => pill(s.status, s.status === "ok" ? "green"
            : s.status === "degraded" || s.status === "stale" ? "amber" : "red"),
        },
        {
          key: "last_success", label: "Last success",
          render: (s) => el("span", "muted", s.last_success ? fmt.dateTime(s.last_success) : "—"),
        },
        { key: "backs", label: "Backs", cls: "muted" },
        { key: "detail", label: "Detail", cls: "muted", render: sourceDetail },
      ],
      rows: srcs,
      empty: "No source reports health.",
    }));
  }

  // ── Anomalies (v1 copy, verbatim) ───────────────────────────────────────
  const an = d.anomalies || {};
  const anoms = an.recent || [];
  out.push(heading("Anomalies",
    `— operational incidents, last ${an.window_days || 14} days · unresolved`
    + " first · a crashlooping task collapses to one counted row", "h3"));
  if (!anoms.length) {
    out.push(el("div", "srcline ok", [
      icon("circle-check", { size: 15 }),
      el("div", "grow", "no anomalies filed in the window"),
    ]));
  } else {
    out.push(table({
      columns: [
        { key: "last", label: "When", render: (a) => el("span", "muted", fmt.dateTime(a.last)) },
        { key: "task_name", label: "Task", cls: "mono" },
        { key: "kind", label: "Kind", render: (a) => el("code", null, a.kind) },
        {
          key: "severity", label: "Severity",
          render: (a) => pill(a.severity, a.recovered ? "outline"
            : a.severity === "critical" ? "red" : "amber"),
        },
        { key: "n", label: "Count", num: true, render: (a) => fmt.num(a.n) },
        {
          key: "state", label: "State",
          // 'recovered' = the task succeeded after this fired (self-healed), or
          // every occurrence was explicitly resolved. Only genuinely-active ones
          // wear the loud colour.
          render: (a) => (a.recovered
            ? el("span", { title: "task has succeeded since, or every occurrence was resolved" },
              pill("recovered", "green"))
            : pill("active", a.severity === "critical" ? "red" : "amber")),
        },
      ],
      rows: anoms,
      empty: "None.",
    }));
  }

  // ── Doctor checks ───────────────────────────────────────────────────────
  out.push(heading("Doctor checks", null, "h3"));
  out.push(table({
    columns: [
      { key: "name", label: "Check" },
      {
        key: "status", label: "Status",
        render: (c) => pill(c.status, String(c.status || "").toLowerCase()),
      },
      { key: "detail", label: "Detail", cls: "muted" },
    ],
    rows: (d.doctor || {}).checks || [],
    empty: "The doctor reported no checks.",
  }));
  out.push(el("div", { class: "muted small" }, [
    "Model-ID validity vs /v1/models runs in ",
    el("code", null, "bin/doctor"),
    " (network).",
  ]));

  // ── Shadow ledger ───────────────────────────────────────────────────────
  const byTask = (d.shadow || {}).by_task || {};
  const shRows = Object.entries(byTask).map(([task, n]) => ({ task, n }));
  if (shRows.length) {
    out.push(heading("Shadow ledger (cutover evidence)",
      "— external writes each module intended, captured instead of sent", "h3"));
    out.push(table({
      columns: [
        { key: "task", label: "Task", cls: "mono" },
        { key: "n", label: "Intents", num: true, render: (x) => fmt.num(x.n) },
      ],
      rows: shRows.sort((a, b) => b.n - a.n),
      empty: "None.",
    }));
  }

  return out;
}

/** v1's per-status Source-health copy, kept word for word. */
function sourceDetail(s) {
  if (s.status === "ok") return `fresh · ${s.age_hours}h ago`;
  if (s.status === "down") return "never produced a successful run";
  if (s.status === "stale") return `${s.age_hours}h ago > ${s.max_age_hours}h max`;
  return "ran but output was thin/degraded";
}

function runNow(ctx) {
  const box = el("div", "card");
  const row = el("div", "hstack");
  // NOTE: these buttons pass through ctx.kick, which replaces the button's
  // textContent while it runs — so they carry no icon (an icon child would be
  // wiped on the first click and never come back).
  [["Scan", "/api/wake", { job: "scan" }],
    ["Sync", "/api/wake", { job: "sync" }],
    ["Inbox", "/api/wake", { job: "inbox" }],
    ["Outcome capture (rail)", "/api/rail", { rail: "outcome-capture" }],
  ].forEach(([label, path, body]) => {
    const b = btn(label, () => ctx.kick(path, body, b), { sm: true });
    row.appendChild(b);
  });
  // A DISABLED control has to say why it is disabled somewhere a keyboard can
  // reach. `title` needs a pointer hover, and a disabled <button> is not even
  // in the tab order in every browser — so the reason is on the page, and
  // aria-describedby ties the two together for a screen reader.
  const brief = btn("Brief", null, { sm: true, disabled: true });
  const briefWhy = el("span", "muted small",
    "Brief is not kickable: cos_brief_am runs on its own schedule and has no"
    + " allowlisted manual trigger. Re-run it from the run log above if the"
    + " server allows it.");
  brief.title = briefWhy.textContent;
  describe(brief, briefWhy);
  row.append(brief);

  box.append(
    el("div", "card-head", [
      el("h3", "card-title", "Run now"),
      el("span", "card-sub", "allowlisted launchd kicks · nothing here sends anything"),
    ]),
    row,
    el("div", { style: { marginTop: "var(--space-2)" } }, briefWhy),
  );
  return box;
}

function healthCards(d) {
  const g = el("div", "grid-4");
  const dr = d.doctor || {}, sp = d.spend || {}, sh = d.shadow || {}, an = d.anomalies || {};

  g.appendChild(kpi({
    label: "Doctor",
    value: pill(dr.worst || "?", doctorTone(dr.worst)),
    sub: `${(dr.checks || []).length} checks · full list below`,
    onClick: () => openDrill({
      title: "Doctor checks",
      rows: dr.checks || [],
      total: (dr.checks || []).length,
      columns: [
        { key: "name", label: "Check" },
        { key: "status", label: "Status", render: (c) => pill(c.status, String(c.status || "").toLowerCase()) },
        { key: "detail", label: "Detail" },
      ],
      search: (c) => `${c.name} ${c.status} ${c.detail}`,
      facets: [{ label: "status", allLabel: "All statuses", of: (c) => c.status }],
      emptyText: "The doctor reported no checks.",
    }),
  }));

  const over = sp.today_usd != null && sp.soft_limit_usd != null
    && sp.today_usd > sp.soft_limit_usd;
  g.appendChild(kpi({
    label: "Spend today",
    // usd2, not usd: the soft limit is $1.50 and whole-dollar rounding printed
    // it as "$2 soft", which is a ceiling the server does not have.
    value: el("span", null, [fmt.usd2(sp.today_usd),
      el("small", null, " / " + fmt.usd2(sp.soft_limit_usd) + " soft")]),
    warn: over ? `over the ${fmt.usd2(sp.soft_limit_usd)} soft limit`
      + ` · hard stop ${fmt.usd2(sp.hard_limit_usd)}` : null,
    sub: `MTD ${fmt.usd2(sp.mtd_usd)}`
      + ((sp.by_task || []).length ? " · click for the breakdown" : " · nothing spent today"),
    onClick: (sp.by_task || []).length ? () => openDrill({
      title: "Model spend today, by task",
      subtitle: "exact — grouped straight off claude_usage.ist_date, no inference",
      rows: sp.by_task,
      total: (sp.by_task || []).length,
      columns: spendCols,
      amount: (r) => r.usd,
      emptyText: "Nothing spent today.",
    }) : null,
  }));

  g.appendChild(kpi({
    label: "Shadow ledger",
    value: el("span", null, [fmt.num(sh.pending || 0), el("small", null, " pending intents")]),
    sub: sh.pending
      ? "captured writes, held while shadowed"
      : "no writes held — modules live or idle",
    onClick: Object.keys(sh.by_task || {}).length ? () => openDrill({
      title: "Shadow ledger — intents by task",
      subtitle: "every external write a module intended while its module flag is shadowed",
      rows: Object.entries(sh.by_task).map(([task, n]) => ({ task, n })),
      total: Object.keys(sh.by_task).length,
      columns: [
        { key: "task", label: "Task" },
        { key: "n", label: "Intents", num: true, render: (x) => fmt.num(x.n) },
      ],
      emptyText: "No intent has ever been captured.",
    }) : null,
  }));

  // ANOMALIES — the tile figure and the drill rows count DIFFERENT THINGS and
  // the screen used to hide that. `an.open` is unresolved OCCURRENCES (5);
  // `an.recent` is INCIDENTS, one row per (task, kind, severity), of which
  // there were 14. Tile said 5, drill showed 14, and nothing on either side
  // said why. Both numbers are real and both are useful, so both are now named,
  // the drill carries an Open column that sums to the tile, and the server's
  // 40-row cut is admitted via `truncated` (it counts nothing beyond it).
  const anoms = an.recent || [];
  const anomOpen = an.open || 0;
  const active = anoms.filter((a) => !a.recovered);
  const openInRows = anoms.reduce((s, a) => s + (a.open || 0), 0);
  g.appendChild(kpi({
    label: `Anomalies (${an.window_days || 14}d)`,
    value: el("span", null, [fmt.num(anomOpen), el("small", null, " open")]),
    sub: anomOpen
      ? `${fmt.num(anomOpen)} unresolved occurrence${anomOpen === 1 ? "" : "s"}`
        + ` across ${fmt.num(active.length)} incident${active.length === 1 ? "" : "s"}`
        + ` · ${fmt.num(anoms.length)} incidents in the window`
      : `nothing unresolved ✓ · ${fmt.num(anoms.length)} incident`
        + `${anoms.length === 1 ? "" : "s"} in the window, all recovered`,
    onClick: anoms.length ? () => openDrill({
      title: `Anomalies, last ${an.window_days || 14} days`,
      subtitle: `${fmt.num(anomOpen)} unresolved occurrences · `
        + `${fmt.num(anoms.length)} incidents (${fmt.num(active.length)} active,`
        + ` ${fmt.num(anoms.length - active.length)} recovered)`,
      note: "The tile counts unresolved OCCURRENCES; one row here is an"
        + " INCIDENT — a (task, kind, severity) group, however many times it"
        + " fired. The Open column is the tile's figure, split across these"
        + " rows: it sums to " + fmt.num(openInRows)
        + (openInRows === anomOpen ? "." : ", which is NOT the tile's "
          + fmt.num(anomOpen) + " — the server capped the group list.")
        + " Recovered = the task succeeded after this fired, or every"
        + " occurrence was explicitly resolved.",
      rows: anoms,
      total: anoms.length,
      // the server's query is LIMIT 40 and counts nothing past it, so at 40
      // rows there is no true total to state — say that rather than imply this
      // is the whole window.
      truncated: anoms.length >= 40,
      capNote: anoms.length >= 40
        ? "The server returns at most 40 incident groups, newest first, and does"
          + " not count what it drops — so this is the newest 40, not the whole"
          + " 14-day window."
        : null,
      columns: [
        { key: "last", label: "When", render: (a) => fmt.dateTime(a.last) },
        { key: "task_name", label: "Task" },
        { key: "kind", label: "Kind" },
        { key: "severity", label: "Severity" },
        { key: "n", label: "Fires", num: true, render: (a) => fmt.num(a.n) },
        {
          key: "open", label: "Open", num: true,
          render: (a) => (a.open
            ? el("b", "neg", fmt.num(a.open))
            : el("span", "muted", "0")),
        },
        { key: "state", label: "State", render: (a) => (a.recovered ? "recovered" : "active") },
      ],
      time: (a) => a.last,
      search: (a) => `${a.task_name} ${a.kind} ${a.severity}`,
      facets: [
        { label: "state", allLabel: "Active + recovered",
          of: (a) => (a.recovered ? "recovered" : "active") },
        { label: "task", allLabel: "All tasks", of: (a) => a.task_name },
      ],
      emptyText: "No anomaly in the window.",
    }) : null,
  }));

  // ── operational alerts from the platforms ─────────────────────────────────
  // Make scenario failures and the like. They are MAIL, kept in `events` rather
  // than copied into `anomalies` so one incident cannot end up with two
  // timelines that disagree. They are here and not on Review because the
  // mapping queue asks "which client is this?" and an automation error cannot
  // answer it — Abhijeet, 2026-08-20, when 134 landed there after the restore.
  const oa = d.ops_alerts || {}, oaRows = oa.recent || [];
  const oaTotal = oa.total || 0;
  g.appendChild(kpi({
    label: `Platform alerts (${oa.window_days || 30}d)`,
    value: oaRows.length
      ? el("span", null, [fmt.num(oaRows.length), el("small", null, " failing")])
      : el("span", "muted", "—"),
    sub: oaRows.length
      ? `${fmt.num(oaTotal)} alert${oaTotal === 1 ? "" : "s"} from`
        + ` ${fmt.num(oaRows.length)} automation${oaRows.length === 1 ? "" : "s"}`
      : "no platform alerts in the window",
    onClick: oaRows.length ? () => openDrill({
      title: `Platform alerts, last ${oa.window_days || 30} days`,
      subtitle: `${fmt.num(oaTotal)} messages, grouped into`
        + ` ${fmt.num(oaRows.length)} automations`,
      note: "One row is an AUTOMATION, not a message: the tile counts how many"
        + " are failing, and Alerts is how often each one wrote. These arrive as"
        + " email and are deliberately kept off Review — the mapping queue asks"
        + " which client a message belongs to, and an automation error cannot"
        + " answer that. Nothing here is deleted; it is filed, not dropped.",
      rows: oaRows,
      total: oaRows.length,
      truncated: oaRows.length >= 40,
      capNote: oaRows.length >= 40
        ? "The server returns at most 40 groups, newest first, and does not"
          + " count what it drops — this is the newest 40, not the whole window."
        : null,
      columns: [
        { key: "last", label: "Last seen", render: (a) => fmt.dateTime(a.last) },
        { key: "platform", label: "Platform" },
        { key: "scenario", label: "Automation" },
        { key: "n", label: "Alerts", num: true, render: (a) => fmt.num(a.n) },
        { key: "first", label: "First seen", render: (a) => fmt.dateTime(a.first) },
      ],
      time: (a) => a.last,
      search: (a) => `${a.platform} ${a.scenario}`,
      facets: [{ label: "platform", allLabel: "All platforms",
                 of: (a) => a.platform }],
      emptyText: "No platform alert in the window.",
    }) : null,
  }));

  return g;
}

/* ── drills ───────────────────────────────────────────────────────────────── */

const runCols = [
  { key: "started_at", label: "Time", render: (r) => el("span", "muted", hhmm(r.started_at)) },
  { key: "task", label: "Task" },
  { key: "status", label: "Status", render: (r) => statusPill(r.status) },
  {
    key: "duration_ms", label: "Duration", num: true,
    render: (r) => dash(r.duration_ms == null ? null : r.duration_ms / 1000, fmt.duration),
  },
  { key: "summary", label: "What it did" },
  {
    key: "usd", label: "≈ Cost", num: true,
    render: (r) => (r.usd == null ? null : "≈" + fmt.usd2(r.usd)),
  },
];

const spendCols = [
  { key: "task_name", label: "Task" },
  { key: "calls", label: "Calls", num: true, render: (r) => fmt.num(r.calls) },
  { key: "usd", label: "USD", num: true, render: (r) => fmt.usd2(r.usd) },
];

function drillRuns(act) {
  const r = act.runs || {};
  openDrill({
    title: "Runs on " + fmt.date(act.day),
    subtitle: "cost is ≈ — inferred from task + time window, never a ledger link",
    rows: r.rows || [],
    total: r.total,
    columns: runCols,
    time: (x) => x.started_at,
    search: (x) => `${x.task} ${x.status} ${x.summary || ""}`,
    facets: [
      { label: "task", allLabel: "All tasks", of: (x) => x.task },
      { label: "status", allLabel: "All statuses", of: (x) => x.status },
    ],
    emptyText: "No run started on this day.",
  });
}

/* ── THE FATHOM DRILL — "which 5, and how did each one get filed?" ────────────
   Abhijeet's ask, verbatim: "Activity shows that 5 fathom transcripts. Once I
   click on it I need to see WHICH 5 transcripts were processed and how they
   were added to the registry and everything."

   This is the only drill in the cockpit that FETCHES. /api/activity returns the
   day's ingest as COUNTS; the per-call story (participants, duration,
   attribution, facts, dossier, cost, registry commit) needs its own read. The
   trigger holds the wait (ui.withBusy) rather than opening an empty panel that
   would flash "No rows behind this figure" — the one sentence this cockpit must
   never say when it simply has not looked yet.

   EVERY CLAIM ON A ROW IS LABELLED BY HOW WELL IT IS KNOWN, because the server
   knows some of this exactly and reconstructs the rest:
     · facts     facts.source_id, a foreign key. Exact.
     · attribution  "recorded" only for a mapping-queue approval (the one
                    timestamped decision in the schema). Everything else is
                    REPLAYED against today's registry and says so; when the
                    replay fails, the row says the registry no longer explains
                    the stored answer instead of inventing an identifier.
     · cost      claude_usage.purpose spells the source_ref in TEXT. A
                 convention, not a key — stated on the row.
     · dossier   a PRESENCE claim, never a timing one: last_compiled_at is
                 stamped at the compiling run's START, so it is routinely older
                 than facts written by that same run. */

const ATTR_TONE = { "mapping-queue approval": "ok" };

function attrCell(r) {
  const a = r.attribution || {};
  if (a.state !== "attributed") {
    return el("div", "stack-1", [
      el("span", "muted", "not attributed"),
      el("div", "small muted", a.detail || "no reason recorded"),
    ]);
  }
  return el("div", "stack-1", [
    el("div", "hstack-tight", [
      el("b", null, r.client_name || r.client_id || "—"),
      r.client_kind ? pill(r.client_kind, "outline") : null,
    ]),
    el("div", "small", [
      a.how ? pill(a.how, ATTR_TONE[a.how] || "outline") : null,
      a.matched_on ? el("span", "mono small", " " + a.matched_on) : null,
    ]),
    el("div", "small muted", [
      a.proven
        ? "recorded decision — "
        : "replayed against today's registry, not a recorded decision — ",
      a.detail || "",
    ]),
  ]);
}

function factsCell(r) {
  const f = r.facts || {};
  if (!f.n) {
    const np = r.not_processed;
    return el("div", "stack-1", [
      el("span", "dash", "0 facts"),
      el("div", "small muted", np
        ? np.reason + (np.run_context ? " — " + np.run_context : "")
        : "extracted, but this source produced no fact"),
    ]);
  }
  const cats = Object.entries(f.categories || {})
    .sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}`).join(" · ");
  // where each fact actually LANDED, which is not the same question as how
  // many there were: a call can produce 18 facts and file none of them.
  const rt = f.routes || {};
  const where = [
    rt.dossier ? `${fmt.num(rt.dossier)} → a dossier` : null,
    rt.proof_points ? `${fmt.num(rt.proof_points)} → proof-points` : null,
    rt.nowhere ? `${fmt.num(rt.nowhere)} → nowhere (no client)` : null,
  ].filter(Boolean).join(" · ");
  return el("div", "stack-1", [
    el("b", null, fmt.num(f.n) + (f.n === 1 ? " fact" : " facts")),
    cats ? el("div", "small muted", cats) : null,
    where ? el("div", "small" + (rt.nowhere ? " neg" : ""), where) : null,
    el("div", "small muted", "linked by " + (f.link || "facts.source_id")),
  ]);
}

function dossierCell(r) {
  const d = r.dossier || {};
  const label = {
    compiled: "in the dossier",
    "no-dossier": "no dossier exists",
    "no-client": "no client to file under",
    "proof-points": "proof-points, not a dossier",
    "no-facts": "—",
  }[d.state] || d.state || "—";
  return el("div", "stack-1", [
    d.state === "no-facts" ? el("span", "dash", "—")
      : el("span", d.state === "compiled" ? "pos" : "muted", label),
    d.client_id ? el("div", "small mono", d.client_id) : null,
    d.note ? el("div", "small muted", d.note) : null,
  ]);
}

/**
 * A commit sha and the button that copies it — which confirms ON ITSELF.
 *
 * copyText() only shows the on-button tick when it is GIVEN the button;
 * without it the only feedback is a toast in the far corner, which is not
 * where you are looking after clicking a 12px icon next to a sha.
 */
function shaWithCopy(sha) {
  const b = iconBtn("copy", "Copy commit " + sha, () => copyText(sha, b),
    { ghost: true, size: 12 });
  return el("span", "hstack-tight", [el("code", null, sha), b]);
}

function registryCell(r) {
  const rc = r.registry_change || {};
  if (rc.state === "committed") {
    return el("div", "stack-1", (rc.commits || []).map((c) => el("div", "stack-1", [
      shaWithCopy(c.sha),
      el("div", "small muted", c.subject || ""),
    ])));
  }
  return el("div", "stack-1", [
    el("span", "dash", "no change"),
    rc.note ? el("div", "small muted", rc.note) : null,
  ]);
}

function callCell(r) {
  const secs = r.duration_seconds;
  const bits = [];
  if (r.occurred_dt) bits.push(fmt.dateTime(r.occurred_dt));
  bits.push(secs == null ? "duration —" : fmt.duration(secs));
  return el("div", "stack-1", [
    el("div", "hstack-tight", [
      el("b", null, r.title || "(untitled)"),
      r.status !== "extracted" ? pill(r.status, "warn") : pill("extracted", "ok"),
    ]),
    el("div", "small muted", bits.join(" · ")),
    el("div", "small", [
      el("span", "mono", r.source_ref),
      r.url ? " " : null,
      r.url ? extLink(r.url, "open", { class: "small" }) : null,
    ]),
  ]);
}

function partsCell(r) {
  const p = r.participants || [];
  if (!p.length) {
    return el("div", "stack-1", [
      el("span", "dash", "—"),
      el("div", "small muted", r.payload_note || "not recorded"),
    ]);
  }
  return el("div", { class: "small", attrs: { title: p.join(", ") } },
    p.join(", "));
}

/**
 * The per-source story for one IST day, openable from OUTSIDE Activity.
 *
 * Knowledge's "Fathom ingest" tile used to end in a caveat naming this screen
 * ("the per-run detail lives under Activity"). Both tiles are real and they
 * are different — Knowledge reports the poll's STATE, this reports the CALLS —
 * so the fix is a door between them, not a second implementation of this
 * table. `day` rather than the whole `act` object is all this ever needed.
 */
export function openSourcesDrill(ctx, day, type, ev) {
  return drillSources(ctx, { day }, type, ev);
}

async function drillSources(ctx, act, type, ev) {
  const trigger = ev && ev.currentTarget instanceof HTMLElement ? ev.currentTarget : null;
  await withBusy(trigger, async () => {
    let res;
    try {
      res = await ctx.api("/api/activity/sources?day=" + encodeURIComponent(act.day)
        + (type ? "&source_type=" + encodeURIComponent(type) : ""));
    } catch (e) {
      toast("Could not read the sources for this day: "
        + String((e && e.message) || e), true);
      return;
    }
    if (res && res.error) { toast(String(res.error), true); return; }

    const rows = res.rows || [];
    const label = type ? type[0].toUpperCase() + type.slice(1) : "Source";
    const stops = (res.extract_runs || []).filter((x) => x.stopped);
    const facts = rows.reduce((s, r) => s + ((r.facts || {}).n || 0), 0);
    const usd = rows.reduce((s, r) => s + ((r.cost || {}).usd || 0), 0);
    const attributed = rows.filter((r) => (r.attribution || {}).state === "attributed").length;

    openDrill({
      wide: true,               // seven columns of story per row, not a list
      title: `${label}${type ? " calls" : "s"} ingested on ${fmt.date(res.day)}`,
      subtitle: `${fmt.num(res.total)} ingested · ${fmt.num(attributed)} attributed to a`
        + ` client · ${fmt.num(facts)} facts extracted from them · `
        + `${fmt.usd2(usd)} of model spend names them`,
      note: el("div", "stack-1", [
        el("div", null, res.note),
        stops.length
          ? el("div", "neg", stops.map((s) =>
            `${s.task} run #${s.run_id} stopped extracting on “${s.stopped}” with `
            + `${fmt.num(s.pending)} sources still pending.`).join(" "))
          : null,
        res.registry_search_note ? el("div", "neg", res.registry_search_note) : null,
        res.registry_note ? el("div", "neg", res.registry_note) : null,
        !type && Object.keys(res.day_by_source_type || {}).length
          ? el("div", "muted", "types this day: " + Object.entries(res.day_by_source_type)
            .sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}`).join(" · "))
          : null,
      ]),
      rows,
      total: res.total,
      capNote: res.capped
        ? `The server returns at most ${fmt.num(res.count)} of this day's`
          + ` ${fmt.num(res.total)} sources, newest call first. The header count`
          + ` is the true total.`
        : null,
      columns: [
        { key: "title", label: "Call", render: callCell },
        { key: "participants", label: "Who was on it", render: partsCell },
        { key: "client_id", label: "Filed under · how", render: attrCell },
        { key: "facts", label: "What came out", render: factsCell },
        { key: "dossier", label: "Dossier", render: dossierCell },
        {
          key: "cost", label: "Cost", num: true,
          render: (r) => (r.cost
            ? el("span", { title: r.cost.link }, [
              fmt.usd2(r.cost.usd),
              el("div", "small muted", `${fmt.num(r.cost.calls)} call`
                + (r.cost.calls === 1 ? "" : "s")),
            ])
            : el("span", { class: "dash", title: r.cost_note || "" }, "—")),
        },
        { key: "registry_change", label: "Registry", render: registryCell },
      ],
      time: (r) => r.occurred_dt || r.ingested_at,
      search: (r) => `${r.title || ""} ${r.source_ref} ${(r.participants || []).join(" ")}`
        + ` ${r.client_name || ""} ${r.client_id || ""} ${r.status}`,
      facets: [
        { label: "source type", allLabel: "All source types", of: (r) => r.source_type },
        {
          label: "attribution", allLabel: "Attributed + not",
          of: (r) => ((r.attribution || {}).state === "attributed"
            ? (r.client_name || r.client_id) : "not attributed"),
        },
        { label: "status", allLabel: "All statuses", of: (r) => r.status },
      ],
      emptyText: type
        ? `No ${type} source was ingested on this day.`
        : "Nothing was ingested on this day.",
    });
  });
}

async function drillFactRows(ctx, act, client, ev) {
  const trigger = ev && ev.currentTarget instanceof HTMLElement ? ev.currentTarget : null;
  await withBusy(trigger, async () => {
    let res;
    try {
      res = await ctx.api("/api/activity/facts?day=" + encodeURIComponent(act.day)
        + (client ? "&client=" + encodeURIComponent(client) : ""));
    } catch (e) {
      toast("Could not read the facts for this day: "
        + String((e && e.message) || e), true);
      return;
    }
    if (res && res.error) { toast(String(res.error), true); return; }
    openDrill({
      wide: true,               // a fact plus its provenance is a wide row
      title: "Facts extracted on " + fmt.date(res.day)
        + (client ? " · " + client : ""),
      subtitle: `${fmt.num(res.total)} facts, each joined to the source it came`
        + " from through facts.source_id",
      note: res.note,
      rows: res.rows || [],
      total: res.total,
      capNote: res.capped
        ? `The server returns the newest ${fmt.num(res.count)} of`
          + ` ${fmt.num(res.total)} facts by fact id. The header count is the`
          + ` true total.`
        : null,
      columns: [
        {
          key: "client_id", label: "Client",
          render: (r) => (r.client_id
            ? el("span", "mono small", r.client_id)
            : el("span", { class: "dash", title: "scope " + (r.scope || "?") },
              "unattributed")),
        },
        { key: "category", label: "Category" },
        { key: "confidence", label: "Confidence", render: (r) => pill(r.confidence, "outline") },
        {
          key: "fact_text", label: "Fact",
          render: (r) => el("span", { class: "small", attrs: { title: r.fact_text || "" } },
            String(r.fact_text || "").slice(0, 220)),
        },
        {
          key: "source_ref", label: "From",
          render: (r) => (r.source_ref
            ? el("div", "stack-1", [
              el("span", "mono small", r.source_ref),
              r.source_title ? el("div", "small muted", r.source_title) : null,
            ])
            : el("span", { class: "dash", title: "facts.source_id points at a row that is gone" },
              "source row missing")),
        },
        { key: "citation", label: "Citation", cls: "mono small" },
      ],
      time: (r) => r.created_at,
      search: (r) => `${r.fact_text || ""} ${r.client_id || ""} ${r.source_ref || ""}`,
      facets: [
        { label: "client", allLabel: "All clients",
          of: (r) => r.client_id || "(unattributed)" },
        { label: "category", allLabel: "All categories", of: (r) => r.category },
        { label: "scope", allLabel: "All scopes", of: (r) => r.scope },
      ],
      emptyText: "No fact was extracted on this day.",
    });
  });
}

function drillFacts(ctx, act) {
  const f = act.facts || {};
  const rows = f.by_client || [];
  openDrill({
    title: "Facts extracted on " + fmt.date(act.day),
    subtitle: `${fmt.num(f.total)} facts across ${fmt.num(f.clients)} clients`,
    rows,
    // the TRUE total is the client count; the server cut this list by RANK,
    // not by time, so capNote replaces the drill's default "newest first".
    total: f.clients == null ? rows.length : f.clients,
    capNote: `The server returns the TOP ${fmt.num(f.top_n)} clients by fact`
      + ` count, not the newest — ${fmt.num(rows.length)} of`
      + ` ${fmt.num(f.clients)} clients are listed, and they account for`
      + ` ${fmt.num(rows.reduce((s, r) => s + r.n, 0))} of ${fmt.num(f.total)}`
      + ` facts. The clients missing from this list are the QUIETEST ones, not`
      + ` the oldest.`,
    columns: [
      { key: "client", label: "Client" },
      {
        key: "n", label: "Facts", num: true,
        // and each of THOSE drills to the facts themselves
        render: (r) => linkNum(fmt.num(r.n),
          (e) => drillFactRows(ctx, act, r.client, e),
          { label: `show ${r.client}'s ${fmt.num(r.n)} facts from this day` }),
      },
    ],
    emptyText: "No fact was extracted on this day.",
  });
}

function drillRegistry(act) {
  const reg = act.registry || {};
  const parsed = (reg.commits || []).filter((c) => c.change).length;
  openDrill({
    title: "Registry changes on " + fmt.date(act.day),
    subtitle: `audited git commits on ${reg.file} · +${fmt.num(reg.insertions)} `
      + `−${fmt.num(reg.deletions)} lines`,
    note: "Every approval in Review writes one of these commits. This is the"
      + " only record that a client was added or an alias learned — nothing in"
      + " the database holds the sha. `What changed` is read out of the commit"
      + " SUBJECT, which approve.py writes as"
      + " “registry: <client> <field> += <value>”; "
      + (parsed === (reg.commits || []).length
        ? "every commit here matched that shape."
        : `${fmt.num((reg.commits || []).length - parsed)} of these were written`
          + " by hand and do not match it, so they say “hand-written” rather"
          + " than being forced into a shape they never had."),
    rows: reg.commits || [],
    total: reg.count,
    columns: [
      { key: "at", label: "At", render: (c) => fmt.dateTime(c.at) },
      { key: "sha", label: "Commit", render: (c) => shaWithCopy(c.sha) },
      {
        key: "change", label: "What changed",
        render: (c) => (c.change
          ? el("span", "small", [
            el("span", "mono", c.change.client_id), " ",
            pill(c.change.field, "outline"), " ",
            el("b", null, c.change.value),
          ])
          : el("span", "muted small", "hand-written commit")),
      },
      { key: "subject", label: "Subject" },
      { key: "insertions", label: "+", num: true, render: (c) => fmt.num(c.insertions) },
      { key: "deletions", label: "−", num: true, render: (c) => fmt.num(c.deletions) },
    ],
    time: (c) => c.at,
    emptyText: "config/clients.yaml did not change on this day.",
  });
}

function drillSpendByTask(act) {
  const sp = act.spend || {};
  openDrill({
    title: "Model spend on " + fmt.date(act.day),
    subtitle: `${fmt.num(sp.calls)} calls · exact, grouped off claude_usage.ist_date`,
    note: sp.unattributed_usd
      ? `${fmt.usd2(sp.unattributed_usd)} of this total could not be tied to any`
        + " run (the per-RUN split is a time-window inference; the per-TASK split"
        + " below is exact)."
      : null,
    rows: sp.by_task || [],
    total: (sp.by_task || []).length,
    columns: spendCols,
    amount: (r) => r.usd,
    emptyText: "No model call was billed on this day.",
  });
}

function drillNudges(act) {
  const nd = act.nudges || {};
  const rows = Object.entries(nd.created_by_kind || {})
    .map(([kind, n]) => ({ kind, n }))
    .sort((a, b) => b.n - a.n);
  openDrill({
    title: "Cards opened on " + fmt.date(act.day),
    subtitle: `${fmt.num(nd.created)} opened · ${fmt.num(nd.closed)} closed`
      + ` (${fmt.num(nd.done)} done, ${fmt.num(nd.dismissed)} dismissed)`,
    note: "Grouped by kind — /api/activity returns the day's cards as counts, so"
      + " the individual cards live on Today, not here.",
    rows,
    total: rows.length,
    columns: [
      { key: "kind", label: "Kind", render: (r) => String(r.kind).replace(/_/g, " ") },
      { key: "n", label: "Cards", num: true, render: (r) => fmt.num(r.n) },
    ],
    emptyText: "No card was opened on this day.",
  });
}

function drillShadowDay(act) {
  const sh = act.shadow || {};
  openDrill({
    title: "Write intents held on " + fmt.date(act.day),
    subtitle: "captured by shadow mode instead of being sent",
    rows: sh.rows || [],
    total: (sh.rows || []).length,
    columns: [
      { key: "target", label: "Target" },
      { key: "operation", label: "Operation" },
      { key: "n", label: "Intents", num: true, render: (r) => fmt.num(r.n) },
    ],
    emptyText: "No write intent was captured on this day.",
  });
}

function drillUsage(d) {
  const u = d.usage || {};
  const run = d.run || {};
  const inferred = u.attribution === "window";
  openDrill({
    title: `Model calls · ${run.task_name} run #${run.id}`
      + (inferred ? " (inferred match)" : ""),
    subtitle: inferred
      ? "matched to this run by task + time window — claude_usage.run_id has never"
        + " been written, so this is an inference, not a ledger link"
      : "linked to this run by run_id",
    rows: u.rows || [],
    // the trigger says "all N calls", so the header must state N — not the page
    // length. They are the same today (the reader returns every matched row),
    // but a figure that quietly re-derives itself from the page is how a cap
    // becomes invisible the day one is added.
    total: u.calls == null ? (u.rows || []).length : u.calls,
    columns: [
      { key: "called_at", label: "At", render: (r) => fmt.dateTime(r.called_at) },
      { key: "purpose", label: "Purpose" },
      { key: "model", label: "Model" },
      { key: "billing_route", label: "Route" },
      { key: "input_tokens", label: "In", num: true, render: (r) => fmt.num(r.input_tokens) },
      { key: "output_tokens", label: "Out", num: true, render: (r) => fmt.num(r.output_tokens) },
      {
        key: "duration_ms", label: "Took", num: true,
        render: (r) => dash(r.duration_ms == null ? null : r.duration_ms / 1000, fmt.duration),
      },
      { key: "total_cost_usd", label: "USD", num: true, render: (r) => fmt.usd2(r.total_cost_usd) },
    ],
    amount: (r) => r.total_cost_usd,
    time: (r) => r.called_at,
    search: (r) => `${r.purpose || ""} ${r.model || ""}`,
    emptyText: "No model call could be matched to this run.",
  });
}
