// ═══════════════════════════════════════════════════════════════════════════
// sections/clients.js — who actually pays, straight out of the ledger.
//
// WHAT IS ON THIS PAGE, AND WHY:
//
//   · doctor gate        the same hard gate as Money. Client economics are
//                        finance numbers; none of them render on RED.
//   · three KPIs         revenue to date · direct/unlinked · contracts linked.
//   · the parity line    every client total + the unlinked bucket must equal the
//                        P&L revenue TO THE CENT. It is checked live and stated
//                        either way — a silent reconciliation proves nothing.
//   · sort toggle        by revenue (who matters) or by recency (who is live).
//   · the table          revenue, payments, channels, active contracts, replies
//                        owed, last activity — every row drills to its exact
//                        payments.
//   · attribution pending  a passthrough payer (an EOR / payment processor) is
//                        NOT the end client. The pill says the money is real and
//                        the attribution is not finished, instead of quietly
//                        crediting a processor with someone else's revenue.
//   · the Airtable roster  NEW (brief §4). Status / tags / notes have existed in
//                        `clients_airtable()` since M0 with no route to reach
//                        them; /api/clients/airtable now serves it and it is
//                        merged onto the ledger rows BY EXACT NAME. Where the
//                        names do not line up this page SAYS SO and lists both
//                        sides, rather than fuzzy-matching a client's status.
//   · the linkage note   contracts and owed replies are matched by an exact
//                        room-name token. Unmatched ones are counted, never
//                        misassigned.
//
// TWO RULES THIS FILE OBEYS LITERALLY:
//   `—` = not measured · `0` = a real zero, and it says why.
//   Every figure drills, and the drill states the true total.
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, clear, fmt, dash, heading, card, kpi, pill, chip, btn, table,
  emptyState, notice, toast,
} from "../ui.js";
import { openDrill, cols } from "../drill.js";

export const meta = { title: "Clients", hint: "revenue economics from the ledger" };

const SORT_KEY = "ew_cockpit_clients_sort";

const STATUS_TONE = {
  Active: "ok", Paused: "warn", Lead: "info",
  Ended: "outline", Past: "outline", Unknown: "outline",
};

/**
 * The join key. Deaccented, lowercased, punctuation dropped — an EXACT match
 * after normalising, never a fuzzy or partial one. Nothing about a client's
 * status is worth guessing at.
 */
export function normName(s) {
  return String(s == null ? "" : s)
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")   // Adelye === Adélye
    .toLowerCase().replace(/[^a-z0-9]/g, "");
}

/**
 * Ledger names arrive comma-joined when one payer is known by two names
 * ("Joshua Cates, Hero Marketing Agency") — the same shape the server's own
 * contract matcher splits on. Each comma part is still matched exactly.
 */
function matchRoster(name, index) {
  const whole = index.get(normName(name));
  if (whole) return whole;
  for (const part of String(name || "").split(",")) {
    const hit = index.get(normName(part));
    if (hit) return hit;
  }
  return null;
}

export async function render(root, ctx) {
  let sort = localStorage.getItem(SORT_KEY) === "recent" ? "recent" : "revenue";
  let d = await ctx.api("/api/clients?sort=" + sort);

  // ── DOCTOR GATE: on RED this panel is the entire page ──────────────────────
  if (d.blocked) {
    root.append(
      heading("Clients", "— gated"),
      el("div", "blocked", [
        el("h3", null, "Client economics hidden"),
        el("div", null, d.reason || "finance-doctor RED"),
        el("div", { class: "small muted", style: { marginTop: "12px" } },
          "Every figure on this page is a finance figure, so it is behind the"
          + " same gate as Money: nothing is shown from a ledger that does not"
          + " reconcile. Run bin/finance-doctor for the failing invariant."),
      ]),
    );
    return;
  }

  // The roster is an external hop (Airtable, 60s cached server-side). It must
  // never be able to take down the ledger view it annotates.
  let roster = null, rosterError = null;
  try {
    roster = await ctx.api("/api/clients/airtable");
  } catch (e) {
    if (e && e.unauthorized) throw e;
    rosterError = String((e && e.message) || e);
  }
  const rosterIndex = new Map();
  (roster ? roster.clients || [] : []).forEach((x) => {
    if (!rosterIndex.has(normName(x.name))) rosterIndex.set(normName(x.name), x);
  });

  root.appendChild(heading("Clients",
    `— revenue economics from the ledger; every figure drills to its`
    + ` transactions · as of ${d.as_of || "?"}`));

  const body = el("div", { class: "stack", style: { gap: "24px" } });
  root.appendChild(body);

  /* ── the per-client drill ─────────────────────────────────────────────── */
  async function openClient(name, label, extra) {
    let res;
    try {
      res = await ctx.api("/api/clients/transactions?client=" + encodeURIComponent(name));
    } catch (e) {
      toast("Could not read that client's payments: " + String(e.message || e), true);
      return;
    }
    if (res.blocked) {
      toast("finance-doctor went RED — client payments are gated", true);
      return;
    }
    const rows = res.rows || [];
    const cli = Math.round(rows.reduce((s, r) => s + (Number(r.usd) || 0), 0) * 100) / 100;
    const notes = [];
    if (Math.abs(cli - (res.sum_usd || 0)) >= 0.005 || res.total > rows.length) {
      notes.push(`The server totals all ${fmt.num(res.total)} payments at`
        + ` ${fmt.usd2(res.sum_usd)} — that is the figure this drill came from;`
        + ` the sum above covers only the rows rendered.`);
    }
    if (extra) notes.push(extra);

    openDrill({
      title: label || name,
      subtitle: "every reconciled revenue transaction attributed to this payer",
      rows,
      total: res.total == null ? rows.length : res.total,
      amount: (r) => r.usd,
      time: (r) => r.dt,
      search: (r) => `${r.payer || ""} ${r.desc || ""} ${r.source || ""}`,
      facets: [{ label: "source", allLabel: "All sources", of: (r) => r.source }],
      columns: [
        { key: "dt", label: "Date", render: (r) => el("span", "muted", fmt.dateShort(r.dt)) },
        { key: "source", label: "Source", render: (r) => pill(r.source, "outline") },
        { key: "payer", label: "Payer" },
        { key: "desc", label: "Description", cls: "muted" },
        { key: "usd", label: "USD", num: true,
          render: (r) => el("span", r.usd >= 0 ? "pos" : "neg", fmt.usd2(r.usd)) },
      ],
      note: notes.length ? notes.join(" ") : null,
      emptyText: "No payments are attributed to this client.",
    });
  }

  /* ── paint (re-run on the sort toggle; the roster is not re-fetched) ────── */
  function paint() {
    clear(body);

    const clients = d.clients || [];
    const clientSum = Math.round(
      clients.reduce((s, c) => s + (Number(c.revenue_usd) || 0), 0) * 100) / 100;
    const unlinked = d.unlinked || {};
    const linkage = d.linkage || {};
    const parityDelta = Math.round(
      (clientSum + (unlinked.usd || 0) - (d.revenue_total_usd || 0)) * 100) / 100;
    const parityOk = Math.abs(parityDelta) < 0.01;

    /* ── three KPIs ──────────────────────────────────────────────────────── */
    const kpis = el("div", { class: "grid", style: { gap: "16px" } });

    kpis.appendChild(kpi({
      label: "Revenue to date",
      value: dash(d.revenue_total_usd, fmt.usd),
      sub: `${fmt.num(d.count)} paying clients · ${dash(d.attributed_usd, fmt.usd)}`
        + ` attributed to one of them`,
      onClick: async () => {
        let res;
        try {
          res = await ctx.api("/api/money/transactions?bucket=revenue");
        } catch (e) {
          toast("Could not read the revenue rows: " + String(e.message || e), true);
          return;
        }
        openDrill({
          title: "Revenue to date — every reconciled payment",
          subtitle: "the same rows the per-client totals below are cut from",
          rows: res.rows || [],
          total: res.count == null ? (res.rows || []).length : res.count,
          columns: cols.txn({ revenue: "Revenue" }),
          amount: (r) => r.u,
          time: (r) => r.dt,
          search: (r) => `${r.d || ""} ${r.t || ""} ${r.s || ""}`,
          facets: [{ label: "source", allLabel: "All sources", of: (r) => r.s }],
          emptyText: "No revenue on the ledger.",
        });
      },
    }));

    kpis.appendChild(kpi({
      label: "Direct / unlinked",
      value: dash(unlinked.usd, fmt.usd),
      sub: unlinked.payments === 0
        ? "a true zero — every payment on the ledger names a payer we could parse"
        : `${fmt.num(unlinked.payments)} payment${unlinked.payments === 1 ? "" : "s"}`
          + ` · ${unlinked.note || "no parseable payer"}`,
      onClick: () => openClient("__unlinked__", "Direct / unlinked revenue",
        "These payments are real and counted in the total above; what is missing"
        + " is a payer name the parser could attribute, so they belong to no"
        + " client row below."),
    }));

    const linked = linkage.contracts_linked, totalC = linkage.contracts_total;
    const withContracts = clients.filter((c) => (c.active_contracts || 0) > 0);
    const linkUnmeasured = linked == null || totalC == null;
    append(kpis, kpi({
      label: "Contracts linked",
      value: linkUnmeasured
        ? null
        : el("span", null, [fmt.num(linked), el("small", null, " of " + fmt.num(totalC))]),
      unmeasured: linkUnmeasured,
      sub: `active/pending contracts matched to a paying client`
        + (linked != null && totalC != null && totalC > linked
          ? ` · ${fmt.num(totalC - linked)} could not be matched to one and are`
            + ` counted, never guessed at`
          : ""),
      onClick: () => openDrill({
        title: "Clients with an active or pending contract",
        subtitle: `${fmt.num(withContracts.length)} of ${fmt.num(clients.length)}`
          + ` paying clients carry at least one linked contract`,
        rows: withContracts,
        total: withContracts.length,
        amount: (c) => c.revenue_usd,
        columns: [
          { key: "name", label: "Client" },
          { key: "active_contracts", label: "Contracts", num: true },
          { key: "revenue_usd", label: "Revenue to date", num: true,
            render: (c) => fmt.usd2(c.revenue_usd) },
          { key: "last", label: "Last payment",
            render: (c) => el("span", "muted", dash(c.last, fmt.date)) },
        ],
        note: linked != null && totalC != null && totalC > linked
          ? `${fmt.num(totalC - linked)} of the ${fmt.num(totalC)} active contracts`
            + ` matched no paying client by an exact room-name token, so they`
            + ` cannot be listed here — they are counted above and nowhere else.`
          : null,
        emptyText: "No contract is linked to a paying client.",
      }),
    }));
    body.appendChild(kpis);

    /* ── the parity line, stated either way ──────────────────────────────── */
    body.appendChild(parityOk
      ? el("div", "muted small",
          `✓ ${fmt.num(clients.length)} client totals (${fmt.usd2(clientSum)})`
          + ` plus unlinked (${fmt.usd2(unlinked.usd)}) reconcile to the P&L`
          + ` revenue of ${fmt.usd2(d.revenue_total_usd)}, to the cent.`)
      : notice("warn",
          `Client totals do not reconcile to the P&L: ${fmt.usd2(clientSum)}`
          + ` attributed plus ${fmt.usd2(unlinked.usd)} unlinked comes to`
          + ` ${fmt.usd2(clientSum + (unlinked.usd || 0))} against a P&L revenue`
          + ` of ${fmt.usd2(d.revenue_total_usd)} — a gap of`
          + ` ${fmt.usd2(parityDelta)}. Trust bin/finance-pnl, not this table.`));

    /* ── roster availability ─────────────────────────────────────────────── */
    if (rosterError) {
      body.appendChild(notice("warn",
        "The Airtable roster could not be read, so status, tags and notes are"
        + " NOT MEASURED on this page right now — the ledger figures below are"
        + " unaffected. (" + rosterError + ")"));
    }

    /* ── sort toggle ─────────────────────────────────────────────────────── */
    const seg = el("div", "segmented");
    [["revenue", "By revenue"], ["recent", "By recent activity"]].forEach(([k, label]) => {
      seg.appendChild(el("button", {
        class: "segbtn" + (sort === k ? " on" : ""), type: "button",
        attrs: { "aria-pressed": String(sort === k) },
        on: { click: async () => {
          if (sort === k) return;
          sort = k;
          localStorage.setItem(SORT_KEY, k);
          try {
            d = await ctx.api("/api/clients?sort=" + sort);
            paint();
          } catch (e) {
            toast("Could not re-sort: " + String(e.message || e), true);
          }
        } },
      }, label));
    });
    body.appendChild(el("div", "hstack", [
      seg,
      el("span", "muted small", sort === "revenue"
        ? "biggest lifetime payer first"
        : "most recent payment first"),
    ]));

    /* ── the table ───────────────────────────────────────────────────────── */
    const columns = [
      { key: "name", label: "Client", render: (c) => {
        const box = el("div", "hstack-tight");
        box.appendChild(el("button", {
          class: "linkname", type: "button",
          attrs: { "aria-label": `show the payments from ${c.name}` },
          on: { click: () => openClient(c.name, c.name, rosterNote(c)) },
        }, c.name));
        if (c.passthrough) {
          box.appendChild(pill("attribution pending", "warn"));
        }
        return box;
      } },
    ];
    if (roster) {
      columns.push({ key: "_status", label: "Status", render: (c) => {
        const m = matchRoster(c.name, rosterIndex);
        return m ? pill(m.status, STATUS_TONE[m.status] || "outline")
          : el("span", "dash", "—");
      } });
    }
    columns.push(
      { key: "revenue_usd", label: "Revenue to date", num: true, cls: "strong",
        render: (c) => fmt.usd2(c.revenue_usd) },
      { key: "payments", label: "Payments", num: true },
      { key: "channels", label: "Channels", render: (c) => el("span", "hstack-tight",
        (c.channels || []).map((x) => pill(x, "outline"))) },
      // a real 0 is rendered as 0, quietly. Rendering it as "—" would claim the
      // count was never measured, which is the one thing this cockpit may not do.
      { key: "active_contracts", label: "Active", num: true,
        render: (c) => (c.active_contracts == null ? null
          : c.active_contracts ? fmt.num(c.active_contracts) : el("span", "muted", "0")) },
      { key: "owed_replies", label: "Owed", num: true,
        render: (c) => (c.owed_replies == null ? null
          : c.owed_replies ? fmt.num(c.owed_replies) : el("span", "muted", "0")) },
      { key: "last", label: "Last activity",
        render: (c) => el("span", "muted", dash(c.last, fmt.date)) },
    );

    // 80+ payers: scroll the table, not the page. The header is sticky, so the
    // reconciliation panel below stays reachable.
    const tbl = table({
      columns, rows: clients,
      empty: "No client has a reconciled payment on the ledger.",
    });
    tbl.style.maxHeight = "620px";
    body.appendChild(tbl);

    const notes = [];
    if (roster) {
      notes.push("A — in Status means this payer is not in the Airtable roster"
        + " under a name that matches exactly; it does not mean the client has"
        + " no status. See the reconciliation below.");
    }
    notes.push("Active contracts and owed replies show a real 0 where the count"
      + " is genuinely zero — a — there would mean the count was never taken.");
    body.appendChild(el("div", "muted small", notes.join(" ")));

    /* ── the roster reconciliation (brief §4) ────────────────────────────── */
    if (roster) body.appendChild(rosterPanel(clients));

    /* ── the linkage note ────────────────────────────────────────────────── */
    body.appendChild(el("div", "muted small", [
      linkage.note || "",
      linkage.owed_total != null
        ? ` · ${fmt.num(linkage.owed_linked)} of ${fmt.num(linkage.owed_total)}`
          + ` owed replies matched a paying client`
        : "",
      ` · source: ${d.source || "ledger"}.`,
    ]));
  }

  /** the roster context for one client, used as the drill's caveat line */
  function rosterNote(c) {
    if (!roster) return null;
    const m = matchRoster(c.name, rosterIndex);
    if (!m) {
      return "This payer is not in the Airtable roster under a matching name, so"
        + " no status, tags or notes are shown for it — that is a missing link,"
        + " not a missing client.";
    }
    const bits = [`Airtable: ${m.status}`];
    if ((m.tags || []).length) bits.push(`tags ${m.tags.join(", ")}`);
    if (m.first_contract) bits.push(`first contract ${m.first_contract}`);
    if (m.contracts != null) bits.push(`${m.contracts} contract record(s)`);
    if (m.note) bits.push(`“${m.note}”`);
    return bits.join(" · ");
  }

  /* ── the two sides that do not line up, both listed rather than guessed ── */
  function rosterPanel(clients) {
    const matched = [], unmatchedLedger = [];
    clients.forEach((c) => {
      const m = matchRoster(c.name, rosterIndex);
      if (m) matched.push({ client: c, roster: m });
      else unmatchedLedger.push(c);
    });
    const usedKeys = new Set(matched.map((x) => normName(x.roster.name)));
    const rosterOnly = (roster.clients || []).filter((x) => !usedKeys.has(normName(x.name)));

    const c = card("Airtable roster", null, {
      aside: el("span", "card-sub", roster.source || "airtable:Clients"),
    });

    c.appendChild(el("div", "hstack", Object.entries(roster.by_status || {})
      .sort((a, b) => b[1] - a[1])
      .map(([status, n]) => pill(`${status} ${n}`, STATUS_TONE[status] || "outline"))));

    c.appendChild(el("div", { class: "muted small", style: { marginTop: "12px" } },
      `${fmt.num(roster.count)} roster records with status, tags and notes.`
      + ` ${fmt.num(matched.length)} of ${fmt.num(clients.length)} paying clients`
      + ` matched one by exact name. The two lists below are the ones that do`
      + ` NOT line up — they are shown rather than fuzzy-matched, because a`
      + ` guessed status is worse than an absent one.`));

    const strip = el("div", { class: "hstack", style: { marginTop: "12px" } });
    strip.appendChild(btn(
      `${fmt.num(unmatchedLedger.length)} paying clients not in the roster`,
      () => openDrill({
        title: "Paying clients with no matching Airtable record",
        subtitle: "they pay; the roster has no record under a name that matches exactly",
        rows: unmatchedLedger,
        total: unmatchedLedger.length,
        amount: (x) => x.revenue_usd,
        time: (x) => x.last,
        search: (x) => x.name,
        columns: [
          { key: "name", label: "Client (as the ledger names it)" },
          { key: "revenue_usd", label: "Revenue to date", num: true,
            render: (x) => fmt.usd2(x.revenue_usd) },
          { key: "payments", label: "Payments", num: true },
          { key: "last", label: "Last payment",
            render: (x) => el("span", "muted", dash(x.last, fmt.date)) },
        ],
        note: "Many of these are the payer string as it arrived (a job title, a"
          + " contract line, a person's name). Fixing them is a registry job —"
          + " this list is the worklist, not a fault in the figures.",
        emptyText: "Every paying client matched a roster record.",
      }), { sm: true, icon: "users" }));

    strip.appendChild(btn(
      `${fmt.num(rosterOnly.length)} roster records with no payment`,
      () => openDrill({
        title: "Airtable roster records with no reconciled payment",
        subtitle: "in the roster, but nothing on the ledger is attributed to them",
        rows: rosterOnly,
        total: rosterOnly.length,
        search: (x) => `${x.name} ${x.status} ${(x.tags || []).join(" ")}`,
        columns: [
          { key: "name", label: "Client" },
          { key: "status", label: "Status",
            render: (x) => pill(x.status, STATUS_TONE[x.status] || "outline") },
          { key: "tags", label: "Tags",
            render: (x) => ((x.tags || []).length
              ? el("span", "hstack-tight", x.tags.map((t) => chip(t)))
              : el("span", "muted small", "none")) },
          { key: "first_contract", label: "First contract",
            render: (x) => el("span", "muted", dash(x.first_contract, fmt.date)) },
          { key: "transactions", label: "Airtable txns", num: true },
          { key: "note", label: "Note", cls: "muted" },
        ],
        note: "A record here is not necessarily wrong: a lead, an ended client"
          + " paid before the ledger starts, or a payer the ledger names"
          + " differently all land in this list. It is a reconciliation queue.",
        emptyText: "Every roster record matched a paying client.",
      }), { sm: true, icon: "inbox" }));
    c.appendChild(strip);

    if (!matched.length) {
      c.appendChild(emptyState(
        "No roster record matched a paying client by name — the two systems are"
        + " not sharing a naming convention.", { icon: "alert-triangle" }));
    }
    return c;
  }

  paint();
}
