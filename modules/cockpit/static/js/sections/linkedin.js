// ═══════════════════════════════════════════════════════════════════════════
// sections/linkedin.js — the second channel, measured like the first.
//
// Spec: etherwise-v3-linkedin-brief.md. Data: /api/linkedin, a read-only view
// of the local copy the linkedin_sync rail keeps of the Airtable tracker. The
// social sessions edit Airtable; this page only reads.
//
// THE ORDER OF THE PAGE IS THE ORDER OF WHAT MATTERS (business/social/CONTEXT
// §2): LinkedIn exists to bring suitable paying clients. So results come first
// (buyer conversations against the day-90 gate of five), then pace (10
// comments a day, his call 2026-10-28), then the commenting funnel, the
// relationships, our posts and the profile. Reactions and followers are
// diagnostics and sit lower on purpose.
//
// `—` = not measured · `0` = a real zero, and it says why. Every figure opens
// the rows it was counted from (they ship with the payload — no second route).
// Nothing here sends, posts, comments or DMs.
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

import {
  el, append, fmt, dash, heading, card, kpi, pill, btn, extLink, table,
  emptyState, notice, linkNum,
} from "../ui.js";
import { openDrill } from "../drill.js";
import { chart, axes } from "../charts.js";

export const meta = { title: "LinkedIn", hint: "the second channel — paying clients, not followers" };

/* ── drill columns ────────────────────────────────────────────────────────── */

const short = (s, n = 140) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s);
const link = (url, text) => (url ? extLink(url, text || "open") : (text || null));

const FIT_TONE = { Strong: "ok", Possible: "info", Weak: "outline" };
const VERDICT_TONE = { Comment: "ok", Brainstorm: "warn", Revisit: "info", Posted: "outline", Skipped: "outline" };

const peopleCols = [
  { key: "name", label: "Person", render: (r) => link(r.url, r.name) },
  { key: "company", label: "Company", cls: "muted" },
  { key: "segment", label: "Segment", cls: "muted" },
  { key: "icp", label: "Fit", render: (r) => (r.icp ? pill(r.icp, FIT_TONE[r.icp]) : null) },
  { key: "stage", label: "Stage" },
  { key: "comments", label: "Comments", num: true },
  { key: "replied", label: "Replies", num: true },
  { key: "next_action", label: "Next", render: (r) => el("span", "muted small", r.next_action || "—") },
];

const commentCols = [
  { key: "at", label: "When (IST)", render: (r) => el("span", "muted", fmt.dateTimeShort(r.at)) },
  { key: "person", label: "On whose post", render: (r) => link(r.url, r.person) },
  { key: "angle", label: "Angle", cls: "muted" },
  { key: "hours_after_post", label: "Hours after post", num: true },
  { key: "impressions", label: "Impressions", num: true },
  { key: "author_replied", label: "Author",
    render: (r) => (r.author_replied ? pill("replied", "ok")
      : (r.author_liked ? pill("liked", "info") : el("span", "muted", "—"))) },
];

const candidateCols = [
  { key: "found_on", label: "Found", render: (r) => el("span", "muted", fmt.dateShort(r.found_on)) },
  { key: "post", label: "Post", render: (r) => link(r.url, r.post) },
  { key: "industry", label: "Industry", cls: "muted" },
  { key: "verdict", label: "Verdict", render: (r) => (r.verdict ? pill(r.verdict, VERDICT_TONE[r.verdict]) : null) },
  { key: "engagement", label: "When found", cls: "muted small" },
];

const messageCols = [
  { key: "sent_on", label: "Sent", render: (r) => el("span", "muted", fmt.dateShort(r.sent_on)) },
  { key: "person", label: "To" },
  { key: "type", label: "Type", cls: "muted" },
  { key: "replied", label: "Replied", render: (r) => (r.replied ? pill("yes", "ok") : el("span", "muted", "no")) },
  { key: "outcome", label: "Outcome" },
  { key: "buyer", label: "Buyer?", render: (r) => (r.buyer ? pill("buyer", "ok") : null) },
  { key: "became", label: "Became", render: (r) => (r.became ? `${r.became}${r.attribution ? " · " + r.attribution : ""}` : null) },
];

const postCols = [
  { key: "at", label: "Posted (IST)", render: (r) => el("span", "muted", fmt.dateTimeShort(r.at)) },
  { key: "post", label: "Post", render: (r) => link(r.url, short(r.post, 70)) },
  { key: "impressions", label: "Impressions", num: true, render: (r) => dash(r.impressions, fmt.num) },
  { key: "impr_24h", label: "At 24h", num: true, render: (r) => dash(r.impr_24h, fmt.num) },
  { key: "impr_7d", label: "At 7 days", num: true, render: (r) => dash(r.impr_7d, fmt.num) },
  { key: "engagement_rate", label: "Engagement", num: true, render: (r) => dash(r.engagement_rate, (v) => fmt.pct(v, 1)) },
  { key: "buyer_comments", label: "Buyer comments", num: true },
  { key: "conversations", label: "Conversations", num: true },
  { key: "followers_gained", label: "Followers +", num: true },
];

function drill(title, rows, columns, opts = {}) {
  openDrill({
    title, rows, total: rows.length, columns,
    subtitle: opts.subtitle, note: opts.note, wide: opts.wide,
    search: true, emptyText: opts.empty || "Nothing behind this figure yet.",
  });
}

/* ── the page ─────────────────────────────────────────────────────────────── */

export async function render(root, ctx) {
  const d = await ctx.api("/api/linkedin");

  const refresh = btn("Refresh from Airtable", null, { sm: true, icon: "refresh-cw" });
  refresh.addEventListener("click", () => ctx.kick("/api/rerun", { task: "linkedin_sync" },
    refresh, { refresh: ctx.refresh }));

  root.append(heading("LinkedIn", "— the second channel: suitable paying clients, not followers"));

  if (d.never_synced) {
    root.append(notice("warn", d.note || "Not synced yet.", { action: refresh }));
    return;
  }

  root.append(el("div", "srcline ok", [
    el("div", "grow", [
      "Copied from the Airtable tracker ", el("b", null, fmt.relative(d.as_of)),
      el("span", "muted", ` (${fmt.dateTime(d.as_of)}) · every 2h, 12 PM–4 AM · read-only — the social sessions edit `),
      extLink(d.base_url, "the tracker"),
    ]),
    refresh,
  ]));

  root.append(
    needsYou(d.needs_you),
    results(d.results),
    pace(d.pace),
    funnel(d.funnel),
    relationships(d.relationships),
    posts(d.posts),
    profile(d.profile),
  );
}

/* ── needs you (the same block Today shows) ───────────────────────────────── */

export function needsYouItems(n) {
  const items = [];
  (n.brainstorm || []).forEach((c) => items.push(el("div", "li-item", [
    pill("Your input", "warn"),
    el("div", "grow", [
      el("div", null, [link(c.url, c.post), el("span", "muted small", ` · ${c.who || ""}`)]),
      c.question ? el("div", "small", c.question) : null,
    ]),
    el("span", "muted small", fmt.ago(c.days_old)),
  ])));
  (n.drafts || []).forEach((c) => items.push(el("div", "li-item", [
    pill("Draft ready", "ok"),
    el("div", "grow", [
      el("div", null, [link(c.url, c.post), el("span", "muted small", ` · ${c.who || ""}`)]),
      c.draft ? el("div", "small muted", short(c.draft, 220)) : null,
    ]),
    el("span", "muted small", fmt.ago(c.days_old)),
  ])));
  (n.people || []).forEach((p) => items.push(el("div", "li-item", [
    pill(p.next_action && p.next_action.startsWith("Ready") ? "Ready to DM" : "Conversation", "info"),
    el("div", "grow", [
      el("div", null, [link(p.url, p.name), el("span", "muted small", ` · ${p.company || ""}`)]),
      el("div", "small muted", p.next_action),
    ]),
    p.days_since_touch != null ? el("span", "muted small", fmt.ago(p.days_since_touch)) : null,
  ])));
  return items;
}

function needsYou(n) {
  if (!n) return el("span");
  const items = needsYouItems(n);
  const older = (n.older || {});
  const olderN = (older.brainstorm || 0) + (older.drafts || 0);
  const box = card(`Needs you — ${n.total}`, items.length
    ? el("div", "li-list", items)
    : emptyState("Nothing on LinkedIn is waiting on you: no brainstorm questions, no drafts"
      + " ready, nobody ready for a DM.", { icon: "circle-check" }), { class: "flush" });
  if (olderN) {
    append(box, el("div", "muted small li-foot",
      `${olderN} older item${olderN === 1 ? "" : "s"} (found more than ${n.fresh_days} days ago) not listed — a LinkedIn post is cold by then.`));
  }
  return box;
}

/* ── 1. results ───────────────────────────────────────────────────────────── */

function results(r) {
  const became = r.became || {};
  const sumB = (k) => Object.values(became[k] || {}).reduce((s, v) => s + v, 0);
  const attrib = () => {
    const by = {};
    Object.values(became).forEach((m) => Object.entries(m).forEach(([a, v]) => { by[a] = (by[a] || 0) + v; }));
    const parts = Object.entries(by).map(([a, v]) => `${v} ${a.replace("LinkedIn-", "")}`);
    return parts.length ? parts.join(" · ") : "originated and influenced are counted apart";
  };
  const typeLine = Object.entries(r.by_type || {}).map(([t, v]) => `${v.sent} ${t.toLowerCase()}${v.sent === 1 ? "" : "s"}`).join(" · ");

  return el("div", "stack", [
    heading("Results", `— day ${r.day_n} of 90 · the gate is ${r.target} buyer conversations by ${fmt.date(r.day90)}`, "h3"),
    el("div", "grid-4", [
      kpi({
        label: "Buyer conversations", value: `${r.buyer_conversations} of ${r.target}`,
        sub: r.buyer_conversations ? `${r.days_left} days left`
          : `${r.days_left} days left · ${r.note || "none marked yet"}`,
        onClick: () => drill("Buyer conversations", r.rows.buyer, messageCols, {
          note: "A message whose 'Buyer conversation' box is ticked: a founder, COO or ops leader describing a real problem.",
          empty: "None ticked yet. When a reply is from a plausible buyer with a real problem, tick 'Buyer conversation' on the message in Airtable." }),
      }),
      kpi({
        label: "Messages sent", value: fmt.num(r.messages),
        sub: r.messages ? `${r.replied} replied${typeLine ? " · " + typeLine : ""}` : "no DMs or requests yet",
        onClick: () => drill("Every message", r.rows.messages, messageCols, {
          empty: "No DMs or connection requests yet. Aastha's rule: DM only after 2+ comments and a reply." }),
      }),
      kpi({
        label: "Calls booked", value: fmt.num(r.calls_booked),
        sub: r.calls_booked ? "from LinkedIn messages" : "none yet",
        onClick: () => drill("Calls booked", r.rows.calls, messageCols),
      }),
      kpi({
        label: "Leads → clients",
        value: `${sumB("Lead")} · ${sumB("Proposal sent")} · ${sumB("Client")}`,
        sub: `leads · proposals · clients — ${attrib()}`,
        onClick: () => drill("Became a lead or more", r.rows.messages.filter((m) => m.became), messageCols, {
          empty: "No conversation has become a lead yet. Set 'Became' and 'CRM name' on the message when one does." }),
      }),
    ]),
  ]);
}

/* ── 2. pace ──────────────────────────────────────────────────────────────── */

function pace(p) {
  const c = card(null, null);
  append(c, el("div", "grid-4", [
    kpi({ label: "Comments today", value: `${p.comments_today} of ${p.comments_target_day}`,
      sub: "target 10 a day (28 Oct)",
      onClick: () => drill("Comments today", p.rows.comments_today, commentCols) }),
    kpi({ label: "Comments, last 7 days", value: `${p.comments_7d} of ${p.comments_target_7d}`,
      sub: `${p.days_at_target_7d} of 7 days at target`,
      onClick: () => drill("Comments in the last 7 days", p.rows.comments_7d, commentCols) }),
    kpi({ label: "Posts this week", value: `${p.posts_this_week} of ${p.posts_target_week}`,
      sub: `week of ${fmt.dateShort(p.week_of)} · Aastha's scope is 3–4`,
      onClick: () => drill("Posts this week", p.rows.posts_this_week, postCols) }),
    kpi({ label: "Posts, last 4 weeks",
      value: p.posts_by_week.map((w) => w.posts).join(" · "),
      sub: "oldest → this week" }),
  ]));
  const host = el("div");
  append(c, host);
  const labels = p.by_day.map((x) => fmt.dateShort(x.day));
  chart(host, (pl) => ({
    type: "bar",
    data: {
      labels,
      datasets: [
        { type: "bar", label: "Comments", data: p.by_day.map((x) => x.n),
          backgroundColor: p.by_day.map((x) => (x.n >= p.comments_target_day ? pl.success : pl.color(0))),
          borderRadius: 3, maxBarThickness: 28 },
        { type: "line", label: `Target (${p.comments_target_day}/day)`,
          data: p.by_day.map(() => p.comments_target_day),
          borderColor: pl.target || pl.muted, borderDash: [4, 4], borderWidth: 1.5,
          pointRadius: 0, fill: false },
      ],
    },
    options: { scales: axes(pl), plugins: { legend: { display: true, position: "bottom" } } },
  }), { height: 190 });
  return el("div", "stack", [heading("Pace", "— comments a day, posts a week", "h3"), c]);
}

/* ── 3. commenting funnel ─────────────────────────────────────────────────── */

function funnel(f) {
  const step = (label, n, onClick, sub) => kpi({ label, value: fmt.num(n), sub, onClick });
  const v = f.by_verdict || {};
  const verdictLine = el("div", "li-verdicts", Object.keys(v).map((k) => el("span", null, [
    pill(k, VERDICT_TONE[k]), " ",
    linkNum(v[k], () => drill(`Verdict: ${k}`, f.rows["verdict:" + k] || [], candidateCols),
      { label: `posts with verdict ${k}` }),
  ])));
  const evl = f.early_vs_late || {};
  const timing = [
    "Median ", el("b", null, f.median_hours_after_post == null ? "—" : `${f.median_hours_after_post}h`),
    " between a post going up and our comment. Comments within 24h: ",
    el("b", null, `${evl.within_24h.n}`), " (median ",
    el("b", null, dash(evl.within_24h.median_impr, fmt.num)), " impressions) vs later: ",
    el("b", null, `${evl.after_24h.n}`), " (median ",
    el("b", null, dash(evl.after_24h.median_impr, fmt.num)), ").",
    evl.small_sample ? el("span", "muted", " Small sample — a hint, not a rule yet.") : null,
  ];
  return el("div", "stack", [
    heading("Commenting funnel", "— every post considered, what we did, what came back", "h3"),
    el("div", "grid-4", [
      step("Posts considered", f.candidates, () => drill("Every post considered", f.rows.candidates, candidateCols, { wide: true })),
      step("Comments posted", f.commented, () => drill("Every comment", f.rows.comments, commentCols, { wide: true })),
      step("Author replied", f.author_replied,
        () => drill("Comments the author replied to", f.rows.replied, commentCols, { wide: true }),
        f.reply_rate == null ? null : `${fmt.pct(f.reply_rate, 0)} of comments`),
      step("Author liked", f.author_liked, () => drill("Comments the author liked", f.rows.liked, commentCols),
        f.liked_note ? "not filled in yet?" : null),
    ]),
    card(null, el("div", "stack-2", [
      verdictLine,
      el("div", "small", timing),
      f.impressions_total != null
        ? el("div", "small muted", `Our comments have been seen ${fmt.num(f.impressions_total)} times in total (median ${fmt.num(f.impressions_median)} each).`)
        : null,
      f.liked_note ? el("div", "small muted", `"Author liked our comment" is ${f.liked_note}.`) : null,
    ])),
  ]);
}

/* ── 4. relationships ─────────────────────────────────────────────────────── */

function relationships(r) {
  const stageRows = Object.entries(r.by_stage).map(([stage, n]) => ({ stage, n }));
  const segRows = Object.entries(r.by_segment).map(([segment, n]) => ({ segment, n }));
  const nextRows = Object.entries(r.by_next_action).map(([next, n]) => ({ next, n }));
  const cnt = (rows, key, prefix, title) => ({
    key: "n", label: "People", num: true,
    render: (x) => (x.n ? linkNum(x.n, () => drill(`${title}: ${x[key]}`, r.rows[prefix + x[key]] || [], peopleCols, { wide: true }),
      { label: `${title} ${x[key]}` }) : el("span", "muted", "0")),
  });
  const fit = Object.entries(r.by_icp).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ");
  const conn = Object.entries(r.by_connection).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ");
  return el("div", "stack", [
    heading("Relationships", `— ${r.people} people watched · fit: ${fit} · ${conn}`, "h3"),
    el("div", "grid-2", [
      card("By stage", table({ rows: stageRows, columns: [{ key: "stage", label: "Stage" }, cnt(stageRows, "stage", "stage:", "Stage")] }), { class: "flush" }),
      el("div", "stack", [
        card("By segment", table({ rows: segRows, columns: [{ key: "segment", label: "Segment" }, cnt(segRows, "segment", "segment:", "Segment")] }), { class: "flush" }),
        card("Next action (Aastha's rule, from the tracker)", table({ rows: nextRows, columns: [{ key: "next", label: "Next action" }, cnt(nextRows, "next", "next:", "Next action")] }), { class: "flush" }),
      ]),
    ]),
    r.unchecked_7d
      ? notice("warn", `${r.unchecked_7d} people have not been checked for new posts in over a week.`,
        { action: btn("Who", () => drill("Not checked for posts in 7+ days", r.rows.unchecked, peopleCols), { sm: true }) })
      : null,
  ].filter(Boolean));
}

/* ── 5. our posts ─────────────────────────────────────────────────────────── */

function posts(p) {
  const body = p.posts
    ? table({ rows: p.rows, columns: postCols })
    : emptyState("No post of ours logged yet.", { icon: "file-text" });
  const sub = p.posts
    ? `— ${p.posts} post${p.posts === 1 ? "" : "s"} · ${fmt.num(p.impressions_total)} impressions · ${p.followers_gained} followers gained · ${p.buyer_comments} buyer comments · ${p.conversations} conversations started`
    : "";
  return el("div", "stack", [
    heading("Our posts", sub, "h3"),
    card(null, body, { class: "flush" }),
    p.posts ? el("div", "muted small", "The 24h and 7-day columns are fixed checkpoints, so posts of different ages compare fairly. Engagement = (reactions + comments + reposts) / impressions.") : null,
  ].filter(Boolean));
}

/* ── 6. profile ───────────────────────────────────────────────────────────── */

function profile(pr) {
  if (!pr.snapshots) {
    return el("div", "stack", [heading("Profile", "— followers and visibility over time", "h3"),
      notice("info", pr.note)]);
  }
  const host = el("div");
  const c = card(null, [
    table({ rows: [...pr.rows].reverse(), columns: [
      { key: "taken_on", label: "Taken", render: (r) => fmt.dateShort(r.taken_on) },
      { key: "followers", label: "Followers", num: true, render: (r) => dash(r.followers, fmt.num) },
      { key: "connections", label: "Connections", num: true, render: (r) => dash(r.connections, fmt.num) },
      { key: "profile_viewers_90d", label: "Profile viewers (90d)", num: true, render: (r) => dash(r.profile_viewers_90d, fmt.num) },
      { key: "post_impressions_7d", label: "Post impressions (7d)", num: true, render: (r) => dash(r.post_impressions_7d, fmt.num) },
      { key: "search_7d", label: "Search (7d)", num: true, render: (r) => dash(r.search_7d, fmt.num) },
    ] }),
    host,
  ]);
  if (pr.rows.length > 1) {
    chart(host, (pl) => ({
      type: "line",
      data: { labels: pr.rows.map((r) => fmt.dateShort(r.taken_on)),
        datasets: [{ label: "Followers", data: pr.rows.map((r) => r.followers),
          borderColor: pl.color(0), backgroundColor: pl.color(0), tension: 0.3, pointRadius: 3 }] },
      options: { scales: axes(pl) },
    }), { height: 180 });
  }
  return el("div", "stack", [heading("Profile", "— followers and visibility over time", "h3"), c]);
}
