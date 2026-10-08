// propfunnel.js — the proposal funnel's arithmetic (Upwork tab › Funnel).
//
// Pure functions over the facts /api/upwork/funnel returns (one row per
// proposal). Every number the Funnel view shows is computed HERE and every
// drill opens the rows the number counted, so a figure and the rows behind it
// are the same array.
//
// THE LAW, as in funnel.js: each stage IS a subset of the one above, derived
// by narrowing the previous stage's rows, so no step can ever read above 100%.
//   sent     → every proposal in the selection
//   opened   → Upwork records the client opening it, OR the client wrote back
//              (a reply proves they saw it; 3 of 2026's 30 replies came straight
//              from the list with no open on record)
//   replied  → of the opened, the client wrote back (a room exists)
//   hired    → of those, a contract followed
//
// Loaded as a plain <script> in the browser (exposes EW_PROPFUNNEL) and
// require()d by tests/test_proposal_funnel_js.py under node — same code.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.EW_PROPFUNNEL = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var STAGES = [
    { k: "sent", label: "Sent", noun: "proposals sent" },
    { k: "opened", label: "Opened", noun: "opened by the client" },
    { k: "replied", label: "Wrote back", noun: "the client wrote back" },
    { k: "hired", label: "Hired", noun: "a contract started" },
  ];
  var INVITE_STAGES = [
    { k: "sent", label: "Accepted", noun: "invitations you accepted" },
    { k: "hired", label: "Hired", noun: "a contract started" },
  ];
  var REFUNDED = { "outbid, bid returned": 1, "job closed, bid returned": 1 };
  // below this many in the denominator a rate is shown but called too few
  var FEW = 8;

  function seen(r) { return r.op === 1 || r.rep === 1; }

  /** the nested stages of a selection; `kind` "invite" gives the 2-step chain */
  function stages(rows, kind) {
    rows = rows || [];
    if (kind === "invite") {
      var h = rows.filter(function (r) { return r.hired === 1; });
      return [
        mk(INVITE_STAGES[0], rows, null, rows.length),
        mk(INVITE_STAGES[1], h, rows.length, rows.length),
      ];
    }
    var opened = rows.filter(seen);
    var replied = opened.filter(function (r) { return r.rep === 1; });
    var hired = replied.filter(function (r) { return r.hired === 1; });
    var sets = [rows, opened, replied, hired];
    return STAGES.map(function (s, i) {
      return mk(s, sets[i], i ? sets[i - 1].length : null, rows.length);
    });
  }

  function mk(s, set, prev, top) {
    return {
      k: s.k, label: s.label, noun: s.noun, n: set.length, rows: set,
      prev: prev,
      ofPrev: prev ? set.length / prev : null,
      ofTop: top ? set.length / top : null,
      few: prev != null && prev < FEW,
    };
  }

  /** rows the nesting cannot place: a hire with no reply on record, an open
      date on a proposal whose page says it was never opened. Shown, not hidden. */
  function strays(rows) {
    return (rows || []).filter(function (r) { return r.hired === 1 && r.rep !== 1; });
  }

  /* ── what the drop at each step consists of ─────────────────────────── */
  function opensOnRecord(rows) {
    var rec = 0, fromList = 0;
    (rows || []).forEach(function (r) {
      if (r.op === 1) rec += 1; else if (r.rep === 1) fromList += 1;
    });
    return { onRecord: rec, answeredFromList: fromList };
  }

  /* ── connects and money ──────────────────────────────────────────────── */
  /** connects a proposal really cost: base + boost, the boost back if it was
      outbid or the job closed. null when nothing about it is known. */
  function connectsSpent(r) {
    if (r.cx == null && r.cb == null) return null;
    if (r.boost === 1 && r.bo && REFUNDED[r.bo]) return r.cb != null ? r.cb : null;
    return r.cx != null ? r.cx : r.cb;
  }

  function money(rows, M) {
    var out = { connects: 0, connectsKnown: 0, connectsUnknown: 0, usd: null,
                revenue: 0, revenueHires: 0, revenueUnknown: 0, hires: 0, blocked: !!(M && M.blocked) };
    (rows || []).forEach(function (r) {
      var c = connectsSpent(r);
      if (c == null) out.connectsUnknown += 1; else { out.connects += c; out.connectsKnown += 1; }
      if (r.hired === 1) {
        out.hires += 1;
        var w = M && M.won ? M.won[r.id] : null;
        if (w && w.usd != null) { out.revenue += w.usd; out.revenueHires += 1; }
        else if (!(w && w.shared_with)) out.revenueUnknown += 1;
      }
    });
    var price = M && !M.blocked ? M.price_per_connect : null;
    out.usd = price != null ? Math.round(out.connects * price * 100) / 100 : null;
    out.price = price;
    out.revenue = Math.round(out.revenue * 100) / 100;
    out.perHire = out.usd != null && out.hires ? out.usd / out.hires : null;
    out.returnPerDollar = out.usd ? out.revenue / out.usd : null;
    return out;
  }

  /* ── periods ─────────────────────────────────────────────────────────── */
  var PERIODS = [
    { k: "4w", label: "Last 4 weeks", days: 28 },
    { k: "3m", label: "Last 3 months", days: 91 },
    { k: "ytd", label: "This year" },
    { k: "12m", label: "Last 12 months", days: 365 },
    { k: "all", label: "All time" },
  ];

  function periodBounds(k, today) {
    var t = new Date(String(today).slice(0, 10) + "T00:00:00Z");
    var p = PERIODS.filter(function (x) { return x.k === k; })[0] || PERIODS[2];
    if (p.k === "all") return { lo: null, hi: null, k: p.k };
    if (p.k === "ytd") return { lo: String(today).slice(0, 4) + "-01-01", hi: null, k: p.k };
    var lo = new Date(t.getTime() - (p.days - 1) * 86400000);
    return { lo: lo.toISOString().slice(0, 10), hi: null, k: p.k };
  }

  function inPeriod(rows, b) {
    return (rows || []).filter(function (r) {
      var d = String(r.sent || "").slice(0, 10);
      return (!b.lo || d >= b.lo) && (!b.hi || d <= b.hi);
    });
  }

  /* ── the dimensions a selection can be split by ──────────────────────── */
  function band(v, edges, labels) {
    if (v == null || Number.isNaN(Number(v))) return null;
    for (var i = 0; i < edges.length; i++) if (Number(v) <= edges[i]) return labels[i];
    return labels[labels.length - 1];
  }
  var ABOUT = { client: "About the client", self: "About you", fit: "A fit claim (“exactly what I do”)",
                video: "Leads with a Loom", plan: "Your plan", mixed: "Mixed", other: "Other" };
  var RULE_TO_ABOUT = { "their problem / insight first": "client", "self / credentials first": "self",
                        "self intro": "self", "fit claim / excitement": "fit", "loom first": "video",
                        "invite thanks": "other", "other": "other" };
  var CAT_ORDER = ["Airtable", "Make / Zapier / n8n", "GoHighLevel / CRM tool", "Voice AI", "AI agent / LLM",
                   "Generic dev / SaaS", "Automation, tool not named", "Other"];
  var DID = { hired_us: "Hired you", hired_other: "Hired someone else",
              interviewed: "Talked to others, hired no one", nobody: "Spoke to nobody" };

  var DIMS = [
    { k: "boost", label: "Boost", of: function (r) { return r.boost === 1 ? "Boosted" : r.boost === 0 ? "Not boosted" : null; },
      order: ["Boosted", "Not boosted"] },
    { k: "boostsize", label: "Boost size",
      hint: "connects bid above the job's base cost",
      of: function (r) {
        if (r.boost === 0) return "No boost";
        if (r.boost !== 1 || r.cx == null || r.cb == null) return null;
        return band(r.cx - r.cb, [50, 100, 200], ["Up to 50", "51-100", "101-200", "Over 200"]);
      },
      order: ["No boost", "Up to 50", "51-100", "101-200", "Over 200"] },
    { k: "boostresult", label: "Boost result",
      hint: "how the boost auction settled (billing since 30 June)",
      of: function (r) {
        if (r.boost !== 1 || !r.bo) return null;
        return { "charged": "Charged (kept the slot)", "outbid, bid returned": "Outbid, refunded",
                 "job closed, bid returned": "Job closed, refunded", "pending": "Still pending" }[r.bo] || null;
      },
      order: ["Charged (kept the slot)", "Outbid, refunded", "Job closed, refunded", "Still pending"] },
    { k: "loom", label: "Loom", of: function (r) { return r.loom === 1 ? "Has a Loom" : r.loom === 0 ? "No Loom" : null; },
      order: ["Has a Loom", "No Loom"] },
    { k: "opening", label: "First lines",
      hint: "what the first two sentences are about: judged by two readers for 2025-26, by rule before",
      of: function (r) {
        var a = r.ab || RULE_TO_ABOUT[r.opf];
        return a ? ABOUT[a] : null;
      },
      order: [ABOUT.client, ABOUT.self, ABOUT.fit, ABOUT.video, ABOUT.plan, ABOUT.mixed, ABOUT.other] },
    { k: "tool", label: "Names your tool",
      hint: "the job title names Airtable, Make, n8n, Zapier, GoHighLevel or another tool you sell",
      of: function (r) { return r.tool === 1 ? "Tool in the title" : r.tool === 0 ? "No tool in the title" : null; },
      order: ["Tool in the title", "No tool in the title"] },
    { k: "category", label: "Job type", of: function (r) { return r.cat || null; }, order: CAT_ORDER },
    { k: "bid", label: "Your bid",
      of: function (r) {
        if (r.bid == null) return null;
        if (r.bid >= 100) return "Fixed price";
        return band(r.bid, [32.5, 45, 52], ["$32.5 or less", "$33-45", "$46-52", "Over $52"]);
      },
      order: ["$32.5 or less", "$33-45", "$46-52", "Over $52", "Fixed price"] },
    { k: "vsmsg", label: "Bid vs who got messaged",
      hint: "your hourly bid against the average bid of the other people the client messaged",
      of: function (r) {
        if (r.avgm == null || r.bid == null || r.bid >= 100 || !r.avgm) return null;
        var x = r.bid / r.avgm;
        return x > 1.2 ? "More than 20% above them" : x < 0.8 ? "More than 20% below them" : "Within 20% of them";
      },
      order: ["Within 20% of them", "More than 20% above them", "More than 20% below them"] },
    { k: "hire", label: "Client hire rate",
      of: function (r) {
        if (r.chr == null) return null;
        if (Number(r.chr) === 0) return "Never hired (0%)";
        return band(r.chr, [40, 70, 90], ["1-40%", "41-70%", "71-90%", "91-100%"]);
      },
      order: ["Never hired (0%)", "1-40%", "41-70%", "71-90%", "91-100%"] },
    { k: "spend", label: "Client spend",
      of: function (r) {
        if (r.csp == null) return null;
        if (Number(r.csp) === 0) return "$0 (new client)";
        return band(r.csp, [1000, 10000, 100000], ["Under $1K", "$1K-10K", "$10K-100K", "$100K+"]);
      },
      order: ["$0 (new client)", "Under $1K", "$1K-10K", "$10K-100K", "$100K+"] },
    { k: "pile", label: "Proposals on the job",
      hint: "Upwork's count on the proposal page (shown only while a proposal is not archived)",
      of: function (r) { return r.pile ? band(r.pile, [20, 50, 100], ["Up to 20", "21-50", "51-100", "Over 100"]) : null; },
      order: ["Up to 20", "21-50", "51-100", "Over 100"] },
    { k: "speed", label: "How fast you applied",
      hint: "hours from the job's posting to your proposal",
      of: function (r) { return band(r.hta, [6, 24], ["Within 6 hours", "6-24 hours", "After the first day"]); },
      order: ["Within 6 hours", "6-24 hours", "After the first day"] },
    { k: "length", label: "Letter length",
      of: function (r) { return band(r.w, [150, 250, 350], ["Up to 150 words", "151-250", "251-350", "Over 350"]); },
      order: ["Up to 150 words", "151-250", "251-350", "Over 350"] },
    { k: "voice", label: "Contractions",
      hint: "I'm, you're, it's — a relaxed voice",
      of: function (r) { return band(r.con, [0, 2], ["None", "1-2", "3 or more"]); },
      order: ["None", "1-2", "3 or more"] },
    { k: "did", label: "What the client did",
      of: function (r) { return r.did ? DID[r.did] : null; },
      order: [DID.hired_us, DID.hired_other, DID.interviewed, DID.nobody] },
    { k: "score", label: "Scanner score",
      of: function (r) { return band(r.sc, [25, 30], ["25 or below", "26-30", "31 or more"]); },
      order: ["25 or below", "26-30", "31 or more"] },
    { k: "month", label: "Month sent",
      of: function (r) { return r.sent ? String(r.sent).slice(0, 7) : null; },
      order: null },
  ];
  var DIM_BY_KEY = {};
  DIMS.forEach(function (d) { DIM_BY_KEY[d.k] = d; });
  var UNKNOWN = "Not known";

  function split(rows, dimKey, kind) {
    var dim = DIM_BY_KEY[dimKey];
    if (!dim) return [];
    var groups = {};
    (rows || []).forEach(function (r) {
      var b = dim.of(r);
      var key = b == null ? UNKNOWN : b;
      (groups[key] = groups[key] || []).push(r);
    });
    var keys = Object.keys(groups);
    var order = dim.order;
    keys.sort(function (a, b) {
      if (a === UNKNOWN) return 1;
      if (b === UNKNOWN) return -1;
      if (!order) return a < b ? -1 : a > b ? 1 : 0;
      var ia = order.indexOf(a), ib = order.indexOf(b);
      if (ia < 0) ia = 999; if (ib < 0) ib = 999;
      return ia - ib || (a < b ? -1 : 1);
    });
    return keys.map(function (k) {
      return { key: k, rows: groups[k], stages: stages(groups[k], kind), unknown: k === UNKNOWN };
    });
  }

  /* ── is a difference real? (two-sided Fisher exact, for the plain-word pill) */
  function logFact(n) { var s = 0; for (var i = 2; i <= n; i++) s += Math.log(i); return s; }
  function fisher(a, b, c, d) {
    // a/b = hits/misses in the segment, c/d = hits/misses in the rest
    var n = a + b + c + d, r1 = a + b, c1 = a + c;
    if (!n || !r1 || r1 === n || !c1 || c1 === n) return 1;
    var lf = logFact;
    var base = lf(r1) + lf(n - r1) + lf(c1) + lf(n - c1) - lf(n);
    function p(x) { return Math.exp(base - lf(x) - lf(r1 - x) - lf(c1 - x) - lf(n - r1 - c1 + x)); }
    var lo = Math.max(0, r1 + c1 - n), hi = Math.min(r1, c1), p0 = p(a), tot = 0;
    for (var x = lo; x <= hi; x++) { var px = p(x); if (px <= p0 * (1 + 1e-7)) tot += px; }
    return Math.min(1, tot);
  }

  /** "clear" | "lean" | null — a segment's rate against the rest of the selection */
  function signal(k, n, K, N) {
    if (n < FEW || N - n < FEW) return null;
    var p = fisher(k, n - k, K - k, (N - n) - (K - k));
    return p < 0.05 ? "clear" : p < 0.2 ? "lean" : null;
  }

  /* ── time ─────────────────────────────────────────────────────────────── */
  function median(xs) {
    var v = (xs || []).filter(function (x) { return x != null && !Number.isNaN(Number(x)); })
      .map(Number).sort(function (a, b) { return a - b; });
    if (!v.length) return null;
    var m = Math.floor(v.length / 2);
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  }

  function byMonth(rows) {
    var g = {};
    (rows || []).forEach(function (r) {
      var m = String(r.sent || "").slice(0, 7);
      if (m) (g[m] = g[m] || []).push(r);
    });
    return Object.keys(g).sort().map(function (m) {
      var st = stages(g[m]);
      return { month: m, rows: g[m], sent: st[0].n, opened: st[1].n, replied: st[2].n, hired: st[3].n };
    });
  }

  return {
    STAGES: STAGES, INVITE_STAGES: INVITE_STAGES, PERIODS: PERIODS, DIMS: DIMS, DIM_BY_KEY: DIM_BY_KEY,
    UNKNOWN: UNKNOWN, FEW: FEW,
    seen: seen, stages: stages, strays: strays, opensOnRecord: opensOnRecord,
    connectsSpent: connectsSpent, money: money,
    periodBounds: periodBounds, inPeriod: inPeriod,
    split: split, fisher: fisher, signal: signal, median: median, byMonth: byMonth,
  };
});
