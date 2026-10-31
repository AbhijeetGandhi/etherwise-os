// ════════════════════════════════════════════════════════════════════════════
// sections/knowledge.js — the cited client memory.
//
// ORDER ON THIS PAGE IS THE ARGUMENT (brief §2):
//
//   1 freshness line   a knowledge base serving old facts is a dead feed, so
//                      its age is stated before anything it says is believed.
//   2 ASK THE LIBRARIAN  first-class, at the top — "a knowledge base where I
//                      can ask any questions" is the thing that was asked for,
//                      and in v1 it was a box halfway down the page.
//   3 four stat cards  what the kb is made of, each drilling to its rows and
//                      each stating the part of itself it CANNOT account for.
//   4 fact search      FTS over cited facts (?q=), rendered as fact lines.
//   5 dossiers         71 of them since the roster fix (2026-08-12), so the
//                      grid is filtered/sorted, not a wall of cards.
//
// THE COST HONESTY RULE (this file's hard-won rule):
//   /api/ask is the ONLY thing in this cockpit that spends money. It shells out
//   to bin/kb-ask (no model call ever runs in the cockpit process), takes up to
//   45 seconds, and bills a Sonnet call every time. So: one in flight at a
//   time, the controls lock while it runs, the elapsed time is visible, and the
//   copy says plainly what it costs. An UNGROUNDED answer is visually muted and
//   labelled — the librarian refusing is a correct outcome, not a failure, and
//   it must never be mistaken for a claim about a client.
//
// CONTRACT (enforced by app.js): meta + async render(root, ctx). Own nothing
// global. ui.js for primitives, drill.js for drills. `—` = not measured,
// `0` = a real zero that carries a note. No send route, ever.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, clear, fmt, dash, icon, heading, kpi, pill, btn, linkNum,
  extLink, emptyState, notice, expander, skeleton, toast,
} from "../ui.js";
import { openDrill } from "../drill.js";
import { subtabs } from "../subtabs.js";

export const meta = { title: "Knowledge", hint: "the cited client memory" };

const GRID_PREVIEW = 12;      // dossier cards shown before "show all"
const FILTER_CAP = 60;        // matches rendered while a filter is typed
const FACT_CAP = 200;         // facts rendered per category / per filter pass

/* ── shared bits ──────────────────────────────────────────────────────────── */

const TRUSTED = new Set(["CONFIRMED", "CROSS-VERIFIED"]);

/**
 * One cited fact. The confidence pill is not decoration: a CONFIRMED fact may
 * go in front of a client, an INFERRED one may not without checking, and the
 * citation is what makes either verifiable.
 */
function factLine(x, showClient) {
  const trusted = TRUSTED.has(x.confidence);
  const head = el("div", "hstack-tight", [
    showClient
      ? (x.client_id
        ? pill(x.client_id, "outline")
        : pill("unattributed", "warn"))
      : null,
    x.category ? el("span", "muted small", x.category) : null,
  ]);
  return el("div", {
    style: {
      padding: "10px 0", borderBottom: "1px solid var(--border)",
    },
  }, [
    head.childNodes.length ? head : null,
    el("div", { style: { margin: "3px 0" } }, [
      x.fact_text || "(no text)",
      " ",
      pill(x.confidence || "UNKNOWN", trusted ? "ok" : "warn"),
    ]),
    el("div", {
      class: "muted small mono",
      style: { overflowWrap: "anywhere" },
    }, x.citation || "(no citation recorded — this fact cannot be verified)"),
  ]);
}

const sourceCols = [
  { key: "occurred_dt", label: "When",
    render: (s) => (s.occurred_dt
      ? el("span", "muted", fmt.date(s.occurred_dt))
      : el("span", { class: "dash", attrs: { title: "this source carries no date" } }, "—")) },
  { key: "source_type", label: "Type", render: (s) => pill(s.source_type, "outline") },
  { key: "title", label: "Title" },
  { key: "source_ref", label: "Reference", cls: "mono",
    render: (s) => (/^https?:/i.test(s.source_ref || "")
      ? extLink(s.source_ref, s.source_ref)
      : s.source_ref) },
];

const dossierCols = [
  { key: "client_name", label: "Client", render: (r) => el("strong", null, r.client_name || r.client_id) },
  { key: "client_id", label: "id", cls: "mono" },
  { key: "fact_count", label: "Facts", num: true, render: (r) => fmt.num(r.fact_count) },
  { key: "source_count", label: "Sources", num: true, render: (r) => fmt.num(r.source_count) },
  { key: "revenue_usd", label: "Revenue", num: true,
    render: (r) => (r.revenue_usd == null
      ? el("span", { class: "dash", attrs: { title: "no ledger revenue is linked to this name" } }, "—")
      : fmt.usd(r.revenue_usd)) },
  { key: "last_compiled_at", label: "Compiled",
    render: (r) => el("span", "muted", String(r.last_compiled_at || "").slice(0, 10) || "—") },
];

// dossierCols, plus the two synthetic BUCKET rows that make the Facts/Sources
// drills sum to their card (statCards). A bucket is not a client: it renders
// muted, and the id column carries what the bucket IS instead of a client_id.
const accountingCols = dossierCols.map((c) => {
  if (c.key === "client_name") {
    return { ...c, render: (r) => (r.bucket
      ? el("em", "muted", r.client_name)
      : el("strong", null, r.client_name || r.client_id)) };
  }
  if (c.key === "client_id") {
    return { ...c, cls: null, render: (r) => (r.bucket
      ? el("span", "muted small", r.why)
      : el("span", "mono", r.client_id)) };
  }
  if (c.key === "fact_count" || c.key === "source_count") {
    return { ...c, render: (r) => (r[c.key] == null
      ? el("span", { class: "dash", attrs: { title: "this bucket is not counted in this column" } }, "—")
      : fmt.num(r[c.key])) };
  }
  return c;
});

/* ── render ───────────────────────────────────────────────────────────────── */

export async function render(root, ctx) {
  const d = await ctx.api("/api/knowledge");

  if (d && d.status === "stub") {
    root.append(
      heading("Knowledge", "— the cited client memory"),
      el("div", "card", emptyState(d.message || "The knowledge module is not live yet.",
        { icon: "book-open" })),
    );
    return;
  }

  const dossiers = d.dossiers || [];
  const totals = d.totals || {};
  const fr = d.freshness || {};

  // REDESIGNED 2026-08-26 in the Upwork tab's style (Abhijeet, 05:20): Ask
  // first (the thing he actually uses), then the dossiers, what came in, and
  // the fact search — four views instead of one page of tiles. Every builder
  // below is unchanged; only where it sits and the sentence on top are new.
  const stale = dossiers.filter((x) => x.stale).length;
  const empty = dossiers.filter((x) => x.empty_reason).length;
  const brainWords = brainSentence(totals, dossiers, fr);
  root.append(el("div", "up-head", [
    el("div", "hstack", [el("h2", "up-title", "Knowledge"),
      el("span", "muted small", fr.last_compiled_at ? `compiled ${fmt.dateTime(fr.last_compiled_at)}` : "")]),
    el("p", "up-lede", brainWords),
  ]));
  // FOUR VIEWS -> TWO (2026-11-01, his call in the cockpit audit). "What came
  // in" was four stat cards — a whole click for one glance — and "Search
  // facts" was one box. Both live on Ask now: the free search under the paid
  // question, the four cards under an expander. An old ?view=camein|search
  // link lands on Ask instead of a blank view.
  await subtabs(root, {
    section: "knowledge", ctx,
    tabs: [{ id: "ask", label: "Ask" }, { id: "dossiers", label: "Dossiers" }],
    badge: (id) => {
      if (id === "dossiers") return el("span", "n" + (stale ? " warn" : ""), stale ? `${stale} stale` : String(dossiers.length));
      return null;
    },
    views: {
      ask: () => el("div", "stack", [
        askCard(d, ctx),
        searchCard(ctx),
        freshnessLine(fr, dossiers),
        expander("What came in — facts, sources, Fathom ingest, proof points",
          (body) => { body.appendChild(statCards(d, dossiers, totals, ctx)); }),
      ]),
      dossiers: () => el("div", "stack", dossierRegion(dossiers, fr, ctx)),
    },
  });
}

/** "18,707 facts about 77 clients · every dossier compiled yesterday · 2 have no facts yet" */
function brainSentence(t, dossiers, fr) {
  const parts = [];
  const facts = t.facts != null ? t.facts : t.fact_count;
  if (facts != null) parts.push(el("b", null, `${fmt.num(facts)} facts`), ` about ${fmt.num(dossiers.length)} clients`);
  else parts.push(el("b", null, `${fmt.num(dossiers.length)} dossiers`));
  const stale = dossiers.filter((x) => x.stale);
  if (stale.length) parts.push(" · ", el("b", "neg", `${stale.length} stale`), ` (oldest ${Math.max(...stale.map((x) => x.stale_days || 0))} days)`);
  else if (dossiers.length) parts.push(" · every dossier is current");
  const empty = dossiers.filter((x) => x.empty_reason);
  if (empty.length) parts.push(" · ", el("b", null, `${empty.length}`), ` ha${empty.length === 1 ? "s" : "ve"} no facts yet: ${empty.slice(0, 3).map((x) => x.client_name).join(", ")}${empty.length > 3 ? "…" : ""}`);
  return parts;
}

/* ── 1. freshness ─────────────────────────────────────────────────────────── */

function freshnessLine(fr, dossiers) {
  const ok = !fr.stale && fr.oldest_days != null;
  const bits = [];
  if (fr.dossiers) {
    bits.push(`${fmt.num(fr.dossiers)} dossiers`);
    bits.push(`${fmt.num(fr.stale || 0)} stale (>${fr.stale_after_days}d)`);
    if (fr.oldest_days != null) bits.push(`oldest recompiled ${fr.oldest_days}d ago`);
    bits.push(fr.kb_daily_last
      ? `kb-daily ran ${fmt.relative(fr.kb_daily_last)} (${fmt.dateTime(fr.kb_daily_last)})`
      : "kb-daily has never completed");
  } else {
    bits.push("no dossier has been compiled yet");
  }

  const empties = dossiers.filter((x) => !x.fact_count).length;
  return el("div", "srcline " + (ok ? "ok" : "bad"), [
    icon(ok ? "circle-check" : "alert-triangle", { size: 15 }),
    el("div", "grow", [
      el("div", null, bits.join(" · ")),
      empties
        ? el("div", { class: "small", style: { fontWeight: "400", marginTop: "3px" } },
          `${fmt.num(empties)} of them hold 0 facts — they are on the roster and`
          + " were compiled, but nothing has been extracted for them yet."
          + " That is a real zero, not a missing read.")
        : null,
    ]),
  ]);
}

/* ── 2. ASK THE LIBRARIAN (first-class, and the only thing here that costs) ── */

function askCard(d, ctx) {
  const dossiers = (d.dossiers || []).slice()
    .sort((a, b) => String(a.client_name || "").localeCompare(String(b.client_name || "")));
  const withFacts = dossiers.filter((x) => x.fact_count);
  const without = dossiers.filter((x) => !x.fact_count);

  // 71 dossiers: an ungrouped flat list is a wall. Grouped + alphabetical, the
  // browser's own type-ahead makes it navigable.
  const sel = el("select", { class: "input", attrs: { "aria-label": "scope the question to a client" } });
  sel.appendChild(el("option", { value: "" },
    `All clients (${fmt.num(dossiers.length)} dossiers)`));
  const group = (label, list) => {
    if (!list.length) return;
    const og = el("optgroup", { attrs: { label: `${label} (${list.length})` } });
    list.forEach((x) => og.appendChild(el("option", { value: x.client_id },
      `${x.client_name || x.client_id} — ${fmt.num(x.fact_count)}`
      + ` fact${x.fact_count === 1 ? "" : "s"}`)));
    sel.appendChild(og);
  };
  group("With facts", withFacts);
  group("No facts yet", without);

  const qi = el("input", {
    class: "input grow", type: "text",
    placeholder: "Ask anything — e.g. what is their stack? what did we promise them?",
    attrs: { "aria-label": "question for the librarian" },
  });
  const ask = btn("Ask", () => run(), { variant: "primary" });
  const out = el("div", { style: { marginTop: "var(--space-4)" } });

  let inFlight = false;

  async function run() {
    // A double-fire here is a double CHARGE. The flag, not just the disabled
    // attribute, is the guard — Enter can outrun a repaint.
    if (inFlight) return;
    const question = qi.value.trim();
    if (!question) {
      // isError, not the success styling: this is a refusal. And put the
      // cursor where the fix is — a toast that says "type a question" while
      // the caret sits nowhere is half an instruction.
      toast("Type a question first — the librarian needs something to answer.", true);
      qi.focus();
      return;
    }

    inFlight = true;
    ask.disabled = true; ask.textContent = "Asking…";
    qi.disabled = true; sel.disabled = true;
    const who = sel.value
      ? (dossiers.find((x) => x.client_id === sel.value) || {}).client_name || sel.value
      : "all clients";

    const elapsed = el("b", "num", "0s");
    clear(out).appendChild(notice("info", [
      `Asking the librarian about ${who}… `,
      elapsed,
      " elapsed.",
    ], {
      children: el("div", { class: "small", style: { marginTop: "4px" } },
        "This runs bin/kb-ask in a child process (no model call happens inside"
        + " the cockpit), retrieves the matching cited facts, and composes an"
        + " answer. It can take up to 45 seconds and it bills a real model"
        + " call — the controls stay locked until it comes back."),
    }));
    const t0 = Date.now();
    const tick = setInterval(() => {
      elapsed.textContent = Math.round((Date.now() - t0) / 1000) + "s";
    }, 1000);

    let a = null, err = null;
    try {
      a = await ctx.post("/api/ask", { question, client: sel.value });
    } catch (e) {
      err = String((e && e.message) || e);
    } finally {
      clearInterval(tick);
      inFlight = false;
      ask.disabled = false; ask.textContent = "Ask";
      qi.disabled = false; sel.disabled = false;
    }

    const took = Math.round((Date.now() - t0) / 1000);
    clear(out);
    if (err) {
      out.appendChild(el("div", "errstate",
        `The librarian did not answer (${took}s): ${err}.`
        + " Nothing was recorded and no answer is shown — an empty panel here is"
        + " the honest outcome, not a blank answer."));
      return;
    }
    out.appendChild(answerBlock(a || {}, { question, who, took }));
  }

  qi.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });

  const c = el("div", "card");
  c.append(
    el("div", "card-head", [
      el("h3", "card-title", "Ask the librarian"),
      el("span", "card-sub", "cited answers only · runs out of process · costs money"),
    ]),
    el("div", { class: "field-row", style: { flexWrap: "wrap" } }, [sel, qi, ask]),
    el("div", { class: "muted small", style: { marginTop: "var(--space-2)" } },
      "Cited answers only — it refuses when the kb is silent."
      + " Each ask is a real model call (bin/kb-ask, up to 45s), so it is"
      + " deliberately one at a time. Scope it to a client and the retrieval"
      + " only looks at that client's facts."),
    out,
  );
  return c;
}

/** the librarian's answer, with the grounded/ungrounded distinction made loud */
function answerBlock(a, meta2) {
  const wrap = el("div");

  if (a.error) {
    wrap.appendChild(el("div", "errstate", String(a.error)));
    return wrap;
  }

  const grounded = !!a.grounded;
  if (!grounded) {
    wrap.appendChild(notice("warn",
      "NOT GROUNDED — the librarian could not answer this from cited facts.", {
        children: el("div", { class: "small", style: { marginTop: "4px" } },
          "This is the correct behaviour, not a failure: it refuses rather than"
          + " guessing. Treat the text below as a refusal, never as a claim"
          + " about the client."),
      }));
  }

  wrap.appendChild(el("div", {
    class: grounded ? "" : "muted",
    style: {
      whiteSpace: "pre-wrap", marginTop: "var(--space-3)",
      padding: "var(--space-4)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      background: grounded ? "var(--muted)" : "none",
      borderStyle: grounded ? "solid" : "dashed",
    },
  }, a.answer || "(the librarian returned no text)"));

  const cites = a.citations || [];
  if (cites.length) {
    wrap.append(
      el("div", { class: "muted small", style: { marginTop: "var(--space-3)" } },
        `${fmt.num(cites.length)} citation${cites.length === 1 ? "" : "s"}`),
      el("div", "stack-2", cites.map((cst) => el("div", {
        class: "mono small",
        style: {
          padding: "6px 10px", border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)", overflowWrap: "anywhere",
        },
      }, String(cst)))),
    );
  } else if (grounded) {
    wrap.appendChild(notice("warn",
      "This answer came back grounded but with NO citations. Do not use it in"
      + " front of a client — a grounded claim without its sources cannot be"
      + " checked."));
  }

  wrap.appendChild(el("div", { class: "muted small", style: { marginTop: "var(--space-3)" } },
    `${dash(a.facts_used, fmt.num)} fact${a.facts_used === 1 ? "" : "s"} retrieved`
    + ` · scope: ${meta2.who} · answered in ${meta2.took}s`
    + (a.facts_used === 0
      ? " — a true zero: the full-text search matched nothing, so no model call"
        + " was even needed"
      : "")));
  return wrap;
}

/* ── 3. the four stat cards ───────────────────────────────────────────────── */

/** the IST calendar day a stored timestamp falls in — the unit Activity's
 *  digest and /api/activity/sources are both keyed on. */
function istDayOf(ts) {
  const s = String(ts || "");
  if (!s) return null;
  const d = new Date(s.includes("T") ? s : s.replace(" ", "T"));
  if (Number.isNaN(+d)) return /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : null;
  return new Date(d.getTime() + 330 * 60000).toISOString().slice(0, 10);
}

/**
 * The note under the Fathom state table: a real way through to the calls.
 *
 * Activity already builds the per-transcript story — one row per source with
 * who was on the call, which client it was filed under and HOW that was
 * decided, what came out of it and what it cost. Knowledge's tile knew that
 * and said so in prose, which left the reader to go and find it. This opens
 * it, in place, for the day the last poll ran. The module is pulled on the
 * click, not on page load: Knowledge does not otherwise need it.
 */
function fathomCallsNote(f, ctx) {
  const base = "/api/knowledge returns the poll's STATE, not the transcripts it"
    + " pulled — those are counted per IST day, not per poll.";
  const day = istDayOf(f.last_poll);
  if (!day) return base + " No poll has completed, so there is no day to open.";
  const n = f.last_saved;
  const go = btn(
    n ? `Show the ${fmt.num(n)} call${n === 1 ? "" : "s"} from that poll`
      : "Show what was ingested that day",
    async (e) => {
      const mod = await import("./activity.js");
      mod.openSourcesDrill(ctx, day, "fathom", e);
    },
    { sm: true, icon: "arrow-up-right" });
  return el("div", "stack-1", [
    el("div", null, base + ` The last poll finished on ${fmt.date(day)} (IST);`
      + " Activity holds one row per call ingested that day — who was on it,"
      + " which client it was filed under and how that was decided, what came"
      + " out of it, and what it cost."),
    go,
  ]);
}

function statCards(d, dossiers, t, ctx) {
  const g = el("div", "grid-4");

  // Every fact and every source lands in exactly one named bucket, and the
  // buckets ADD UP to the card. This replaces a vague "N belong to no dossier"
  // that piled two different things — one a defect, one not — into one number
  // and left both drills admitting they could not sum:
  //   in_dossiers   claimed by a compiled dossier
  //   unattributed  client-scoped, but the SOURCE was never attributed, so
  //                 there is no dossier for it to be missing FROM
  //   proof_points  agency + cross-client, excluded from dossiers BY DESIGN
  //   unaccounted   the residual, which must be 0. Non-zero is a real defect
  //                 (a client_id with no dossier row, or an index count that
  //                 has drifted from its facts) and is called one.
  // The server computes this (data._kb_accounting) so one arithmetic lives in
  // one place and a test can pin the identity.
  const acc = d.accounting || { facts: {}, sources: {} };
  const af = acc.facts || {};
  const as = acc.sources || {};

  const bucket = (label, why, facts, sources) => ({
    bucket: true, client_name: label, why, client_id: "",
    fact_count: facts, source_count: sources,
    revenue_usd: null, last_compiled_at: null,
  });
  const residualNote = (n, what) => (n
    ? ` ${fmt.num(Math.abs(n))} ${what} ${n > 0 ? "are in NO bucket" : "are"
      + " counted twice"} — that is a defect, not a definition: a client_id`
      + " with no dossier row, or an index count that has drifted from the"
      + " facts behind it. Recompile (bin/kb-backfill compile) and it should"
      + " go to zero."
    : "");

  // 1) FACTS
  const factRows = dossiers.concat([
    bucket("Unattributed — no client yet",
      "client-scoped facts whose source was never attributed",
      af.unattributed || 0, null),
    bucket("Agency & cross-client → proof-points",
      "no client by design; compiled into the proof-points wiki",
      af.proof_points || 0, null),
  ]);
  g.appendChild(kpi({
    label: "Facts",
    value: dash(t.facts, fmt.num),
    sub: `${fmt.num(af.in_dossiers)} in dossiers`
      + ` · ${fmt.num(af.unattributed)} unattributed`
      + ` · ${fmt.num(af.proof_points)} proof-points`
      + (af.unaccounted ? ` · ${fmt.num(af.unaccounted)} unaccounted` : ""),
    onClick: () => openDrill({
      title: "Every fact, by where it lives",
      subtitle: `${fmt.num(dossiers.length)} dossiers + 2 buckets`
        + ` = ${fmt.num(t.facts)} facts`,
      note: `These rows sum to the ${fmt.num(t.facts)} on the card:`
        + ` ${fmt.num(af.in_dossiers)} claimed by a dossier,`
        + ` ${fmt.num(af.unattributed)} client-scoped facts whose SOURCE`
        + " carries no client_id (there is no dossier for them to be missing"
        + " from — they are the mapping queue's backlog), and"
        + ` ${fmt.num(af.proof_points)} agency / cross-client facts, which`
        + " belong to no single client by design."
        + residualNote(af.unaccounted, "facts"),
      rows: factRows,
      total: factRows.length,
      columns: accountingCols,
      amount: null,
      search: (r) => `${r.client_name} ${r.client_id}`,
      emptyText: "No dossier has been compiled.",
    }),
  }));

  // 2) SOURCES
  const sourceRows = dossiers.concat([
    bucket("Unattributed — no client yet",
      "ingested and searchable, but not filed under a client",
      null, as.unattributed || 0),
  ]);
  g.appendChild(kpi({
    label: "Sources",
    value: dash(t.sources, fmt.num),
    sub: "meetings, docs & threads behind the facts · "
      + `${fmt.num(as.in_dossiers)} in dossiers`
      + ` · ${fmt.num(as.unattributed)} unattributed`
      + (as.unaccounted ? ` · ${fmt.num(as.unaccounted)} unaccounted` : ""),
    onClick: () => openDrill({
      title: "Every source, by where it lives",
      subtitle: `${fmt.num(dossiers.length)} dossiers + 1 bucket`
        + ` = ${fmt.num(t.sources)} sources`,
      note: `These rows sum to the ${fmt.num(t.sources)} on the card:`
        + ` ${fmt.num(as.in_dossiers)} attached to a dossier and`
        + ` ${fmt.num(as.unattributed)} carrying no client_id. The unattributed`
        + " ones are ingested and full-text searchable; they are waiting on an"
        + " attribution, not lost. Open a dossier below for its own sources,"
        + " with type, title and date."
        + residualNote(as.unaccounted, "sources"),
      rows: sourceRows,
      total: sourceRows.length,
      columns: accountingCols,
      search: (r) => `${r.client_name} ${r.client_id}`,
      emptyText: "No dossier has been compiled.",
    }),
  }));

  // 3) FATHOM INGEST
  const f = d.fathom || {};
  append(g, kpi({
    label: "Fathom ingest",
    value: f.last_poll ? "daily ✓" : "not running",
    // no poll has ever completed, so "how fresh is Fathom" is NOT MEASURED —
    // the tile greys itself rather than reading like a live status.
    unmeasured: !f.last_poll,
    stale: !f.last_poll,
    sub: f.last_poll
      ? `last pull ${fmt.relative(f.last_poll)}`
        + (f.last_saved != null
          ? ` · ${fmt.num(f.last_saved)} new transcript${f.last_saved === 1 ? "" : "s"}`
          : "")
        + ` · caught up to ${String(f.cursor || "—").slice(0, 16)}`
      : "kb-daily has not completed — transcripts are not being pulled, so the"
        + " kb is not just quiet, it is blind",
    onClick: () => openDrill({
      title: "Fathom ingest state",
      subtitle: "the poll runs inside the kb_daily task",
      rows: [
        { k: "Last poll", v: f.last_poll || null },
        { k: "Cursor (caught up to)", v: f.cursor || null },
        { k: "Transcripts saved on the last poll", v: f.last_saved == null ? null : String(f.last_saved) },
      ],
      total: 3,
      columns: [
        { key: "k", label: "Field" },
        { key: "v", label: "Value", cls: "mono" },
      ],
      // This tile reports the poll's STATE ("daily ✓"), and a state table is
      // the honest drill for it. But the note used to stop at "the per-run
      // detail lives under Activity" — a caveat pointing at a screen, which is
      // a dead end you have to navigate yourself. The per-transcript story is
      // REAL (/api/activity/sources?source_type=fathom, one row per call with
      // its attribution, facts, dossier, cost and any registry commit), so the
      // note now opens it instead of naming it.
      note: fathomCallsNote(f, ctx),
      emptyText: "The Fathom poll has never run.",
    }),
  }));

  // 4) PROOF-POINTS
  const pp = d.proof_points || {};
  const ppn = (pp.agency || 0) + (pp.cross || 0);
  g.appendChild(kpi({
    label: "Proof-points",
    value: fmt.num(ppn),
    sub: ppn
      ? `${fmt.num(pp.agency)} agency · ${fmt.num(pp.cross)} cross-client`
      : "a true zero — none captured yet. These are the agency and"
        + " cross-client facts you can cite in a pitch; they are extracted with"
        + " scope='agency'/'cross-client', and nothing has produced one.",
    onClick: ppn ? () => openDrill({
      title: "Proof-points by scope",
      subtitle: "facts that are about US, not about one client",
      note: "/api/knowledge returns proof-points as COUNTS only — the"
        + " individual rows are not routed. Use the fact search below with a"
        + " keyword to read them; the search covers every scope.",
      rows: [
        { scope: "agency", n: pp.agency || 0 },
        { scope: "cross-client", n: pp.cross || 0 },
      ],
      total: 2,
      columns: [
        { key: "scope", label: "Scope", render: (r) => pill(r.scope, "outline") },
        { key: "n", label: "Facts", num: true, render: (r) => fmt.num(r.n) },
      ],
      emptyText: "No proof-point has been captured.",
    }) : null,
  }));

  return g;
}

/* ── 4. fact search (FTS over cited facts) ────────────────────────────────── */

function searchCard(ctx) {
  const preset = String(ctx.params.get("q") || "").trim();
  const si = el("input", {
    class: "input grow", type: "search", value: preset,
    placeholder: "full-text search across every cited fact",
    attrs: { "aria-label": "search facts" },
  });
  const go = btn("Search", () => run(), { icon: "search" });
  const res = el("div", { style: { marginTop: "var(--space-3)" } });
  let busy = false;

  async function run() {
    const q = si.value.trim();
    if (busy) return;
    if (!q) {
      // An empty Search used to be a NO-OP: no fetch, no DOM change, no toast,
      // no focus move. A control that answers a click with absolutely nothing
      // is indistinguishable from a broken one, so it now says what it needs
      // and puts the caret there.
      toast("Type something to search for — the fact index needs a term.", true);
      clear(res).appendChild(emptyState(
        "Nothing searched yet.",
        {
          icon: "search",
          detail: "This is an empty BOX, not an empty result: type a"
            + " distinctive word and press Search. Every scope is covered —"
            + " client, agency and cross-client facts.",
        }));
      si.focus();
      return;
    }
    busy = true;
    go.disabled = true;
    clear(res).appendChild(skeleton({ lines: 3 }));
    try {
      const r = await ctx.api("/api/knowledge?q=" + encodeURIComponent(q));
      const rows = r.results || [];
      clear(res);
      if (!rows.length) {
        res.appendChild(emptyState(
          `Nothing in the kb matches “${q}”.`,
          {
            icon: "search",
            detail: "This is a real empty result, not an error — the full-text"
              + " index was searched and no cited fact matched. Try a single"
              + " distinctive word; the search ORs the terms it keeps.",
          }));
      } else {
        res.appendChild(el("div", "muted small",
          `${fmt.num(rows.length)} matching fact${rows.length === 1 ? "" : "s"}`
          + (rows.length >= 40
            ? " — the server caps this search at 40, ranked by relevance, so"
              + " there may be more"
            : "")
          + ". Every scope is searched: a row with no client is an agency,"
          + " cross-client or unattributed fact."));
        rows.forEach((x) => res.appendChild(factLine(x, true)));
      }
    } catch (e) {
      clear(res).appendChild(el("div", "errstate",
        "The search failed: " + String((e && e.message) || e)));
    }
    busy = false;
    go.disabled = false;
  }

  si.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });

  const c = el("div", "card");
  c.append(
    el("div", "card-head", [
      el("h3", "card-title", "Search facts"),
      el("span", "card-sub", "full-text · no model call · free"),
    ]),
    el("div", "field-row", [si, go]),
    res,
  );
  if (preset) run();                 // deep link: #/knowledge?q=…
  return c;
}

/* ── 5. dossiers ──────────────────────────────────────────────────────────── */

function dossierRegion(dossiers, fr, ctx) {
  const out = [heading("Dossiers",
    `— ${fmt.num(dossiers.length)} clients · facts + revenue-to-date · click one`
    + " to open it", "h3")];

  const detail = el("div");
  const gridHost = el("div");
  const countLine = el("span", "muted small");

  let needle = "";
  let sort = "facts";
  let showAll = false;

  const SORTS = {
    facts: (a, b) => (b.fact_count || 0) - (a.fact_count || 0),
    revenue: (a, b) => (b.revenue_usd || 0) - (a.revenue_usd || 0),
    sources: (a, b) => (b.source_count || 0) - (a.source_count || 0),
    name: (a, b) => String(a.client_name || "").localeCompare(String(b.client_name || "")),
  };

  const filterIn = el("input", {
    class: "input grow", type: "search",
    placeholder: `filter ${dossiers.length} dossiers by name…`,
    attrs: { "aria-label": "filter dossiers" },
    on: { input: (e) => { needle = e.target.value.trim().toLowerCase(); paint(); } },
  });
  const sortSel = el("select", {
    class: "input", attrs: { "aria-label": "sort dossiers" },
    on: { change: (e) => { sort = e.target.value; paint(); } },
  }, [
    el("option", { value: "facts" }, "Most facts"),
    el("option", { value: "revenue" }, "Most revenue"),
    el("option", { value: "sources" }, "Most sources"),
    el("option", { value: "name" }, "Name (A–Z)"),
  ]);
  const allBtn = btn("Show all", () => { showAll = !showAll; paint(); }, { sm: true });

  const controls = el("div", "card-controls", [
    icon("filter", { size: 14, class: "muted" }),
    filterIn, sortSel, allBtn, el("span", "grow"), countLine,
  ]);

  function paint() {
    const matched = dossiers
      .filter((x) => !needle
        || String(x.client_name || "").toLowerCase().includes(needle)
        || String(x.client_id || "").toLowerCase().includes(needle))
      .sort(SORTS[sort] || SORTS.facts);

    const cap = needle ? FILTER_CAP : (showAll ? matched.length : GRID_PREVIEW);
    const shown = matched.slice(0, cap);

    clear(countLine).append(
      `${fmt.num(shown.length)} of ${fmt.num(matched.length)} shown`
      + (matched.length === dossiers.length ? "" : ` (${fmt.num(dossiers.length)} total)`));
    allBtn.textContent = showAll ? "Show top " + GRID_PREVIEW : "Show all " + dossiers.length;
    allBtn.disabled = !!needle;

    const grid = el("div", "grid");
    shown.forEach((x) => grid.appendChild(dossierCard(x, fr, ctx, detail)));
    clear(gridHost);
    gridHost.appendChild(matched.length
      ? grid
      : emptyState(`No dossier matches “${filterIn.value}”.`, { icon: "search" }));
    if (matched.length > shown.length) {
      gridHost.appendChild(el("div", { class: "muted small", style: { marginTop: "var(--space-3)" } },
        needle
          ? `${fmt.num(matched.length - shown.length)} more match — narrow the filter.`
          : `${fmt.num(matched.length - shown.length)} more dossiers are hidden —`
            + " use “Show all” or the filter above."));
    }
  }

  paint();
  out.push(controls, gridHost, detail);
  return out;
}

function dossierCard(x, fr, ctx, detail) {
  const empty = !x.fact_count;

  const stalePill = x.stale ? pill(`stale ${x.stale_days}d`, "warn") : null;
  if (stalePill) {
    stalePill.setAttribute("title",
      `not recompiled in over ${fr.stale_after_days} days`);
  }

  // The pills live IN the label row rather than absolutely positioned: .kpi's
  // own "open →" tag already owns the top-right corner, and a 30-character
  // client name would have run underneath them. `.lbl-row` (components.css)
  // is that layout, and the absence of `.caps` is deliberate — a client name
  // is a name, not a stat label, so it is not shouted.
  return el("button", {
    class: "kpi", type: "button",
    attrs: { "aria-label": `open the ${x.client_name || x.client_id} dossier` },
    on: { click: () => openDossier(x, ctx, detail) },
  }, [
    el("div", "lbl lbl-row", [
      el("span", null, x.client_name || x.client_id),
      x.revenue_usd != null ? pill(fmt.usd(x.revenue_usd), "ok") : null,
      stalePill,
    ]),
    el("div", "val", fmt.num(x.fact_count)),
    el("div", "sub2", [
      `${fmt.num(x.source_count)} source${x.source_count === 1 ? "" : "s"}`
      + " · compiled "
      + (String(x.last_compiled_at || "").slice(0, 10) || "never"),
      // a zero that explains itself. `empty_reason` is only set when the
      // dossier was NEVER compiled; a compiled dossier with 0 facts is a
      // different (and quieter) failure, so it gets its own sentence.
      empty
        ? el("div", { style: { marginTop: "3px" } },
          x.empty_reason
            ? `0 facts — ${x.empty_reason}`
            : "0 facts — it WAS compiled, and nothing was extracted from its"
              + ` ${fmt.num(x.source_count)} source${x.source_count === 1 ? "" : "s"}`
              + " yet. A real zero, not a missing read.")
        : null,
    ]),
    el("div", "tag", "open →"),
  ]);
}

/* ── the dossier itself ───────────────────────────────────────────────────── */

export async function openDossier(entry, ctx, host) {
  clear(host).appendChild(el("div", "card", skeleton({ lines: 4 })));
  host.scrollIntoView({ behavior: "smooth", block: "nearest" });

  let d;
  try {
    d = await ctx.api("/api/knowledge?client=" + encodeURIComponent(entry.client_id));
  } catch (e) {
    clear(host).appendChild(el("div", "errstate",
      `Could not open the ${entry.client_name || entry.client_id} dossier: `
      + String((e && e.message) || e)));
    return;
  }

  const m = d.meta || {};
  const facts = d.facts || [];
  const sources = d.sources || [];

  const c = el("div", "card");
  const head = el("div", "card-head", [
    el("div", "hstack", [
      // `.plain`: this heading is a CLIENT NAME, so it is not given the
      // uppercase stat-label treatment the other card titles carry.
      el("h3", "card-title plain", m.client_name || entry.client_name || d.client),
      entry.revenue_usd != null ? pill(fmt.usd(entry.revenue_usd), "ok") : null,
      entry.stale ? pill(`stale ${entry.stale_days}d`, "warn") : null,
    ]),
    el("div", "hstack-tight", [
      btn("Close", () => clear(host), { sm: true, icon: "x" }),
    ]),
  ]);

  const metaLine = el("div", "muted small", [
    `${fmt.num(facts.length)} client fact${facts.length === 1 ? "" : "s"} · `,
    sources.length
      ? linkNum(`${fmt.num(sources.length)} source${sources.length === 1 ? "" : "s"}`, () => openDrill({
        title: `Sources — ${m.client_name || d.client}`,
        subtitle: "the meetings, recordings, threads and documents these facts"
          + " were extracted from",
        rows: sources,
        total: sources.length,
        columns: sourceCols,
        time: (s) => s.occurred_dt || "",
        search: (s) => `${s.title || ""} ${s.source_ref || ""} ${s.source_type || ""}`,
        facets: [{ label: "type", allLabel: "All source types", of: (s) => s.source_type }],
        emptyText: "This dossier has no source.",
      }))
      : el("span", { attrs: { title: "a real zero — nothing has been attached to this client" } }, "0 sources"),
    ` · compiled ${String(m.last_compiled_at || "").slice(0, 16) || "never"}`,
    m.dossier_path ? el("div", "mono", m.dossier_path) : null,
  ]);

  // the meta count and the row count can disagree: fact_count was written at
  // compile time, `facts` is read live. Say which is which rather than pick one.
  const drift = m.fact_count != null && m.fact_count !== facts.length
    ? notice("warn",
      `The dossier index says ${fmt.num(m.fact_count)} facts; ${fmt.num(facts.length)}`
      + " are in the table right now. The index count is written when the"
      + " dossier is compiled, so the difference is what has landed since the"
      + " last compile. The rows below are the live truth.")
    : null;

  const filterIn = el("input", {
    class: "input grow", type: "search",
    placeholder: `filter ${facts.length} facts…`,
    attrs: { "aria-label": "filter this dossier's facts" },
  });
  const body = el("div", { style: { marginTop: "var(--space-3)" } });

  const byCat = new Map();
  facts.forEach((x) => {
    const k = x.category || "uncategorised";
    if (!byCat.has(k)) byCat.set(k, []);
    byCat.get(k).push(x);
  });
  const cats = [...byCat.entries()].sort((a, b) => b[1].length - a[1].length);

  function paintFacts() {
    const needle = filterIn.value.trim().toLowerCase();
    clear(body);

    if (!facts.length) {
      body.appendChild(emptyState(
        "No fact has been extracted for this client.",
        {
          icon: "book-open",
          detail: entry.empty_reason
            ? String(entry.empty_reason)
            : `The dossier was compiled, and its ${fmt.num(sources.length)} source`
              + `${sources.length === 1 ? "" : "s"} produced nothing. That is a`
              + " real zero — the extractor ran and found no citable fact.",
        }));
      return;
    }

    if (needle) {
      const hits = facts.filter((x) =>
        `${x.fact_text || ""} ${x.citation || ""} ${x.category || ""}`
          .toLowerCase().includes(needle));
      body.appendChild(el("div", "muted small",
        `${fmt.num(hits.length)} of ${fmt.num(facts.length)} facts match`
        + (hits.length > FACT_CAP ? ` — showing the first ${FACT_CAP}` : "")));
      if (!hits.length) {
        body.appendChild(emptyState("Nothing in this dossier matches that.",
          { icon: "search" }));
      }
      hits.slice(0, FACT_CAP).forEach((x) => body.appendChild(factLine(x, false)));
      return;
    }

    // Unfiltered: one lazy expander per category. 5,670 facts in a single
    // dossier is normal here, so nothing is built until it is opened.
    const autoOpen = facts.length <= 60;
    cats.forEach(([cat, list]) => {
      const ex = expander(`${cat} · ${fmt.num(list.length)} fact${list.length === 1 ? "" : "s"}`,
        (target) => {
          if (list.length > FACT_CAP) {
            target.appendChild(el("div", "muted small",
              `Showing the first ${FACT_CAP} of ${fmt.num(list.length)} —`
              + " use the filter above to narrow this category."));
          }
          list.slice(0, FACT_CAP).forEach((x) => target.appendChild(factLine(x, false)));
        });
      body.appendChild(ex);
      if (autoOpen) {
        const b = ex.querySelector(".expander-btn");
        if (b) b.click();
      }
    });
  }

  filterIn.addEventListener("input", paintFacts);

  // append() here is ui.js's helper, NOT the native Element.append(): it drops
  // nulls, where the native one would stringify `drift` into the text "null".
  append(c, [
    head, metaLine, drift,
    el("div", { class: "field-row", style: { marginTop: "var(--space-3)" } }, filterIn),
    body,
  ]);
  paintFacts();

  clear(host).appendChild(c);
  c.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
