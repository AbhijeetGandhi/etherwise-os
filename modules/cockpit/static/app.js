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
  // Money + Today rendered in their phases; others honest placeholders.
  Knowledge: async (view) => {
    view.appendChild(el("h2", "section", "Knowledge"));
    placeholder(view, "Ingestion arrives with M3. Fathom poller is staged; nothing to show yet.");
  },
};

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
