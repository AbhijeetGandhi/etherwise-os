# Jarvis Slack rail (J2 rail #1) — build report

**Built:** 2026-08-09 (night shift). **Status:** code complete, 851-suite green,
migration 022 applied live, launchd job **STAGED but NOT loaded** (per spec — the
live selector calibration is Abhijeet's ◆). No live dry-run yet: there is no Slack
browser session in the headless build env; the dry-run + calibration run on the Mac.

## What shipped
- `modules/jarvis/slack_rail.py` — DOM-only Slack COLLECT rail. Thin injectable
  transport (navigate + a DOM `eval`) feeding a pure, fixture-tested normalization
  layer. Deterministic mapping (a client workspace IS the client, via the registry
  `slack.url`/`slack.team_id`) → every message stored `state='mapped'` with the
  workspace's `client_id`, no proposer, no queue.
- `bin/jarvis-slack-rail` — `[--since DAYS] [--dry-run]`; `module='system'`
  (zero external writes — local events only, like the sweep).
- `core/migrations/022_events_direction.sql` — `events.direction`
  (NULL | inbound | outbound), nullable + additive. **Applied to the live DB**
  (the live sweep calls `events.store_event`, which now writes the column).
- `launchd/io.etherwise.v3.jarvis-slack.plist` — 11:00 + 19:00 IST, `RunAtLoad`
  false, **staged, not loaded**.
- Registry: `slack.url` / `slack.team_id` seeded for the four client workspaces;
  `Registry.match_slack_team()` + `Registry.slack_workspaces()` + team-id
  uniqueness validation; `registry.loads_registry()` helper.
- `config.py`: `JARVIS_SLACK_*` ceilings + `JARVIS_SLACK_SELF_AUTHORS`; a
  `jarvis_slack` `SOURCES` row (degrade kinds `jarvis_slack_empty_window` +
  `jarvis_slack_logged_out`; `critical:False`; backs `today`).

## Seeded workspaces (internal Etherwise excluded — v1 skips it, Q1)
| client | slack.url | team_id |
|---|---|---|
| the-cradle-company | the-cradle-company.slack.com | T01FJJNP2F6 |
| optimize-is | numbersdriven.slack.com | (resolved live from the URL) |
| beglaubigt | beglaubigt.slack.com | (resolved live) |
| cmf-publishing | cmfirm.slack.com | (resolved live) |

Only Cradle's team id was in the recon; the rest are resolved from the live
`/client/T…/` URL at run time and used only to key the event id (navigation goes
through the `<ws>/archives/<conv>` path, which needs no team id).

## The four answered design questions, as built
1. **Internal workspace skipped** — it is not a registry client, so
   `slack_workspaces()` never yields it. No code path touches it.
2. **Bounded, freshest-first, `--since`** — conversations are ordered by most
   recent activity and truncated to `JARVIS_SLACK_MAX_CONVERSATIONS` (40), so a
   truncation always keeps the freshest; messages are windowed to `--since`
   (default 3 days) AND newer than the per-workspace cursor.
3. **Build against fixtures, calibrate live at the ◆** — every guarantee is
   proven offline against a scripted Chrome. The DOM selectors + the two JS
   probes live in ONE clearly-boxed CALIBRATION BLOCK in `slack_rail.py`; the
   parse layer depends on the JSON *contract*, not the selectors, so re-pointing
   a selector never touches tested code.
4. **`direction` column** — added (nullable, migration 022). Our own messages are
   captured and tagged `outbound` (Slack's `is_me` DOM cue, else our display name
   via `JARVIS_SLACK_SELF_AUTHORS`), inbound otherwise — so the follow-up logic
   can tell we already replied and won't mint false "owed a reply" cards.

## Robustness (learned from every browser rail)
- **Logged out / redirected to sign-in** ⇒ `WorkspaceLoggedOut` → per-workspace
  **critical** anomaly (`jarvis_slack_logged_out`) carrying the exact instruction
  "log into `<url>` in Chrome"; the sibling workspaces still complete.
- **Signed in but zero conversations** ⇒ **warn** anomaly
  (`jarvis_slack_empty_window`) — a degrade signal, never a crash.
- **Bounded runtime** — a poll timeout on every DOM read + the 40-conversation
  cap; a wedged workspace cannot hang the rail.
- **Idempotent** — canonical event id `evt:slack:<team>:<conv>:<ts>`;
  SELECT-before-INSERT makes a re-run (even with the cursor reset) a no-op
  (test-covered).

## Tests (23 new; full suite 851 GREEN)
Parse/normalization (event shape, permalink, direction via is_me + name fallback,
team-id fallback); collect flow (recency ordering + bound, `--since` windowing,
channels+DMs, cursor advance + second-run-sees-only-new, idle-conversation skip,
logged-out raises with instruction, empty-but-signed-in); run_rail integration
(stores mapped events with client+direction, logged-out degrades while siblings
continue, empty files a warn, dry-run stores nothing, idempotent across runs);
registry (team match, workspaces listing, duplicate-team load error, seeded live
registry); `events.store_event` persists direction; SOURCES registration.

## What looks fragile / open for the ◆ (live calibration)
- **`last_activity_ts` in the sidebar** — the conversations probe currently reads
  a `data-ts` / `data-qa-last-activity-ts` attribute for recency ordering. Slack's
  sidebar may not expose a per-conversation timestamp there; if it doesn't, the
  ordering/window needs a different signal (e.g. reading each conversation's last
  message ts, or Slack's unreads/Activity view). This is the single most likely
  calibration change. Flagged, not guessed.
- **`is_me` cue** — `c-message--is-me` / `data-qa-is-self` is best-effort; the
  name-based fallback (`JARVIS_SLACK_SELF_AUTHORS`) covers it if the DOM cue is
  absent. Confirm the display name Abhijeet shows as inside client workspaces.
- **Marker hash on Slack URLs** — the live browser wiring appends `#ew-scan` to
  keep the scan tab findable (the connects-rail pattern). If Slack's router
  strips/ў fights the hash, switch to a Slack-dedicated tab lookup — a small,
  isolated change in `_live_browser`.

## To finish (Mac, at the ◆)
`PYTHONPATH=. /usr/bin/python3 bin/jarvis-slack-rail --dry-run` with the five Slack
sessions logged in → calibrate the SELECTORS block against the live DOM → confirm
per-workspace row counts → then load the launchd job.
