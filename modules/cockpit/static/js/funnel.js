// Pipeline conversion funnel — the ONE place the band topology lives.
//
// The law the UI states is "each band IS a strict subset of the one above". To
// make that true BY CONSTRUCTION (not by hopeful ordering), every band is
// derived by NARROWING the previous band's rows. A rendered step can then never
// exceed its parent, so no conversion step can ever read >100%.
//
// The 131% bug (2026-08-08): the order was Scanned -> Passed -> Scored -> Hot ->
// Carded, but the scanner cards EVERY job scored >= FLOOR, so Carded (228) is a
// subset of SCORED, not of Hot (174) — Carded/Hot = 131%. The fix is the honest
// nesting: Scanned -> Passed -> Scored -> Carded -> (of which) Hot, where Hot is
// the deepest band ("of the carded, how many cleared the hot bar").
//
// Loaded as a plain <script> in the browser (exposes EW_FUNNEL) and require()d
// by the test harness under node (module.exports) — same code, both worlds.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.EW_FUNNEL = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // rows: the loaded pipeline jobs; each has .k (skip reason, truthy = killed by
  // a hard floor), .s (score, may be null), .c (carded / has a ClickUp card).
  function computeFunnel(rows, opts) {
    var FLOOR = (opts && opts.FLOOR) || 8;
    var HOT = (opts && opts.HOT) || 16;
    rows = rows || [];

    var scanned = rows;
    var passed = scanned.filter(function (j) { return !j.k; });
    var scored = passed.filter(function (j) { return j.s != null && j.s >= FLOOR; });
    var carded = scored.filter(function (j) { return j.c; });
    var hot = carded.filter(function (j) { return j.s != null && j.s >= HOT; });

    var steps = [
      { k: "scanned", lbl: "Scanned", n: scanned.length,
        scope: {}, note: "every job the feeds returned" },
      { k: "passed", lbl: "Passed the floors", n: passed.length,
        scope: { skip: "__none__" },
        note: "survived n8n / rate / fixed-budget hard rules" },
      { k: "scored", lbl: "Scored ≥" + FLOOR, n: scored.length,
        scope: { skip: "__none__", score_min: FLOOR },
        note: "the model found real fit" },
      { k: "carded", lbl: "Carded to ClickUp", n: carded.length,
        scope: { skip: "__none__", score_min: FLOOR, carded: "1" },
        note: "the scanner puts every scored≥" + FLOOR + " job on the board" },
      // `skip: "__none__"` is NOT optional here even though it looks redundant.
      // Hot is derived by narrowing CARDED, and carded carries the hard-floor
      // exclusion — so a row that is both carded AND hard-rule-skipped (none
      // exist today; nothing prevents one) would be counted OUT of the band and
      // yet returned BY its drill. A band whose rows outnumber the band is the
      // traceability contract broken. Every scope must restate its parent's
      // predicates, because pipeline_rows() re-derives from filters, not from
      // the parent's row set.
      { k: "hot", lbl: "of which Hot ≥" + HOT, n: hot.length,
        scope: { skip: "__none__", score_min: HOT, carded: "1" },
        note: "of the carded, the ones that cleared the hot bar" },
    ];

    // drop bands sit between a step and its child — only where the failing rows
    // are actually identifiable inside the job table.
    var drops = [
      { after: "scanned", n: scanned.length - passed.length,
        lbl: "killed by the hard floors", scope: { skip: "__any__" } },
      { after: "scored", n: scored.length - carded.length,
        lbl: "scored ≥" + FLOOR + " but never carded (shadow era / expired)",
        scope: { skip: "__none__", score_min: FLOOR, carded: "0" } },
      // same rule as the Hot step above: this drop is a slice of CARDED, so it
      // must restate carded's hard-floor exclusion or its drill can out-count it
      { after: "carded", n: carded.length - hot.length,
        lbl: "carded but scored " + FLOOR + "–" + (HOT - 1) + " — not hot",
        scope: { skip: "__none__", score_min: FLOOR, score_max: HOT, carded: "1" } },
    ];

    return { scanned: scanned.length, steps: steps, drops: drops };
  }

  return { computeFunnel: computeFunnel };
});
