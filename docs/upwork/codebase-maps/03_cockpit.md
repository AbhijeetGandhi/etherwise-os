# Cockpit map for the Upwork tab (Etherwise v3, snapshot 2026-08-24)

Repo root the `etherwise-v3/` repo. All paths below are relative to it unless absolute. Line numbers are from this snapshot.

## 1. Architecture

**Server** — `modules/cockpit/server.py`
- Stdlib `http.server.ThreadingHTTPServer` (L20, `make_server` L402-406 sets `httpd.db_path`, `httpd.token`). Entry `main()` L421-432 binds `config.COCKPIT_HOST="127.0.0.1"` / `COCKPIT_PORT=5051` (`core/config.py` L1075-1076). Run as `python -m modules.cockpit.server` by the KeepAlive plist `launchd/io.etherwise.v3.cockpit.plist` (framework Python 3.9, `PYTHONPATH` = repo, logs `~/Library/Logs/Etherwise-v3/cockpit.log`).
- **Auth**: header `X-Cockpit-Token` compared to `self.server.token` (`_authed` L177-180). Token file `config.COCKPIT_TOKEN_FILE` = `CREDENTIALS_DIR/cockpit-token.txt` where `CREDENTIALS_DIR` defaults to `../etherwise-os/.credentials` (config L36, L1077) — the v2 tree; minted on first start by `_ensure_token` L409-418 (mode 600). `doctor_checks.check_cockpit` L377-397 audits token/mode/loopback. `bin/cockpit url|open` builds `http://127.0.0.1:5051/?token=…`; `app.js bootToken()` L89-98 stores `?token=` in `localStorage.ew_cockpit_token` and strips it from the URL.
- **Routing table** (L33-70, GET; L141-160, POST). Dispatch: `do_GET` L312-354 → `GET_ROUTES.get(path)` → `fn(self.server.db_path, **kwargs)` L350. `do_POST` L356-399 → `POST_ROUTES[path](db_path, body)` L397. Raw-body upload route `UPLOAD_ROUTE="/api/finance/upload"` L162/L364-376.

| Route | data.py / actions.py | Notes |
|---|---|---|
| `GET /api/today` | `data.today` L1686 | Today tab |
| `GET /api/today/rows` | `data.today_rows` L2285 | `?which=fresh\|accumulated` |
| `GET /api/pipeline` | `data.pipeline` L1126 | KPI strip, funnel_weeks, by_status, win_rate |
| `GET /api/pipeline/rows` | `data.pipeline_rows` L1460 | THE drill behind every Pipeline figure |
| `GET /api/pipeline/facts` | `data.pipeline_facts` L1349 | one fact row per job; charts aggregate client-side |
| `GET /api/clients`, `/api/clients/transactions`, `/api/clients/airtable` | `clients` L919, `client_transactions` L1033, `clients_airtable` L1068 | airtable reader 60s-cached (`_clients_cache` L20) |
| `GET /api/knowledge` | `knowledge` L432 | `?client=`, `?q=` |
| `GET /api/system` | `system` L254 | health chip + Activity §3 |
| `GET /api/money`, `/api/money/transactions` | `money` L2353, `money_transactions` L2503 | doctor-gated |
| `GET /api/nudges` | `nudges` L398 | CoS grouped queue + latest brief |
| `GET /api/note` | `note_status` L410 | `?note_id=` |
| `GET /api/queue` | `queue` L2637 | Jarvis mapping queue |
| `GET /api/review/threads`, `/api/review/counts` | `review_threads` L2911, `review_counts` L3119 | badge source |
| `GET /api/activity`, `/run`, `/sources`, `/facts` | `activity` L3326, `activity_run` L3549, `activity_sources` L3922, `activity_facts` L4306 | run log + drills |
| `POST /api/nudge` | `actions.nudge` L452 | follow-up card done/snooze/dismiss |
| `POST /api/cos-nudge` | `actions.cos_nudge` L225 | nudge card done/snooze/dismiss |
| `POST /api/card/closure` | `actions.card_closure` L701 | confirm/reject chip |
| `POST /api/wake`, `/api/rail`, `/api/rerun` | `wake` L192, `rail` L213, `rerun` L201 | allowlisted `launchctl kickstart` (`_kick` L73-78; `WAKE_JOBS` L28, `RAIL_JOBS` L34, `RERUN_JOBS` L52-70) |
| `POST /api/ask` | `ask` L346 | shells out to `bin/kb-ask` |
| `POST /api/note`, `/api/note/resolve` | `note` L299, `note_resolve` L331 | capture; classifier spawned detached (`_spawn_classifier` L267) |
| `POST /api/queue/resolve`, `/api/queue/batch`, `/api/review/thread` | L467, L490, L586 | registry writes via `modules.jarvis.approve` |
| `POST /api/finance/import` | `finance_import` L159 | importer bins |

- **Query-param guard** (`QUERY_PARAMS` L97-139 + filter L344-348): a URL param reaches a reader only if it is (1) KEYWORD-ONLY in the reader's signature, (2) a key of `QUERY_PARAMS`, (3) not `_`-prefixed. `db_path` stays the fixed positional arg. Clock slots are positional (`today=` on `today()`/`pipeline()`/`money()`/`pipeline_facts()`) or `_today` keyword-only (`today_rows`, `clients`, `pipeline_rows`). **`QUERY_PARAMS` is one flat namespace across all routes** — a registered name (`stage`, `week`, `frm`, `to`, `status`, `client`, `q`, `day`, `run_id`, …) is forwarded to any reader that declares it; reuse names, validate in the reader.
- **Read-only DB helper** `data._ro(db_path)` L30-41: `sqlite3.connect(timeout=10)` + `PRAGMA query_only=ON` + `row_factory=sqlite3.Row` (not `mode=ro` URI — WAL incident 2026-07-07). Readers open/close per call (`try/finally conn.close()`). Writes go through `core.db.connect()` only in actions.py (and the two once-a-day anomaly flags `_flag_no_provenance` L151 / `_flag_applied_anomaly` L170, which are the only writes from data.py).
- **No model calls in the cockpit process** (server.py L3; UI_BUILD_BRIEF §0.7): `/api/ask` → `subprocess.run(bin/kb-ask)` (actions L356-360), `/api/note` → `Popen(bin/note-classify, start_new_session=True)` (actions L289-293; test `test_capture_spawns_the_classifier_as_a_DETACHED_CHILD` test_cockpit.py L2084-2100).
- **`bin/cockpit reload`** (`bin/cockpit` L21-25): copies `launchd/io.etherwise.v3.cockpit.plist` to `~/Library/LaunchAgents/` then `launchctl kickstart -k gui/$(id -u)/io.etherwise.v3.cockpit` (fallback unload/load). Required after ANY Python change (server, data, actions). JS/CSS/HTML changes need only a browser reload: `_static` L275-309 serves files with `Cache-Control: no-cache` + strong SHA-256 ETag + `Last-Modified`, 304 on match (tests `TestStaticCaching` L144-269).
- **Static serving**: `/`, `/index.html`, `/static/*` from `STATIC_DIR` L28; path-traversal guarded L279-280; content types `_CT` L29-30; `/favicon.ico` → 204 L314.
- **JSON conventions**: no envelope — the reader's dict is the body (`_json` L215-220, `Cache-Control: no-store`). HTTP errors: 401 `{"error":"unauthorized"}`, 404 `{"error":"unknown endpoint"|"not found"}`, 500 `{"error": repr(exc)}` (L351-352, L398-399), 413 with a readable reason (L388-392). **Validation errors are 200** carrying `{"error": "...", "rows": [], "count": 0, "total": None}` — `_bad_filter` L1428-1432; the UI checks `res.error` before touching rows and toasts red (pipeline.js L242). `total: None` = not measured, never 0.
- **Caching**: none for API JSON. Module-level TTL caches in data.py only: `_clients_cache` (60s, L20) and `_registry_cache` (120s git log, L3171-3172). Tests clear both (`ActivityCase` L2993, L2996).

## 2. How a tab is built end-to-end (Pipeline + Today traced) → recipe

**Shell** — `modules/cockpit/static/index.html`: theme pre-paint script L12-18; three stylesheets L20-22; inline lucide sprite L28-63 (`<symbol id="i-*">`, referenced by `ui.icon(name)`); `#navlist` L72 (sidebar, built by app.js); topbar: `#stale` L90, `#reloadBtn` L91, `#health` L97, `#themeToggle` L98; `<main id="view">` L102. Script order L107-110: `/static/vendor/chart.umd.js` (classic), `/static/js/funnel.js` (UMD → `window.EW_FUNNEL`), `app.js` (module). **No per-tab markup in index.html** — a section renders entirely into the `root` div.

**Registration/routing** — `static/js/app.js`
- Section contract L5-22: `export const meta = {title, hint}` (documented but **never read by app.js**; nav label + `document.title` come from `SECTIONS[].label` L390) and `export async function render(root, ctx)`.
- `SECTIONS` L45-53: `{ id, label, icon, load: () => import("./sections/x.js"), badge? }`. Order = sidebar order = keyboard shortcut number (`1..SECTIONS.length`, L448-449). `ALIASES` L57 maps retired ids.
- Hash router: `#/<id>?<query>` (`parseHash` L342-347, `navigate(id, query)` L349-352, `hashchange` L430-433). `show()` L354-404: bumps `renderToken`, `closeDrill()`, `destroyCharts()`, `renderNav`, skeleton, builds `ctx = { api, post, section, params (URLSearchParams), navigate, refresh, kick }` L370-380, awaits `mod.render(root, ctx)`, swaps in on success; 401 → unlock screen; throw → `.errstate`.
- **Refresh cadence**: a section renders once and never polls (L292-302). `r` key / Refresh button re-run the same section+params (`refreshView` L331-333). Only `refreshHealth` (`/api/system` → `#health` chip) and `refreshBadges` (`/api/review/counts` → `navBadges.review`) run every 5 min (L452-454). Staleness label ticks every 30s (L457). `ctx.kick(path, body, button)` L142-174 POSTs an allowlisted job and re-renders the asking section 6s later.
- `api(path)` L107-112 / `post(path, body)` L115-125: fetch with the token header; 401 → `e.unauthorized`.

**Pipeline section** — `static/js/sections/pipeline.js`
- `render` L205: `Promise.all([ctx.api("/api/pipeline"), ctx.api("/api/pipeline/facts")])` L206-209.
- `openRows(title, params, columns, opts)` L233-291 is the ONE drill path: GET `/api/pipeline/rows?` + `qstr(params)` → checks `res.error` → `openDrill({... rows, total, columns, time, search, facets})`. Extra hooks: `figure` (the tile's count becomes the drill's stated total), `narrow` (client-side predicate the server can't express), mismatch notes L266-274.
- Page layout: `heading()` L310 → `kpiStrip` L1091-1274 (`grid-4` of `kpi()` tiles: Carded 7d, ICP/Prime 30d, Applied 7d, Client replies month) → `winRateLine` L1276 (`notice("info")`) → range bar (`.segmented`, presets L319-327, persisted `localStorage.ew_cockpit_pipeline_filters` L51) → `redraw()` L449-482 rebuilding regions: `funnelRegion` L488, `outcomesRegion` L568, `scoreRegion` L632, `skipRegion` L746, `feedRegion` L852, `weeklyRegion` L900, `qualityRegion` L986 → `funnelByWeek` L1287 → `threadsByStatus` L1343.
- Column presets per row SHAPE L112-168 (`jobCols`, `chartCols`, `appliedCols`, `threadCols`, `hiredCols`).
- Server reference day: `F.as_of` L303 (never the browser clock) so drill windows match the server's IST windows (`dAdd(asOf,-6)` L1098).

**Pipeline data** — `data.pipeline()` L1126-1346 → tables: `scored_jobs` (fetched_at, first_scored_at, score, clickup_task_id, icp_tier), `connects_history` (kind='applied', event_date), `proposals` (created_dt, status, upwork_status), `contracts` (start_dt, upwork_status), `source_health` stamps L1337-1339. `pipeline_facts()` L1349-1405: `{jobs:[{i,d,m,s,k,f,c,t,nr}], applies, replies, hires, rail_since, as_of, hot_threshold, score_floor}` from `scored_jobs`/`connects_history`/`proposals`/`contracts`. `pipeline_rows()` L1460-1658: stages `_STAGES` L1424-1425 (`jobs|scanned|scored|carded|applied|replied|hired|triage|threads`), filters validated up front L1488-1503, `week` → Monday..+6 on the stage's date column (`wk()` L1510-1525), else `frm/to` month range; each stage runs `SELECT … LIMIT 300` plus an uncapped `COUNT(*)` → `{stage, week, status, rows, count, total}` L1656-1658.

**Today section** — `static/js/sections/today.js`: `render` L77-91 does one GET `/api/today`, then `sourceHealth` L95 (`.srcline`), `quickCapture` L159, `captured` L212, `actNow` L277 (cards `queueCard` L330, expander "+N more" L317-323 → `/api/nudges`), `backlog` L731 (drills via `/api/today/rows` L810-838), `atAGlance` L842 (`grid-4` of `metricCard` — static, not drillable). Data: `data.today()` L1686-2282 (see §5).

### Recipe: add the Upwork tab (house style)

1. **Reader(s)** in `modules/cockpit/data.py` (one function per section, `_ro()`, no model calls):
   - `def upwork(db_path: Optional[Path] = None, today: Optional[str] = None) -> dict` — `today` POSITIONAL (test clock; `test_no_routed_reader_exposes_a_clock_slot` L867-877 forbids keyword-only `today`/`now`). Compute `day = today or _ist_today()`; windows from `date.fromisoformat(day)`.
   - `def upwork_rows(db_path=None, *, stage=None, week=None, frm=None, to=None, _today=None) -> dict` — validate every user param first with `_bad_filter` (pattern L1488-1503), return `{stage, rows (≤300), count, total (uncapped COUNT)}`.
   - Every count in `upwork()` must be reproducible by one `upwork_rows` query with the same predicate (the tile↔drill parity law, §3).
   - Do NOT write the literal substring `/send` anywhere in server.py/data.py/actions.py (test L319-323 greps the source).
2. **Routes** in `server.py`: add `"/api/upwork": data.upwork` and `"/api/upwork/rows": data.upwork_rows` to `GET_ROUTES` L33-70. Any NEW keyword-only param name must be added to `QUERY_PARAMS` L97-139 (`test_every_forwardable_param_is_registered` L838-854) and every registry key must be declared by some reader (`test_registry_has_no_dead_entries` L856-865). Prefer reusing `stage|week|frm|to|status`.
3. **Section module** `static/js/sections/upwork.js`: `export const meta = { title: "Upwork", hint: "…" }` + `export async function render(root, ctx)`. Import only from `../ui.js`, `../drill.js`, `../charts.js`. Build with `heading(title, "— hint", "h2"|"h3")`, `el("div","grid-4")` of `kpi({...})`, `card(...)`, `table({columns, rows, empty})`, `notice(tone, text)`, `emptyState`. Persist only `localStorage.ew_cockpit_upwork_*`.
4. **Register** in `app.js` `SECTIONS` L45-53: `{ id: "upwork", label: "Upwork", icon: "<sprite id without i->", load: () => import("./sections/upwork.js") }` — position sets the keyboard digit; add `badge: true` + a `navBadges.upwork` writer in `refreshBadges` only if a "needs me" count belongs in the nav (Review does this via `reviewBadgeCount` L36/L285).
5. **Icon**: add a `<symbol id="i-…">` to the index.html sprite L28-63 (lucide, 24×24 stroke paths) unless reusing `zap`/`trending-up`/`message-square`/`inbox`.
6. **CSS**: tokens only (`tokens.css` rule 1: no colour literals outside it); add modifiers to `components.css` rather than near-duplicate classes.
7. **Tests** in `tests/test_cockpit.py`: `TestUpworkReader(unittest.TestCase)` (`db.migrate` + seed, assert counts and tile↔drill parity), `TestUpworkServerParams(CockpitServerCase)` (params reach the reader over HTTP; malformed → 200 `{"error"}`), keep `test_pipeline_endpoint_200` keys if you touch `pipeline()`.
8. `PYTHONPATH=. python3 -m unittest tests.test_cockpit`; then `bin/cockpit reload`; open `#/upwork`.

## 3. The drill mechanism

- **Client**: `static/js/drill.js` — single app-level sheet (`mount` L55-74 appends `.drill-backdrop` + `.drill-panel[role=dialog]` to `document.body`; Esc/backdrop close; focus returns to opener). `openDrill(spec)` L146-384. Spec (docblock L99-145): required `title, rows, columns`; traceability `total` (TRUE count; header states it and admits the cap L171-186), `note` (rendered even on empty L203-213), `capNote`, `truncated` (server cut with no count); power-ups `amount(row)` (sums, biggest-first), `time(row)` (newest-first), `search`, `facets [{label, allLabel, of(row), labelOf}]` (pill strips L245-277, regroup inside the previous pick), `defaultSort`, `emptyText`, `wide`, `rowCap` (default `ROW_CAP=300` L27), `maxFacet`, `subtitle`. Body: `drillsub` count/sum line, `ui.table` (L359-362), cap notices L363-378. Columns use `ui.table` shape `{key, label, num?, render?(row)→Node|string, cls?}`; missing values render `—` (ui.js L475). Shared presets `cols.txn` L403-416.
- **A number becomes clickable** via `kpi({label, value, sub, onClick, warn, stale, unmeasured, plainLabel})` (ui.js L316-330: a real `<button class="kpi">`, `.static` + `aria-disabled` when no `onClick`, hover tag "details →") or `linkNum(text, onClick, {label})` L341-347 (`<button class="linknum">` with aria-label). Chart bars use Chart.js `onClick` → `openRows` (pipeline.js L716-723); funnel bars/stackbar segments are `<button>`s with `srLabel` text (L522-529, L1036-1046). Drills that must fetch first wrap in `withBusy(trigger, fn)` (ui.js L382-398; activity.js L1621-1704).
- **Request/response**: GET the rows endpoint with `qstr(params)` (ui.js L256-261), e.g. `/api/pipeline/rows?stage=applied&week=2026-08-17`; response `{stage, week, status, rows, count, total}` or `{"error", rows:[], count:0, total:null}`. No pagination — a 300-row cap + uncapped `total`; the header says "showing the latest 300" or the caller's `capNote` (see today.js L829-835 for a rank-ordered cap, pipeline.js L1366-1372 for the `threads` stage which returns no total).
- **Enforcement of "no number without a drill"**: UI convention (the helpers), not a static test. Server-side parity IS tested: `test_backlog_count_matches_its_drill` L517-522, `test_pipeline_rows_drills` L1471-1498, `TestPipelineFacts.test_every_funnel_band_equals_its_drill` L2700-2722 (every chart band's predicate == `pipeline_rows(stage="jobs", …)["total"]`), `test_outcome_drills_honour_the_month_range` L2745-2766, `TestMoneyDrill` L2531-2569 (sums to the cent), `test_client_drill_rows_sum_to_the_total` L1279-1285, `TestActivitySourcesDrill.test_the_rows_reconcile_with_the_tile_they_hang_off` L3320-3327. There is no test enumerating JS tiles.

Minimal house-style tile + drill:

```js
// sections/upwork.js (excerpt)
import { el, fmt, dash, kpi, heading, qstr, toast, extLink, pill } from "../ui.js";
import { openDrill } from "../drill.js";

const appliedCols = [
  { key: "d", label: "Date", render: (r) => el("span", "muted", fmt.dateShort(r.d)) },
  { key: "title", label: "Job", render: (r) => (r.job_url ? extLink(r.job_url, r.title || r.job_id) : (r.title || null)) },
  { key: "status", label: "Status", render: (r) => (r.status ? pill(r.status, "outline") : null) },
];

async function openRows(ctx, title, params, columns, opts = {}) {
  let res;
  try { res = await ctx.api("/api/upwork/rows?" + qstr(params)); }
  catch (e) { toast("Could not read the rows behind that figure: " + String(e.message || e), true); return; }
  if (res && res.error) { toast(String(res.error), true); return; }   // 200 + {error}: never an empty list
  openDrill({ title, subtitle: opts.subtitle, rows: res.rows || [], total: res.total, columns,
              time: (r) => r.d, search: (r) => `${r.title || ""} ${r.job_id || ""}`,
              emptyText: opts.emptyText || "No rows behind this figure." });
}

export async function render(root, ctx) {
  const d = await ctx.api("/api/upwork");
  const f = d.funnel.week;                       // {applied: {n, since, until}, ...}
  root.append(heading("Upwork", "— every figure drills to the jobs behind it"));
  const g = el("div", "grid-4");
  g.appendChild(kpi({
    label: "Applied (7d)",
    value: dash(f.applied.n, fmt.num),           // null → "—", 0 → "0"
    unmeasured: f.applied.n == null,
    sub: f.applied.n === 0 ? "a true zero — the rail ran and recorded no application" : `${f.applied.since} → ${f.applied.until}`,
    onClick: f.applied.n == null ? null
      : () => openRows(ctx, `Applied — ${f.applied.since} → ${f.applied.until}`,
                       { stage: "applied", week: f.applied.since }, appliedCols),
  }));
  root.appendChild(g);
}
```

```python
# data.py (excerpt) — the reader half of the same tile
def upwork_rows(db_path=None, *, stage=None, week=None, _today=None) -> dict:
    if stage not in _UPWORK_STAGES:
        return _bad_filter("stage", stage, "one of " + "|".join(_UPWORK_STAGES))
    if week and not _is_iso_day(week):
        return _bad_filter("week", week, "YYYY-MM-DD, the window start")
    lo, hi = week, (date.fromisoformat(week) + timedelta(days=6)).isoformat()
    conn = _ro(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT event_date AS d, job_title AS title, job_id FROM connects_history"
            " WHERE kind='applied' AND event_date BETWEEN ? AND ?"
            " ORDER BY event_date DESC LIMIT 300", (lo, hi))]
        total = conn.execute("SELECT COUNT(*) FROM connects_history WHERE kind='applied'"
                             " AND event_date BETWEEN ? AND ?", (lo, hi)).fetchone()[0]
    finally:
        conn.close()
    return {"stage": stage, "week": week, "rows": rows, "count": len(rows), "total": total}
```

## 4. The funnel component

- **`static/js/funnel.js`** (UMD; `window.EW_FUNNEL` / `module.exports`): `computeFunnel(rows, {FLOOR=8, HOT=16})` L26-78. Bands by narrowing L31-35: `scanned` = all rows → `passed` = `!k` (no `hard_rule_skip`) → `scored` = `s >= FLOOR` → `carded` = `c` → `hot` = `s >= HOT` (of carded). Each step carries `scope` (the exact `pipeline_rows(stage="jobs", …)` filters L37-60; every scope restates its parent's predicates) and `drops` L64-75 between steps. Pure, no DOM. UI_BUILD_BRIEF §0.9: stays UMD and separately unit-tested; pipeline.js refuses to approximate it (L494-502).
- **Conversion %** is computed in `pipeline.js funnelRegion` L512-539: `conv = pctOf(s.n, prev.n)` (step-to-step, `pctOf` L55 = one-decimal %), plus `% of scanned`; bar width `= n/scanned` L515. Red (`.neg`) below 50% L537.
- **Windows today**: `pipeline()` gives 10 ISO weeks (Monday-anchored, `this_mon` L1141, L1168-1176) for `funnel_weeks` (scanned/scored/carded/applied/replied/hired per week); rolling 7-day tiles (`carded_between(day-6, day)` L1183-1188, `_rail_applied_windows` L208-224); rolling 30 days for ICP L1307. pipeline.js scopes charts by calendar MONTH presets L319-327 and `weeklyRegion` shows the last 13 Mondays L911. **No trailing average exists anywhere** (no 8-week or other baseline; `vsPrev` in activity.js L108-114 only compares to the previous day from server-supplied `prev_*`).
- **Stage → SQL today** (`pipeline()`): scanned = `scored_jobs.fetched_at` L1155; scored = `scored_jobs.first_scored_at WHERE score >= 8` L1157; carded = `clickup_task_id IS NOT NULL` L1159; applied = `connects_history WHERE kind='applied'` by `event_date` L1208 (None for weeks before `rail_since` L1227-1231 — never a fabricated 0); replied = `proposals.created_dt` (room-sourced only) L1162; hired = `contracts.start_dt` L1165. Win rate: `proposals.status='Won'` / `status IN ('Won','Lost','Expired','Withdrawn')` (`_DECIDED_PROP` L366, L1267-1277), caveated "threads-only upper bound". Interviews: `proposals.status='Interview'` (today() L1955-1957). Outcomes are deliberately NOT chained into the funnel (pipeline.js L567-629: three sources, three coverage windows).
- **`tests/test_pipeline_funnel.py`**: runs the real funnel.js under `node` (`_node()` L37-45, skips if absent; `DRIVER` L64-98) over four scenarios and pins: monotonic non-increasing bands L104-110, no step >100% L112-120, order `scanned→passed→scored→carded→hot` L122-126, live 131% shape L128-134, hot-uncarded and carded-below-floor adversarial rows L136-149.
- **What the brief's funnel needs that is missing**:
  - `fetched` (MCP): `mcp_shadow_jobs.first_seen_at` (migration 027) is a SHADOW table the pipeline must not read (§5.2 — no mixed provenance; 027 header says so). After cutover 0.1 the provider-flag path presumably writes `scored_jobs` — which table/column marks "fetched via MCP" is not decided in code (only `feed_source` exists). Ambiguous; do not guess.
  - `passed rules`: `scored_jobs.hard_rule_skip IS NULL` (also exists on `mcp_shadow_jobs.hard_rule_skip`).
  - `hot ≥16`: `scored_jobs.score >= config.HOT_LEAD_THRESHOLD` (config L166); brief says thresholds will be recalibrated — read the constant, never 16.
  - `applied`: today `connects_history kind='applied'` (Chrome rail); §0.6 says both tabs must read `_rail_applied_windows` L208-224. After 0.2 (`list_freelancer_proposals`) the truth source changes — the helper must change in ONE place.
  - `viewed`: **no column anywhere** (`proposals` has status/upwork_status only, 002 L40-55). Needs a new column from 0.2 + a migration.
  - `interviewed`: `proposals.status='Interview'` (also `upwork_status='Activated'` in `HOT_PROPOSAL_STATUSES`, poll_inbox.py L30; `threads.room_type='INTERVIEW'`) — three signals, pick and pin one.
  - `offer`: `offers` table (002 L72-80; written only by `sync_state.py` L147-163), `threads.offer_ids_json`, `proposals.upwork_status='Offered'`.
  - `won`: two definitions in code — `proposals.status='Won'` (win rate) vs `contracts.start_dt` (funnel "hired"). Pick one per stage and say which.
  - Conversion deltas vs trailing 8-week average: new server-side computation (`pipeline()`-style `week_counts` Counter over 9+8 weeks); house rule says compute in the reader, ship `prev`/`baseline` fields, never derive a baseline client-side from a missing value.

## 5. Cards on Today

- **Producer**: `data.today()` L1686-2282. Two card streams merged into `queue`:
  1. **Follow-up cards** L1799-1884: `SELECT … FROM drafts d JOIN threads t … WHERE d.sent_status='pending'` (L1800-1806) → keyed by `nudges.followup_key(client_id, room_name, thread_id)` (chief_of_staff/nudges.py L141-165: `followup:c:<client_id>` | `followup:p:<slug>` | `followup:<thread_id>`) → one card per counterparty (rooms collapse, `drafts[]`/`thread_ids[]` ride along) → evidence per group via `evidence.counterparty(conn, thread_ids, client_id)` + `evidence.engagement` L1817-1829.
  2. **Nudge cards** L2056-2126: `nudges.active_nudges()` rows → superseded/closure overlays → `{"kind":"nudge", "nudge_kind", "urgency": urgency_live, "title", "action", "due", "dept", "client", "item_key": "nudge:<dedup_key>", "detail"}`; `owed_reply` nudges whose key is already a follow-up card are deduped L2104-2105; `hotlead_no_loom` is excluded (it is the backlog) L2096.
- **Card JSON** (follow-up, L2007-2037): `kind:"followup", urgency, title, client, tier, bucket, age_days, draft, thread_url, item_key, threads, label ("client comms"|"reply owed"), engagement{state,active,contract_id,status,since,title}, evidence{ts,channel,ours_ts,ours_channel,answered_since,channels[],unproven,waiting_since}, detail{kind,client,age_days,tier,idle_days,source,thread_url,draft,drafts[],thread_urls[]}, possibly_resolved?{source,confidence,evidence}`. Top-level: `queue` (≤`config.TODAY_QUEUE_TOPN`=12, L2188-2190), `queue_total`, `queue_more`, `queue_hidden_no_provenance`, `queue_auto_closed`, `clusters[{label,count}]`, `follow_ups`, `backlog`, `metrics`, `sources`, `captures`.
- **Identity + suppression tables**: `nudge_state` (003; cockpit overlay `item_key→state`, namespaced `followup:*`, `nudge:*`, `review:thread:*`), `nudges` (006 + 018 closure columns), `card_subjects` (024; thread ids a key was ABOUT, written by `actions._capture_subject` L382-398 → `decisions.capture`), `commitments`, `notes`. `_card_key` L57-67 is the render identity; decisions are re-filed under today's key by `decisions.forward_map` L1773-1779 (UI_BUILD_BRIEF §0.8b).
- **Actions**: today.js `act()` L612-627 routes by kind — follow-up → `POST /api/nudge {item_key, action: done|snooze|dismiss, snooze_days?}` → `actions.apply_nudge` L401-449 (upsert `nudge_state`, capture subject, write-through `nudges.set_state`); nudge → `POST /api/cos-nudge {dedup_key, action}` → `actions.cos_nudge` L225-261 (engine first, then `nudge_state` write-through). Chip → `POST /api/card/closure {item_key, action: confirm|reject}` L701-736. Snooze default 3 days (L373).
- **Auto-clear as it exists today — the exact conditions**:
  1. Card **existence** is owned by the Chrome `upwork_inbox` rail (`modules/upwork/poll_inbox.py`, plist `io.etherwise.v3.upwork-inbox` at 10:15 and 19:15). `compute_awaiting` L131-150 reads the last human `messages` row: sender `== config.UPWORK_USER_ID` (config L1052) → `"Client"` else `"Abhijeet"`; `classify_thread` L178-212 sets `threads.bucket`; a pending draft is marked `stale` when the bucket leaves `ACTIONABLE_BUCKETS` L36-37 (L347-354) or `superseded` on re-draft (L331-333). **Nothing in the cockpit clears a follow-up card by itself**: `today()` only reads `sent_status='pending'`.
  2. A card is **hidden** only when the founder recorded done/dismissed/snoozed AND the counterparty has not spoken since: `_is_actioned` L70-125 called at L1838 with `evidence.ts` (newest INBOUND on any channel, evidence.py L278-283/302). Newer evidence revives it (test `test_a_new_client_message_revives_a_done_followup` L1594-1605).
  3. Render-time **chip, not hide**: `answered_since and ours_channel != "upwork"` L2043-2049; `nudges.closure_state='possibly_resolved'` L2148-2151. Hidden only for `auto_closed|confirmed` L2145-2147.
  4. **Where the Dhanya Sangoli class lives**: the brief's "his reply" is ambiguous (Abhijeet's or Dhanya's). Either way the card survives until the inbox rail (a) ingests the message into `messages` and (b) reclassifies the thread. A reply from Abhijeet on Upwork newer than the client's last message gives `answered_since=True` with `ours_channel='upwork'` → excluded from the chip condition L2043 → no clear. Seams to change: the existence query L1800-1806 (add an observed condition: `evidence.answered_since` on upwork, or `messages` newer than `drafts.generated_at`), `evidence.counterparty` (evidence.py L252-313), and the rail freshness (`upwork_inbox` is NOT in `config.SOURCES`, so Today's health line cannot say the rail is stale). Also note `poll_inbox._consecutive_outbound` L164-174 compares `direction == 'outbound'` case-sensitively while evidence.py L27-29 documents mixed-case values — a possible reason a reply does not register (not verified).
- **Brief law #5 in house terms**: each card should ship a server-computed `clear_condition` (what observed event retires it + `observed_at`/`checked_at` from the rail's last run) the way follow-up cards already ship `evidence`/`engagement`; the UI renders it in `followupMeta` L416-508 style (server decides, UI never infers — L419-421).

## 6. System/health surface

- **Where**: Activity tab region 3 — `activity.js systemRegion` L1037-1176 (Run-now buttons L1186-1222; `healthCards` L1224-1407 = Doctor / Spend / Shadow / Anomalies / Platform alerts as `kpi()` tiles with `openDrill`; **Jobs table** L1050-1066 "latest run of every task"; **Source health table** L1068-1098 with `sourceDetail` L1179-1184; Anomalies; Doctor checks; Shadow ledger). The topbar chip is `refreshHealth` (app.js L253-276) off `/api/system`. Today's `.srcline` is `source_health.report()` (data.py L2193-2201).
- **Reader**: `data.system()` L254-361 → `jobs` = latest run per `task_name` by `started_at` from `runs` (L261-265; columns `task_name,status,started_at,completed_at,duration_ms`), spend (`claude_usage`), shadow (`shadow_ledger`), `anomalies` grouped with self-heal logic L295-319, `ops_alerts` from `events` L331-342, `sources` = `source_health.source_health(conn)`, `doctor` = `_doctor_light()` L237-251.
- **Rails registry**: `config.SOURCES` (core/config.py ~L727-895): per source `key,label,task_name,max_age_hours,degrade_kinds,critical,backs,launchd_label,catch_up,empty_ok,why`. `core/source_health.py`: `source_status` L81-159 (`status ∈ down|stale|degraded|ok`, `last_success`, `age_hours`, `hours_overdue`, `escalation`, `backs`), `source_health` L162-177 (`{sources[], worst, ok, critical_unhealthy[]}`), `by_backs` L180-186, `worst_for` L262-271, `report`/`headline`/`source_line` L199-259 (wording). Registered Upwork rails: `connects_rail` (`upwork_connects`, 26h, backs `applied`), `scanner` (`upwork_scan`, 3h, backs `pipeline`), `upwork_sync` (3h, backs `money` — this is the proposals/contracts/offers sync, `sync_state.py`). **Not registered**: `upwork_inbox` (messages), `upwork_outcomes`, `mcp_shadow_poll` (`feeds/shadow_poll.py` L37, plist `launchd/io.etherwise.v3.upwork-mcp-shadow.plist` at :40, metrics `queries/surfaced/new/seen_again/gets/backfilled/get_capped` L210-213). There is no ClickUp-push task of its own (carding happens inside `upwork_scan`; metrics L372-373).
- **Reuse for the per-rail strip**: (a) `source_health.source_health(conn)` filtered to the Upwork task names (status pill tones already exist: `.pill.ok/.degraded/.stale/.down`, components.css L265-273) — requires adding the missing rails to `config.SOURCES` (config is not agent-writable: propose the diff); adding an entry with `launchd_label`+`catch_up=True` also forces the same task into `actions.RERUN_JOBS` and a plist in `launchd/` (tests L3684-3701); (b) or read `runs` directly like `system()` L261-265 for "last run + status + duration + `_metrics_summary(metrics_json)`" (L3194-3216) with no freshness verdict. Drill: `/api/activity?day=` rows (L3378-3383, cap 500) or `ctx.navigate("activity", { day, run: id })` (activity.js reads `params.day`/`params.run` L133-134); one run: `/api/activity/run?run_id=` L3549-3647. Re-run button: `ctx.kick("/api/rerun", {task})` gated by `rerunnable` from the server (activity.js L849-861).

## 7. Charts / CSS / UI conventions

- **charts.js**: `chart(container, factory(p) → ChartConfig, {height})` L148-213 — creates `.chartframe > canvas`, defers construction a frame (detached-container rule L138-146; never wrap in your own rAF), re-runs the factory on theme flip (`THEME_EVENT` L38, `retheme` L216) and `destroyAll()` on section switch (app.js L361). `palette()` L43-67 reads tokens (`--chart-1..5`, `--chart-*-soft`, `--chart-grid/axis/target`, `--foreground`, `--muted-foreground`, `--border`, `--popover`, `--success/warning/destructive/info`); `axes(p, opts)` L97-113 for consistent scales; `applyDefaults` L70-91 (12px font, 160ms easing, tooltip skin). Chart.js is vendored at `/static/vendor/chart.umd.js` (index.html L107) — **not present in this snapshot**; `chart()` degrades to a `.chart-empty` message L154-157. Custom plugins close over `p` (pipeline.js `hotBar` L661-682).
- **tokens.css**: shadcn-style semantic tokens L18-112 (light) / `html[data-theme="dark"]` L114-169; radius `--radius: 0.625rem` (+`-sm/-lg/-full`); type scale `--text-xs 12 / sm 13 / base 14 / md 16 / lg 20 / xl 24 / 2xl 30` L79-86; spacing `--space-1..8` = 4/8/12/16/20/24/32/48 L91-93; shadows L96-98; motion `--dur-fast 120ms`, `--dur 160ms`, `--ease` L101-103; layout `--sidebar-w 220`, `--topbar-h 56`, `--content-max 1180` L105-109. Rule: no hex/rgb/hsl outside tokens.css; translucency via `color-mix`.
- **Theme**: `index.html` L12-18 pre-paints `data-theme` from `localStorage.ew_cockpit_theme` or `prefers-color-scheme`; `app.js setTheme` L70-80 flips + dispatches `THEME_EVENT`; OS changes followed until an explicit choice L83-85.
- **components.css classes**: `.card` (+`.flush`, `.card-head`, `.card-title`(+`.plain`), `.card-sub`, `.card-controls`, `.card-foot`) L166-205; `.metric`/`.metric-note`/`.bar` L208-218; `.kpi` (`.lbl.caps`, `.val`, `.sub2`, `.tag`, `.kpiwarn`, `.kpi-stale`, `.static`) L221-255; `.pill` tones `ok|green|pass|completed|icp / warn|amber|degraded|stale / fail|red|failed|down / info|owed / outline / tier-hot|warm|cold|unknown` L258-279; `.chip` L280-287; tables `.tablewrap`, `th.num/td.num` L289-305; drill `.drill-panel(.wide)`, `.drill-head .count .capped`, `.drill-controls`, `.gpill`, `.drillsub`, `.drill-cap` L307-370; `.toast` L372-383; `.emptystate`, `.errstate`, `.blocked` L386-430; `.notice.ok|warn|bad|info`, `.srcline.ok|bad` L433-452; `.skel`/`.skel-card` L455-468; Today `.qitem .q-row .q-title .q-meta .q-actions .q-chip .q-detail .q-kv .q-draft*` L480-541; `.backlog-ctx`, `.lead-row`, `.lead-right` L610-620; `.expander` L622-632; funnel `.funnel .funnel-step .funnel-head .funnel-bar(.last) .funnel-drop` L647-677; `.stackbar .stackbar-seg .stack-legend` L679-702; `.segmented .segbtn(.on)` L151-163; `.linknum`, `.linkname` L119-132; `.healthchip.green|amber|red` L62-75. base.css: `.view` flex column gap 24 L85-89; `.grid` (auto-fit 230px), `.grid-2`, `.grid-4` (→2 cols ≤1080px, →1 ≤620px) L98-102; `.stack .stack-2 .hstack .hstack-tight .between .grow` L92-97; text utils `.muted .small .sm .strong .mono .truncate .pre .num .pos .neg .nowrap` L104-116; `.dash` L120; `.section-head h2/h3 .hint` L123-128; tabular digits everywhere L22-23.
- **Density rules** (UI_BUILD_BRIEF §5): body 14px, 4px spacing base "use more than feels necessary", borders over shadows (shadows only on drill/tooltip/toast), skeletons not spinners, motion 120–160ms opacity/2–4px only, `esc()` discipline (ui.js L5-10: `el()` sets text; `html:` only with `esc()`), real `<button>`/`<table>`, `:focus-visible`, `aria-label` on icon buttons, `rel="noopener"` (`extLink` L451-457). Use `append()` from ui.js (native `.append(null)` prints "null", L70-88).
- **Numbers**: `fmt.usd` (whole $, real minus), `fmt.usd2` (cents), `fmt.num` (1,234), `fmt.pct(n, digits)`, `fmt.date` ("12 Aug 2026"), `fmt.dateShort`, `fmt.dateTime` (stored wall clock, no TZ conversion), `fmt.dateTimeShort`, `fmt.duration`, `fmt.relative` ("3 minutes ago"), `fmt.ago(days)`, `fmt.due` — ui.js L124-233. `dash(v, fmt)` L245-248: null → "—", 0 → "0" (and a 0 must carry a note). Never `value || 0`. `kpi({unmeasured:true})`/`metricCard({unmeasured:true})` grey the tile.
- **Empty states**: `emptyState(text, {icon, title, detail, action})` L485-493 — a true zero says why (e.g. today.js L307-313 distinguishes "all sources fresh" from "a source is degraded"); an unmeasured thing renders `—`, never an empty list; server-side `empty: true` for "nothing ran" days (activity L3496-3498, activity.js L200-213).

## 8. Test harness

- `tests/test_cockpit.py`: 47 classes, 292 tests (UI_BUILD_BRIEF's "1975 lines, 22 classes" is stale). `CockpitServerCase` L52-91: temp dir, `db.migrate(db_path)` (applies `core/migrations/*.sql`), `seed()` L26-49, real `server.make_server("127.0.0.1", 0, db_path, TOKEN)` in a daemon thread, `self.get(path, token)` / `self.post(path, token, body)` over `urllib` returning `(status, body_text)`. Reader tests subclass `unittest.TestCase` with the same migrate+seed and call `data.x(self.db_path, today="2026-06-15")` directly (`seed_today` L420-465, `seed_client_econ` L948, `seed_money` L2267, `seed_chart_jobs` L2634, `seed_activity` L2885, `ReviewCase` L3733 patches `registry.load`). Launchctl/git/Airtable/importers are `mock.patch`ed (`actions.subprocess.run`, `data.subprocess.run`, `data.AirtableClient`, `actions._load_importer`). Naming: `test_<behaviour_in_plain_english>` with a docstring that names the incident and date.
- JS testing: only `funnel.js` via node (`test_pipeline_funnel.py`); no jsdom, no static assertions on `SECTIONS`, sections or index.html (only `TestStaticCaching` L144-269 with a fake `STATIC_DIR`). `tests/test_jarvis_cockpit.py`: HTTP round-trip for `/api/queue` + `/api/queue/resolve`.
- **Pinned behaviours a new tab must keep**: unknown `/api/*` → 404 (L280-282); every `/api/*` 401 without token (L122-128); no `/send` route and no `/send` literal in server/data/actions source (L311-323); every keyword-only param registered in `QUERY_PARAMS`, no dead registry keys, no `today`/`now` keyword-only slots, `_`-params never forwarded (L729-877); malformed filters → 200 `{"error"}` with `total: None` (L880-941); reader payload keys asserted for `/api/system` (`jobs,spend,shadow,doctor` L277), `/api/today` (`sources,queue,backlog,metrics` L293), `/api/pipeline` (`applied,by_status,win_rate,bands,to_triage` L300 — the UI dropped `bands`/`to_triage` but the reader must keep them); applied tile == Pipeline applied (`test_applied_tile_and_pipeline_read_one_shared_source` L544-562); pre-rail weeks are `None` not 0 (L1353-1359, L1392-1422); `RERUN_JOBS` labels have plists and mirror `SOURCES.catch_up` (L3684-3701); JSON `Cache-Control: no-store` (L265-269); `bin/doctor` `check_cockpit` PASS.

## 9. Gotchas

- `data.py` is one 4,350-line module; `today()` is ~600 lines (L1686-2282), opens three connections (L1702, L1976, L2194) and calls `money()` (finance doctor) at L2204. `pipeline_rows` is a 200-line if/elif over stages. Duplicated SQL: applied windows exist in both `pipeline()` and `today()` only through `_rail_applied_windows` — keep it that way (§0.6); `_HOURLY_SKIPS` shared by facts and rows L1122; anomaly/self-heal logic in `system()` vs `activity()`.
- **Clocks** (data.py L3153-3167): `runs.started_at` IST with `+05:30` (substr = IST date); `claude_usage.ist_date` already IST; `nudges.*_at` IST-naive; `sources/facts/threads/shadow_ledger/events/nudge_state.updated_at` UTC (`datetime('now')`) → use `_ist_day(col)` L3180-3182; compare instants only via `nudges.ts_key(ts, naive="utc"|"ist")` (L107-135, `_ts_utc` L3185). `_ist_today()` L44 is the server day; pipeline.js uses `F.as_of` L303, activity.js computes IST client-side L62-64. `proposals.created_dt` was epoch-millis once (BUILD_BRIEF) — readers filter `LIKE '20%'`.
- `config.APPLICATIONS_PAUSED_SINCE` (L199) is reconciled against the rail (`_pause_contradiction` L1668-1683); `TODAY_QUEUE_TOPN=12`, `TODAY_FRESH_LEAD_DAYS=10`, `TODAY_BACKLOG_TOPN=8` L205-207; `HOT_LEAD_THRESHOLD=16` L166; `REPLY_LAG_DAYS=3` L211; `MONTHLY_REVENUE_TARGET_USD` L1089. config.py is not agent-writable — propose diffs.
- `SHADOW_MODE["cockpit"]=False` (config L136): the cockpit's only writes are `nudge_state`, engine write-through, anomalies, staged uploads, and registry commits via `jarvis.approve`.
- Python on the Mac is 3.9 (plist) — no `match`, no `tomllib`; stdlib only.
- `bin/cockpit` hardcodes absolute Mac paths (`PLIST_SRC`, `TOKEN_FILE` L6-8); `reload` after every Python change — the server process is a long-lived import of `data`/`actions`.
- **Snapshot gaps** (say so, do not build on them): no `static/vendor/` (chart.umd.js) and no `static/_legacy/` folder exist here — UI_BUILD_BRIEF §6 lists `vendor/`, nothing references `_legacy`; `launchd/` lacks the kb-daily/jarvis/finance/cos plists that `RERUN_JOBS` L52-70 names, so `test_every_label_has_a_plist_on_disk` L3684-3688 would fail in this snapshot (almost certainly snapshot incompleteness, unverified).
- `meta` exports are dead (app.js never reads them); labels/icons come from `SECTIONS`. Icons must exist in the index.html sprite. Keyboard digit = position in `SECTIONS`.
- Today's "At a glance" uses `metricCard` (non-drillable) L842-895 — an existing exception to "everything drills"; do not copy it for the Upwork tab.
- `pipeline_rows(stage="threads")` returns no uncapped total (falls back to `len(rows)`, L1656-1658) — pipeline.js supplies `figure` + `capNote` L1361-1373; a new rows reader should always compute `COUNT(*)`.
- Drill `capNote` default claims "the latest N" — override whenever the server's order is not time (today.js L824-835).
- Sections must never keep state across renders except `localStorage.ew_cockpit_*` (app.js L20-22); `redraw()` must destroy its own chart handles (pipeline.js L439-447).
- `nudge_state`/`nudges` are two stores for one decision — always write both (`apply_nudge` L426-437, `cos_nudge` L244-259) and read both (`today()` L1731-1756).
- Rails behind Upwork numbers are Chrome scrapes with their own freshness verdicts; a stale `connects_rail` renders the Applied tile as "stale", dashed (pipeline.js L1176-1232). The MCP shadow poller, inbox and outcome rails have no registry entry, so their staleness is invisible to every tab today.
