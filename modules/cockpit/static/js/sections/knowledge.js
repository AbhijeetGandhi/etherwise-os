// ════════════════════════════════════════════════════════════════════════════
// sections/knowledge.js — NOT PORTED YET. Overwrite this whole file.
//
// Read js/sections/today.js first: it is the worked reference for this contract.
// v1 source to port from: modules/cockpit/static/_legacy/RENDERERS.Knowledge (app.js v1 ~L1447-1566) + factLine/openDossier (~L2273-2295)
//
// Ask-the-librarian gets FIRST-CLASS placement, not a box halfway down (brief §2).
// /api/ask shells out to bin/kb-ask — there is no model call in this process.
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

export const meta = { title: "Knowledge", hint: "the cited client memory" };

export async function render(root, ctx) {
  root.append(
    heading("Knowledge", "— the cited client memory"),
    el("div", "card", emptyState(
      "This section has not been rebuilt yet.",
      {
        icon: "refresh-cw",
        title: "Knowledge is being ported",
        detail: "The v1 implementation is preserved at"
          + " static/_legacy/app.js and still works; this rebuild ports it"
          + " section by section onto the shared ui/drill/charts modules.",
      })),
  );
}
