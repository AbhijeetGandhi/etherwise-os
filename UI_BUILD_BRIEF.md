# Cockpit UI rebuild — build brief (2026-08-12)

**Why:** Abhijeet is about to start using this daily and every attempt so far has hit
a "but". Two asks: (1) a place to see **everything the system does each day** — Fathoms
transcribed, registry changes, every run with detail; (2) a UI that is **modern, slick,
minimal, both themes**, to the standard of `app.hermon.io`.

**His words:** *"I need it like a billion dollar company"* · *"keep no build"* ·
*"even if you revamp the whole structure I am fine"* · *"it really is time I start using
this on a daily basis"*.

---

## 0. Non-negotiables carried from the old cockpit

These are not style preferences. Each one exists because something went wrong once.

1. **No fabricated zeros.** `—` means *not measured*; `0` means a true zero and must
   carry a note saying why. Never render an unmeasured thing as 0.
2. **Everything drills.** Every figure opens the rows behind it, the drill states the
   TRUE total, and admits when it is capped ("showing the latest 300"). Rows sum to the
   figure to the cent.
3. **Doctor gate.** Money and Clients render *nothing* on finance-doctor RED — a red
   panel with the failing invariants instead.
4. **Provenance law.** A client-facing card with no evidence link is hidden AND raises
   an anomaly. The count of hidden cards must now be VISIBLE (it wasn't).
5. **Drafts only.** There is no send route on any channel. Do not add one.
6. **One applied-truth source.** `_rail_applied_windows` feeds both Today and Pipeline
   so they can never disagree.
7. **No model calls in the cockpit process.** `/api/ask` shells out to `bin/kb-ask`.
8. **Card identity** = (client, intent), not surface. `_card_key`, `_is_actioned`,
   `nudges.followup_key`, `nudges.ts_key` — see CLAUDE.md.
9. `funnel.js` stays a pure, separately unit-tested module. A 131% conversion bug is why.
10. Token file lives in the **v2 tree** (`etherwise-os/.credentials/cockpit-token.txt`).
    Cross-repo dependency, easy to break.

---

## 1. Stack decision

**No build step. Native ES modules. Vendored dependencies.**

Abhijeet asked for "no build" and "best tech stack, like React, billion dollar" in the
same breath. Those reconcile once you notice what he actually pointed at: `app.hermon.io`
is Next.js + **shadcn/ui**, and shadcn's entire visual identity is a set of **CSS custom
properties** (`--background`, `--foreground`, `--muted`, `--border`, `--radius`) plus a
disciplined type/spacing scale. None of that needs React or Tailwind. The look is the
tokens and the restraint, not the framework.

What a build step would cost here is real: node on a Mac mini running launchd jobs, a
bundler that can break at 3am, and a dev/prod split for a single-user local tool. What it
would buy — components and state — we get from ES modules and a 60-line render loop.

So:

- `<script type="module">`, one module per section, no bundler, no transpile.
- **Vendored, not CDN** (`static/vendor/`) — the cockpit must work with the wifi off.
- **Drop uPlot** (49.7 KB, loaded, never used). Vendor **Chart.js** instead: it does
  per-bar emphasis and labelled target lines, which is exactly why uPlot was abandoned.
- Python side unchanged: stdlib `http.server`, port 5051, `X-Cockpit-Token`.

---

## 2. Information architecture

Old: `Today · Nudges · Mapping · Pipeline · Money · Clients · Knowledge · System`
New: `Today · Pipeline · Money · Clients · Knowledge · Activity · Review`

| New | Absorbs | Rationale |
|---|---|---|
| **Today** | Today + **Nudges** | Nudges was the overflow of the same queue behind a second tab. One queue, one place; "+N more" expands in place. |
| **Pipeline** | Pipeline | Unchanged in scope. Charts re-rendered properly. |
| **Money** | Money | Plus the six figures the server already computes and nobody could see. |
| **Clients** | Clients | Plus the unrouted Airtable roster (status/tags/notes). |
| **Knowledge** | Knowledge | Ask-the-librarian gets first-class placement, not a box halfway down. |
| **Activity** | **System** + NEW run log | The thing he asked for. Digest on top, every run below. |
| **Review** | **Mapping** + anything needing input | Nav badge with a count. If it's zero, the badge is gone. |

Left sidebar, collapsible, icon+label. Sidebar because seven items in a top bar is a
compromise and this is a desktop tool on a large screen.

---

## 3. Activity — the new surface (highest priority)

Two stacked regions.

### 3a. Daily digest ("what the system did today")

Plain language, one line per real thing, with yesterday's comparison where it helps.
Date picker to walk back through previous days. Draw from:

- `sources` — Fathom transcripts pulled/ingested, Loom recordings, email messages,
  Slack messages, by day.
- `facts` — extracted, by client.
- `clients.yaml` — registry changes. **Read the git log of the registry file**: every
  approval writes an audited commit, so "2 clients added, 1 alias" is derivable and
  currently invisible. This is the one he named explicitly ("add to registry").
- `threads` — new threads, newly attributed.
- `runs` — succeeded / skipped / failed counts, total wall time.
- `claude_usage` — spend for the day, by task.
- `nudges` — cards created, cards he closed.
- `shadow_ledger` — intents recorded but not sent (cutover evidence).

Empty days must say "nothing ran" rather than render an empty shell.

### 3b. Run log

Every row in `runs`, newest first. Columns: time · task · status · duration · a one-line
summary derived from that run's `metrics` JSON · cost. Filter by task and status.
Click a row → full detail: complete metrics JSON rendered readably, the anomalies that
run raised, its `claude_usage` rows, and — for failures — the error and a **Re-run**
button (allowlisted `launchctl kickstart`, same mechanism as the existing wake/rail).

Then the existing System content as a third region: doctor checks, source health,
spend vs ceilings, shadow ledger, anomalies.

**Server work needed** (`modules/cockpit/data.py`): `activity(day=None)` and
`activity_run(run_id)`. Read-only via `_ro()` like everything else. The registry-history
read is a `git log` subprocess against `config/clients.yaml` — cache it, it is slow.

---

## 4. Surface what already exists but is invisible

All computed today, all unreachable. Not new features — new *visibility*.

| Where | What | Why it matters |
|---|---|---|
| Money | `pnl.runway_months` | He has never seen his own runway. |
| Money | `provisional` | "N payments seen in email, not yet imported" — written specifically to stop a client looking delinquent when the ledger is just stale. |
| Money | `connects{this_month_usd, lifetime_usd}` | Upwork connects spend. |
| Money | `pnl.operating_costs_usd`, `owner_draw_usd`, `monthly_operating_profit_usd`, `uncategorized_count` | |
| Money | `revenue.last_month_mtd_usd` | Same-day-last-month, the only fair MTD comparison. |
| Today | `queue_hidden_no_provenance` | Cards are being hidden and he cannot tell. |
| Today | `detail.drafts[]`, `detail.thread_urls[]` | A collapsed two-room card ships both drafts; the UI showed one. |
| Clients | `clients_airtable()` | A whole working Airtable roster with status/tags/notes, never routed. |
| Money | server-side filters on `money_transactions` | UI pulls the entire ledger and filters client-side. |

Drop: `pipeline.to_triage` / `bands` / `backlog_uncarded` and `pipeline_rows(stage="triage")`
(structurally always 0 since the ClickUp cutover), and `drawBarChart` (dead).

---

## 5. Design system

Port shadcn's token model to plain CSS. Both themes are first-class; light is not the
"real" one with a dark afterthought. Follow `prefers-color-scheme` on first load, then
respect the explicit toggle (`localStorage.ew_cockpit_theme`).

- **Neutrals do the work.** Colour appears only for status and only where it carries
  meaning. The current palette uses brand navy/blue as furniture; it should not.
- **Semantic tokens**, not brand tokens, in components: `--background --foreground
  --card --card-foreground --muted --muted-foreground --border --input --ring
  --primary --destructive --success --warning`. Both themes define the same names.
- **One radius token** (`--radius: 0.625rem`) with `sm`/`lg` derivations.
- **Type scale**: 12 / 13 / 14 / 16 / 20 / 24 / 30. Body 14. Numbers get
  `font-variant-numeric: tabular-nums` everywhere — misaligned digits are the single
  biggest cheap-looking tell in a financial UI.
- **Borders over shadows.** 1px `--border` and generous padding. Shadows only on
  genuinely floating things (drill panel, tooltip, toast).
- **Spacing**: 4px base, and use more of it than feels necessary.
- **Icons**: vendor the ~25 lucide SVGs actually used as an inline sprite. No icon font.
- **Motion**: 120–160ms ease-out, opacity and 2–4px transforms only. Nothing bounces.
- **Skeletons** on load, never a spinner or a layout jump.
- Keep the existing `esc()` discipline — every interpolated value is escaped.

Accessibility to keep: `:focus-visible` rings, `aria-label` on icon buttons,
`rel="noopener"`, and real `<button>`/`<table>` semantics.

---

## 6. File layout

```
static/
  index.html            shell only
  css/tokens.css        both themes, semantic names
  css/base.css          reset, type, layout primitives
  css/components.css    card, button, table, pill, drill, nav, chart frames
  js/app.js             boot, router, auth, theme, shared fetch
  js/ui.js              el() helpers, esc, fmt (money/date/number), toast, skeleton
  js/drill.js           the shared drill panel (one implementation, all sections)
  js/charts.js          Chart.js wrapper reading CSS vars, theme-aware
  js/sections/{today,pipeline,money,clients,knowledge,activity,review}.js
  js/funnel.js          UNCHANGED — still UMD, still unit-tested from node
  vendor/chart.umd.js   vendored, no CDN
```

One section per file so work can proceed in parallel without collisions.

---

## 7. Testing

`tests/test_cockpit.py` (1975 lines, 22 classes) must stay green — it encodes the rules
above. Add: `activity()` / `activity_run()` readers, the registry-history parse, and the
re-run allowlist. Then a live click-through of all seven sections against the real DB,
both themes, with the failure modes deliberately triggered (doctor RED, degraded source,
empty day, 401).

---

## 8. Open questions for Abhijeet (do not block on these)

- Pipeline wasn't in his list but is a large working surface; kept on the assumption
  that "all the pieces we have need to be there" includes it.
- The 85 unattributed threads get a Review surface tomorrow, as agreed.
