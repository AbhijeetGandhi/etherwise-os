// ════════════════════════════════════════════════════════════════════════════
// sections/activity.js — NOT PORTED YET. Overwrite this whole file.
//
// Read js/sections/today.js first: it is the worked reference for this contract.
// v1 source to port from: modules/cockpit/static/_legacy/RENDERERS.System (app.js v1 ~L213-363) + the NEW run log
//
// THE HIGHEST-PRIORITY NEW SURFACE (brief §3). Three stacked regions:
//   3a daily digest — plain language, one line per real thing, date picker,
//      'nothing ran' on an empty day. /api/activity?day=YYYY-MM-DD.
//   3b run log — every row in runs, newest first, filterable, click for the full
//      metrics JSON + anomalies + claude_usage + (on failure) error and Re-run
//      (/api/rerun, allowlisted). /api/activity/run?run_id=N.
//   3c the old System content — doctor, source health, spend vs ceilings, shadow
//      ledger, anomalies, and the Run-now buttons (ctx.kick).
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

export const meta = { title: "Activity", hint: "everything the system did, run by run" };

export async function render(root, ctx) {
  root.append(
    heading("Activity", "— everything the system did, run by run"),
    el("div", "card", emptyState(
      "This section has not been rebuilt yet.",
      {
        icon: "refresh-cw",
        title: "Activity is being ported",
        detail: "The v1 implementation is preserved at"
          + " static/_legacy/app.js and still works; this rebuild ports it"
          + " section by section onto the shared ui/drill/charts modules.",
      })),
  );
}
