// ════════════════════════════════════════════════════════════════════════════
// sections/lifecycle.js — the Pipeline tab: Upwork as the sales engine, end to
// end (2026-10-09). Abhijeet, before bed: "now Upwork is our sales engine ...
// the scanner ... the people we apply to ... opened ... replied ... hired ...
// how much they are paying ... why only five people hired us and what are the
// reasons that 25 people did not move ahead ... reactivation ... a really good
// UI in the cockpit ... different agents doing different things but at the end
// there is this cohesive knowledge base". His answer the same night: a NEW
// top-level tab that the Funnel and the scanner charts fold into — nothing
// parallel (both left the Upwork tab in the same commit).
//
// Two reads side by side: /api/upwork/funnel (one row per proposal + the
// money; every proposal figure is computed by ../propfunnel.js exactly as on
// Proposals) and /api/lifecycle (the scanner, the conversations counted, the
// reader's notes, the tests, where each client is now, the agents, the
// knowledge base).
//
// The views follow the pipeline, left to right:
//   Flow            job → proposal → opened → wrote back → hired → paying:
//                   where each step loses people, what we know about why, and
//                   what is stopping the machine right now
//   Scanner         the scanner's own charts (./pipeline.js renderAnalysis)
//   Proposals       sent → opened → wrote back → hired, broken down
//                   (./upwork_funnel.js renderFunnel)
//   Conversations   every proposal the client answered: who wrote last, a
//                   call, an offer, what Upwork says the client did, and the
//                   conversation reader's note
//   After the hire  who is paying now, who stopped, who came back, and the
//                   win-back list (drafts only; he sends)
//   Tests           the running test, version against version, and the log
//   Agents          the seven agents, what blocks each, the knowledge base
//
// COPY RULES (the Upwork tab's): plain words, no statistics vocabulary; "—"
// means not measured, never zero; every figure opens the rows behind it.
// CONTRACT (enforced by app.js): meta + async render(root, ctx). Own nothing
// global. No send route: a win-back draft is copied, never sent from here.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import { el, append, clear, fmt, icon, pill, notice, emptyState, btn, extLink, table, copyText } from "../ui.js";
import { openDrill } from "../drill.js";
import { subtabs } from "../subtabs.js";
import { renderFunnel, drillRows as proposalDrill } from "./upwork_funnel.js";
import { renderAnalysis } from "./pipeline.js";

export const meta = { title: "Pipeline", hint: "Upwork as the sales engine, end to end" };

const STATE_KEY = "ew_cockpit_lifecycle_state";
const F = () => window.EW_PROPFUNNEL;
const PROPOSAL_URL = "https://www.upwork.com/nx/proposals/";
const ROOM_URL = "https://www.upwork.com/nx/messages/rooms/";
const PEEK = 10;

const TABS = [
  { id: "flow", label: "Flow" },
  { id: "scanner", label: "Scanner" },
  { id: "proposals", label: "Proposals" },
  { id: "talks", label: "Conversations" },
  { id: "after", label: "After the hire" },
  { id: "tests", label: "Tests" },
  { id: "agents", label: "Agents" },
];

// the scheduled jobs, in words (the screen never prints a task name)
const TASK_WORDS = {
  upwork_scan: "the job scan (every 30 minutes)",
  upwork_sync: "the Upwork sync (hourly)",
  upwork_pulse: "the 15-minute heartbeat",
  upwork_inbox: "the inbox read",
  upwork_financials: "the Upwork ledger read (daily)",
  finance_wise_mail: "Wise payment emails",
  finance_stripe: "Stripe payments",
  finance_payment_mail: "payment receipts by email",
  upwork_conversation_codes: "the conversation reading",
  upwork_reactivation: "the weekly win-back drafts",
  upwork_progress: "the progress email",
  upwork_outcomes: "the Sunday outcome email",
};

const DID = { hired_us: "Hired you", hired_other: "Hired someone else",
              interviewed: "Talked to others, hired no one", nobody: "Spoke to nobody" };
const LOST = { price: "price", chose_someone_else: "chose someone else", timing: "timing",
               scope_or_fit: "scope or fit", we_did_not_answer: "we did not answer",
               they_went_quiet: "they went quiet", project_cancelled: "project cancelled",
               trust: "trust", skills: "skills", communication: "communication", other: "other",
               not_lost: "not lost" };
const STATE_WORDS = { working: "Working", blocked: "Blocked", failing: "Failing", off: "Not switched on yet" };
const STATE_TONE = { working: "ok", blocked: "fail", failing: "warn", off: "outline" };

/* ── small helpers ────────────────────────────────────────────────────────── */

const pct = (x) => (x == null ? "—" : fmt.pct(100 * x, 0));
const rate = (n, d) => (d ? n / d : null);
const plural = (n, one, many) => `${fmt.num(n)} ${n === 1 ? one : (many || one + "s")}`;
const day = (iso) => String(iso || "").slice(0, 10);

function loadState() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem(STATE_KEY) || "{}") || {}; } catch (e) { s = {}; }
  return { period: s.period || "3m", talks: s.talks || "lost", after: s.after || "paying" };
}
function saveState(s) {
  try { localStorage.setItem(STATE_KEY, JSON.stringify(s)); } catch (e) { /* private mode */ }
}

function inB(d, b) {
  const x = day(d);
  return !!x && (!b.lo || x >= b.lo) && (!b.hi || x <= b.hi);
}

function segmented(options, value, onPick, label) {
  return el("div", { class: "segmented", attrs: { role: "group", "aria-label": label } },
    options.map(([v, text]) => el("button", {
      class: "segbtn" + (value === v ? " on" : ""), type: "button",
      attrs: { "aria-pressed": String(value === v) },
      on: { click: () => onPick(v) },
    }, text)));
}

function section(title, sub, children, opts = {}) {
  return el("section", "card lc-sec" + (opts.cls ? " " + opts.cls : ""), [
    el("div", "lc-sec-head", [
      el("h3", "lc-h", title),
      sub ? el("span", "muted small", sub) : null,
      opts.right ? el("span", "grow-right", opts.right) : null,
    ]),
    children,
  ]);
}

function linkBtn(text, onClick, title) {
  return el("button", { class: "linktext", type: "button", attrs: title ? { title } : {},
    on: { click: onClick } }, text);
}

function goTo(ctx, view) {
  ctx.navigate("pipeline", { view });
}

/** a ctx whose api() hands back the same promise for the same path, so the
    Proposals view reuses the Flow's /api/upwork/funnel read */
function memoCtx(ctx, cache) {
  return Object.assign({}, ctx, { api: (p) => cache[p] || (cache[p] = ctx.api(p)) });
}

/* ── the blockers banner (Flow + Agents) ──────────────────────────────────── */

function blockersBanner(L, ctx, opts = {}) {
  const bl = ((L.agents || {}).blockers) || [];
  if (!bl.length) {
    return opts.quietWhenClear ? null
      : el("div", "callout ok", [icon("circle-check", { size: 16, class: "ico" }),
        el("div", null, "Nothing is blocking the agents right now.")]);
  }
  const who = (b) => ((L.agents || {}).agents || []).filter((a) => (a.blockers || []).includes(b.kind))
    .map((a) => a.name);
  return el("div", "lc-block", [
    el("div", "lc-block-head", [icon("alert-triangle", { size: 16 }),
      el("b", null, bl.length === 1 ? "One thing is stopping part of the pipeline"
        : `${bl.length} things are stopping parts of the pipeline`),
      opts.link ? el("span", "grow-right", linkBtn("the agents →", () => goTo(ctx, "agents"))) : null]),
    el("ul", "lc-block-list", bl.map((b) => {
      const names = who(b);
      const isCmd = /python3/.test(b.fix || "");
      const cmd = isCmd ? b.fix.replace(/^.*?:\s*/, "") : null;
      return el("li", null, [
        el("div", null, [el("b", null, b.words), b.since ? el("span", "muted", ` Since ${fmtSince(b.since)}.`) : null]),
        names.length ? el("div", "muted small", `Stops: ${names.join(", ")}.`) : null,
        el("div", "lc-fix", [
          el("span", "muted small", "Fix: "),
          isCmd ? el("code", "lc-cmd", cmd) : el("span", "small", b.fix),
          isCmd ? btn("Copy", (e) => copyText(cmd, e.currentTarget), { sm: true, variant: "ghost", icon: "copy" }) : null,
        ]),
      ]);
    })),
  ]);
}

function fmtSince(s) {
  // "2026-10-08 16:15:05 IST" or an ISO stamp → "8 Oct, 16:15"
  const m = String(s).match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return m ? `${fmt.dateShort(m[1])}, ${m[2]}` : String(s);
}

/* ══ Flow ═════════════════════════════════════════════════════════════════ */

function flowData(D, L, b) {
  const facts = D.facts || [];
  const sc = L.scanner || {};
  const seen = sc.seen || {};
  const days = (sc.days || []).filter((x) => inB(x[0], b));
  const hot = (sc.hot || []).filter((h) => inB(h[1], b))
    .map((h) => ({ id: h[0], d: h[1], s: h[2], title: h[3], pid: h[4] }));
  const cold = F().inPeriod(facts.filter((r) => r.k === "cold"), b);
  const inv = F().inPeriod(facts.filter((r) => r.k === "invite"), b);
  const flagged = (r) => { const s = seen[r.id]; return !!(s && s[0] != null && s[0] >= sc.hot_at); };
  return {
    b, days, hot, cold, inv, st: F().stages(cold),
    read: days.reduce((s, x) => s + x[1], 0),
    onHot: cold.filter(flagged), notFlagged: cold.filter((r) => !flagged(r)),
    unseen: cold.filter((r) => !seen[r.id]),
    hotApplied: hot.filter((h) => h.pid), invHired: inv.filter((r) => r.hired === 1),
    scanSince: sc.since,
  };
}

const HOT_COLS = (L) => [
  { key: "d", label: "Found", render: (h) => el("span", "muted nowrap", fmt.dateShort(h.d)) },
  { key: "title", label: "Job", render: (h) => extLink((L.scanner || {}).job_url + h.id, h.title || h.id,
    { title: "the job on Upwork" }) },
  { key: "s", label: "Score", num: true, render: (h) => el("span", "strong", String(h.s)) },
  { key: "pid", label: "Your proposal", render: (h) => (h.pid
    ? extLink(PROPOSAL_URL + h.pid, "sent", { title: "your proposal on Upwork" })
    : el("span", "muted", "none")) },
];

function hotDrill(L, title, rows, subtitle) {
  openDrill({
    title, subtitle, rows, total: rows.length, columns: HOT_COLS(L), wide: true,
    time: (h) => h.d, search: (h) => `${h.title || ""} ${h.id}`,
    facets: [{ label: "Applied", allLabel: "All", of: (h) => (h.pid ? "you applied" : "no proposal") }],
    defaultSort: "newest", emptyText: "No hot jobs behind this figure.",
  });
}

/** the strip: Found → Applied → Opened → Wrote back → Hired */
function stripBlock(D, L, fd, ctx) {
  const st = fd.st;
  const top = Math.max(1, fd.hot.length);
  const scanLate = fd.scanSince && (!fd.b.lo || fd.scanSince > fd.b.lo);
  const tiles = [
    { l: "Hot jobs found", v: fd.hot.length,
      s: [el("b", null, fmt.num(fd.read)), " jobs read"
        + (scanLate ? `, since ${fmt.dateShort(fd.scanSince)} (when the scanner started)` : "")],
      go: () => hotDrill(L, "Hot jobs the scanner found", fd.hot,
        `score ${(L.scanner || {}).hot_at} or more, in this period; the ones with a proposal are marked`) },
    { l: "Proposals sent", v: st[0].n,
      s: [el("b", null, pct(rate(fd.hotApplied.length, fd.hot.length))), " of the hot jobs got one",
        fd.inv.length ? ` · +${fmt.num(fd.inv.length)} invitations accepted` : ""],
      go: () => proposalDrill("Proposals sent", st[0].rows, { subtitle: "cold proposals in this period" }) },
    { l: "Opened", v: st[1].n, s: [el("b", null, pct(st[1].ofPrev)), " of those sent"],
      go: () => proposalDrill("Opened by the client", st[1].rows,
        { subtitle: "Upwork records the open, or the client wrote back" }) },
    { l: "Wrote back", v: st[2].n, s: [el("b", null, pct(st[2].ofPrev)), " of those opened"],
      go: () => talksDrill(D, L, "The client wrote back", st[2].rows) },
    { l: "Hired you", v: st[3].n,
      s: [el("b", null, pct(st[3].ofPrev)), " of those who wrote back",
        fd.invHired.length ? ` · +${fmt.num(fd.invHired.length)} from invitations` : ""],
      go: () => proposalDrill("Became a client", st[3].rows.concat(fd.invHired),
        { subtitle: "cold proposals and accepted invitations that became clients" }) },
  ];
  const flow = el("div", { class: "flow lc-flow", style: { gridTemplateColumns: `repeat(${tiles.length}, 1fr)` } });
  tiles.forEach((t, i) => {
    flow.appendChild(el("button", {
      class: "stage" + (t.v ? "" : " dim"), type: "button",
      attrs: { title: `${t.l}: ${fmt.num(t.v)} — click for the rows` }, on: { click: t.go },
    }, [
      el("div", "l", t.l), el("div", "v", fmt.num(t.v)), el("div", "s", t.s),
      el("div", "bar", el("i", { style: { width: Math.max(1.5, (100 * t.v) / top) + "%" } })),
      i < tiles.length - 1 ? el("span", "arrow", "›") : null,
    ]));
  });
  return flow;
}

/** best and worst group of a split by the rate at one step (step 1 = opened
    of sent, 2 = wrote back of opened), groups of at least FEW only */
function bestWorst(rows, dimKey, step) {
  const groups = F().split(rows, dimKey).filter((g) => !g.unknown)
    .map((g) => ({ key: g.key, n: g.stages[step - 1].n, k: g.stages[step].n,
                   r: rate(g.stages[step].n, g.stages[step - 1].n) }))
    .filter((g) => g.n >= F().FEW);
  if (groups.length < 2) return null;
  groups.sort((a, c) => c.r - a.r);
  return { best: groups[0], worst: groups[groups.length - 1] };
}

// how a group of a split reads inside a sentence ("Opened 33% when ...")
const WHEN = {
  speed: { "Within 6 hours": "you applied within 6 hours of the post", "6-24 hours": "you applied 6 to 24 hours after it",
           "After the first day": "you applied after the first day" },
  pile: { "Up to 20": "the job had up to 20 proposals", "21-50": "it had 21 to 50", "51-100": "it had 51 to 100",
          "Over 100": "it had over 100" },
  opening: { "About the client": "the first lines were about the client", "About you": "they were about you",
             "A fit claim (“exactly what I do”)": "they claimed a fit", "Leads with a Loom": "they led with a Loom",
             "Your plan": "they set out your plan", "Mixed": "they were mixed", "Other": "they were something else" },
};

function bwLine(x, verb, dimKey) {
  if (!x || x.best.r === x.worst.r) return null;
  const say = (k) => ((WHEN[dimKey] || {})[k] || k.toLowerCase());
  return el("li", null, [`${verb} `, el("b", null, pct(x.best.r)), ` when ${say(x.best.key)} (${fmt.num(x.best.n)}),`
    + " ", el("b", null, pct(x.worst.r)), ` when ${say(x.worst.key)} (${fmt.num(x.worst.n)}).`]);
}

function compareWords(k, n, K, N) {
  const s = F().signal(k, n, K, N);
  if (s === "clear") return "a clear difference";
  if (s === "lean") return "a lean, not yet clear";
  return n < F().FEW || N - n < F().FEW ? "too few to tell yet" : "no real difference yet";
}

function dropCard(title, big, lines, foot) {
  return el("div", "lc-drop", [
    el("div", "lc-drop-t", title),
    el("div", "lc-drop-big", big),
    el("ul", "lc-drop-lines", lines.filter(Boolean)),
    foot ? el("div", "lc-drop-foot", foot) : null,
  ]);
}

function dropsBlock(D, L, fd, ctx) {
  const st = fd.st;
  const cards = [];

  // 1. hot job → proposal: did we apply where the scanner pointed, and did it pay off
  const repOf = (rows) => rows.filter((r) => r.rep === 1).length;
  const kH = repOf(fd.onHot), kAll = repOf(fd.cold);
  cards.push(dropCard("Hot job → proposal",
    [el("b", null, fmt.num(fd.hot.length - fd.hotApplied.length)), " hot jobs got no proposal"],
    [
      el("li", null, ["You applied on ", el("b", null, pct(rate(fd.hotApplied.length, fd.hot.length))),
        " of the jobs the scanner flagged."]),
      fd.notFlagged.length ? el("li", null, [
        linkBtn(plural(fd.notFlagged.length, "proposal"), () => proposalDrill(
          "Proposals the scanner did not flag", fd.notFlagged,
          { subtitle: "it scored the job below hot, or never read it" })),
        ` went to jobs the scanner scored lower or never read (${fmt.num(fd.unseen.length)} never read).`]) : null,
      fd.onHot.length && fd.notFlagged.length ? el("li", null, [
        "Written back: ", el("b", null, pct(rate(kH, fd.onHot.length))), ` on hot jobs (${fmt.num(fd.onHot.length)}), `,
        el("b", null, pct(rate(kAll - kH, fd.notFlagged.length))), ` on the others (${fmt.num(fd.notFlagged.length)}): `
        + compareWords(kH, fd.onHot.length, kAll, fd.cold.length) + "."]) : null,
    ],
    [linkBtn("the hot jobs with no proposal", () => hotDrill(L, "Hot jobs with no proposal",
      fd.hot.filter((h) => !h.pid), "flagged hot in this period; nobody applied")),
    " · ", linkBtn("the scanner's charts →", () => goTo(ctx, "scanner"))]));

  // 2. sent → opened
  const speed = bestWorst(fd.cold, "speed", 1), pile = bestWorst(fd.cold, "pile", 1);
  cards.push(dropCard("Sent → opened",
    [el("b", null, fmt.num(st[0].n - st[1].n)), " never opened"],
    [bwLine(speed, "Opened", "speed"), bwLine(pile, "Opened", "pile"),
      !speed && !pile ? el("li", "muted", "Too few proposals in this period to say what moves the open.") : null],
    linkBtn("break it down on Proposals →", () => goTo(ctx, "proposals"))));

  // 3. opened → wrote back
  const opening = bestWorst(st[1].rows, "opening", 2);
  const t1 = (L.tests || []).find((t) => t.status === "running");
  cards.push(dropCard("Opened → wrote back",
    [el("b", null, fmt.num(st[1].n - st[2].n)), " opened and did not write back"],
    [bwLine(opening, "Written back", "opening"),
      !opening ? el("li", "muted", "Too few opened proposals in this period to compare the first lines.") : null,
      t1 ? el("li", null, ["Being tested now: ", linkBtn(t1.title, () => goTo(ctx, "tests"))]) : null]));

  // 4. wrote back → hired: the question he asked by name
  const lost = st[2].rows.filter((r) => r.hired !== 1);
  const talks = L.talks || {};
  const codes = codeIndex(L);
  const did = {};
  lost.forEach((r) => { const k = r.did && r.did !== "hired_us" ? r.did : "unknown"; did[k] = (did[k] || 0) + 1; });
  const lastUs = lost.filter((r) => (talks[r.id] || {}).lf === "us").length;
  const lastThem = lost.filter((r) => (talks[r.id] || {}).lf === "them").length;
  const noMsgs = lost.filter((r) => (talks[r.id] || {}).th == null).length;
  const calls = lost.filter((r) => (talks[r.id] || {}).call === 1).length;
  const read = lost.filter((r) => codes[r.id]).length;
  const reasons = {};
  lost.forEach((r) => { const c = codes[r.id]; if (c && c.lost && c.lost !== "not_lost") reasons[c.lost] = (reasons[c.lost] || 0) + 1; });
  const topReasons = Object.entries(reasons).sort((a, c) => c[1] - a[1]).slice(0, 3);
  cards.push(dropCard("Wrote back → hired",
    [el("b", null, fmt.num(lost.length)), " wrote back and did not hire you"],
    [
      lost.length ? el("li", null, ["Upwork says: ", ["hired_other", "interviewed", "nobody", "unknown"]
        .filter((k) => did[k]).map((k) => `${(DID[k] || "not known").toLowerCase()} ${fmt.num(did[k])}`).join(", ") + "."]) : null,
      lost.length ? el("li", null, [`In the messages: you wrote last in ${fmt.num(lastUs)} (they went quiet), they wrote last in `,
        el("b", null, fmt.num(lastThem)), noMsgs ? `; ${fmt.num(noMsgs)} not stored yet.` : "."]) : null,
      calls ? el("li", null, `A call came up in ${fmt.num(calls)} of them.`) : null,
      lost.length ? el("li", null, read
        ? [`The conversation reader has read ${fmt.num(read)} of ${fmt.num(lost.length)}`,
          topReasons.length ? ": " + topReasons.map(([k, n]) => `${LOST[k] || k} ${n}`).join(", ") + "." : "."]
        : `The conversation reader has not read these yet (${readerWhy(L)}).`) : null,
    ],
    linkBtn("see each conversation →", () => goTo(ctx, "talks"))));

  return el("div", "lc-drops", cards);
}

function readerWhy(L) {
  const a = ((L.agents || {}).agents || []).find((x) => x.id === "reader");
  if (a && (a.blockers || []).includes("ai_credit")) return "waiting on AI credit";
  if (a && a.state === "off") return "it is not switched on yet";
  return "it reads a few each run";
}

function codeIndex(L) {
  const out = {};
  ((L.codes || {}).rows || []).forEach((c) => { if (c.pid) out[String(c.pid)] = c; });
  return out;
}

/** after the hire, in the period: new clients, money in, paying now, stopped, came back, win-back */
function afterStrip(D, L, fd, ctx) {
  const C = L.clients || {};
  if (C.blocked) return notice("warn", "Who is paying is held back: " + (C.reason || "the finance doctor is RED") + ".");
  const rows = C.rows || [];
  const b = fd.b;
  const inc = F().income(D.money, b);
  const fresh = rows.filter((r) => r.origin && r.origin.upwork && inB(r.first_paid, b));
  const paying = rows.filter((r) => r.stage === "paying");
  const stopped = rows.filter((r) => r.stage === "stopped" && inB(r.last_paid, b));
  const back = rows.filter((r) => (r.returns || []).some((x) => inB(x.on, b)));
  const wb = ((C.winback || {}).top) || [];
  const item = (k, v, sub, go) => el(go ? "button" : "div", go
    ? { class: "strip-item", type: "button", on: { click: go } } : "strip-item",
    [el("span", "k", k), el("span", "v", v), sub ? el("span", "muted small", sub) : null]);
  return el("div", "strip lc-after", [
    item("New clients", fmt.num(fresh.length), "found on Upwork, first paid in this period",
      () => clientsDrill(L, "New clients in this period", fresh)),
    item("Money in", inc ? fmt.usd(inc.total) : "—", "from every client found on Upwork",
      inc ? () => goTo(ctx, "proposals") : null),
    item("Paying now", fmt.num(paying.length), `paid in the last ${(C.rules || {}).paying_days} days`,
      () => clientsDrill(L, "Paying now", paying)),
    item("Stopped", fmt.num(stopped.length), "last paid in this period",
      () => clientsDrill(L, "Stopped paying in this period", stopped)),
    item("Came back", fmt.num(back.length), "paid again after a long gap",
      () => clientsDrill(L, "Came back in this period", back)),
    item("Worth a check-in", fmt.num(wb.length), "past clients, ranked",
      () => goTo(ctx, "after")),
  ]);
}

function agentsStrip(L, ctx) {
  const ag = ((L.agents || {}).agents) || [];
  return el("div", "lc-agents-strip", ag.map((a) => el("button", {
    class: "lc-agent-chip", type: "button", attrs: { title: a.does }, on: { click: () => goTo(ctx, "agents") },
  }, [el("span", "dot " + (a.state === "working" ? "ok" : a.state === "off" ? "" : a.state === "failing" ? "warn" : "bad")),
    el("span", null, a.name), el("span", "muted small", STATE_WORDS[a.state] || a.state)])));
}

function flowView(D, L, ctx, state) {
  const out = el("div", "stack lc-page");
  const head = el("div");
  const body = el("div", "stack lc-body");
  out.append(head, body);
  function redraw() {
    saveState(state);
    const b = F().periodBounds(state.period, D.as_of || L.as_of);
    const fd = flowData(D, L, b);
    const label = (F().PERIODS.find((p) => p.k === state.period) || {}).label || "";
    clear(head).append(el("div", "fn-filters", [
      segmented(F().PERIODS.map((p) => [p.k, p.label]), state.period,
        (v) => { state.period = v; redraw(); }, "Period"),
    ]));
    const st = fd.st;
    const paying = ((L.clients || {}).rows || []).filter((r) => r.stage === "paying").length;
    clear(body);
    append(body,
      blockersBanner(L, ctx, { quietWhenClear: true, link: true }),
      el("p", "lc-lede", [`${label}: the scanner flagged `, el("b", null, fmt.num(fd.hot.length)),
        ` hot job${fd.hot.length === 1 ? "" : "s"}, you sent `, el("b", null, fmt.num(st[0].n)),
        ` proposal${st[0].n === 1 ? "" : "s"}, `, el("b", null, fmt.num(st[1].n)),
        st[1].n === 1 ? " was opened, " : " were opened, ", el("b", null, fmt.num(st[2].n)),
        " wrote back and ", el("b", null, fmt.num(st[3].n)), " hired you.",
        (L.clients || {}).blocked ? "" : ` ${plural(paying, "client is", "clients are")} paying now.`]),
      section("From job to client", "click any step for the rows behind it", stripBlock(D, L, fd, ctx)),
      section("Where they drop, and what we know about why", null, dropsBlock(D, L, fd, ctx)),
      section("After the hire", null, afterStrip(D, L, fd, ctx),
        { right: linkBtn("everyone, and the win-back list →", () => goTo(ctx, "after")) }),
      testsMini(L, ctx),
      section("The agents", "each does one job; they share one knowledge base", agentsStrip(L, ctx),
        { right: linkBtn("what each one does →", () => goTo(ctx, "agents")) }));
  }
  redraw();
  return out;
}

/* ══ Conversations ════════════════════════════════════════════════════════ */

function talkRows(D, L, rows) {
  const talks = L.talks || {};
  const codes = codeIndex(L);
  return rows.map((r) => Object.assign({}, r, { t: talks[r.id] || null, c: codes[r.id] || null }));
}

function lastWords(t) {
  if (!t || t.th == null) return el("span", "muted", "not stored yet");
  if (!t.lf) return el("span", "muted", "—");
  return el("span", t.lf === "them" ? "lc-them" : null,
    [t.lf === "them" ? "they did" : "you did", el("span", "muted", " · " + fmt.dateShort(t.l))]);
}

function noteCell(r) {
  if (r.c) {
    return el("div", "stack-1", [
      r.c.lost && r.c.lost !== "not_lost" ? el("b", null, LOST[r.c.lost] || r.c.lost) : null,
      el("span", "small", r.c.summary || r.c.lost_detail || ""),
      r.c.next && r.c.next.toLowerCase() !== "none" ? el("span", "small muted", "Next: " + r.c.next) : null,
    ]);
  }
  return el("span", "muted small", "not read yet");
}

const TALK_COLS = [
  { key: "sent", label: "Sent", render: (r) => el("span", "muted nowrap", fmt.dateShort(r.sent)) },
  { key: "title", label: "Job", render: (r) => el("div", "stack-1", [
    r.url ? extLink(r.url, r.title || r.id, { title: "the job on Upwork" }) : el("span", null, r.title || r.id),
    el("span", "small muted", [r.k === "invite" ? "invitation" : "proposal", r.co ? " · " + r.co : ""])]) },
  { key: "msgs", label: "Messages", render: (r) => (r.t && r.t.th != null
    ? el("span", "nowrap", `${r.t.th} from them · ${r.t.us} from you`) : el("span", "muted", "—")) },
  { key: "last", label: "Wrote last", render: (r) => lastWords(r.t) },
  { key: "call", label: "Call", render: (r) => (r.t && r.t.call === 1 ? el("span", null, "came up")
    : el("span", "muted", "—")) },
  { key: "did", label: "Upwork says", render: (r) => (r.hired === 1 ? el("span", "pos", "Hired you")
    : r.did ? el("span", null, DID[r.did] || r.did) : el("span", "muted", "not known")) },
  { key: "note", label: "The reader's note", render: noteCell },
  { key: "room", label: "", render: (r) => (r.t && r.t.rm
    ? extLink(ROOM_URL + r.t.rm, "messages", { title: "the conversation on Upwork" }) : null) },
];

function talksDrill(D, L, title, rows) {
  const tr = talkRows(D, L, rows);
  openDrill({
    title, subtitle: "every conversation behind this figure, newest first",
    rows: tr, total: tr.length, columns: TALK_COLS, wide: true,
    time: (r) => r.sent,
    search: (r) => `${r.title || ""} ${r.co || ""} ${(r.c && r.c.summary) || ""}`,
    facets: [
      { label: "Hired", allLabel: "All", of: (r) => (r.hired === 1 ? "hired you" : "did not") },
      { label: "Wrote last", allLabel: "Anyone", of: (r) => (!r.t || r.t.th == null ? "not stored"
        : r.t.lf === "them" ? "they did" : "you did") },
    ],
    defaultSort: "newest", emptyText: "No conversations behind this figure.",
  });
}

function talksView(D, L, ctx, state) {
  const out = el("div", "stack lc-page");
  const head = el("div");
  const body = el("div", "stack");
  out.append(head, body);
  function redraw() {
    saveState(state);
    const b = F().periodBounds(state.period, D.as_of || L.as_of);
    const all = F().inPeriod((D.facts || []).filter((r) => r.rep === 1), b);
    const rows = talkRows(D, L, all);
    const hired = rows.filter((r) => r.hired === 1);
    const lost = rows.filter((r) => r.hired !== 1);
    const pick = state.talks === "hired" ? hired : state.talks === "all" ? rows : lost;
    clear(head).append(el("div", "fn-filters", [
      segmented(F().PERIODS.map((p) => [p.k, p.label]), state.period,
        (v) => { state.period = v; redraw(); }, "Period"),
      segmented([["lost", `Did not hire you (${lost.length})`], ["hired", `Hired you (${hired.length})`],
        ["all", `All (${rows.length})`]], state.talks, (v) => { state.talks = v; redraw(); }, "Which"),
    ]));
    const codes = (L.codes || {});
    const lastThem = lost.filter((r) => r.t && r.t.lf === "them");
    clear(body);
    append(body,
      el("p", "lc-lede", [`In this period ${plural(rows.filter((r) => r.k !== "invite").length, "client")}`
        + ` answered a proposal and ${plural(rows.filter((r) => r.k === "invite").length, "invitation")}`
        + " became a conversation; ",
        el("b", null, fmt.num(hired.length)), " hired you and ", el("b", null, fmt.num(lost.length)),
        " did not. ",
        lastThem.length ? [el("b", null, plural(lastThem.length, "conversation")),
          ` ended with their message, not yours — worth a look.`] : ""]),
      readerNotice(L),
      pick.length
        ? table({ columns: TALK_COLS, rows: pick.slice().sort((a, c) => String(c.sent).localeCompare(String(a.sent))),
          wrapClass: "lc-table" })
        : emptyState("No conversations in this period.", { icon: "message-square" }),
      el("p", "muted small foot", "Counts come from the messages stored here; "
        + `${fmt.num(((L.kb || {}).gaps || {}).rooms_with_messages)} of ${fmt.num(((L.kb || {}).gaps || {}).proposal_rooms)}`
        + " proposal conversations have their messages stored so far, and the rest arrive with the backfill."
        + ` The reader never reads ${(codes.excluded || []).length ? "Globaltize" : "an excluded client"}.`));
  }
  redraw();
  return out;
}

function readerNotice(L) {
  const c = L.codes || {};
  const a = ((L.agents || {}).agents || []).find((x) => x.id === "reader") || {};
  const spent = c.spent_usd || 0, budget = c.budget_usd;
  const n = (c.rows || []).length;
  const tone = n ? "info" : "warn";
  const words = n
    ? `The conversation reader has read ${plural(n, "conversation")} (${fmt.usd2(spent)} of its ${fmt.usd(budget)} first-pass budget); ${fmt.num(c.due)} wait.`
    : `The conversation reader has not read any yet: ${readerWhy(L)}. ${fmt.num(c.due)} conversations wait; the first pass is capped at ${fmt.usd(budget)}.`;
  return notice(tone, words + (a.state === "blocked" && n ? " It is paused: " + readerWhy(L) + "." : ""));
}

/* ══ After the hire ═══════════════════════════════════════════════════════ */

function originCell(r) {
  const o = r.origin || {};
  const bits = [o.words || "not on record"];
  if (o.referred_by) bits.push("by " + o.referred_by);
  return el("span", o.upwork ? null : "muted", bits.join(" "));
}

const CLIENT_COLS = [
  { key: "name", label: "Client", render: (r) => el("div", "stack-1", [el("b", null, r.name),
    r.referred && r.referred.length ? el("span", "small muted", "referred " + r.referred.join(", ")) : null]) },
  { key: "origin", label: "How they reached you", render: originCell },
  { key: "first_paid", label: "First paid", render: (r) => el("span", "nowrap", fmt.date(r.first_paid)) },
  { key: "months", label: "Months", num: true, render: (r) => fmt.num(r.months) },
  { key: "paid_usd", label: "Paid in all", num: true, render: (r) => fmt.usd(r.paid_usd) },
  { key: "last_paid", label: "Last paid", render: (r) => el("span", "nowrap", [fmt.date(r.last_paid),
    r.quiet_days != null ? el("span", "muted", ` · ${fmt.ago(r.quiet_days)}`) : null]) },
  { key: "came_back", label: "Came back", render: (r) => (r.came_back
    ? el("span", null, (r.returns || []).map((x) => `${fmt.date(x.on)}, after ${x.days} days`).join("; "))
    : el("span", "muted", "—")) },
];

function clientsDrill(L, title, rows) {
  openDrill({
    title, subtitle: "where each client is now; names as the Clients tab knows them",
    rows, total: rows.length, columns: CLIENT_COLS, wide: true,
    time: (r) => r.last_paid, search: (r) => `${r.name} ${(r.origin || {}).words || ""}`,
    amount: (r) => r.paid_usd,
    facets: [{ label: "Found", allLabel: "Anywhere", of: (r) => ((r.origin || {}).upwork ? "on Upwork" : "elsewhere") }],
    defaultSort: "newest", emptyText: "No clients behind this figure.",
  });
}

function winbackCard(r, i) {
  const room = r.room && r.room.room;
  const max = 100;
  return el("div", "lc-wb", [
    el("div", "lc-wb-head", [
      el("span", "lc-wb-rank", String(i + 1)),
      el("b", "lc-wb-name", r.name),
      el("span", "lc-wb-score", [el("i", { style: { width: Math.max(4, Math.min(100, (100 * r.score) / max)) + "%" } })]),
      el("span", "muted small", `${r.score} points`),
      r.resting_until ? pill(`drafted; next after ${fmt.dateShort(r.resting_until)}`, "outline") : null,
    ]),
    el("ul", "lc-wb-why", (r.why || []).map((w) => el("li", null, w))),
    r.angle ? el("div", "small", ["Angle: ", el("span", null, r.angle)]) : null,
    el("div", "lc-wb-foot", [
      el("span", "muted small", `${(r.origin || {}).words || ""}`),
      room ? extLink(ROOM_URL + room, "the conversation on Upwork", { class: "small" }) : null,
      r.draft ? btn("Copy the draft", (e) => copyText(r.draft.body, e.currentTarget),
        { sm: true, icon: "copy", aria: `copy the check-in draft for ${r.name}` }) : null,
      r.draft ? el("span", "muted small", `drafted ${fmt.dateShort(r.draft.at)}; you send it`) : null,
    ]),
  ]);
}

function afterView(D, L, ctx, state) {
  const C = L.clients || {};
  const out = el("div", "stack lc-page");
  if (C.blocked) {
    out.appendChild(notice("warn", "Who is paying is held back: " + (C.reason || "the finance doctor is RED") + "."));
    return out;
  }
  const rows = C.rows || [];
  const rules = C.rules || {};
  const paying = rows.filter((r) => r.stage === "paying");
  const stopped = rows.filter((r) => r.stage === "stopped");
  const back = rows.filter((r) => r.came_back);
  const wb = C.winback || { top: [], left_out: [] };
  const lists = { paying, stopped, back };
  const pickHost = el("div");
  function drawPick() {
    saveState(state);
    const which = lists[state.after] ? state.after : "paying";
    clear(pickHost).append(
      el("div", "fn-filters", [segmented([["paying", `Paying now (${paying.length})`],
        ["stopped", `Stopped (${stopped.length})`], ["back", `Came back (${back.length})`]],
      which, (v) => { state.after = v; drawPick(); }, "Which clients")]),
      table({ columns: CLIENT_COLS, rows: lists[which], wrapClass: "lc-table" }));
  }
  drawPick();
  const top = wb.top || [];
  const shown = top.slice(0, PEEK);
  const rest = top.slice(PEEK);
  const restHost = el("div", "lc-wb-grid");
  append(out,
    el("p", "lc-lede", [el("b", null, plural(paying.length, "client")), ` paid you in the last ${rules.paying_days} days; `,
      el("b", null, fmt.num(stopped.length)), " have stopped, and ", el("b", null, fmt.num(back.length)),
      " of everyone came back after a long gap at least once."]),
    section("Worth a check-in", `stopped at least ${rules.quiet_days} days ago, paid at least ${fmt.usd(rules.min_paid_usd)}; `
      + `up to ${rules.weekly_drafts} drafts a week, and you send them`,
    el("div", "stack", [
      top.length ? el("div", "lc-wb-grid", shown.map(winbackCard)) : emptyState("Nobody to suggest yet.", { icon: "users" }),
      rest.length ? btn(`Show the other ${rest.length}`, (e) => {
        e.currentTarget.remove();
        append(restHost, rest.map((r, i) => winbackCard(r, i + PEEK)));
      }, { sm: true, variant: "ghost" }) : null,
      restHost,
      (wb.left_out || []).length ? el("p", "muted small foot", "Not suggested: "
        + Object.entries((wb.left_out || []).reduce((m, r) => { m[r.reason] = (m[r.reason] || 0) + 1; return m; }, {}))
          .map(([k, n]) => `${n} ${k}`).join(", ") + ".") : null,
      el("p", "muted small foot", "The points, so the order can be argued with: money paid in all (up to 40),"
        + " how many months they paid in (up to 15), how recently they stopped (up to 25), came back before (10),"
        + " referred someone (5), an angle from the conversation (5); no Upwork conversation on record costs 5."),
    ])),
    section("Every client, by where they are now", "names as the Clients tab knows them", pickHost));
  return out;
}

/* ══ Tests ════════════════════════════════════════════════════════════════ */

const TEST_STATUS = { running: ["Running", "info"], decided: ["Decided", "ok"], stopped: ["Stopped", "outline"],
                      planned: ["Planned", "outline"] };

function scoreTable(t) {
  const sb = t.scoreboard || {};
  const arms = sb.arms || {};
  const rows = (t.arms || []).map((a) => Object.assign({ arm: a.arm, label: a.label }, arms[a.arm] || {},
    { drafts: (t.drafts || {})[a.arm] || 0, detected: (t.detected || {})[a.arm] || 0 }));
  return table({
    columns: [
      { key: "arm", label: "Version", render: (r) => el("div", "stack-1", [el("b", null, `${r.arm} · ${r.label}`)]) },
      { key: "drafts", label: "Drafts written", num: true, render: (r) => fmt.num(r.drafts) },
      { key: "sent", label: "Sent", num: true, render: (r) => fmt.num(r.sent || 0) },
      { key: "opened", label: "Opened", num: true, render: (r) => fmt.num(r.opened || 0) },
      { key: "replied", label: "Wrote back", num: true, render: (r) => fmt.num(r.replied || 0) },
      { key: "hired", label: "Hired", num: true, render: (r) => fmt.num(r.hired || 0) },
      { key: "rate", label: `Rate (${sb.metric_words || t.metric})`, num: true,
        render: (r) => el("b", null, r.rate == null ? "—" : pct(r.rate)) },
    ],
    rows, wrapClass: "lc-table",
  });
}

function testCard(t, opts = {}) {
  const sb = t.scoreboard || {};
  const [sw, tone] = TEST_STATUS[t.status] || [t.status, "outline"];
  return el("div", "card lc-test", [
    el("div", "lc-sec-head", [
      el("h3", "lc-h", t.title), pill(sw, tone),
      t.started_at ? el("span", "muted small", `since ${fmt.dateShort(t.started_at)}`
        + (sb.weeks >= 1 ? `, ${sb.weeks} weeks` : "")) : null,
    ]),
    el("p", "lc-test-change", t.change),
    scoreTable(t),
    el("div", "callout " + (sb.tone === "good" ? "ok" : ""), [icon(sb.tone === "bad" ? "alert-triangle" : "info", { size: 16, class: "ico" }),
      el("div", null, [el("b", null, "Verdict: "), sb.verdict || "—"])]),
    opts.compact ? null : el("p", "muted small foot", `Called when each version has ${t.min_per_arm} proposals and`
      + ` ${t.wait_days} days have passed since the last one; at most ${t.max_weeks} weeks. Only drafts whose`
      + " version the alternation chose are compared; letters you wrote yourself are counted beside it"
      + ` (${fmt.num(Object.values(t.detected || {}).reduce((s, n) => s + n, 0))} so far). Approved: ${t.approved_by || "—"}.`),
    opts.compact ? null : el("details", "lc-details", [el("summary", null, "Why this test"), el("p", null, t.hypothesis)]),
    t.decision ? el("p", null, [el("b", null, "Decision: "), `${t.decision}. ${t.lesson || ""}`,
      t.next_idea ? el("span", "muted", ` Next: ${t.next_idea}`) : null]) : null,
  ]);
}

function testsMini(L, ctx) {
  const t = (L.tests || []).find((x) => x.status === "running");
  if (!t) return null;
  return section("The running test", null, testCard(t, { compact: true }),
    { right: linkBtn("all tests →", () => goTo(ctx, "tests")) });
}

function testsView(D, L) {
  const ts = L.tests || [];
  const out = el("div", "stack lc-page");
  if (!ts.length) {
    out.appendChild(emptyState("No test yet.", { icon: "zap" }));
    return out;
  }
  const running = ts.filter((t) => t.status === "running");
  const done = ts.filter((t) => t.status !== "running");
  append(out,
    el("p", "lc-lede", "One change at a time: every other draft gets the new version, so both versions meet the"
      + " same weeks and the same kinds of jobs. A test is called in plain words, and every decision goes in the log."),
    running.map((t) => testCard(t)),
    section("The log", "decided and stopped tests, newest first",
      done.length ? el("div", "stack", done.map((t) => testCard(t)))
        : emptyState("No test has been called yet.", { icon: "clock" })));
  return out;
}

/* ══ Agents ═══════════════════════════════════════════════════════════════ */

function taskLine(t) {
  const words = TASK_WORDS[t.task] || "a scheduled job";
  if (!t.ran) return el("li", "muted", `${words}: not switched on yet`);
  const bad = t.last_status === "failed" && !t.recovered;
  return el("li", null, [
    el("span", null, words + ": "),
    el("span", bad ? "neg" : null, `last ran ${fmt.dateTimeShort(t.last_at)}, `
      + (t.recovered ? "failed before the sign-in was renewed; fine from its next run" : bad ? "failed" : "fine")),
    el("span", "muted", ` · ${fmt.num(t.ok_24h)} fine, ${fmt.num(t.failed_24h)} failed in 24 hours`
      + (t.ai_usd_today ? ` · AI today ${fmt.usd2(t.ai_usd_today)}` : "")),
  ]);
}

function agentCard(a) {
  return el("div", "card lc-agent", [
    el("div", "lc-sec-head", [el("h3", "lc-h", a.name), pill(STATE_WORDS[a.state] || a.state, STATE_TONE[a.state])]),
    el("p", "lc-agent-does", a.does),
    el("ul", "lc-agent-tasks", (a.tasks || []).map(taskLine)),
    (a.made || []).length ? el("div", "lc-agent-made", (a.made || []).map(([k, v]) => el("span", null,
      [el("b", null, v == null ? "—" : fmt.num(v)), " " + k]))) : null,
    el("div", "muted small", "in the last 7 days"),
  ]);
}

function agentsView(D, L, ctx) {
  const A = L.agents || {};
  const kb = L.kb || {};
  const gaps = kb.gaps || {};
  return el("div", "stack lc-page", [
    blockersBanner(L, ctx),
    el("div", "lc-agent-grid", (A.agents || []).map(agentCard)),
    section("The knowledge base they share", "every agent reads and writes the same stores",
      el("div", "stack", [
        table({
          columns: [
            { key: "words", label: "What it holds" },
            { key: "rows", label: "Rows", num: true, render: (s) => fmt.num(s.rows) },
            { key: "newest", label: "Newest", render: (s) => el("span", "muted nowrap", s.newest ? fmt.dateTimeShort(s.newest) : "—") },
          ],
          rows: kb.stores || [], wrapClass: "lc-table",
        }),
        el("p", "muted small foot", `${fmt.num(gaps.rooms_with_messages)} of ${fmt.num(gaps.proposal_rooms)} proposal`
          + " conversations have their messages stored here; the backfill brings the rest once Upwork is signed in again."),
      ])),
  ]);
}

/* ══ render ═══════════════════════════════════════════════════════════════ */

export async function render(root, ctx) {
  const cache = {};
  const mctx = memoCtx(ctx, cache);
  const [L, D] = await Promise.all([mctx.api("/api/lifecycle"), mctx.api("/api/upwork/funnel")]);
  if (!F()) {
    root.appendChild(notice("bad", "The funnel arithmetic did not load (/static/js/propfunnel.js)."));
    return;
  }
  root.append(el("div", "up-head", [
    el("div", "hstack", [el("h2", "up-title", "Pipeline"),
      el("span", "muted small", `as of ${fmt.dateShort(L.as_of || D.as_of)}`)]),
    el("p", "up-lede", "Upwork as the sales engine: from the job the scanner finds to the client who pays, and back again."),
  ]));
  if (!L || !L.built || !D || !D.built) {
    root.appendChild(emptyState((L && L.reason) || (D && D.reason) || "Nothing to show yet.",
      { icon: "info", title: "The pipeline has no data yet" }));
    return;
  }
  const state = loadState();
  const nBlock = ((L.agents || {}).blockers || []).length;
  await subtabs(root, {
    section: "pipeline", tabs: TABS, ctx, storageKey: "ew_cockpit_pipeline_view",
    badge: (id) => {
      if (id === "agents") return nBlock ? el("span", "n warn", String(nBlock)) : el("span", "n ok", "✓");
      if (id === "tests") {
        const n = (L.tests || []).filter((t) => t.status === "running").length;
        return n ? el("span", "n", `${n} running`) : null;
      }
      return null;
    },
    views: {
      flow: () => flowView(D, L, ctx, state),
      scanner: async () => { const box = el("div"); await renderAnalysis(box, mctx); return box; },
      proposals: async () => { const box = el("div"); await renderFunnel(box, mctx); return box; },
      talks: () => talksView(D, L, ctx, state),
      after: () => afterView(D, L, ctx, state),
      tests: () => testsView(D, L),
      agents: () => agentsView(D, L, ctx),
    },
  });
}
