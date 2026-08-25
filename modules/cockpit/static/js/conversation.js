// ════════════════════════════════════════════════════════════════════════════
// conversation.js — ONE conversation row for both screens (Today's queue and
// the Upwork Inbox), 2026-08-25 night.
//
// Abhijeet on the first Upwork tab: "the part that I hate most is the client
// comms and how you represent it — it should be intuitive, not a lot of data
// laid out without any structure." The old card was a title plus a machine
// sentence. This row reads the way a person reads a conversation:
//
//   [RC]  Rocky Choy  Simplebookme ↔ Make   Active client · since 24 Aug   Reply owed · today
//         “Awesome”                                     Rocky C. · Upwork · 25 Aug
//         ✎ Draft ready (55 words): “Hey Rocky, great — I'm in and…”
//         [Open on Upwork] [Copy draft] [Done] [Snooze 3d]        2 rooms with Rocky — also: …
//         ▸ click anywhere on the row: the last messages as bubbles + the full draft
//
// Everything rendered here is server-derived (cockpit/data._conversation):
// the row never infers a relationship, a state or a date on its own. Done,
// Snooze and the closure buttons post to the SAME routes the old card used, so
// both screens stay one queue. Drafts only — nothing here sends anything.
// ════════════════════════════════════════════════════════════════════════════

"use strict";

import { el, append, icon, fmt, btn, extLink, toast, copyText } from "./ui.js";

const QUOTE_LIMIT = 220;         // the collapsed row shows this much of a message
const DRAFT_PEEK = 140;          // and this much of the draft

const CHANNEL = { upwork: "Upwork", email: "email", slack: "Slack", fathom: "a call", loom: "a Loom" };

function channelName(ch) { return CHANNEL[String(ch || "").toLowerCase()] || ch || "a channel"; }

function short(text, n) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n).replace(/\s+\S*$/, "") + "…" : t;
}

function daysWord(n) {
  if (n == null) return "";
  if (n <= 0) return "today";
  if (n === 1) return "1 day";
  return `${n} days`;
}

/* ── the actions (drafts only — nothing here sends anything) ─────────────── */

/**
 * The queue mixes two action systems: follow-ups live in the local nudge_state
 * suppression (item_key 'followup:<key>' → /api/nudge), engine nudges live in
 * the real nudges table (dedup_key → /api/cos-nudge). Route by kind, or the
 * action is recorded in the wrong place and the card comes straight back.
 */
export async function act(item, action, snoozeDays, ctx) {
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

export async function closure(item, action, ctx) {
  try {
    await ctx.post("/api/card/closure", { item_key: item.item_key, action });
    ctx.refresh();
  } catch (e) {
    toast("Could not record that: " + String(e.message || e), true);
  }
}

/* ── pieces ──────────────────────────────────────────────────────────────── */

function relationshipTag(rel) {
  if (!rel) return null;
  const since = rel.since ? ` · since ${fmt.dateShort(rel.since)}` : "";
  return el("span", { class: "cv-tag " + (rel.tone || "lead"),
    attrs: { title: rel.contract_id ? `contract ${rel.contract_id}${rel.title ? " — " + rel.title : ""}` : "" } },
  rel.label + since);
}

function stateChip(st, extraOpen) {
  const kind = (st && st.kind) || "owed";
  const days = st && st.days != null ? daysWord(st.days) : "";
  const chip = el("button", {
    class: "cv-state " + kind, type: "button",
    attrs: { "aria-expanded": String(!!extraOpen), title: "show the messages and the draft" },
  }, [
    el("span", "lbl", (st && st.label) || "Reply owed"),
    days ? el("span", "days", " · " + days) : null,
    icon("chevron-down", { size: 13, class: "cv-chev" }),
  ]);
  return chip;
}

/** what THEY last said — the line the row is about */
function theirLine(c) {
  const t = c.their;
  if (!t) {
    return el("div", "cv-quote muted", "No message from them is on file yet.");
  }
  const when = fmt.dateShort(t.at);
  if (t.channel === "upwork" || !t.subject) {
    return el("div", "cv-quote", [
      el("span", "txt", `“${short(t.text || "(no text)", QUOTE_LIMIT)}”`),
      el("span", "cv-meta", `${t.sender || "them"} · ${channelName(t.channel)} · ${when}`
        + (t.room && c.threads && c.threads.length > 1 ? ` · in “${short(t.room, 40)}”` : "")),
    ]);
  }
  // another channel: we hold one line for it (a mail subject, a call title)
  const who = t.sender ? `Email from ${t.sender}` : "An email";
  return el("div", "cv-quote", [
    el("span", "txt", [
      `${who}: `, el("i", null, t.text ? `“${short(t.text, QUOTE_LIMIT)}”` : "(no subject)"),
    ]),
    el("span", "cv-meta", [
      `${channelName(t.channel)} · ${when}`,
      t.note ? ` — ${t.note}` : "",
      t.link ? [" · ", extLink(t.link, "open in Gmail")] : null,
    ]),
  ]);
}

/** what WE last did — for a gone-quiet row this is the line, not theirs */
function oursLine(c) {
  const o = c.ours;
  if (!o) return null;
  const when = fmt.dateShort(o.at);
  if (o.channel === "upwork" && o.text) {
    return el("div", "cv-quote ours", [
      el("span", "txt", ["You wrote last: ", `“${short(o.text, QUOTE_LIMIT)}”`]),
      el("span", "cv-meta", `You · Upwork · ${when} — no answer since`),
    ]);
  }
  const what = o.channel === "fathom" || o.channel === "loom"
    ? `You spoke with them on ${channelName(o.channel)}${o.title ? ` (${o.title})` : ""}`
    : `You wrote to them on ${channelName(o.channel)}`;
  return el("div", "cv-quote ours", [
    el("span", "txt", what),
    el("span", "cv-meta", `${when} — no answer since`),
  ]);
}

function maybeLine(c) {
  const t = c.their, o = c.ours;
  const theirs = t ? `Their ${channelName(t.channel)} of ${fmt.dateShort(t.at)}` : "Their last message";
  const ours = o
    ? (o.channel === "fathom" || o.channel === "loom"
      ? `you spoke with them on a call on ${fmt.dateShort(o.at)}${o.title ? ` (${o.title})` : ""}`
      : `you wrote to them on ${channelName(o.channel)} on ${fmt.dateShort(o.at)}`)
    : "you answered since";
  return el("div", "cv-quote ours", [
    el("span", "txt", `${theirs} looks answered: ${ours}.`),
    el("span", "cv-meta", "Nothing seems owed — confirm, or keep it open if that is wrong."),
  ]);
}

function draftLine(c) {
  const d = c.draft;
  if (!d || !d.body) {
    return el("div", "cv-draft muted", [icon("file-text", { size: 14 }), el("span", null, "No draft written for this one.")]);
  }
  const kind = d.kind === "follow-up" ? "Follow-up draft ready" : "Draft ready";
  return el("div", "cv-draft" + (d.stale ? " stale" : ""), [
    icon("file-text", { size: 14 }),
    el("span", null, [
      el("span", "t", `${kind}${d.words ? ` (${fmt.num(d.words)} words)` : ""}: “${short(d.body, DRAFT_PEEK)}”`),
      d.stale && d.stale_note ? el("span", "warn", ` · ${d.stale_note}`) : null,
    ]),
  ]);
}

function bubbles(th) {
  const box = el("div", "cv-bubbles");
  if (!th.messages || !th.messages.length) {
    box.appendChild(el("div", "muted small", "No message text is synced for this room."));
    return box;
  }
  th.messages.forEach((m) => {
    box.appendChild(el("div", "cv-bubble " + (m.who === "you" ? "out" : "in"), [
      el("span", "txt", m.text || ""),
      el("span", "m", `${m.who === "you" ? "You" : (m.sender || "them")} · ${fmt.dateTimeShort(m.at)}`),
    ]));
  });
  return box;
}

function draftBox(d, url) {
  const body = el("div", "cv-draftbody", d.body || "");
  const copy = btn("Copy draft", () => copyText(d.body || "", copy), { variant: "primary", sm: true, icon: "copy" });
  return el("div", "cv-draftbox", [
    el("div", "cv-lbl", [
      d.kind === "follow-up" ? "Follow-up draft" : "Draft reply",
      d.written ? ` · written ${fmt.dateShort(d.written)}` : "",
      d.words ? ` · ${fmt.num(d.words)} words` : "",
      " · not sent",
    ]),
    body,
    el("div", "cv-actions", [
      copy,
      url ? btn("Open the room on Upwork", () => window.open(url, "_blank", "noopener"), { sm: true, icon: "arrow-up-right" }) : null,
      el("span", "cv-note", "Drafts are never sent by the system — you paste and send."),
    ]),
  ]);
}

/** the open panel: every room this person holds, most recent first */
function mailPanel(mails) {
  const box = el("div", "cv-room");
  box.appendChild(el("div", "cv-lbl", [`Email${mails.length === 1 ? "" : "s"} they sent you outside Upwork`]));
  const bubbles = el("div", "cv-bubbles");
  mails.forEach((m) => {
    bubbles.appendChild(el("div", "cv-bubble in mail", [
      el("span", "strong", m.subject || "(no subject)"),
      el("span", "txt", short(m.body || "", 600)),
      el("span", "m", [`${m.sender || m.author || "them"} · email · ${fmt.dateTimeShort(m.at)}`,
        m.link ? [" · ", extLink(m.link, "open in Gmail")] : null]),
    ]));
  });
  box.appendChild(bubbles);
  return box;
}

function threadPanel(c) {
  const panel = el("div", "cv-thread");
  const threads = c.threads || [];
  if (c.emails && c.emails.length) panel.appendChild(mailPanel(c.emails));
  if (!threads.length) {
    panel.appendChild(el("div", "muted small", "No Upwork room is linked to this card."));
    if (c.draft && c.draft.body) panel.appendChild(draftBox(c.draft, c.links && c.links.upwork));
    return panel;
  }
  threads.forEach((th, i) => {
    const room = el("div", "cv-room" + (i ? " later" : ""));
    room.appendChild(el("div", "cv-lbl", [
      threads.length > 1 ? `Room ${i + 1} of ${threads.length} · ` : "Last messages · ",
      el("span", "strong", short(th.topic || th.room_name || "Upwork room", 70)),
      " · ", extLink(th.url, "open on Upwork"),
    ]));
    room.appendChild(bubbles(th));
    if (th.draft && th.draft.body) room.appendChild(draftBox(th.draft, th.url));
    panel.appendChild(room);
  });
  return panel;
}

/* ── the row ─────────────────────────────────────────────────────────────── */

/**
 * conversationRow(item, ctx, { open }) — `item` is a queue entry carrying
 * `conversation` (server-built). `open` renders the thread panel expanded.
 */
export function conversationRow(it, ctx, opts = {}) {
  const c = it.conversation || {};
  const st = c.state || { kind: "owed", label: "Reply owed", days: it.age_days };
  const who = c.who || { name: it.client || it.title, initials: "?" };
  const wrap = el("div", "cv " + (st.kind || "owed") + (opts.open ? " open" : ""));
  let panel = null;

  const setOpen = (open) => {
    wrap.classList.toggle("open", open);
    chip.setAttribute("aria-expanded", String(open));
    if (open && !panel) { panel = threadPanel(c); wrap.appendChild(panel); }
    if (panel) panel.style.display = open ? "block" : "none";
  };

  const chip = stateChip(st, opts.open);
  chip.addEventListener("click", (ev) => { ev.stopPropagation(); setOpen(!wrap.classList.contains("open")); });

  const main = el("div", "cv-main");
  main.appendChild(el("div", "cv-who", [
    el("span", "cv-name", who.name || "Unknown"),
    who.context ? el("span", "cv-ctx", short(who.context, 60)) : null,
    relationshipTag(c.relationship),
  ]));

  if (st.kind === "maybe") main.appendChild(maybeLine(c));
  else if (st.kind === "quiet") main.appendChild(oursLine(c) || theirLine(c));
  else main.appendChild(theirLine(c));

  const mails = c.emails || [];
  if (mails.length && !(c.their && c.their.channel === "email")) {
    const m = mails[0];
    main.appendChild(el("div", "cv-meta", [
      `Also by email, ${fmt.dateShort(m.at)}: `, el("i", null, `“${short(m.subject || "(no subject)", 90)}”`),
      m.link ? [" · ", extLink(m.link, "open in Gmail")] : null,
    ]));
  }

  if (st.kind === "owed" && st.oldest_days != null) {
    main.appendChild(el("div", "cv-meta warn",
      `An older question of theirs has waited ${daysWord(st.oldest_days)}`
      + ((c.threads || []).length > 1 ? " in their other room." : ".")));
  }

  main.appendChild(draftLine(c));

  const actions = el("div", "cv-actions");
  const url = (c.links && c.links.upwork) || it.thread_url;
  const draft = c.draft && c.draft.body ? c.draft.body : (it.draft || "");
  if (st.kind === "maybe") {
    actions.append(
      btn("Done — nothing owed", () => closure(it, "confirm", ctx), { variant: "primary", sm: true, icon: "check" }),
      btn("Keep open", () => closure(it, "reject", ctx), { sm: true }),
      url ? btn("Open on Upwork", () => window.open(url, "_blank", "noopener"), { sm: true, icon: "arrow-up-right" }) : null,
    );
  } else {
    const copy = draft ? btn("Copy draft", () => copyText(draft, copy), { sm: true, icon: "copy" }) : null;
    actions.append(
      url ? btn("Open on Upwork", () => window.open(url, "_blank", "noopener"), { variant: "primary", sm: true, icon: "arrow-up-right" }) : null,
      copy,
      btn("Done", () => act(it, "done", null, ctx), { sm: true, variant: "ghost", icon: "check" }),
      btn("Snooze 3d", () => act(it, "snooze", 3, ctx), { sm: true, variant: "ghost", icon: "alarm-clock" }),
    );
  }
  if (c.note) actions.appendChild(el("span", "cv-note", c.note));
  main.appendChild(actions);

  const row = el("div", "cv-row", [
    el("div", "cv-avatar", who.initials || "?"),
    main,
    chip,
  ]);
  // the row itself opens the thread — but never when a button or link was the target
  row.addEventListener("click", (ev) => {
    if (ev.target.closest("button, a, .cv-actions")) return;
    setOpen(!wrap.classList.contains("open"));
  });
  wrap.appendChild(row);
  if (opts.open) setOpen(true);
  return wrap;
}
