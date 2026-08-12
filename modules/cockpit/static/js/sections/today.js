// ═══════════════════════════════════════════════════════════════════════════
// sections/today.js — the daily action screen, and the reference for every
// other section. Read app.js for the contract; this file is what it looks like.
//
// WHAT IS ON THIS PAGE, AND WHY (the copy here is load-bearing — it was written
// after specific misreads, and changing it re-opens them):
//
//   · source-health line   a quiet queue must be provably quiet, not a dead
//                          feed. It is ALWAYS rendered, including when fine.
//   · quick capture        zero friction; the next sweep classifies it.
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
  el, clear, fmt, dash, icon, heading, metricCard, pill, btn,
  linkNum, extLink, emptyState, notice, expander, toast, copyText,
} from "../ui.js";
import { openDrill } from "../drill.js";

export const meta = { title: "Today", hint: "what to act on right now" };

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

  root.append(
    heading("Today", "— what to act on right now"),
    sourceHealth(d.sources || {}),
    quickCapture(ctx),
    ...actNow(d, ctx),
    ...backlog(d, ctx),
    heading("At a glance", null, "h3"),
    atAGlance(d, m),
  );
}

/* ── source health ────────────────────────────────────────────────────────── */

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
    try {
      await ctx.post("/api/note", { text });
      input.value = "";
      toast("Captured — classified next sweep");
    } catch (e) {
      toast("Capture failed", true);
    }
    go.disabled = false;
  }, { icon: "zap" });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go.click(); });
  return el("div", "card", el("div", "field-row", [input, go]));
}

/* ── ACT NOW ──────────────────────────────────────────────────────────────── */

function actNow(d, ctx) {
  const q = d.queue || [];
  const out = [];

  out.push(heading("Act now",
    `— ${q.length ? `top ${q.length}` : "0"} by urgency`, "h3"));

  // render-time clusters over the atomic set ("5 commitments · 7 follow-ups")
  if (d.clusters && d.clusters.length) {
    const line = el("div", "q-clusters",
      d.clusters.map((c) => `${c.count} ${c.label}`).join(" · "));
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
function queueCard(it, ctx) {
  const wrap = el("div", "qitem");
  const detail = el("div", { class: "q-detail", style: { display: "none" } });

  const title = el("button", {
    class: "q-title", type: "button", attrs: { "aria-expanded": "false" },
  }, [
    icon("chevron-right", { size: 14, class: "q-expand" }),
    el("span", "txt", it.title || "(untitled)"),
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
    metaRow.append(pill("reply owed", "owed"),
      pill(it.tier, "tier-" + String(it.tier || "unknown").toLowerCase()));
    if (it.age_days != null) {
      metaRow.appendChild(el("span", null, fmt.ago(it.age_days) + " since client"));
    }
    // a collapsed two-room card ships BOTH drafts — say so, or the second one
    // is unreachable (the Dhanya / Rocky case)
    const nd = ((it.detail || {}).drafts || []).length;
    if (it.threads > 1 || nd > 1) {
      metaRow.appendChild(pill(`${Math.max(it.threads || 0, nd)} rooms · ${nd} drafts inside`, "outline"));
    }
  } else {
    metaRow.append(pill(String(it.nudge_kind || "").replace(/_/g, " "), "outline"),
      el("span", null, it.dept || ""));
    if (it.due) metaRow.append(el("span", null, " · " + fmt.due(it.due)));
    if (it.action) metaRow.appendChild(el("div", "q-actionnote", it.action));
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
  }
  actions.append(
    btn("Done", () => act(it, "done", null, ctx), { sm: true, icon: "check" }),
    btn("Snooze 3d", () => act(it, "snooze", 3, ctx), { sm: true, icon: "alarm-clock" }),
  );

  wrap.append(el("div", "q-row q-" + it.kind, [left, actions]), detail);
  return wrap;
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

  kv("Client", dt.client || el("span", "muted", "not linked to a client"));
  kv("Type", dt.kind);
  if (dt.full_text) kv("Detail", dt.full_text);
  if (dt.due) kv("Due", String(dt.due).slice(0, 10));
  if (dt.idle_days != null) kv("Idle", `${dt.idle_days} days since the client wrote`);
  if (dt.tier) kv("Tier", dt.tier);
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

/* ── actions (drafts only — nothing here sends anything) ──────────────────── */

/**
 * The queue mixes two action systems: follow-ups live in the local nudge_state
 * suppression (item_key 'followup:<key>' → /api/nudge), M4 nudges live in the
 * real nudges table (dedup_key → /api/cos-nudge). Route by kind, or the action
 * is recorded in the wrong place and the card comes straight back.
 */
async function act(item, action, snoozeDays, ctx) {
  try {
    if (item.kind === "nudge") {
      const body = { dedup_key: String(item.item_key).replace(/^nudge:/, ""), action };
      if (snoozeDays) body.snooze_days = snoozeDays;
      await ctx.post("/api/cos-nudge", body);
    } else {
      const body = { item_key: item.item_key, action };
      if (snoozeDays) body.snooze_days = snoozeDays;
      await ctx.post("/api/nudge", body);
    }
    ctx.refresh();
  } catch (e) {
    toast("That action did not stick: " + String(e.message || e), true);
  }
}

async function closure(item, action, ctx) {
  try {
    await ctx.post("/api/card/closure", { item_key: item.item_key, action });
    ctx.refresh();
  } catch (e) {
    toast("Could not record that: " + String(e.message || e), true);
  }
}

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

  // an honest context line: accumulated vs still-fresh, both drillable
  const ctxLine = el("div", "backlog-ctx");
  const accBtn = linkNum(fmt.num(b.accumulated || 0),
    () => openLeadRows(ctx, "accumulated",
      b.paused ? "Accumulated hot leads since the pause" : "Hot leads in the window"));
  const freshBtn = linkNum(fmt.num(b.fresh_count || 0),
    () => openLeadRows(ctx, "fresh", "Fresh hot leads (still live)"));
  ctxLine.append(
    accBtn, ` hot leads since ${String(b.accumulated_since || "").slice(0, 10)} · `,
    freshBtn, ` still fresh (last ${b.fresh_days || 10}d) · `,
    el("b", null, fmt.num(b.icp || 0)), " ICP · ",
    document.createTextNode(`${fmt.num(b.icp_near || 0)} near-miss`),
  );
  box.appendChild(ctxLine);

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
  openDrill({
    title,
    rows: res.rows || [],
    total: res.total == null ? (res.count == null ? (res.rows || []).length : res.count) : res.total,
    columns: leadCols,
    time: (r) => r.d,
    search: (r) => `${r.title || ""} ${r.id || ""} ${r.icp_tier || ""}`,
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
