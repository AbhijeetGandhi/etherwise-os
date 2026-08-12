// ════════════════════════════════════════════════════════════════════════════
// sections/money.js — NOT PORTED YET. Overwrite this whole file.
//
// Read js/sections/today.js first: it is the worked reference for this contract.
// v1 source to port from: modules/cockpit/static/_legacy/renderMoney (app.js v1 ~L1715-2271) + openDrill v2 (~L1608)
//
// The drill is ALREADY DONE: import { openDrill, cols } from '../drill.js'. Pass
//   amount: r => r.u, time: r => r.dt, search: true and the two facets (bucket, whoOf).
//   Do not re-implement the who-strip — whoOf() is the only piece still to move.
// DOCTOR GATE: on d.blocked render the .blocked panel and NOTHING else.
// SURFACE (brief §4): pnl.runway_months, provisional, connects{this_month_usd,
//   lifetime_usd}, pnl.operating_costs_usd / owner_draw_usd /
//   monthly_operating_profit_usd / uncategorized_count, revenue.last_month_mtd_usd.
// Use the server-side filters on /api/money/transactions instead of pulling the
//   whole ledger and filtering in the browser.
//
// CONTRACT (enforced by app.js):
//   export const meta = { title, hint };
//   export async function render(root, ctx) { … }
//   ctx = { api, post, kick, refresh, navigate, section, params }
//   Own nothing global. Use ui.js for every primitive, drill.js for every
//   drill, charts.js for every chart. `—` = not measured, `0` = a real zero
//   that carries a note. Every figure drills. No send route, ever.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import { el, heading, emptyState } from "../ui.js";

export const meta = { title: "Money", hint: "every figure explains itself, to the cent" };

export async function render(root, ctx) {
  root.append(
    heading("Money", "— every figure explains itself, to the cent"),
    el("div", "card", emptyState(
      "This section has not been rebuilt yet.",
      {
        icon: "refresh-cw",
        title: "Money is being ported",
        detail: "The v1 implementation is preserved at"
          + " static/_legacy/app.js and still works; this rebuild ports it"
          + " section by section onto the shared ui/drill/charts modules.",
      })),
  );
}
