// ════════════════════════════════════════════════════════════════════════════
// sections/review.js — "anything that needs my judgement", as a working queue.
//
// THREE BACKLOGS LIVE HERE. They were three because they arrive through three
// different rails, not because they are three different jobs:
//
//   1. RECORDINGS — Fathom calls and Loom walkthroughs the classifier was only
//      55-75% sure about. Deliberately not auto-attributed. Confirming one
//      re-attributes its source and its facts and recompiles that client's
//      dossier; it does NOT touch clients.yaml (they carry no author, so
//      approve.learned_pattern has nothing to learn — see `learns` below).
//   2. MAIL — queued/held email the sweep could not place. Assigning one DOES
//      write clients.yaml, because a sender address is a reusable pattern.
//   3. UPWORK THREADS — 75 rooms with no client. For most of them "a prospect
//      who never bought" is the CORRECT answer and this screen has to let him
//      say it once, permanently.
//
// THE TWO THINGS THIS SCREEN MUST NOT GET WRONG:
//
//  * PERMANENCE, STATED PER ACTION. Assign / Create client WRITE
//    config/clients.yaml and make an AUDITED GIT COMMIT. Announced before,
//    two-step confirmed, receipted after. But the old screen said that on EVERY
//    row, and for 39 of the 51 queued events it was false — a warning that is
//    wrong four times in five is a warning nobody reads the fifth time. Each
//    row now says what IT will write, from the server's own `learns` field.
//  * A DISMISSAL HAS TO STICK. For events it always did (state='excluded' is a
//    ledger state, and the producing query is state='queued'). For threads
//    there was nowhere to put it, so it now lives in nudge_state under
//    'review:thread:<id>' — cockpit-owned, no migration, and unlike a Done it
//    does not decay, because "not a client" is a fact about identity and a new
//    message does not make a stranger a client. Restore deletes the row.
//
// NAV BADGE: `badgeCount()` reads /api/review/counts — ONE server-side
// definition of "awaiting Abhijeet" across all three backlogs. app.js imports
// it rather than summing anything itself.
//
// CONTRACT (enforced by app.js): meta + async render(root, ctx). Own nothing
// global. ui.js for primitives, drill.js for drills. `—` = not measured,
// `0` = a real zero that carries a note. No send route, ever.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, clear, fmt, icon, heading, card, pill, btn, linkNum, extLink,
  table, emptyState, notice, expander, toast, copyText, iconBtn,
} from "../ui.js";
import { openDrill } from "../drill.js";
import { subtabs } from "../subtabs.js";

export const meta = { title: "Review", hint: "the things waiting on you" };

/* the server caps a batch; the UI chunks to stay under it rather than being
   refused. Kept in sync with actions.QUEUE_BATCH_MAX / REVIEW_BATCH_MAX. */
const EVENT_CHUNK = 60;
const THREAD_CHUNK = 100;
const REGION_KEY = "ew_cockpit_review_region";   // "all" | "events" | "threads"

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
 * The nav badge number: everything on this screen genuinely waiting on a human
 * decision, across all three backlogs.
 *
 * Takes the /api/review/counts payload rather than deriving anything, so the
 * shell, this screen and the server can never hold three opinions. Threads
 * already dismissed are excluded server-side — the badge has to FALL when he
 * clears a backlog or he stops believing it.
 */
export function badgeCount(c) {
  return (c && Number.isFinite(c.total)) ? c.total : 0;
}

/* ── render ───────────────────────────────────────────────────────────────── */

export async function render(root, ctx) {
  const show = ["open", "dismissed", "all"].includes(ctx.params.get("show"))
    ? ctx.params.get("show") : "open";
  let region = ctx.params.get("region") || localStorage.getItem(REGION_KEY) || "all";
  if (!["all", "events", "threads"].includes(region)) region = "all";

  const [q, th, counts] = await Promise.all([
    ctx.api("/api/queue"),
    ctx.api("/api/review/threads?show=" + encodeURIComponent(show)),
    ctx.api("/api/review/counts"),
  ]);

  // REDESIGNED 2026-08-26 in the Upwork tab's style (Abhijeet, 05:20): a
  // sentence on top that says how many things need a name and how old the
  // oldest is; To decide / Decided / Dismissed as views; the per-item line
  // in words ("the system thinks: Hipp Health — 9 in 10 sure") instead of a
  // row of pills. The decisions themselves — assign, internal, ignore, the
  // batch bars, the armed confirm, the receipts — are exactly as they were.
  const total = counts.total || 0;
  const oldest = oldestDays(q);
  root.append(el("div", "up-head", [
    el("div", "hstack", [el("h2", "up-title", "Review"),
      el("span", "muted small", total ? `${fmt.num(total)} waiting` : "nothing waiting")]),
    el("p", "up-lede", [
      total ? [el("b", null, `${fmt.num(total)} thing${total === 1 ? "" : "s"}`), " need a name"]
        : [el("b", null, "Nothing needs your input"), " — every recording, mail and thread is placed"],
      q.queued_total ? [" · ", el("b", null, fmt.num(q.queued_total)), " already come with the system's best guess"] : "",
      oldest != null ? [" · the oldest has waited ", el("b", oldest >= 7 ? "neg" : null, `${oldest} day${oldest === 1 ? "" : "s"}`)] : "",
    ]),
  ]));

  if (th.error) {
    root.appendChild(notice("bad", "Thread queue: " + th.error, {
      children: el("div", { class: "small", style: { marginTop: "4px" } },
        "The filter was rejected, so no thread rows were read. This is NOT"
        + " “no threads” — press Refresh or drop the ?show= parameter."),
    }));
  }

  await subtabs(root, {
    section: "review", ctx,
    tabs: [{ id: "decide", label: "To decide" }, { id: "decided", label: "Decided" }, { id: "dismissed", label: "Dismissed" }],
    badge: (id) => (id === "decide" && total ? el("span", "n hot", String(total))
      : id === "dismissed" && th.dismissed_total ? el("span", "n", String(th.dismissed_total)) : null),
    views: {
      decide: () => {
        const out = el("div", "stack");
        if (!total) {
          out.appendChild(el("div", "card", emptyState(
            "Queue clear — every event is mapped or excluded, and every Upwork thread is attributed or dismissed.",
            { icon: "circle-check", title: "Nothing needs your input",
              detail: `${fmt.num((q.counts || {}).mapped)} events are mapped to a client and`
                + ` ${fmt.num((q.counts || {}).excluded)} are excluded; ${fmt.num(th.attributed_total)}`
                + " Upwork threads carry a client. The next sweep will queue anything it cannot place on its own." })));
          return out;
        }
        out.append(writeWarning(), regionBar(region, q, th, counts, ctx));
        if (region !== "threads") out.appendChild(eventsRegion(q, ctx));
        if (region !== "events") out.appendChild(threadsRegion(th, "open", ctx));
        return out;
      },
      decided: () => el("div", "stack", [statsCard(q, th, counts, ctx), receiptsCard(ctx)]),
      dismissed: async () => {
        const dth = await ctx.api("/api/review/threads?show=dismissed");
        return el("div", "stack", [threadsRegion(dth, "dismissed", ctx)]);
      },
    },
  });
}

/** days since the oldest queued item, from the groups' timestamps (null when unknown) */
function oldestDays(q) {
  let oldest = null;
  (q.groups || []).forEach((g) => (g.items || []).forEach((it) => {
    const t = Date.parse(it.ts || "");
    if (!Number.isNaN(t) && (oldest == null || t < oldest)) oldest = t;
  }));
  if (oldest == null) return null;
  return Math.max(0, Math.floor((Date.now() - oldest) / 864e5));
}

/** "9 in 10 sure" — a confidence in words, never a percentage pill */
function sureWords(c) {
  if (c == null) return "no guess";
  const n = Math.round(Number(c) * 10);
  return n >= 10 ? "as sure as it gets" : `${n} in 10 sure`;
}

/* ── the header: three backlogs, one line each, every figure drilling ─────── */

function statsCard(q, th, counts, ctx) {
  const c = q.counts || {};
  const rec = q.last_sweep;

  const line = el("div", "hstack", [
    el("b", null, [
      // The label matters here, not just the digit: on a clear queue these are
      // all "0" and a default aria-label would make them the SAME button.
      linkNum(fmt.num(q.queued_total), () => drillQueued(q),
        { label: `show the ${fmt.num(q.queued_total)} queued events` }),
      " queued · ",
      linkNum(fmt.num(q.held_total), () => drillHeld(q),
        { label: `show the ${fmt.num(q.held_total)} held events` }),
      " held · ",
      linkNum(fmt.num(th.open_total), () => drillThreads(th),
        { label: `show the ${fmt.num(th.open_total)} unattributed Upwork threads` }),
      " threads",
    ]),
    el("span", "muted", "·"),
    el("span", null, [el("b", null, fmt.num(c.mapped)), " mapped"]),
    el("span", null, [el("b", null, fmt.num(c.excluded)), " excluded"]),
    el("span", null, [el("b", null, fmt.num(th.attributed_total)), " threads attributed"]),
    el("span", "muted", "·"),
    el("span", "hstack-tight", ["registry ", shaCode(q.registry_sha,
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

  const box = card("What is waiting", [
    line,
    el("div", { class: "muted small", style: { marginTop: "6px" } },
      `events: mapped + queued + held + excluded = ${fmt.num(c.total)}`
      + " · mapped and excluded are counts only — /api/queue returns no rows"
      + " behind them, so they do not drill. Upwork threads: "
      + `${fmt.num(th.total)} carry no client, of which `
      + `${fmt.num(th.dismissed_total)} you have already dismissed.`),
    // A REAL ZERO WITH ITS REASON (never a bare 0): every synced contract points
    // at a thread that already has a client, so this is a finding, not a gap.
    el("div", { class: "muted small", style: { marginTop: "4px" } },
      `Of the unattributed threads, ${fmt.num(th.contract_backed)} are backed by`
      + " a contract"
      + (th.contract_backed === 0
        ? " — a true zero: every contract in the ledger already points at an"
        + " attributed thread, so none of this backlog is signed work." : "")
      + `, ${fmt.num(th.money_backed)} match money in the ledger, and `
      + `${fmt.num(th.hinted)} match a registry name the strict matcher missed.`),
  ], {
    aside: el("span", "card-sub",
      `${fmt.num((q.clients || []).length)} clients in the registry`),
  });

  if (th.dismissed_total) {
    box.appendChild(el("div", { class: "hstack", style: { marginTop: "var(--space-3)" } }, [
      btn(`Show the ${fmt.num(th.dismissed_total)} dismissed threads`,
        () => ctx.navigate("review", { show: "dismissed", region: "threads" }),
        { sm: true, icon: "inbox" }),
      el("span", "muted small",
        "A dismissal is stored, not deleted. You can put any of them back."),
    ]));
  }
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
  // pass the BUTTON: without it copyText falls through to a toast, and a toast
  // in the corner is not the confirmation you look for after clicking a tiny
  // icon next to the sha you are about to paste into `git show`.
  const btnEl = iconBtn("copy", `Copy ${sha}`,
    () => copyText(String(sha), btnEl), { ghost: true, size: 13 });
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
    event: r.event_id || r.thread_id,
  }));

  return card("Registry receipts", [
    el("div", "muted small",
      "Every approval you have made in THIS browser, newest first."
      + " An action that changed config/clients.yaml carries the git commit it"
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
        { key: "event", label: "Item", cls: "mono small" },
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

/* ── the permanence warning, stated once and then per row ─────────────────── */

function writeWarning() {
  return notice("warn",
    "Some of these actions write the client registry — permanently.", {
      children: el("div", { class: "small", style: { marginTop: "4px" } },
        "Assign on a MAIL event teaches config/clients.yaml the sender pattern"
        + " and commits it to git with an audit note. Assign on an UPWORK THREAD"
        + " claims that room id for the client, the same way. Create client adds"
        + " a whole new entry. All three are audited commits with no undo button"
        + " here — reversing one means another commit, and every future sweep,"
        + " dossier and attribution follows the rule you create."
        + " Assign on a RECORDING writes no registry at all: it re-files the"
        + " transcript and its facts and recompiles that client's dossier."
        + " Ignore, Internal call and Dismiss never touch the registry."
        + " Each row below says which of these it is."),
    });
}

/* ── region filter ────────────────────────────────────────────────────────── */

function regionBar(region, q, th, counts, ctx) {
  const opts = [
    ["all", "Everything", counts.total],
    ["events", "Recordings & mail", (counts.events_queued || 0) + (counts.events_held || 0)],
    ["threads", "Upwork threads", counts.threads_open],
  ];
  const seg = el("div", { class: "segmented", attrs: { role: "tablist" } },
    opts.map(([id, label, n]) => {
      const b = el("button", {
        class: "segbtn" + (id === region ? " on" : ""), type: "button",
        attrs: { role: "tab", "aria-selected": String(id === region) },
        on: {
          click: () => {
            localStorage.setItem(REGION_KEY, id);
            ctx.navigate("review", Object.assign(
              { region: id }, ctx.params.get("show") ? { show: ctx.params.get("show") } : {}));
          },
        },
      }, [label, " ", el("span", "muted", "(" + fmt.num(n) + ")")]);
      return b;
    }));
  return el("div", { class: "hstack", style: { marginBottom: "var(--space-4)" } }, [
    seg,
    el("span", "muted small",
      "Nothing is hidden by this filter that is not counted in it."),
  ]);
}

/* ── selection: the machinery behind every "select all" ───────────────────── */

/**
 * One group's checkbox state. Deliberately tiny and per-group: a page-wide
 * selection that spans two groups would let one click assign items with
 * different proposed clients to one client, which is the single worst mistake
 * this screen can make (a wrong attribution is permanent).
 */
function makeSelection() {
  const set = new Set();
  const boxes = new Map();
  let onChange = () => {};
  return {
    register(id, box) { boxes.set(id, box); },
    onChange(fn) { onChange = fn; },
    has(id) { return set.has(id); },
    toggle(id, on) { if (on) set.add(id); else set.delete(id); onChange(); },
    ids() { return [...set]; },
    get size() { return set.size; },
    all(on) {
      boxes.forEach((box, id) => {
        box.checked = on;
        if (on) set.add(id); else set.delete(id);
      });
      onChange();
    },
  };
}

function selectBox(sel, id, label) {
  const box = el("input", {
    type: "checkbox", class: "rev-check",
    attrs: { "aria-label": label },
    on: { change: () => sel.toggle(id, box.checked) },
  });
  sel.register(id, box);
  return box;
}

/** chunk a long id list so one click never exceeds the server's batch cap */
function chunk(list, n) {
  const out = [];
  for (let i = 0; i < list.length; i += n) out.push(list.slice(i, i + n));
  return out;
}

/**
 * A two-step button that cannot get stuck armed.
 *
 * TWO WAYS THAT GUARANTEE USED TO EVAPORATE, both fixed here:
 *  * ON A REFUSAL. The old order was `clearTimeout(disarm)` and then await — so
 *    when the server answered {error} the auto-disarm was already cancelled and
 *    `armed` was still true. The button stayed hot for the rest of the session
 *    and the very next click committed with NO confirm step. Now disarm happens
 *    BEFORE the request and any outcome leaves the button cold.
 *  * ON A DOUBLE-CLICK. There was no in-flight lock, so two fast clicks sent two
 *    POSTs and made two git commits. `lock` is shared by every control that
 *    resolves the same rows.
 */
function armedButton(label, armedLabel, run, opts = {}) {
  let armed = false, disarm = null;
  const b = btn(label, () => {
    if (b.disabled) return;
    if (opts.guard && !opts.guard()) return;
    if (!armed) { setArmed(true); return; }
    setArmed(false);
    run();
  }, { variant: opts.variant || "primary", sm: true });
  b.title = opts.title || "";

  function setArmed(on) {
    armed = !!on;
    if (disarm) { clearTimeout(disarm); disarm = null; }
    if (armed) {
      clear(b);
      append(b, typeof armedLabel === "function" ? armedLabel() : armedLabel);
      b.classList.add("danger");
      b.classList.remove("primary");
      b.title = "This writes config/clients.yaml and commits it. Click again to confirm.";
      disarm = setTimeout(() => setArmed(false), 6000);
    } else {
      clear(b);
      append(b, label);
      b.classList.add(opts.variant === "primary" || !opts.variant ? "primary" : opts.variant);
      b.classList.remove("danger");
      b.title = opts.title || "";
    }
  }
  b.rearm = () => { if (armed) setArmed(true); };
  b.disarm = () => setArmed(false);
  return b;
}

/* ══════════════════════════════════════════════════════════════════════════
   REGION 1 — recordings & mail (the events ledger)
   ══════════════════════════════════════════════════════════════════════════ */

function eventsRegion(d, ctx) {
  const wrap = el("div", "stack-4");
  wrap.appendChild(el("div", "rev-region-head", [
    el("h3", null, "Recordings & mail"),
    el("span", "muted small",
      `${fmt.num(d.queued_total)} with a proposal, ${fmt.num(d.held_total)} without.`
      + ` ${fmt.num((d.by_kind || {}).recording)} are recordings (assigning one`
      + " re-files a transcript and recompiles a dossier); "
      + `${fmt.num((d.by_kind || {}).message)} are messages (assigning one can`
      + " teach the registry a sender pattern)."),
  ]));

  if (!d.queued_total && !d.held_total) {
    wrap.appendChild(el("div", "card", emptyState(
      "Nothing collected is waiting to be placed.",
      { icon: "circle-check", title: "Mapping queue clear" })));
    return wrap;
  }

  // groups come from the server already ordered: biggest proposed client first,
  // the unassigned group last (data.queue sorts on proposed_client_id IS NULL).
  (d.groups || []).forEach((g) => wrap.appendChild(eventGroupCard(g, d, ctx)));
  if (d.held_total) wrap.appendChild(heldCard(d, ctx));
  return wrap;
}

function eventGroupCard(g, d, ctx) {
  const unassigned = g.proposed_client_id == null;
  const sel = makeSelection();
  const box = el("div", "card flush");

  const head = el("div", "backlog-ctx between", [
    el("div", "hstack", [
      selectAll(sel, `select all ${g.count} events proposed to ${g.proposed_name}`),
      icon(unassigned ? "inbox" : "user", { size: 15, class: "muted" }),
      el("strong", null, g.proposed_name || "(unnamed)"),
      pill(`${fmt.num(g.count)} event${g.count === 1 ? "" : "s"}`, "outline"),
      g.kinds && g.kinds.length ? pill(g.kinds.join(" + "), "info") : null,
    ]),
    unassigned
      ? el("span", "muted small",
        "no confident proposal — pick the client yourself, or exclude it")
      : el("span", "muted small",
        "proposed by the sweep — confirm each one, it is not applied yet"),
  ]);
  box.appendChild(head);
  box.appendChild(el("div", { class: "muted small", style: { padding: "0 var(--space-5) var(--space-3)" } },
    g.learns_n
      ? `${fmt.num(g.learns_n)} of these would also teach config/clients.yaml a`
        + " sender pattern (audited commit). The rest write no registry."
      : "None of these writes config/clients.yaml — they carry no sender"
        + " pattern to learn. Assign re-files the recording and recompiles the"
        + " dossier."));

  box.appendChild(eventBatchBar(sel, g, d, ctx));
  (g.items || []).forEach((it) =>
    box.appendChild(eventRow(it, g.proposed_client_id, d, ctx, sel)));
  return box;
}

function heldCard(d, ctx) {
  const sel = makeSelection();
  const box = el("div", "card flush");
  box.appendChild(el("div", "backlog-ctx", [
    el("div", "hstack", [
      selectAll(sel, `select all ${d.held_total} held events`),
      icon("clock", { size: 15, class: "muted" }),
      el("strong", null, "Awaiting proposal (held)"),
      pill(fmt.num(d.held_total), "outline"),
    ]),
    el("div", "muted small",
      "Collected, no confident proposal yet — assign now or exclude. Most of"
      + " this is bank, airline and vendor mail: select all, Ignore, done."),
  ]));
  box.appendChild(eventBatchBar(sel, { proposed_client_id: null, count: d.held_total },
    d, ctx));
  (d.held_items || []).forEach((it) =>
    box.appendChild(eventRow(it, "", d, ctx, sel)));
  return box;
}

function selectAll(sel, label) {
  const box = el("input", {
    type: "checkbox", class: "rev-check",
    attrs: { "aria-label": label, title: label },
    on: { change: () => sel.all(box.checked) },
  });
  return box;
}

/** the batch bar: what a select-all can actually do, and what it will write */
function eventBatchBar(sel, g, d, ctx) {
  const bar = el("div", "rev-bar");
  const count = el("span", "rev-count", "none selected");
  const select = clientSelect(d.clients, g.proposed_client_id);
  let busy = false;

  const assign = armedButton("Assign selected",
    () => `Confirm — assign ${sel.size} to ${selectedName(select)}`,
    () => run("assign", { client_id: select.value }), {
      guard: () => {
        if (!sel.size) { toast("Select some events first.", true); return false; }
        if (!select.value) { toast("Pick a client first.", true); select.focus(); return false; }
        return true;
      },
      title: "Assign every selected event to the chosen client. Mail events also"
        + " teach the registry their sender pattern (audited git commit).",
    });
  select.addEventListener("change", () => assign.rearm());

  const internal = btn("Internal call", () => run("internal"), { sm: true });
  internal.title = "Etherwise internal — excluded from client dossiers, but"
    + " recorded as internal:team-call rather than discarded. No registry write.";
  const ignore = btn("Ignore", () => run("ignore", { reason: "not a client" }),
    { sm: true });
  ignore.title = "Exclude every selected event as 'not a client'. Writes"
    + " events.state='excluded' — permanent, and the registry is untouched.";

  const controls = [select, assign, internal, ignore];
  function lock(on) {
    busy = !!on;
    controls.forEach((n) => { n.disabled = !!on; });
  }
  function update() {
    count.textContent = sel.size
      ? `${fmt.num(sel.size)} selected` : "none selected";
    bar.classList.toggle("on", sel.size > 0);
    if (!sel.size) assign.disarm();
  }
  sel.onChange(update);

  async function run(action, extra) {
    if (busy) return;
    if (!sel.size) { toast("Select some events first.", true); return; }
    lock(true);
    const ids = sel.ids();
    const note = el("span", "muted small", `Applying to ${fmt.num(ids.length)}…`);
    bar.appendChild(note);
    let applied = 0, failed = 0, commits = [];
    const errors = [];
    try {
      for (const part of chunk(ids, EVENT_CHUNK)) {
        const res = await ctx.post("/api/queue/batch",
          Object.assign({ event_ids: part, action }, extra || {}));
        if (res && res.error) { errors.push(res.error); failed += part.length; continue; }
        applied += res.applied || 0;
        failed += res.failed || 0;
        (res.commits || []).forEach((c) => { if (!commits.includes(c)) commits.push(c); });
        (res.results || []).forEach((r) => { if (r.error) errors.push(`${r.event_id}: ${r.error}`); });
      }
    } catch (e) {
      errors.push(String((e && e.message) || e));
      failed = ids.length - applied;
    }
    note.remove();
    addReceipt({
      action: action + " ×" + fmt.num(ids.length),
      client_id: (extra || {}).client_id || null,
      client_name: (extra || {}).client_id ? selectedName(select) : null,
      commit: commits[0] || null,
      outcome: commits.length
        ? `${applied} applied · ${commits.length} registry commit(s)`
        : `${applied} applied · registry unchanged`,
      event_id: `${fmt.num(ids.length)} events`,
    });
    if (failed) {
      toast(`${applied} applied, ${failed} FAILED — ${errors[0] || "see console"}`, true);
      console.warn("[cockpit] batch errors", errors);
    } else {
      toast(`${applied} applied` + (commits.length
        ? ` · ${commits.length} registry commit(s), see Registry receipts` : ""));
    }
    ctx.refresh();
  }

  append(bar,
    el("span", "hstack-tight", [icon("list-checks", { size: 14, class: "muted" }), count]),
    el("span", "grow"), select, assign, internal, ignore);
  update();
  return bar;
}

function clientSelect(clients, preferred) {
  return el("select", {
    class: "input", attrs: { "aria-label": "registry client for these events" },
  }, [el("option", { value: "" }, "— pick client —")].concat(
    (clients || []).map((cl) => el("option", {
      value: cl.id, selected: cl.id === preferred,
    }, cl.name))));
}

function selectedName(select) {
  const o = select.options[select.selectedIndex];
  return o ? o.text : "(none)";
}

/* ── one event, its evidence, and the four things you can do with it ──────── */

function eventRow(it, defaultClient, d, ctx, sel) {
  const wrap = el("div", "qitem");

  const what = it.kind === "recording" ? "a recording" : (it.source === "email" ? "an email" : it.source === "slack" ? "a Slack thread" : it.source === "loom" ? "a Loom" : it.source || "an item");
  const guessName = d.clients && defaultClient ? ((d.clients.find((c) => c.client_id === defaultClient) || {}).name || defaultClient) : null;
  const meta = el("div", "q-meta", [
    el("span", null, `${what}${it.ts ? ` · ${fmt.dateTime(it.ts)}` : ""}`),
    el("span", null, [" · the system thinks: ", guessName
      ? [el("b", null, guessName), ` (${sureWords(it.confidence)})`]
      : el("span", "muted", "nobody in particular — pick the client yourself, or say it is not a client")]),
    it.recording && it.recording.facts != null
      ? el("span", "muted", ` · ${fmt.num(it.recording.facts)} facts would go to that dossier`)
      : null,
  ]);

  const left = el("div", "q-left", [
    el("div", "hstack", [
      selectBox(sel, it.event_id, `select ${it.subject || it.event_id}`),
      el("strong", null, it.author || (it.kind === "recording" ? "(recording)" : "?")),
      el("span", "muted", "—"),
      el("span", "grow", it.subject || "(no subject)"),
    ]),
    meta,
    it.evidence
      ? el("div", "rev-why", ["Why this client: ", el("span", null, it.evidence)])
      : null,
    // WHAT AN ASSIGN WOULD WRITE — per row, from the server. This is the line
    // that stops the blanket "assign writes the registry" warning being a lie.
    el("div", { class: "rev-writes " + (it.learns ? "warn" : "") }, it.learns
      ? [icon("alert-triangle", { size: 12 }),
        ` Assign also writes clients.yaml: ${it.learns.field} += ${it.learns.value}`]
      : [icon("info", { size: 12 }),
        " Assign writes no registry — it re-files this item and recompiles the"
        + " client's dossier."]),
    it.excerpt
      ? expander("Show the text", (body) => {
        body.appendChild(el("div", {
          class: "q-draft-body", style: { whiteSpace: "pre-wrap" },
        }, it.excerpt + (it.excerpt_truncated ? "\n… (truncated)" : "")));
      })
      : (it.preview
        ? el("div", { class: "q-actionnote", style: { whiteSpace: "pre-wrap" } }, it.preview)
        : null),
  ]);

  // ── the client picker + actions ─────────────────────────────────────────
  const select = clientSelect(d.clients, defaultClient);
  const actions = el("div", "q-actions");
  const ASSIGN_TITLE = it.learns
    ? "Assign this event and teach the registry the sender pattern"
      + " (audited git commit)."
    : "Assign this event to the selected client. No registry write; the"
      + " recording and its facts are re-filed and the dossier recompiled.";

  let busy = false;
  function lock(on) {
    busy = !!on;
    [assign, internal, ignore, select].forEach((n) => { n.disabled = !!on; });
  }
  async function act(action, extra) {
    if (busy) return;
    lock(true);
    try {
      await resolveOne(ctx, wrap, it.event_id, action, extra);
    } finally {
      // a success ends in ctx.refresh(), which replaces this node — only
      // re-enable a row that is still on screen.
      if (wrap.isConnected) lock(false);
    }
  }

  const assign = armedButton("Assign",
    () => `Confirm → ${selectedName(select)}`,
    () => act("assign", { client_id: select.value }), {
      guard: () => {
        if (busy) return false;
        if (!select.value) { toast("Pick a client first.", true); select.focus(); return false; }
        return true;
      },
      title: ASSIGN_TITLE,
    });
  // re-picking the client while armed must re-state what is about to be
  // written, or the button says one name and the select holds another
  select.addEventListener("change", () => assign.rearm());

  // "Internal" is its own action, not Ignore-with-a-note: an internal team call
  // is our own work, kept findable under one canonical reason.
  const internal = btn("Internal call", () => act("internal"), { sm: true });
  internal.title = "Etherwise internal — excluded from client dossiers, but"
    + " recorded as internal:team-call rather than discarded";

  const ignore = btn("Ignore", () =>
    act("ignore", { reason: "not a client" }), { sm: true });
  ignore.title = "Exclude this event as 'not a client'. Writes"
    + " events.state='excluded' — it will not come back. The registry is not touched.";

  actions.append(select, assign, internal, ignore);
  if (it.permalink) actions.appendChild(extLink(it.permalink, "Open", { class: "btn sm" }));

  wrap.appendChild(el("div", "q-row", [left, actions]));
  return wrap;
}

/**
 * POST one decision, then show the receipt. queue_resolve answers 200 with an
 * {error} body for a refusal (unknown event, unknown client), so a truthy
 * `error` is checked explicitly — an HTTP 200 here does not mean it worked.
 */
async function resolveOne(ctx, rowNode, eventId, action, extra) {
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

/* ══════════════════════════════════════════════════════════════════════════
   REGION 2 — unattributed Upwork threads
   ══════════════════════════════════════════════════════════════════════════ */

function threadsRegion(th, show, ctx) {
  const wrap = el("div", "stack-4");
  const head = el("div", "rev-region-head", [
    el("h3", null, "Upwork threads"),
    el("span", "muted small",
      `${fmt.num(th.total)} rooms carry no client. For most of them "a prospect`
      + ` who never bought" is the right answer — say it once and it stays said.`
      + " Dismissing writes nothing to the registry and can be undone."),
  ]);
  const filter = el("div", "segmented", [
    ["open", `Open (${fmt.num(th.open_total)})`],
    ["dismissed", `Dismissed (${fmt.num(th.dismissed_total)})`],
    ["all", `All (${fmt.num(th.total)})`],
  ].map(([id, label]) => el("button", {
    class: "segbtn" + (id === show ? " on" : ""), type: "button",
    attrs: { "aria-selected": String(id === show) },
    on: { click: () => ctx.navigate("review", { show: id, region: "threads" }) },
  }, label)));
  head.appendChild(filter);
  wrap.appendChild(head);

  if (!(th.groups || []).length) {
    wrap.appendChild(el("div", "card", emptyState(
      show === "dismissed"
        ? "You have not dismissed any threads."
        : "Every Upwork thread is attributed or dismissed.",
      {
        icon: "circle-check",
        title: show === "dismissed" ? "Nothing dismissed" : "Thread queue clear",
        detail: `${fmt.num(th.attributed_total)} threads carry a client;`
          + ` ${fmt.num(th.dismissed_total)} are dismissed as prospects.`,
      })));
    return wrap;
  }

  (th.groups || []).forEach((g) =>
    wrap.appendChild(threadGroupCard(g, th, show, ctx)));
  return wrap;
}

function threadGroupCard(g, th, show, ctx) {
  const sel = makeSelection();
  const box = el("div", "card flush");
  const isHint = g.key.startsWith("hint:");

  box.appendChild(el("div", "backlog-ctx", [
    el("div", "hstack", [
      selectAll(sel, `select all ${g.count} threads in ${g.label}`),
      icon(isHint ? "user" : g.key === "evidence" ? "wallet"
        : g.key === "live" ? "activity" : "inbox", { size: 15, class: "muted" }),
      el("strong", null, g.label),
      pill(`${fmt.num(g.count)} thread${g.count === 1 ? "" : "s"}`, "outline"),
    ]),
    el("div", "muted small", g.why),
  ]));
  box.appendChild(threadBatchBar(sel, g, th, show, ctx));
  (g.items || []).forEach((it) => box.appendChild(threadRow(it, g, th, ctx, sel)));
  return box;
}

function threadBatchBar(sel, g, th, show, ctx) {
  const bar = el("div", "rev-bar");
  const count = el("span", "rev-count", "none selected");
  const select = clientSelect(th.clients, g.client_id);
  const reason = el("input", {
    type: "text", class: "input rev-reason", value: "prospect — never converted",
    attrs: { "aria-label": "dismissal reason", title: "stored with the dismissal" },
  });
  let busy = false;

  const assign = armedButton("Assign selected",
    () => `Confirm — claim ${sel.size} room(s) for ${selectedName(select)}`,
    () => run("assign", { client_id: select.value }), {
      guard: () => {
        if (!sel.size) { toast("Select some threads first.", true); return false; }
        if (!select.value) { toast("Pick a client first.", true); select.focus(); return false; }
        return true;
      },
      title: "Claim every selected room id for the chosen client in"
        + " config/clients.yaml (audited git commit) and set threads.client_id.",
    });
  select.addEventListener("change", () => assign.rearm());

  const dismiss = btn("Not a client", () =>
    run("dismiss", { reason: reason.value }), { sm: true });
  dismiss.title = "Record 'prospect, not a client' for every selected thread."
    + " Stored in nudge_state — it will not come back on its own, and the"
    + " registry is not touched.";
  const restore = btn("Restore", () => run("restore"), { sm: true });
  restore.title = "Put these threads back in the open queue.";

  const controls = [select, assign, dismiss, restore, reason];
  function lock(on) { busy = !!on; controls.forEach((n) => { n.disabled = !!on; }); }
  function update() {
    count.textContent = sel.size ? `${fmt.num(sel.size)} selected` : "none selected";
    bar.classList.toggle("on", sel.size > 0);
    if (!sel.size) assign.disarm();
  }
  sel.onChange(update);

  async function run(action, extra) {
    if (busy) return;
    if (!sel.size) { toast("Select some threads first.", true); return; }
    lock(true);
    const ids = sel.ids();
    const note = el("span", "muted small", `Applying to ${fmt.num(ids.length)}…`);
    bar.appendChild(note);
    let applied = 0, failed = 0, commits = [];
    const errors = [];
    try {
      for (const part of chunk(ids, THREAD_CHUNK)) {
        const res = await ctx.post("/api/review/thread",
          Object.assign({ thread_ids: part, action }, extra || {}));
        if (res && res.error) { errors.push(res.error); failed += part.length; continue; }
        applied += res.applied || 0;
        failed += res.failed || 0;
        (res.commits || []).forEach((c) => { if (!commits.includes(c)) commits.push(c); });
        (res.results || []).forEach((r) => { if (r.error) errors.push(`${r.thread_id}: ${r.error}`); });
      }
    } catch (e) {
      errors.push(String((e && e.message) || e));
      failed = ids.length - applied;
    }
    note.remove();
    if (action === "assign" || commits.length) {
      addReceipt({
        action: action + " ×" + fmt.num(ids.length),
        client_id: (extra || {}).client_id || null,
        client_name: (extra || {}).client_id ? selectedName(select) : null,
        commit: commits[0] || null,
        outcome: commits.length
          ? `${applied} applied · ${commits.length} registry commit(s)`
          : `${applied} applied · registry unchanged`,
        thread_id: `${fmt.num(ids.length)} threads`,
      });
    }
    if (failed) {
      toast(`${applied} applied, ${failed} FAILED — ${errors[0] || "see console"}`, true);
      console.warn("[cockpit] thread batch errors", errors);
    } else if (action === "dismiss") {
      toast(`${applied} recorded as prospects — they will not come back.`);
    } else if (action === "restore") {
      toast(`${applied} put back in the queue.`);
    } else {
      toast(`${applied} claimed` + (commits.length
        ? ` · ${commits.length} registry commit(s), see Registry receipts` : ""));
    }
    ctx.refresh();
  }

  append(bar,
    el("span", "hstack-tight", [icon("list-checks", { size: 14, class: "muted" }), count]),
    el("span", "grow"),
    show === "dismissed" ? restore : null,
    show === "dismissed" ? null : reason,
    show === "dismissed" ? null : dismiss,
    select, assign);
  update();
  return bar;
}

/* ── one thread: everything needed to decide, without leaving the screen ──── */

function threadRow(it, g, th, ctx, sel) {
  const wrap = el("div", "qitem");
  const p = it.proposal || {};

  const meta = el("div", "q-meta", [
    pill(it.room_type === "ONE_ON_ONE" ? "direct message" : "interview", "outline"),
    it.tier ? pill(String(it.tier).toLowerCase(), "tier-" + String(it.tier).toLowerCase()) : null,
    el("span", null, `${fmt.num(it.messages_n)} messages`
      + ` (${fmt.num(it.their_messages_n)} theirs)`),
    it.latest_message_dt
      ? el("span", null, `· last ${fmt.date(it.latest_message_dt)}`
        + (it.age_days != null ? ` · ${fmt.num(it.age_days)}d ago` : ""))
      : el("span", "dash", "· no message dated"),
    it.owed ? pill("you owe the reply", "warn") : null,
    p.status ? pill("proposal " + p.status,
      p.status === "Expired" ? "outline" : "info") : null,
  ]);

  // the hard evidence, inline
  const ev = el("div", "rev-ev");
  if (it.money) {
    ev.appendChild(el("span", "rev-ev-item ok", [
      icon("wallet", { size: 12 }),
      ` ${fmt.usd2(it.money.usd)} of revenue from “${it.money.label}”`
      + ` (${fmt.num(it.money.txns)} rows, matched on “${it.money.matched_on}”)`,
    ]));
  }
  if (it.contract) {
    ev.appendChild(el("span", "rev-ev-item ok", [
      icon("file-text", { size: 12 }),
      ` contract ${it.contract.id} — ${it.contract.title || "(untitled)"}`
      + ` (${it.contract.upwork_status || "?"})`,
    ]));
  }
  if (it.offers_n) {
    ev.appendChild(el("span", "rev-ev-item ok", [
      icon("file-text", { size: 12 }), ` ${fmt.num(it.offers_n)} Upwork offer(s)`]));
  }
  if (it.hint) {
    ev.appendChild(el("span", "rev-ev-item warn", [
      icon("user", { size: 12 }),
      ` registry holds “${it.hint.registry_value}” for ${it.hint.client_name}`
      + ` — matched on “${it.hint.matched_on}”`,
    ]));
  }
  if (p.job_title || p.charge_rate) {
    ev.appendChild(el("span", "rev-ev-item", [
      icon("trending-up", { size: 12 }),
      ` ${p.job_title || "(no title)"}`
      + (p.charge_rate ? ` · ${fmt.usd2(p.charge_rate)} ${p.charge_currency || ""}` : ""),
    ]));
  }
  if (!ev.childNodes.length) {
    ev.appendChild(el("span", "rev-ev-item muted", [
      icon("info", { size: 12 }),
      " no money, no contract, no offer, no registry match — nothing says this"
      + " is a client",
    ]));
  }

  const last = it.last_message;
  const left = el("div", "q-left", [
    el("div", "hstack", [
      selectBox(sel, it.thread_id, `select ${it.room_name || it.thread_id}`),
      el("strong", null, it.room_name || "(unnamed room)"),
      el("span", "muted", "—"),
      el("span", "grow", it.topic || "(no job title)"),
      el("span", "muted small", `score ${fmt.num(it.score)}`),
    ]),
    meta,
    ev,
    last
      ? el("div", "rev-quote", [
        el("span", "muted small",
          `${last.sender_user_name || "them"}, ${fmt.date(last.created_dt)}: `),
        el("span", null, last.excerpt || "(empty message)"),
      ])
      : el("div", "muted small", "They never sent a message in this room."),
    it.dismissed
      ? el("div", "rev-writes", [icon("circle-check", { size: 12 }),
        ` Dismissed ${fmt.dateTime(it.dismissed.at)} — “${it.dismissed.reason || "no reason given"}”`])
      : null,
  ]);

  const select = clientSelect(th.clients, (it.hint || {}).client_id);
  const actions = el("div", "q-actions");
  let busy = false;

  const assign = armedButton("Assign",
    () => `Confirm → ${selectedName(select)}`,
    () => act("assign", { client_id: select.value }), {
      guard: () => {
        if (busy) return false;
        if (!select.value) { toast("Pick a client first.", true); select.focus(); return false; }
        return true;
      },
      title: "Claim this room id for the chosen client in config/clients.yaml"
        + " (audited git commit) and set threads.client_id. It claims THIS ROOM,"
        + " not the person's name.",
    });
  select.addEventListener("change", () => assign.rearm());

  const dismiss = btn("Not a client", () =>
    act("dismiss", { reason: "prospect — never converted" }), { sm: true });
  dismiss.title = "Record this as a prospect who never converted. Stored in"
    + " nudge_state; it will not reappear. No registry write.";
  const restore = btn("Restore", () => act("restore"), { sm: true });
  restore.title = "Put this thread back in the open queue.";
  const create = btn("New client…", () => openCreate(), { sm: true });
  create.title = "Add a NEW entry to config/clients.yaml for this counterparty"
    + " — the largest write in the cockpit. One thread at a time.";

  const controls = [select, assign, it.dismissed ? restore : dismiss, create];
  function lock(on) { busy = !!on; controls.forEach((n) => { n.disabled = !!on; }); }

  async function act(action, extra) {
    if (busy) return;
    lock(true);
    const note = el("div", { class: "muted small", style: { padding: "0 var(--space-5) var(--space-3)" } },
      "Applying…");
    wrap.appendChild(note);
    try {
      const res = await ctx.post("/api/review/thread",
        Object.assign({ thread_ids: [it.thread_id], action }, extra || {}));
      const err = (res && res.error)
        || ((res.results || []).find((r) => r.error) || {}).error;
      if (err) { note.remove(); toast("Failed: " + err, true); return; }
      const commit = (res.commits || [])[0]
        || (((res.results || [])[0] || {}).registry || {}).commit;
      if (action === "assign" || action === "create_client") {
        addReceipt({
          action, thread_id: it.thread_id,
          client_id: (extra || {}).client_id
            || ((res.created || {}).client_id) || null,
          client_name: (res.created || {}).name || selectedName(select),
          commit: commit || null,
          outcome: commit ? "registry committed" : "registry unchanged",
        });
        toast(commit
          ? `Claimed · registry committed ${String(commit).slice(0, 7)} — full sha in Registry receipts.`
          : "Claimed — the registry already held this room.");
      } else if (action === "dismiss") {
        toast("Recorded as a prospect. It will not come back.");
      } else {
        toast("Put back in the queue.");
      }
      ctx.refresh();
    } catch (e) {
      note.remove();
      toast("Failed: " + String((e && e.message) || e), true);
    } finally {
      if (wrap.isConnected) lock(false);
    }
  }

  /** the new-registry-entry form: opens in place, nothing is pre-filled that
      would be committed without being read. */
  function openCreate() {
    if (wrap.querySelector(".rev-create")) return;
    const suggested = String(it.room_name || "").split(",")[0].trim();
    const idIn = el("input", {
      type: "text", class: "input", value: slugify(suggested),
      attrs: { "aria-label": "new client id (slug)", placeholder: "client-slug" },
    });
    const nameIn = el("input", {
      type: "text", class: "input", value: suggested,
      attrs: { "aria-label": "new client display name", placeholder: "Display Name" },
    });
    const kindIn = el("select", { class: "input", attrs: { "aria-label": "kind" } }, [
      el("option", { value: "prospect" }, "prospect — keeps context, not counted as revenue"),
      el("option", { value: "client" }, "client — counted in the economics roster"),
    ]);
    const go = armedButton("Create & claim",
      () => `Confirm — write ${idIn.value || "?"} into clients.yaml`,
      async () => {
        lock(true);
        try {
          const res = await ctx.post("/api/review/thread", {
            thread_ids: [it.thread_id], action: "create_client",
            new_client: { id: idIn.value.trim(), name: nameIn.value.trim(), kind: kindIn.value },
          });
          const err = (res && res.error)
            || ((res.results || []).find((r) => r.error) || {}).error;
          if (err) { toast("Create failed: " + err, true); return; }
          addReceipt({
            action: "create_client", thread_id: it.thread_id,
            client_id: (res.created || {}).client_id,
            client_name: (res.created || {}).name,
            commit: (res.commits || [])[0] || null,
            outcome: "new registry entry",
          });
          toast(`Created ${(res.created || {}).name} and claimed this room.`);
          ctx.refresh();
        } catch (e) {
          toast("Create failed: " + String((e && e.message) || e), true);
        } finally {
          if (wrap.isConnected) lock(false);
        }
      }, {
        guard: () => {
          if (!idIn.value.trim() || !nameIn.value.trim()) {
            toast("An id and a name are both required.", true); return false;
          }
          return true;
        },
        title: "Writes a NEW client into config/clients.yaml and commits it.",
      });
    [idIn, nameIn, kindIn].forEach((n) =>
      n.addEventListener("change", () => go.rearm()));

    const form = el("div", "rev-create", [
      el("div", "muted small",
        "This adds a whole new identity to the registry — the biggest write in"
        + " the cockpit. It claims this room only; the evidence line (room id,"
        + " room name, message count, proposal id) goes into the commit."
        + " If this counterparty is already in the registry under another name,"
        + " use Assign instead."),
      el("div", "field-row", [
        el("label", "muted small", "id"), idIn,
        el("label", "muted small", "name"), nameIn,
        kindIn, go,
        btn("Cancel", () => form.remove(), { sm: true }),
      ]),
    ]);
    wrap.appendChild(form);
    idIn.focus();
  }

  actions.append(select, assign, it.dismissed ? restore : dismiss, create);
  if (it.permalink) actions.appendChild(extLink(it.permalink, "Open room", { class: "btn sm" }));
  wrap.appendChild(el("div", "q-row", [left, actions]));
  return wrap;
}

function slugify(s) {
  return String(s || "").toLowerCase().normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
}

/* ── drills ───────────────────────────────────────────────────────────────── */

const itemCols = [
  { key: "ts", label: "When", render: (r) => el("span", "muted", fmt.dateTime(r.ts)) },
  { key: "source", label: "Source" },
  { key: "kind", label: "Kind" },
  { key: "author", label: "Author" },
  { key: "subject", label: "Subject" },
  {
    key: "confidence", label: "Proposal", num: true,
    render: (r) => (r.confidence == null ? null : (Number(r.confidence) * 100).toFixed(0) + "%"),
  },
  {
    key: "learns", label: "Registry write on assign",
    render: (r) => (r.learns
      ? el("code", null, `${r.learns.field} += ${r.learns.value}`)
      : el("span", "muted small", "none")),
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
    facets: [
      { label: "proposed client", allLabel: "All proposals", of: (r) => r.proposed },
      { label: "kind", allLabel: "All kinds", of: (r) => r.kind },
    ],
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

function drillThreads(d) {
  const rows = (d.groups || []).flatMap((g) => (g.items || []).map((it) =>
    Object.assign({ group: g.label }, it)));
  openDrill({
    title: "Unattributed Upwork threads",
    subtitle: `${fmt.num(d.open_total)} awaiting a decision`
      + ` · ${fmt.num(d.dismissed_total)} already dismissed`
      + ` · ${fmt.num(d.attributed_total)} threads DO carry a client`,
    rows,
    total: d.shown_total,
    columns: [
      { key: "group", label: "Group" },
      { key: "room_name", label: "Room" },
      { key: "topic", label: "Job" },
      { key: "messages_n", label: "Msgs", num: true, render: (r) => fmt.num(r.messages_n) },
      {
        key: "latest_message_dt", label: "Last message",
        render: (r) => el("span", "muted", fmt.date(r.latest_message_dt)),
      },
      {
        key: "money", label: "Ledger match",
        render: (r) => (r.money ? `${fmt.usd2(r.money.usd)} (${r.money.label})` : null),
      },
      {
        key: "hint", label: "Registry near-match",
        render: (r) => (r.hint ? r.hint.client_name : null),
      },
      {
        key: "proposal", label: "Proposal",
        render: (r) => ((r.proposal || {}).status || null),
      },
      { key: "score", label: "Score", num: true, render: (r) => fmt.num(r.score) },
    ],
    time: (r) => r.latest_message_dt,
    search: (r) => `${r.room_name || ""} ${r.topic || ""} ${(r.counterparties || []).join(" ")}`,
    facets: [{ label: "group", allLabel: "All groups", of: (r) => r.group }],
    emptyText: "No unattributed threads.",
  });
}
