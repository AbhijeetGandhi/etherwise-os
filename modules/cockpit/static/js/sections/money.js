// ═══════════════════════════════════════════════════════════════════════════
// sections/money.js — the flagship. Every number on this page is clickable and
// resolves to the exact transactions behind it, which sum to it TO THE CENT.
//
// WHAT IS ON THIS PAGE, AND WHY (ported from v1 renderMoney + brief §4):
//
//   · doctor gate          finance-doctor RED renders the red panel and NOTHING
//                          else. No finance number may appear on RED, ever.
//   · parity check         a LIVE client-side re-sum of the ledger rows against
//                          the server's audited pnl. If they disagree the page
//                          says so and names bin/finance-pnl as the truth.
//   · source freshness     GREEN means the ledger is internally CONSISTENT. It
//                          says nothing about whether anything was ADDED. A dead
//                          sync leaves these figures self-consistent and stale.
//   · provisional overlay  NEW (§4): payments seen in email but not yet in the
//                          ledger. Written so a client does not look delinquent
//                          when the ledger is merely behind. ALWAYS rendered —
//                          "nothing waiting" is a finding, not an empty state.
//   · range bar            7 presets + custom months, persisted. FLOWS scope to
//                          the range; CASH never does (it is point-in-time).
//   · import               upload → auto dry-run → an explicit second click to
//                          commit → the doctor verdict after the write.
//   · KPIs                 the four from v1, plus the four the server has always
//                          computed and no screen ever showed: runway, monthly
//                          operating profit, operating costs, owner draws.
//   · revenue by month     Chart.js bars with the monthly target drawn on.
//   · P&L by month         every cell drills.
//   · waterfall            owner draws BELOW the line — equity, not a cost.
//   · cash                 point-in-time, with the pinned FX rate stated.
//   · explorer             filters run on the SERVER (see below).
//
// WHERE THE ROWS COME FROM (this is the §4 change, and it is deliberate):
//   v1 pulled the entire 3,700-row ledger once and did every filter, drill and
//   subtotal in the browser. Here:
//     · every DRILL and the EXPLORER re-query /api/money/transactions with the
//       server-side filters (bucket, month, source, q), so what you see is the
//       server's own selection and the server's own integer-minor sum;
//     · ONE aggregate read remains — the economic buckets only
//       (?bucket=revenue,refund,platform_fee,tax,payroll,opex,owner_draw) —
//       because no endpoint returns the month × bucket matrix the P&L table and
//       the waterfall need, and because the live parity check has to re-sum real
//       rows to be worth anything. It pulls nothing it does not aggregate.
//   A multi-month range cannot be expressed in the server's single `month=`
//   filter, so a ranged drill narrows on the row's SERVER-ASSIGNED month (`m`,
//   which already honours the pnl_month accrual override). The figure above it
//   is narrowed by the identical predicate, so the rows can never fail to sum
//   to the figure they came from.
//
// TWO RULES THIS FILE OBEYS LITERALLY:
//   `—` = not measured · `0` = a real zero, and it says why.
//   Every figure drills, and the drill states the true total.
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, clear, fmt, dash, isUnmeasured, heading, card, kpi, pill, chip, btn,
  emptyState, notice, expander, toast, qstr,
} from "../ui.js";
import { openDrill, cols } from "../drill.js";
import { chart, axes } from "../charts.js";
import { bootToken } from "../app.js";           // raw-body upload needs the token

export const meta = { title: "Money", hint: "every figure explains itself, to the cent" };

const FILTERS_KEY = "ew_cockpit_money_filters";

const BLABEL = {
  revenue: "Revenue", refund: "Refunds", platform_fee: "Platform fees",
  tax: "Taxes", payroll: "Payroll", opex: "Operating expenses",
  owner_draw: "Owner draws", transfer: "Transfers", sweep: "Sweeps",
  cc_settlement: "Card settlement", uncategorized: "Uncategorized",
};

const OPERATING_COSTS = ["platform_fee", "tax", "payroll", "opex"];
const OPERATING_ALL = ["revenue", "refund", ...OPERATING_COSTS];
// the aggregate slice: the economic buckets, and nothing else. Transfers,
// sweeps and card settlements are money moving between our own accounts.
const ECON = [...OPERATING_ALL, "owner_draw"];

/* the six statement kinds the audited importers accept */
const KINDS = [
  ["idfc", "Bank statement", "IDFC .xlsx", ".xlsx"],
  ["wise", "Wise", "account .csv", ".csv"],
  ["upwork-personal", "Upwork personal", "full-history .csv", ".csv"],
  ["upwork-agency", "Upwork agency", "full-history .csv", ".csv"],
  ["card", "Credit card", "IDFC Mayura .pdf", ".pdf"],
  ["stripe", "Stripe", "balance_history / Payments .csv", ".csv"],
];

const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100;
const sumU = (rows) => round2(rows.reduce((s, r) => s + (Number(r.u) || 0), 0));

/**
 * The §0 rule-1 pair for a KPI tile: the formatted value, and whether it was
 * measured at all. ui.js owns what "not measured" LOOKS like (kpi's
 * `unmeasured` flag) — this file only decides which figures are missing.
 */
const kv = (v, formatter) => ({
  value: dash(v, formatter), unmeasured: isUnmeasured(v),
});

/**
 * Who/what is behind a row — vendor for card/POS spends, payee for bank
 * transfers, client for earnings. Presentational only: it groups the drill's
 * "where it went" strip and never touches a sum.
 */
export function whoOf(x) {
  const d = (x.d || "").toUpperCase();
  let m = d.match(/^POS(?:-VISA)?\/([^/]+)/);
  if (m) return m[1].replace(/\s+(CYBS|SI)\b.*/, "").trim().slice(0, 26);
  m = d.match(/^(?:NEFT|RTGS)\/[^/]+\/([^/]+)\//);
  if (m) return m[1].trim().slice(0, 26);
  m = d.match(/^IMPS[^/]*\/[^/]*\/[^/]*\/([^/]+)/);
  if (m) return m[1].trim().slice(0, 26);
  if (d.startsWith("UPI")) return "UPI payments";
  if (d.includes("EMI")) return "Loan EMI";
  m = (x.d || "").match(/^\(\d+\)\s*([^-]+?)\s*-/);            // Upwork hourly: client
  if (m) return m[1].trim().slice(0, 26);
  m = (x.d || "").match(/Received money from ([^.]+?) with/i); // Wise receipt: sender
  if (m) return m[1].trim().slice(0, 26);
  if (x.s === "Upwork" || x.s === "Upwork-CSV") return x.t || "Upwork";
  const vendor = (x.d || "").split(",")[0].trim();             // card rows: "VENDOR, CITY"
  if (x.s === "Card" && vendor) return vendor.slice(0, 26);
  return (x.s || "?") + (x.t ? " · " + x.t : "");
}

/* ── the section ──────────────────────────────────────────────────────────── */

export async function render(root, ctx) {
  const d = await ctx.api("/api/money");

  // ── DOCTOR GATE: on RED this panel is the entire page ──────────────────────
  if (d.blocked) {
    root.append(
      heading("Money", "— gated"),
      el("div", "blocked", [
        el("h3", null, "Finance blocked — finance-doctor is RED"),
        el("div", null, d.note || "No figures are shown until every invariant is GREEN."),
        (d.reds || []).length
          ? el("ul", { style: { margin: "10px 0 0", paddingLeft: "18px" } },
              (d.reds || []).map((x) => el("li", "small", x)))
          : el("div", "small muted", "The doctor did not name the failing invariant."),
        el("div", { class: "small muted", style: { marginTop: "12px" } },
          "Run bin/finance-doctor for the detail. Nothing on this page is"
          + " rendered from a partially-reconciled ledger — a plausible wrong"
          + " number is worse than no number."),
      ]),
    );
    return;
  }

  const r = d.revenue || {}, p = d.pnl || {}, cash = d.cash || {};
  const srvBuckets = d.buckets || {};
  const byMonth = r.by_month || [];
  const thisM = r.month, lastM = r.last_month;

  /* ── transaction reads: one cache, every read server-filtered ───────────── */
  const txCache = new Map();
  async function loadTx(filters) {
    const q = qstr(filters || {});
    if (!txCache.has(q)) txCache.set(q, ctx.api("/api/money/transactions" + (q ? "?" + q : "")));
    return txCache.get(q);
  }

  const econ = await loadTx({ bucket: ECON.join(",") });
  const rows = econ.rows || [];

  /* ── range engine: ONE scope state. Flows scope; cash does not. ─────────── */
  const mAdd = (ym, k) => {                        // "2026-04" + k months
    let [y, m] = String(ym || "").split("-").map(Number);
    if (!y || !m) return ym;
    m += k;
    while (m < 1) { m += 12; y -= 1; }
    while (m > 12) { m -= 12; y += 1; }
    return `${y}-${String(m).padStart(2, "0")}`;
  };
  const PRESETS = [
    ["all", "All time", () => [null, null]],
    ["this", "This month", () => [thisM, thisM]],
    ["last", "Last month", () => [lastM, lastM]],
    ["l3", "Last 3 mo", () => [mAdd(thisM, -2), thisM]],
    ["t12", "Trailing 12", () => [mAdd(thisM, -11), thisM]],
    ["ytd", "YTD", () => [String(thisM || "").slice(0, 4) + "-01", thisM]],
    ["custom", "Custom", null],
  ];
  // READ IT EVERY TIME, never once. `explorer()` is rebuilt on every range
  // change and used to restore its filters from a snapshot taken at page load,
  // so picking a preset silently reset the bucket/source/search you had chosen
  // — and then `load()` persisted the reset values over your real preference.
  const readSaved = () => {
    try { return JSON.parse(localStorage.getItem(FILTERS_KEY) || "{}"); }
    catch (e) { return {}; }
  };
  const saved = readSaved();
  let range = { preset: saved.preset || "all", from: saved.from || null, to: saved.to || null };
  if (!PRESETS.some(([k]) => k === range.preset)) range = { preset: "all", from: null, to: null };

  const bounds = () => {
    const pr = PRESETS.find(([k]) => k === range.preset);
    return pr && pr[2] ? pr[2]() : [range.from, range.to];
  };
  const isAll = () => { const [f, t] = bounds(); return !f && !t; };
  const oneMonth = () => { const [f, t] = bounds(); return f && f === t ? f : null; };
  const inRange = (m) => {
    const [f, t] = bounds();
    return (!f || m >= f) && (!t || m <= t);
  };
  const rangeLabel = () => {
    if (isAll()) return "all time";
    const [f, t] = bounds();
    return f === t ? f : `${f || "…"} → ${t || "…"}`;
  };
  const scopedRows = () => rows.filter((x) => inRange(x.m));
  const sumB = (bs, rs) => round2(rs.reduce((s, x) => (bs.includes(x.b) ? s + x.u : s), 0));

  function persist(extra) {
    localStorage.setItem(FILTERS_KEY, JSON.stringify(Object.assign(
      readSaved(), { preset: range.preset, from: range.from, to: range.to },
      extra || {})));
  }

  /* the trailing-12 window the server's runway / monthly-profit figures are
     computed over: `pnl.compute(rows where creation_dt >= max(creation_dt) −
     365 days)` in cockpit/data.money. Derived from the SAME `as_of` the server
     reports so the two can never drift apart. */
  const T12_HI = /^\d{4}-\d{2}-\d{2}$/.test(String(d.as_of || "")) ? d.as_of : null;
  const T12_LO = T12_HI
    ? new Date(Date.parse(T12_HI + "T00:00:00Z") - 365 * 864e5)
      .toISOString().slice(0, 10)
    : null;

  /* ── the live parity check (all-time, to the cent) ──────────────────────── */
  const cliNet = sumB(["revenue", "refund"], rows);
  const cliOp = sumB(OPERATING_ALL, rows);
  const parityOk = Math.abs(cliNet - (p.net_revenue_usd || 0)) < 0.005
    && Math.abs(cliOp - (p.operating_profit_usd || 0)) < 0.005;
  if (!parityOk) console.warn("MONEY PARITY MISMATCH: client row sums != server pnl");

  /* ── the shared drill: one path for every figure on the page ────────────── */
  /**
   * drillTx({title, buckets, month, q, source, subtitle, ranged, sortAmount,
   *          since, until, note})
   *
   * `ranged` applies the current range on the server-assigned month, which is
   * the same predicate the figure above it was computed with.
   *
   * `since`/`until` bound the rows by PAYMENT DATE (`creation_dt`, the row's
   * `dt`). That is a different key from `ranged`, deliberately: the server's
   * trailing-12 figures (`monthly_operating_profit_usd`, `runway_months`) are
   * computed as `pnl.compute(rows where creation_dt >= as_of − 365d)`, on the
   * payment date and NOT on the pnl_month accrual override. A drill titled
   * "trailing 12 months" that passed no bound at all returned the whole ledger
   * — 2,467 rows and $176,046 against a figure whose own arithmetic is
   * $126,200 ÷ 12. The title was simply false. This endpoint returns every
   * matching row (it has no cap), so filtering here is exact.
   */
  async function drillTx(spec) {
    const filters = {};
    if (spec.buckets) filters.bucket = spec.buckets.join(",");
    if (spec.source) filters.source = spec.source;
    if (spec.q) filters.q = spec.q;
    const fixedMonth = spec.month || (spec.ranged ? oneMonth() : null);
    if (fixedMonth) filters.month = fixedMonth;

    let res;
    try {
      res = await loadTx(filters);
    } catch (e) {
      toast("Could not read the rows behind that figure: " + String(e.message || e), true);
      return;
    }
    let out = res.rows || [];
    let narrowed = false;
    let dated = false;
    if (spec.since || spec.until) {
      const before = out.length;
      out = out.filter((x) => (!spec.since || (x.dt || "") >= spec.since)
        && (!spec.until || (x.dt || "") <= spec.until));
      dated = out.length !== before;
    }
    if (spec.ranged && !fixedMonth && !isAll()) {
      const before = out.length;
      out = out.filter((x) => inRange(x.m));
      narrowed = out.length !== before;
    }

    // the server sums integer minor units; the browser sums 2dp floats. They
    // agree to the cent or the drill says which one to trust.
    const cli = sumU(out);
    const notes = [];
    if (spec.note) notes.push(spec.note);
    if (!narrowed && !dated && !spec.q
        && Math.abs(cli - (res.sum_usd || 0)) >= 0.005) {
      notes.push(`The server totals these rows at ${fmt.usd2(res.sum_usd)}`
        + ` (integer minor units) — trust that figure, not the row sum above.`);
    }
    if (dated) {
      notes.push(`Bounded to ${spec.since || "the start of the ledger"} →`
        + ` ${spec.until || "the end of the ledger"} on each row's PAYMENT date`
        + ` (creation_dt), which is the same key the server bounds this figure`
        + ` on — ${fmt.num(res.count)} rows matched the bucket filter over all`
        + ` time, ${fmt.num(out.length)} of them fall inside the window.`);
    }
    if (narrowed) {
      notes.push(`Narrowed to ${rangeLabel()} on each row's attributed month`
        + ` (the pnl_month accrual override, as the server assigned it) —`
        + ` ${fmt.num(res.count)} rows matched the filter before the range.`);
    }

    openDrill({
      title: spec.title,
      subtitle: spec.subtitle,
      rows: out,
      total: out.length,                 // no cap on this endpoint: count == rows
      columns: cols.txn(BLABEL),
      amount: (x) => x.u,
      time: (x) => x.dt,
      search: (x) => `${x.d || ""} ${x.t || ""} ${x.s || ""}`,
      facets: [
        { label: "bucket", allLabel: "All buckets", of: (x) => x.b,
          labelOf: (k) => BLABEL[k] || k },
        { label: "who", allLabel: "Everyone", of: whoOf },
      ],
      defaultSort: spec.sortAmount ? "biggest" : undefined,
      note: notes.length ? notes.join(" ") : null,
      emptyText: "No transactions behind this figure.",
    });
  }

  /* ── static chrome ──────────────────────────────────────────────────────── */

  // declared before the builders run: rangeBar() writes into them, and a `let`
  // below its own call site would be in the temporal dead zone.
  let segHost, fromIn, toIn, customWrap;
  let revChart = null;

  const head = heading("Money",
    `— every figure is clickable and matches bin/finance-pnl · ledger through ${d.as_of || "?"}`);
  if (!parityOk) head.appendChild(pill("parity mismatch", "fail"));
  root.appendChild(head);

  if (!parityOk) {
    root.appendChild(notice("bad",
      `The ledger rows no longer re-sum to the audited P&L: this browser makes`
      + ` net revenue ${fmt.usd2(cliNet)} and operating profit ${fmt.usd2(cliOp)},`
      + ` the server reports ${fmt.usd2(p.net_revenue_usd)} and`
      + ` ${fmt.usd2(p.operating_profit_usd)}. Trust bin/finance-pnl and treat`
      + ` everything below as unverified until they agree.`));
  }

  root.appendChild(sourceFreshness(d.source_health));
  root.appendChild(provisionalPanel(d.provisional));
  root.appendChild(rangeBar());
  root.appendChild(importCard(ctx));

  const body = el("div", { class: "stack", style: { gap: "24px" } });
  root.appendChild(body);

  /* ── the range bar ──────────────────────────────────────────────────────── */
  function rangeBar() {
    segHost = el("div", "segmented");
    fromIn = el("input", { type: "month", attrs: { "aria-label": "range from month" } });
    toIn = el("input", { type: "month", attrs: { "aria-label": "range to month" } });
    customWrap = el("div", { class: "hstack-tight", style: { display: "none" } },
      [fromIn, el("span", "muted small", "→"), toIn]);
    fromIn.addEventListener("change", () => {
      range.preset = "custom"; range.from = fromIn.value || null; rerender();
    });
    toIn.addEventListener("change", () => {
      range.preset = "custom"; range.to = toIn.value || null; rerender();
    });
    return el("div", "hstack", [
      segHost, customWrap,
      el("span", "muted small", "flows are scoped to this range; cash is not"),
    ]);
  }

  function drawSeg() {
    clear(segHost);
    PRESETS.forEach(([key, label]) => {
      const b = el("button", {
        class: "segbtn" + (range.preset === key ? " on" : ""), type: "button",
        attrs: { "aria-pressed": String(range.preset === key) },
        on: { click: () => {
          range.preset = key;
          if (key === "custom") {
            range.from = fromIn.value || range.from || lastM;
            range.to = toIn.value || range.to || thisM;
          }
          rerender();
        } },
      }, label);
      segHost.appendChild(b);
    });
    customWrap.style.display = range.preset === "custom" ? "flex" : "none";
    fromIn.value = range.from || "";
    toIn.value = range.to || "";
  }

  /* ── the body, rebuilt whenever the range changes ───────────────────────── */

  function rerender() {
    // MATERIALISE the preset's window back into `range` before anything reads
    // it. bounds() was the only place that knew what "Last month" meant; the
    // month pickers read range.from/range.to directly, so after clicking "Last
    // month" the KPIs measured 2026-07 while both pickers still showed 2026-06
    // — and switching to Custom then adopted the pickers' stale window and
    // quietly applied the wrong one. For "custom" this is a no-op (bounds()
    // returns range.from/range.to unchanged), so it cannot fight the user.
    const [bf, bt] = bounds();
    range.from = bf || null;
    range.to = bt || null;
    persist();
    drawSeg();
    // destroy() cancels a build that has not happened yet as well as one that
    // has: charts.js keeps every live handle for the theme flip, and an orphan
    // whose canvas has been cleared out of the DOM would refresh forever.
    if (revChart) { try { revChart.destroy(); } catch (e) { /* already gone */ } }
    revChart = null;
    clear(body);

    const scoped = scopedRows();
    const rl = rangeLabel();
    // All-time anchors on the SERVER's audited figure; any narrower range is a
    // row sum. A bucket the server omits has no rows, which is a true zero — but
    // a headline the server did not compute stays null, so it renders "—".
    const bucketSum = (b) => (isAll() ? round2(srvBuckets[b] || 0) : sumB([b], scoped));
    const anchor = (v, rangeFn) => (isAll()
      ? (v == null ? null : round2(v))
      : rangeFn());
    const netRev = anchor(p.net_revenue_usd, () => sumB(["revenue", "refund"], scoped));
    const opProfit = anchor(p.operating_profit_usd, () => sumB(OPERATING_ALL, scoped));

    body.append(
      kpiRowA(netRev, opProfit, rl),
      heading("Runway and cost base",
        "— the trailing-12 rate: what comes in, what running the business costs,"
        + " and what you take out", "h3"),
      kpiRowB(bucketSum, rl),
      kpiRowC(),
      heading("Revenue by month", "— 13 months · click a bar for its transactions", "h3"),
      revenueChart(),
      heading("P&L by month", `— ${rl} · click any cell for its transactions`, "h3"),
      pnlByMonth(scoped, rl),
      heading("Profit & loss", `— ${rl} · click any line for its transactions`, "h3"),
      waterfall(bucketSum, opProfit, rl),
      heading("Cash position", "— point-in-time balances, never scoped by the range", "h3"),
      cashCard(),
      heading("Transaction explorer", "— every reconciled transaction, filtered on the server", "h3"),
      explorer(),
      provenanceFooter(scoped),
    );
  }

  /* ── KPI row A: the four from v1 ────────────────────────────────────────── */
  function kpiRowA(netRev, opProfit, rl) {
    const g = el("div", "grid-4");

    g.appendChild(kpi({
      label: "Cash position",
      ...kv(cash.value_usd, fmt.usd),
      sub: (cash.note ? cash.note + " · " : "") + "point-in-time, not scoped by the range",
      onClick: drillCash,
    }));

    g.appendChild(kpi({
      label: "Net revenue",
      ...kv(netRev, fmt.usd),
      sub: `${rl} · revenue + refunds`,
      onClick: () => drillTx({
        title: `Net revenue — ${rl}`, buckets: ["revenue", "refund"], ranged: true,
        subtitle: "revenue plus refunds; the P&L's top line",
      }),
    }));

    g.appendChild(kpi({
      label: "Operating profit",
      ...kv(opProfit, fmt.usd),
      sub: `${rl} · after platform fees, taxes, payroll and opex`,
      onClick: () => drillTx({
        title: `Operating profit — ${rl}`, buckets: OPERATING_ALL, ranged: true,
        subtitle: "every operating row; owner draws are equity and sit below the line",
      }),
    }));

    // MTD vs the SAME DAY last month — the only fair comparison, and the one
    // figure the server computed that v1 never rendered (§4).
    const mtd = r.month_usd, lastMtd = r.last_month_mtd_usd;
    const cmp = (mtd != null && lastMtd != null) ? round2(mtd - lastMtd) : null;
    const subBits = [];
    if (cmp != null) {
      subBits.push(el("span", cmp >= 0 ? "pos" : "neg",
        `${cmp >= 0 ? "▲" : "▼"} ${fmt.usd(Math.abs(cmp))}`));
      subBits.push(` vs ${fmt.usd(lastMtd)} by this day last month`);
    } else {
      subBits.push("same-day-last-month comparison not measured");
    }
    subBits.push(el("div", null, [
      `${dash(r.last_month_usd, fmt.usd)} was ${lastM || "last month"} in full · `,
      r.pct_to_target != null
        ? `${fmt.pct(r.pct_to_target, 1)} of the ${fmt.usd(r.target_usd)} target`
        : "no target measured",
    ]));
    g.appendChild(kpi({
      label: `This month (${thisM || "—"})`,
      ...kv(mtd, fmt.usd),
      sub: el("div", null, subBits),
      onClick: () => drillTx({
        title: `Revenue — ${thisM}`, buckets: ["revenue"], month: thisM,
        subtitle: "month-to-date, on the server's attributed month",
      }),
    }));
    return g;
  }

  /**
   * The drill spec shared by the two trailing-12 figures (runway and the
   * monthly operating rate). Both are computed by the server over the SAME
   * window, so they get the same predicate here, stated in the title — a title
   * that says "trailing 12 months" over an unbounded read is a lie in the one
   * place the reader has no way to check.
   */
  function t12Spec(title, subtitle) {
    if (!T12_LO) {
      return {
        title: `${title} (window not measured)`, buckets: OPERATING_ALL,
        subtitle,
        note: "The ledger reported no usable as_of date, so the trailing-12"
          + " window could not be reconstructed and these rows are ALL TIME."
          + " They are not the rows the figure above was computed from — treat"
          + " the totals below as the whole ledger, not the last twelve months.",
      };
    }
    return {
      title: `${title} · ${T12_LO} → ${T12_HI}`,
      buckets: OPERATING_ALL, since: T12_LO, until: T12_HI, subtitle,
    };
  }

  /* ── KPI row B: brief §4 — computed forever, never once displayed ───────── */
  function kpiRowB(bucketSum, rl) {
    const g = el("div", "grid-4");
    const monthlyOp = p.monthly_operating_profit_usd;
    const burning = monthlyOp != null && monthlyOp < 0;

    // RUNWAY. null does not mean "not measured" here — it means the trailing
    // twelve months made money, so there is no burn to exhaust. Saying that is
    // the whole point; a bare em dash would read as a broken figure.
    append(g, kpi({
      label: "Runway",
      value: p.runway_months != null
        ? el("span", null, [fmt.num(p.runway_months), el("small", null, " months")])
        : null,
      unmeasured: p.runway_months == null,
      sub: p.runway_months != null
        ? `${dash(cash.value_usd, fmt.usd)} of cash at ${fmt.usd(Math.abs(monthlyOp))}`
          + ` burned per month (trailing 12)`
        : `not applicable — the trailing 12 months average`
          + ` ${dash(monthlyOp, fmt.usd)} of operating profit per month, so there`
          + ` is no burn rate to run out of. Runway only exists while losing money.`,
      warn: burning && p.runway_months != null && p.runway_months < 6
        ? `Under six months of runway.` : null,
      onClick: () => drillTx(t12Spec(
        "Trailing 12 months — every operating row",
        "the same rows the runway maths averages over")),
    }));

    g.appendChild(kpi({
      label: "Monthly operating profit",
      ...kv(monthlyOp, fmt.usd),
      sub: `trailing-12 average, the rate the runway is computed at`
        + (T12_LO ? ` · ${T12_LO} → ${T12_HI}` : ""),
      onClick: () => drillTx(t12Spec(
        "Operating rows — trailing 12 months",
        "these rows are the trailing-12 operating profit; the figure above is"
        + " that total divided by twelve")),
    }));

    const costs = isAll() ? p.operating_costs_usd : sumB(OPERATING_COSTS, scopedRows());
    g.appendChild(kpi({
      label: "Operating costs",
      ...kv(costs, fmt.usd),
      sub: `${rl} · platform fees + taxes + payroll + opex`
        + (isAll() ? "" : ` · ${dash(p.operating_costs_usd, fmt.usd)} all time`),
      onClick: () => drillTx({
        title: `Operating costs — ${rl}`, buckets: OPERATING_COSTS, ranged: true,
        sortAmount: true, subtitle: "biggest outflow first",
      }),
    }));

    const draws = isAll() ? p.owner_draw_usd : bucketSum("owner_draw");
    g.appendChild(kpi({
      label: "Owner draws",
      ...kv(draws, fmt.usd),
      sub: `${rl} · equity, NOT a cost — excluded from operating profit`,
      onClick: () => drillTx({
        title: `Owner draws — ${rl}`, buckets: ["owner_draw"], ranged: true,
        sortAmount: true,
        subtitle: "below the line: this is money taken out, not money spent running the business",
      }),
    }));
    return g;
  }

  /* ── KPI row C: connects + the unfinished business ──────────────────────── */
  function kpiRowC() {
    const g = el("div", "grid-2");
    const cn = d.connects || {};
    const thisMonth = cn.this_month_usd, life = cn.lifetime_usd;

    // THE TILE WAS RIGHT AND THE DRILL WAS WRONG. `connects.this_month_usd` is
    // the sum of Connect Purchase rows attributed to THIS month (0 for 2026-08
    // — a real zero, no such row has landed). The drill passed no month at all
    // and answered with the LIFETIME set: 89 rows and −$2,536.27 under a tile
    // that reads $0. Same predicate now; lifetime keeps its own way in below.
    g.appendChild(kpi({
      label: "Connects spend",
      ...kv(thisMonth, fmt.usd),
      sub: thisMonth === 0
        ? `a true zero for ${thisM} — no Connect Purchase row has reached the`
          + ` ledger this month. ${dash(life, fmt.usd)} lifetime. If the Upwork`
          + ` CSV import is behind, this month's spend will appear when it lands.`
        : `${thisM} · ${dash(life, fmt.usd)} lifetime`,
      onClick: () => drillTx({
        title: `Connect purchases — ${thisM}`,
        q: "Connect Purchase", month: thisM,
        subtitle: "matched on the transaction type, on the server's attributed"
          + " month — the same month and the same match the tile counted",
        note: thisMonth === 0
          ? `This is empty because the figure is a real zero, not because the`
            + ` read failed: no Connect Purchase row is attributed to ${thisM}.`
            + ` Across every month there are Connect Purchase rows totalling`
            + ` ${dash(life, fmt.usd2)} — use "every Connect Purchase" below the`
            + ` tiles for those.`
          : null,
      }),
    }));

    const uc = p.uncategorized_count;
    g.appendChild(kpi({
      label: "Uncategorized rows",
      ...kv(uc, fmt.num),
      sub: uc === 0
        ? "a true zero — every reconciled transaction has a P&L bucket"
        : `${dash(srvBuckets.uncategorized, fmt.usd2)} of value sits in no P&L`
          + ` bucket, so it is in neither revenue nor costs. Bucket them and the`
          + ` operating profit above will move.`,
      onClick: () => drillTx({
        title: "Uncategorized transactions", buckets: ["uncategorized"],
        subtitle: "reconciled, but with no P&L bucket — excluded from every figure above",
      }),
    }));

    // The LIFETIME connects figure quoted in the tile's sub-line is a different
    // set from the tile itself, so it gets its own way in rather than hijacking
    // the tile's drill. (It cannot live inside the tile: a <button> nested in
    // the kpi <button> is invalid and its click would fire both.)
    return el("div", "stack-2", [
      g,
      el("div", "hstack", [
        btn(`Show every Connect Purchase (${dash(life, fmt.usd)} lifetime)`,
          () => drillTx({
            title: "Connect purchases — every one on the ledger",
            q: "Connect Purchase", sortAmount: true,
            subtitle: "matched on the transaction type, across all buckets and"
              + " months — this is the LIFETIME set, not the tile above",
          }), { sm: true, icon: "wallet" }),
        el("span", "muted small",
          `the tile above counts ${thisM} only; this is every month on the ledger`),
      ]),
    ]);
  }

  /* ── revenue by month (Chart.js, with the target drawn on) ──────────────── */
  function revenueChart() {
    const c = card(null, null);
    if (!byMonth.length) {
      c.appendChild(emptyState("No revenue months on the ledger yet.", { icon: "inbox" }));
      return c;
    }

    // per-month context for the tooltip, from the rows we already hold
    const ctxByMonth = new Map();
    rows.filter((x) => x.b === "revenue").forEach((x) => {
      const g = ctxByMonth.get(x.m) || { n: 0, bySource: new Map() };
      g.n += 1;
      g.bySource.set(x.s || "?", (g.bySource.get(x.s || "?") || 0) + x.u);
      ctxByMonth.set(x.m, g);
    });

    const labels = byMonth.map((m) => m.month.slice(2));
    const data = byMonth.map((m) => m.usd);
    const target = Number(r.target_usd) || 0;
    const top = Math.max(...data, target, 1);

    // The monthly target, drawn as a labelled line. It takes the palette its
    // factory was handed: charts.js re-creates the inline plugins from the
    // factory on every refresh, so a theme flip replaces this closure.
    const targetLine = (p2) => ({
      id: "ew-target",
      afterDatasetsDraw(ch) {
        if (!target) return;
        const y = ch.scales.y.getPixelForValue(target);
        const { left, right } = ch.chartArea;
        const g = ch.ctx;
        g.save();
        g.setLineDash([4, 4]);
        g.lineWidth = 1;
        g.strokeStyle = p2.target;
        g.beginPath(); g.moveTo(left, y); g.lineTo(right, y); g.stroke();
        g.setLineDash([]);
        g.fillStyle = p2.target;
        g.font = "600 11px " + (p2.font || "system-ui");
        g.textAlign = "right";
        g.fillText("target " + fmt.usd(target), right - 2, y - 5);
        g.restore();
      },
    });

    const factory = (pal) => ({
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Revenue",
          data,
          backgroundColor: byMonth.map((m) => (inRange(m.month)
            ? (m.current ? pal.color(1) : pal.color(0))
            : pal.soft(0))),
          borderRadius: 4,
          maxBarThickness: 44,
        }],
      },
      options: {
        scales: axes(pal, {
          y: { suggestedMax: top * 1.12 },
          yTicks: { callback: (v) => fmt.usd(v) },
        }),
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => byMonth[items[0].dataIndex].month,
              label: (item) => {
                const m = byMonth[item.dataIndex];
                const g = ctxByMonth.get(m.month);
                const out = [fmt.usd2(m.usd)];
                if (g) {
                  out.push(`${g.n} transaction${g.n === 1 ? "" : "s"}`);
                  const best = [...g.bySource.entries()].sort((a, b) => b[1] - a[1])[0];
                  if (best) out.push(`top source: ${best[0]} ${fmt.usd(best[1])}`);
                }
                if (!inRange(m.month)) out.push("outside the current range");
                out.push("click to drill down");
                return out;
              },
            },
          },
        },
        onClick: (evt, els) => {
          if (!els.length) return;
          const m = byMonth[els[0].index].month;
          drillTx({
            title: `Revenue — ${m}`, buckets: ["revenue"], month: m,
            subtitle: "on the server's attributed month (pnl_month wins over the payment date)",
          });
        },
        onHover: (evt, els) => {
          const t = evt.native && evt.native.target;
          if (t) t.style.cursor = els.length ? "pointer" : "default";
        },
      },
      plugins: [targetLine(pal)],
    });

    // charts.js owns the detached-container problem (it defers construction a
    // frame and skips a slot that never reached the page), so this is a plain
    // call; the handle is kept only so a range change can destroy it.
    revChart = chart(c, factory, { height: 250 });

    append(c, el("div", "muted small",
      `Bars outside ${rangeLabel()} are dimmed; the range does not change them,`
      + ` it only changes what the figures above are measuring.`));
    return c;
  }

  /* ── P&L by month ───────────────────────────────────────────────────────── */
  function pnlByMonth(scoped, rl) {
    const c = card(null, null, { class: "flush" });
    const months = [...new Set(scoped.filter((x) => OPERATING_ALL.includes(x.b)).map((x) => x.m))]
      .sort().reverse().slice(0, isAll() ? 13 : 24);

    if (!months.length) {
      c.appendChild(emptyState(`No operating transactions in ${rl}.`, { icon: "inbox" }));
      return c;
    }

    const sumWhere = (pred) => round2(scoped.reduce((s, x) => (pred(x) ? s + x.u : s), 0));
    const t = el("table");
    t.appendChild(el("thead", null, el("tr", null, [
      el("th", null, "Month"),
      el("th", "num", "Revenue"),
      el("th", "num", "Expenses"),
      el("th", "num", "Profit"),
    ])));
    const tb = el("tbody");

    // THE WHOLE CELL IS THE TARGET. `td.drillable` already carries
    // `cursor: pointer` and a hover underline across the full cell, but the
    // handler used to sit on the inner <button> — leaving a measured 183.7px
    // strip per cell that looked clickable, underlined on hover, and did
    // nothing. The handler now lives on the td, and the inner <button> is kept
    // purely so the cell is still reachable and operable from the keyboard: its
    // activation click bubbles to the td, so either route fires exactly once.
    const cell = (cls, text, onClick) => {
      const td = el("td", {
        class: "num drillable " + cls,
        on: { click: onClick },
      });
      td.appendChild(el("button", {
        class: "linknum", type: "button",
        attrs: { "aria-label": "show the transactions behind " + text },
      }, text));
      return td;
    };

    months.forEach((m) => {
      const rev = sumWhere((x) => x.m === m && (x.b === "revenue" || x.b === "refund"));
      const cost = sumWhere((x) => x.m === m && OPERATING_COSTS.includes(x.b));
      const profit = round2(rev + cost);
      tb.appendChild(el("tr", null, [
        el("td", null, [m, m === thisM ? " " : null, m === thisM ? chip("current") : null]),
        cell("pos", fmt.usd2(rev), () => drillTx({
          title: `Revenue — ${m}`, buckets: ["revenue", "refund"], month: m })),
        cell("neg", fmt.usd2(cost), () => drillTx({
          title: `Expenses — ${m}`, buckets: OPERATING_COSTS, month: m, sortAmount: true })),
        cell(profit >= 0 ? "pos" : "neg", fmt.usd2(profit), () => drillTx({
          title: `P&L — ${m} (every operating row)`, buckets: OPERATING_ALL, month: m })),
      ]));
    });

    const totRev = sumWhere((x) => x.b === "revenue" || x.b === "refund");
    const totCost = sumWhere((x) => OPERATING_COSTS.includes(x.b));
    const totProfit = round2(totRev + totCost);
    tb.appendChild(el("tr", { style: { fontWeight: "700" } }, [
      el("td", null, isAll() ? "All-time" : "Range total"),
      el("td", "num pos", fmt.usd2(totRev)),
      el("td", "num neg", fmt.usd2(totCost)),
      el("td", "num " + (totProfit >= 0 ? "pos" : "neg"), fmt.usd2(totProfit)),
    ]));

    t.appendChild(tb);
    c.appendChild(el("div", "tablewrap", t));
    if (isAll() && months.length === 13) {
      c.appendChild(el("div", { class: "muted small", style: { padding: "10px 20px" } },
        "Showing the last 13 months — pick a range above to reach further back."));
    }
    return c;
  }

  /* ── the waterfall (hand-rolled; every colour is a live CSS variable) ───── */
  function waterfall(bucketSum, opProfit, rl) {
    const c = card(null, null);
    const lines = [
      ["revenue", "Revenue"], ["refund", "Refunds"],
      ["platform_fee", "Platform fees (Upwork / Wise)"], ["tax", "Taxes (WHT / GST)"],
      ["payroll", "Payroll"], ["opex", "Operating expenses"],
    ];
    const max = Math.max(...lines.map(([b]) => Math.abs(bucketSum(b))), 1);

    const rowStyle = {
      display: "grid", gridTemplateColumns: "minmax(150px, 1.1fr) minmax(80px, 2fr) 130px",
      alignItems: "center", gap: "16px", width: "100%", textAlign: "left",
      background: "none", border: "0", borderBottom: "1px solid var(--border)",
      font: "inherit", color: "inherit", padding: "10px 0", cursor: "pointer",
    };
    const track = (v) => el("div", {
      style: {
        height: "8px", borderRadius: "999px", background: "var(--muted)",
        overflow: "hidden", position: "relative",
      },
    }, el("span", {
      style: {
        display: "block", height: "100%", borderRadius: "inherit",
        width: (100 * Math.abs(v) / max) + "%",
        background: v >= 0 ? "var(--success)" : "var(--destructive)",
      },
    }));

    lines.forEach(([b, label]) => {
      const v = bucketSum(b);
      c.appendChild(el("button", {
        style: rowStyle, type: "button",
        attrs: { "aria-label": `show the transactions behind ${label}` },
        on: { click: () => drillTx({
          title: `${BLABEL[b] || b} — ${rl}`, buckets: [b], ranged: true,
          sortAmount: v < 0 }) },
      }, [
        el("div", null, label),
        track(v),
        el("div", "num " + (v >= 0 ? "pos" : "neg"), fmt.usd2(v)),
      ]));
    });

    c.appendChild(el("div", {
      style: Object.assign({}, rowStyle, {
        cursor: "default", fontWeight: "700",
        borderBottom: "2px solid var(--border-strong)",
      }),
    }, [
      el("div", null, "Operating profit"),
      el("div"),
      el("div", "num " + (opProfit == null ? "dash" : opProfit >= 0 ? "pos" : "neg"),
        fmt.usd2(opProfit)),
    ]));

    // BELOW THE LINE. Owner draws are equity: money taken out, not a cost of
    // running the business. Putting them above the line would understate profit.
    const od = bucketSum("owner_draw");
    c.appendChild(el("button", {
      style: Object.assign({}, rowStyle, { borderBottom: "0" }), type: "button",
      attrs: { "aria-label": "show the transactions behind owner draws" },
      on: { click: () => drillTx({
        title: `Owner draws — ${rl}`, buckets: ["owner_draw"], ranged: true, sortAmount: true }) },
    }, [
      el("div", "muted", "Owner draws (below the line — equity, not a cost)"),
      el("div"),
      el("div", "num neg", fmt.usd2(od)),
    ]));

    c.appendChild(el("div", "muted small",
      "Transfers, sweeps and card settlements are excluded — they are"
      + " non-economic (money moving between our own accounts) and would"
      + " double-count if they appeared here."));
    return c;
  }

  /* ── cash ───────────────────────────────────────────────────────────────── */
  function cashCard() {
    const c = card(null, null);
    const accounts = cash.by_account || [];
    if (!accounts.length) {
      c.appendChild(emptyState("No account balances have been imported.", { icon: "inbox" }));
      return c;
    }
    const line = (left, right, strong) => el("div", {
      class: "between",
      style: {
        padding: "9px 0", borderBottom: "1px solid var(--border)",
        fontWeight: strong ? "700" : "400",
      },
    }, [left, right]);

    accounts.forEach((a) => {
      const local = a.currency === "INR"
        ? "₹" + Number(a.closing).toLocaleString("en-IN", { minimumFractionDigits: 2 })
        : fmt.usd2(Number(a.closing));
      c.appendChild(line(
        el("div", null, [el("strong", null, a.account), " ", el("span", "muted", a.currency)]),
        el("div", "num", [local, el("span", "muted", " → " + fmt.usd2(a.usd))]),
      ));
    });
    c.appendChild(line(
      el("div", null, "Total (base USD)"),
      el("div", "num", fmt.usd2(cash.value_usd)), true));
    c.appendChild(el("div", { class: "muted small", style: { marginTop: "10px" } },
      `INR → USD at ${dash(cash.inr_usd_rate)} (pinned, not live) · FD = fixed`
      + ` deposit · balances as of ${dash(cash.as_of)}. Cash is a point in time:`
      + ` the range above does not touch it.`));
    c.appendChild(el("div", { style: { marginTop: "10px" } },
      btn("Show the accounts behind this", drillCash, { sm: true, icon: "wallet" })));
    return c;
  }

  function drillCash() {
    const accounts = cash.by_account || [];
    openDrill({
      title: "Cash accounts",
      subtitle: `point-in-time closing balances as of ${cash.as_of || "?"},`
        + ` INR converted at the pinned ${cash.inr_usd_rate || "?"}`,
      rows: accounts,
      total: accounts.length,
      amount: (a) => a.usd,
      columns: [
        { key: "account", label: "Account", render: (a) => el("strong", null, a.account) },
        { key: "currency", label: "Currency" },
        { key: "closing", label: "Local", num: true,
          render: (a) => Number(a.closing).toLocaleString("en-US", { minimumFractionDigits: 2 }) },
        { key: "usd", label: "USD", num: true,
          render: (a) => el("span", a.usd >= 0 ? "pos" : "neg", fmt.usd2(a.usd)) },
      ],
      emptyText: "No account balances have been imported.",
    });
  }

  /* ── transaction explorer (every filter runs on the server) ─────────────── */
  function explorer() {
    const c = card(null, null);
    const fMonth = el("select", { attrs: { "aria-label": "filter by month" } });
    const fBucket = el("select", { attrs: { "aria-label": "filter by bucket" } });
    const fSource = el("select", { attrs: { "aria-label": "filter by source" } });
    const fSearch = el("input", {
      type: "search", placeholder: "search description or type…",
      attrs: { "aria-label": "search transactions" },
    });
    const count = el("span", "muted small", "loading…");
    const host = el("div", { style: { marginTop: "12px" } });

    const monthsInRange = (d.months || []).filter(inRange).sort().reverse();
    fMonth.appendChild(new Option(`All months in ${rangeLabel()}`, ""));
    monthsInRange.forEach((m) => fMonth.appendChild(new Option(m, m)));
    fBucket.appendChild(new Option("All buckets", ""));
    Object.keys(srvBuckets).sort().forEach((b) =>
      fBucket.appendChild(new Option(BLABEL[b] || b, b)));
    fSource.appendChild(new Option("All sources", ""));
    [...new Set(rows.map((x) => x.s))].filter(Boolean).sort()
      .forEach((s) => fSource.appendChild(new Option(s, s)));

    // The CURRENT preference, not the one this page loaded with. rerender()
    // rebuilds this whole card on every range change, and reading the page-load
    // snapshot meant that picking a preset reverted your bucket/source/search
    // and then wrote the reverted values back over them.
    const pref = readSaved();
    const keepMonth = pref.m && !monthsInRange.includes(pref.m) ? pref.m : "";
    if (pref.m && monthsInRange.includes(pref.m)) fMonth.value = pref.m;
    if (pref.b && srvBuckets[pref.b] !== undefined) fBucket.value = pref.b;
    if (pref.s) fSource.value = pref.s;
    if (pref.q) fSearch.value = pref.q;

    c.append(
      el("div", "hstack", [fMonth, fBucket, fSource, fSearch, count]),
      host,
    );

    let sortKey = "dt", sortDir = -1, seq = 0;
    let current = [];

    async function load() {
      const mine = ++seq;
      // `keepMonth` holds a stored month that this range cannot display. It is
      // not applied, and it is not destroyed either — leaving the range puts it
      // back. Everything else is exactly what the controls now say.
      persist({ m: fMonth.value || keepMonth, b: fBucket.value,
        s: fSource.value, q: fSearch.value });
      count.textContent = "loading…";
      const filters = {};
      if (fBucket.value) filters.bucket = fBucket.value;
      if (fSource.value) filters.source = fSource.value;
      if (fSearch.value.trim()) filters.q = fSearch.value.trim();
      if (fMonth.value) filters.month = fMonth.value;
      else if (oneMonth()) filters.month = oneMonth();

      let res;
      try {
        res = await loadTx(filters);
      } catch (e) {
        if (mine !== seq) return;
        clear(host).appendChild(el("div", "errstate",
          "Could not read the ledger: " + String(e.message || e)));
        count.textContent = "";
        return;
      }
      if (mine !== seq) return;                    // a later keystroke won
      let out = res.rows || [];
      if (!fMonth.value && !oneMonth() && !isAll()) out = out.filter((x) => inRange(x.m));
      current = out;
      draw();
    }

    function draw() {
      const out = current.slice().sort((a, b) => {
        const x = a[sortKey], y = b[sortKey];
        if (sortKey === "u") return ((Number(x) || 0) - (Number(y) || 0)) * sortDir;
        return (String(x) < String(y) ? -1 : String(x) > String(y) ? 1 : 0) * sortDir;
      });
      clear(count).append(
        `${fmt.num(out.length)} transaction${out.length === 1 ? "" : "s"} · sum `,
        el("b", null, fmt.usd2(sumU(out))),
      );

      const t = el("table");
      const hr = el("tr");
      [["dt", "Date", false], ["b", "Bucket", false], ["t", "Type", false],
       ["s", "Source", false], ["d", "Description", false], ["u", "Amount", true]]
        .forEach(([k, label, num]) => {
          const th = el("th", num ? "num" : null);
          th.appendChild(el("button", {
            class: "linknum", type: "button",
            attrs: { "aria-label": "sort by " + label },
            on: { click: () => {
              if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = -1; }
              draw();
            } },
          }, label + (sortKey === k ? (sortDir < 0 ? " ▼" : " ▲") : "")));
          hr.appendChild(th);
        });
      t.appendChild(el("thead", null, hr));

      const tb = el("tbody");
      out.slice(0, 600).forEach((x) => {
        tb.appendChild(el("tr", null, [
          el("td", "muted", x.dt),
          el("td", null, el("span", "chip " + (x.b || ""), BLABEL[x.b] || x.b || "—")),
          el("td", null, x.t || el("span", "dash", "—")),
          el("td", "muted", x.s || el("span", "dash", "—")),
          el("td", null, x.d || el("span", "dash", "—")),
          el("td", "num " + (x.u >= 0 ? "pos" : "neg"), fmt.usd2(x.u)),
        ]));
      });
      t.appendChild(tb);

      clear(host);
      if (!out.length) {
        host.appendChild(emptyState(
          "No transaction matches those filters — widen them above.", { icon: "search" }));
        return;
      }
      const wrap = el("div", { class: "tablewrap", style: { maxHeight: "480px" } }, t);
      host.appendChild(wrap);
      if (out.length > 600) {
        host.appendChild(el("div", "muted small",
          `Showing the first 600 of ${fmt.num(out.length)} rows in this selection`
          + ` (the sum above covers all ${fmt.num(out.length)}). Narrow the`
          + ` filters to see the rest.`));
      }
    }

    [fMonth, fBucket, fSource].forEach((s) => s.addEventListener("change", load));
    let typing = null;
    fSearch.addEventListener("input", () => {
      clearTimeout(typing);
      typing = setTimeout(load, 220);            // the search runs on the server
    });
    load();
    return c;
  }

  /* ── provenance ─────────────────────────────────────────────────────────── */
  function provenanceFooter(scoped) {
    return el("div", "muted small", [
      `Ledger through `, el("b", null, d.as_of || "?"),
      ` · ${fmt.num(rows.length)} economic transactions read for these`
      + ` aggregates (${fmt.num(scoped.length)} in ${rangeLabel()}); drills and`
      + ` the explorer re-query the server. Computed from the same audited`,
      el("code", null, "pnl.compute"), ` buckets as `, el("code", null, "bin/finance-pnl"),
      `, gated on finance-doctor GREEN. Net revenue = revenue + refunds;`
      + ` operating profit is after platform fees, taxes, payroll and opex;`
      + ` owner draws are equity, below the line; transfers, sweeps and card`
      + ` settlements are excluded as non-economic. Cash is point-in-time and`
      + ` is never scoped by the range.`,
    ]);
  }

  rerender();
}

/* ── source freshness — ALWAYS rendered, including when it is fine ────────── */

function sourceFreshness(mh) {
  if (!mh) {
    return notice("info",
      "Source freshness is not measured right now — the health read returned"
      + " nothing, so treat the age of these figures as unknown.");
  }
  const age = mh.age_hours == null ? "never"
    : mh.age_hours < 48 ? `${Math.round(mh.age_hours)}h ago`
    : `${Math.round(mh.age_hours / 24)} days ago`;
  if (mh.ok) {
    return notice("ok",
      `${mh.label || "The money sources"} last produced ${age}`
      + (mh.last_success ? ` (${String(mh.last_success).slice(0, 10)})` : "")
      + ` — the ledger is both consistent and current.`);
  }
  const lvl3 = (mh.escalation || 0) >= 3;
  return notice(lvl3 ? "bad" : "warn", [
    el("b", null, lvl3 ? "Money data is not current" : "Money data may be stale"),
    ` — ${mh.label || "a money source"} last produced ${age}`
    + (mh.last_success ? ` (${String(mh.last_success).slice(0, 10)})` : "")
    + `. A GREEN doctor proves these figures are internally consistent; it`
    + ` says nothing about whether anything has been ADDED since. Everything`
    + ` below may be missing what has arrived in the meantime.`,
  ]);
}

/* ── the payment-mail overlay (brief §4) ──────────────────────────────────── */

/**
 * Written for one specific failure: a client looking delinquent because the
 * ledger is behind, not because they did not pay. Rendered in every state —
 * "nothing waiting" is itself the answer to "is this total complete?".
 */
function provisionalPanel(prov) {
  if (!prov) {
    return notice("info",
      "Payment-mail overlay unavailable — the rail's table could not be read,"
      + " so whether any payment has arrived that is not yet on the ledger is"
      + " NOT MEASURED. Do not read the totals below as complete.");
  }
  const st = prov.staleness || {};
  const unmapped = prov.unmapped_count;
  const extra = unmapped
    ? ` ${unmapped} reported payment${unmapped === 1 ? "" : "s"} could not be`
      + ` attributed to a client and ${unmapped === 1 ? "is" : "are"} waiting to be mapped.`
    : "";

  if (st.stale && st.count) {
    const mr = st.most_recent || {};
    const rowsOf = (st.unimported || []).map((x) => ({
      client: x.client_id || x.payer || "—",
      payer: x.payer || "—",
      paid_date: x.paid_date,
      amount_usd: x.amount_usd,
    }));
    const open = () => openDrill({
      title: "Payments seen in email but not yet imported",
      subtitle: `the ledger ends ${st.ledger_newest || "?"}; the newest payment`
        + ` email is dated ${st.email_newest || "?"}`,
      rows: rowsOf,
      total: rowsOf.length,
      amount: (x) => (x.amount_usd == null ? 0 : Number(x.amount_usd)),
      time: (x) => x.paid_date,
      columns: [
        { key: "paid_date", label: "Paid", render: (x) => fmt.dateShort(x.paid_date) },
        { key: "client", label: "Client" },
        { key: "payer", label: "Payer", cls: "muted" },
        { key: "amount_usd", label: "USD", num: true,
          render: (x) => (x.amount_usd == null
            ? el("span", "dash", "—")
            : el("span", "pos", fmt.usd2(x.amount_usd))) },
      ],
      note: "An amount of — means the email reported a payment without a"
        + " parseable figure: the payment is real, the number is not measured."
        + " These rows are EVIDENCE, not ledger entries; they are deliberately"
        + " excluded from every total on this page until the import runs.",
      emptyText: "No unimported payments.",
    });
    return notice("warn", [
      el("b", null, `${st.count} payment${st.count === 1 ? "" : "s"} seen in email but not yet imported`),
      ` — most recently ${mr.client_id || mr.payer || "an unknown payer"} on`
      + ` ${mr.paid_date || "an unknown date"}`
      + (mr.amount_usd != null ? ` (${fmt.usd2(mr.amount_usd)})` : " (amount not parsed)")
      + `. At least ${fmt.usd2(st.floor_usd)} has arrived that these totals do`
      + ` not yet contain, so a client can look delinquent here purely because`
      + ` the ledger is behind.${extra} `,
    ], { action: btn("Show them", open, { sm: true, icon: "eye-off" }) });
  }

  return notice("ok",
    `Payment-mail overlay: nothing is waiting to be imported — the ledger runs`
    + ` to ${st.ledger_newest || "?"} and the newest payment email is dated`
    + ` ${st.email_newest || "—"}, so no known payment is missing from the`
    + ` figures below.${extra}`);
}

/* ── import: upload → auto dry-run → an explicit second click to commit ───── */

/**
 * The cockpit never writes the ledger itself. An upload STAGES a file into
 * finance-inbox/; the import runs the same audited importer bin the CLI runs
 * (reconcile-or-abort, idempotent); and committing is always a second,
 * separate click on a report you have already read.
 */
function importCard(ctx) {
  return expander("Import data — bank / card / Wise / Upwork / Stripe exports", (host) => {
    host.appendChild(el("div", "muted small",
      "Uploading only stages the file. Every import dry-runs first and prints"
      + " the importer's own report; committing is a second click, and the"
      + " finance-doctor verdict is shown after the write."));

    const out = el("div", { class: "stack-2", style: { marginTop: "12px" } });
    const tiles = el("div", { class: "grid", style: { marginTop: "12px" } });

    KINDS.forEach(([kind, label, hint, accept]) => {
      const input = el("input", { type: "file", accept, style: { display: "none" } });
      const go = btn("Upload", () => input.click(), { sm: true, icon: "upload" });
      input.addEventListener("change", () => {
        if (input.files[0]) doUpload(kind, input.files[0], go, out, ctx);
        input.value = "";
      });
      tiles.appendChild(el("div", {
        style: {
          border: "1px solid var(--border)", borderRadius: "var(--radius)",
          padding: "12px 14px", display: "flex", alignItems: "center",
          justifyContent: "space-between", gap: "12px",
        },
      }, [
        el("div", null, [el("b", null, label), el("div", "muted small", hint)]),
        input, go,
      ]));
    });

    host.append(tiles, out);
  }, { class: "importcard" });
}

function reportChips(report) {
  return Object.entries(report || {})
    .filter(([, v]) => v == null || typeof v !== "object")
    .slice(0, 12)
    .map(([k, v]) => chip(`${k}: ${v}`));
}

async function doUpload(kind, file, button, out, ctx) {
  button.disabled = true;
  const label = button.textContent;
  button.textContent = "Uploading…";
  clear(out);
  try {
    const res = await fetch(
      `/api/finance/upload?kind=${encodeURIComponent(kind)}`
      + `&filename=${encodeURIComponent(file.name)}`,
      { method: "POST", headers: { "X-Cockpit-Token": bootToken() }, body: file });
    if (res.status === 401) {
      out.appendChild(el("div", "errstate", "Unauthorized — reload to unlock the cockpit."));
      return;
    }
    const up = await res.json().catch(() => ({}));
    if (!up.ok) {
      out.appendChild(el("div", "errstate", up.error || `upload failed (HTTP ${res.status})`));
      return;
    }
    out.appendChild(el("div", "muted small", [
      "staged ", el("code", null, up.staged_as), " in ", el("code", null, up.dir),
      up.note ? " — " + up.note : "",
    ]));
    if (!up.importable) return;
    await doImport(kind, false, out, ctx);       // auto dry-run once it is staged
  } catch (e) {
    out.appendChild(el("div", "errstate", "upload failed: " + String(e.message || e)));
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function doImport(kind, commit, out, ctx) {
  const pending = el("div", "muted small", commit ? "Committing…" : "Dry-run…");
  out.appendChild(pending);
  let res;
  try {
    res = await ctx.post("/api/finance/import", { kind, commit });
  } catch (e) {
    pending.remove();
    out.appendChild(el("div", "errstate", "import failed: " + String(e.message || e)));
    return;
  }
  pending.remove();
  if (!res.ok) {
    out.appendChild(el("div", "errstate", res.error || "import failed"));
    return;
  }

  const box = el("div", {
    style: {
      border: "1px solid var(--border)", borderRadius: "var(--radius)",
      padding: "12px 14px", display: "flex", flexDirection: "column", gap: "10px",
    },
  });
  box.appendChild(el("div", "hstack", [
    el("b", null, commit ? "COMMITTED" : "DRY-RUN"),
    el("span", "muted", kind),
    ...reportChips(res.report),
  ]));

  if (res.report && res.report.abort) {
    box.appendChild(el("div", "errstate", "importer refused: " + res.report.abort));
  } else if (!commit) {
    box.appendChild(el("div", "hstack", [
      btn("Commit to ledger", () => doImport(kind, true, out, ctx),
        { variant: "primary", sm: true, icon: "check" }),
      el("span", "muted small",
        "this writes to the ledger and re-runs the bucketizer and the doctor"),
    ]));
  } else {
    const green = res.doctor === "GREEN";
    box.appendChild(el("div", "hstack", [
      "finance-doctor after the write: ", pill(res.doctor || "?", green ? "ok" : "fail"),
      res.bucketized != null
        ? el("span", "muted small", `${res.bucketized} rows bucketized`) : null,
      res.bucketize_error ? el("span", "neg small", res.bucketize_error) : null,
    ]));
    if (!green && (res.reds || []).length) {
      box.appendChild(el("div", "errstate", (res.reds || []).join(", ")));
    }
    box.appendChild(el("div", "muted small", "refreshing the audited figures…"));
    toast(green ? "Imported — figures refreshing" : "Imported, but the doctor is RED", !green);
    setTimeout(() => ctx.refresh(), 1200);
  }
  out.appendChild(box);
}
