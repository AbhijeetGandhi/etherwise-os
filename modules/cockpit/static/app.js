"use strict";
// Etherwise cockpit SPA. Token: taken from ?token= on first load, stored in
// localStorage, stripped from the URL; sent as X-Cockpit-Token on every API
// call. No send actions anywhere (drafts-only).

const SECTIONS = ["Today", "Pipeline", "Money", "Clients", "Knowledge", "System"];
const TOKEN_KEY = "ew_cockpit_token";

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
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

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
  },
  Today: async (view) => {
    const d = await api("/api/today");
    const m = d.metrics || {};
    view.appendChild(el("h2", "section", "Today"));

    // ── Actions (top) ──
    const fu = el("div", "card");
    fu.appendChild(el("h3", null, `Follow-ups due (${(d.follow_ups||[]).length})`));
    if (!(d.follow_ups || []).length) fu.appendChild(el("div", "muted", "Nothing owed or due. ✓"));
    (d.follow_ups || []).forEach((f) => {
      const item = el("div", "row");
      const left = el("div");
      left.innerHTML = `<strong>${f.topic || f.thread_id}</strong> `
        + `<span class="pill ${f.bucket}">${f.bucket}</span> `
        + `<span class="muted">${f.tier || ""} · ${f.word_count||0}w</span>`;
      const btns = el("div");
      const copy = el("button", "action", "Copy draft");
      copy.onclick = () => { navigator.clipboard.writeText(f.draft || ""); copy.textContent = "Copied"; setTimeout(() => copy.textContent = "Copy draft", 1200); };
      const open = el("button", "action", "Open thread");
      open.onclick = () => window.open(f.thread_url, "_blank");
      const done = el("button", "action primary", "Done");
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
      + `<div class="muted">${wr.won||0} won of ${wr.decided||0} decided</div>`));
    grid.appendChild(card("Score bands", `<div class="metric">${b.hot||0} <small>hot</small></div>`
      + `<div class="muted">${b.standard||0} standard · ${b.low||0} low</div>`));
    grid.appendChild(card("To triage", `<div class="metric">${d.to_triage||0}</div>`
      + `<div class="muted">untasked hot leads (all-time backlog)</div>`));
    view.appendChild(grid);

    view.appendChild(el("h2", "section", "Applied per week"));
    const chartBox = el("div", "card");
    const chart = el("div", "chart");
    chartBox.appendChild(chart);
    view.appendChild(chartBox);
    drawBarChart(chart, (d.applied_trend || []).map(t => ({ label: (t.week||"").slice(2), v: t.count })));

    view.appendChild(el("h2", "section", "Proposals by status"));
    const t = el("table");
    t.innerHTML = "<tr><th>Status</th><th>Count</th></tr>";
    Object.entries(d.by_status || {}).sort((x, y) => y[1] - x[1]).forEach(([s, n]) => {
      const tr = el("tr"); tr.innerHTML = `<td>${s}</td><td>${n}</td>`; t.appendChild(tr);
    });
    view.appendChild(t);
  },
  Money: async (view) => {
    const d = await api("/api/money");
    const r = d.revenue || {};
    view.appendChild(el("h2", "section", "Money"));
    const grid = el("div", "grid");
    const pct = Math.min(100, r.pct_to_target || 0);
    grid.appendChild(card("Revenue this month",
      `<div class="metric">${fmtUsd(r.month_usd)} <small>/ ${fmtUsd(r.target_usd)}</small></div>`
      + `<div class="bar"><span style="width:${pct}%"></span></div>`
      + `<div class="muted">${r.pct_to_target ?? 0}% to target · ${r.month}</div>`));
    const delta = (r.month_usd || 0) - (r.last_month_usd || 0);
    grid.appendChild(card("Last month",
      `<div class="metric">${fmtUsd(r.last_month_usd)}</div>`
      + `<div class="muted">${delta >= 0 ? "+" : ""}${fmtUsd(delta)} vs this month-to-date</div>`));
    const c = d.connects || {};
    grid.appendChild(card("Connects spend",
      `<div class="metric">${fmtUsd(c.this_month_usd)} <small>this month</small></div>`
      + `<div class="muted">${fmtUsd(c.lifetime_usd)} lifetime</div>`));
    const cash = d.cash || {};
    grid.appendChild(card("Cash position",
      `<div class="metric muted">—</div>`
      + `<div class="muted" style="font-size:12px">${cash.note || ""}</div>`));
    view.appendChild(grid);

    view.appendChild(el("h2", "section", "Revenue by month"));
    const chartBox = el("div", "card");
    const chart = el("div", "chart");
    chartBox.appendChild(chart);
    view.appendChild(chartBox);
    drawBarChart(chart, (r.by_month || []).map((m) => ({ label: m.month.slice(2), v: m.usd })), { money: true });

    view.appendChild(el("h2", "section", "Recent transactions"));
    const t = el("table");
    t.innerHTML = "<tr><th>Date</th><th>Type</th><th>Amount</th><th>Profile</th></tr>";
    (d.transactions || []).forEach((x) => {
      const tr = el("tr");
      const amt = (x.amount >= 0 ? "" : "−") + fmtUsd(Math.abs(x.amount));
      tr.innerHTML = `<td class="muted">${(x.creation_dt||"").slice(0,10)}</td>`
        + `<td>${x.type || "—"}</td><td>${amt} ${x.currency||""}</td>`
        + `<td class="muted">${x.profile || "—"}</td>`;
      t.appendChild(tr);
    });
    view.appendChild(t);
  },
  Knowledge: async (view) => {
    view.appendChild(el("h2", "section", "Knowledge"));
    placeholder(view, "Ingestion arrives with M3. Fathom poller is staged; nothing to show yet.");
  },
};

function drawBarChart(box, items, opts) {  // items: [{label, v}]; opts.money
  opts = opts || {};
  if (!items.length) { box.innerHTML = '<div class="muted">No data yet.</div>'; return; }
  const labels = items.map((m) => m.label);
  const ys = items.map((m) => m.v);
  const xs = items.map((_, i) => i);
  if (typeof uPlot === "undefined") {  // fallback: inline bars, no dep
    const max = Math.max(...ys, 1);
    box.innerHTML = '<div style="display:flex;align-items:flex-end;gap:6px;height:200px">'
      + items.map((m, i) => `<div title="${m.label}: ${m.v}" style="flex:1;background:var(--accent);opacity:.85;height:${Math.round(100*ys[i]/max)}%"></div>`).join("")
      + "</div>";
    return;
  }
  const cfg = {
    width: box.clientWidth || 820, height: 220,
    scales: { x: { time: false } },
    legend: { show: false },
    axes: [
      { values: (u, s) => s.map((i) => labels[i] || "") },
      { values: (u, s) => s.map((v) => opts.money ? "$" + Math.round(v / 1000) + "k" : String(v)) },
    ],
    series: [{}, {
      label: "Revenue", stroke: "#2f6f6b", fill: "rgba(47,111,107,.18)",
      paths: uPlot.paths.bars({ size: [0.6, 60] }),
    }],
  };
  new uPlot(cfg, [xs, ys], box);
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
  const view = document.getElementById("view");
  view.innerHTML = "";
  const r = RENDERERS[section];
  try {
    if (r) await r(view);
    else { view.appendChild(el("h2", "section", section)); placeholder(view, section + " — wiring in a later phase."); }
  } catch (e) {
    if (e && e.unauthorized) { renderUnlock(view, !!localStorage.getItem(TOKEN_KEY)); return; }
    view.appendChild(el("div", "placeholder", String(e.message || e)));
  }
}

document.getElementById("env").textContent = location.host;
show("Today");
