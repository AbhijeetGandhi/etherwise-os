// ════════════════════════════════════════════════════════════════════════════
// sections/review.js — "anything that needs my input."
//
// Today's only occupant is the Jarvis mapping queue (v1's Mapping tab), ported
// in full. An event lands here when the sweep collected it but could not say
// with confidence which client it belongs to; until it is resolved, its facts
// are missing from that client's dossier.
//
// THE ONE THING THIS SCREEN MUST NOT UNDERSELL:
//   Assign / Create alias do NOT toggle a local flag. They call
//   modules/jarvis/approve, which WRITES config/clients.yaml and makes an
//   AUDITED GIT COMMIT — the registry learns the sender pattern permanently,
//   and every later sweep, dossier and attribution follows it. So the write is
//   announced before it happens (banner + a two-step confirm on the button)
//   and the resulting commit sha is shown after it does. v1 only console.log'd
//   that commit, which meant the single most consequential action in the whole
//   cockpit gave no visible receipt.
//
// HELD ITEMS stay on screen with the same controls: "collected, but no
//   confident proposal" is a state Abhijeet must act on, and an invisible
//   state cannot be acted on.
//
// NAV BADGE: `badgeCount(queue)` below is the one definition of "items
//   genuinely awaiting Abhijeet" = queued + held. app.js/refreshBadges()
//   currently computes the same sum inline; if what Review contains ever
//   changes, change it HERE and point app.js at this export.
//
// CONTRACT (enforced by app.js): meta + async render(root, ctx). Own nothing
// global. ui.js for primitives, drill.js for drills. `—` = not measured,
// `0` = a real zero that carries a note. No send route, ever.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, fmt, icon, heading, card, pill, btn, linkNum, extLink,
  table, emptyState, notice, expander, toast, copyText, iconBtn,
} from "../ui.js";
import { openDrill } from "../drill.js";

export const meta = { title: "Review", hint: "the things waiting on you" };

/* ── the registry receipt book ────────────────────────────────────────────────
   An Assign makes a GIT COMMIT against config/clients.yaml. The sha of that
   commit used to appear in one place only: a 3.8-second toast, fired
   immediately before ctx.refresh() tore the row it came from off the screen.
   Blink and the audit receipt for the most consequential action in the cockpit
   was gone forever — nothing in the DB records it (approve.py returns the sha
   to its caller and writes it nowhere), so the only other copy is `git log`.

   So every resolve writes a receipt here. localStorage because app.js's section
   contract allows exactly one form of cross-render state (an `ew_cockpit_*`
   key) and a receipt must survive the refresh that follows it. */
const RECEIPTS_KEY = "ew_cockpit_registry_receipts";
const RECEIPTS_MAX = 30;

function readReceipts() {
  try {
    const raw = JSON.parse(localStorage.getItem(RECEIPTS_KEY) || "[]");
    return Array.isArray(raw) ? raw.slice(0, RECEIPTS_MAX) : [];
  } catch (e) {
    return [];                        // corrupt store must not take Review down
  }
}

function addReceipt(entry) {
  const list = readReceipts();
  list.unshift(Object.assign({ at: new Date().toISOString() }, entry));
  try {
    localStorage.setItem(RECEIPTS_KEY, JSON.stringify(list.slice(0, RECEIPTS_MAX)));
  } catch (e) { /* quota: the toast is still shown, we just cannot keep it */ }
  return list;
}

/**
 * The nav badge number: everything on this screen that is genuinely waiting on
 * a human decision. Queued items have a proposal to accept or reject; held
 * items have none and need one. Both block a client's dossier, so both count.
 *
 * Exported so the shell has ONE definition to read instead of re-deriving it:
 *   const q = await api("/api/queue"); navBadges.review = badgeCount(q);
 */
export function badgeCount(q) {
  return ((q && q.queued_total) || 0) + ((q && q.held_total) || 0);
}

/* ── render ───────────────────────────────────────────────────────────────── */

export async function render(root, ctx) {
  const d = await ctx.api("/api/queue");

  root.append(
    heading("Review", "— every event the system could not attribute on its own"),
    statsCard(d, ctx),
  );
  append(root, receiptsCard(ctx));

  const waiting = badgeCount(d);
  if (!waiting) {
    root.appendChild(el("div", "card", emptyState(
      "Queue clear — every collected event is mapped or excluded.",
      {
        icon: "circle-check",
        title: "Nothing needs your input",
        detail: `${fmt.num((d.counts || {}).mapped)} events are mapped to a`
          + ` client and ${fmt.num((d.counts || {}).excluded)} are excluded.`
          + " The next sweep will queue anything it cannot place on its own.",
      })));
    return;
  }

  root.appendChild(writeWarning());

  // groups come from the server already ordered: biggest proposed client first,
  // the unassigned group last (approve.py sorts on proposed_client_id IS NULL).
  (d.groups || []).forEach((g) => root.appendChild(groupCard(g, d, ctx)));

  if (d.held_total) root.appendChild(heldCard(d, ctx));
}

/* ── the stats line (v1's, with every figure that HAS rows now drilling) ──── */

function statsCard(d, ctx) {
  const c = d.counts || {};
  const rec = d.last_sweep;

  const line = el("div", "hstack", [
    el("b", null, [
      // The label matters here, not just the digit: on a clear queue these two
      // are both "0" and the default aria-label made them the SAME button —
      // "show the rows behind 0", twice, doing different things.
      linkNum(fmt.num(d.queued_total), () => drillQueued(d),
        { label: `show the ${fmt.num(d.queued_total)} queued events` }),
      " queued · ",
      linkNum(fmt.num(d.held_total), () => drillHeld(d),
        { label: `show the ${fmt.num(d.held_total)} held events` }),
      " held",
    ]),
    el("span", "muted", "·"),
    el("span", null, [el("b", null, fmt.num(c.mapped)), " mapped"]),
    el("span", null, [el("b", null, fmt.num(c.excluded)), " excluded"]),
    el("span", "muted", "·"),
    el("span", "hstack-tight", ["registry ", shaCode(d.registry_sha,
      "the clients.yaml revision this queue was read against")]),
  ]);

  // the reconciliation verdict of the last sweep: counts that do not reconcile
  // mean the queue on this screen is not the whole truth, so it is loud.
  if (rec) {
    line.append(
      el("span", "muted", "·"),
      el("span", "muted", "last sweep " + (rec.at ? fmt.dateTime(rec.at) : "—") + ":"),
      rec.reconciled === false
        ? pill("⚠ RECONCILE MISMATCH", "fail")
        : pill("reconciled ✓", "ok"),
    );
  }

  const box = card("Mapping queue", [
    line,
    el("div", { class: "muted small", style: { marginTop: "6px" } },
      `total = mapped + queued + held + excluded = ${fmt.num(c.total)}`
      + " · mapped and excluded are counts only — /api/queue returns no rows"
      + " behind them, so they do not drill."),
  ], {
    aside: el("span", "card-sub", `${fmt.num((d.clients || []).length)} clients in the registry`),
  });

  if (rec) box.appendChild(lastSweepDetail(rec, ctx));
  return box;
}

/**
 * A commit/registry sha, with the copy button it needs.
 *
 * A sha is a thing you paste into `git show`. Rendering it as text you have to
 * select by hand — in a monospace run of 12 hex characters, next to other
 * monospace runs — is a receipt you cannot actually take away with you.
 */
function shaCode(sha, why) {
  if (!sha) return el("span", "dash", "—");
  const code = el("code", { attrs: { title: why || "" } }, String(sha));
  const btnEl = iconBtn("copy", `Copy ${sha}`, () => copyText(String(sha)),
    { ghost: true, size: 13 });
  return el("span", "hstack-tight", [code, btnEl]);
}

/**
 * Every registry write this browser has made, newest first — the receipt book.
 * It outlives the refresh that follows an approval, which the toast could not.
 */
function receiptsCard(ctx) {
  const list = readReceipts();
  if (!list.length) return null;

  const rows = list.map((r) => ({
    at: r.at,
    action: r.action,
    client: r.client_name || r.client_id || "—",
    sha: r.commit,
    outcome: r.outcome,
    event: r.event_id,
  }));

  return card("Registry receipts", [
    el("div", "muted small",
      "Every approval you have made in THIS browser, newest first."
      + " An assign that changed config/clients.yaml carries the git commit it"
      + " made; verify any of them with "
      ),
    el("div", { class: "pre mono", style: { margin: "4px 0 var(--space-3)" } },
      "git -C ~/Desktop/Etherwise/etherwise-v3 show <sha>"),
    table({
      columns: [
        { key: "at", label: "When", render: (r) => el("span", "muted", fmt.dateTime(r.at)) },
        { key: "action", label: "Action" },
        { key: "client", label: "Client" },
        {
          key: "sha", label: "Registry commit",
          render: (r) => (r.sha && !String(r.sha).startsWith("commit-failed")
            ? shaCode(r.sha, "the audited commit this approval made")
            : el("span", "muted small", r.outcome || "registry unchanged")),
        },
        { key: "event", label: "Event", cls: "mono small" },
      ],
      rows,
      empty: "No approval has been made from this browser.",
    }),
    el("div", { class: "hstack", style: { marginTop: "var(--space-3)" } }, [
      btn("Clear this list", () => {
        localStorage.removeItem(RECEIPTS_KEY);
        toast("Receipt list cleared — the git commits themselves are untouched.");
        ctx.refresh();
      }, { sm: true }),
      el("span", "muted small",
        "This list lives in this browser only. Clearing it removes the"
        + " convenience copy, never the commits — git is the record."),
    ]),
  ], { aside: el("span", "card-sub", `${fmt.num(list.length)} kept · newest ${RECEIPTS_MAX}`) });
}

/**
 * The last sweep's own metrics blob and run id. Fetched by /api/queue on every
 * load and never displayed until now — it is the fastest way to tell "the queue
 * is empty because nothing arrived" from "the queue is empty because the sweep
 * excluded everything".
 */
function lastSweepDetail(rec, ctx) {
  const m = rec.metrics || {};
  const rows = Object.entries(m).map(([k, v]) => ({
    k,
    v: (v == null) ? null
      : Array.isArray(v) ? (v.length ? JSON.stringify(v) : "none")
        : typeof v === "boolean" ? (v ? "yes" : "no") : v,
  }));

  return expander(`Last sweep — run #${rec.run_id} · ${rows.length} metrics`, (body) => {
    body.append(
      el("div", "muted small",
        `jarvis_sweep run #${rec.run_id}, finished ${fmt.dateTime(rec.at)}.`
        + (rec.reconciled === false
          ? " Its own reconciliation check FAILED: collected ≠ mapped + queued +"
          + " held + excluded, so treat these numbers as suspect."
          : " Its reconciliation check passed: everything it collected is"
          + " accounted for in exactly one state.")),
      el("div", { style: { marginTop: "var(--space-2)" } }, table({
        columns: [
          { key: "k", label: "Metric", cls: "mono" },
          {
            key: "v", label: "Value", num: true,
            render: (r) => (r.v == null ? null
              : typeof r.v === "number" ? fmt.num(r.v) : String(r.v)),
          },
        ],
        rows,
        empty: "That run recorded no metrics.",
      })),
      el("div", { class: "hstack", style: { marginTop: "var(--space-3)" } }, [
        btn("Open this run in Activity", () =>
          ctx.navigate("activity", {
            day: String(rec.at || "").slice(0, 10),
            run: String(rec.run_id),
          }), { sm: true, icon: "arrow-up-right" }),
        el("span", "muted small", "shows the full run: metrics, anomalies, cost."),
      ]),
    );
  });
}

/* ── the permanence warning ───────────────────────────────────────────────── */

function writeWarning() {
  return notice("warn",
    "Assign writes to the client registry — permanently.", {
      children: el("div", { class: "small", style: { marginTop: "4px" } },
        "Assigning an event teaches config/clients.yaml the sender pattern it"
        + " came from and commits that change to git with an audit note. It is"
        + " not a local toggle and there is no undo button here: reversing it"
        + " means another commit. Every future sweep, dossier and attribution"
        + " follows the rule you create. Internal call and Ignore only mark the"
        + " event; they do not touch the registry."),
    });
}

/* ── groups ───────────────────────────────────────────────────────────────── */

function groupCard(g, d, ctx) {
  const unassigned = g.proposed_client_id == null;
  const box = el("div", "card flush");
  box.appendChild(el("div", {
    class: "backlog-ctx between",
  }, [
    el("div", "hstack", [
      icon(unassigned ? "inbox" : "user", { size: 15, class: "muted" }),
      el("strong", null, g.proposed_name || "(unnamed)"),
      pill(`${fmt.num(g.count)} event${g.count === 1 ? "" : "s"}`, "outline"),
    ]),
    unassigned
      ? el("span", "muted small",
        "no confident proposal — pick the client yourself, or exclude it")
      : el("span", "muted small",
        "proposed by the sweep — confirm each one, it is not applied yet"),
  ]));
  (g.items || []).forEach((it) =>
    box.appendChild(itemRow(it, g.proposed_client_id, d, ctx)));
  return box;
}

function heldCard(d, ctx) {
  const box = el("div", "card flush");
  box.appendChild(el("div", "backlog-ctx", [
    el("div", "hstack", [
      icon("clock", { size: 15, class: "muted" }),
      el("strong", null, "Awaiting proposal (held)"),
      pill(fmt.num(d.held_total), "outline"),
    ]),
    el("div", "muted small",
      "Collected, no confident proposal yet — assign now or leave for the next sweep."),
  ]));
  (d.held_items || []).forEach((it) => box.appendChild(itemRow(it, "", d, ctx)));
  return box;
}

/* ── one event, and the four things you can do with it ────────────────────── */

function itemRow(it, defaultClient, d, ctx) {
  const wrap = el("div", "qitem");

  const meta = el("div", "q-meta", [
    pill(it.source || "?", "outline"),
    it.ts ? el("span", null, fmt.dateTime(it.ts)) : null,
    it.confidence != null
      ? pill(`proposal ${(Number(it.confidence) * 100).toFixed(0)}%`,
        Number(it.confidence) >= 0.8 ? "ok" : "warn")
      : el("span", "muted", "no confidence score"),
    it.evidence ? el("span", null, "· " + it.evidence) : null,
  ]);

  const left = el("div", "q-left", [
    el("div", "hstack", [
      el("strong", null, it.author || "?"),
      el("span", "muted", "—"),
      el("span", "grow", it.subject || "(no subject)"),
    ]),
    meta,
    it.preview
      ? el("div", { class: "q-actionnote", style: { whiteSpace: "pre-wrap" } }, it.preview)
      : null,
  ]);

  // ── the client picker + actions ─────────────────────────────────────────
  const select = el("select", {
    class: "input",
    attrs: { "aria-label": "registry client for this event" },
  }, [el("option", { value: "" }, "— pick client —")].concat(
    (d.clients || []).map((cl) => el("option", {
      value: cl.id,
      selected: cl.id === defaultClient,
    }, cl.name))));

  const actions = el("div", "q-actions");
  const ASSIGN_TITLE = "Assign this event to the selected client and teach the"
    + " registry the sender pattern (audited git commit).";

  // Assign is TWO steps on purpose: the first click states what is about to be
  // written, the second does it. A registry commit should not be one stray
  // click away.
  //
  // TWO WAYS THAT GUARANTEE USED TO EVAPORATE, both fixed here:
  //
  //  * ON A REFUSAL. The old order was `clearTimeout(disarm)` and then await —
  //    so when the server answered `{error}` the auto-disarm was already
  //    cancelled and `armed` was still true. The button stayed hot for the rest
  //    of the session and the very next click committed with NO confirm step.
  //    Now arming is one function with one exit, disarm happens BEFORE the
  //    request, and any outcome leaves the button cold.
  //  * ON A DOUBLE-CLICK. There was no in-flight lock, so two fast clicks on
  //    the armed button sent two POSTs and made two git commits. `busy` is
  //    shared by all four controls on the row, because they all resolve the
  //    same event.
  let armed = false, disarm = null, busy = false;

  function setArmed(on) {
    armed = !!on;
    if (disarm) { clearTimeout(disarm); disarm = null; }
    if (armed) {
      const name = select.options[select.selectedIndex].text;
      assign.textContent = `Confirm registry write → ${name}`;
      assign.classList.add("danger");
      assign.classList.remove("primary");
      assign.title = "Writes config/clients.yaml and commits it. Click again to confirm.";
      disarm = setTimeout(() => setArmed(false), 6000);
    } else {
      assign.textContent = "Assign";
      assign.classList.add("primary");
      assign.classList.remove("danger");
      assign.title = ASSIGN_TITLE;
    }
  }

  function lock(on) {
    busy = !!on;
    [assign, internal, ignore, select].forEach((n) => { n.disabled = !!on; });
  }

  async function act(action, extra) {
    if (busy) return;
    lock(true);
    try {
      await resolve(ctx, wrap, it.event_id, action, extra);
    } finally {
      // a success ends in ctx.refresh(), which replaces this node — only
      // re-enable a row that is still on screen.
      if (wrap.isConnected) lock(false);
    }
  }

  const assign = btn("Assign", () => {
    if (busy) return;
    if (!select.value) { toast("Pick a client first.", true); select.focus(); return; }
    if (!armed) { setArmed(true); return; }
    setArmed(false);                 // disarm BEFORE the write, whatever happens
    act("assign", { client_id: select.value });
  }, { variant: "primary", sm: true });
  assign.title = ASSIGN_TITLE;
  // re-picking the client while armed must re-state what is about to be
  // written, or the button says one name and the select holds another
  select.addEventListener("change", () => { if (armed) setArmed(true); });

  // "Internal" is its own action, not Ignore-with-a-note: an internal team call
  // is our own work, kept findable under one canonical reason.
  const internal = btn("Internal call", () => act("internal"), { sm: true });
  internal.title = "Etherwise internal — excluded from client dossiers, but"
    + " recorded as internal:team-call rather than discarded";

  const ignore = btn("Ignore", () =>
    act("ignore", { reason: "not a client" }), { sm: true });
  ignore.title = "Exclude this event as 'not a client'. The registry is not touched.";

  actions.append(select, assign, internal, ignore);
  if (it.permalink) actions.appendChild(extLink(it.permalink, "Open", { class: "btn sm" }));

  wrap.appendChild(el("div", "q-row", [left, actions]));
  return wrap;
}

/**
 * POST the decision, then show the receipt. queue_resolve answers 200 with an
 * {error} body for a refusal (unknown event, unknown client), so a truthy
 * `error` is checked explicitly — an HTTP 200 here does not mean it worked.
 */
async function resolve(ctx, rowNode, eventId, action, extra) {
  const body = Object.assign({ event_id: eventId, action }, extra || {});
  const busy = el("div", { class: "muted small", style: { padding: "var(--space-3) var(--space-5)" } },
    "Applying…");
  rowNode.appendChild(busy);
  try {
    const res = await ctx.post("/api/queue/resolve", body);
    if (res && res.error) {
      busy.remove();
      toast("Resolve failed: " + res.error, true);
      return false;
    }
    // the receipt v1 swallowed into console.log: say whether the registry
    // actually changed, and quote the commit if it did. The toast is the
    // NOTIFICATION; addReceipt() is the copy that survives ctx.refresh().
    const reg = (res && res.registry) || {};
    const failed = reg.commit && String(reg.commit).startsWith("commit-failed");
    let outcome;
    if (reg.commit && !failed) {
      outcome = "registry committed";
      toast(`Assigned · registry committed ${String(reg.commit).slice(0, 7)}`
        + " — the full sha is in Registry receipts above.");
    } else if (failed) {
      outcome = "the registry file changed but git refused: " + reg.commit;
      toast("Assigned, but the git commit FAILED — see Registry receipts above.", true);
    } else if (action === "assign") {
      outcome = reg.note === "no-pattern"
        ? "no reusable sender pattern — registry unchanged"
        : "registry unchanged (" + (reg.note || "no note") + ")";
      toast("Assigned — " + outcome);
    } else {
      outcome = action === "internal" ? "marked internal:team-call" : "excluded";
      toast(action === "internal" ? "Marked internal:team-call" : "Excluded");
    }
    addReceipt({
      action,
      event_id: eventId,
      client_id: (extra || {}).client_id || null,
      client_name: res && res.client_id ? res.client_id : (extra || {}).client_id || null,
      commit: reg.commit || null,
      outcome,
      healed: !!(res && res.heal),
    });
    if (res && res.heal) {
      toast("Recording re-attributed and the dossier recompiled");
    }
    ctx.refresh();
    return true;
  } catch (e) {
    busy.remove();
    toast("Resolve failed: " + String((e && e.message) || e), true);
    return false;
  }
}

/* ── drills ───────────────────────────────────────────────────────────────── */

const itemCols = [
  { key: "ts", label: "When", render: (r) => el("span", "muted", fmt.dateTime(r.ts)) },
  { key: "source", label: "Source" },
  { key: "author", label: "Author" },
  { key: "subject", label: "Subject" },
  {
    key: "confidence", label: "Proposal", num: true,
    render: (r) => (r.confidence == null ? null : (Number(r.confidence) * 100).toFixed(0) + "%"),
  },
  { key: "evidence", label: "Evidence" },
];

function drillQueued(d) {
  const rows = (d.groups || []).flatMap((g) => (g.items || []).map((it) =>
    Object.assign({ proposed: g.proposed_name }, it)));
  openDrill({
    title: "Queued events",
    subtitle: "collected, proposed to a client, waiting for you to confirm",
    rows,
    total: d.queued_total,
    columns: [
      { key: "proposed", label: "Proposed client" },
    ].concat(itemCols),
    time: (r) => r.ts,
    search: (r) => `${r.author || ""} ${r.subject || ""} ${r.proposed || ""}`,
    facets: [{ label: "proposed client", allLabel: "All proposals", of: (r) => r.proposed }],
    emptyText: "Nothing is queued.",
  });
}

function drillHeld(d) {
  openDrill({
    title: "Held events",
    subtitle: "collected, but the sweep had no confident proposal for them",
    rows: d.held_items || [],
    total: d.held_total,
    columns: itemCols,
    time: (r) => r.ts,
    search: (r) => `${r.author || ""} ${r.subject || ""}`,
    facets: [{ label: "source", allLabel: "All sources", of: (r) => r.source }],
    emptyText: "Nothing is held.",
  });
}
