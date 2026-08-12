// ═══════════════════════════════════════════════════════════════════════════
// app.js — the shell: boot, theme, auth, the shared fetch, the hash router,
// the sidebar, the health chip. It owns nothing a section should know about.
//
// THE SECTION CONTRACT (every file in js/sections/ implements exactly this):
//
//   export const meta = { title: "Money", hint: "optional grey sub-line" };
//   export async function render(root, ctx) { … }
//
//   root  an empty <div> already inserted in the page. Append to it.
//   ctx   { api, post, refresh, navigate, section, params, token }
//         api(path)            GET  → parsed JSON. Throws; 401 is handled for you.
//         post(path, body)     POST → parsed JSON.
//         refresh()            re-render this section (after an action)
//         navigate(id)         go to another section
//         params               URLSearchParams from the hash query, e.g. #/activity?day=…
//
//   render() may be async. While it runs the router shows a skeleton. If it
//   throws, the router shows the error state; if the throw is a 401 the router
//   shows the unlock screen. A section NEVER writes to document.body, NEVER
//   registers a global, and NEVER keeps state across renders except in
//   localStorage under an `ew_cockpit_*` key.
//
// AUTH: token in localStorage.ew_cockpit_token, sent as X-Cockpit-Token.
// DRAFTS ONLY: there is no send route on any channel. Do not add one.
// ═══════════════════════════════════════════════════════════════════════════

"use strict";

import { el, clear, esc, icon, skeletonSection, toast, fmt } from "./ui.js";
import { closeDrill } from "./drill.js";
import { destroyAll as destroyCharts, THEME_EVENT } from "./charts.js";

const TOKEN_KEY = "ew_cockpit_token";
const THEME_KEY = "ew_cockpit_theme";      // "light" | "dark" | absent = system
const NAV_KEY = "ew_cockpit_nav";          // "expanded" | "collapsed"
const LAST_SECTION_KEY = "ew_cockpit_section";

/* ── the seven sections ───────────────────────────────────────────────────── */

const SECTIONS = [
  { id: "today", label: "Today", icon: "list-checks", load: () => import("./sections/today.js") },
  { id: "pipeline", label: "Pipeline", icon: "trending-up", load: () => import("./sections/pipeline.js") },
  { id: "money", label: "Money", icon: "wallet", load: () => import("./sections/money.js") },
  { id: "clients", label: "Clients", icon: "users", load: () => import("./sections/clients.js") },
  { id: "knowledge", label: "Knowledge", icon: "book-open", load: () => import("./sections/knowledge.js") },
  { id: "activity", label: "Activity", icon: "activity", load: () => import("./sections/activity.js") },
  { id: "review", label: "Review", icon: "inbox", load: () => import("./sections/review.js"), badge: true },
];

// old deep links keep working (Nudges folded into Today, System into Activity,
// Mapping into Review) — a bookmark must not 404 into an empty screen
const ALIASES = { nudges: "today", system: "activity", mapping: "review" };

const byId = (id) => SECTIONS.find((s) => s.id === id);

/* ── theme ────────────────────────────────────────────────────────────────────
   index.html applies the stored/system theme in a tiny inline <script> so the
   first paint is already correct (a module is deferred — it always flashes).
   This is the owner of every subsequent change. */

export function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function setTheme(next, persist = true) {
  document.documentElement.setAttribute("data-theme", next);
  if (persist) localStorage.setItem(THEME_KEY, next);
  const b = document.getElementById("themeToggle");
  if (b) {
    clear(b).appendChild(icon(next === "dark" ? "sun" : "moon", { size: 16 }));
    b.setAttribute("aria-label", next === "dark" ? "Switch to light theme" : "Switch to dark theme");
    b.title = b.getAttribute("aria-label");
  }
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: { theme: next } }));
}

// follow the OS until the founder makes an explicit choice
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
  if (!localStorage.getItem(THEME_KEY)) setTheme(e.matches ? "dark" : "light", false);
});

/* ── auth + fetch ─────────────────────────────────────────────────────────── */

function bootToken() {
  const u = new URL(location.href);
  const t = u.searchParams.get("token");
  if (t) {                                  // ?token=… → store once, then strip
    localStorage.setItem(TOKEN_KEY, t);
    u.searchParams.delete("token");
    history.replaceState({}, "", u.pathname + u.search + u.hash);
  }
  return localStorage.getItem(TOKEN_KEY) || "";
}

function unauthorized() {
  const e = new Error("unauthorized");
  e.unauthorized = true;
  return e;
}

/** GET a JSON endpoint. Throws on !ok; .unauthorized is set on a 401. */
export async function api(path) {
  const r = await fetch(path, { headers: { "X-Cockpit-Token": bootToken() } });
  if (r.status === 401) throw unauthorized();
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}

/** POST JSON. Same error discipline. Drafts-only: no route here sends anything. */
export async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "X-Cockpit-Token": bootToken(), "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (r.status === 401) throw unauthorized();
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${path} → HTTP ${r.status}`);
  return data;
}

/** Kick an allowlisted launchd job / rail and report on the button that asked. */
export async function kick(path, body, button) {
  const name = button ? button.textContent : "job";
  toast(`${name} started — the run log will update shortly`);
  if (button) { button.disabled = true; button.textContent = "Running…"; }
  try {
    const d = await post(path, body);
    if (button) button.textContent = d.ok ? "Started ✓" : "Failed";
    if (!d.ok) toast(`${name} failed: ${d.error || "see logs"}`, true);
    return d;
  } catch (e) {
    if (button) button.textContent = "Failed";
    toast(`${name} failed to start`, true);
    return { ok: false, error: String(e.message || e) };
  } finally {
    if (button) setTimeout(() => { button.textContent = name; button.disabled = false; }, 2500);
  }
}

/* ── unlock screen ────────────────────────────────────────────────────────── */

function renderUnlock(view, failed) {
  const input = el("input", {
    class: "input grow", type: "password", placeholder: "cockpit token",
    attrs: { "aria-label": "cockpit token", autocomplete: "off" },
  });
  const unlock = () => {
    const t = input.value.trim();
    if (!t) return;
    localStorage.setItem(TOKEN_KEY, t);
    show(currentSection);
  };
  const go = el("button", { class: "btn primary", type: "button", on: { click: unlock } }, "Unlock");
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") unlock(); });

  clear(view).appendChild(el("div", "unlock fade-in", [
    el("h3", null, "Cockpit locked"),
    el("p", null, "Paste your cockpit token to unlock this browser. It is saved locally — you only do this once."),
    failed ? el("p", "unlock-err", "That token didn’t work. Try again.") : null,
    el("div", { class: "field-row", style: { margin: "16px 0" } }, [input, go]),
    el("div", { class: "small muted", html: "Get it: run <code>bin/cockpit url</code> in the repo — or <code>bin/cockpit open</code> to skip this screen." }),
  ]));
  input.focus();
}

/* ── sidebar ──────────────────────────────────────────────────────────────── */

let navBadges = {};

function renderNav(active) {
  const list = document.getElementById("navlist");
  if (!list) return;
  clear(list);
  SECTIONS.forEach((s) => {
    const n = navBadges[s.id] || 0;
    const link = el("button", {
      class: "navlink", type: "button",
      attrs: Object.assign({ "data-section": s.id, title: s.label },
        s.id === active ? { "aria-current": "page" } : {}),
      on: { click: () => navigate(s.id) },
    }, [
      icon(s.icon, { size: 16 }),
      el("span", "lbl", s.label),
      // the badge exists ONLY when there is something in it
      s.badge && n > 0 ? el("span", "navbadge", fmt.num(n)) : null,
    ]);
    list.appendChild(link);
  });
}

function setNavState(state) {
  document.getElementById("app").dataset.nav = state;
  if (state !== "open") localStorage.setItem(NAV_KEY, state);
}

/* ── health chip + review badge (both on the 5-minute timer) ──────────────── */

async function refreshHealth() {
  const chip = document.getElementById("health");
  if (!chip) return;
  try {
    const d = await api("/api/system");
    const worst = ((d.doctor || {}).worst || "?").toUpperCase();
    const last = (d.jobs || []).map((j) => j.started_at || "").sort().pop() || "";
    // green = all clear · amber = WARN (owner reminders, not faults) · red = FAIL/RED
    const cls = worst === "GREEN" || worst === "PASS" ? "green"
      : worst === "WARN" ? "amber" : worst === "?" ? "" : "red";
    chip.className = "healthchip " + cls;
    clear(chip).append(
      el("span", "dot2"),
      document.createTextNode(`doctor ${worst}` + (last ? ` · sync ${fmt.dateTime(last).slice(3)}` : "")),
    );
    chip.title = `finance-doctor: ${worst}` + (last ? ` · last job ${fmt.dateTime(last)}` : "");
  } catch (e) {
    chip.className = "healthchip";
    clear(chip).append(el("span", "dot2"), document.createTextNode("health n/a"));
    chip.title = e && e.unauthorized ? "locked — unlock to read system health" : String(e.message || e);
  }
}

async function refreshBadges() {
  try {
    const q = await api("/api/queue");
    navBadges.review = (q.queued_total || 0) + (q.held_total || 0);
  } catch (e) {
    navBadges.review = 0;                 // never invent a count out of an error
  }
  renderNav(currentSection);
}

/* ── router ───────────────────────────────────────────────────────────────── */

let currentSection = "today";
let renderToken = 0;                       // guards against a slow render landing late

function parseHash() {
  const raw = (location.hash || "").replace(/^#\/?/, "");
  const [name, query] = raw.split("?");
  const id = ALIASES[name] || name;
  return { id: byId(id) ? id : null, params: new URLSearchParams(query || "") };
}

export function navigate(id, query) {
  const q = query ? "?" + (typeof query === "string" ? query : new URLSearchParams(query).toString()) : "";
  location.hash = "#/" + id + q;
}

async function show(id, params) {
  const sec = byId(id) || SECTIONS[0];
  currentSection = sec.id;
  const mine = ++renderToken;

  closeDrill();
  destroyCharts();
  renderNav(sec.id);
  setNavState(localStorage.getItem(NAV_KEY) === "collapsed" ? "collapsed" : "expanded");
  localStorage.setItem(LAST_SECTION_KEY, sec.id);

  const host = document.getElementById("view");
  clear(host).appendChild(skeletonSection());

  const ctx = {
    api, post, kick,
    section: sec.id,
    params: params || new URLSearchParams(),
    navigate,
    refresh: () => show(sec.id, params),
  };

  try {
    const mod = await sec.load();
    if (mine !== renderToken) return;                       // superseded
    const root = el("div", "view fade-in");
    if (typeof mod.render !== "function") throw new Error(`${sec.id}.js does not export render()`);
    await mod.render(root, ctx);
    if (mine !== renderToken) return;
    clear(host).appendChild(root);
    document.title = `${sec.label} · Etherwise cockpit`;
  } catch (e) {
    if (mine !== renderToken) return;
    if (e && e.unauthorized) { renderUnlock(host, !!localStorage.getItem(TOKEN_KEY)); return; }
    clear(host).appendChild(el("div", "view", el("div", "errstate", [
      el("div", "strong", `${sec.label} failed to load.`),
      el("div", { class: "small", style: { marginTop: "6px" } }, String(e && e.message || e)),
      el("div", { class: "small", style: { marginTop: "10px" } },
        "Nothing was changed. Pick the section again to retry."),
    ])));
    console.error(`[cockpit] ${sec.id} render failed`, e);
  }
}

/* ── boot ─────────────────────────────────────────────────────────────────── */

function boot() {
  document.getElementById("envline").textContent = location.host;

  const toggle = document.getElementById("themeToggle");
  toggle.addEventListener("click", () => setTheme(currentTheme() === "dark" ? "light" : "dark"));
  setTheme(currentTheme(), false);          // paint the correct glyph, persist nothing

  document.getElementById("navToggle").addEventListener("click", () => {
    const app = document.getElementById("app");
    if (window.matchMedia("(max-width: 900px)").matches) {
      setNavState(app.dataset.nav === "open" ? "expanded" : "open");
    } else {
      setNavState(app.dataset.nav === "collapsed" ? "expanded" : "collapsed");
    }
  });
  setNavState(localStorage.getItem(NAV_KEY) === "collapsed" ? "collapsed" : "expanded");

  window.addEventListener("hashchange", () => {
    const { id, params } = parseHash();
    show(id || currentSection, params);
  });

  // keyboard: 1–7 jump to a section when nothing is focused in a field
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target && e.target.tagName) || "";
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || e.target.isContentEditable) return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= SECTIONS.length) navigate(SECTIONS[n - 1].id);
  });

  refreshHealth();
  refreshBadges();
  setInterval(() => { refreshHealth(); refreshBadges(); }, 5 * 60 * 1000);

  const { id, params } = parseHash();
  const start = id || localStorage.getItem(LAST_SECTION_KEY) || "today";
  if (!location.hash) history.replaceState({}, "", "#/" + start);
  show(start, params);
}

boot();

// exported for sections that need to reach the shell (rare — prefer ctx)
export { show, SECTIONS, bootToken, esc };
