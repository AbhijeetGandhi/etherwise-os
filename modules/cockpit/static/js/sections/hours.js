// ═══════════════════════════════════════════════════════════════════════════
// sections/hours.js — ClickUp time, as a number he can defend.
//
// WHAT IS ON THIS PAGE, AND WHY:
//
//   · as-of, everywhere    hours keep arriving AFTER a period closes. Measured
//                          2026-10-19: 13.6h landed at 03:45-03:52 against a
//                          sprint that ended the day before, and Horizons 43
//                          read 26.05h on 7 Oct and 32.55h twelve days later.
//                          Neither was wrong. A figure without its as-of is the
//                          one that will be wrong, because he will compare a
//                          report he sent last week against this screen today.
//   · overview             every client's current period, against the SAME
//                          POINT in the previous one — comparing a part-done
//                          period to a finished one makes every live period
//                          look like a collapse.
//   · one client's period  by person, by task, by folder. The folder split is
//                          how "70 of those 178 hours were Vibe Coding" stays
//                          answerable without reopening ClickUp.
//   · gaps                 a weekday somebody logged NOTHING. Not thin days:
//                          the failure worth catching is a day never recorded.
//   · report               total billed hours only, unless that client has been
//                          switched on for a breakdown in config. Most clients
//                          get a number.
//
// Every figure drills to the entries behind it and they sum to it to the
// minute — same traceability contract as Money.
// ═══════════════════════════════════════════════════════════════════════════
import {
  el, clear, fmt, dash, card, kpi, pill, btn, table, emptyState, notice, toast,
} from "../ui.js";
import { openDrill } from "../drill.js";

const h = (v) => (v == null ? "—" : `${Number(v).toFixed(2)} h`);

/** The stamp. Rendered the same way on every surface so it reads as one fact
 *  about the data, not as decoration on one card. */
function asOf(stamp) {
  return el("div", "small muted", [
    "hours as of ", el("b", null, stamp || "never synced"),
    " · they keep arriving after a period closes, so this number moves",
  ]);
}

export async function render(root, ctx) {
  const d = await ctx.api("/api/hours");
  if (d.error) { root.appendChild(notice("bad", d.error)); return; }

  const wrap = el("div", { class: "stack", style: { gap: "20px" } });
  wrap.appendChild(asOf(d.as_of));

  // ── overview ──────────────────────────────────────────────────────────────
  const rows = (d.clients || []).filter((c) => c.period);
  const noPeriod = (d.clients || []).filter((c) => !c.period);

  if (!rows.length) {
    wrap.appendChild(emptyState(
      "No client has a parsable sprint period yet. Run bin/hours-sync.",
      { icon: "clock" }));
  } else {
    wrap.appendChild(card("Current period, per client", table({
      rows,
      columns: [
        { key: "client", label: "Client" },
        { key: "period", label: "Period",
          render: (x) => el("span", null, [
            x.period, " ",
            x.open ? pill("open", "warn") : pill("closed", "ok"),
          ]) },
        { key: "hours", label: "Hours", num: true,
          render: (x) => el("b", null, h(x.hours)) },
        { key: "prev", label: "Same point last period", num: true,
          render: (x) => {
            if (!x.previous) return el("span", "muted", "—");
            const was = x.previous.hours_same_point;
            const diff = Math.round((x.hours - was) * 100) / 100;
            return el("span", null, [
              el("span", "muted", h(was)), " ",
              el("span", diff >= 0 ? "pos" : "neg",
                `${diff >= 0 ? "▲" : "▼"} ${Math.abs(diff).toFixed(2)}`),
            ]);
          } },
        { key: "entries", label: "Entries", num: true, cls: "muted" },
      ],
      onRow: (x) => openClient(ctx, x.client_id, null),
      note: d.basis,
    })));
  }

  if (noPeriod.length) {
    wrap.appendChild(notice("warn",
      `${noPeriod.length} client${noPeriod.length === 1 ? "" : "s"} with no`
      + " parsable sprint period: " + noPeriod.map((x) => x.client).join(", ")
      + ". A period that cannot be read is not billed, so it is named here"
      + " rather than quietly skipped."));
  }
  if (d.unattributed && d.unattributed.entries) {
    wrap.appendChild(notice("warn",
      `${d.unattributed.entries} time entries (${h(d.unattributed.hours)})`
      + " belong to no registered client. They are counted nowhere until the"
      + " Space is matched in config/clients.yaml."));
  }

  // ── gaps ──────────────────────────────────────────────────────────────────
  const g = await ctx.api("/api/hours/gaps");
  const gapBody = el("div", "stack");
  if (g.error) {
    gapBody.appendChild(notice("bad", g.error));
  } else if (!(g.people || []).length) {
    gapBody.appendChild(emptyState(
      `Everyone logged something on all ${g.weekdays} weekdays.`,
      { icon: "check" }));
  } else {
    gapBody.appendChild(table({
      rows: g.people,
      columns: [
        { key: "person", label: "Person" },
        { key: "missing_days", label: "Weekdays with nothing logged",
          num: true,
          render: (x) => el("span", x.nothing_all_period ? "neg" : null,
            `${x.missing_days} of ${x.of_weekdays}`) },
        { key: "days", label: "Which",
          render: (x) => el("span", "muted small",
            x.nothing_all_period ? "nothing all period"
              : (x.days || []).join(", ")) },
      ],
      note: `${g.rule}. Window ${g.from} → ${g.to}.`,
    }));
  }
  wrap.appendChild(card("Logging gaps", gapBody));

  clear(root);
  root.appendChild(wrap);
}

/** One client's period — and the report that comes out of it. */
async function openClient(ctx, clientId, listId) {
  const q = `client=${encodeURIComponent(clientId)}`
    + (listId ? `&period=${encodeURIComponent(listId)}` : "");
  const d = await ctx.api("/api/hours/client?" + q);
  if (d.error) { toast(d.error, true); return; }

  const body = el("div", { class: "stack", style: { gap: "16px" } });
  body.appendChild(asOf(d.as_of));
  body.appendChild(el("div", "flow", [
    kpi({ label: "Billed hours", value: h(d.hours),
      sub: `${d.period} · ${d.starts_on} → ${d.ends_on}`
        + (d.open ? " · still open" : "") }),
    kpi({ label: "Entries", value: String(d.entries) }),
  ]));

  const grp = (title, list, why) => {
    if (!(list || []).length) return null;
    return card(title, table({
      rows: list,
      columns: [{ key: "key", label: "" },
        { key: "hours", label: "Hours", num: true,
          render: (x) => h(x.hours) },
        { key: "entries", label: "Entries", num: true, cls: "muted" }],
      note: why,
    }), { sm: true });
  };
  [grp("By person", d.by_person),
    grp("By task", d.by_task),
    grp("By folder", d.by_folder,
      "Work outside the sprint folder is included in the total above — this is"
      + " where that is visible without reopening ClickUp.")]
    .filter(Boolean).forEach((c) => body.appendChild(c));

  const actions = el("div", "field-row", [
    btn("See the entries", async () => {
      const r = await ctx.api("/api/hours/rows?" + q);
      openDrill({
        title: `${d.period} — every entry`,
        subtitle: `${h(r.sum_hours)} across ${r.count} entries; they sum to the`
          + " figure above to the minute",
        rows: r.rows, total: r.count,
        columns: [
          { key: "when", label: "When" },
          { key: "task", label: "Task" },
          { key: "who", label: "Who" },
          { key: "folder", label: "Folder", cls: "muted" },
          { key: "hours", label: "Hours", num: true },
          { key: "logged_at", label: "Logged at", cls: "muted",
            title: "when the entry was WRITTEN — this is how a closed"
              + " period's total can still move" },
        ],
        search: (x) => `${x.task || ""} ${x.who || ""} ${x.folder || ""}`,
        emptyText: "No entries in this period.",
      });
    }),
    btn("Build the report", async () => {
      const r = await ctx.api("/api/hours/report?" + q);
      if (r.error) { toast(r.error, true); return; }
      openDrill({
        title: `Report — ${r.period}`,
        subtitle: `${h(r.total_hours)} · as of ${r.as_of}`,
        rows: (r.by_task || r.by_person || []),
        total: (r.by_task || r.by_person || []).length,
        columns: [{ key: "key", label: "Item" },
          { key: "hours", label: "Hours", num: true,
            render: (x) => h(x.hours) }],
        note: r.note + (r.breakdowns_enabled.length
          ? ` Breakdown shown: ${r.breakdowns_enabled.join(", ")}.`
          : " Total only — no breakdown is enabled for this client."),
        emptyText: "Total billed hours only — no breakdown enabled for this"
          + " client. Switch one on in config when they ask.",
      });
    }),
  ]);
  body.appendChild(actions);

  if ((d.periods || []).length > 1) {
    const sel = el("select", { class: "input",
      attrs: { "aria-label": "period" } },
    d.periods.map((p) => el("option",
      { value: p.list_id, selected: p.name === d.period },
      `${p.name}`)));
    sel.addEventListener("change", () => openClient(ctx, clientId, sel.value));
    body.appendChild(el("div", "rep-controls", [
      el("div", "rep-field", [el("label", null, "Period"), sel])]));
  }

  openDrill({ title: `Hours — ${clientId}`, custom: body,
    rows: [], total: 0, columns: [] });
}
