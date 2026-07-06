"use strict";
// Etherwise cockpit SPA. Token: taken from ?token= on first load, stored in
// localStorage, stripped from the URL; sent as X-Cockpit-Token on every API
// call. No send actions anywhere (drafts-only).

const SECTIONS = ["Today", "Nudges", "Pipeline", "Money", "Clients", "Knowledge", "System"];
const TOKEN_KEY = "ew_cockpit_token";
const THEME_KEY = "ew_cockpit_theme";
const LAST_SECTION_KEY = "ew_cockpit_section";
const MONEY_FILTERS_KEY = "ew_cockpit_money_filters";

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
    const grid = el("div", "grid");
    const dr = d.doctor || {};
    grid.appendChild(card("Doctor",
      `<div class="metric"><span class="pill ${(dr.worst||"").toLowerCase()}">${dr.worst||"?"}</span></div>`
      + `<div class="muted">${(dr.checks||[]).length} checks</div>`));
    const sp = d.spend || {};
    grid.appendChild(card("Spend today",
      `<div class="metric">${fmtUsd(sp.today_usd)} <small>/ ${fmtUsd(sp.soft_limit_usd)} soft</small></div>`
      + `<div class="muted">MTD ${fmtUsd(sp.mtd_usd)}</div>`));
    grid.appendChild(card("Shadow ledger",
      `<div class="metric">${(d.shadow||{}).pending||0} <small>pending intents</small></div>`));
    view.appendChild(grid);
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
    view.appendChild(el("h2", "section", "Today"));

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

    // ── Actions (top) ──
    const fu = el("div", "card");
    fu.appendChild(el("h3", null, `Follow-ups due (${(d.follow_ups||[]).length})`));
    if (!(d.follow_ups || []).length) fu.appendChild(el("div", "muted", "Nothing owed or due. ✓"));
    (d.follow_ups || []).forEach((f) => {
      const item = el("div", "fu-row");
      const left = el("div", "fu-left");
      const title = el("div", "fu-title", f.topic || f.thread_id);  // truncates via CSS
      const meta = el("div", "fu-meta");
      meta.append(pill(f.bucket, f.bucket), pill(f.tier, "tier-" + (f.tier || "").toLowerCase()));
      if (f.age_days != null) meta.appendChild(el("span", "muted", ago(f.age_days)));
      left.append(title, meta);
      const btns = el("div", "fu-actions");
      const copy = el("button", "action", "Copy draft");
      copy.onclick = () => { navigator.clipboard.writeText(f.draft || ""); copy.textContent = "Copied"; setTimeout(() => copy.textContent = "Copy draft", 1200); };
      const open = el("button", "action", "Open thread");
      open.onclick = () => window.open(f.thread_url, "_blank");
      const done = el("button", "action", "Done");  // neutral until clicked
      done.onclick = () => nudge("followup:" + f.thread_id, "done");
      const snooze = el("button", "action", "Snooze 3d");
      snooze.onclick = () => nudge("followup:" + f.thread_id, "snooze", 3);
      const dismiss = el("button", "action", "Dismiss");
      dismiss.onclick = () => nudge("followup:" + f.thread_id, "dismiss");
      btns.append(copy, open, done, snooze, dismiss);
      item.append(left, btns);
      fu.appendChild(item);
    });
    view.appendChild(fu);

    const hl = el("div", "card");
    hl.appendChild(el("h3", null, `Hot leads (${(d.hot_leads||[]).length})`));
    if (!(d.hot_leads || []).length) hl.appendChild(el("div", "muted", "No new hot leads awaiting a proposal."));
    (d.hot_leads || []).forEach((h) => {
      const item = el("div", "row");
      const left = el("div", null, `<strong>${h.title || h.id}</strong> <span class="muted">${h.has_draft ? "· draft ready" : ""}</span>`);
      const right = el("div");
      right.appendChild(el("span", "pill ok", "score " + h.score));
      if (h.job_url) { const o = el("button", "action", "Open job"); o.onclick = () => window.open(h.job_url, "_blank"); right.appendChild(o); }
      item.append(left, right);
      hl.appendChild(item);
    });
    view.appendChild(hl);

    if ((d.proto_nudges || []).length) {
      const pn = el("div", "card");
      pn.appendChild(el("h3", null, "Nudges (proto · real feed with M4)"));
      d.proto_nudges.forEach((n) => pn.appendChild(el("div", "row", `<div>${n.text}</div>`)));
      view.appendChild(pn);
    }

    // ── Metrics strip (below) ──
    view.appendChild(el("h2", "section", "At a glance"));
    const grid = el("div", "grid");
    const a = m.applied || {}, rev = m.revenue || {}, act = m.active || {};
    grid.appendChild(card("Applied", `<div class="metric">${a.today||0} <small>today</small></div>`
      + `<div class="muted">${a.week||0} this week · ${a.last_week||0} last week</div>`));
    const pct = Math.min(100, rev.pct || 0);
    grid.appendChild(card("Revenue MTD", `<div class="metric">${fmtUsd(rev.mtd_usd)} <small>/ ${fmtUsd(rev.target_usd)}</small></div>`
      + `<div class="bar"><span style="width:${pct}%"></span></div>`));
    grid.appendChild(card("Pipeline", `<div class="metric">${act.proposals||0} <small>active</small></div>`
      + `<div class="muted">${act.interviews||0} interviews · ${act.contracts||0} contracts</div>`));
    grid.appendChild(card("Queue", `<div class="metric">${m.follow_ups_due||0} <small>follow-ups</small></div>`
      + `<div class="muted">${m.hot_leads||0} hot leads</div>`));
    view.appendChild(grid);
  },
  Pipeline: async (view) => {
    const d = await api("/api/pipeline");
    view.appendChild(el("h2", "section", "Pipeline"));
    const a = d.applied || {}, wr = d.win_rate || {}, b = d.bands || {};
    const grid = el("div", "grid");
    grid.appendChild(card("Applied", `<div class="metric">${a.today||0} <small>today</small></div>`
      + `<div class="muted">${a.week||0} this week · ${a.last_week||0} last week</div>`));
    grid.appendChild(card("Win rate", `<div class="metric">${wr.pct != null ? wr.pct + "%" : "—"}</div>`
      + `<div class="muted">${wr.won||0} won of ${wr.decided||0} decided</div>`
      + `<div class="muted" style="font-size:11px">${wr.caveat || ""}</div>`));
    grid.appendChild(card("Open board", `<div class="metric">${b.hot||0} <small>hot</small></div>`
      + `<div class="muted">${b.standard||0} standard · ${b.low||0} low</div>`
      + `<div class="muted" style="font-size:11px">${b.note || ""}</div>`));
    grid.appendChild(card("To triage", `<div class="metric">${d.to_triage||0}</div>`
      + `<div class="muted">untasked hot leads to action</div>`));
    view.appendChild(grid);

    view.appendChild(el("h2", "section", "Applied per week"));
    const chartBox = el("div", "card");
    const chart = el("div", "chart");
    chartBox.appendChild(chart);
    view.appendChild(chartBox);
    drawBarChart(chart, (d.applied_trend || []).map(t => ({ label: (t.week || "").slice(5), v: t.count, current: t.current })));

    view.appendChild(el("h2", "section", "Proposals by status"));
    const t = el("table");
    t.innerHTML = "<tr><th>Status</th><th>Count</th></tr>";
    Object.entries(d.by_status || {}).sort((x, y) => y[1] - x[1]).forEach(([s, n]) => {
      const tr = el("tr"); tr.innerHTML = `<td>${s}</td><td>${n}</td>`; t.appendChild(tr);
    });
    view.appendChild(t);
  },
  Money: renderMoney,
  Clients: async (view) => {
    const d = await api("/api/clients");
    view.appendChild(el("h2", "section", `Clients (${d.count || 0})`));
    const sb = d.by_status || {};
    const grid = el("div", "grid");
    const order = ["Active", "Paused", "Lead", "Ended", "Past"];
    Object.keys(sb).sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99))
      .forEach((s) => grid.appendChild(card(s, `<div class="metric">${sb[s]}</div>`)));
    view.appendChild(grid);
    const t = el("table");
    t.innerHTML = "<tr><th>Client</th><th>Status</th><th>Contracts</th><th>Activity</th><th>Tags</th></tr>";
    (d.clients || []).forEach((c) => {
      const tr = el("tr");
      const tags = (c.tags || []).slice(0, 3).map((x) => `<span class="pill">${x}</span>`).join(" ");
      tr.innerHTML = `<td><strong>${c.name}</strong></td>`
        + `<td><span class="pill ${(c.status||"").toLowerCase()==="active"?"ok":""}">${c.status}</span></td>`
        + `<td>${c.contracts}</td><td class="muted">${c.transactions} txns</td>`
        + `<td>${tags}</td>`;
      t.appendChild(tr);
    });
    view.appendChild(t);
    view.appendChild(el("div", "muted", "Source: Airtable Clients · deeper per-client health (owed replies, hours vs cap) arrives with the comms mirror."));
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
    view.appendChild(el("h2", "section", "Knowledge"));
    if (d.status === "stub") { placeholder(view, d.message); return; }

    const t = d.totals || {};
    const strip = el("div", "grid");
    strip.appendChild(card("Facts", `<div class="metric">${t.facts || 0}</div>`
      + `<div class="muted">${t.client_facts || 0} client · ${t.agency_facts || 0} agency · ${t.cross_facts || 0} cross</div>`));
    strip.appendChild(card("Sources", `<div class="metric">${t.sources || 0}</div>`));
    const f = d.fathom || {};
    strip.appendChild(card("Fathom poller",
      `<div class="muted">HWM: ${esc(f.cursor) || "—"}</div>`
      + `<div class="muted">Last poll: ${esc(f.last_poll) || "never"}</div>`));
    const pp = d.proof_points || {};
    strip.appendChild(card("Proof-points",
      `<div class="metric">${(pp.agency || 0) + (pp.cross || 0)}</div>`
      + `<div class="muted">${pp.agency || 0} agency · ${pp.cross || 0} cross-client</div>`));
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

    // Dossier browser
    view.appendChild(el("h2", "section", "Dossiers"));
    const detail = el("div", "kb-detail");
    const grid = el("div", "grid");
    (d.dossiers || []).forEach((x) => {
      const c = card(esc(x.client_name),
        `<div class="metric">${x.fact_count}</div>`
        + `<div class="muted">${x.source_count} sources · compiled ${esc((x.last_compiled_at||"").slice(0,10))}</div>`);
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
    ["card", "Credit card", "stages only — importer pending", ".pdf,.csv,.xlsx"],
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
