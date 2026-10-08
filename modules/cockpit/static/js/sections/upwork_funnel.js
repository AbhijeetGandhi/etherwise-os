// ════════════════════════════════════════════════════════════════════════════
// sections/upwork_funnel.js — the Upwork tab's "Funnel" view (Upwork brief
// Phase 2, Loop 1). Abhijeet, 2026-10-08: "applied to 100 jobs, 20 were
// opened, three replies, closed one — I want to see our percentage in every
// stage ... then the breakdown: boosted, with Loom, without, what the first
// lines said, the client ... proper funnel diagrams, I can hover over it".
//
// One fetch (/api/upwork/funnel) returns the FACTS, one row per proposal.
// Everything on this page is computed from them by ../propfunnel.js (pure,
// unit-tested under node), and every drill opens the rows the figure counted.
//
// Top to bottom:
//   filters      period · cold proposals | invitations   (scope everything)
//   the funnel   sent → opened → wrote back → hired, hover + drill, with the
//                money beside it (connects in $, revenue the hires brought)
//   money in     what came in during the period from every client first met
//                on Upwork — through your profile, the agency, or directly
//                after they moved to Wise / Stripe / the bank (2026-10-08:
//                "globaltize hired us from upwork ... pays us through wise")
//   what we know the latest written reading (proposal_insights), dated
//   break it down   split by any dimension, then by a second one
//   over time    sent / opened per month, and the two rates
//   first lines  what the client saw before deciding to open
//   invitations  received → accepted → hired, per month
//
// COPY RULES (the Upwork tab's): plain words, no statistics vocabulary. A
// difference is "clear" or "a lean" or nothing; small groups say "too few to
// tell". "—" means not measured, never zero. No send route, no model call.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import { el, append, clear, fmt, icon, pill, notice, emptyState, btn, extLink, table } from "../ui.js";
import { openDrill } from "../drill.js";
import { chart, axes } from "../charts.js";

const STATE_KEY = "ew_cockpit_funnel_state";
const SVGNS = "http://www.w3.org/2000/svg";
const LINES_PAGE = 15;

const F = () => window.EW_PROPFUNNEL;
const MIN_MONTH = 5;                                // proposals before a month's open rate is drawn
const MIN_OPENED = 4;                               // opened before its reply rate is drawn

function loadState() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem(STATE_KEY) || "{}") || {}; } catch (e) { s = {}; }
  return {
    period: s.period || "ytd",
    kind: s.kind === "invite" ? "invite" : "cold",
    dim: s.dim || "boost",
    dim2: s.dim2 || "",
    lines: s.lines || "answered",
  };
}
function saveState(s) {
  try { localStorage.setItem(STATE_KEY, JSON.stringify(s)); } catch (e) { /* private mode */ }
}

const pct = (x, digits = 0) => (x == null ? "—" : fmt.pct(100 * x, digits));
const plural = (n, one, many) => `${fmt.num(n)} ${n === 1 ? one : (many || one + "s")}`;

function svg(tag, attrs, children) {
  const n = document.createElementNS(SVGNS, tag);
  Object.entries(attrs || {}).forEach(([k, v]) => { if (v != null) n.setAttribute(k, v); });
  (children || []).forEach((c) => { if (c) n.appendChild(c); });
  return n;
}
function svgText(x, y, text, cls, anchor) {
  const t = svg("text", { x, y, class: cls, "text-anchor": anchor || "start" });
  t.textContent = text;
  return t;
}

/* ── the one tooltip ──────────────────────────────────────────────────────── */

let _tip = null;                                    // one tooltip node for the page, reused across renders

function makeTip(host) {
  if (_tip && _tip.isConnected) _tip.remove();
  const tip = el("div", { class: "fn-tip", attrs: { role: "status", "aria-live": "polite" } });
  host.appendChild(tip);
  _tip = tip;
  let shown = false, owner = null;
  // a re-render can remove the mark under a resting pointer without a
  // pointerleave, so the tooltip also goes on scroll and whenever its mark
  // is no longer in the page
  window.addEventListener("scroll", () => { tip.classList.remove("show"); shown = false; }, { passive: true });
  function place(x, y) {
    const pad = 12, r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width + pad > window.innerWidth) left = x - r.width - 14;
    if (top + r.height + pad > window.innerHeight) top = y - r.height - 14;
    tip.style.left = Math.max(pad, left) + "px";
    tip.style.top = Math.max(pad, top) + "px";
  }
  return {
    show(content, x, y, node) {
      owner = node || null;
      clear(tip);
      append(tip, content);
      tip.classList.add("show");
      shown = true;
      place(x, y);
    },
    move(x, y) {
      if (owner && !owner.isConnected) { this.hide(); return; }
      if (shown) place(x, y);
    },
    hide() { tip.classList.remove("show"); shown = false; owner = null; },
    /** wire a mark: hover, keyboard focus and click all do the right thing */
    bind(node, content, onClick) {
      node.addEventListener("pointerenter", (e) => this.show(content(), e.clientX, e.clientY, node));
      node.addEventListener("pointermove", (e) => this.move(e.clientX, e.clientY));
      node.addEventListener("pointerleave", () => this.hide());
      node.addEventListener("focus", () => {
        const b = node.getBoundingClientRect();
        this.show(content(), b.left + b.width / 2, b.top + b.height, node);
      });
      node.addEventListener("blur", () => this.hide());
      if (onClick) {
        node.addEventListener("click", () => { this.hide(); onClick(); });
        node.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.hide(); onClick(); }
        });
      }
    },
  };
}

function tipBody(title, lines, foot) {
  return [
    el("div", "fn-tip-title", title),
    ...lines.map(([v, label]) => el("div", "fn-tip-row", [el("b", null, v), el("span", null, " " + label)])),
    foot ? el("div", "fn-tip-foot", foot) : null,
  ];
}

/* ── the drill: the proposals behind any figure ───────────────────────────── */

const dateCell = (iso) => el("span", "muted nowrap", iso ? fmt.dateShort(iso) : "—");
const yesNo = (v, yes) => (v === 1 ? el("span", "pos", yes) : el("span", "muted", "—"));

function bidText(r) {
  if (r.bid == null) return "—";
  return r.bid >= 100 ? fmt.usd(r.bid) + " fixed" : "$" + Number(r.bid).toString() + "/h";
}

const PROPOSAL_COLS = [
  { key: "sent", label: "Sent", render: (r) => dateCell(r.sent) },
  { key: "title", label: "Job", render: (r) => el("div", "stack-1", [
    r.url ? extLink(r.url, r.title || r.id, { title: "the job on Upwork" }) : el("span", null, r.title || r.id),
    r.fl ? el("span", "small muted fn-clamp", r.fl) : null,
  ]) },
  { key: "bid", label: "Bid", render: (r) => el("span", "nowrap", bidText(r)) },
  { key: "boost", label: "Boost", render: (r) => (r.boost === 1
    ? el("span", null, [String(r.cx != null ? r.cx : "?"), el("span", "muted", r.bo === "outbid, bid returned" ? " outbid" : "")])
    : el("span", "muted", "—")) },
  { key: "op", label: "Opened", render: (r) => (r.op === 1 ? el("span", "nowrap", fmt.dateShort(r.od))
    : r.rep === 1 ? el("span", "muted", "from the list") : el("span", "muted", r.op === 0 ? "no" : "—")) },
  { key: "rep", label: "Wrote back", render: (r) => yesNo(r.rep, "yes") },
  { key: "hired", label: "Hired", render: (r) => (r.hired !== 1 ? el("span", "muted", "—")
    : r.hw === "after" ? el("span", { class: "pos nowrap", attrs: { title: "Upwork does not mark this proposal"
      + " hired; the client in this conversation paid you after it (another job, or off Upwork)" } }, "client later")
    : el("span", "pos", "hired")) },
  { key: "id", label: "", render: (r) => extLink("https://www.upwork.com/nx/proposals/" + r.id, "proposal",
    { title: "your proposal on Upwork" }) },
];

function drillRows(title, rows, opts = {}) {
  openDrill({
    title,
    subtitle: opts.subtitle,
    note: opts.note || null,
    rows, total: rows.length, columns: PROPOSAL_COLS, wide: true,
    time: (r) => r.sent,
    search: (r) => `${r.title || ""} ${r.fl || ""} ${r.co || ""} ${r.cat || ""}`,
    facets: [
      { label: "Opened", allLabel: "All", of: (r) => (F().seen(r) ? "opened" : "not opened") },
      { label: "Boost", allLabel: "All", of: (r) => (r.boost === 1 ? "boosted" : "not boosted") },
    ],
    defaultSort: "newest",
    emptyText: "No proposals behind this figure.",
  });
}

/* ── render ───────────────────────────────────────────────────────────────── */

export async function renderFunnel(root, ctx) {
  const d = await ctx.api("/api/upwork/funnel");
  if (!F()) {
    root.appendChild(notice("bad", "The funnel arithmetic did not load (/static/js/propfunnel.js)."));
    return;
  }
  if (!d || !d.built) {
    root.appendChild(emptyState(d && d.reason ? d.reason : "The funnel has no data yet.",
      { icon: "info", title: "Nothing to show yet" }));
    return;
  }
  const state = loadState();
  const page = el("div", "stack fn-page");
  root.appendChild(page);
  const tip = makeTip(document.body);
  const all = d.facts || [];

  const filterHost = el("div");
  const body = el("div", "stack fn-body");
  page.append(filterHost, body);

  function selection() {
    const b = F().periodBounds(state.period, d.as_of);
    const rows = F().inPeriod(all.filter((r) => r.k === state.kind), b);
    return { rows, bounds: b };
  }

  function redraw() {
    saveState(state);
    tip.hide();
    clear(filterHost).append(filters(d, state, redraw));
    const sel = selection();
    clear(body);
    if (!sel.rows.length) {
      body.appendChild(emptyState(`No ${state.kind === "invite" ? "accepted invitations" : "proposals"} in this period.`,
        { icon: "inbox" }));
      return;
    }
    append(body,
      heroBlock(d, state, sel, tip),
      incomeBlock(d, state, sel),
      insightsBlock(d, state, redraw),
      splitBlock(d, state, sel, tip, redraw),
      timeBlock(d, state, sel),
      linesBlock(d, state, sel, redraw),
      invitesBlock(d, tip),
      footBlock(d));
  }
  redraw();
}

/* ── filters ──────────────────────────────────────────────────────────────── */

function segmented(options, value, onPick, label) {
  return el("div", { class: "segmented", attrs: { role: "group", "aria-label": label } },
    options.map(([v, text]) => el("button", {
      class: "segbtn" + (value === v ? " on" : ""), type: "button",
      attrs: { "aria-pressed": String(value === v) },
      on: { click: () => onPick(v) },
    }, text)));
}

function filters(d, state, redraw) {
  const fresh = d.fresh || {};
  const behind = fresh.not_in_funnel;
  return el("div", "stack-2", [
    el("div", "fn-filters", [
      segmented(F().PERIODS.map((p) => [p.k, p.label]), state.period,
        (v) => { state.period = v; redraw(); }, "Period"),
      segmented([["cold", "Cold proposals"], ["invite", "Invitations"]], state.kind,
        (v) => { state.kind = v; redraw(); }, "Which proposals"),
      el("span", "grow"),
      el("span", "muted small", [
        "proposals to ", fmt.dateShort(fresh.newest_sent),
        " · opened dates read ", fmt.dateShort(fresh.views_at)]),
    ]),
    behind ? notice("warn", `${plural(behind, "newer proposal")} ${behind === 1 ? "is" : "are"} not in the funnel yet.`
      + " The daily refresh that adds them is the next thing to build; until then this view stops at "
      + fmt.dateShort(fresh.newest_sent) + ".") : null,
  ]);
}

/* ── 1. the funnel + money ────────────────────────────────────────────────── */

function heroBlock(d, state, sel, tip) {
  const st = F().stages(sel.rows, state.kind);
  const m = F().money(sel.rows, d.money);
  const card = el("section", "card fn-hero");
  card.append(
    el("div", "fn-hero-head", [
      el("h3", "fn-h", state.kind === "invite" ? "Invitations you accepted" : "The funnel"),
      el("span", "muted small", periodWords(state, sel)),
    ]),
    el("div", "fn-hero-grid", [
      el("div", "fn-diagram", funnelSvg(st, sel, state, tip)),
      el("div", "fn-side", moneyTiles(d, m, sel, state)),
    ]),
  );
  return card;
}

function periodWords(state, sel) {
  const p = F().PERIODS.find((x) => x.k === state.period);
  const lo = sel.bounds.lo ? fmt.date(sel.bounds.lo) : "the first proposal";
  return `${p ? p.label.toLowerCase() : ""} · sent from ${lo}`;
}

function funnelSvg(st, sel, state, tip) {
  const W = 640, barX0 = 160, barW = 320, valX = 498, rowH = 40, gapH = 40;
  const top = st[0].n || 1;
  const n = st.length;
  const H = n * rowH + (n - 1) * gapH + 4;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "fn-svg", role: "img",
    "aria-label": st.map((s) => `${s.label} ${s.n}`).join(", ") });
  const cx = barX0 + barW / 2;
  const widthOf = (k) => Math.max(6, barW * (k / top));
  const rec = F().opensOnRecord(st[1] ? st[1].rows : []);
  st.forEach((s, i) => {
    const y = i * (rowH + gapH);
    const w = widthOf(s.n);
    // the connector into the next stage: a soft wash, the drop made visible
    if (i < n - 1) {
      const wn = widthOf(st[i + 1].n);
      const y1 = y + rowH, y2 = y + rowH + gapH;
      root.appendChild(svg("path", {
        d: `M${cx - w / 2},${y1} L${cx + w / 2},${y1} L${cx + wn / 2},${y2} L${cx - wn / 2},${y2} Z`,
        class: "fn-conn",
      }));
      const nxt = st[i + 1];
      const drop = s.n - nxt.n;
      root.appendChild(svgText(0, y1 + gapH / 2 + 4,
        `↓ ${pct(nxt.ofPrev)} ${stepVerb(nxt.k, state.kind)}`, "fn-step"));
      root.appendChild(svgText(W, y1 + gapH / 2 + 4, `${fmt.num(drop)} ${dropWords(nxt.k, state.kind)}`, "fn-drop", "end"));
    }
    const tone = n === 2 ? (i ? 3 : 0) : Math.min(3, i);
    const g = svg("g", { class: `fn-stage fn-s${tone}${s.n === 0 ? " fn-empty" : ""}`,
      tabindex: "0", role: "button", "aria-label": `${s.label}: ${s.n}. Show these proposals.` });
    g.appendChild(svg("rect", { x: 0, y: y - 2, width: W, height: rowH + 4, class: "fn-hit" }));
    g.appendChild(svg("rect", { x: cx - w / 2, y, width: w, height: rowH, rx: 4, class: "fn-bar" }));
    g.appendChild(svgText(0, y + 17, s.label, "fn-label"));
    g.appendChild(svgText(0, y + 34, s.noun, "fn-sub"));
    g.appendChild(svgText(valX, y + 26, fmt.num(s.n), "fn-val"));
    if (i) g.appendChild(svgText(W, y + 26, pct(s.ofTop, s.ofTop < 0.1 ? 1 : 0) + " of all", "fn-of", "end"));
    root.appendChild(g);
    tip.bind(g, () => stageTip(s, st, i, rec, state), () => drillRows(
      `${s.label}: ${plural(s.n, "proposal")}`, s.rows,
      { subtitle: periodWords(state, sel),
        note: s.k === "opened" && rec.answeredFromList
          ? `${rec.answeredFromList} of these were answered straight from the list: Upwork records no open, but the client wrote back.`
          : null }));
  });
  return root;
}

function dropWords(k, kind) {
  if (kind === "invite") return "not hired";
  return { opened: "never opened", replied: "opened, no reply", hired: "talked, no hire" }[k] || "did not";
}

function stepVerb(k, kind) {
  if (kind === "invite") return "hired";
  return { opened: "opened", replied: "of those wrote back", hired: "of those hired you" }[k] || "";
}

function stageTip(s, st, i, rec, state) {
  const lines = [[fmt.num(s.n), s.noun]];
  if (i) lines.push([pct(s.ofPrev, 1), `of the ${st[i - 1].label.toLowerCase()} (${fmt.num(s.n)} of ${fmt.num(st[i - 1].n)})`]);
  if (i > 1) lines.push([pct(s.ofTop, 1), "of everything sent"]);
  if (s.k === "opened") {
    lines.push([fmt.num(rec.onRecord), "with Upwork's open date"]);
    if (rec.answeredFromList) lines.push([fmt.num(rec.answeredFromList), "answered from the list, no open on record"]);
    const h = F().median(s.rows.filter((r) => r.op === 1).map((r) => r.hto));
    if (h != null) lines.push([h < 48 ? `${Math.round(h)} h` : `${(h / 24).toFixed(1)} days`, "median wait for the first open"]);
  }
  if (s.k === "replied") {
    const h = F().median(s.rows.map((r) => r.rl));
    if (h != null) lines.push([h < 48 ? `${Math.round(h)} h` : `${(h / 24).toFixed(1)} days`, "median from sending to their first message"]);
  }
  const b = s.rows.filter((r) => r.boost === 1).length;
  if (state.kind === "cold" && s.n) lines.push([fmt.num(b), "of them boosted"]);
  return tipBody(s.label, lines, s.few ? "Too few at the step above to read much into the rate." : "Click to see the proposals");
}

function moneyTiles(d, m, sel, state) {
  const M = d.money;
  if (!M || M.blocked) {
    return [notice("bad", "Money is hidden while the finance doctor is RED"
      + (M && M.reds && M.reds.length ? ` (${M.reds.join(", ")})` : "") + ". The funnel itself is unaffected.")];
  }
  const hiredRows = sel.rows.filter((r) => r.hired === 1);
  const later = hiredRows.filter((r) => r.hw === "after").length;
  const hireSub = m.hires ? `${plural(m.hires, "hire")}` + (later ? ` (${later} became ${later === 1 ? "a client" : "clients"} later)` : "")
    + (m.revenueUnknown ? ` · ${m.revenueUnknown} with no payment found` : "") : "no hires in this selection";
  if (state.kind === "invite") {
    // answering an invitation costs no connects, so there is no spend to divide
    return [el("div", "fn-tiles", [
      tile("Revenue from these hires", m.hires && m.revenueHires ? fmt.usd(m.revenue) : "—", hireSub,
        m.hires ? () => revenueDrill(d, hiredRows) : null),
      tile("Connects spent", "$0", "answering an invitation costs no connects", null),
    ]), el("div", "small muted", "Revenue is everything each won client has paid since, on Upwork or off it."
      + " A few older hires have no payment under any name; they show as “no payment found”, never as $0.")];
  }
  const price = M.price_per_connect != null ? "$" + Number(M.price_per_connect).toFixed(2) : "—";
  const tiles = el("div", "fn-tiles", [
    tile("Connects spent", m.usd == null ? "—" : fmt.usd(m.usd),
      `${fmt.num(m.connects)} connects at ${price}` + (m.connectsUnknown ? ` · ${m.connectsUnknown} unknown` : ""), null),
    tile("Revenue from these hires", m.hires && m.revenueHires ? fmt.usd(m.revenue) : "—", hireSub,
      m.hires ? () => revenueDrill(d, hiredRows) : null),
    tile("Connects per hire", m.perHire == null ? "—" : fmt.usd(m.perHire),
      m.hires ? "spent ÷ hires" : "no hires to divide by", null),
    tile("Back per $1 of connects",
      m.returnPerDollar == null ? "—" : "$" + m.returnPerDollar.toFixed(m.returnPerDollar < 10 ? 1 : 0),
      "revenue ÷ connects spent", null),
  ]);
  const months = monthsOfPurchases(M, sel);
  return [
    tiles,
    el("button", { class: "linktext small fn-purch", type: "button", on: { click: () => purchasesDrill(M, sel) } },
      months.n ? `Connects bought in this period: ${fmt.usd(months.usd)} (${plural(months.n, "purchase")}) →`
        : "No connects bought in this period"),
    state.kind === "cold" ? el("div", "small muted", "Revenue is everything each won client has paid since you"
      + " won them: through Upwork on your profile or the agency, or directly (Wise, Stripe, the bank) after"
      + " moving off. Connects are what each proposal really cost: a boost that was outbid or refunded counts"
      + " at its base.") : null,
    M.registry === false ? notice("warn", "The client list did not load, so the ledger's spellings of one client"
      + " are not merged; some payments may be missing from a hire.") : null,
  ];
}

function tile(label, value, sub, onClick) {
  const b = el(onClick ? "button" : "div", {
    class: "fn-tile" + (onClick ? " click" : ""), type: onClick ? "button" : null,
  }, [
    el("div", "fn-tile-lbl", label),
    el("div", "fn-tile-val" + (value === "—" ? " dash-value" : ""), value),
    sub ? el("div", "fn-tile-sub", sub) : null,
  ]);
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

function monthsOfPurchases(M, sel) {
  const lo = sel.bounds.lo;
  const rows = (M.purchases || []).filter((p) => !lo || p.d >= lo);
  return { rows, n: rows.length, usd: rows.reduce((s, p) => s + (p.usd || 0), 0) };
}

function purchasesDrill(M, sel) {
  const { rows } = monthsOfPurchases(M, sel);
  openDrill({
    title: "Connects bought", subtitle: "the ledger's Connect Purchase rows (the Money page's own)",
    rows, total: rows.length,
    columns: [
      { key: "d", label: "Date", render: (r) => dateCell(r.d) },
      { key: "desc", label: "What" },
      { key: "usd", label: "Paid", num: true, render: (r) => fmt.usd2(r.usd) },
    ],
    amount: (r) => r.usd, time: (r) => r.d, defaultSort: "newest",
  });
}

function revenueDrill(d, hiredRows) {
  const M = d.money || {};
  const won = M.won || {};
  const rows = hiredRows.map((r) => {
    const w = won[r.id] || {};
    return { ...r, client: w.name, start: w.start, usd: w.usd, shared: w.shared_with, w };
  });
  openDrill({
    title: "What the hires brought in",
    subtitle: "everything each won client has paid since — through Upwork or directly",
    rows, total: rows.length, wide: true,
    columns: [
      { key: "sent", label: "Proposal", render: (r) => dateCell(r.sent) },
      { key: "title", label: "Job", render: (r) => (r.url ? extLink(r.url, r.title) : el("span", null, r.title)) },
      { key: "client", label: "Client", render: (r) => el("div", "stack-1", [
        el("span", null, r.client || "—"),
        r.w.via ? el("span", "small muted", "found by " + r.w.via) : null]) },
      { key: "hw", label: "How", render: (r) => (r.hw === "after"
        ? el("span", { class: "nowrap", attrs: { title: "Upwork does not mark this proposal hired" } }, "became a client later")
        : el("span", "muted nowrap", "hired on Upwork")) },
      { key: "start", label: "Counted from", render: (r) => dateCell(r.start || r.sent) },
      { key: "usd", label: "Revenue", num: true, render: (r) => (r.usd != null
        ? el("button", { class: "linknum", type: "button",
          attrs: { "aria-label": `the payments behind ${fmt.usd2(r.usd)}` },
          on: { click: () => hirePaymentsDrill(M, r) } }, fmt.usd2(r.usd))
        : el("span", "muted", r.shared ? "counted on their first hire" : "no payment found")) },
    ],
    amount: (r) => r.usd || 0, time: (r) => r.sent, defaultSort: "biggest",
  });
}

const payWhere = (x) => (x.ch === "freelancer" ? "Upwork · your profile" : x.ch === "agency" ? "Upwork · agency"
  : x.src || "directly");
const PAY_COLS = [
  { key: "d", label: "Date", render: (x) => el("span", "muted nowrap", fmt.date(x.d)) },
  { key: "ch", label: "Paid", render: (x) => el("span", "nowrap", payWhere(x)) },
  { key: "desc", label: "What", render: (x) => el("div", "stack-1", [
    el("span", "muted", x.desc || "—"),
    x.as ? el("span", "small muted", "ledger name: " + x.as) : null]) },
  { key: "usd", label: "Amount", num: true, render: (x) => fmt.usd2(x.usd) },
];

function hirePaymentsDrill(M, r) {
  const c = (M.clients || {})[r.w.client];
  const from = r.w.from || "";
  const rows = c ? c.rows.filter((x) => (x.m || "") >= from) : [];
  openDrill({
    title: `${r.client}: payments`,
    subtitle: `from ${fmt.date(from + "-01").replace(/^01 /, "")} on, every rail`,
    rows, total: rows.length, columns: PAY_COLS,
    facets: [{ label: "Paid", allLabel: "All", of: payWhere }],
    amount: (x) => x.usd, time: (x) => x.d, defaultSort: "newest",
  });
}

/* ── 1b. money in from the clients you found on Upwork ────────────────────── */
// Answers "how much did Upwork bring in over the last six months" — which the
// tiles above cannot: they follow the proposals SENT in the period, this
// follows the money that ARRIVED in it, from every client first met on Upwork
// (whatever year), on every rail. Sums by ../propfunnel.js income().

const CH_SUB = {
  freelancer: "contracts on your own profile",
  agency: "contracts run through the Etherwise agency",
  direct: "Wise, Stripe or the bank, once a client moved off Upwork",
};
const INC_PAGE = 10;

function foundWords(c) {
  if (c.by === "cold") return "a cold proposal";
  if (c.by === "invite") return "an invitation";
  if (c.by === "arranged") return "a direct offer";
  if (c.by === "contract") return c.profile === "agency" ? "an agency contract, no proposal on record"
    : "a contract, no proposal on record";
  if (c.by === "declared") return "Upwork, as you told it — before these records";
  return "Upwork payments, no proposal or contract on record";
}

function incomeBlock(d, state, sel) {
  const M = d.money;
  if (!M || M.blocked) return null;                  // the funnel card already says money is hidden
  const inc = F().income(M, sel.bounds);
  if (!inc) return null;
  const p = F().PERIODS.find((x) => x.k === state.period);
  const sec = el("section", "card fn-income");
  sec.append(el("div", "fn-hero-head", [
    el("h3", "fn-h", "Money in from clients you found on Upwork"),
    el("span", "muted small", (p ? p.label.toLowerCase() + " · " : "")
      + (sel.bounds.lo ? `paid from ${fmt.date(sel.bounds.lo)}` : "every payment on record")
      + " · cold proposals, invitations and contracts alike"),
  ]));
  if (!inc.clients.length) {
    sec.append(emptyState("No payments from Upwork clients in this period.", { icon: "inbox" }));
    return sec;
  }
  const say = el("div", null, `came in from ${plural(inc.clients.length, "client")} you first met on Upwork.`);
  if (inc.others > 0 && inc.share != null) {
    say.append(` That is ${pct(inc.share, inc.share > 0.99 ? 1 : 0)} of all revenue in the period; `,
      el("button", { class: "linktext", type: "button", on: { click: () => otherDrill(inc, sel) } },
        `the other ${fmt.usd(inc.others)} came from elsewhere`), ".");
  } else {
    say.append(" That is all of the revenue in the period.");
  }
  sec.append(el("div", "fn-inc-top", [
    el("button", { class: "fn-inc-big", type: "button",
      attrs: { "aria-label": `every payment behind ${fmt.usd2(inc.total)}` },
      on: { click: () => incomeDrill(inc, sel, {}) } }, fmt.usd(inc.total)),
    el("div", "fn-inc-say", [
      say,
      inc.newer != null ? el("div", "muted small", `${fmt.usd(inc.newer)} from clients first met in this period`
        + ` · ${fmt.usd(inc.older)} from clients met before it, who are still paying`) : null,
    ]),
  ]));
  sec.append(el("div", "fn-inc-tiles", F().CHANNELS.map((c) =>
    tile(c.label, fmt.usd(inc[c.k]), CH_SUB[c.k], inc[c.k] ? () => incomeDrill(inc, sel, { ch: c.k }) : null))));
  const chartHost = el("div");
  sec.append(el("div", "fn-inc-grid", [
    el("div", "stack-2", [el("div", "fn-side-h", "Month by month, by how it was paid"), chartHost,
      el("div", "muted small", "click a month for its payments")]),
    el("div", "stack-2", [el("div", "fn-side-h", "Client by client"), clientTable(inc, sel)]),
  ]));
  incomeChart(chartHost, inc, sel);
  sec.append(el("div", "small muted", "Counted by the day each payment arrived, before Upwork's fee (the Money"
    + " page's revenue). A client counts from the month you first met them on Upwork and keeps counting after"
    + " they move to Wise, Stripe or the bank; the ledger's different spellings of one client are merged"
    + " through the client list."));
  if (M.registry === false) sec.append(notice("warn", "The client list did not load, so names are not merged:"
    + " one client may show as several."));
  return sec;
}

// How a client outside the Upwork money reached you — the registry's
// `found_via` / `referred_by`, as Abhijeet said it (2026-10-08: "hipp health is
// also someone we found through alex"); "not known" when nobody has said.
function originWords(o) {
  if (!o) return "not known";
  if (o.found_via === "referral") return o.referred_by ? `a referral from ${o.referred_by}` : "a referral";
  if (o.found_via === "before") return "paid before you met them on Upwork";
  if (o.found_via === "other") return "not through Upwork";
  return "not known";
}

function otherDrill(inc, sel) {
  const rows = inc.otherRows || [];
  openDrill({
    title: "Revenue from clients not found on Upwork",
    subtitle: sel.bounds.lo ? `from ${fmt.date(sel.bounds.lo)}` : "every payment on record",
    rows, total: rows.length, wide: true,
    columns: [
      { key: "d", label: "Date", render: (x) => el("span", "muted nowrap", fmt.date(x.d)) },
      { key: "who", label: "Client", render: (x) => el("span", x.who || x.as ? null : "muted",
        x.who || x.as || "no name on the payment") },
      { key: "origin", label: "How they reached you", render: (x) => el("span", x.origin ? null : "muted",
        originWords(x.origin)) },
      { key: "desc", label: "What", cls: "muted" },
      { key: "usd", label: "Amount", num: true, render: (x) => fmt.usd2(x.usd) },
    ],
    facets: [{ label: "How", allLabel: "All", of: (x) => originWords(x.origin) }],
    amount: (x) => x.usd, time: (x) => x.d, defaultSort: "newest",
  });
}

function incomeChart(host, inc, sel) {
  const months = inc.months;
  if (!months.length) return;
  const labels = months.map((m) => new Date(m.month + "-01T00:00:00").toLocaleDateString("en-GB",
    { month: "short", year: months.length > 12 ? "2-digit" : undefined }));
  const money = (v) => (v < 0 ? "−$" : "$") + (Math.abs(v) >= 1000 ? Math.round(Math.abs(v) / 1000) + "k" : Math.abs(v));
  chart(host, (p) => ({
    type: "bar",
    data: { labels, datasets: F().CHANNELS.map((c, i) => ({
      label: c.label, data: months.map((m) => m[c.k]), backgroundColor: p.color(i),
      borderRadius: 3, borderSkipped: "start", maxBarThickness: 28, stack: "s" })) },
    options: {
      interaction: { mode: "index", intersect: false },
      onClick: (e, els) => {
        if (!els.length) return;
        const m = months[els[0].index];
        incomeDrill(inc, sel, { month: m.month, label: labels[els[0].index] });
      },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: {
        title: (it) => `${labels[it[0].dataIndex]} · ${fmt.usd(months[it[0].dataIndex].total)}`,
        label: (it) => `${it.dataset.label}: ${fmt.usd(it.raw)}`,
      } } },
      scales: (() => {
        const a = axes(p, { yTicks: { callback: money } });
        return { x: { ...a.x, stacked: true }, y: { ...a.y, stacked: true } };
      })(),
    },
  }), { height: 220 });
}

function clientTable(inc, sel) {
  const host = el("div", "stack-2");
  let all = false;
  const usdCell = (v) => el("span", v ? null : "muted", fmt.usd(v));
  function fill() {
    clear(host);
    host.append(table({
      wrapClass: "fn-inc-table",
      rows: all ? inc.clients : inc.clients.slice(0, INC_PAGE),
      columns: [
        { key: "name", label: "Client", render: (c) => el("div", "stack-1", [
          el("span", null, c.name),
          c.aka.length ? el("span", "small muted", "also in the ledger as " + c.aka.join(", ")) : null]) },
        { key: "by", label: "Found through", render: (c) => el("div", "stack-1", [
          c.pid ? extLink("https://www.upwork.com/nx/proposals/" + c.pid, foundWords(c), { title: "your proposal on Upwork" })
            : el("span", null, foundWords(c)),
          el("span", "small muted nowrap", "first met " + fmt.date(c.first))]) },
        { key: "up", label: "Upwork", num: true, render: (c) => usdCell(c.freelancer + c.agency) },
        { key: "direct", label: "Directly", num: true, render: (c) => usdCell(c.direct) },
        { key: "usd", label: "Total", num: true, render: (c) => el("button", { class: "linknum", type: "button",
          attrs: { "aria-label": `the payments behind ${fmt.usd2(c.usd)}` },
          on: { click: () => clientPaymentsDrill(c, sel) } }, fmt.usd(c.usd)) },
      ],
    }));
    if (inc.clients.length > INC_PAGE) {
      host.append(el("button", { class: "linktext small", type: "button", on: { click: () => { all = !all; fill(); } } },
        all ? `Show the top ${INC_PAGE}` : `Show all ${inc.clients.length} clients`));
    }
  }
  fill();
  return host;
}

function clientPaymentsDrill(c, sel) {
  openDrill({
    title: `${c.name}: payments`,
    subtitle: (sel.bounds.lo ? `from ${fmt.date(sel.bounds.lo)}` : "every payment") + ` · first met on Upwork ${fmt.date(c.first)}`,
    rows: c.rows, total: c.rows.length, columns: PAY_COLS,
    facets: [{ label: "Paid", allLabel: "All", of: payWhere }],
    amount: (x) => x.usd, time: (x) => x.d, defaultSort: "newest",
  });
}

function incomeDrill(inc, sel, opts) {
  const rows = [];
  inc.clients.forEach((c) => c.rows.forEach((x) => {
    if (opts.ch && x.ch !== opts.ch) return;
    if (opts.month && x.d.slice(0, 7) !== opts.month) return;
    rows.push({ ...x, client: c.name });
  }));
  const what = opts.ch ? F().CHANNELS.find((c) => c.k === opts.ch).label : "Every payment";
  openDrill({
    title: opts.month ? `${opts.label}: money in from Upwork clients` : `${what}: money in from Upwork clients`,
    subtitle: sel.bounds.lo ? `from ${fmt.date(sel.bounds.lo)}` : "every payment on record",
    rows, total: rows.length, wide: true,
    columns: [PAY_COLS[0], { key: "client", label: "Client" }, ...PAY_COLS.slice(1)],
    search: (x) => `${x.client} ${x.as || ""} ${x.desc || ""}`,
    facets: [
      { label: "Paid", allLabel: "All", of: payWhere },
      { label: "Client", allLabel: "All", of: (x) => x.client },
    ],
    amount: (x) => x.usd, time: (x) => x.d, defaultSort: "newest",
  });
}

/* ── 2. what we know (the written reading) ────────────────────────────────── */

const TONE = { good: ["ok", "circle-check"], bad: ["bad", "alert-triangle"], info: ["info", "info"],
               neutral: ["", "info"] };
const STRENGTH = { clear: "clear", lean: "a lean", none: "no difference" };

function insightsBlock(d, state, redraw) {
  const ins = d.insights && d.insights.latest;
  const sec = el("section", "card fn-ins");
  if (!ins) {
    sec.append(el("h3", "fn-h", "What we know"),
      emptyState("No written reading yet. The weekly runner will add one every week.", { icon: "info" }));
    return sec;
  }
  const items = ins.items || [];
  const head = el("div", "fn-hero-head", [
    el("h3", "fn-h", ins.title || "What we know"),
    el("span", "muted small", `written ${fmt.date(ins.created_at)} · ${ins.author}`
      + (ins.window && ins.window.hi ? ` · proposals to ${fmt.date(ins.window.hi)}` : "")),
  ]);
  const grid = el("div", "fn-ins-grid");
  const card = (it) => {
    const [tone, ic] = TONE[it.tone] || TONE.neutral;
    const show = it.show && (it.show.split || it.show.kind)
      ? btn("Show me", () => {
        if (it.show.kind) state.kind = it.show.kind;
        if (it.show.split) { state.dim = it.show.split; state.dim2 = ""; }
        redraw();
        const t = document.getElementById("fn-split");
        if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
      }, { sm: true, variant: "ghost", iconRight: "chevron-right" })
      : null;
    return el("article", "fn-ins-item " + tone, [
      el("div", "fn-ins-top", [
        el("span", "fn-ins-ic " + tone, icon(ic, { size: 15 })),
        el("span", "fn-ins-strength", STRENGTH[it.strength] || ""),
      ]),
      el("h4", null, it.title),
      el("p", null, it.text),
      (it.facts || []).length ? el("dl", "fn-facts", (it.facts || []).map(([k, v]) =>
        el("div", null, [el("dt", null, k), el("dd", null, v)]))) : null,
      show,
    ]);
  };
  const first = items.slice(0, 4), rest = items.slice(4);
  first.forEach((it) => grid.appendChild(card(it)));
  const more = el("div", { class: "fn-ins-grid", style: { display: "none" } }, rest.map(card));
  const toggle = rest.length ? el("button", {
    class: "expander-btn", type: "button", attrs: { "aria-expanded": "false" },
    on: { click: (e) => {
      const open = more.style.display === "none";
      more.style.display = open ? "" : "none";
      e.currentTarget.setAttribute("aria-expanded", String(open));
      e.currentTarget.lastChild.textContent = open ? "Show fewer" : `Show the other ${rest.length}`;
    } },
  }, [icon("chevron-right", { size: 15 }), el("span", null, `Show the other ${rest.length}`)]) : null;
  const side = el("div", "fn-ins-side", [
    (ins.next_tests || []).length ? el("div", "stack-2", [
      el("div", "fn-side-h", "What to try next"),
      el("ol", "fn-tests", (ins.next_tests || []).map((t) => el("li", null, [
        el("b", null, t.title), el("span", "muted", " — " + t.why)]))),
    ]) : null,
    (ins.cautions || []).length ? el("div", "stack-2", [
      el("div", "fn-side-h", "Read with care"),
      el("ul", "fn-cautions", (ins.cautions || []).map((c) => el("li", "muted", c))),
    ]) : null,
  ]);
  sec.append(head, el("p", "fn-summary", ins.summary || ""), el("div", "fn-ins-wrap", [
    el("div", "stack-2", [grid, more, toggle]), side]));
  return sec;
}

/* ── 3. break it down ─────────────────────────────────────────────────────── */

function splitBlock(d, state, sel, tip, redraw) {
  const sec = el("section", { class: "card fn-split", id: "fn-split" });
  const dims = F().DIMS;
  const dim = F().DIM_BY_KEY[state.dim] || dims[0];
  const chips = el("div", "fn-chips", dims.map((x) => el("button", {
    class: "fchip" + (x.k === dim.k ? " on" : ""), type: "button",
    attrs: { "aria-pressed": String(x.k === dim.k), title: x.hint || "" },
    on: { click: () => { state.dim = x.k; if (state.dim2 === x.k) state.dim2 = ""; redraw(); } },
  }, x.label)));
  const then = el("select", { class: "input fn-then", attrs: { "aria-label": "then split by" },
    on: { change: (e) => { state.dim2 = e.target.value; redraw(); } } }, [
    el("option", { value: "" }, "nothing"),
    ...dims.filter((x) => x.k !== dim.k).map((x) => el("option", { value: x.k, selected: x.k === state.dim2 }, x.label)),
  ]);
  sec.append(
    el("div", "fn-hero-head", [
      el("h3", "fn-h", "Break it down"),
      el("span", "muted small", dim.hint || "what changes along the funnel when you split by one thing"),
    ]),
    chips,
    el("div", "fn-then-row", [el("span", "muted small", "then split each group by"), then]),
  );
  const parent = F().stages(sel.rows, state.kind);
  const groups = F().split(sel.rows, dim.k, state.kind);
  const maxSent = Math.max(1, ...groups.map((g) => g.stages[0].n));
  const tbl = el("div", { class: "fn-table", attrs: { role: "table" } });
  tbl.appendChild(splitHeader(state.kind));
  groups.forEach((g) => {
    tbl.appendChild(splitRow(g, parent, maxSent, sel, state, tip, 0));
    if (state.dim2) {
      F().split(g.rows, state.dim2, state.kind).forEach((sub) => {
        tbl.appendChild(splitRow(sub, g.stages, maxSent, sel, state, tip, 1, g.key));
      });
    }
  });
  sec.append(tbl, el("div", "fn-legend", [
    ...(state.kind === "invite" ? ["Accepted", "Hired"] : ["Sent", "Opened", "Wrote back", "Hired"])
      .map((l, i, a) => el("span", "fn-key", [el("i", `fn-sw fn-s${a.length === 2 ? i * 3 : i}`), l])),
    el("span", "grow"),
    el("span", "muted small", "“clear” and “a lean” compare a group with the rest of this selection;"
      + " groups under " + F().FEW + " are too few to tell"),
  ]));
  return sec;
}

function splitHeader(kind) {
  const cols = kind === "invite"
    ? ["", "Accepted", "", "", "Hired", ""]
    : ["", "Sent", "Funnel", "Opened", "Wrote back, of opened", "Hired"];
  return el("div", { class: "fn-tr fn-th", attrs: { role: "row" } },
    cols.map((c) => el("div", { attrs: { role: "columnheader" } }, c)));
}

function rateCell(k, n, sig, onClick, label) {
  const few = n < F().FEW;
  const b = el("button", { class: "fn-rate" + (few ? " few" : ""), type: "button",
    attrs: { "aria-label": label } }, [
    el("b", null, n ? pct(k / n) : "—"),
    el("span", "muted", ` ${fmt.num(k)} of ${fmt.num(n)}`),
    sig ? el("span", "fn-sig " + sig, sig === "clear" ? "clear" : "a lean") : null,
    few && n ? el("span", "fn-sig few", "too few") : null,
  ]);
  b.addEventListener("click", onClick);
  return b;
}

function splitRow(g, parentStages, maxSent, sel, state, tip, depth, parentKey) {
  const st = g.stages;
  const top = parentStages;
  const name = el("div", { class: "fn-td fn-name" + (depth ? " sub" : ""), attrs: { role: "cell" } }, [
    el("span", g.unknown ? "muted" : null, g.key),
  ]);
  const where = depth ? `${parentKey} › ${g.key}` : g.key;
  const sent = el("div", { class: "fn-td num", attrs: { role: "cell" } }, el("button", {
    class: "linknum", type: "button", attrs: { "aria-label": `the ${st[0].n} proposals in ${where}` },
    on: { click: () => drillRows(`${where}: ${plural(st[0].n, "proposal")}`, st[0].rows) },
  }, fmt.num(st[0].n)));
  const cells = [name, sent];
  if (state.kind === "invite") {
    cells.push(el("div", "fn-td"), el("div", "fn-td"));
    cells.push(el("div", { class: "fn-td", attrs: { role: "cell" } },
      rateCell(st[1].n, st[0].n, null, () => drillRows(`${where}: hired`, st[1].rows), `hired in ${where}`)));
    cells.push(el("div", "fn-td"));
  } else {
    cells.push(el("div", { class: "fn-td", attrs: { role: "cell" } }, miniFunnel(st, maxSent, tip, where)));
    const N = top[0].n, K1 = top[1].n, K2 = top[2].n;
    const sig1 = F().signal(st[1].n, st[0].n, K1, N);
    const sig2 = F().signal(st[2].n, st[1].n, K2, top[1].n);
    cells.push(el("div", { class: "fn-td", attrs: { role: "cell" } },
      rateCell(st[1].n, st[0].n, sig1, () => drillRows(`${where}: opened`, st[1].rows), `opened in ${where}`)));
    cells.push(el("div", { class: "fn-td", attrs: { role: "cell" } },
      rateCell(st[2].n, st[1].n, sig2, () => drillRows(`${where}: wrote back`, st[2].rows), `wrote back in ${where}`)));
    cells.push(el("div", { class: "fn-td num", attrs: { role: "cell" } }, st[3].n
      ? el("button", { class: "linknum", type: "button", attrs: { "aria-label": `the hires in ${where}` },
        on: { click: () => drillRows(`${where}: hired`, st[3].rows) } }, fmt.num(st[3].n))
      : el("span", "muted", "0")));
  }
  return el("div", { class: "fn-tr" + (depth ? " sub" : ""), attrs: { role: "row" } }, cells);
}

function miniFunnel(st, maxSent, tip, where) {
  const W = 180, H = 22;
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "fn-mini", tabindex: "0", role: "img",
    "aria-label": st.map((x) => `${x.label} ${x.n}`).join(", ") });
  const hs = [20, 15, 10, 6];                   // each step a thinner bar inside the one before
  st.forEach((x, i) => {
    const w = Math.max(x.n ? 2 : 0, W * (x.n / maxSent));
    const h = hs[Math.min(i, hs.length - 1)];
    s.appendChild(svg("rect", { x: 0, y: (H - h) / 2, width: w, height: h, rx: 3, class: `fn-mbar fn-s${i}` }));
  });
  tip.bind(s, () => tipBody(where, st.map((x, i) => [fmt.num(x.n),
    x.label.toLowerCase() + (i ? ` (${pct(x.ofPrev)} of the step before)` : "")])));
  return s;
}

/* ── 4. over time ─────────────────────────────────────────────────────────── */

function timeBlock(d, state, sel) {
  if (state.kind === "invite") return null;
  const sec = el("section", "card fn-time");
  const months = F().byMonth(sel.rows);
  sec.append(el("div", "fn-hero-head", [
    el("h3", "fn-h", "Month by month"),
    el("span", "muted small", "click a month's column for its proposals"),
  ]));
  const grid = el("div", "fn-time-grid");
  const a = el("div"), b = el("div");
  grid.append(el("div", "stack-2", [el("div", "fn-side-h", "Proposals sent, and how many were opened"), a]),
    el("div", "stack-2", [el("div", "fn-side-h", "Opened, and wrote back once opened"), b,
      el("div", "muted small", `a month with under ${MIN_MONTH} proposals, or under ${MIN_OPENED} opened, is left out of its line`)]));
  sec.appendChild(grid);
  const labels = months.map((m) => {
    const dt = new Date(m.month + "-01T00:00:00");
    return dt.toLocaleDateString("en-GB", { month: "short", year: months.length > 12 ? "2-digit" : undefined });
  });
  chart(a, (p) => ({
    type: "bar",
    data: { labels, datasets: [
      { label: "Opened", data: months.map((m) => m.opened), backgroundColor: p.color(1),
        borderRadius: 4, borderSkipped: "start", maxBarThickness: 24, stack: "s" },
      { label: "Not opened", data: months.map((m) => m.sent - m.opened), backgroundColor: p.soft(1),
        borderRadius: 4, borderSkipped: "start", maxBarThickness: 24, stack: "s" },
    ] },
    options: {
      onClick: (e, els) => {
        if (!els.length) return;
        const m = months[els[0].index];
        drillRows(`${labels[els[0].index]}: ${plural(m.sent, "proposal")}`, m.rows);
      },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: {
        title: (it) => labels[it[0].dataIndex],
        label: (it) => {
          const m = months[it.dataIndex];
          return it.datasetIndex === 0 ? `${m.opened} opened of ${m.sent}` : `${m.sent - m.opened} not opened`;
        },
        afterBody: (it) => {
          const m = months[it[0].dataIndex];
          return [`${m.replied} wrote back · ${m.hired} hired`];
        },
      } } },
      scales: { ...axes(p), x: { ...axes(p).x, stacked: true }, y: { ...axes(p).y, stacked: true } },
    },
  }), { height: 220 });
  chart(b, (p) => ({
    type: "line",
    data: { labels, datasets: [
      { label: "Opened", data: months.map((m) => (m.sent >= MIN_MONTH ? Math.round(100 * m.opened / m.sent) : null)),
        borderColor: p.color(1), backgroundColor: p.color(1), borderWidth: 2, pointRadius: 4, tension: 0.25 },
      { label: "Wrote back, of opened", data: months.map((m) => (m.opened >= MIN_OPENED ? Math.round(100 * m.replied / m.opened) : null)),
        borderColor: p.color(2), backgroundColor: p.color(2), borderWidth: 2, pointRadius: 4, tension: 0.25 },
    ] },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: {
        label: (it) => {
          const m = months[it.dataIndex];
          return it.datasetIndex === 0 ? `Opened ${it.formattedValue}% (${m.opened} of ${m.sent})`
            : `Wrote back ${it.formattedValue}% of opened (${m.replied} of ${m.opened})`;
        },
      } } },
      scales: { ...axes(p, { y: { max: 100, ticks: { callback: (v) => v + "%" } } }) },
    },
  }), { height: 220 });
  return sec;
}

/* ── 5. the first lines ───────────────────────────────────────────────────── */

const LINE_SETS = [
  ["answered", "Opened, and they wrote back", (r) => r.rep === 1],
  ["passed", "Opened, no reply", (r) => r.op === 1 && r.rep !== 1],
  ["unopened", "Never opened", (r) => r.op === 0 && r.rep !== 1],
];

function linesBlock(d, state, sel, redraw) {
  if (state.kind === "invite") return null;
  const sec = el("section", "card fn-lines");
  const sets = LINE_SETS.map(([k, label, f]) => ({ k, label, rows: sel.rows.filter(f) }));
  const cur = sets.find((s) => s.k === state.lines) || sets[0];
  const q = el("input", { class: "input fn-search", type: "search", placeholder: "Search the first lines…",
    attrs: { "aria-label": "search the first lines" } });
  const list = el("div", "fn-line-list");
  let shown = LINES_PAGE;
  function fill() {
    const needle = (q.value || "").trim().toLowerCase();
    const rows = cur.rows.filter((r) => !needle || `${r.fl || ""} ${r.title || ""}`.toLowerCase().includes(needle));
    clear(list);
    rows.slice(0, shown).forEach((r) => list.appendChild(lineRow(r)));
    if (!rows.length) list.appendChild(el("div", "muted small", "No first lines match."));
    if (rows.length > shown) {
      list.appendChild(btn(`Show ${Math.min(LINES_PAGE, rows.length - shown)} more of ${rows.length - shown}`,
        () => { shown += LINES_PAGE; fill(); }, { sm: true, variant: "ghost" }));
    }
  }
  q.addEventListener("input", () => { shown = LINES_PAGE; fill(); });
  sec.append(
    el("div", "fn-hero-head", [
      el("h3", "fn-h", "What the client saw first"),
      el("span", "muted small", "the opening as the proposal list shows it, before anyone clicks"),
    ]),
    el("div", "fn-filters", [
      segmented(sets.map((s) => [s.k, `${s.label} · ${fmt.num(s.rows.length)}`]), cur.k,
        (v) => { state.lines = v; redraw(); }, "Which first lines"),
      el("span", "grow"), q,
    ]),
    list,
  );
  fill();
  return sec;
}

function lineRow(r) {
  const tags = [];
  if (r.boost === 0) tags.push(pill("not boosted", "outline"));
  if (r.bo === "outbid, bid returned") tags.push(pill("boost outbid", "warn"));
  if (r.loom === 1) tags.push(pill("Loom", "outline"));
  if (r.tool === 0) tags.push(pill("no tool in title", "outline"));
  if (r.hired === 1) tags.push(pill("hired", "ok"));
  return el("div", "fn-line", [
    el("div", "fn-line-meta", [dateCell(r.sent),
      r.url ? extLink(r.url, r.title || r.id, { class: "fn-line-job" }) : el("span", "fn-line-job", r.title || r.id),
      el("span", "nowrap muted", bidText(r)), ...tags]),
    el("blockquote", "fn-quote", r.fl || "—"),
  ]);
}

/* ── 6. invitations ───────────────────────────────────────────────────────── */

function invitesBlock(d, tip) {
  const rows = (d.invites || []).filter((r) => r.received != null);
  if (!rows.length) return null;
  const sec = el("section", "card fn-inv");
  const max = Math.max(1, ...rows.map((r) => r.received));
  const W = 1000, H = 150, bw = Math.min(18, (W - 40) / rows.length - 10);
  const s = svg("svg", { viewBox: `0 0 ${W} ${H + 24}`, width: W, height: H + 24, class: "fn-svg", role: "img",
    "aria-label": "invitations received per month" });
  rows.forEach((r, i) => {
    const x = 20 + i * ((W - 40) / rows.length);
    const hR = H * (r.received / max), hA = H * (r.accepted / max), hH = H * (r.hired / max);
    const g = svg("g", { class: "fn-inv-col", tabindex: "0", role: "img",
      "aria-label": `${r.month}: ${r.received} received, ${r.accepted} accepted, ${r.hired} hired` });
    g.appendChild(svg("rect", { x: x - 4, y: 0, width: bw + 8, height: H + 24, class: "fn-hit" }));
    g.appendChild(svg("rect", { x, y: H - hR, width: bw, height: hR, rx: 3, class: "fn-mbar fn-s0" }));
    if (hA) g.appendChild(svg("rect", { x, y: H - hA, width: bw, height: hA, rx: 3, class: "fn-mbar fn-s2" }));
    if (hH) g.appendChild(svg("rect", { x, y: H - hH, width: bw, height: hH, rx: 3, class: "fn-mbar fn-s3" }));
    if (i % 2 === 0 || rows.length < 13) {
      const lbl = new Date(r.month + "-01T00:00:00").toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
      g.appendChild(svgText(x + bw / 2, H + 16, lbl, "fn-axis", "middle"));
    }
    s.appendChild(g);
    tip.bind(g, () => tipBody(new Date(r.month + "-01T00:00:00").toLocaleDateString("en-GB",
      { month: "long", year: "numeric" }), [
      [fmt.num(r.received), "invitations received"],
      [fmt.num(r.accepted), "accepted"],
      [fmt.num(r.hired), "hired"],
    ]));
  });
  sec.append(
    el("div", "fn-hero-head", [
      el("h3", "fn-h", "Invitations, month by month"),
      el("span", "muted small", "clients who found your profile; how many arrive does not depend on any letter"),
    ]),
    s,
    el("div", "fn-legend", [
      el("span", "fn-key", [el("i", "fn-sw fn-s0"), "Received"]),
      el("span", "fn-key", [el("i", "fn-sw fn-s2"), "Accepted"]),
      el("span", "fn-key", [el("i", "fn-sw fn-s3"), "Hired"]),
      el("span", "grow"),
      el("span", "muted small", "received = your “Archived interviews” list + the ones you accepted; read 8 Oct"),
    ]),
  );
  return sec;
}

/* ── provenance ───────────────────────────────────────────────────────────── */

function footBlock(d) {
  const f = d.fresh || {};
  return el("p", "muted small fn-foot", [
    "Where this comes from: every proposal's own Upwork record (read 8 Oct, read-only), the date each",
    " client first opened it from the proposal's page in your browser (last read ", fmt.dateTimeShort(f.views_at),
    " UTC), the job records, and the ledger for money (every rail, one client however the ledger spells it).",
    " A first open is permanent; a proposal answered",
    " straight from the list counts as opened. Nothing on this page is sent anywhere.",
  ]);
}
