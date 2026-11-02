// ═══════════════════════════════════════════════════════════════════════════
// sections/today.js — the daily action screen, and the reference for every
// other section. Read app.js for the contract; this file is what it looks like.
//
// WHAT IS ON THIS PAGE, AND WHY (the copy here is load-bearing — it was written
// after specific misreads, and changing it re-opens them):
//
//   · source-health line   a quiet queue must be provably quiet, not a dead
//                          feed. It is ALWAYS rendered, including when fine.
//   · quick capture        zero friction, and it TELLS HIM WHAT HAPPENED. The
//                          box polls the note it just wrote and names the
//                          verdict ("→ card created: …") within seconds. It
//                          used to say "classified next sweep", which was
//                          honest and was the admission of the bug: the sweep
//                          is 2-hourly, so Today did not change and the only
//                          reading available was "it didn't save".
//   · captured notes       the other half of that bug. A note the classifier
//                          judged NOT actionable produced nothing anywhere a
//                          screen could reach — note #2, "evolve exteriors
//                          everything sorted out", vanished into a table
//                          nobody renders. Every such note is listed here with
//                          the verdict that stopped it, and one click makes it
//                          a card anyway.
//   · act-now queue        ONE ruthlessly ranked list. Cards expand in place.
//   · +N more              the old Nudges TAB, folded in (brief §2). Same
//                          queue, so it is the same page — with the grouped
//                          expander and the latest-brief replay it used to own.
//   · hidden-card notice   provenance law: a client-facing card with no
//                          evidence link is hidden AND files an anomaly. v1
//                          hid them silently — that is the bug this fixes.
//   · backlog              paused-aware. Applications are paused BY INTENT, so
//                          accumulated hot leads wait; they do not nag.
//   · at a glance          four figures, every zero explaining itself.
//
// TWO RULES THIS FILE OBEYS LITERALLY:
//   `—` = not measured · `0` = a real zero, and it says why.
//   Every figure drills, and the drill states the true total.
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, clear, fmt, dash, icon, heading, metricCard, pill, btn,
  linkNum, extLink, emptyState, notice, expander, toast, copyText,
} from "../ui.js";
import { openDrill } from "../drill.js";
import { rings } from "../shapes.js";
// ONE conversation row for both screens (Upwork brief, 2026-08-25 night):
// a follow-up / owed-reply card renders as a person with their last message,
// the draft and one state word — here and on Upwork › Inbox alike.
import { conversationRow, act, closure } from "../conversation.js";
// LinkedIn — needs you (2026-10-28): the SAME rows the LinkedIn tab shows, so
// the two screens cannot describe one draft differently.
import { needsYouItems as linkedinItems } from "./linkedin.js";

export const meta = { title: "Today", hint: "what to act on right now" };

/**
 * The singular of each render-time cluster label the server can emit
 * (`_CLUSTER` in modules/cockpit/data.py). A generic "strip the s" is WRONG
 * here — it turns "scoring" into "scorin" and "client comms" into "client
 * comm" — so this is an explicit map and anything not in it is left alone.
 */
const CLUSTER_SINGULAR = {
  "follow-ups": "follow-up", "commitments": "commitment", "dossiers": "dossier",
  "hot leads": "hot lead", "win outcomes": "win outcome",
  "escalations": "escalation", "captures": "capture",
  "invites": "invite", "offers": "offer", "unfunded milestones": "unfunded milestone",
};

const clusterText = (c) => `${c.count} `
  + (c.count === 1 ? (CLUSTER_SINGULAR[c.label] || c.label) : c.label);

/* ── the drill columns for a hot lead (shared by both backlog figures) ────── */
const leadCols = [
  { key: "d", label: "Scored", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "title", label: "Job",
    render: (r) => (r.job_url ? extLink(r.job_url, r.title || r.id) : (r.title || r.id)) },
  { key: "score", label: "Score", num: true },
  { key: "icp_tier", label: "Tier",
    render: (r) => (r.icp_tier ? pill(r.icp_tier, r.icp_tier === "icp" ? "icp" : "outline") : null) },
  { key: "clickup_task_url", label: "Card",
    render: (r) => (r.clickup_task_url ? extLink(r.clickup_task_url, "card") : null) },
];

export async function render(root, ctx) {
  const d = await ctx.api("/api/today");
  const m = d.metrics || {};

  // REGROUPED 2026-08-26 (Abhijeet, 05:20: "I want the UI of all the tabs
  // improved in the same way" as the Upwork tab): one list ranked by an
  // urgency number read as "a lot of data laid out without structure". The
  // cards are now grouped by WHAT THEY ASK OF HIM — people waiting on a reply,
  // things he promised, decisions, housekeeping — each group longest-wait
  // first with a fold; the health line is one sentence; the hot-lead backlog,
  // the four figures and the latest brief sit under "More".
  root.append(
    heading("Today", "— what to act on right now"),
    healthLine(d.sources || {}),
    // the four numbers he asked to SEE, as bars, before anything that asks
    // for a decision (2026-11-01). They used to sit under "More".
    glanceStrip(d, m),
    quickCapture(ctx),
    ...captured(d, ctx),
    ...groups(d, ctx),
    ...draftsStrip(d, ctx),
    ...leadsBlock(d.leads, ctx),
    ...linkedinBlock(d.linkedin, ctx),
    expander("More — hot leads waiting, the latest brief", (body) => {
      body.append(...backlog(d, ctx),
        expander("Latest brief (draft)", (b2) => renderBrief(b2, ctx)));
    }),
  );
}

/* ── the glance strip (2026-11-01) ───────────────────────────────────────── */
/*
 * Four bars: revenue this month against the target, proposals this week
 * against the pace the running test needs, comments today against 10, buyer
 * conversations against the day-90 gate of 5. Each is a fraction of a goal he
 * set, so the bar means something; "—" when a figure is not measured.
 */
function glanceStrip(d, m) {
  // Four rings, not four boxes (Abhijeet, 2026-11-02): each is a share of a
  // target, and a ring says "how far along" before you read a digit. The
  // figure and the target stay in the DOM text; `—` when a source is gated.
  const rev = m.revenue || {}, a = m.applied || {}, li = d.linkedin || {};
  const haveRev = rev.mtd_usd != null;
  const usdK = (v) => (v >= 1000 ? "$" + (Math.round(v / 100) / 10) + "k" : fmt.usd(v));
  return rings([
    { label: "Revenue this month", value: haveRev ? rev.mtd_usd : 0, target: haveRev ? rev.target_usd : null,
      format: usdK, tone: haveRev ? null : "bad",
      note: haveRev ? `${fmt.pct(rev.pct)} of the $${Math.round((rev.target_usd || 0) / 1000)}k target` : "finance read is gated — see Money" },
    { label: "Proposals this week", value: a.paused ? 0 : (a.week || 0), target: a.paused ? null : (a.target_week || 25),
      tone: a.paused ? "bad" : null,
      note: a.paused ? a.note : [`${a.today || 0} today · ${a.last_week || 0} last week · the test needs `, el("b", null, a.target_week || 25), " a week"] },
    { label: "Comments today", value: li.comments_today || 0, target: li.comments_target_day,
      note: li.comments_target_day != null ? "LinkedIn · your 10-a-day" : "LinkedIn copy not synced yet" },
    { label: "Buyer conversations", value: li.buyer_conversations || 0, target: li.day90_target,
      note: li.days_left != null ? [`the day-90 gate · `, el("b", null, `${li.days_left} days`), " left"] : "LinkedIn copy not synced yet" },
  ]);
}

/* ── drafts ready to send (2026-11-01) ───────────────────────────────────── */
/*
 * The running test had 157 drafts an arm and 17 sends in three weeks. The
 * five best hot jobs with a draft written and not yet applied, Copy + Open on
 * each. He sends — nothing here does. Same predicate as Upwork › Jobs ›
 * Drafts to review, so the count is the same there.
 */
function draftsStrip(d, ctx) {
  const dr = d.drafts_ready || {};
  const items = dr.items || [];
  if (!items.length) return [];
  const rows = items.map((j) => {
    const copy = btn("Copy draft", (e) => copyText(j.draft_proposal || "", e.currentTarget), { sm: true, icon: "copy" });
    return el("div", "li-item", [
      pill(j.icp_tier === "icp" ? "ICP" : (j.icp_tier === "icp_near" ? "near" : `score ${j.score}`),
        j.icp_tier === "icp" ? "icp" : "outline"),
      el("div", "grow", [
        el("div", null, [j.job_url ? extLink(j.job_url, j.title || j.id) : (j.title || j.id),
          el("span", "muted small", ` · score ${j.score} · scored ${fmt.dateShort(j.d)}`)]),
        el("div", "small muted", short(j.draft_proposal, 180)),
      ]),
      el("div", "hstack-tight", [copy, j.job_url ? extLink(j.job_url, "Open job", { class: "btn sm" }) : null]),
    ]);
  });
  return [
    heading("Drafts ready to send", `— ${dr.shown} of ${dr.total} · the test needs sends, not drafts`, "h3"),
    el("div", "card flush", [
      el("div", "li-list", rows),
      el("div", "muted small li-foot", [
        el("button", { class: "linktext", type: "button",
          on: { click: () => ctx.navigate("upwork", { view: "jobs" }) } }, "all drafts on Upwork › Jobs"),
      ]),
    ]),
  ];
}

const short = (s, n = 160) => (s && s.length > n ? s.slice(0, n - 1) + "…" : (s || ""));

/* ── the health line: one sentence, the names, a link ────────────────────── */

function healthLine(s) {
  const lines = s.lines || [];
  if (s.ok) {
    return el("div", "srcline ok", [icon("circle-check", { size: 15 }),
      el("div", "grow", s.headline || "Every system ran on time.")]);
  }
  // "STALE: Upwork sync last produced 10h ago (expected every 3h)." -> "Upwork sync"
  // "STILL STALE (1d overdue): Sunday outcome email last produced …" -> "Sunday outcome email"
  const names = lines.map((x) => String(x).replace(/^[A-Z][A-Z ]*(?:\([^)]*\))?:\s*/, "")
    .replace(/\s+(?:last produced|has not|never|is|—).*$/i, "").trim()).filter(Boolean);
  const n = lines.length || names.length;
  const go = () => { location.hash = "#/activity?view=health"; };
  return el("div", "srcline bad", [icon("alert-triangle", { size: 15 }),
    el("div", "grow", [
      el("button", { class: "linktext strong", type: "button", on: { click: go } },
        `${n} system${n === 1 ? " is" : "s are"} late`),
      names.length ? el("span", "muted", ` — ${names.join(", ")}. `) : el("span", "muted", ". "),
      el("span", "muted", "Their numbers on this page may be behind."),
    ])]);
}

/* ── the groups: what each card asks of him ──────────────────────────────── */

const GROUPS = [
  { key: "people", title: "People waiting on you", hint: "longest wait first" },
  { key: "promised", title: "Things you promised", hint: "the system clears one the moment you deliver it" },
  { key: "decide", title: "Needs a decision", hint: "" },
  { key: "house", title: "Housekeeping", hint: "nothing a client can see" },
];
const DECIDE_KINDS = new Set(["escalate_client", "escalate_commitment", "won_no_outcome",
  "deadline", "lost_pattern", "capture", "owed_reply",
  // priced kinds since 2026-11-01 — they used to arrive here disguised as
  // escalate_client; an invite, an offer or an unfunded milestone IS a decision
  "upwork_invitation", "upwork_offer", "stalled_milestone"]);
const GROUP_FOLD = 5;

function groupOf(it) {
  if (it.conversation) return "people";
  const k = it.nudge_kind || "";
  if (k === "commitment_due") return "promised";
  if (it.kind === "followup") return "people";
  if (DECIDE_KINDS.has(k)) return "decide";
  return "house";
}

function groups(d, ctx) {
  const all = d.queue_all || d.queue || [];
  const out = [];

  if (d.queue_hidden_no_provenance) {
    const n = d.queue_hidden_no_provenance;
    out.push(notice("warn",
      `${n} card${n === 1 ? " is" : "s are"} hidden — client-facing but without an evidence`
      + ` link, and an unsourced claim about a client is never shown. Listed under Activity.`));
  }

  if (!all.length) {
    const ok = (d.sources || {}).ok;
    out.push(el("div", "card flush", emptyState(
      ok ? "Nothing needs you right now — and every system is fresh, so that is a real quiet."
         : "Nothing is listed — but a system is late (see the line above), so this may be a gap, not a clear desk.",
      { icon: ok ? "circle-check" : "alert-triangle" })));
    return out;
  }

  const by = {};
  all.forEach((it) => { (by[groupOf(it)] = by[groupOf(it)] || []).push(it); });
  GROUPS.forEach((g) => {
    const items = by[g.key] || [];
    if (!items.length) return;
    out.push(heading(g.title, `— ${items.length}${g.hint ? " · " + g.hint : ""}`, "h3"));
    const list = el("div", "card flush");
    items.slice(0, GROUP_FOLD).forEach((it) => list.appendChild(queueCard(it, ctx)));
    if (items.length > GROUP_FOLD) {
      list.appendChild(expander(`+${items.length - GROUP_FOLD} more`, (body) => {
        items.slice(GROUP_FOLD).forEach((it) => body.appendChild(queueCard(it, ctx)));
      }));
    }
    out.push(list);
  });
  if (d.queue_auto_closed) {
    out.push(el("p", "muted small", `${d.queue_auto_closed} card${d.queue_auto_closed === 1 ? "" : "s"} closed by themselves — a call or a reply settled them.`));
  }
  return out;
}

/* ── New leads (2026-11-01) ──────────────────────────────────────────────── */
/*
 * The prospects the Review queue could never place — the website form, a cold
 * inbound he marked Lead. Listed while status is still "new"; the moment he
 * marks one contacted on Pipeline › Leads it leaves this list. No card keys,
 * no Done: the leads table is the state.
 */
function leadsBlock(ld, ctx) {
  if (!ld || ld.error || !ld.total) return [];
  const open = () => ctx.navigate("pipeline", { view: "leads" });
  const items = (ld.rows || []).map((r) => el("div", "li-item", [
    pill("New lead", "warn"),
    el("div", "grow", [
      el("div", null, [
        r.permalink ? extLink(r.permalink, r.name || r.email || "(no name)") : el("b", null, r.name || r.email || "(no name)"),
        el("span", "muted small", ` · ${[r.company, r.source && r.source.replace("_", " ")].filter(Boolean).join(" · ")}`),
      ]),
      el("div", "small muted", r.subject || ""),
    ]),
    el("span", "muted small", fmt.dateShort(r.first_seen_at)),
  ]));
  return [
    heading("New leads", `— ${ld.total} · reply on the channel they used, then mark them on `, "h3"),
    el("div", "card flush", [
      el("div", "li-list", items),
      el("div", "muted small li-foot", [
        el("button", { class: "linktext", type: "button", on: { click: open } }, "Pipeline › Leads"),
        ld.older ? ` · ${ld.older} older than ${ld.fresh_days} days not listed` : "",
      ]),
    ]),
  ];
}

/* ── LinkedIn — needs you (2026-10-28) ───────────────────────────────────── */
/*
 * Derived from the Airtable tracker's own state, so an item leaves this list
 * when the social session updates the tracker (a draft becomes Posted). No
 * Done button, on purpose: a Done here would be a second copy of a state that
 * lives in Airtable, and the two would drift. The pace line is always shown —
 * a quiet block must still say how today's commenting is going.
 */
function linkedinBlock(li, ctx) {
  if (!li) return [];
  if (li.error) return [notice("warn", `LinkedIn: ${li.error}`)];
  if (li.never_synced) return [];
  const pace = `comments today ${li.comments_today} of ${li.comments_target_day}`;
  const open = () => ctx.navigate("linkedin");
  if (!li.total) {
    return [el("p", "muted small", [
      "LinkedIn: nothing waiting on you · ", pace, " · ",
      el("button", { class: "linktext", type: "button", on: { click: open } }, "open the tab"),
    ])];
  }
  const list = el("div", "card flush", el("div", "li-list", linkedinItems(li)));
  return [
    heading("LinkedIn — needs you", `— ${li.total} · ${pace}`, "h3"),
    list,
  ];
}

/* ── source health (the older box; kept for the Upwork tab's readers) ────── */

function sourceHealth(s) {
  const box = el("div", "srcline " + (s.ok ? "ok" : "bad"), [
    icon(s.ok ? "circle-check" : "alert-triangle", { size: 15 }),
    el("div", "grow", [
      el("div", null, s.headline || "source health unavailable"),
      !s.ok && (s.lines || []).length
        ? el("ul", { style: { margin: "6px 0 0", paddingLeft: "18px", fontWeight: "400" } },
            (s.lines || []).map((x) => el("li", null, x)))
        : null,
    ]),
  ]);
  return box;
}

/* ── quick capture ────────────────────────────────────────────────────────── */

/**
 * Poll the note we just wrote until it has a verdict.
 *
 * The classifier runs in a child process (bin/note-classify), spawned by
 * /api/note the moment the row commits — the cockpit server may not make model
 * calls, and it may not sit on the HTTP response for seconds either. So the
 * write returns instantly and this asks "is it a card yet?" until the answer
 * arrives, typically in two or three seconds.
 *
 * It gives up after CAPTURE_POLL_MS and SAYS SO, rather than pretending. The
 * note is safe either way: it is in the inbox, the 2-hourly sweep still
 * classifies it, and it is already listed under Captured below.
 */
const CAPTURE_POLL_EVERY_MS = 700;
const CAPTURE_POLL_MS = 20000;

async function pollNote(ctx, id) {
  const until = Date.now() + CAPTURE_POLL_MS;
  while (Date.now() < until) {
    await new Promise((r) => setTimeout(r, CAPTURE_POLL_EVERY_MS));
    let d;
    try { d = await ctx.api("/api/note?note_id=" + encodeURIComponent(id)); }
    catch (e) { return null; }                 // network/401 — the caller says so
    if (d && d.done) return d;
  }
  return null;
}

function captureOutcome(d) {
  if (!d) {
    return ["Saved. Still classifying — it is listed under Captured below.", false];
  }
  if (d.failed || (d.state === "new")) {
    return ["Saved, but it could not be classified: "
      + (d.last_error || "no reason recorded")
      + ". It is under Captured below — make it a card from there.", true];
  }
  if (d.bucket === "nudge") {
    return ["Card created: " + (d.card_title || "(untitled)"), false];
  }
  if (d.bucket === "commitment") {
    return ["Saved as a commitment.", false];
  }
  // kb_note / idea: a real verdict, and the reason it must be visible
  return ["Saved as a note, not a to-do — see Captured below to make it a card.",
    false];
}

function quickCapture(ctx) {
  const input = el("input", {
    class: "input grow", type: "text",
    placeholder: "Quick capture — note, to-do, idea… (Enter)",
    attrs: { "aria-label": "quick capture" },
  });
  const go = btn("Capture", async () => {
    const text = input.value.trim();
    if (!text) return;
    go.disabled = true;
    let wrote = null;
    try {
      wrote = await ctx.post("/api/note", { text });
    } catch (e) {
      // THE TEXT STAYS IN THE BOX. Clearing it on a failed write is how a
      // capture box loses what he typed, which is the whole subject here.
      toast("Not saved — " + String((e && e.message) || e)
        + ". Your text is still in the box.", true);
      go.disabled = false;
      return;
    }
    input.value = "";
    if (!wrote.classifying) {
      // the child could not be spawned — say what will happen instead
      toast(wrote.note || "Saved.", false);
      ctx.refresh();
      go.disabled = false;
      return;
    }
    const t = toast("Captured — classifying…");
    const done = await pollNote(ctx, wrote.id);
    const [msg, bad] = captureOutcome(done);
    if (t && t.remove) t.remove();
    toast(msg, bad);
    ctx.refresh();                              // the card, or the note, appears
    go.disabled = false;
  }, { icon: "zap" });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go.click(); });
  return el("div", "card", el("div", "field-row", [input, go]));
}

/* ── captured notes that produced no card ─────────────────────────────────── */

/**
 * The fix for the second half of the capture bug: a note the classifier judged
 * NOT actionable used to produce nothing at all — no card, no row on any
 * screen, just a `notes` record nobody rendered. Something written down in
 * order not to be forgotten was then unreachable.
 *
 * So every note still waiting on him is listed here WITH THE VERDICT, and two
 * buttons: disagree (make it a card) or agree (file it, stop showing it).
 * Nothing here deletes a note; "Keep as a note" only stops it asking.
 */
function captured(d, ctx) {
  const c = d.captures || {};
  const items = c.items || [];
  if (c.error) {
    return [notice("bad", c.error)];
  }
  if (!items.length) return [];                 // silent when there is nothing

  const bits = [];
  if (c.pending) bits.push(`${c.pending} still classifying`);
  if (c.needs_decision) bits.push(`${c.needs_decision} waiting on you`);

  const box = el("div", "card flush");
  items.forEach((n) => box.appendChild(capturedRow(n, ctx)));
  if (c.total > items.length) {
    box.appendChild(el("div", "muted small",
      `Showing ${items.length} of ${c.total} — the rest are older captures`
      + ` still waiting on you.`));
  }
  return [
    heading("Captured", bits.length ? "— " + bits.join(" · ") : "— just typed", "h3"),
    box,
  ];
}

function capturedRow(n, ctx) {
  const row = el("div", "qitem");
  const left = el("div", "q-left", [
    el("div", "q-title", el("span", "txt", n.text || "(empty)")),
    el("div", "q-meta", [
      pill(n.verdict || "captured", n.needs_decision ? "warn" : "outline"),
      el("span", "muted", `captured ${fmt.dateShort(n.created_at)}`
        + (n.source ? ` from ${n.source}` : "")),
      n.verdict_detail ? el("span", "neg", "· " + n.verdict_detail) : null,
    ]),
  ]);

  const actions = el("div", "q-actions");
  if (n.needs_decision) {
    actions.append(
      btn("Make it a card", () => resolveNote(n, "card", ctx),
        { variant: "primary", sm: true, icon: "check" }),
      btn("Keep as a note", () => resolveNote(n, "filed", ctx),
        { sm: true, icon: "file-text" }),
    );
  } else {
    actions.appendChild(el("span", "muted small", "classifying…"));
  }
  row.appendChild(el("div", "q-row q-capture", [left, actions]));
  return row;
}

async function resolveNote(n, action, ctx) {
  try {
    await ctx.post("/api/note/resolve", { note_id: n.id, action });
    toast(action === "card" ? "Card created from your note."
      : "Kept as a note — it will stop asking.");
    ctx.refresh();
  } catch (e) {
    toast("That did not stick: " + String((e && e.message) || e), true);
  }
}

/* ── ACT NOW ──────────────────────────────────────────────────────────────── */

function actNow(d, ctx) {
  const q = d.queue || [];
  const out = [];

  out.push(heading("Act now",
    `— ${q.length ? `top ${q.length}` : "0"} by urgency`, "h3"));

  // render-time clusters over the atomic set ("5 commitments · 1 follow-up").
  // The server's labels are plural; a count of 1 takes the singular, or the
  // line reads "1 commitments" (2026-08-13).
  if (d.clusters && d.clusters.length) {
    const line = el("div", "q-clusters", d.clusters.map(clusterText).join(" · "));
    if (d.queue_auto_closed) {
      line.append(" · ", el("span", "q-closed", `${d.queue_auto_closed} auto-closed`));
    }
    out.push(line);
  }

  // PROVENANCE LAW made visible (brief §4). v1 hid these and said nothing, so
  // the board could quietly shrink with no way to tell.
  if (d.queue_hidden_no_provenance) {
    const n = d.queue_hidden_no_provenance;
    out.push(notice("warn",
      `${n} card${n === 1 ? " is" : "s are"} hidden from this queue — `
      + `${n === 1 ? "it is" : "they are"} client-facing but carr${n === 1 ? "ies" : "y"}`
      + ` no evidence link, and the provenance law will not render an unsourced`
      + ` claim about a client. An anomaly was filed; it is listed under Activity.`));
  }

  const list = el("div", "card flush");
  if (!q.length) {
    const ok = (d.sources || {}).ok;
    list.appendChild(emptyState(
      ok ? "Nothing needs you right now. ✓ (queue is empty, and all sources are fresh)"
         : "Queue is empty — but a source is degraded above, so this may be a feed gap, not a clear desk.",
      { icon: ok ? "circle-check" : "alert-triangle" }));
  }
  q.forEach((it) => list.appendChild(queueCard(it, ctx)));

  // ── the old Nudges tab, folded in: "+N more" opens IN PLACE ──────────────
  if (d.queue_more) {
    list.appendChild(expander(`+${d.queue_more} more in the ranked queue`,
      (body) => renderMore(body, d, ctx)));
  } else {
    // the brief replay used to live on the Nudges tab; it still belongs here
    list.appendChild(expander("Latest brief (draft)", (body) => renderBrief(body, ctx)));
  }

  out.push(list);
  return out;
}

/** one act-now card: a row + an expandable detail panel underneath it */
/**
 * ONE card component for the ONE queue (Upwork brief decision 5, 2026-08-25):
 * the Upwork tab renders its subset of the act-now queue with exactly this,
 * so the two screens cannot drift in what a card says or what Done/Snooze do.
 */
export function queueCard(it, ctx, opts = {}) {
  if (it.conversation) return conversationRow(it, ctx, opts);
  const wrap = el("div", "qitem");
  const detail = el("div", { class: "q-detail", style: { display: "none" } });

  const title = el("button", {
    class: "q-title", type: "button", attrs: { "aria-expanded": "false" },
  }, [
    icon("chevron-right", { size: 14, class: "q-expand" }),
    el("span", "txt", String(it.title || "(untitled)").replace(/^Commitment due:\s*/, "")),
  ]);
  title.addEventListener("click", () => {
    const open = detail.style.display === "none";
    detail.style.display = open ? "block" : "none";
    title.setAttribute("aria-expanded", String(open));
    if (open && !detail.childNodes.length) detail.appendChild(queueDetail(it));
  });

  const metaRow = el("div", "q-meta");
  // the CLIENT is the first thing on the meta line — who this is about
  if (it.client) metaRow.appendChild(el("span", "q-client", [icon("user", { size: 12 }), it.client]));
  if (it.kind === "followup") {
    followupMeta(it, metaRow);
  } else {
    // in words, never a kind pill: where the promise came from and when it is
    // due, or the action the escalation asks for
    const dt = it.detail || {};
    if (it.nudge_kind === "commitment_due") {
      const where = dt.source_kind === "upwork" ? "on Upwork"
        : dt.source_kind === "fathom" ? `on the call${dt.source_title ? ` “${dt.source_title}”` : ""}`
          : dt.source_kind ? `from ${dt.source_kind}` : "";
      const bits = [];
      if (dt.promised_on) bits.push(`promised ${fmt.dateShort(dt.promised_on)}${where ? " " + where : ""}${dt.to ? ", to " + dt.to : ""}`);
      else if (where) bits.push(`promised ${where}`);
      if (it.due) bits.push(fmt.due(it.due));
      if (bits.length) metaRow.appendChild(el("span", null, bits.join(" · ")));
      if (it.action && !/^Deliver or update the client\.?$/.test(it.action)) {
        metaRow.appendChild(el("div", "q-actionnote", it.action));
      }
    } else {
      if (it.due) metaRow.append(el("span", null, fmt.due(it.due)));
      if (it.action) metaRow.appendChild(el("div", "q-actionnote", it.action));
    }
  }

  const left = el("div", "q-left", [title, metaRow]);

  // J1.5 closure chip: possibly resolved on a call — confirm or keep open
  if (it.possibly_resolved) {
    const src = it.possibly_resolved.source || "a recent call";
    left.appendChild(el("div", "q-chip", [
      icon("link", { size: 13 }),
      el("span", "grow", `possibly resolved in ${src} —`),
      btn("Confirm resolved", () => closure(it, "confirm", ctx), { variant: "primary", sm: true }),
      btn("Keep open", () => closure(it, "reject", ctx), { sm: true }),
    ]));
  }

  const actions = el("div", "q-actions");
  if (it.kind === "followup") {
    const drafts = (it.detail || {}).drafts || [];
    const copy = btn(drafts.length > 1 ? `Copy draft 1 of ${drafts.length}` : "Copy draft",
      () => copyText(it.draft || "", copy), { variant: "primary", sm: true, icon: "copy" });
    actions.append(copy,
      btn("Open thread", () => window.open(it.thread_url, "_blank", "noopener"),
        { sm: true, icon: "arrow-up-right" }));
  } else if ((it.detail || {}).thread_url) {
    // a promise typed on Upwork: the room it was made in is one click away
    actions.append(btn("Open on Upwork", () => window.open(it.detail.thread_url, "_blank", "noopener"),
      { sm: true, icon: "arrow-up-right" }));
  }
  actions.append(
    btn("Done", () => act(it, "done", null, ctx), { sm: true, icon: "check" }),
    btn("Snooze 3d", () => act(it, "snooze", 3, ctx), { sm: true, icon: "alarm-clock" }),
  );

  wrap.append(el("div", "q-row q-" + it.kind, [left, actions]), detail);
  return wrap;
}

/**
 * The meta line of a FOLLOW-UP card — the one line Abhijeet actually reads.
 *
 * This used to hard-code `pill("reply owed")` for every card of this kind and
 * age it off `threads.latest_message_dt`. The Cradle Company therefore read as
 * a cold prospect who had ignored us for nine days while it had a signed,
 * ACTIVE contract and a live Slack conversation. Nothing on the card was true.
 *
 * The server now ships the answer instead of leaving it to be inferred
 * (modules/chief_of_staff/evidence.py + cockpit/data.today):
 *   it.label       "client comms" | "reply owed" — the RELATIONSHIP, decided
 *                  from the contracts table, not from the card's kind.
 *   it.engagement  {state, active, contract_id, status, since, title}
 *   it.evidence    {ts, channel, ours_ts, ours_channel, channels[], unproven}
 *   it.age_days    days since their OLDEST unanswered message, cross-channel
 *
 * Three things this renders that the old line could not, and must not lose:
 *   · the CONTRACT, when one is reachable — a contracted client is never
 *     described as an unanswered prospect;
 *   · WHICH CHANNEL they last spoke on — "on slack" is the whole difference
 *     between a 4-day-old conversation and a 9-day-old silence;
 *   · `evidence.unproven` — 86 of 139 threads carry no registry client, so for
 *     most cards ONLY Upwork was searched. The card says so. Implying a
 *     cross-channel lookup that never happened is the same class of lie as the
 *     one this whole change exists to remove.
 */
function followupMeta(it, metaRow) {
  const ev = it.evidence || {};
  const eng = it.engagement || {};
  // it.label is the server's answer. Default only when the field is absent
  // (an older payload) — never override it.
  const label = it.label || "reply owed";
  const comms = label === "client comms";

  append(metaRow,
    pill(label, comms ? "ok" : "owed"),
    pill(it.tier, "tier-" + String(it.tier || "unknown").toLowerCase()));

  // ── the relationship, as the contracts table can prove it ────────────────
  if (eng.contract_id) {
    const st = eng.active
      ? `${eng.status || "ACTIVE"}${eng.since ? " since " + eng.since : ""}`
      : eng.state === "paused" ? "PAUSED"
        : eng.state === "past" ? "closed"
          : (eng.status || "");
    metaRow.appendChild(el("span", {
      class: "pill " + (eng.active ? "ok" : "outline"),
      attrs: {
        title: `contract ${eng.contract_id}`
          + (eng.title ? ` — ${eng.title}` : "")
          + (eng.status ? ` · ${eng.status}` : "")
          + (eng.since ? ` · started ${eng.since}` : ""),
      },
    }, `contract ${eng.contract_id}${st ? " · " + st : ""}`));
  } else {
    metaRow.appendChild(el("span", {
      attrs: {
        title: "no contract row is reachable from this thread. That is NOT"
          + " proof they are only a prospect — it is the safe default when we"
          + " cannot prove otherwise.",
      },
    }, "no contract on file"));
  }

  // ── when and WHERE they last spoke ───────────────────────────────────────
  if (ev.ts) {
    metaRow.appendChild(el("span", {
      attrs: { title: `their last message: ${String(ev.ts).replace("T", " ").slice(0, 16)} (${ev.channel || "channel unknown"})` },
    }, [
      "last spoke ", el("b", null, fmt.dateShort(ev.ts)),
      " on ", el("b", null, ev.channel || "an unnamed channel"),
    ]));
  } else {
    metaRow.appendChild(el("span", "dash",
      "never heard from them on any channel we read"));
  }

  // the card's CLOCK is a different question from the line above: it runs from
  // their OLDEST unanswered message, so a person holding two rooms is not aged
  // off their freshest one. null is "—", never 0.
  metaRow.appendChild(el("span", {
    attrs: {
      title: "measured from the oldest message of theirs we have not answered,"
        + " across every channel checked — not from the newest row on the"
        + " Upwork thread, which counted our own replies and Upwork's system"
        + " rows.",
    },
  }, it.age_days == null
    ? "age not measured"
    : `oldest ask ${fmt.ago(it.age_days)}`));

  // ── what WE last did, when we can see it ─────────────────────────────────
  if (ev.ours_ts) {
    metaRow.appendChild(el("span", "muted",
      `· last from us ${fmt.dateShort(ev.ours_ts)}`
      + (ev.ours_channel ? ` on ${ev.ours_channel}` : "")));
  }

  // ── how far the search actually reached. THIS IS THE HONESTY PILL ────────
  if (ev.unproven) {
    metaRow.appendChild(el("span", {
      class: "pill warn",
      attrs: { title: String(ev.unproven) },
    }, "Upwork only — not cross-checked"));
  } else if ((ev.channels || []).length) {
    metaRow.appendChild(el("span", {
      class: "pill outline",
      attrs: { title: `channels read for this counterparty: ${ev.channels.join(", ")}` },
    }, `${ev.channels.length} channels checked`));
  }

  // a collapsed two-room card ships BOTH drafts — say so, or the second one
  // is unreachable (the Dhanya / Rocky case)
  const nd = ((it.detail || {}).drafts || []).length;
  if (it.threads > 1 || nd > 1) {
    metaRow.appendChild(pill(
      `${Math.max(it.threads || 0, nd)} rooms · ${nd} drafts inside`, "outline"));
  }
}

/**
 * The expanded detail — everything known about the item, so a card answers
 * "what is this and who's it about?" on click.
 */
function queueDetail(it) {
  const dt = it.detail || {};
  const inner = el("div", "q-detail-inner");
  const kv = (k, v) => {
    if (v == null || v === "") return;
    inner.appendChild(el("div", "q-kv", [el("span", "k", k), el("span", "v", v)]));
  };

  const ev = it.evidence || {};
  const eng = it.engagement || {};

  kv("Client", dt.client || el("span", "muted", "not linked to a client"));
  kv("Type", dt.kind);
  if (dt.full_text) kv("Detail", dt.full_text);
  if (dt.promised_on) kv("Promised", `${fmt.dateShort(dt.promised_on)} on Upwork, to ${dt.to || dt.room_name || "the client"}${dt.room_name && dt.to && dt.room_name !== dt.to ? ` (${dt.room_name})` : ""}${dt.undated ? " — no date given, so it is due in a couple of days" : ""}`);
  if (dt.due) kv("Due", String(dt.due).slice(0, 10));
  // "idle" is now the CROSS-CHANNEL age (evidence.waiting_since), not the age
  // of the newest Upwork row — the label has to say which, or it re-opens the
  // exact misread it was changed to fix.
  if (dt.idle_days != null) {
    kv("Idle", `${dt.idle_days} days since their oldest unanswered message`
      + (ev.unproven ? " (Upwork only — see below)"
        : (ev.channels || []).length ? ` (across ${ev.channels.join(", ")})` : ""));
  }
  if (dt.tier) kv("Tier", dt.tier);
  // the relationship, spelled out — a contract id is a fact, not an inference
  if (eng.contract_id) {
    kv("Contract", `${eng.contract_id}`
      + (eng.title ? ` — ${eng.title}` : "")
      + (eng.status ? ` · ${eng.status}` : "")
      + (eng.since ? ` · started ${eng.since}` : "")
      + (eng.active ? " · LIVE, so this is client comms, not a sales chase" : ""));
  } else if (it.kind === "followup") {
    kv("Contract", el("span", "muted",
      "none reachable from this thread — shown on the prospect track because we"
      + " cannot prove otherwise, not because we proved they are a prospect"));
  }
  if (ev.ts) {
    kv("Last from them", `${String(ev.ts).replace("T", " ").slice(0, 16)}`
      + ` on ${ev.channel || "an unnamed channel"}`);
  }
  if (ev.ours_ts) {
    kv("Last from us", `${String(ev.ours_ts).replace("T", " ").slice(0, 16)}`
      + ` on ${ev.ours_channel || "an unnamed channel"}`);
  }
  // HOW FAR THE SEARCH REACHED. Never let the card imply more than was read.
  if (ev.unproven) {
    kv("Evidence", el("span", "neg", String(ev.unproven)
      + " — everything above is Upwork-only for this card."));
  } else if ((ev.channels || []).length) {
    kv("Evidence", `channels read: ${ev.channels.join(", ")}`);
  }
  if (dt.source) kv("From", dt.source);          // e.g. the Fathom call cite
  if (dt.job_url) kv("Job", extLink(dt.job_url, "open job"));

  // EVERY room this one card stands for (brief §4). One card, N threads.
  const urls = dt.thread_urls || (dt.thread_url ? [dt.thread_url] : []);
  if (urls.length === 1) kv("Thread", extLink(urls[0], "open in Upwork"));
  else if (urls.length > 1) {
    kv("Threads", el("span", "hstack",
      urls.map((u, i) => extLink(u, `room ${i + 1}`))));
  }

  // EVERY draft. Collapsing a person's two rooms must not make the second
  // room's pre-written reply unreachable.
  const drafts = dt.drafts && dt.drafts.length
    ? dt.drafts
    : (dt.draft ? [{ body: dt.draft, topic: null, thread_url: dt.thread_url }] : []);
  drafts.forEach((dr, i) => {
    const body = el("div", "q-draft-body", dr.body || "");
    const copy = btn("Copy", () => copyText(dr.body || "", copy), { sm: true, icon: "copy" });
    inner.appendChild(el("div", "q-draft", [
      el("div", "q-draft-head", [
        el("span", "k", drafts.length > 1
          ? `Draft ${i + 1} of ${drafts.length}${dr.topic ? " — " + dr.topic : ""}`
          : "Draft reply"),
        el("div", "hstack-tight", [
          dr.thread_url ? extLink(dr.thread_url, "thread") : null,
          copy,
        ]),
      ]),
      body,
    ]));
  });

  if (!inner.childNodes.length) {
    inner.appendChild(el("div", "muted small", "No further detail was recorded for this card."));
  }
  return inner;
}

/* ── actions: act() and closure() live in ../conversation.js — the one
   place both screens post Done / Snooze / closure from ──────────────── */

/* ── the merged Nudges overflow ───────────────────────────────────────────── */

/**
 * The rest of the ranked Chief-of-Staff queue, rendered in place. Cards already
 * shown above are filtered out by dedup_key: the same fact twice is exactly
 * what made the old two-tab arrangement read as noise.
 */
async function renderMore(body, d, ctx) {
  const n = await ctx.api("/api/nudges");
  const shown = new Set((d.queue || [])
    .map((x) => String(x.item_key || "").replace(/^nudge:/, "")));
  const entries = (n.entries || []).filter((e) => !shown.has(e.dedup_key));

  clear(body);
  body.appendChild(el("div", "muted small", [
    "Chief of Staff — ranked by urgency. It surfaces; you decide.",
    entries.length !== (n.entries || []).length
      ? ` (${(n.entries || []).length - entries.length} already shown above)` : "",
  ]));

  if (!entries.length) {
    body.appendChild(emptyState("Queue is clear. ✓", { icon: "circle-check" }));
  }

  entries.forEach((e) => {
    const head = el("div", "between", [
      el("div", "grow", [
        el("div", "hstack", [
          pill(e.kind, "outline"),
          el("b", null, e.title || "(untitled)"),
          e.due ? el("span", "muted small", "· due " + String(e.due).slice(0, 10)) : null,
        ]),
        e.action ? el("div", "muted small", e.action) : null,
      ]),
      el("span", "muted small nowrap", "u=" + Math.round(e.urgency || 0)),
    ]);

    const box = el("div", { class: "card", style: { marginTop: "8px" } }, head);
    if (e.grouped) {
      // the grouped-nudge expander, brought across from the Nudges tab
      const det = el("details", "group");
      det.appendChild(el("summary", null, `expand ${e.count} items`));
      (e.items || []).forEach((item) => {
        det.appendChild(el("div", "between", [
          el("div", "grow", [
            item.title || "(untitled)",
            item.due ? el("span", "muted small", " · due " + String(item.due).slice(0, 10)) : null,
          ]),
          nudgeActions(item.dedup_key, ctx),
        ]));
      });
      box.appendChild(det);
    } else {
      box.appendChild(el("div", { style: { marginTop: "10px" } }, nudgeActions(e.dedup_key, ctx)));
    }
    body.appendChild(box);
  });

  await renderBrief(body, ctx, n);
}

function nudgeActions(dedupKey, ctx) {
  const row = el("div", "hstack-tight");
  [["Done", "done", "check"], ["Snooze", "snooze", "alarm-clock"], ["Dismiss", "dismiss", "x"]]
    .forEach(([label, action, ic]) => {
      row.appendChild(btn(label, async () => {
        try { await ctx.post("/api/cos-nudge", { dedup_key: dedupKey, action }); ctx.refresh(); }
        catch (e) { toast("That action did not stick", true); }
      }, { sm: true, icon: ic }));
    });
  return row;
}

/** the latest Chief-of-Staff brief, replayed. Server-rendered — our own HTML. */
async function renderBrief(body, ctx, preloaded) {
  const n = preloaded || await ctx.api("/api/nudges");
  const lb = n.latest_brief;
  if (!lb) {
    if (!preloaded) {
      clear(body).appendChild(emptyState("No brief has been drafted yet.", { icon: "file-text" }));
    }
    return;
  }
  body.appendChild(el("div", { class: "card", style: { marginTop: "12px" } }, [
    el("div", "card-head", [el("h3", "card-title", "Latest brief (draft)"),
      el("span", "card-sub", lb.name || "")]),
    // trusted: this HTML is produced by modules/chief_of_staff/brief.py
    el("div", { class: "brief-html", html: lb.html || "" }),
  ]));
}

/* ── the paused-aware lead backlog ────────────────────────────────────────── */

function backlog(d, ctx) {
  const b = d.backlog || {};
  const out = [];

  out.push(b.paused
    ? heading("Ready when you resume",
        `— applications paused by intent since ${b.paused_since || ""};`
        + " these wait, they don't nag", "h3")
    : heading("Hot leads to work", "— top by ICP then score", "h3"));

  const box = el("div", "card flush");
  const freshDays = b.fresh_days || 10;
  const since = String(b.accumulated_since || "").slice(0, 10);

  // ── ONE figure unless there are genuinely two windows ────────────────────
  // The server sets the accumulated window to the fresh window whenever
  // applications are NOT paused (data.py: `acc_lo = paused_since if paused
  // else fresh_lo`). Both counts then come from the same query over the same
  // days, and /api/today/rows?which=fresh and ?which=accumulated return
  // byte-identical rows. Rendering them side by side as "273 since X · 273
  // still fresh" presented one measurement twice and invited the reader to
  // conclude that nothing had expired. There is only a second window once a
  // pause has actually started.
  const ctxLine = el("div", "backlog-ctx");
  if (b.paused) {
    const accBtn = linkNum(fmt.num(b.accumulated || 0),
      () => openLeadRows(ctx, "accumulated",
        "Accumulated hot leads since the pause"));
    const freshBtn = linkNum(fmt.num(b.fresh_count || 0),
      () => openLeadRows(ctx, "fresh", "Fresh hot leads (still live)"));
    ctxLine.append(
      accBtn, ` hot leads have piled up since the pause on ${since} · only `,
      freshBtn, ` are still inside the ${freshDays}-day fresh window · `,
      el("b", null, fmt.num(b.icp || 0)), " ICP · ",
      document.createTextNode(`${fmt.num(b.icp_near || 0)} near-miss`),
    );
    box.appendChild(ctxLine);
  } else {
    const freshBtn = linkNum(fmt.num(b.fresh_count || 0),
      () => openLeadRows(ctx, "fresh",
        `Hot leads scored in the last ${freshDays} days`));
    ctxLine.append(
      freshBtn, ` hot leads scored in the last ${freshDays} days`,
      since ? ` (since ${since})` : "", ` · `,
      el("b", null, fmt.num(b.icp || 0)), " ICP · ",
      document.createTextNode(`${fmt.num(b.icp_near || 0)} near-miss`),
    );
    box.append(ctxLine, el("div", { class: "muted small", style: { padding: "0 var(--space-5) 8px" } },
      `Applications are not paused, so there is no backlog window separate from`
      + ` this one — the accumulated count and the fresh count would be the same`
      + ` ${freshDays} days and the same rows. One number, not two. A second`
      + ` figure appears the moment a pause starts and leads begin to age out.`));
  }

  const items = b.items || [];
  if (!items.length) {
    box.appendChild(emptyState("No fresh hot leads in the window.", { icon: "inbox" }));
  }
  items.forEach((h) => {
    const right = el("div", "lead-right");
    if (h.icp_tier) right.appendChild(pill(h.icp_tier, h.icp_tier === "icp" ? "icp" : "outline"));
    right.appendChild(pill("score " + h.score, "ok"));
    if (h.job_url) {
      right.appendChild(btn("Open job", () => window.open(h.job_url, "_blank", "noopener"),
        { sm: true, icon: "arrow-up-right" }));
    }
    box.appendChild(el("div", "lead-row", [
      el("div", "grow", [
        el("strong", null, h.title || h.id),
        h.has_draft ? el("span", "muted small", " · draft ready") : null,
      ]),
      right,
    ]));
  });

  out.push(box);
  return out;
}

async function openLeadRows(ctx, which, title) {
  const res = await ctx.api("/api/today/rows?which=" + encodeURIComponent(which));
  const rows = res.rows || [];
  const total = res.total == null
    ? (res.count == null ? rows.length : res.count)
    : res.total;
  openDrill({
    title,
    subtitle: res.since ? `every hot lead first scored on or after ${res.since}` : null,
    rows,
    total,
    columns: leadCols,
    time: (r) => r.d,
    search: (r) => `${r.title || ""} ${r.id || ""} ${r.icp_tier || ""}`,
    // The server caps this read at 300 and orders it ICP tier → score → date
    // (data.today_rows). Without this the drill would fall back to drill.js's
    // default "showing the latest 300", which is a claim about ORDER and would
    // be false — the rows that are missing are the low-scoring ones, not the
    // old ones. capNote exists precisely to stop that sentence being printed.
    capNote: total > rows.length
      ? `The server capped this read at ${fmt.num(rows.length)} of`
        + ` ${fmt.num(total)} rows and ranked them ICP tier first, then score,`
        + ` then date — so these are the BEST ${fmt.num(rows.length)}, not the`
        + ` latest. The missing rows are the weakest ones, not the oldest. The`
        + ` count in the header is the true total.`
      : null,
    emptyText: "No rows behind this figure.",
  });
}

/* ── at a glance ──────────────────────────────────────────────────────────── */

function atAGlance(d, m) {
  const grid = el("div", "grid-4");
  const a = m.applied || {}, rev = m.revenue || {}, act2 = m.active || {};

  // 1) Applied — the connects rail is the one applied-truth source
  grid.appendChild(metricCard({
    label: "Applied (7d)",
    value: a.paused ? "paused" : dash(a.week, fmt.num),
    unmeasured: a.paused || a.week == null,
    note: a.note || `${a.today || 0} today · ${a.last_week || 0} prev week`,
  }));

  // 2) Revenue MTD — when finance-doctor is RED, money() omits `revenue`
  //    entirely. A missing figure is "—" with the reason, never $0.
  const haveRev = rev.mtd_usd != null;
  grid.appendChild(metricCard({
    label: "Revenue MTD",
    value: haveRev
      ? el("span", null, [fmt.usd(rev.mtd_usd),
          el("small", null, " / " + dash(rev.target_usd, fmt.usd))])
      : "—",
    unmeasured: !haveRev,
    bar: haveRev ? Math.min(100, rev.pct || 0) : null,
    note: haveRev
      ? (rev.pct != null ? `${fmt.pct(rev.pct)} of target` : null)
      : "not measured here — the finance read is gated (finance-doctor RED). Money has the detail.",
  }));

  // 3) Active pipeline
  grid.appendChild(metricCard({
    label: "Active pipeline",
    value: el("span", null, [dash(act2.proposals, fmt.num), el("small", null, " proposals")]),
    note: `${dash(act2.interviews, fmt.num)} interviews · ${dash(act2.contracts, fmt.num)} contracts`,
  }));

  // 4) Your queue — the TRUE total, not the truncated top-N shown above.
  //    (v1 read metrics.queue_total, which the server never sets, so this tile
  //    rendered a hard 0 forever. A fabricated zero is the one thing the
  //    cockpit may not do.)
  const total = d.queue_total;
  grid.appendChild(metricCard({
    label: "Your queue",
    value: dash(total, fmt.num),
    unmeasured: total == null,
    note: total === 0
      ? "a true zero — nothing is open, and no card was suppressed"
      : `${dash(m.follow_ups_due, fmt.num)} replies owed · `
        + `${dash(m.backlog_fresh, fmt.num)} fresh leads waiting`
        + (d.queue_hidden_no_provenance
            ? ` · ${d.queue_hidden_no_provenance} hidden (no evidence)` : ""),
  }));

  return grid;
}
