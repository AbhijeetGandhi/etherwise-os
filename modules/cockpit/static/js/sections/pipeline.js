// ════════════════════════════════════════════════════════════════════════════
// sections/pipeline.js — NOT PORTED YET. Overwrite this whole file.
//
// Read js/sections/today.js first: it is the worked reference for this contract.
// v1 source to port from: modules/cockpit/static/_legacy/RENDERERS.Pipeline (app.js v1 ~L580-1284)
//
// Port the KPI strip, the conversion funnel (js/funnel.js is UNCHANGED and unit-tested
//   — import EW_FUNNEL from window, it is loaded as a classic script), the range bar
//   and every chart via charts.js (NOT the v1 hand-rolled divs, and NOT drawBarChart).
// DROP (brief §4): pipeline.to_triage / bands / backlog_uncarded and
//   pipeline_rows(stage='triage') — structurally always 0 since the ClickUp cutover.
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

export const meta = { title: "Pipeline", hint: "the scanner funnel, end to end" };

export async function render(root, ctx) {
  root.append(
    heading("Pipeline", "— the scanner funnel, end to end"),
    el("div", "card", emptyState(
      "This section has not been rebuilt yet.",
      {
        icon: "refresh-cw",
        title: "Pipeline is being ported",
        detail: "The v1 implementation is preserved at"
          + " static/_legacy/app.js and still works; this rebuild ports it"
          + " section by section onto the shared ui/drill/charts modules.",
      })),
  );
}
