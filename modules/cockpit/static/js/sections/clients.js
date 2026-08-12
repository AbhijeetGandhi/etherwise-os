// ════════════════════════════════════════════════════════════════════════════
// sections/clients.js — NOT PORTED YET. Overwrite this whole file.
//
// Read js/sections/today.js first: it is the worked reference for this contract.
// v1 source to port from: modules/cockpit/static/_legacy/RENDERERS.Clients (app.js v1 ~L1285-1383)
//
// DOCTOR GATE applies here too: d.blocked renders the .blocked panel and nothing else.
// Route clients_airtable() — a whole working Airtable roster with status/tags/notes
//   that has never been rendered (brief §4).
// openTxns() becomes openDrill({ amount: r => r.usd, … }).
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

export const meta = { title: "Clients", hint: "revenue economics from the ledger" };

export async function render(root, ctx) {
  root.append(
    heading("Clients", "— revenue economics from the ledger"),
    el("div", "card", emptyState(
      "This section has not been rebuilt yet.",
      {
        icon: "refresh-cw",
        title: "Clients is being ported",
        detail: "The v1 implementation is preserved at"
          + " static/_legacy/app.js and still works; this rebuild ports it"
          + " section by section onto the shared ui/drill/charts modules.",
      })),
  );
}
