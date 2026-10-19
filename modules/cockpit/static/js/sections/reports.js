// ═══════════════════════════════════════════════════════════════════════════
// sections/reports.js — what gets sent to a client, and the hours behind it.
//
// WHY THIS IS ITS OWN TAB (2026-10-19). Hours shipped first and landed inside
// Clients, and his reaction was the right one: "wasn't the main goal to
// generate EOW report — currently i only see hours". The report was built but
// sat three clicks deep behind a drill, which is the same as not being built.
// The thing you come here to do is now the thing on the page.
//
//   · EOW report   the five sections he already sends — shipped, caught before
//                  you noticed, hours, in progress, needs your input — rendered
//                  as markdown he can paste into mail or Slack. NOT sent from
//                  here: drafts-only is a §0 law.
//   · Hours        the overview, per-client periods and logging gaps, moved out
//                  of Clients where they were a fact about a client rather than
//                  a thing you came to do.
// ═══════════════════════════════════════════════════════════════════════════
import { el, clear, fmt, heading, card, kpi, btn, table, emptyState, notice, toast } from "../ui.js";
import { subtabs } from "../subtabs.js";

const h = (v) => (v == null ? "—" : `${Number(v).toFixed(2)} h`);

export async function render(root, ctx) {
  await subtabs(root, {
    section: "reports", ctx,
    tabs: [{ id: "eow", label: "Weekly report" }, { id: "hours", label: "Hours" }],
    views: {
      eow: () => { const host = el("div", "stack"); eow(host, ctx); return host; },
      hours: () => {
        const host = el("div", "stack");
        import("./hours.js").then((m) => m.render(host, ctx));
        return host;
      },
    },
  });
}

async function eow(root, ctx) {
  const ov = await ctx.api("/api/hours");
  const clients = (ov.clients || []).filter((c) => c.period);
  if (!clients.length) {
    root.appendChild(emptyState(
      "No client has a readable period yet — run bin/hours-sync.",
      { icon: "file-text" }));
    return;
  }

  // the picker IS the page: client + period, then the report. No drill, no
  // hunting — the one job this tab exists for is the first thing on it.
  const pick = el("div", "row", []);
  const cSel = el("select", { class: "sel" },
    clients.map((c) => el("option", { value: c.client_id }, c.client)));
  const pSel = el("select", { class: "sel" }, []);
  const out = el("div", "stack");
  pick.append(el("span", "small muted", "Client"), cSel,
    el("span", "small muted", "Period"), pSel,
    btn("Build report", () => build()));
  root.append(card("Weekly client report", el("div", "stack", [
    pick,
    el("div", "small muted",
      "Nothing is sent from here — the report renders for you to send."),
  ])), out);

  async function loadPeriods() {
    const d = await ctx.api("/api/hours/client?client="
      + encodeURIComponent(cSel.value));
    clear(pSel);
    (d.periods || []).slice().reverse().forEach((p) => pSel.appendChild(
      el("option", { value: p.list_id }, `${p.name}`)));
  }
  cSel.addEventListener("change", loadPeriods);
  await loadPeriods();

  async function build() {
    clear(out);
    const q = `client=${encodeURIComponent(cSel.value)}`
      + `&period=${encodeURIComponent(pSel.value)}&fmt=md`;
    const r = await ctx.api("/api/hours/eow?" + q);
    if (r.error) { out.appendChild(notice("bad", r.error)); return; }

    out.appendChild(el("div", "flow", [
      kpi({ label: "Hours", value: h(r.hours.total),
        sub: `${r.period}${r.open ? " · still open" : ""}` }),
      kpi({ label: "Shipped", value: String(r.shipped.length) }),
      kpi({ label: "Alerts caught", value: String(r.caught.total_alerts),
        sub: `${r.caught.scenarios.length} scenarios` }),
      kpi({ label: "Needs their input", value: String(r.needs_input.length) }),
    ]));

    if ((r.empty_sections || []).length) {
      out.appendChild(notice("warn",
        `Empty in this period: ${r.empty_sections.join(", ")}. An empty section`
        + " is stated rather than hidden — a report that quietly drops a"
        + " heading reads as if the work was not done."));
    }

    const sec = (title, rows, columns, empty) => card(title,
      rows.length ? table({ rows, columns }) : emptyState(empty, { sm: true }),
      { sm: true });

    out.append(
      sec("1 · Shipped this week", r.shipped,
        [{ key: "name", label: "" }, { key: "status", label: "Status", cls: "muted" },
          { key: "when", label: "Closed", cls: "muted" }],
        "Nothing closed in this period."),
      sec("2 · Caught before you noticed", r.caught.scenarios,
        [{ key: "scenario", label: "Scenario" },
          { key: "occurrences", label: "Times", num: true },
          { key: "last", label: "Last", cls: "muted" }],
        "No platform alerts reached us this week."),
      sec("4 · In progress / next week", r.in_progress,
        [{ key: "name", label: "" }, { key: "status", label: "Status", cls: "muted" },
          { key: "open_for", label: "Open", cls: "muted" }],
        "Nothing open."),
      sec("5 · Needs your input", r.needs_input,
        [{ key: "what", label: "" }, { key: "why", label: "Why", cls: "muted" },
          { key: "waiting", label: "Waiting", cls: "muted" }],
        "Nothing waiting on them."),
    );

    const ta = el("textarea", { class: "mono", rows: "18",
      style: { width: "100%" } }, r.markdown || "");
    out.appendChild(card("Send this", el("div", "stack", [
      el("div", "small muted", r.basis),
      ta,
      el("div", "row", [
        btn("Copy", async () => {
          try {
            await navigator.clipboard.writeText(ta.value);
            toast("Report copied.");
          } catch (e) { ta.select(); toast("Select-all done — press ⌘C.", true); }
        }),
      ]),
    ])));
  }
}
