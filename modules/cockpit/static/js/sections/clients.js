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
//   · the linkage note   contracts and owed replies are matched exactly: by
//                        the conversation's client (the registry's), else by
//                        a room-name token, as written or as the registry
//                        names it. Unmatched ones are counted, never
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
import { subtabs } from "../subtabs.js";
import { openDossier } from "./knowledge.js";

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
// A client row carries the registry's name and, since 2026-10-08, the ways
// the ledger spelled it ("aka": Homestyling was paid as "Tomas Eklund"). An
// Airtable record may sit under either, so both are tried — still exact.
function matchRoster(c, index) {
  const names = typeof c === "string" ? [c] : [c.name, ...(c.aka || [])];
  for (const name of names) {
    const whole = index.get(normName(name));
    if (whole) return whole;
    for (const part of String(name || "").split(",")) {
      const hit = index.get(normName(part));
      if (hit) return hit;
    }
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

  // THE AIRTABLE ROSTER IS NO LONGER FETCHED HERE (2026-11-01, the cockpit
  // audit). It was merged by exact name and the names did not line up: the
  // Status column read "—" on nearly every row, and the external hop made the
  // first load take ~10 s. config/clients.yaml is the roster; a client's
  // status here is what the ledger says (paying / quiet / ended). The route
  // stays for anything else that reads it; every roster branch below is
  // guarded by `if (roster)` and simply does not draw.
  const roster = null, rosterError = null;
  const rosterIndex = new Map();
  (roster ? roster.clients || [] : []).forEach((x) => {
    if (!rosterIndex.has(normName(x.name))) rosterIndex.set(normName(x.name), x);
  });

  // REDESIGNED 2026-08-26 in the Upwork tab's style (Abhijeet, 05:20): one
  // row per client in words — what they pay, how you talk, whether anything
  // is owed, what the brain knows — under three views: Working with now,
  // Everyone (the audited table, the tiles and the roster reconciliation,
  // unchanged), Needs a look. The dossier opens under the row.
  const dossierIndex = new Map();
  try {
    const k = await ctx.api("/api/knowledge");
    (k.dossiers || []).forEach((x) => dossierIndex.set(normName(x.client_name || x.client_id), x));
  } catch (e) { /* the brain is optional here */ }

  const allClients = d.clients || [];
  const cutoff90 = new Date(Date.now() - 90 * 864e5).toISOString().slice(0, 10);
  const working = allClients.filter((c) => (c.active_contracts || 0) > 0 || (c.last && c.last >= cutoff90));
  const cutoff120 = new Date(Date.now() - 120 * 864e5).toISOString().slice(0, 10);
  const needsLook = allClients.filter((c) => (c.owed_replies || 0) > 0 || c.passthrough
    || ((c.active_contracts || 0) > 0 && c.last && c.last < cutoff120));

  root.append(el("div", "up-head", [
    el("div", "hstack", [el("h2", "up-title", "Clients"), el("span", "muted small", `as of ${d.as_of ? fmt.dateShort(d.as_of) : "?"}`)]),
    el("p", "up-lede", [
      el("b", null, `${fmt.num(working.length)} client${working.length === 1 ? "" : "s"}`), " working with you now · ",
      el("b", null, dash(d.revenue_total_usd, fmt.usd)), ` paid to date by ${fmt.num(allClients.length)}`,
      needsLook.length ? [" · ", el("b", "neg", `${needsLook.length}`), ` need${needsLook.length === 1 ? "s" : ""} a look`] : "",
    ]),
  ]));

  const body = el("div", { class: "stack", style: { gap: "24px" } });

  await subtabs(root, {
    section: "clients", ctx,
    // Hours MOVED OUT to the Reports tab (2026-10-19). It landed here first
    // because hours are a fact about a client — but what he actually comes to
    // do is produce the weekly report, and that had nowhere to live.
    tabs: [{ id: "now", label: "Working with now" }, { id: "all", label: "Everyone" }, { id: "look", label: "Needs a look" }],
    badge: (id) => (id === "now" ? el("span", "n", String(working.length))
      : id === "all" ? el("span", "n", String(allClients.length))
        : needsLook.length ? el("span", "n warn", String(needsLook.length)) : null),
    views: {
      now: () => clientRows(working, "A client is here while a contract is live or money arrived in the last 90 days. Most recent first."),
      all: () => { paint(); return body; },
      look: () => clientRows(needsLook, "Owed a reply, a payment the parser could not attribute, or a contract Upwork still calls live with no payment for four months (probably finished — worth closing).", true),
    },
  });

  /* ── the rows in words ──────────────────────────────────────────────────── */
  function clientRows(list, footnote, why = false) {
    const out = el("div", "stack");
    if (!list.length) {
      out.appendChild(el("div", "card flush", emptyState("Nobody here right now.", { icon: "circle-check" })));
      return out;
    }
    const sorted = list.slice().sort((a, b) => String(b.last || "").localeCompare(String(a.last || "")));
    sorted.forEach((c) => out.appendChild(clientRow(c, why)));
    out.appendChild(el("p", "muted small foot", footnote));
    return out;
  }

  function clientRow(c, why) {
    const initials = String(c.name || "?").split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase();
    const dz = dossierIndex.get(normName(c.name));
    const m = matchRoster(c, rosterIndex);
    const bits = [];
    if ((c.active_contracts || 0) > 0) bits.push(el("span", "pos", `${c.active_contracts === 1 ? "a live contract" : c.active_contracts + " live contracts"}`));
    bits.push(el("span", null, ["paid us ", el("b", null, fmt.usd(c.revenue_usd)), " to date",
      c.last ? [", last ", el("b", null, fmt.dateShort(c.last))] : ""]));
    if ((c.channels || []).length) bits.push(`talks on ${c.channels.join(", ")}`);
    if ((c.aka || []).length) bits.push(el("span", "muted", `also in the ledger as ${c.aka.join(", ")}`));
    if ((c.owed_replies || 0) > 0) bits.push(el("span", "neg", `owed a reply${c.owed_replies > 1 ? ` on ${c.owed_replies} threads` : ""}`));
    if (c.passthrough) bits.push(el("span", "warn", "a payment still to be attributed"));
    if (dz) bits.push(el("span", null, ["knows ", el("b", null, fmt.num(dz.fact_count)), ` facts, compiled ${dz.last_compiled_at ? fmt.dateShort(dz.last_compiled_at) : "never"}`]));
    else bits.push(el("span", "muted", "no dossier yet"));
    if (m && m.status) bits.push(el("span", "muted", `Airtable: ${m.status}`));
    const desc = el("div", "d");
    bits.forEach((b, i) => { if (i) desc.append(" · "); desc.append(b); });
    if (why && (c.active_contracts || 0) > 0 && c.last && c.last < cutoff120) {
      desc.append(el("div", "warn", `A live contract but nothing paid since ${fmt.dateShort(c.last)}.`));
    }
    const host = el("div", { class: "dossier-host", style: { display: "none" } });
    const actions = el("div", "btns", [
      btn("Payments", () => openClient(c.name, c.name, rosterNote(c)), { sm: true, icon: "wallet" }),
      dz ? btn("Open dossier", async () => {
        if (host.style.display === "none") { host.style.display = "block"; await openDossier(dz, ctx, host); }
        else { host.style.display = "none"; clear(host); }
      }, { sm: true, variant: "primary", icon: "book-open" }) : null,
    ]);
    return el("div", "row client-row", [
      el("div", "in", [el("div", "avatar", initials), el("div", null, [el("div", "t", c.name), desc]), actions]),
      host,
    ]);
  }

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

    /* THE FIGURE AND THE ROWS ARE NOW THE SAME SET.
       This tile read "20 of 28" — CONTRACTS — and opened 18 rows, because the
       rows are CLIENTS and two of them carry more than one contract. Nothing
       reachable from here can list a contract individually: /api/clients
       returns a per-client `active_contracts` count and no contract identity,
       so a per-contract drill would have to invent 20 rows out of 18 facts.
       So the FIGURE moved to what the rows actually are. The 20-of-28 contract
       linkage keeps its place as the sub-line — it is the number that says 8
       contracts are still unattributed, and that is worth reading — but it is
       stated as context, not as a drillable figure that cannot open itself. */
    const linked = linkage.contracts_linked, totalC = linkage.contracts_total;
    const withContracts = clients.filter((c) => (c.active_contracts || 0) > 0);
    // "not measured" here means the linkage pass did not run at all. Without
    // that guard a missing linkage block would render 0 clients — a fabricated
    // zero, the one thing this cockpit may not do.
    const linkUnmeasured = linked == null || totalC == null;
    const carried = withContracts.reduce((s, c) => s + (c.active_contracts || 0), 0);
    append(kpis, kpi({
      label: "Clients with a live contract",
      value: linkUnmeasured
        ? null
        : el("span", null, [fmt.num(withContracts.length),
          el("small", null, " of " + fmt.num(clients.length) + " paying")]),
      unmeasured: linkUnmeasured,
      sub: linkUnmeasured
        ? "the contract-linkage pass did not run, so no contract could be"
          + " matched to a payer — this is not a zero"
        : `${fmt.num(linked)} of ${fmt.num(totalC)} active/pending contracts`
          + ` matched a paying client exactly (the conversation's client, or a room-name token)`
          + (totalC > linked
            ? ` · ${fmt.num(totalC - linked)} matched none and are counted,`
              + ` never guessed at`
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
        note: `One row per CLIENT, which is what this figure counts.`
          + ` The ${fmt.num(carried)} contracts they hold between them are the`
          + ` ${fmt.num(linked)} linked contracts in the sub-line — some clients`
          + ` carry more than one, which is why the two numbers differ.`
          + (linked != null && totalC != null && totalC > linked
            ? ` The remaining ${fmt.num(totalC - linked)} of ${fmt.num(totalC)}`
              + ` active contracts matched no paying client exactly (by the`
              + ` conversation's client or a room-name token), so they belong to no row here — they are`
              + ` counted in the sub-line and nowhere else.`
            : ""),
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
        if ((c.aka || []).length) {
          box.appendChild(el("span", { class: "muted small", attrs: { title: "the ledger's own spellings of this client,"
            + " merged by the client list" } }, "also " + c.aka.join(", ")));
        }
        return box;
      } },
    ];
    if (roster) {
      columns.push({ key: "_status", label: "Status", render: (c) => {
        const m = matchRoster(c, rosterIndex);
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
    const m = matchRoster(c, rosterIndex);
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
      const m = matchRoster(c, rosterIndex);
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
        search: (x) => [x.name, ...(x.aka || [])].join(" "),
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
