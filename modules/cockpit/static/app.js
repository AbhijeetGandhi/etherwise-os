"use strict";
// Etherwise cockpit SPA. Token: taken from ?token= on first load, stored in
// localStorage, stripped from the URL; sent as X-Cockpit-Token on every API
// call. No send actions anywhere (drafts-only).

const SECTIONS = ["Today", "Nudges", "Pipeline", "Money", "Clients", "Knowledge", "System"];
const TOKEN_KEY = "ew_cockpit_token";
const THEME_KEY = "ew_cockpit_theme";
const LAST_SECTION_KEY = "ew_cockpit_section";
const MONEY_FILTERS_KEY = "ew_cockpit_money_filters";
const PIPE_FILTERS_KEY = "ew_cockpit_pipeline_filters";

// query string from a param object; nulls drop out (so an all-time range simply
// omits frm/to rather than sending empty bounds)
function qstr(params) {
  return Object.entries(params).filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
}
const nfmtG = (n) => Number(n).toLocaleString();

// ── theme (persisted; tokens.css carries the palettes) ──
function initTheme() {
  const t = localStorage.getItem(THEME_KEY);
  if (t) document.documentElement.setAttribute("data-theme", t);
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", cur);
  localStorage.setItem(THEME_KEY, cur);
}

// ── health chip: doctor + last sync, refreshed on boot + every 5 min ──
async function refreshHealth() {
  const chip = document.getElementById("health");
  if (!chip) return;
  try {
    const d = await api("/api/system");
    const worst = ((d.doctor || {}).worst || "?").toUpperCase();
    const last = (d.jobs || []).map((j) => j.started_at || "").sort().pop() || "";
    // green=all clear · amber=WARN (owner reminders, not faults) · red=FAIL/RED
    const cls = worst === "GREEN" || worst === "PASS" ? "green"
      : worst === "WARN" ? "amber" : worst === "?" ? "" : "red";
    chip.className = "healthchip " + cls;
    chip.innerHTML = `<span class="dot2"></span>doctor ${worst}`
      + (last ? ` · sync ${esc(last.replace("T", " ").slice(5, 16))}` : "");
  } catch (e) {
    chip.className = "healthchip";
    chip.innerHTML = `<span class="dot2"></span>health n/a`;
  }
}

function bootToken() {
  const u = new URL(location.href);
  const t = u.searchParams.get("token");
  if (t) {
    localStorage.setItem(TOKEN_KEY, t);
    u.searchParams.delete("token");
    history.replaceState({}, "", u.pathname + u.hash);
  }
  return localStorage.getItem(TOKEN_KEY) || "";
}

async function api(path) {
  const r = await fetch(path, { headers: { "X-Cockpit-Token": bootToken() } });
  if (r.status === 401) { const e = new Error("unauthorized"); e.unauthorized = true; throw e; }
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

let currentSection = "Today";

function renderUnlock(view, failed) {
  view.innerHTML = "";
  const box = el("div", "unlock");
  box.appendChild(el("h3", null, "Cockpit locked"));
  box.appendChild(el("p", "muted",
    "Paste your cockpit token to unlock this browser. Saved locally — you only do this once."));
  if (failed) box.appendChild(el("p", "unlock-err", "That token didn’t work. Try again."));
  const input = el("input", "unlock-input");
  input.type = "password";
  input.placeholder = "cockpit token";
  input.autofocus = true;
  const btn = el("button", "action primary", "Unlock");
  const doUnlock = () => {
    const t = input.value.trim();
    if (!t) return;
    localStorage.setItem(TOKEN_KEY, t);
    show(currentSection);
  };
  btn.onclick = doUnlock;
  input.onkeydown = (e) => { if (e.key === "Enter") doUnlock(); };
  const row = el("div", "unlock-row");
  row.append(input, btn);
  box.appendChild(row);
  box.appendChild(el("div", "muted",
    "Get it: run <code>bin/cockpit url</code> in the repo — or <code>bin/cockpit open</code> to skip this screen."));
  view.appendChild(box);
  input.focus();
}

async function nudge(itemKey, action, snoozeDays) {
  const body = { item_key: itemKey, action: action };
  if (snoozeDays) body.snooze_days = snoozeDays;
  await fetch("/api/nudge", {
    method: "POST",
    headers: { "X-Cockpit-Token": bootToken(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  show("Today");  // refresh
}

// The Today queue mixes two action systems: follow-ups live in the local
// nudge_state suppression (item_key 'followup:<thread>' -> /api/nudge), M4
// nudges live in the real nudges table (dedup_key -> /api/cos-nudge). Route by
// kind so the right one actually records the action.
async function queueAct(item, action, snoozeDays) {
  if (item.kind === "nudge") {
    const body = { dedup_key: item.item_key.replace(/^nudge:/, ""), action };
    if (snoozeDays) body.snooze_days = snoozeDays;
    await fetch("/api/cos-nudge", { method: "POST",
      headers: { "X-Cockpit-Token": bootToken(), "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    show("Today");
  } else {
    await nudge(item.item_key, action, snoozeDays);
  }
}

const fmtUsd = (n) => "$" + (n || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
const fmtUsd2 = (n) => { const s = n < 0 ? "−" : ""; n = Math.abs(n || 0);
  return s + "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
const fmtUsd0 = (n) => { const s = n < 0 ? "−" : ""; n = Math.abs(n || 0);
  return s + "$" + n.toLocaleString("en-US", { maximumFractionDigits: 0 }); };
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };
const pill = (text, cls) => text ? el("span", "pill " + (cls || ""), text) : document.createTextNode("");
function ago(days) {
  if (days == null) return "";
  if (days <= 0) return "today";
  if (days < 7) return days + "d ago";
  const w = Math.floor(days / 7);
  return w + "w ago";  // explicitly "ago" — never bare "Nw" (read as weeks)
}

function renderNav(active) {
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  SECTIONS.forEach((s) => {
    const b = el("button", s === active ? "active" : "", s);
    b.onclick = () => show(s);
    nav.appendChild(b);
  });
}

function placeholder(view, msg) {
  view.appendChild(el("div", "placeholder", msg));
}

const RENDERERS = {
  System: async (view) => {
    const d = await api("/api/system");
    view.appendChild(el("h2", "section", "System"));

    // Run-now: allowlisted launchd kicks (wake) + the live rail
    const run = el("div", "card");
    run.appendChild(el("h3", null, "Run now"));
    const runRow = el("div", "fu-actions");
    [["Scan", "/api/wake", { job: "scan" }],
     ["Sync", "/api/wake", { job: "sync" }],
     ["Inbox", "/api/wake", { job: "inbox" }],
     ["Outcome capture (rail)", "/api/rail", { rail: "outcome-capture" }]].forEach(([label, path, body]) => {
      const b = el("button", "action", label);
      b.onclick = () => kick(path, body, b);
      runRow.appendChild(b);
    });
    const brief = el("button", "action", "Brief (M4)");
    brief.disabled = true; brief.title = "Chief of Staff brief arrives with M4";
    runRow.appendChild(brief);
    run.appendChild(runRow);
    view.appendChild(run);
    const sysDrill = el("div", "drill");
    const grid = el("div", "grid");
    const dr = d.doctor || {};
    grid.appendChild(card("Doctor",
      `<div class="metric"><span class="pill ${(dr.worst||"").toLowerCase()}">${dr.worst||"?"}</span></div>`
      + `<div class="muted small">${(dr.checks||[]).length} checks · full list below</div>`));
    const sp = d.spend || {};
    const spCard = card("Spend today",
      `<div class="metric">${fmtUsd2(sp.today_usd)} <small>/ ${fmtUsd(sp.soft_limit_usd)} soft</small></div>`
      + `<div class="muted small">MTD ${fmtUsd2(sp.mtd_usd)}${(sp.by_task||[]).length ? " · click for the breakdown" : " · nothing spent today"}</div>`);
    if ((sp.by_task || []).length) {
      spCard.style.cursor = "pointer";
      spCard.onclick = () => {
        sysDrill.innerHTML = ""; sysDrill.classList.add("open");
        sysDrill.appendChild(el("h4", null, `Model spend today <span class="count">— ${fmtUsd2(sp.today_usd)}</span>`
          + `<button class="close">✕ close</button>`));
        sysDrill.querySelector(".close").onclick = () => sysDrill.classList.remove("open");
        const wrap = el("div", "tablewrap"), t = el("table");
        t.innerHTML = "<tr><th>Task</th><th class='num'>Calls</th><th class='num'>USD</th></tr>";
        sp.by_task.forEach((r) => t.appendChild(el("tr", null,
          `<td>${esc(r.task_name)}</td><td class="num">${r.calls}</td><td class="num">${fmtUsd2(r.usd)}</td>`)));
        wrap.appendChild(t); sysDrill.appendChild(wrap);
        sysDrill.scrollIntoView({ behavior: "smooth", block: "nearest" });
      };
    }
    grid.appendChild(spCard);
    const sh = (d.shadow||{});
    grid.appendChild(card("Shadow ledger",
      `<div class="metric">${sh.pending||0} <small>pending intents</small></div>`
      + `<div class="muted small">${sh.pending ? "captured writes, held while shadowed" : "no writes held — modules live or idle"}</div>`));
    const an = d.anomalies || {};
    grid.appendChild(card("Anomalies (14d)",
      `<div class="metric${an.open ? "" : " muted"}">${an.open || 0} <small>open</small></div>`
      + `<div class="muted small">${an.open ? "unresolved operational signals below" : "nothing unresolved ✓"}</div>`));
    view.appendChild(grid);
    view.appendChild(sysDrill);
    view.appendChild(el("h2", "section", "Jobs"));
    const t = el("table");
    t.innerHTML = "<tr><th>Job</th><th>Status</th><th>Last run</th></tr>";
    (d.jobs || []).forEach((j) => {
      const tr = el("tr");
      tr.innerHTML = `<td>${j.task_name}</td>`
        + `<td><span class="pill ${j.status}">${j.status}</span></td>`
        + `<td class="muted">${(j.started_at||"").replace("T"," ").slice(0,16)}</td>`;
      t.appendChild(tr);
    });
    view.appendChild(t);

    // ── Source health: every number-backing source self-reports ──
    const srcs = ((d.sources || {}).sources) || [];
    if (srcs.length) {
      view.appendChild(el("h2", "section",
        `Source health <span class="hint muted">— freshness + degradation of`
        + ` each number-backing source · a critical one in trouble turns the`
        + ` health chip amber</span>`));
      const stbl = el("table");
      stbl.innerHTML = "<tr><th>Source</th><th>Status</th><th>Last success</th>"
        + "<th>Backs</th><th>Detail</th></tr>";
      srcs.forEach((s) => {
        const cls = s.status === "ok" ? "green"
          : s.status === "degraded" ? "amber" : "red";
        const detail = s.status === "ok" ? `fresh · ${s.age_hours}h ago`
          : s.status === "down" ? "never produced a successful run"
          : s.status === "stale" ? `${s.age_hours}h ago > ${s.max_age_hours}h max`
          : "ran but output was thin/degraded";
        const tr = el("tr");
        tr.innerHTML = `<td>${esc(s.label)}${s.critical ? "" : ' <span class="muted small">(non-critical)</span>'}</td>`
          + `<td><span class="pill ${cls}">${esc(s.status)}</span></td>`
          + `<td class="muted">${esc((s.last_success || "—").replace("T", " ").slice(0, 16))}</td>`
          + `<td class="muted">${esc(s.backs || "—")}</td>`
          + `<td class="muted">${esc(detail)}</td>`;
        stbl.appendChild(tr);
      });
      view.appendChild(stbl);
    }

    // ── Anomalies: the operational incident feed (was invisible until now) ──
    const anoms = (d.anomalies || {}).recent || [];
    view.appendChild(el("h2", "section",
      `Anomalies <span class="hint muted">— operational incidents, last`
      + ` ${(d.anomalies || {}).window_days || 14} days · unresolved first · a`
      + ` crashlooping task collapses to one counted row</span>`));
    if (!anoms.length) {
      view.appendChild(el("div", "srcline ok", "✓ no anomalies filed in the window"));
    } else {
      const at = el("table");
      at.innerHTML = "<tr><th>When</th><th>Task</th><th>Kind</th><th>Severity</th>"
        + "<th class='num'>Count</th><th>State</th></tr>";
      anoms.forEach((a) => {
        const sev = a.severity === "critical" ? "red" : "amber";
        // 'recovered' = the task succeeded after this fired (self-healed); only
        // genuinely-active ones wear the loud severity colour
        const state = a.recovered
          ? `<span class="pill green" title="task has succeeded since">recovered</span>`
          : `<span class="pill ${sev}">active</span>`;
        const tr = el("tr", a.recovered ? "muted" : null,
          `<td class="muted">${esc((a.last || "").replace("T", " ").slice(0, 16))}</td>`
          + `<td>${esc(a.task_name)}</td><td><code>${esc(a.kind)}</code></td>`
          + `<td><span class="pill ${a.recovered ? "" : sev}">${esc(a.severity)}</span></td>`
          + `<td class="num">${a.n}</td><td>${state}</td>`);
        at.appendChild(tr);
      });
      view.appendChild(at);
    }

    // doctor check list (full System)
    view.appendChild(el("h2", "section", "Doctor checks"));
    const dt = el("table");
    dt.innerHTML = "<tr><th>Check</th><th>Status</th><th>Detail</th></tr>";
    ((d.doctor || {}).checks || []).forEach((c) => {
      const tr = el("tr");
      tr.innerHTML = `<td>${c.name}</td>`
        + `<td><span class="pill ${c.status.toLowerCase()}">${c.status}</span></td>`
        + `<td class="muted">${c.detail || ""}</td>`;
      dt.appendChild(tr);
    });
    view.appendChild(dt);
    view.appendChild(el("div", "muted", "Model-ID validity vs /v1/models runs in <code>bin/doctor</code> (network)."));

    const byTask = (d.shadow || {}).by_task || {};
    if (Object.keys(byTask).length) {
      view.appendChild(el("h2", "section", "Shadow ledger (cutover evidence)"));
      const st = el("table");
      st.innerHTML = "<tr><th>Task</th><th>Intents</th></tr>";
      Object.entries(byTask).forEach(([k, n]) => {
        const tr = el("tr"); tr.innerHTML = `<td>${k}</td><td>${n}</td>`; st.appendChild(tr);
      });
      view.appendChild(st);
    }
  },
  Today: async (view) => {
    const d = await api("/api/today");
    const m = d.metrics || {};
    const drill = el("div", "drill");

    async function openRows(title, params, cols) {
      const res = await api("/api/today/rows?" + qstr(params));
      const rows = res.rows || [];
      const total = res.total == null ? rows.length : res.total;
      const capped = total > rows.length;
      drill.innerHTML = ""; drill.classList.add("open");
      const h = el("h4", null, `${esc(title)} <span class="count">— ${nfmtG(total)}`
        + ` row${total === 1 ? "" : "s"}${capped ? ` (showing ${rows.length})` : ""}</span>`
        + `<button class="close">✕ close</button>`);
      h.querySelector(".close").onclick = () => drill.classList.remove("open");
      drill.appendChild(h);
      if (!rows.length) drill.appendChild(el("div", "emptystate", "No rows behind this figure."));
      else {
        const wrap = el("div", "tablewrap"), t = el("table");
        t.innerHTML = "<tr>" + cols.map((c) => `<th${c.num ? " class='num'" : ""}>${c.label}</th>`).join("") + "</tr>";
        rows.forEach((r) => {
          const tr = el("tr");
          tr.innerHTML = cols.map((c) => `<td${c.num ? " class='num'" : ""}>${c.render ? c.render(r) : esc(r[c.k] == null ? "—" : r[c.k])}</td>`).join("");
          t.appendChild(tr);
        });
        wrap.appendChild(t); drill.appendChild(wrap);
      }
      drill.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    const leadCols = [
      { k: "d", label: "Scored" },
      { k: "title", label: "Job", render: (r) => r.job_url
        ? `<a href="${esc(r.job_url)}" target="_blank" rel="noopener">${esc(r.title || r.id)}</a>` : esc(r.title || r.id) },
      { k: "score", label: "Score", num: true },
      { k: "icp_tier", label: "Tier", render: (r) => r.icp_tier
        ? `<span class="pill ${r.icp_tier === "icp" ? "icp" : ""}">${esc(r.icp_tier)}</span>` : "—" },
      { k: "clickup_task_url", label: "Card", render: (r) => r.clickup_task_url
        ? `<a href="${esc(r.clickup_task_url)}" target="_blank" rel="noopener">card</a>` : "—" },
    ];

    view.appendChild(el("h2", "section",
      `Today <span class="hint muted">— what to act on right now</span>`));

    // ── source-health line: a quiet queue must read as genuinely quiet ──
    const s = d.sources || {};
    const sline = el("div", "srcline" + (s.ok ? " ok" : " bad"));
    sline.innerHTML = (s.ok ? "✓ " : "⚠ ") + esc(s.headline || "");
    if (!s.ok && (s.lines || []).length) {
      const ul = el("ul", "srcline-list");
      s.lines.forEach((x) => ul.appendChild(el("li", null, esc(x))));
      sline.appendChild(ul);
    }
    view.appendChild(sline);

    // ── Quick Capture (zero-friction; next sweep classifies) ──
    const qc = el("div", "card");
    const qrow = el("div", "unlock-row");
    const qi = el("input", "unlock-input");
    qi.placeholder = "Quick capture — note, to-do, idea… (Enter)";
    const qb = el("button", "action", "Capture");
    const doCapture = async () => {
      const text = qi.value.trim(); if (!text) return;
      qb.disabled = true;
      try {
        const r = await fetch("/api/note", { method: "POST",
          headers: { "X-Cockpit-Token": bootToken(), "Content-Type": "application/json" },
          body: JSON.stringify({ text }) });
        if (r.ok) { qi.value = ""; toast("Captured — classified next sweep"); }
        else toast("Capture failed", true);
      } catch (e) { toast("Capture failed", true); }
      qb.disabled = false;
    };
    qb.onclick = doCapture;
    qi.onkeydown = (e) => { if (e.key === "Enter") doCapture(); };
    qrow.append(qi, qb);
    qc.appendChild(qrow);
    view.appendChild(qc);

    // ── ACT NOW: one ruthlessly ranked queue (follow-ups + nudges) ──
    const q = d.queue || [];
    view.appendChild(el("h3", "section",
      `Act now <span class="hint muted">— ${q.length ? `top ${q.length}` : "0"}`
      + ` by urgency${d.queue_more ? ` · +${d.queue_more} more in Nudges` : ""}</span>`));
    const qcard = el("div", "card");
    if (!q.length) {
      qcard.appendChild(el("div", "emptystate",
        s.ok ? "Nothing needs you right now. ✓ (queue is empty, and all sources are fresh)"
             : "Queue is empty — but a source is degraded above, so this may be a feed gap, not a clear desk."));
    }
    q.forEach((it) => {
      const row = el("div", "q-row q-" + it.kind);
      const left = el("div", "q-left");
      const title = el("div", "q-title", esc(it.title || "(untitled)"));
      const meta = el("div", "q-meta");
      if (it.kind === "followup") {
        meta.append(pill("reply owed", "owed"),
                    pill(it.tier, "tier-" + (it.tier || "").toLowerCase()));
        if (it.age_days != null) meta.appendChild(el("span", "muted", ago(it.age_days) + " since client"));
      } else {
        meta.append(pill((it.nudge_kind || "").replace(/_/g, " "), ""),
                    el("span", "muted", esc(it.dept || "")));
        if (it.due) meta.appendChild(el("span", "muted", " · due " + esc(it.due.slice(0, 10))));
      }
      left.append(title, meta);
      const btns = el("div", "q-actions");
      if (it.kind === "followup") {
        const copy = el("button", "action primary", "Copy draft");
        copy.onclick = () => { navigator.clipboard.writeText(it.draft || ""); copy.textContent = "Copied ✓"; setTimeout(() => copy.textContent = "Copy draft", 1200); };
        const open = el("button", "action", "Open thread");
        open.onclick = () => window.open(it.thread_url, "_blank");
        btns.append(copy, open);
      } else if (it.action) {
        meta.appendChild(el("div", "q-actionnote muted", esc(it.action)));
      }
      const done = el("button", "action", "Done");
      done.onclick = () => queueAct(it, "done");
      const snooze = el("button", "action", "Snooze 3d");
      snooze.onclick = () => queueAct(it, "snooze", 3);
      btns.append(done, snooze);
      row.append(left, btns);
      qcard.appendChild(row);
    });
    view.appendChild(qcard);

    // ── READY WHEN YOU RESUME: the lead backlog (paused-aware, not a nag) ──
    const b = d.backlog || {};
    const head = b.paused
      ? `Ready when you resume <span class="hint muted">— applications paused`
        + ` by intent since ${esc(b.paused_since || "")}; these wait, they don't nag</span>`
      : `Hot leads to work <span class="hint muted">— top by ICP then score</span>`;
    view.appendChild(el("h3", "section", head));
    const bcard = el("div", "card");
    // an honest context line: accumulated vs still-fresh, both drillable
    const ctx = el("div", "backlog-ctx");
    ctx.innerHTML =
      `<button class="linknum">${nfmtG(b.accumulated || 0)}</button> hot leads`
      + ` since ${esc((b.accumulated_since || "").slice(0, 10))}`
      + ` · <button class="linknum">${nfmtG(b.fresh_count || 0)}</button> still fresh`
      + ` (last ${b.fresh_days || 10}d)`
      + ` · <b>${nfmtG(b.icp || 0)}</b> ICP · ${nfmtG(b.icp_near || 0)} near-miss`;
    const [accBtn, freshBtn] = ctx.querySelectorAll(".linknum");
    accBtn.onclick = () => openRows("Accumulated hot leads since the pause", { which: "accumulated" }, leadCols);
    freshBtn.onclick = () => openRows("Fresh hot leads (still live)", { which: "fresh" }, leadCols);
    bcard.appendChild(ctx);
    if (!(b.items || []).length) {
      bcard.appendChild(el("div", "emptystate", "No fresh hot leads in the window."));
    }
    (b.items || []).forEach((h) => {
      const row = el("div", "row lead-row");
      const left = el("div", null,
        `<strong>${esc(h.title || h.id)}</strong>`
        + (h.has_draft ? ' <span class="muted">· draft ready</span>' : ""));
      const right = el("div", "lead-right");
      if (h.icp_tier) right.appendChild(pill(h.icp_tier, h.icp_tier === "icp" ? "icp" : ""));
      right.appendChild(el("span", "pill ok", "score " + h.score));
      if (h.job_url) { const o = el("button", "action", "Open job"); o.onclick = () => window.open(h.job_url, "_blank"); right.appendChild(o); }
      row.append(left, right);
      bcard.appendChild(row);
    });
    view.appendChild(bcard);
    view.appendChild(drill);

    // ── At a glance: every zero explains itself; figures drill ──
    view.appendChild(el("h3", "section", "At a glance"));
    const grid = el("div", "grid");
    const a = m.applied || {}, rev = m.revenue || {}, act = m.active || {};
    const appliedVal = a.paused ? "paused" : String(a.week || 0);
    grid.appendChild(card("Applied (7d)",
      `<div class="metric${a.paused ? " muted" : ""}">${appliedVal}</div>`
      + `<div class="muted small">${esc(a.note || `${a.today || 0} today · ${a.last_week || 0} prev week`)}</div>`));
    const pct = Math.min(100, rev.pct || 0);
    grid.appendChild(card("Revenue MTD", `<div class="metric">${fmtUsd(rev.mtd_usd)} <small>/ ${fmtUsd(rev.target_usd)}</small></div>`
      + `<div class="bar"><span style="width:${pct}%"></span></div>`));
    grid.appendChild(card("Active pipeline", `<div class="metric">${act.proposals||0} <small>proposals</small></div>`
      + `<div class="muted small">${act.interviews||0} interviews · ${act.contracts||0} contracts</div>`));
    grid.appendChild(card("Your queue", `<div class="metric">${m.queue_total||0}</div>`
      + `<div class="muted small">${m.follow_ups_due||0} replies owed · ${m.backlog_fresh||0} fresh leads waiting</div>`));
    view.appendChild(grid);
  },
  Pipeline: async (view) => {
    const [d, F] = await Promise.all([api("/api/pipeline"),
                                      api("/api/pipeline/facts")]);
    view.appendChild(el("h2", "section",
      `Pipeline <span class="hint muted">— every number is clickable; sources`
      + ` are truthful (applied = connects rail)</span>`));
    const c = d.carded || {}, wr = d.win_rate || {};
    const cr = d.client_replied || {}, a = d.applied || {};
    const drill = el("div", "drill");

    // generic rows-drill (jobs / applies / threads), same panel contract:
    // header shows the count of the rows actually shown
    async function openRows(title, params, cols) {
      const res = await api("/api/pipeline/rows?" + qstr(params));
      const rows = res.rows || [];
      // the drill states the TRUE count and, when the 300-row cap bites, says so
      // — a capped list must never read as "that's all there was"
      const total = res.total == null ? rows.length : res.total;
      const capped = total > rows.length;
      drill.innerHTML = "";
      drill.classList.add("open");
      const h = el("h4", null, `${esc(title)} <span class="count">— ${nfmtG(total)}`
        + ` row${total === 1 ? "" : "s"}`
        + `${capped ? ` (showing the latest ${rows.length})` : ""}</span>`
        + `<button class="close">✕ close</button>`);
      h.querySelector(".close").onclick = () => drill.classList.remove("open");
      drill.appendChild(h);
      if (!rows.length) {
        drill.appendChild(el("div", "emptystate", "No rows behind this figure."));
      } else {
        const wrap = el("div", "tablewrap");
        const t = el("table");
        t.innerHTML = "<tr>" + cols.map((cc) =>
          `<th${cc.num ? " class='num'" : ""}>${cc.label}</th>`).join("") + "</tr>";
        rows.forEach((r) => {
          const tr = el("tr");
          tr.innerHTML = cols.map((cc) => {
            const v = cc.render ? cc.render(r) : esc(r[cc.k] == null ? "—" : r[cc.k]);
            return `<td${cc.num ? " class='num'" : ""}>${v}</td>`;
          }).join("");
          t.appendChild(tr);
        });
        wrap.appendChild(t);
        drill.appendChild(wrap);
      }
      drill.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    const jobCols = [
      { k: "d", label: "Date" },
      { k: "title", label: "Job", render: (r) => r.job_url
        ? `<a href="${esc(r.job_url)}" target="_blank" rel="noopener">${esc(r.title || r.id)}</a>`
        : esc(r.title || r.id) },
      { k: "score", label: "Score", num: true },
      { k: "status", label: "Status",
        render: (r) => `<span class="pill">${esc(r.status || "—")}</span>` },
      { k: "clickup_task_url", label: "Card", render: (r) => r.clickup_task_url
        ? `<a href="${esc(r.clickup_task_url)}" target="_blank" rel="noopener">card</a>` : "—" },
    ];
    const appliedCols = [
      { k: "d", label: "Date" },
      { k: "title", label: "Job", render: (r) => r.job_id
        ? `<a href="https://www.upwork.com/jobs/~02${esc(r.job_id)}" target="_blank"`
          + ` rel="noopener">${esc(r.title || r.job_id)}</a>` : esc(r.title || "—") },
      { k: "connects_delta", label: "Connects", num: true },
    ];
    const threadCols = [
      { k: "d", label: "Started" },
      { k: "modified", label: "Updated" },
      { k: "title", label: "Job", render: (r) => esc(r.title || r.id) },
      { k: "client_company", label: "Client" },
      { k: "status", label: "Status",
        render: (r) => `<span class="pill">${esc(r.status)}</span>` },
    ];
    const hiredCols = [
      { k: "d", label: "Started" },
      { k: "title", label: "Contract", render: (r) => esc(r.title || r.id) },
      { k: "client_company", label: "Client" },
      { k: "contract_type", label: "Type" },
      { k: "hourly_rate", label: "Rate", num: true,
        render: (r) => r.hourly_rate ? "$" + r.hourly_rate + "/hr" : "—" },
      { k: "status", label: "Status",
        render: (r) => `<span class="pill">${esc(r.status || "—")}</span>` },
    ];

    const mondayOf = (iso) => iso;   // funnel weeks already carry the Monday

    // ── KPI strip (all clickable) ──
    const kpis = el("div", "kpis");
    const mkKpi = (lbl, val, sub, act) => {
      const k = el("button", "kpi");
      k.appendChild(el("div", "lbl", lbl));
      k.appendChild(el("div", "val", val));
      k.appendChild(el("div", "sub2", sub));
      k.appendChild(el("div", "tag", "details →"));
      k.onclick = act;
      return k;
    };
    // The headline window is the 7-DAY one, not 'today': the scanner runs
    // hourly and the founder reads this at 3am, when 'today' is structurally
    // near-zero and says nothing. Today stays visible in the sub-line.
    // Every zero carries WHY it is zero — a bare 0 is a dead end.
    const icp = d.icp || {};
    const ago = (n) => n == null ? "never"
      : n === 0 ? "today" : n === 1 ? "yesterday" : `${n}d ago`;

    // ── source-health stamps (a stale/degraded source must never look live) ──
    // A KPI whose backing source is unhealthy carries a warning and, for the
    // applied count specifically, refuses to show a live-looking number: a
    // stale rail's "0" is indistinguishable from a real 0 (2026-07-14).
    const sh = d.scanner_health || {};
    const scannerBad = sh.status && sh.status !== "ok";
    const scanWarn = scannerBad
      ? `<div class="kpiwarn">⚠ scanner ${esc(sh.status)}`
        + (sh.status === "stale" && sh.age_hours != null
            ? ` — last run ${sh.age_hours}h ago` : "")
        + (sh.status === "degraded" ? " — feed thin, may undercount" : "")
        + `; recent numbers may be low</div>`
      : "";
    const cardedKpi = mkKpi("Carded to board", String(c.week || 0),
      `last 7 days · ${c.today || 0} today · ${c.last_week || 0} the week before`,
      () => openRows("Carded — all recent", { stage: "carded" }, jobCols));
    if (scanWarn) cardedKpi.insertAdjacentHTML("beforeend", scanWarn);
    kpis.appendChild(cardedKpi);
    kpis.appendChild(mkKpi("ICP / Prime", String(icp.last_30 || 0),
      `last 30 days · ${icp.near_30 || 0} near-miss · ${icp.total || 0} all-time`
      + ` · last ${ago(icp.days_since)}`,
      () => openRows("ICP / Prime leads (all-time)",
        { stage: "jobs", icp: "icp" }, chartCols)));
    // the rail's own freshness gates whether its 0 can be trusted: a stale rail
    // means "we may be missing applications", not "you applied to nothing".
    const rs = (a.source || {});
    const railStale = rs.status && rs.status !== "ok";
    const appliedKpi = mkKpi(
      "Applied" + (a.available ? (railStale ? " ⚠" : "") : " (rail pending)"),
      !a.available ? "—" : railStale ? "stale" : String(a.week || 0),
      !a.available ? esc((a.rail || "").slice(0, 60))
        : railStale
          ? `NOT CURRENT — rail last ran ${rs.age_hours != null ? rs.age_hours + "h ago" : "unknown"}`
            + ` (max ${rs.max_age_hours}h); its 0 can't be trusted until it scrapes`
          : (a.week ? `last 7 days · ${a.today || 0} today`
                    : `last 7 days — a TRUE zero (applications paused; rail live`
                      + ` since ${esc(a.since || "")})`),
      () => openRows("Applications (connects rail)", { stage: "applied" }, appliedCols));
    if (railStale) appliedKpi.classList.add("kpi-stale");
    kpis.appendChild(appliedKpi);
    kpis.appendChild(mkKpi("Client replies", String(cr.this_month || 0),
      `this month · ${cr.total || 0} all-time · last reply ${ago(cr.days_since)}`
      + (cr.this_month ? "" : " — nothing applied, so nothing to reply to"),
      () => openRows("Client replies (room-sourced proposals)",
        { stage: "replied" }, threadCols)));
    view.appendChild(kpis);
    view.appendChild(drill);

    // win rate + zero-applies context line
    view.appendChild(el("div", "muted small",
      `Win rate ≤${wr.pct != null ? wr.pct : "—"}% (${wr.won || 0} won of`
      + ` ${wr.decided || 0} decided threads) — ${esc(wr.caveat || "")}`));

    // ══════════════════════════════════════════════════════════════════════
    // CHARTS — all scoped by ONE range state, all aggregated from the SAME
    // fact rows the drills query. A bar can therefore never disagree with the
    // rows behind it: same predicate, same source, by construction.
    // ══════════════════════════════════════════════════════════════════════
    const jobs = F.jobs || [];
    const HOT = F.hot_threshold || 16, FLOOR = F.score_floor || 8;
    const monthsPresent = [...new Set(jobs.map((j) => j.m).filter(Boolean))].sort();
    const thisM = (F.as_of || "").slice(0, 7)
      || monthsPresent[monthsPresent.length - 1] || "";
    const mAdd = (ym, k) => {
      let [y, m] = ym.split("-").map(Number);
      m += k; while (m < 1) { m += 12; y -= 1; } while (m > 12) { m -= 12; y += 1; }
      return `${y}-${String(m).padStart(2, "0")}`;
    };
    const lastM = mAdd(thisM, -1);
    const PRESETS = [
      ["all", "All time", () => [null, null]],
      ["this", "This month", () => [thisM, thisM]],
      ["last", "Last month", () => [lastM, lastM]],
      ["l3", "Last 3 mo", () => [mAdd(thisM, -2), thisM]],
      ["t12", "Trailing 12", () => [mAdd(thisM, -11), thisM]],
      ["ytd", "YTD", () => [thisM.slice(0, 4) + "-01", thisM]],
      ["custom", "Custom", null],
    ];
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(PIPE_FILTERS_KEY) || "{}"); }
    catch (e) { saved = {}; }
    let range = { preset: saved.preset || "all",
                  from: saved.from || null, to: saved.to || null };
    if (!PRESETS.some(([k]) => k === range.preset)) {
      range = { preset: "all", from: null, to: null };
    }
    const bounds = () => {
      const pr = PRESETS.find(([k]) => k === range.preset);
      return pr && pr[2] ? pr[2]() : [range.from, range.to];
    };
    const isAll = () => { const [f, t] = bounds(); return !f && !t; };
    const inRange = (x) => { const [f, t] = bounds();
      return (!f || x.m >= f) && (!t || x.m <= t); };
    const rangeLabel = () => {
      if (isAll()) return "all time";
      const [f, t] = bounds();
      return f === t ? f : `${f || "…"} → ${t || "…"}`;
    };
    // the scoped fact sets — every chart below reads ONLY these
    const J = () => jobs.filter(inRange);
    const A = () => (F.applies || []).filter(inRange);
    const RP = () => (F.replies || []).filter(inRange);
    const H = () => (F.hires || []).filter(inRange);
    // the filter object a chart band hands to the drill — identical semantics
    const scopeQ = (extra) => {
      const [f, t] = bounds();
      return Object.assign({ stage: "jobs", frm: f, to: t }, extra || {});
    };
    const pctOf = (n, dn) => (dn ? Math.round((1000 * n) / dn) / 10 : 0);
    const nfmt = (n) => n.toLocaleString();

    // the column set for CHART drills — carries the fields a chart band is
    // actually about (why it was skipped, what it paid, which feed, its tier)
    const chartCols = [
      { k: "d", label: "Seen" },
      { k: "title", label: "Job", render: (r) => r.job_url
        ? `<a href="${esc(r.job_url)}" target="_blank" rel="noopener">${esc(r.title || r.id)}</a>`
        : esc(r.title || r.id) },
      { k: "score", label: "Score", num: true },
      { k: "budget", label: "Budget", num: true, render: (r) => r.hourly_max
        ? "$" + r.hourly_max + "/hr"
        : (r.fixed_budget ? "$" + Number(r.fixed_budget).toLocaleString() : "—") },
      { k: "client_total_spent", label: "Client spent", num: true,
        render: (r) => r.client_total_spent
          ? "$" + Math.round(r.client_total_spent).toLocaleString() : "—" },
      { k: "feed_source", label: "Feed" },
      { k: "icp_tier", label: "Tier", render: (r) => r.icp_tier
        ? `<span class="pill ${r.icp_tier === "icp" ? "icp" : ""}">${esc(r.icp_tier)}</span>` : "—" },
      { k: "hard_rule_skip", label: "Skipped for",
        render: (r) => r.hard_rule_skip
          ? `<span class="pill fail">${esc(r.hard_rule_skip)}</span>` : "—" },
      { k: "clickup_task_url", label: "Card", render: (r) => r.clickup_task_url
        ? `<a href="${esc(r.clickup_task_url)}" target="_blank" rel="noopener">card</a>` : "—" },
    ];
    // the "N died here" band between two funnel steps — itself drillable
    const dropRow = (label, q, rl) => {
      const dr = el("button", "fndrop");
      dr.innerHTML = `<span class="fnx">↓</span> ${esc(label)}`
        + ` <span class="tag">see them →</span>`;
      dr.onclick = () => openRows(`${label} — ${rl}`, q, chartCols);
      return dr;
    };

    // ── range bar ──
    const bar = el("div", "rangebar");
    const seg = el("div", "rangeseg");
    const fromIn = el("input"), toIn = el("input");
    fromIn.type = toIn.type = "month";
    fromIn.title = "from month"; toIn.title = "to month";
    const custom = el("span", "rangecustom");
    custom.append(fromIn, el("span", "muted small", "→"), toIn);
    const drawSeg = () => {
      seg.innerHTML = "";
      PRESETS.forEach(([key, label]) => {
        const bt = el("button", "rbtn" + (range.preset === key ? " on" : ""), label);
        bt.onclick = () => {
          range.preset = key;
          if (key === "custom") {
            range.from = fromIn.value || range.from || lastM;
            range.to = toIn.value || range.to || thisM;
          }
          redraw();
        };
        seg.appendChild(bt);
      });
      custom.style.display = range.preset === "custom" ? "inline-flex" : "none";
      fromIn.value = range.from || ""; toIn.value = range.to || "";
    };
    fromIn.onchange = () => { range.preset = "custom"; range.from = fromIn.value || null; redraw(); };
    toIn.onchange = () => { range.preset = "custom"; range.to = toIn.value || null; redraw(); };
    bar.append(seg, custom);
    view.appendChild(bar);

    const charts = el("div");            // everything the range rescopes
    view.appendChild(charts);

    function redraw() {
      localStorage.setItem(PIPE_FILTERS_KEY, JSON.stringify(range));
      drawSeg();
      charts.innerHTML = "";
      const js = J(), rl = rangeLabel();

      // ── 1. CONVERSION FUNNEL ────────────────────────────────────────────
      // Each stage is a strict subset of the one above it, so the drop-off
      // between two bars IS the rows that failed that step — clickable too.
      const scanned = js.length;
      const passed = js.filter((j) => !j.k);
      const scored = js.filter((j) => j.s != null && j.s >= FLOOR);
      const hot = js.filter((j) => j.s != null && j.s >= HOT);
      const carded = js.filter((j) => j.c);
      // applied is only KNOWN where the connects rail covers the range
      // The applied count is only KNOWN for a period the rail actually measured.
      // Coverage is judged on the period's START DATE, and 'all time' is the
      // LEAST covered range there is, not the most: it reaches back to 2024,
      // while the rail began on 2026-06-30. Two bugs lived here — 'all time'
      // short-circuited to covered, and a month-granularity compare called June
      // covered when the rail only holds Jun 30. Both produced a bare 0 where
      // the truth is "never measured" (2026-07-14).
      const railSince = F.rail_since;
      const [rf] = bounds();
      const rangeStart = isAll() || !rf ? null : rf + "-01";
      const railCovers = !!railSince && !!rangeStart && rangeStart >= railSince;
      // ONLY the scanner stages belong in the funnel. Each of these five IS a
      // strict subset of the one above (same table, same rows, narrowing
      // predicate), so the taper is a real loss and the step-% is a real
      // conversion. Applied/replied/hired are NOT subsets of these — they come
      // from other tables with other time coverage (contracts reach back to
      // 2024; 51 of 55 predate the scanner's first scan) — so they are shown
      // BELOW as outcomes, with no step-% pretending they divide into this.
      const steps = [
        { k: "scanned", lbl: "Scanned", n: scanned, q: scopeQ({}),
          note: "every job the feeds returned" },
        { k: "passed", lbl: "Passed the floors", n: passed.length,
          q: scopeQ({ skip: "__none__" }),
          note: "survived n8n / rate / fixed-budget hard rules" },
        { k: "scored", lbl: `Scored ≥${FLOOR}`, n: scored.length,
          q: scopeQ({ skip: "__none__", score_min: FLOOR }),
          note: "the model found real fit" },
        { k: "hot", lbl: `Hot ≥${HOT}`, n: hot.length,
          q: scopeQ({ score_min: HOT }), note: "cleared the hot bar" },
        { k: "carded", lbl: "Carded to ClickUp", n: carded.length,
          q: scopeQ({ carded: "1" }), note: "made it onto the board" },
      ];
      charts.appendChild(el("h2", "section",
        `Conversion funnel <span class="hint muted">— ${esc(rl)} · the scanner`
        + ` stages, where each band IS a subset of the one above · click a band`
        + ` for its rows, or a drop-off for the jobs that died there</span>`));
      const fc = el("div", "card");
      const fn = el("div", "funnel");
      steps.forEach((s, i) => {
        const prev = i ? steps[i - 1] : null;
        const row = el("div", "fnstep");
        const w = scanned ? Math.max(0.6, (100 * (s.n || 0)) / scanned) : 0;
        const conv = prev && prev.n ? pctOf(s.n || 0, prev.n) : null;
        const btn = el("button", "fnbar" + (s.outcome ? " out" : "")
          + (s.q ? "" : " nodrill"));
        btn.style.width = w + "%";
        btn.title = s.note;
        if (s.q) btn.onclick = () => openRows(`${s.lbl} — ${rl}`, s.q, chartCols);
        const meta = el("div", "fnmeta");
        meta.innerHTML = `<span class="fnlbl">${esc(s.lbl)}</span>`
          + `<span class="fnnum">${s.n == null ? "—" : nfmt(s.n)}</span>`
          + (scanned && s.n != null && !s.outcome
              ? `<span class="fnpct">${pctOf(s.n, scanned)}% of scanned</span>` : "")
          + (conv != null && s.n != null
              ? `<span class="fnconv ${conv < 50 ? "warn" : ""}">${conv}% step</span>` : "");
        row.append(meta, btn);
        fn.appendChild(row);
        // the drop-off band between this step and the next (only where the
        // failing rows are actually identifiable, i.e. inside the job table)
        if (i === 0) {
          const lost = scanned - passed.length;
          if (lost > 0) fn.appendChild(dropRow(`${nfmt(lost)} killed by the hard`
            + ` floors`, scopeQ({ skip: "__any__" }), rl));
        }
        if (i === 2) {
          const lost = scored.length - hot.length;
          if (lost > 0) fn.appendChild(dropRow(`${nfmt(lost)} scored ${FLOOR}–`
            + `${HOT - 1} — real but not hot`,
            scopeQ({ score_min: FLOOR, score_max: HOT }), rl));
        }
        if (i === 3) {
          const lost = hot.length - carded.length;
          if (lost > 0) fn.appendChild(dropRow(`${nfmt(lost)} hot but never`
            + ` carded (shadow era / expired)`,
            scopeQ({ score_min: HOT, carded: "0" }), rl));
        }
      });
      fc.appendChild(fn);
      charts.appendChild(fc);

      // ── OUTCOMES (deliberately NOT part of the funnel above) ─────────────
      // These three come from three different sources with three different
      // coverage windows. They do not divide into 'carded', and showing a
      // conversion between them would be a lie: 'Hired 55, 75% of replies'
      // implied three quarters of client replies become contracts, when in
      // truth most of those contracts predate the scanner entirely.
      charts.appendChild(el("h2", "section",
        `Outcomes <span class="hint muted">— ${esc(rl)} · what actually came`
        + ` back · these are NOT a subset of the funnel above, so there is no`
        + ` conversion % between them</span>`));
      const oc = el("div", "card");
      const og = el("div", "outcomes");
      const outs = [
        { lbl: "Applied", n: railCovers ? A().length : null,
          src: railCovers
            ? `connects-history rail · live since ${esc(railSince || "")}`
            // the uncovered case must never read as "you applied to nothing" —
            // it means "we weren't counting yet". Say so, and say what IS known.
            : `NOT MEASURED for this range — the rail only counts from`
              + ` ${esc(railSince || "—")}`
              + (railSince ? ` (${nfmt(A().length)} since then)` : ""),
          zero: "a true zero — applications are paused",
          stage: "applied", cols: appliedCols },
        { lbl: "Client replied", n: RP().length,
          src: "room-sourced threads · reaches back to 2024",
          stage: "replied", cols: threadCols },
        { lbl: "Hired", n: H().length,
          src: "contracts started · most predate the scanner",
          stage: "hired", cols: hiredCols },
      ];
      outs.forEach((o) => {
        const b3 = el("button", "ocard" + (o.n == null ? " unknown" : ""));
        b3.innerHTML = `<div class="lbl">${esc(o.lbl)}</div>`
          + `<div class="val">${o.n == null ? "—" : nfmt(o.n)}</div>`
          + `<div class="sub2">${esc(o.n === 0 && o.zero ? o.zero : o.src)}</div>`;
        if (o.n != null) {
          // the drill inherits the SAME month bounds the card counted with —
          // otherwise a range-scoped card would open an all-time row list
          const [of_, ot] = bounds();
          b3.onclick = () => openRows(`${o.lbl} — ${rl}`,
            { stage: o.stage, frm: of_, to: ot }, o.cols);
        }
        og.appendChild(b3);
      });
      oc.appendChild(og);
      oc.appendChild(el("div", "muted small",
        "Three sources, three coverage windows — that is why they are not"
        + " stacked into the funnel. The connects rail only measures applications"
        + (railSince ? ` from ${esc(railSince)}` : "")
        + "; client replies and contracts reach back to 2024, long before the"
        + " scanner existed. A blank is “not measured”, never a zero."));
      charts.appendChild(oc);

      // ── 2. SCORE DISTRIBUTION ───────────────────────────────────────────
      charts.appendChild(el("h2", "section",
        `Score distribution <span class="hint muted">— ${esc(rl)} · where the`
        + ` model actually lands · the hot bar sits at ${HOT}</span>`));
      const BW = 4, TOP = 40;
      const hist = [];
      for (let lo = 0; lo < TOP; lo += BW) {
        const n = js.filter((j) => j.s != null && j.s >= lo && j.s < lo + BW).length;
        hist.push({ lo, hi: lo + BW, n });
      }
      const hmax = Math.max(1, ...hist.map((h) => h.n));
      const hc = el("div", "card");
      const hch = el("div", "mchart");
      hist.forEach((h) => {
        const col = el("button", "mcol" + (h.lo >= HOT ? " hot" : ""));
        const bh = el("div", "mbar");
        bh.style.height = (100 * h.n) / hmax + "%";
        col.append(bh, el("div", "xlbl", String(h.lo)));
        col.title = `${h.n} jobs scored ${h.lo}–${h.hi - 1}`;
        if (h.n) col.onclick = () => openRows(
          `Scored ${h.lo}–${h.hi - 1} — ${rl}`,
          scopeQ({ score_min: h.lo, score_max: h.hi }), chartCols);
        hch.appendChild(col);
      });
      hc.appendChild(hch);
      const nScored = js.filter((j) => j.s != null).length;
      const pHot = pctOf(js.filter((j) => j.s != null && j.s >= HOT).length, nScored);
      hc.appendChild(el("div", "muted small",
        `${nfmt(nScored)} scored · ${pHot}% clear the hot bar of ${HOT}`
        + (pHot > 60 ? " — the bar is a rubber stamp at this rate; ICP is the"
          + " tier that actually discriminates" : "")));
      charts.appendChild(hc);

      // ── 3. WHY WE SKIP ──────────────────────────────────────────────────
      // hourly_below_30 is SPLIT: a stated low rate is a real reject; a MISSING
      // rate is the rule misfiring on absent data (the 2026-07-13 finding).
      charts.appendChild(el("h2", "section",
        `Why we skip <span class="hint muted">— ${esc(rl)} · the jobs the hard`
        + ` floors killed before scoring · click any bar to read what we threw`
        + ` away</span>`));
      const skipped = js.filter((j) => j.k);
      // Reasons are DERIVED from the data, never hardcoded: the reason strings
      // drifted across the v2/v3 scanners (17 distinct values live in the
      // table) and a fixed list would silently swallow the ones it forgot.
      // Anything unmapped still shows, under its raw key.
      const LBL = {
        no_rate: "hourly — no rate (pre-fix, scored as $0)",
        rate_unstated_low_client: "hourly — no rate + unproven client",
        hourly_below_30: "hourly < $30 (stated)",
        low_hourly_rate: "hourly < $30 (stated, v2 rule)",
        low_hourly: "hourly < $30 (stated, v2 rule)",
        fixed_below_500: "fixed < $500",
        fixed_below_floor: "fixed below floor (v2 rule)",
        fixed_5_placeholder: "fixed $5 placeholder",
        fixed_5_low_quality: "fixed $5, low quality",
        fixed_low_weak_client: "fixed low + weak client",
        n8n_exclusion: "n8n-only",
        outside_domain: "outside our domain",
        out_of_domain: "outside our domain (v2 rule)",
        no_asia_timezone: "no-Asia timezone",
        no_asia_tz: "no-Asia timezone (v2 rule)",
        onsite_required: "onsite required",
        already_hired: "already hired",
        already_applied: "already applied",
        client_filled: "client filled the role",
        spam: "spam",
      };
      const HOURLY_SKIPS = ["hourly_below_30", "low_hourly_rate", "low_hourly"];
      const byReason = {};
      skipped.forEach((j) => {
        // an hourly-floor skip with NO stated rate is its OWN reason: the client
        // left the rate blank, Upwork ships it as $0, and the floor reads $0 as
        // cheap. That is an unknown, not a reject — it must not hide inside the
        // "too cheap" bar (2026-07-13 finding).
        const key = (j.nr && HOURLY_SKIPS.includes(j.k)) ? "no_rate" : j.k;
        byReason[key] = (byReason[key] || 0) + 1;
      });
      const reasons = Object.entries(byReason).map(([key, n]) => ({
        key, n, lbl: LBL[key] || key, flag: key === "no_rate",
      })).sort((x, y) => y.n - x.n);
      const sc = el("div", "card");
      const sk = el("div", "wf");
      const smax = Math.max(1, ...reasons.map((r) => r.n));
      reasons.forEach((r) => {
        const row = el("button", "wfrow" + (r.flag ? " flag" : ""));
        const bl = el("div", "bl");
        const sp = el("span");
        sp.style.width = (100 * r.n) / smax + "%";
        sp.style.background = r.flag ? "var(--amber)" : "var(--red)";
        bl.appendChild(sp);
        row.append(el("div", "n", esc(r.lbl) + (r.flag
          ? ' <span class="chip warn">rule misfire?</span>' : "")), bl,
          el("div", "amt", nfmt(r.n)));
        row.onclick = () => openRows(`Skipped — ${r.lbl} · ${rl}`,
          scopeQ({ skip: r.key }), chartCols);
        sk.appendChild(row);
      });
      if (!reasons.length) {
        sk.appendChild(el("div", "emptystate", "Nothing was skipped in this range."));
      }
      sc.appendChild(sk);
      const nNoRate = byReason.no_rate || 0;
      const nUnproven = byReason.rate_unstated_low_client || 0;
      sc.appendChild(el("div", "muted small",
        `${nfmt(skipped.length)} of ${nfmt(js.length)} scanned jobs were killed`
        + ` before scoring (${pctOf(skipped.length, js.length)}%).`
        + (nNoRate ? ` ${nfmt(nNoRate)} historical rows failed ONLY because the`
          + ` client never stated a rate — the old $30 floor read a blank as $0.`
          + ` FIXED 2026-07-16 (option B): a rate-unstated job now scores when`
          + ` the client is a proven spender, and only skips (as “no rate +`
          + ` unproven client”${nUnproven ? `, ${nfmt(nUnproven)} here` : ""})`
          + ` otherwise.` : "")));
      charts.appendChild(sc);

      // ── 4. FEED ROI + WEEKLY CONVERSION TREND ───────────────────────────
      charts.appendChild(el("h2", "section",
        `Feed ROI <span class="hint muted">— ${esc(rl)} · which feed actually`
        + ` produces work · click any cell</span>`));
      const feeds = [...new Set(js.map((j) => j.f).filter(Boolean))];
      const ft2 = el("table");
      ft2.innerHTML = "<tr><th>Feed</th><th class='num'>Scanned</th>"
        + `<th class='num'>Hot ≥${HOT}</th><th class='num'>Carded</th>`
        + "<th class='num'>ICP</th><th class='num'>Carded rate</th></tr>";
      feeds.map((f) => {
        const rowsF = js.filter((j) => j.f === f);
        return { f, n: rowsF.length,
                 hot: rowsF.filter((j) => j.s != null && j.s >= HOT).length,
                 carded: rowsF.filter((j) => j.c).length,
                 icp: rowsF.filter((j) => j.t === "icp").length };
      }).sort((x, y) => y.n - x.n).forEach((r) => {
        const tr = el("tr");
        const cell = (v, q) => {
          const td = el("td", "num" + (v && q ? " drillable" : ""), nfmt(v));
          if (v && q) td.onclick = () => openRows(
            `${r.f} — ${rl}`, scopeQ(Object.assign({ feed: r.f }, q)), chartCols);
          return td;
        };
        tr.appendChild(el("td", null, `<code>${esc(r.f)}</code>`));
        tr.appendChild(cell(r.n, {}));
        tr.appendChild(cell(r.hot, { score_min: HOT }));
        tr.appendChild(cell(r.carded, { carded: "1" }));
        tr.appendChild(cell(r.icp, { icp: "icp" }));
        tr.appendChild(el("td", "num muted", pctOf(r.carded, r.n) + "%"));
        ft2.appendChild(tr);
      });
      const fcard = el("div", "card");
      fcard.appendChild(el("div", "tablewrap", "")).appendChild(ft2);
      charts.appendChild(fcard);

      // weekly conversion trend — scanned bars + carded-rate line
      const wk = (iso) => {
        const dt = new Date(iso + "T00:00:00");
        dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7));   // back to Monday
        return dt.toISOString().slice(0, 10);
      };
      const byWeek = {};
      js.forEach((j) => {
        if (!j.d) return;
        const w = wk(j.d);
        (byWeek[w] = byWeek[w] || { n: 0, c: 0, hot: 0 });
        byWeek[w].n++;
        if (j.c) byWeek[w].c++;
        if (j.s != null && j.s >= HOT) byWeek[w].hot++;
      });
      const wks = Object.keys(byWeek).sort().slice(-13);
      if (wks.length) {
        charts.appendChild(el("h2", "section",
          `Weekly throughput <span class="hint muted">— scanned volume (bars)`
          + ` vs the share that reached the board (line) · last ${wks.length}`
          + ` weeks in range</span>`));
        const tc = el("div", "card");
        const wmax = Math.max(1, ...wks.map((w) => byWeek[w].n));
        const tch = el("div", "mchart trend");
        // the carded-% line, drawn over the bars
        // points sit at the CENTRE of each bar's column (i + 0.5 of n columns),
        // not at the column edge — an off-by-half-a-column line reads as noise
        const NS = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(NS, "svg");
        svg.setAttribute("class", "trendline");
        svg.setAttribute("viewBox", `0 0 ${wks.length} 100`);
        svg.setAttribute("preserveAspectRatio", "none");
        const xy = wks.map((w, i) => [
          i + 0.5, 100 - Math.min(100, pctOf(byWeek[w].c, byWeek[w].n))]);
        const poly = document.createElementNS(NS, "polyline");
        poly.setAttribute("points", xy.map(([x, y]) => `${x},${y}`).join(" "));
        // no dots: the viewBox is stretched non-uniformly (preserveAspectRatio
        // none), so a circle would render as a squashed ellipse. The line alone,
        // with a non-scaling stroke, is the only shape that survives the stretch.
        svg.appendChild(poly);
        wks.forEach((w) => {
          const b2 = byWeek[w];
          const col = el("button", "mcol");
          const bh = el("div", "mbar");
          bh.style.height = (100 * b2.n) / wmax + "%";
          col.append(bh, el("div", "xlbl", w.slice(5)));
          col.title = `week of ${w}: ${b2.n} scanned · ${b2.hot} hot · ${b2.c}`
            + ` carded (${pctOf(b2.c, b2.n)}%)`;
          col.onclick = () => openRows(`Scanned — week of ${w}`,
            { stage: "scanned", week: w }, jobCols);
          tch.appendChild(col);
        });
        tch.appendChild(svg);
        tc.appendChild(tch);
        tc.appendChild(el("div", "muted small",
          "Bars = jobs scanned. Line = the share that reached the ClickUp board."
          + " A line that sinks while bars hold means the feed got worse, not quieter."));
        charts.appendChild(tc);
      }

      // ── 5. LEAD QUALITY (ICP tier) ──────────────────────────────────────
      charts.appendChild(el("h2", "section",
        `Lead quality <span class="hint muted">— ${esc(rl)} · the ICP overlay`
        + ` above the hot bar · deterministic, recomputable, drillable</span>`));
      const scoredJs = js.filter((j) => j.s != null);
      const tiers = [
        { k: "icp", lbl: "ICP / Prime", cls: "t-icp",
          n: scoredJs.filter((j) => j.t === "icp").length,
          q: { icp: "icp" },
          note: "Airtable/Make + $51/hr + rich client + long-term" },
        { k: "icp_near", lbl: "Near-miss", cls: "t-near",
          n: scoredJs.filter((j) => j.t === "icp_near").length,
          q: { icp: "icp_near" }, note: "must-haves hold, one qualifier short" },
        { k: "hot", lbl: `Hot ≥${HOT} (not prime)`, cls: "t-hot",
          n: scoredJs.filter((j) => j.s >= HOT && !j.t).length,
          q: { score_min: HOT }, note: "cleared the bar, not the profile" },
        { k: "rest", lbl: `Scored < ${HOT}`, cls: "t-rest",
          n: scoredJs.filter((j) => j.s < HOT).length,
          q: { score_max: HOT }, note: "below the hot bar" },
      ];
      const qc = el("div", "card");
      const stack = el("div", "qstack");
      const qTot = Math.max(1, scoredJs.length);
      tiers.filter((t) => t.n).forEach((t) => {
        const seg2 = el("button", "qseg " + t.cls);
        seg2.style.flex = String(t.n);
        seg2.title = `${t.lbl}: ${t.n} (${pctOf(t.n, qTot)}%) — ${t.note}`;
        seg2.onclick = () => openRows(`${t.lbl} — ${rl}`,
          scopeQ(t.q), chartCols);
        stack.appendChild(seg2);
      });
      qc.appendChild(stack);
      const leg = el("div", "qlegend");
      tiers.forEach((t) => {
        const it = el("button", "qlegit");
        it.innerHTML = `<span class="dot3 ${t.cls}"></span>${esc(t.lbl)}`
          + ` <b>${nfmt(t.n)}</b> <span class="muted">${pctOf(t.n, qTot)}%</span>`;
        if (t.n) it.onclick = () => openRows(`${t.lbl} — ${rl}`, scopeQ(t.q), chartCols);
        leg.appendChild(it);
      });
      qc.appendChild(leg);
      qc.appendChild(el("div", "muted small",
        "ICP is recomputed from stored fields (bin/icp-backfill) — the tier in"
        + " the DB is always the tier today's profile would award."));
      charts.appendChild(qc);
    }
    redraw();

    // ── Funnel by week (cells drill) ──
    view.appendChild(el("h2", "section",
      `Funnel by week <span class="hint muted">— scanned → scored ≥8 → carded`
      + ` → applied → replied → hired · click a cell for its rows · “—” = not`
      + ` measured that week</span>`));
    const fCard = el("div", "card");
    const fWrap = el("div", "tablewrap");
    const ft = el("table");
    ft.innerHTML = "<tr><th>Week</th><th class='num'>Scanned</th>"
      + "<th class='num'>Scored ≥8</th><th class='num'>Carded</th>"
      + "<th class='num'>Applied</th><th class='num'>Client replied</th>"
      + "<th class='num'>Hired</th></tr>";
    const stages = [["scanned", "scanned", jobCols], ["scored", "scored", jobCols],
                    ["carded", "carded", jobCols], ["applied", "applied", appliedCols],
                    ["replied", "replied", threadCols], ["hired", "hired", hiredCols]];
    (d.funnel_weeks || []).slice().reverse().forEach((w) => {
      const tr = el("tr");
      const tdW = el("td", null,
        `${esc(w.week)}${w.current ? ' <span class="chip">current</span>' : ""}`);
      tr.appendChild(tdW);
      stages.forEach(([key, stage, cols]) => {
        const v = w[key];
        const td = el("td", "num" + (v ? " drillable" : ""),
          v == null ? "—" : String(v));
        if (v) td.onclick = () => openRows(
          `${stage[0].toUpperCase() + stage.slice(1)} — week of ${w.week}`,
          { stage, week: mondayOf(w.week) }, cols);
        tr.appendChild(td);
      });
      ft.appendChild(tr);
    });
    fWrap.appendChild(ft);
    fCard.appendChild(fWrap);
    fCard.appendChild(el("div", "muted small",
      "The outage (w/06-22) and shadow weeks (carded 0) are real history —"
      + " the funnel never rewrites the past."));
    view.appendChild(fCard);

    // ── Threads by status (rows drill) ──
    view.appendChild(el("h2", "section",
      `Threads by status <span class="hint muted">— the ${cr.total || 0}`
      + ` room-sourced proposals · click a status</span>`));
    const t = el("table");
    t.innerHTML = "<tr><th>Status</th><th class='num'>Count</th></tr>";
    Object.entries(d.by_status || {}).sort((x, y) => y[1] - x[1]).forEach(([s, n]) => {
      const tr = el("tr");
      tr.style.cursor = "pointer";
      tr.innerHTML = `<td><span class="pill">${esc(s)}</span></td>`
        + `<td class="num drillable">${n}</td>`;
      tr.onclick = () => openRows(`Threads — ${s}`,
        { stage: "threads", status: s }, threadCols);
      t.appendChild(tr);
    });
    view.appendChild(t);
  },
  Money: renderMoney,
  Clients: async (view) => {
    let sort = localStorage.getItem("ew_clients_sort") || "revenue";
    const drill = el("div", "drill");

    async function render() {
      view.innerHTML = "";
      const d = await api("/api/clients?sort=" + sort);
      view.appendChild(el("h2", "section",
        `Clients <span class="hint muted">— revenue economics from the ledger;`
        + ` every figure drills to its transactions</span>`));
      if (d.blocked) {
        view.appendChild(el("div", "blocked",
          `<h3>Client economics hidden</h3><div>${esc(d.reason || "finance-doctor RED")}</div>`));
        return;
      }

      // headline: total revenue, attributed vs unlinked, parity to the P&L
      const clientSum = (d.clients || []).reduce((s, c) => s + c.revenue_usd, 0);
      const parityOk = Math.abs(clientSum + (d.unlinked.usd || 0) - d.revenue_total_usd) < 0.01;
      const kpis = el("div", "kpis");
      const mk = (lbl, val, sub, act) => {
        const k = el("button", "kpi" + (act ? "" : " static"));
        k.innerHTML = `<div class="lbl">${lbl}</div><div class="val">${val}</div>`
          + `<div class="sub2">${sub}</div>` + (act ? `<div class="tag">details →</div>` : "");
        if (act) k.onclick = act;
        return k;
      };
      kpis.appendChild(mk("Revenue to date", fmtUsd(d.revenue_total_usd),
        `${d.count} clients · ${fmtUsd(d.attributed_usd)} attributed`, null));
      kpis.appendChild(mk("Direct / unlinked", fmtUsd(d.unlinked.usd),
        `${d.unlinked.payments} payments · no parseable payer`,
        () => openTxns("__unlinked__", "Direct / unlinked revenue")));
      kpis.appendChild(mk("Contracts linked",
        `${d.linkage.contracts_linked}/${d.linkage.contracts_total}`,
        "active contracts matched to a paying client", null));
      view.appendChild(kpis);
      if (!parityOk) view.appendChild(el("div", "muted small",
        '⚠ client totals do not reconcile to the P&L — trust bin/finance-pnl'));

      // sort toggle
      const bar = el("div", "rangebar");
      const seg = el("div", "rangeseg");
      [["revenue", "By revenue"], ["recent", "By recent activity"]].forEach(([k, lbl]) => {
        const b = el("button", "rbtn" + (sort === k ? " on" : ""), lbl);
        b.onclick = () => { sort = k; localStorage.setItem("ew_clients_sort", k); render(); };
        seg.appendChild(b);
      });
      bar.appendChild(seg);
      view.appendChild(bar);

      const wrap = el("div", "tablewrap");
      const t = el("table", "clients-table");
      t.innerHTML = "<tr><th>Client</th><th class='num'>Revenue to date</th>"
        + "<th class='num'>Payments</th><th>Channels</th><th class='num'>Active</th>"
        + "<th class='num'>Owed</th><th>Last activity</th></tr>";
      (d.clients || []).forEach((c) => {
        const tr = el("tr");
        const nm = esc(c.name) + (c.passthrough
          ? ' <span class="pill warn" title="a payment processor / EOR — the end client is not yet mapped">attribution pending</span>' : "");
        tr.innerHTML =
          `<td><button class="linkname">${nm}</button></td>`
          + `<td class="num strong">${fmtUsd(c.revenue_usd)}</td>`
          + `<td class="num">${c.payments}</td>`
          + `<td>${(c.channels || []).map((x) => `<span class="pill">${esc(x)}</span>`).join(" ")}</td>`
          + `<td class="num">${c.active_contracts || "—"}</td>`
          + `<td class="num">${c.owed_replies || "—"}</td>`
          + `<td class="muted">${esc(c.last || "—")}</td>`;
        tr.querySelector(".linkname").onclick = () => openTxns(c.name, c.name);
        t.appendChild(tr);
      });
      wrap.appendChild(t);
      view.appendChild(wrap);
      view.appendChild(el("div", "muted small", esc(d.linkage.note)));
      view.appendChild(drill);
    }

    async function openTxns(key, title) {
      const res = await api("/api/clients/transactions?client=" + encodeURIComponent(key));
      drill.innerHTML = ""; drill.classList.add("open");
      const h = el("h4", null, `${esc(title)} <span class="count">— ${res.count}`
        + ` payment${res.count === 1 ? "" : "s"} · ${fmtUsd(res.sum_usd)}</span>`
        + `<button class="close">✕ close</button>`);
      h.querySelector(".close").onclick = () => drill.classList.remove("open");
      drill.appendChild(h);
      const wrap = el("div", "tablewrap"), t = el("table");
      t.innerHTML = "<tr><th>Date</th><th>Source</th><th>Payer</th><th>Description</th><th class='num'>USD</th></tr>";
      (res.rows || []).forEach((r) => {
        const tr = el("tr");
        tr.innerHTML = `<td>${esc(r.dt)}</td><td><span class="pill">${esc(r.source)}</span></td>`
          + `<td>${esc(r.payer)}</td><td class="muted">${esc(r.desc)}</td>`
          + `<td class="num">${fmtUsd(r.usd)}</td>`;
        t.appendChild(tr);
      });
      wrap.appendChild(t); drill.appendChild(wrap);
      drill.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    await render();
  },
  Nudges: async (view) => {
    const d = await api("/api/nudges");
    view.appendChild(el("h2", "section", `Nudge Queue (${d.total || 0})`));
    view.appendChild(el("div", "muted", "Chief of Staff — ranked by urgency. It surfaces; you decide."));

    const cosAct = async (dedupKey, action) => {
      await fetch("/api/cos-nudge", { method: "POST",
        headers: { "X-Cockpit-Token": bootToken(), "Content-Type": "application/json" },
        body: JSON.stringify({ dedup_key: dedupKey, action }) });
      show("Nudges");
    };
    const actions = (dedupKey) => {
      const row = el("div", "fu-actions");
      [["Done", "done"], ["Snooze", "snooze"], ["Dismiss", "dismiss"]].forEach(([lbl, a]) => {
        const b = el("button", "action", lbl);
        b.onclick = () => cosAct(dedupKey, a);
        row.appendChild(b);
      });
      return row;
    };

    (d.entries || []).forEach((e) => {
      const card = el("div", "card");
      const head = el("div", "fu-row");
      const left = el("div", "fu-left");
      left.appendChild(el("div", null,
        `<span class="pill">${esc(e.kind)}</span> <b>${esc(e.title)}</b>`
        + (e.due ? ` <span class="muted">· due ${esc(e.due.slice(0,10))}</span>` : "")));
      if (e.action) left.appendChild(el("div", "muted", esc(e.action)));
      head.appendChild(left);
      const ur = el("div", "muted"); ur.textContent = "u=" + Math.round(e.urgency);
      head.appendChild(ur);
      card.appendChild(head);
      if (e.grouped) {
        const det = el("details");
        det.appendChild(el("summary", "muted", `expand ${e.count} items`));
        (e.items || []).forEach((it) => {
          const r = el("div", "fu-row");
          const l = el("div", "fu-left");
          l.appendChild(el("div", null, esc(it.title)
            + (it.due ? ` <span class="muted">· due ${esc((it.due||"").slice(0,10))}</span>` : "")));
          r.appendChild(l);
          r.appendChild(actions(it.dedup_key));
          det.appendChild(r);
        });
        card.appendChild(det);
      } else {
        card.appendChild(actions(e.dedup_key));
      }
      view.appendChild(card);
    });
    if (!(d.entries || []).length) placeholder(view, "Queue is clear. ✓");

    // brief replay
    const lb = d.latest_brief;
    if (lb) {
      view.appendChild(el("h2", "section", "Latest brief (draft)"));
      const bc = el("div", "card");
      bc.appendChild(el("div", "muted", esc(lb.name)));
      bc.appendChild(el("div", null, lb.html));   // server-rendered, our own HTML
      view.appendChild(bc);
    }
  },
  Knowledge: async (view) => {
    const d = await api("/api/knowledge");
    view.appendChild(el("h2", "section",
      `Knowledge <span class="hint muted">— the cited client memory; every`
      + ` dossier carries what the client is worth</span>`));
    if (d.status === "stub") { placeholder(view, d.message); return; }

    // ── freshness line (a stale KB serving old facts is a dead feed) ──
    const fr = d.freshness || {};
    const kbOk = !fr.stale && fr.oldest_days != null;
    const sline = el("div", "srcline" + (kbOk ? " ok" : " bad"));
    sline.innerHTML = (kbOk ? "✓ " : "⚠ ")
      + (fr.dossiers
          ? `${fr.dossiers} dossiers · ${fr.stale || 0} stale (>${fr.stale_after_days}d)`
            + (fr.oldest_days != null ? ` · oldest recompiled ${fr.oldest_days}d ago` : "")
            + (fr.kb_daily_last ? ` · kb-daily ran ${esc(fr.kb_daily_last.replace("T", " ").slice(5, 16))}` : " · kb-daily never ran")
          : "no dossiers compiled yet");
    view.appendChild(sline);

    const t = d.totals || {};
    const strip = el("div", "grid");
    strip.appendChild(card("Facts", `<div class="metric">${t.facts || 0}</div>`
      + `<div class="muted small">${t.client_facts || 0} client · ${t.agency_facts || 0} agency · ${t.cross_facts || 0} cross</div>`));
    strip.appendChild(card("Sources", `<div class="metric">${t.sources || 0}</div>`
      + `<div class="muted small">meetings, docs & threads behind the facts</div>`));
    const f = d.fathom || {};
    strip.appendChild(card("Fathom ingest",
      `<div class="metric small">${f.last_poll ? "daily ✓" : "not running"}</div>`
      + `<div class="muted small">${f.last_poll
          ? "last pull " + esc(String(f.last_poll).replace("T", " ").slice(5, 16))
            + (f.last_saved != null ? ` · ${f.last_saved} new transcript${f.last_saved === 1 ? "" : "s"}` : "")
          : "kb-daily has not completed — transcripts are not being pulled"}</div>`
      + `<div class="muted small">through ${esc((f.cursor || "—")).slice(0, 16)}</div>`));
    const pp = d.proof_points || {};
    const ppn = (pp.agency || 0) + (pp.cross || 0);
    strip.appendChild(card("Proof-points",
      `<div class="metric">${ppn}</div>`
      + `<div class="muted small">${ppn ? `${pp.agency || 0} agency · ${pp.cross || 0} cross-client`
          : "none captured yet — agency/cross-client facts you can cite in pitches"}</div>`));
    view.appendChild(strip);

    // Ask box — proxies the kb-librarian (model runs out-of-process)
    const ask = el("div", "card");
    ask.appendChild(el("h3", null, "Ask the librarian"));
    ask.appendChild(el("div", "muted", "Cited answers only — it refuses when the kb is silent."));
    const arow = el("div", "unlock-row");
    const sel = el("select", "kb-select");
    sel.appendChild(new Option("All clients", ""));
    (d.dossiers || []).forEach((x) => sel.appendChild(new Option(x.client_name, x.client_id)));
    const qi = el("input", "unlock-input"); qi.placeholder = "e.g. what is their stack?";
    const ab = el("button", "action primary", "Ask");
    const out = el("div", "kb-answer");
    const doAsk = async () => {
      const question = qi.value.trim(); if (!question) return;
      ab.disabled = true; ab.textContent = "Asking…"; out.innerHTML = "";
      try {
        const r = await fetch("/api/ask", { method: "POST",
          headers: { "X-Cockpit-Token": bootToken(), "Content-Type": "application/json" },
          body: JSON.stringify({ question, client: sel.value }) });
        const a = await r.json();
        if (a.error) { out.appendChild(el("div", "unlock-err", esc(a.error))); }
        else {
          out.appendChild(el("div", a.grounded ? "" : "muted", esc(a.answer)));
          (a.citations || []).forEach((c) => out.appendChild(el("div", "kb-cite", esc(c))));
        }
      } catch (e) { out.appendChild(el("div", "unlock-err", "ask failed")); }
      ab.disabled = false; ab.textContent = "Ask";
    };
    ab.onclick = doAsk;
    qi.onkeydown = (e) => { if (e.key === "Enter") doAsk(); };
    arow.append(sel, qi, ab);
    ask.append(arow, out);
    view.appendChild(ask);

    // Search box — FTS over cited facts
    const search = el("div", "card");
    search.appendChild(el("h3", null, "Search facts"));
    const srow = el("div", "unlock-row");
    const si = el("input", "unlock-input"); si.placeholder = "full-text search across all dossiers";
    const sbtn = el("button", "action", "Search");
    const sres = el("div", "kb-results");
    const doSearch = async () => {
      const q = si.value.trim(); if (!q) return;
      sbtn.disabled = true; sres.innerHTML = "";
      try {
        const r = await api("/api/knowledge?q=" + encodeURIComponent(q));
        const rows = r.results || [];
        if (!rows.length) sres.appendChild(el("div", "muted", "No matches."));
        rows.forEach((x) => sres.appendChild(factLine(x, true)));
      } catch (e) { sres.appendChild(el("div", "unlock-err", "search failed")); }
      sbtn.disabled = false;
    };
    sbtn.onclick = doSearch;
    si.onkeydown = (e) => { if (e.key === "Enter") doSearch(); };
    srow.append(si, sbtn);
    search.append(srow, sres);
    view.appendChild(search);

    // Dossier browser — each card ties knowledge to economics + freshness
    view.appendChild(el("h2", "section",
      `Dossiers <span class="hint muted">— facts + revenue-to-date · click to`
      + ` open</span>`));
    const detail = el("div", "kb-detail");
    const grid = el("div", "grid");
    (d.dossiers || []).forEach((x) => {
      const rev = x.revenue_usd != null
        ? `<span class="pill ok">${fmtUsd(x.revenue_usd)}</span>` : "";
      const stale = x.stale
        ? ` <span class="pill warn" title="not recompiled in over ${fr.stale_after_days} days">stale ${x.stale_days}d</span>` : "";
      const c = card(esc(x.client_name) + " " + rev,
        `<div class="metric">${x.fact_count}</div>`
        + `<div class="muted small">${x.source_count} sources · compiled `
        + `${esc((x.last_compiled_at || "").slice(0, 10)) || (x.empty_reason || "—")}${stale}</div>`);
      c.style.cursor = "pointer";
      c.onclick = () => openDossier(x.client_id, detail);
      grid.appendChild(c);
    });
    view.appendChild(grid);
    view.appendChild(detail);
  },
};

// ════════════════════════ MONEY (flagship section) ════════════════════════
// Principle: every number is explainable — click any figure and the exact
// transactions behind it expand, and they sum to it, to the cent.
const BLABEL = { revenue: "Revenue", refund: "Refunds", platform_fee: "Platform fees",
  tax: "Taxes", payroll: "Payroll", opex: "Operating expenses", owner_draw: "Owner draws",
  transfer: "Transfers", sweep: "Sweeps", cc_settlement: "Card settlement",
  uncategorized: "Uncategorized" };

function moneyTip() {
  let tip = document.getElementById("tip");
  if (!tip) { tip = el("div", "tip"); tip.id = "tip"; document.body.appendChild(tip); }
  return tip;
}

// Who/what is behind a row — vendor for card/POS spends, payee for bank
// transfers, client for earnings. Presentational only (sums stay exact).
function whoOf(x) {
  const d = (x.d || "").toUpperCase();
  let m = d.match(/^POS(?:-VISA)?\/([^/]+)/);
  if (m) return m[1].replace(/\s+(CYBS|SI)\b.*/, "").trim().slice(0, 26);
  m = d.match(/^(?:NEFT|RTGS)\/[^/]+\/([^/]+)\//);
  if (m) return m[1].trim().slice(0, 26);
  m = d.match(/^IMPS[^/]*\/[^/]*\/[^/]*\/([^/]+)/);
  if (m) return m[1].trim().slice(0, 26);
  if (d.startsWith("UPI")) return "UPI payments";
  if (d.includes("EMI")) return "Loan EMI";
  m = (x.d || "").match(/^\(\d+\)\s*([^-]+?)\s*-/);          // Upwork hourly: client
  if (m) return m[1].trim().slice(0, 26);
  m = (x.d || "").match(/Received money from ([^.]+?) with/i); // Wise receipt: sender
  if (m) return m[1].trim().slice(0, 26);
  if (x.s === "Upwork" || x.s === "Upwork-CSV") return x.t || "Upwork";
  const vendor = (x.d || "").split(",")[0].trim();             // card rows: "VENDOR, CITY"
  if (x.s === "Card" && vendor) return vendor.slice(0, 26);
  return (x.s || "?") + (x.t ? " · " + x.t : "");
}

// Drill panel v2 (founder flow): headline count+sum -> WHERE-IT-WENT breakdown
// (grouped pills, biggest first, click to narrow) -> search -> rows.
// Traceability contract unchanged: the header sums the returned rows only.
function openDrill(host, title, rows, opts) {
  opts = opts || {};
  host.innerHTML = "";
  host.classList.add("open");
  const total = rows.reduce((s, r) => s + r.u, 0);
  const h = el("h4", null, `${esc(title)} <span class="count">— ${rows.length}`
    + ` transaction${rows.length === 1 ? "" : "s"} · sum ${fmtUsd2(total)}</span>`
    + `<button class="close">✕ close</button>`);
  h.querySelector(".close").onclick = () => host.classList.remove("open");
  host.appendChild(h);
  if (!rows.length) {
    host.appendChild(el("div", "emptystate", "No transactions behind this figure."));
    host.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return;
  }

  let pickBucket = null, pickWho = null, needle = "";
  // cost drills read biggest-outflow-first; revenue/month drills newest-first
  let sortAmount = opts.sortAmount != null ? opts.sortAmount : total < 0;

  const buckets = [...new Set(rows.map((x) => x.b))];
  const strips = el("div");
  const body = el("div");
  host.append(strips, body);

  function pills(items, active, onpick, allLabel) {
    const strip = el("div", "drillstrip");
    const mk = (label, key, sum, n, cls) => {
      const b = el("button", "gpill" + (cls || "") + (active === key ? " on" : ""),
        `${esc(label)} <span class="gsum">${fmtUsd0(sum)}</span>`
        + (n != null ? ` <span class="gn">×${n}</span>` : ""));
      b.onclick = () => onpick(active === key ? null : key);
      return b;
    };
    strip.appendChild(mk(allLabel, null, items.reduce((s, g) => s + g.sum, 0), null, " all"));
    items.forEach((g) => strip.appendChild(mk(g.label, g.key, g.sum, g.n)));
    return strip;
  }

  function render() {
    // group over the CURRENT bucket selection so the who-strip follows it
    const inBucket = rows.filter((x) => !pickBucket || x.b === pickBucket);
    const groups = {};
    inBucket.forEach((x) => {
      const k = whoOf(x);
      (groups[k] = groups[k] || { key: k, label: k, sum: 0, n: 0 });
      groups[k].sum += x.u; groups[k].n += 1;
    });
    const top = Object.values(groups)
      .sort((a, b) => Math.abs(b.sum) - Math.abs(a.sum)).slice(0, 9);

    strips.innerHTML = "";
    if (buckets.length > 1) {
      const bsums = buckets.map((b) => ({
        key: b, label: BLABEL[b] || b,
        sum: rows.reduce((s, x) => x.b === b ? s + x.u : s, 0),
        n: rows.filter((x) => x.b === b).length,
      })).sort((a, b) => Math.abs(b.sum) - Math.abs(a.sum));
      strips.appendChild(pills(bsums, pickBucket,
        (k) => { pickBucket = k; pickWho = null; render(); }, "All buckets"));
    }
    if (top.length > 1) {
      strips.appendChild(pills(top, pickWho,
        (k) => { pickWho = k; render(); }, "Everyone"));
    }
    const bar = el("div", "filters");
    const q = el("input");
    q.type = "search"; q.placeholder = "search within…"; q.value = needle;
    q.oninput = () => { needle = q.value; renderRows(); };
    const st = el("button", "action", sortAmount ? "sort: biggest" : "sort: newest");
    st.onclick = () => { sortAmount = !sortAmount; st.textContent = sortAmount ? "sort: biggest" : "sort: newest"; renderRows(); };
    bar.append(q, st);
    strips.appendChild(bar);
    renderRows();
    function renderRows() {
      const nl = needle.toLowerCase();
      let out = inBucket.filter((x) => (!pickWho || whoOf(x) === pickWho)
        && (!nl || (x.d + " " + x.t + " " + x.s).toLowerCase().includes(nl)));
      out = out.slice().sort(sortAmount
        ? (a, b) => Math.abs(b.u) - Math.abs(a.u)
        : (a, b) => (a.dt < b.dt ? 1 : -1));
      const sub = out.reduce((s, r) => s + r.u, 0);
      body.innerHTML = "";
      body.appendChild(el("div", "muted small drillsub",
        `${out.length} shown · ${fmtUsd2(sub)}`));
      const wrap = el("div", "tablewrap");
      const t = el("table");
      t.innerHTML = "<tr><th>Date</th><th>Bucket</th><th>Type</th><th>Source</th>"
        + "<th>Description</th><th class='num'>Amount</th></tr>";
      out.slice(0, 300).forEach((x) => {
        const tr = el("tr");
        tr.innerHTML = `<td class="muted">${esc(x.dt)}</td>`
          + `<td><span class="chip ${esc(x.b)}">${esc(BLABEL[x.b] || x.b)}</span></td>`
          + `<td>${esc(x.t)}</td><td class="muted">${esc(x.s)}</td><td>${esc(x.d)}</td>`
          + `<td class="num ${x.u >= 0 ? "pos" : "neg"}">${fmtUsd2(x.u)}</td>`;
        t.appendChild(tr);
      });
      if (out.length > 300) t.appendChild(el("tr", null,
        `<td colspan=6 class="muted small">showing 300 of ${out.length} — narrow above</td>`));
      wrap.appendChild(t);
      body.appendChild(wrap);
    }
  }
  render();
  host.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function renderMoney(view) {
  const [d, tx] = await Promise.all([
    api("/api/money"), api("/api/money/transactions")]);
  if (d.blocked) {                       // finance-doctor RED -> show nothing
    const b = el("div", "blocked");
    b.appendChild(el("h3", null, "Finance blocked — doctor RED"));
    b.appendChild(el("div", null, esc(d.note || "")));
    b.appendChild(el("div", "muted small", "RED: " + (d.reds || []).join(", ")));
    view.appendChild(b);
    return;
  }
  const rows = tx.rows || [];            // the full reconciled ledger (drill source)
  const r = d.revenue || {}, p = d.pnl || {}, cash = d.cash || {};
  const srvBuckets = d.buckets || {};
  const byMonth = r.by_month || [];
  const thisM = r.month, lastM = r.last_month;
  const revThis = r.month_usd || 0, revLast = r.last_month_usd || 0;
  const OPERATING_COSTS = ["platform_fee", "tax", "payroll", "opex"];
  const OPERATING_ALL = ["revenue", "refund", ...OPERATING_COSTS];

  // ══ range engine (architecture: ONE scope state; flows scope, cash doesn't;
  // every scoped header + drill inherits it; all-time anchors to the SERVER
  // figures with a live client-sum parity check) ══
  const mAdd = (ym, k) => {                    // "2026-04" + k months
    let [y, m] = ym.split("-").map(Number);
    m += k; while (m < 1) { m += 12; y -= 1; } while (m > 12) { m -= 12; y += 1; }
    return `${y}-${String(m).padStart(2, "0")}`;
  };
  const PRESETS = [
    ["all", "All time", () => [null, null]],
    ["this", "This month", () => [thisM, thisM]],
    ["last", "Last month", () => [lastM, lastM]],
    ["l3", "Last 3 mo", () => [mAdd(thisM, -2), thisM]],
    ["t12", "Trailing 12", () => [mAdd(thisM, -11), thisM]],
    ["ytd", "YTD", () => [thisM.slice(0, 4) + "-01", thisM]],
    ["custom", "Custom", null],
  ];
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(MONEY_FILTERS_KEY) || "{}"); }
  catch (e) { saved = {}; }
  let range = { preset: saved.preset || "all",
                from: saved.from || null, to: saved.to || null };
  if (!PRESETS.some(([k]) => k === range.preset)) range = { preset: "all", from: null, to: null };
  const bounds = () => {
    const pr = PRESETS.find(([k]) => k === range.preset);
    if (pr && pr[2]) return pr[2]();
    return [range.from, range.to];             // custom
  };
  const isAll = () => { const [f, t] = bounds(); return !f && !t; };
  const inRange = (x) => { const [f, t] = bounds();
    return (!f || x.m >= f) && (!t || x.m <= t); };
  const rangeLabel = () => {
    if (isAll()) return "all time";
    const [f, t] = bounds();
    return f === t ? f : `${f || "…"} → ${t || "…"}`;
  };
  const R = () => rows.filter(inRange);        // the scoped ledger
  const sumB = (bs, rs) => rs.reduce((s, x) => bs.includes(x.b) ? s + x.u : s, 0);
  const round2 = (n) => Math.round(n * 100) / 100;

  // live parity check: client sums over ALL rows must equal the audited server
  // figures to the cent — the traceability contract, enforced in the UI.
  const parityOk =
    Math.abs(round2(sumB(["revenue", "refund"], rows)) - (p.net_revenue_usd || 0)) < 0.005
    && Math.abs(round2(sumB(OPERATING_ALL, rows)) - (p.operating_profit_usd || 0)) < 0.005;
  if (!parityOk) console.warn("MONEY PARITY MISMATCH: client row sums != server pnl");

  view.appendChild(el("h2", "section",
    `Money <span class="hint muted">— every number is clickable; figures match`
    + ` bin/finance-pnl · ledger through ${esc(d.as_of || "")}</span>`
    + (parityOk ? "" : ' <span class="pill fail">⚠ parity mismatch — trust bin/finance-pnl</span>')));

  // ── source freshness (doctor GREEN != the ledger is up to date) ──
  // A GREEN finance-doctor proves the ledger is internally consistent; it says
  // nothing about whether anything has been ADDED lately. A dead sync or an
  // unfed statement import leaves these figures self-consistent and months old.
  const mh = d.source_health;
  if (mh && !mh.ok) {
    const lvl3 = (mh.escalation || 0) >= 3;
    const banner = el("div", "srcbanner" + (lvl3 ? " bad" : ""));
    banner.innerHTML = `<b>${lvl3 ? "⚠ Money data is not current" : "Money data may be stale"}</b>`
      + ` — ${esc(mh.label)} last produced `
      + (mh.age_hours == null ? "never"
         : mh.age_hours < 48 ? `${Math.round(mh.age_hours)}h ago`
         : `${Math.round(mh.age_hours / 24)} days ago`)
      + (mh.last_success ? ` (${esc(String(mh.last_success).slice(0, 10))})` : "")
      + `. The figures below are internally consistent but may be missing`
      + ` everything since then.`;
    view.appendChild(banner);
  }

  // ── range bar ──
  const bar = el("div", "rangebar");
  const seg = el("div", "rangeseg");
  const fromIn = el("input"), toIn = el("input");
  fromIn.type = toIn.type = "month";
  fromIn.title = "from month"; toIn.title = "to month";
  const custom = el("span", "rangecustom");
  custom.append(fromIn, el("span", "muted small", "→"), toIn);
  const drawSeg = () => {
    seg.innerHTML = "";
    PRESETS.forEach(([key, label]) => {
      const b = el("button", "rbtn" + (range.preset === key ? " on" : ""), label);
      b.onclick = () => {
        range.preset = key;
        if (key === "custom") {
          range.from = fromIn.value || range.from || lastM;
          range.to = toIn.value || range.to || thisM;
        }
        rerender();
      };
      seg.appendChild(b);
    });
    custom.style.display = range.preset === "custom" ? "inline-flex" : "none";
    fromIn.value = range.from || ""; toIn.value = range.to || "";
  };
  fromIn.onchange = () => { range.preset = "custom"; range.from = fromIn.value || null; rerender(); };
  toIn.onchange = () => { range.preset = "custom"; range.to = toIn.value || null; rerender(); };
  bar.append(seg, custom);
  view.appendChild(bar);

  // ── Import data (upload -> stage -> dry-run -> explicit commit) ──
  // The cockpit never writes the ledger itself: uploads stage into
  // finance-inbox/, imports run the same audited importer bins
  // (reconcile-or-abort, idempotent), and commit is a second explicit click.
  // Card statements stage only — the card importer is still pending.
  const KINDS = [
    ["idfc", "Bank statement", "IDFC xlsx", ".xlsx"],
    ["wise", "Wise", "account CSV", ".csv"],
    ["upwork-personal", "Upwork personal", "full-history CSV", ".csv"],
    ["upwork-agency", "Upwork agency", "full-history CSV", ".csv"],
    ["card", "Credit card", "IDFC Mayura PDF", ".pdf"],
  ];
  const impWrap = el("details", "card importcard");
  impWrap.appendChild(el("summary", null,
    `<b>Import data</b> <span class="muted small">— upload bank / card / Wise /`
    + ` Upwork exports; dry-run first, commit is a second click</span>`));
  const tiles = el("div", "imptiles");
  const impOut = el("div", "impout");
  KINDS.forEach(([kind, label, hint, accept]) => {
    const t = el("div", "imptile");
    t.appendChild(el("div", null,
      `<b>${label}</b><br><span class="muted small">${hint}</span>`));
    const inp = el("input");
    inp.type = "file"; inp.accept = accept; inp.style.display = "none";
    const btn = el("button", "action", "Upload");
    btn.onclick = () => inp.click();
    inp.onchange = () => {
      if (inp.files[0]) doUpload(kind, inp.files[0], btn);
      inp.value = "";
    };
    t.append(inp, btn);
    tiles.appendChild(t);
  });
  impWrap.append(tiles, impOut);
  view.appendChild(impWrap);

  function reportChips(rep) {
    const keep = Object.entries(rep || {}).filter(([k, v]) =>
      typeof v !== "object" || v == null).slice(0, 12);
    return keep.map(([k, v]) =>
      `<span class="chip">${esc(k)}: <b>${esc(String(v))}</b></span>`).join(" ");
  }

  async function doUpload(kind, file, btn) {
    btn.disabled = true; btn.textContent = "Uploading…";
    impOut.innerHTML = "";
    try {
      const r = await fetch(`/api/finance/upload?kind=${encodeURIComponent(kind)}`
        + `&filename=${encodeURIComponent(file.name)}`,
        { method: "POST", headers: { "X-Cockpit-Token": bootToken() }, body: file });
      const up = await r.json();
      if (!up.ok) {
        impOut.appendChild(el("div", "errstate", esc(up.error || "upload failed")));
        return;
      }
      impOut.appendChild(el("div", "muted small",
        `staged <code>${esc(up.staged_as)}</code> in <code>${esc(up.dir)}</code>`
        + (up.note ? ` — ${esc(up.note)}` : "")));
      if (!up.importable) return;
      await doImport(kind, false);              // auto dry-run after staging
    } catch (e) {
      impOut.appendChild(el("div", "errstate",
        "upload failed: " + esc(String(e.message || e))));
    } finally { btn.disabled = false; btn.textContent = "Upload"; }
  }

  async function doImport(kind, commit) {
    impOut.appendChild(el("div", "muted small", commit ? "Committing…" : "Dry-run…"));
    const r = await fetch("/api/finance/import", { method: "POST",
      headers: { "X-Cockpit-Token": bootToken(), "Content-Type": "application/json" },
      body: JSON.stringify({ kind, commit }) });
    const res = await r.json();
    if (!res.ok) {
      impOut.appendChild(el("div", "errstate", esc(res.error || "import failed")));
      return;
    }
    const box = el("div", "impreport");
    box.appendChild(el("div", null,
      `<b>${commit ? "COMMITTED" : "DRY-RUN"}</b> · ${esc(kind)} ${reportChips(res.report)}`));
    if (res.report && res.report.abort) {
      box.appendChild(el("div", "errstate", "importer refused: " + esc(res.report.abort)));
    } else if (!commit) {
      const c = el("button", "action primary", "Commit to ledger");
      c.onclick = () => doImport(kind, true);
      box.appendChild(c);
    } else {
      box.appendChild(el("div", null,
        `doctor: <span class="pill ${res.doctor === "GREEN" ? "ok" : "fail"}">`
        + `${esc(res.doctor || "?")}</span>`
        + (res.reds ? ` <span class="errstate">${esc(res.reds.join(", "))}</span>` : "")));
      box.appendChild(el("div", "muted small", "refreshing figures…"));
      setTimeout(() => show("Money"), 1200);    // re-pull the audited numbers
    }
    impOut.appendChild(box);
  }

  const body = el("div");
  view.appendChild(body);
  body.addEventListener("keydown", (e) => {
    if (e.key === "Escape") body.querySelectorAll(".drill.open")
      .forEach((h) => h.classList.remove("open"));
  });

  function persist(extra) {
    let cur = {};
    try { cur = JSON.parse(localStorage.getItem(MONEY_FILTERS_KEY) || "{}"); }
    catch (e) { cur = {}; }
    localStorage.setItem(MONEY_FILTERS_KEY, JSON.stringify(
      Object.assign(cur, { preset: range.preset, from: range.from, to: range.to }, extra || {})));
  }

  function rerender() {
    persist();
    drawSeg();
    body.innerHTML = "";
    const scoped = R();
    const rl = rangeLabel();
    const rowsFor = (pred) => scoped.filter(pred);

    // scoped headline figures: server-anchored on all-time, row-sums otherwise
    const netRev = isAll() ? (p.net_revenue_usd || 0)
      : round2(sumB(["revenue", "refund"], scoped));
    const opProfit = isAll() ? (p.operating_profit_usd || 0)
      : round2(sumB(OPERATING_ALL, scoped));
    const bucketSum = (b) => isAll() ? (srvBuckets[b] || 0)
      : round2(sumB([b], scoped));

    // ── KPI row ──
    const kpis = el("div", "kpis");
    const revDrill = el("div", "drill");
    const wfDrill = el("div", "drill");
    const mkKpi = (lbl, val, sub, act) => {
      const c = el("button", "kpi");
      c.appendChild(el("div", "lbl", lbl));
      c.appendChild(el("div", "val", val));
      c.appendChild(el("div", "sub2", sub));
      c.appendChild(el("div", "tag", "details →"));
      c.onclick = act;
      return c;
    };
    kpis.appendChild(mkKpi("Cash position", fmtUsd0(cash.value_usd),
      esc(cash.note || "") + (isAll() ? "" :
        ' · <span class="muted">point-in-time, not range-scoped</span>'),
      () => drillCash()));
    kpis.appendChild(mkKpi("Net revenue", fmtUsd0(netRev),
      `${esc(rl)} · revenue + refunds`,
      () => openDrill(wfDrill, `Net revenue (${rl})`,
        rowsFor((x) => x.b === "revenue" || x.b === "refund"))));
    kpis.appendChild(mkKpi("Operating profit", fmtUsd0(opProfit),
      `${esc(rl)} · after all costs`,
      () => wfCard.scrollIntoView({ behavior: "smooth", block: "center" })));
    const delta = revThis - revLast;
    kpis.appendChild(mkKpi(`This month (${esc(thisM || "")})`, fmtUsd0(revThis),
      `<span class="${delta >= 0 ? "up" : "down"}">${delta >= 0 ? "▲" : "▼"} `
      + `${fmtUsd0(Math.abs(delta))}</span> vs ${fmtUsd0(revLast)} last month`,
      () => { selectBar(thisM); drillMonth(thisM); }));
    body.appendChild(kpis);

    // ── Revenue by month (13 bars; out-of-range bars dim; click drills) ──
    body.appendChild(el("h2", "section",
      `Revenue by month <span class="hint muted">— hover for detail, click a bar`
      + ` for its transactions</span>`));
    const chartCard = el("div", "card");
    const chart = el("div", "mchart");
    chartCard.appendChild(chart);
    chartCard.appendChild(revDrill);
    body.appendChild(chartCard);
    const tip = moneyTip();
    const maxRev = Math.max(...byMonth.map((m) => m.usd), 1);
    const revRows = (m) => rows.filter((x) => x.b === "revenue" && x.m === m);
    const drillMonth = (m) => openDrill(revDrill, `Revenue — ${m}`, revRows(m));
    const selectBar = (m) => chart.querySelectorAll(".mcol").forEach((x) =>
      x.classList.toggle("sel", x.dataset.m === m));
    byMonth.forEach((mrow) => {
      const m = mrow.month, v = mrow.usd;
      const dim = !inRange({ m }) ? " dimmed" : "";
      const col = el("button", "mcol" + (mrow.current ? " cur" : "") + dim);
      col.dataset.m = m;
      const barEl = el("div", "mbar");
      barEl.style.height = (100 * v / maxRev) + "%";
      col.appendChild(barEl);
      col.appendChild(el("div", "xlbl" + (mrow.current ? " cur" : ""), m.slice(2)));
      col.onmousemove = (e) => {
        const mr = revRows(m);
        const bySrc = {};
        mr.forEach((x) => { bySrc[x.s] = (bySrc[x.s] || 0) + x.u; });
        const top = Object.entries(bySrc).sort((a, b) => b[1] - a[1])[0];
        tip.style.opacity = 1;
        tip.style.left = Math.min(e.clientX + 14, innerWidth - 250) + "px";
        tip.style.top = (e.clientY - 10) + "px";
        tip.innerHTML = `<b>${m}</b> · ${fmtUsd2(v)}<br>${mr.length} transaction`
          + `${mr.length === 1 ? "" : "s"}`
          + (top ? `<br>top: ${esc(top[0])} ${fmtUsd0(top[1])}` : "")
          + `<br><span style="opacity:.75">click to drill down</span>`;
      };
      col.onmouseleave = () => { tip.style.opacity = 0; };
      col.onclick = () => { selectBar(m); drillMonth(m); };
      chart.appendChild(col);
    });
    if (!byMonth.length) chart.appendChild(el("div", "emptystate", "No revenue months yet."));

    // ── P&L by month (within the range; cells drill) ──
    body.appendChild(el("h2", "section",
      `P&amp;L by month <span class="hint muted">— ${esc(rl)} · click a cell`
      + ` for its transactions</span>`));
    const pnlmCard = el("div", "card");
    const pnlmDrill = el("div", "drill");
    const monthsAll = [...new Set(scoped.filter((x) => OPERATING_ALL.includes(x.b))
      .map((x) => x.m))].sort().reverse().slice(0, isAll() ? 13 : 24);
    const sumWhere = (pred) => scoped.reduce((s, x) => pred(x) ? s + x.u : s, 0);
    const pnlmWrap = el("div", "tablewrap");
    const pnlmTable = el("table");
    pnlmTable.innerHTML = "<tr><th>Month</th><th class='num'>Revenue</th>"
      + "<th class='num'>Expenses</th><th class='num'>Profit</th></tr>";
    monthsAll.forEach((m) => {
      const rev = sumWhere((x) => x.m === m && (x.b === "revenue" || x.b === "refund"));
      const cost = sumWhere((x) => x.m === m && OPERATING_COSTS.includes(x.b));
      const profit = rev + cost;
      const tr = el("tr");
      const tdM = el("td", null,
        `${esc(m)}${m === thisM ? ' <span class="chip">current</span>' : ""}`);
      const tdR = el("td", "num pos drillable", fmtUsd2(rev));
      const tdC = el("td", "num neg drillable", fmtUsd2(cost));
      const tdP = el("td", `num ${profit >= 0 ? "pos" : "neg"} drillable`,
        `<b>${fmtUsd2(profit)}</b>`);
      tdR.onclick = () => openDrill(pnlmDrill, `Revenue — ${m}`,
        rowsFor((x) => x.m === m && (x.b === "revenue" || x.b === "refund")));
      tdC.onclick = () => openDrill(pnlmDrill, `Expenses — ${m}`,
        rowsFor((x) => x.m === m && OPERATING_COSTS.includes(x.b)),
        { sortAmount: true });
      tdP.onclick = () => openDrill(pnlmDrill, `P&L — ${m} (all operating rows)`,
        rowsFor((x) => x.m === m && OPERATING_ALL.includes(x.b)));
      tr.append(tdM, tdR, tdC, tdP);
      pnlmTable.appendChild(tr);
    });
    const totRev = sumWhere((x) => x.b === "revenue" || x.b === "refund");
    const totCost = sumWhere((x) => OPERATING_COSTS.includes(x.b));
    const trAll = el("tr");
    trAll.style.fontWeight = "700";
    trAll.innerHTML = `<td>${isAll() ? "All-time" : "Range total"}</td>`
      + `<td class="num pos">${fmtUsd2(totRev)}</td>`
      + `<td class="num neg">${fmtUsd2(totCost)}</td>`
      + `<td class="num ${totRev + totCost >= 0 ? "pos" : "neg"}">${fmtUsd2(totRev + totCost)}</td>`;
    pnlmTable.appendChild(trAll);
    pnlmWrap.appendChild(pnlmTable);
    pnlmCard.appendChild(pnlmWrap);
    if (isAll() && monthsAll.length === 13) pnlmCard.appendChild(
      el("div", "muted small", "showing the last 13 months — pick a range above for more"));
    pnlmCard.appendChild(pnlmDrill);
    body.appendChild(pnlmCard);

    // ── P&L waterfall (scoped; each line drills; owner draws below) ──
    body.appendChild(el("h2", "section",
      `Profit &amp; Loss <span class="hint muted">— ${esc(rl)} · click any line`
      + ` for its transactions</span>`));
    const wfCard = el("div", "card");
    const wf = el("div", "wf");
    const wfLines = [["revenue", "Revenue"], ["refund", "Refunds"],
      ["platform_fee", "Platform fees (Upwork/Wise)"], ["tax", "Taxes (WHT/GST)"],
      ["payroll", "Payroll"], ["opex", "Operating expenses"]];
    const wfMax = Math.max(...wfLines.map(([b]) => Math.abs(bucketSum(b))), 1);
    wfLines.forEach(([b, label]) => {
      const v = bucketSum(b);
      const row = el("button", "wfrow");
      row.appendChild(el("div", "n", label));
      const bl = el("div", "bl");
      const sp = el("span");
      sp.style.width = (100 * Math.abs(v) / wfMax) + "%";
      sp.style.left = "0";
      sp.style.background = v >= 0 ? "var(--green)" : "var(--red)";
      bl.appendChild(sp);
      row.appendChild(bl);
      row.appendChild(el("div", "amt " + (v >= 0 ? "pos" : "neg"), fmtUsd2(v)));
      row.onclick = () => openDrill(wfDrill, `${BLABEL[b] || b} (${rl})`,
        rowsFor((x) => x.b === b), { sortAmount: v < 0 });
      wf.appendChild(row);
    });
    const tot = el("div", "wfrow total");
    tot.appendChild(el("div", "n", "Operating profit"));
    tot.appendChild(el("div", "bl"));
    tot.appendChild(el("div", "amt " + (opProfit >= 0 ? "pos" : "neg"),
      fmtUsd2(opProfit)));
    wf.appendChild(tot);
    const odV = bucketSum("owner_draw");
    const od = el("button", "wfrow");
    od.appendChild(el("div", "n",
      `<span class="muted">Owner draws (below the line — equity, not a cost)</span>`));
    od.appendChild(el("div", "bl"));
    od.appendChild(el("div", "amt neg", fmtUsd2(odV)));
    od.onclick = () => openDrill(wfDrill, `Owner draws (${rl})`,
      rowsFor((x) => x.b === "owner_draw"), { sortAmount: true });
    wf.appendChild(od);
    wfCard.appendChild(wf);
    wfCard.appendChild(el("div", "muted small",
      "Transfers, sweeps and card settlements are excluded — non-economic"
      + " (money moving between our own accounts)."));
    wfCard.appendChild(wfDrill);
    body.appendChild(wfCard);

    // ── Cash position (point-in-time; never range-scoped) ──
    body.appendChild(el("h2", "section",
      `Cash position <span class="hint muted">— point-in-time balances,`
      + ` not affected by the range</span>`));
    const cashCard = el("div", "card");
    (cash.by_account || []).forEach((a) => {
      const row = el("div", "cashrow");
      row.appendChild(el("div", null,
        `<strong>${esc(a.account)}</strong> <span class="muted">${esc(a.currency)}</span>`));
      const local = a.currency === "INR"
        ? "₹" + Number(a.closing).toLocaleString("en-IN", { minimumFractionDigits: 2 })
        : fmtUsd2(Number(a.closing));
      row.appendChild(el("div", "num", `${local} <span class="muted">→ ${fmtUsd2(a.usd)}</span>`));
      cashCard.appendChild(row);
    });
    const ctot = el("div", "cashrow");
    ctot.style.fontWeight = "700";
    ctot.appendChild(el("div", null, "Total (base USD)"));
    ctot.appendChild(el("div", "num", fmtUsd2(cash.value_usd)));
    cashCard.appendChild(ctot);
    cashCard.appendChild(el("div", "muted small",
      `INR→USD at ${esc(cash.inr_usd_rate || "?")} (pinned) · FD = fixed deposit`
      + ` · as of ${esc(cash.as_of || "")}`));
    body.appendChild(cashCard);

    function drillCash() {
      openDrill(wfDrill, "Cash accounts", []);
      wfDrill.querySelector(".emptystate")?.remove();
      const wrap = el("div", "tablewrap");
      const t = el("table");
      t.innerHTML = "<tr><th>Account</th><th>Currency</th><th class='num'>Local</th>"
        + "<th class='num'>USD</th></tr>";
      (cash.by_account || []).forEach((a) => {
        const tr = el("tr");
        tr.innerHTML = `<td><strong>${esc(a.account)}</strong></td><td>${esc(a.currency)}</td>`
          + `<td class="num">${Number(a.closing).toLocaleString()}</td>`
          + `<td class="num pos">${fmtUsd2(a.usd)}</td>`;
        t.appendChild(tr);
      });
      wrap.appendChild(t);
      wfDrill.appendChild(wrap);
      wfDrill.querySelector(".count").textContent =
        `— ${(cash.by_account || []).length} accounts · total ${fmtUsd2(cash.value_usd)}`;
    }

    // ── Transaction explorer (range pre-applied) ──
    body.appendChild(el("h2", "section",
      `Transaction explorer <span class="hint muted">— ${esc(rl)} · filter &amp;`
      + ` search every reconciled transaction</span>`));
    const expCard = el("div", "card");
    const filters = el("div", "filters");
    const fMonth = el("select"), fBucket = el("select"), fSource = el("select");
    const fSearch = el("input");
    fSearch.type = "search";
    fSearch.placeholder = "search description…";
    const expCount = el("span", "muted small");
    fMonth.appendChild(new Option("All months", ""));
    [...new Set(scoped.map((t) => t.m))].sort().reverse()
      .forEach((m) => fMonth.appendChild(new Option(m, m)));
    fBucket.appendChild(new Option("All buckets", ""));
    [...new Set(scoped.map((t) => t.b))].sort()
      .forEach((b) => fBucket.appendChild(new Option(BLABEL[b] || b, b)));
    fSource.appendChild(new Option("All sources", ""));
    [...new Set(scoped.map((t) => t.s))].sort()
      .forEach((s) => fSource.appendChild(new Option(s || "—", s)));
    filters.append(fMonth, fBucket, fSource, fSearch, expCount);
    const expWrap = el("div", "tablewrap");
    expWrap.style.maxHeight = "460px";
    expWrap.style.overflow = "auto";
    const expTable = el("table");
    expWrap.appendChild(expTable);
    expCard.append(filters, expWrap);
    body.appendChild(expCard);

    if (saved.m && [...fMonth.options].some((o) => o.value === saved.m)) fMonth.value = saved.m;
    if (saved.b) fBucket.value = saved.b;
    if (saved.s) fSource.value = saved.s;
    if (saved.q) fSearch.value = saved.q;

    let sortKey = "dt", sortDir = -1;
    function renderExp() {
      persist({ m: fMonth.value, b: fBucket.value, s: fSource.value, q: fSearch.value });
      const needle = fSearch.value.toLowerCase();
      let out = scoped.filter((t) =>
        (!fMonth.value || t.m === fMonth.value)
        && (!fBucket.value || t.b === fBucket.value)
        && (!fSource.value || t.s === fSource.value)
        && (!needle || (t.d + " " + t.t).toLowerCase().includes(needle)));
      out.sort((a, b) => {
        const x = a[sortKey], y = b[sortKey];
        if (sortKey === "u") return (x - y) * sortDir;
        return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
      });
      const total = out.reduce((s, t) => s + t.u, 0);
      expCount.innerHTML = `${out.length} of ${scoped.length} · sum <b>${fmtUsd2(total)}</b>`;
      expTable.innerHTML = "";
      const head = el("tr");
      [["dt", "Date"], ["b", "Bucket"], ["t", "Type"], ["s", "Source"],
       ["d", "Description"], ["u", "Amount"]].forEach(([k, l]) => {
        const th = el("th", "sortable" + (k === "u" ? " num" : ""),
          l + (sortKey === k ? (sortDir < 0 ? " ▼" : " ▲") : ""));
        th.onclick = () => {
          if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = -1; }
          renderExp();
        };
        head.appendChild(th);
      });
      expTable.appendChild(head);
      out.slice(0, 600).forEach((x) => {
        const tr = el("tr");
        tr.innerHTML = `<td class="muted">${esc(x.dt)}</td>`
          + `<td><span class="chip ${esc(x.b)}">${esc(BLABEL[x.b] || x.b)}</span></td>`
          + `<td>${esc(x.t)}</td><td class="muted">${esc(x.s)}</td><td>${esc(x.d)}</td>`
          + `<td class="num ${x.u >= 0 ? "pos" : "neg"}">${fmtUsd2(x.u)}</td>`;
        expTable.appendChild(tr);
      });
      if (out.length > 600) expTable.appendChild(el("tr", null,
        `<td colspan=6 class="muted small">showing first 600 of ${out.length}`
        + ` — narrow the filter to see the rest</td>`));
    }
    [fMonth, fBucket, fSource].forEach((s) => { s.onchange = renderExp; });
    fSearch.oninput = renderExp;
    renderExp();

    body.appendChild(el("div", "foot",
      `Ledger through <b>${esc(d.as_of || "")}</b> (Upwork pre-dates upcoming invoices) · ${rows.length} reconciled transactions`
      + ` (${scoped.length} in range) · computed from the same audited`
      + ` <code>pnl.compute</code> buckets as <code>bin/finance-pnl</code>, gated on`
      + ` finance-doctor GREEN. Net revenue = revenue + refunds; operating profit is`
      + ` after platform fees, taxes, payroll and opex; owner draws are equity (below`
      + ` the line); transfers/sweeps/card-settlement are excluded (non-economic).`
      + ` Cash is point-in-time and not range-scoped.`));
  }

  rerender();
}

function factLine(x, showClient) {
  const trusted = x.confidence === "CONFIRMED" || x.confidence === "CROSS-VERIFIED";
  const who = showClient && x.client_id ? `<span class="pill">${esc(x.client_id)}</span> ` : "";
  const tag = `<span class="pill ${trusted ? "ok" : ""}">${esc(x.confidence)}</span>`;
  return el("div", "kb-fact", `${who}${esc(x.fact_text)} ${tag}`
    + `<div class="kb-cite">${esc(x.citation)}</div>`);
}

async function openDossier(clientId, host) {
  host.innerHTML = "";
  const d = await api("/api/knowledge?client=" + encodeURIComponent(clientId));
  const m = d.meta || {};
  host.appendChild(el("h3", "section", esc(m.client_name || clientId)
    + ` — ${(d.facts || []).length} client facts`));
  const byCat = {};
  (d.facts || []).forEach((x) => { (byCat[x.category] = byCat[x.category] || []).push(x); });
  Object.keys(byCat).forEach((cat) => {
    host.appendChild(el("h4", null, esc(cat)));
    byCat[cat].forEach((x) => host.appendChild(factLine(x, false)));
  });
  host.appendChild(el("div", "muted", `Sources: ${(d.sources || []).map((s) => esc(s.source_ref)).join(" · ")}`));
  host.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function toast(msg, isError) {
  let host = document.getElementById("toasts");
  if (!host) { host = el("div", "toasts"); host.id = "toasts"; document.body.appendChild(host); }
  const t = el("div", "toast" + (isError ? " toast-err" : ""), msg);
  host.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 300); }, 3800);
}

async function kick(path, body, btn) {
  const name = btn.textContent;
  toast(`${name} started — Jobs will update shortly`);
  btn.disabled = true; btn.textContent = "Running…";
  let ok = false;
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "X-Cockpit-Token": bootToken(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    ok = !!d.ok;
    btn.textContent = ok ? "Started ✓" : "Failed";
    if (!ok) toast(`${name} failed: ${d.error || "see logs"}`, true);
  } catch (e) { btn.textContent = "Failed"; toast(`${name} failed to start`, true); }
  if (ok) {
    // the job runs async — refresh the Jobs ledger a few seconds later so
    // the new run shows without a manual reload
    setTimeout(() => {
      if (currentSection === "System") { toast("Jobs ledger refreshed"); show("System"); }
    }, 4500);
  } else {
    setTimeout(() => { btn.textContent = name; btn.disabled = false; }, 2500);
  }
}

// Small categorical bar chart (inline — full control over per-bar emphasis
// and a labeled target line, which uPlot's single-fill bars make awkward).
// items: [{label, v, current?}]; opts: {money?, target?, targetLabel?}
function drawBarChart(box, items, opts) {
  opts = opts || {};
  if (!items.length) { box.innerHTML = '<div class="muted">No data yet.</div>'; return; }
  const fmt = (v) => opts.money ? fmtUsd(v) : String(v);
  const max = Math.max(...items.map((m) => m.v), opts.target || 0, 1) * 1.12;
  const bars = items.map((m) => {
    const h = Math.round(100 * m.v / max);
    const cls = "bc-bar" + (m.current ? " bc-current" : "");
    return `<div class="bc-col" title="${m.label}: ${fmt(m.v)}">`
      + `<div class="${cls}" style="height:${h}%"></div></div>`;
  }).join("");
  const labels = items.map((m) =>
    `<div class="bc-lbl${m.current ? " bc-lbl-current" : ""}">${m.label}</div>`).join("");
  let target = "";
  if (opts.target) {
    const bottom = Math.round(100 * opts.target / max);
    target = `<div class="bc-target" style="bottom:${bottom}%">`
      + `<span>${opts.targetLabel || ("target " + fmt(opts.target))}</span></div>`;
  }
  box.innerHTML = `<div class="bc"><div class="bc-plot">${target}`
    + `<div class="bc-bars">${bars}</div></div>`
    + `<div class="bc-labels">${labels}</div></div>`;
}

function card(title, html) {
  const c = el("div", "card");
  c.appendChild(el("h3", null, title));
  c.appendChild(el("div", null, html));
  return c;
}

async function show(section) {
  currentSection = section;
  renderNav(section);
  // deep-linkable sections + remember the last one
  const want = "#" + section.toLowerCase();
  if (location.hash !== want) history.replaceState({}, "", want);
  localStorage.setItem(LAST_SECTION_KEY, section);
  document.getElementById("nav").classList.remove("open");
  const view = document.getElementById("view");
  view.innerHTML = "";
  view.appendChild(el("div", "loading", "Loading…"));
  const r = RENDERERS[section];
  try {
    const stage = el("div");
    if (r) await r(stage);
    else { stage.appendChild(el("h2", "section", section)); placeholder(stage, section + " — wiring in a later phase."); }
    view.innerHTML = "";
    view.appendChild(stage);
  } catch (e) {
    view.innerHTML = "";
    if (e && e.unauthorized) { renderUnlock(view, !!localStorage.getItem(TOKEN_KEY)); return; }
    view.appendChild(el("div", "errstate", "This section failed to load: "
      + esc(String(e.message || e)) + " — retry from the nav."));
  }
}

function sectionFromHash() {
  const h = (location.hash || "").replace("#", "").toLowerCase();
  return SECTIONS.find((s) => s.toLowerCase() === h) || null;
}

// ── boot ──
initTheme();
document.getElementById("env").textContent = location.host;
document.getElementById("themeToggle").onclick = toggleTheme;
document.getElementById("navToggle").onclick = () =>
  document.getElementById("nav").classList.toggle("open");
window.addEventListener("hashchange", () => {
  const s = sectionFromHash();
  if (s && s !== currentSection) show(s);
});
refreshHealth();
setInterval(refreshHealth, 5 * 60 * 1000);
show(sectionFromHash() || localStorage.getItem(LAST_SECTION_KEY) || "Today");
