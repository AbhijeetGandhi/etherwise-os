// ════════════════════════════════════════════════════════════════════════════
// sections/review.js — NOT PORTED YET. Overwrite this whole file.
//
// Read js/sections/today.js first: it is the worked reference for this contract.
// v1 source to port from: modules/cockpit/static/_legacy/RENDERERS.Mapping (app.js v1 ~L2417-2492) + qResolve (~L2399)
//
// The nav badge reads (queued_total + held_total) from /api/queue — app.js already
//   polls it. If you change what Review contains, update refreshBadges() in app.js.
// Held items (collected, no confident proposal) must stay visible with the same
//   controls: a state Abhijeet must act on cannot be invisible.
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

export const meta = { title: "Review", hint: "the things waiting on you" };

export async function render(root, ctx) {
  root.append(
    heading("Review", "— the things waiting on you"),
    el("div", "card", emptyState(
      "This section has not been rebuilt yet.",
      {
        icon: "refresh-cw",
        title: "Review is being ported",
        detail: "The v1 implementation is preserved at"
          + " static/_legacy/app.js and still works; this rebuild ports it"
          + " section by section onto the shared ui/drill/charts modules.",
      })),
  );
}
