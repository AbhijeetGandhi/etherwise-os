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
import { funnel as funnelShape, ring, rings, hbars, runway } from "../shapes.js";

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

  // The gate as a shape (2026-11-02): where we are on the 90 days, and how
  // many of the five conversations are in — then the ladder a conversation
  // climbs, as a funnel. Every figure still drills.
  return el("div", "stack", [
    heading("Results", `— the gate is ${r.target} buyer conversations by ${fmt.date(r.day90)}`, "h3"),
    el("div", "li-gate", [
      ring({ label: "Buyer conversations", value: r.buyer_conversations, target: r.target, size: 88,
        note: r.buyer_conversations ? `${r.days_left} days left` : [`${r.days_left} days left · `, r.note || "none marked yet"],
        onClick: () => drill("Buyer conversations", r.rows.buyer, messageCols, {
          note: "A message whose 'Buyer conversation' box is ticked: a founder, COO or ops leader describing a real problem.",
          empty: "None ticked yet. When a reply is from a plausible buyer with a real problem, tick 'Buyer conversation' on the message in Airtable." }) }),
      runway({ label: "The 90 days", at: r.day_n, of: 90, sub: `from ${fmt.date(r.day0)}`,
        marks: [{ at: 30, label: "day 30" }, { at: 60, label: "day 60" }, { at: 90, label: `gate · ${fmt.dateShort(r.day90)}` }],
        note: "Comments first, DMs only after a reply — the conversations are expected to come late in the window." }),
    ]),
    funnelShape([
      { label: "Messages sent", short: "messages", n: r.messages,
        sub: r.messages ? `${r.replied} replied${typeLine ? " · " + typeLine : ""}` : "no DMs or requests yet",
        onClick: () => drill("Every message", r.rows.messages, messageCols, {
          empty: "No DMs or connection requests yet. Aastha's rule: DM only after 2+ comments and a reply." }) },
      { label: "Replied", short: "replies", n: r.replied, onClick: () => drill("Every message", r.rows.messages, messageCols) },
      { label: "Calls booked", short: "calls", n: r.calls_booked, sub: r.calls_booked ? "from LinkedIn messages" : "none yet",
        onClick: () => drill("Calls booked", r.rows.calls, messageCols) },
      { label: "Leads", short: "leads", n: sumB("Lead"),
        onClick: () => drill("Became a lead or more", r.rows.messages.filter((m) => m.became), messageCols, {
          empty: "No conversation has become a lead yet. Set 'Became' and 'CRM name' on the message when one does." }) },
      { label: "Proposals sent", short: "proposals", n: sumB("Proposal sent") },
      { label: "Clients", n: sumB("Client"), tone: "good", sub: attrib() },
    ], { compact: true, title: "From a message to a client" }),
  ]);
}

/* ── 2. pace ──────────────────────────────────────────────────────────────── */

function pace(p) {
  const c = card(null, null);
  append(c, el("div", "li-pace", [
    rings([
      { label: "Comments today", value: p.comments_today, target: p.comments_target_day,
        note: "target 10 a day (28 Oct)",
        onClick: () => drill("Comments today", p.rows.comments_today, commentCols) },
      { label: "Comments, last 7 days", value: p.comments_7d, target: p.comments_target_7d,
        note: [el("b", null, `${p.days_at_target_7d} of 7`), " days at target"],
        onClick: () => drill("Comments in the last 7 days", p.rows.comments_7d, commentCols) },
      { label: "Posts this week", value: p.posts_this_week, target: p.posts_target_week,
        note: `week of ${fmt.dateShort(p.week_of)} · Aastha's scope is 3–4`,
        onClick: () => drill("Posts this week", p.rows.posts_this_week, postCols) },
    ]),
    el("div", "li-weeks", [
      el("div", "ring-l", "Posts, last 4 weeks"),
      hbars(p.posts_by_week.map((w, i, all) => ({
        label: i === all.length - 1 ? "this week" : `week of ${fmt.dateShort(w.week_of)}`, n: w.posts,
        tone: w.posts >= p.posts_target_week ? "good" : null })), { max: Math.max(p.posts_target_week, ...p.posts_by_week.map((w) => w.posts)) }),
    ]),
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
    funnelShape([
      { label: "Posts considered", short: "posts considered", n: f.candidates,
        onClick: () => drill("Every post considered", f.rows.candidates, candidateCols, { wide: true }) },
      { label: "Comments posted", short: "comments", n: f.commented,
        onClick: () => drill("Every comment", f.rows.comments, commentCols, { wide: true }) },
      { label: "Author replied", short: "replies", n: f.author_replied,
        onClick: () => drill("Comments the author replied to", f.rows.replied, commentCols, { wide: true }) },
      { label: "Author liked", n: f.author_liked, tone: "good", sub: f.liked_note ? "not filled in yet?" : null,
        onClick: () => drill("Comments the author liked", f.rows.liked, commentCols) },
    ], { compact: true, title: "Commenting funnel",
      drops: [{ n: Math.max(0, f.candidates - f.commented), label: "skipped or still to revisit" },
        { n: Math.max(0, f.commented - f.author_replied), label: "no reply from the author" }] }),
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
  const fit = Object.entries(r.by_icp).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ");
  const conn = Object.entries(r.by_connection).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ");
  return el("div", "stack", [
    heading("Relationships", `— ${r.people} people watched · fit: ${fit} · ${conn}`, "h3"),
    el("div", "grid-2 li-rel", [
      card("By stage — the ladder a person climbs", (() => {
        // the exits (paused, not a fit) are not rungs: they come off the ladder
        // and are said beside it, so "not a fit" cannot read as the green goal
        const isExit = (x) => /paused|not a fit|lost|closed/i.test(x.stage);
        const ladder = stageRows.filter((x) => !isExit(x)), exits = stageRows.filter(isExit);
        const stageDrill = (x) => (x.n ? () => drill(`Stage: ${x.stage}`, r.rows["stage:" + x.stage] || [], peopleCols, { wide: true }) : null);
        return el("div", "stack-2", [
          funnelShape(ladder.map((x) => ({ label: x.stage, short: x.stage.toLowerCase(), n: x.n,
            tone: /client/i.test(x.stage) ? "good" : null, onClick: stageDrill(x) })),
            { compact: true, floor: 0.08, title: "People by stage" }),
          exits.length ? el("div", "muted small", ["Off the ladder: ", exits.map((x, i) => [i ? " · " : "",
            x.n ? linkNum(x.n, stageDrill(x), { label: `people ${x.stage}` }) : el("b", null, "0"), ` ${x.stage.toLowerCase()}`])]) : null,
        ]);
      })()),
      el("div", "stack", [
        card("By segment", hbars(segRows.map((x) => ({ label: x.segment, n: x.n,
          onClick: x.n ? () => drill(`Segment: ${x.segment}`, r.rows["segment:" + x.segment] || [], peopleCols, { wide: true }) : null })))),
        card("Next action (Aastha's rule, from the tracker)", hbars(nextRows.map((x) => ({ label: x.next, n: x.n,
          onClick: x.n ? () => drill(`Next action: ${x.next}`, r.rows["next:" + x.next] || [], peopleCols, { wide: true }) : null })))),
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
