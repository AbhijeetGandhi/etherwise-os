// ═══════════════════════════════════════════════════════════════════════════
// subtabs.js — one sub-tab bar for every section (2026-08-26).
//
// The Upwork tab's Inbox / Jobs / Health / Analysis bar is what Abhijeet
// liked ("different data views" instead of one long page). This is that bar
// as a helper so Money, Clients, Knowledge, Activity and Review get the same
// one: a remembered view (localStorage), the hash kept honest
// (`#/<section>?view=<id>`) so Refresh and a bookmark land on the same view,
// and a badge slot per tab. Views are builders that return a node (or a
// promise of one); a view that throws renders its error in place, never a
// blank page.
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

import { el, clear } from "./ui.js";

export function remember(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
}
export function recall(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
}

/**
 * subtabs(root, {section, tabs, views, ctx, storageKey, badge})
 *   tabs   [{id, label}]           in display order
 *   views  {id: () => node|Promise} the builders
 *   badge  (id) => node|null       optional count chip per tab
 * Returns {switchTo(id), current()}.
 */
export async function subtabs(root, { section, tabs, views, ctx, storageKey, badge }) {
  const key = storageKey || `ew_cockpit_${section}_view`;
  const wanted = ctx && ctx.params ? ctx.params.get("view") : null;
  let tab = tabs.some((t) => t.id === wanted) ? wanted : recall(key, tabs[0].id);
  if (!tabs.some((t) => t.id === tab)) tab = tabs[0].id;

  const bar = el("div", { class: "subtabs", attrs: { role: "tablist" } });
  const host = el("div", `${section}-host`);
  const buttons = {};
  tabs.forEach((t) => {
    const b = el("button", {
      class: "subtab", type: "button",
      attrs: { role: "tab", "aria-selected": "false", "data-tab": t.id },
      on: { click: () => switchTo(t.id) },
    }, [t.label, badge ? badge(t.id) : null]);
    buttons[t.id] = b;
    bar.appendChild(b);
  });
  root.append(bar, host);

  async function switchTo(id) {
    if (!views[id]) return;
    tab = id;
    remember(key, id);
    Object.entries(buttons).forEach(([k, b]) => b.setAttribute("aria-selected", String(k === id)));
    try { history.replaceState({}, "", `#/${section}?view=${id}`); } catch (e) { /* ignore */ }
    clear(host);
    try {
      const view = await views[id]();
      clear(host).appendChild(view);
    } catch (e) {
      clear(host).appendChild(el("div", "card pad",
        [el("b", null, "This view could not be drawn."), el("div", "muted small", String((e && e.message) || e))]));
    }
  }
  await switchTo(tab);
  return { switchTo, current: () => tab };
}
