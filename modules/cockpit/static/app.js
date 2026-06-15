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
  if (r.status === 401) throw new Error("unauthorized — open via `bin/cockpit open`");
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
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
    drawRevChart(chart, r.by_month || []);

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

function drawRevChart(box, byMonth) {
  if (!byMonth.length) { box.innerHTML = '<div class="muted">No revenue data.</div>'; return; }
  const labels = byMonth.map((m) => m.month.slice(2));  // "26-06"
  const ys = byMonth.map((m) => m.usd);
  const xs = byMonth.map((_, i) => i);
  if (typeof uPlot === "undefined") {  // fallback: inline bars, no dep
    const max = Math.max(...ys, 1);
    box.innerHTML = '<div style="display:flex;align-items:flex-end;gap:6px;height:200px">'
      + byMonth.map((m, i) => `<div title="${m.month}: ${fmtUsd(m.usd)}" style="flex:1;background:var(--accent);opacity:.85;height:${Math.round(100*ys[i]/max)}%"></div>`).join("")
      + "</div>";
    return;
  }
  const opts = {
    width: box.clientWidth || 820, height: 220,
    scales: { x: { time: false } },
    legend: { show: false },
    axes: [
      { values: (u, s) => s.map((i) => labels[i] || "") },
      { values: (u, s) => s.map((v) => "$" + Math.round(v / 1000) + "k") },
    ],
    series: [{}, {
      label: "Revenue", stroke: "#2f6f6b", fill: "rgba(47,111,107,.18)",
      paths: uPlot.paths.bars({ size: [0.6, 60] }),
    }],
  };
  new uPlot(opts, [xs, ys], box);
}

function card(title, html) {
  const c = el("div", "card");
  c.appendChild(el("h3", null, title));
  c.appendChild(el("div", null, html));
  return c;
}

async function show(section) {
  renderNav(section);
  const view = document.getElementById("view");
  view.innerHTML = "";
  const r = RENDERERS[section];
  try {
    if (r) await r(view);
    else { view.appendChild(el("h2", "section", section)); placeholder(view, section + " — wiring in a later phase."); }
  } catch (e) {
    view.appendChild(el("div", "placeholder", String(e.message || e)));
  }
}

document.getElementById("env").textContent = location.host;
show("System");
