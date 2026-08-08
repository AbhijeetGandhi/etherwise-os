# Finance Payment-Mail Rail — Design Spec

**Date:** 2026-08-09 (night shift, Cowork-discovered) · **Module:** `modules/finance/payment_mail_rail.py` + `bin/finance-payment-mail`
**Status:** design → build under the night-shift autonomous protocol (build vs fixtures, flag open decisions, leave live run-commands for one approval; do not claim green solo; do not commit until green).

## Problem (the "stated vs observed" failure family, finance edition)

The ledger's money picture silently goes stale whenever the **manual weekly Wise CSV import** slips (it has since July). A stale import does not merely *delay* data — it **manufactures false conclusions about clients**: on 2026-08-09 the cockpit showed August revenue at $550, which made Adelye (who actually paid Aug 7) look delinquent, purely because the CSV was not imported. Same law we killed for the applied-tile contradiction guard: a source that stopped producing must announce it, not render a confidently-wrong number.

The truth already exists in the **Wise/Stripe notification emails** in `abhijeetgandhi01@gmail.com`. Reading them read-only gives an independent, earlier signal of money received — which we use to (a) know the ledger is behind, and (b) stop flagging clients who have in fact paid.

## Hard constraints (from CLAUDE.md / SOUL)

- **Read-only, no sends.** IMAP `select(readonly=True)`, `BODY.PEEK` only, SEARCH/FETCH verbs only — identical read-only-by-construction discipline as `modules/jarvis/imap_rail.py`.
- **Finance stays shadowed for external writes** (`SHADOW_MODE['finance']=True`). This rail makes ZERO external writes. Storing provisional rows to a **local** table is a local ledger op (same class as `finance-import-wise`), so the bin runs as `module='system'` (local-only, runs live) exactly like `bin/finance-stripe-sync`.
- **Never double-count.** The authoritative CSV/statement import stays the ONLY writer of the real ledger (`transactions`). Provisional events live in their OWN table that P&L / doctor / cash never read.
- **Client-facing facts require attribution.** Unknown payer → mapping queue, never a guess.
- Integer minor units only (`modules/finance/money.to_minor`, Decimal, no float). IST timestamps. No emojis. Never echo credentials.

## Decisions (spec-faithful defaults; AskUserQuestion did not register — flagged in BUILD_BRIEF for morning review, all reversible)

1. **Storage = separate `provisional_payments` table** (not flagged rows in `transactions`). A provisional row *physically cannot* enter P&L/doctor/cash because it is not in `transactions`. Own namespace `wise-email:<msgid>` / `stripe-email:<msgid>`. **[confirm]**
2. **Staleness surfacing = banner + caveat, numbers still shown** (not a hard-block like doctor-RED). The ledger is not *wrong*, just *behind*; blanking a working tab is worse than a loud "understated floor" banner. Money tab renders the total with a loud "ledger stale — N payments seen in email but not yet imported (most recent: <client>, <date>); revenue is a floor, understated by ≥ $X" line. **[confirm — task text leans 'do not render a confidently-wrong total'; banner+caveat satisfies that without blanking]**
3. **Stripe = build the parser (own parser, same provisional mechanism) alongside Wise.** Note: a daily `finance_stripe` rail already imports Beglaubigt via API/CSV, so Stripe email events will usually *confirm-match fast* on reconcile (low staleness value) — Wise is the proven-stale path. Building both is spec-faithful and the reconcile makes redundancy harmless. **[confirm whether Stripe email is even needed given the daily rail]**
4. **Reconcile window N = 10 days** (weekly Wise cadence 7d + buffer). An unmatched provisional older than 10d surfaces loudly (anomaly + cockpit). **[confirm]**

## Architecture

Six units, each independently testable; the parsing/attribution/reconcile/staleness/AR logic is **pure over its inputs** (no socket, no clock) so fixtures cover it with zero network — the transport is the only injectable side.

### 1. Parsers (pure) — `parse_wise`, `parse_stripe`
`parse_wise(subject, body, *, native_id) -> Optional[dict]`. Subject pattern `"<Payer> just paid you"` (+ a small set of known Wise incoming-payment subject variants). Amount + currency + date pulled from the body; payer name from the subject. **All provider-specific regexes live in ONE clearly-boxed CALIBRATION block** (same approach as the Slack rail's selector block) because the exact live email HTML is not verifiable in the headless build env — it needs one live calibration against a real message in inbox 01 before enabling.
`parse_stripe(subject, body, *, native_id)` — Stripe receipt/notification format (Beglaubigt.de), own regexes in the same CALIBRATION block. Returns a normalized provisional dict or `None` (not a payment notification).

Normalized provisional dict:
```
{"canonical_key": "wise-email:<msgid>", "provider": "wise"|"stripe",
 "payer": "<raw name>", "amount_minor": <signed +>, "currency": "USD",
 "paid_date": "YYYY-MM-DD", "provider_ref": <id|None>, "native_id": <msgid>}
```
A row that parses to no amount/date is **quarantined loudly**, never silently dropped.

### 2. Sender classify — `provider_for(from_addr) -> 'wise'|'stripe'|None`
Sender allowlist in `config.PAYMENT_MAIL_SENDERS`. Only allow-listed senders are parsed; everything else in the inbox is ignored by this rail (the jarvis IMAP rail owns client mail).

### 3. Attribution — `attribute(payer, registry) -> client_id|None`
Chain: finance-specific `PAYER_ALIASES` (the billing entities: "Consulting Systems BV"→hermon, "Adélye Ltd."→adelye, "Horizons ABA MIdCo LLC"→horizons-aba, "CMF Publishing"→cmf-publishing, "Beglaubigt"→beglaubigt) → `registry.match_name` → `config.CLIENT_CANONICAL`. No hit → `None` → mapping queue. Never guesses.

### 4. Transport / collect — `collect(inbox, cursor, ...) -> (provisional[], cursor)`
Reuses `imap_rail`'s read-only primitives (`_connect`, `_uidvalidity`, `_search`, `_fetch_headers`, `_fetch_body`, cursor read/write, `permalink`). Per-inbox UID cursor `"<uidvalidity>:<maxuid>"`, cold start bounded to `PAYMENT_MAIL_FIRST_FETCH_DAYS=60` (the report needs 60 days). Header fetch → `provider_for`; only an allow-listed sender triggers the body PEEK → parser. Injectable `conn_factory`/`pw_fn` for fixtures. `InboxSkip` on missing app password (clean skip, never a crash), mirroring `imap_rail`.

### 5. DAL + reconcile — `upsert_provisional`, `reconcile`, `staleness`
- `upsert_provisional(conn, p)` — idempotent by `canonical_key` (SELECT-before-INSERT); re-run is a no-op.
- `reconcile(conn, *, window_days=10, now)` — for each `provisional`/`superseded`-less row, look for a real ledger `transactions` income row matching **(client attribution) AND (amount_minor within tolerance) AND (paid_date within ±window)**; on match flip `state='confirmed'`, set `matched_canonical` + `confirmed_at`. Unmatched past `window_days` → collect into a loud list (bin raises `finance_payment_mail_unmatched` anomaly). Reconcile never writes `transactions`.
- `staleness(conn) -> dict` — the point of the whole thing. Compare newest `paid_date` among un-confirmed provisional rows vs newest ledger income `creation_dt`. If provisional is ahead → `{"stale": True, "unimported": [...], "most_recent": {"client_id","paid_date","amount_usd"}, "count": N}`. Deterministic, pure over the two queries.

### 6. AR view — `ar_overdue(conn, today)`
Per client with a payment cadence (reuse `modules/finance/nudges.CLIENTS` anchors + ledger last-payment), compute expected-next vs last-actual → "overdue by N days, ~$X". **A provisional payment counts as 'seen'** so a client who paid (email says so) is NOT flagged overdue even while the ledger is behind — this is exactly what would have stopped Adelye looking delinquent. Genuinely-overdue (no email, no ledger) still surfaces (Hermon 24d, Horizons 36d on 2026-08-09).

### Migration `023_provisional_payments.sql`
Additive, non-destructive. Table as in §1 (`canonical_key` PK/unique, `provider`, `payer`, `client_id`, `amount_minor` signed, `currency`, `paid_date`, `provider_ref`, `native_id`, `permalink`, `raw_ref`, `state` default `provisional`, `matched_canonical`, `seen_at`, `confirmed_at`, `updated_at`) + indexes on `state`, `client_id`. Deliberately NOT referenced by doctor/pnl/cash.

### Cockpit — `money()` gains a `provisional` block
`money()` return grows `"provisional": {"staleness": staleness(conn), "ar_overdue": [...], "pending": [...]}`. Rendered as a banner above the revenue KPI when `staleness.stale`. Does NOT change the doctor gate (RED still blanks everything; staleness is a softer caveat layered on GREEN numbers). Read-only, never raises (a health read cannot take down the panel it describes).

### `config.py` additions (build-phase writes allowed per ALLOW_CORE_WRITES)
- `PAYMENT_MAIL_INBOX = "abhijeetgandhi01@gmail.com"`
- `PAYMENT_MAIL_SENDERS = {"wise": (...), "stripe": (...)}`
- `PAYMENT_MAIL_RECONCILE_WINDOW_DAYS = 10`, `PAYMENT_MAIL_FIRST_FETCH_DAYS = 60`, `PAYMENT_MAIL_AMOUNT_TOLERANCE_MINOR = 100`
- `PAYER_ALIASES = {...}` (the billing-entity map above)
- One `SOURCES` row `finance_payment_mail` (backs `money`, `critical: False`, `freshness_sql` = newest `seen_at` on `provisional_payments`, `max_age_hours` ~26) so the rail's OWN staleness (did it run?) is tracked by the existing `source_health` mechanism; the email-ahead-of-ledger signal is the separate computed `staleness()`.

### `bin/finance-payment-mail`
- default: **read-only dry-run** (like `bin/jarvis-imap-check`) — collect + parse, print what provisional events WOULD be emitted, per-provider counts, attribution, no store, no cursor write, never prints bodies/credentials.
- `--apply`: store provisional (idempotent) + reconcile + emit staleness/AR; `module='system'`; writes a `runs` row.
- `--report [--days 60]`: the 60-day deliverable — what the notifications produce and which payments are in email but missing from the ledger.

## Tests (fixtures, no network)
`tests/test_payment_mail_rail.py`: parse Wise subject/body → amount/currency/date/payer; parse Stripe; sender classify; attribution (4 billing entities + unknown→None); provisional idempotency (re-upsert no-op); reconcile matches on (client,amount,±window) and flips confirmed; **provisional NEVER enters `transactions` / never double-counts after a CSV import** (the cardinal test); unmatched-past-window surfaces; staleness triggers when email is ahead of ledger and is silent when caught up; AR overdue calc (overdue when no email; NOT overdue when a provisional exists); `InboxSkip` on missing password; read-only transport assertion (fake IMAP whose surface omits STORE/EXPUNGE/APPEND/COPY).

## Open (Abhijeet — morning)
The 4 `[confirm]` decisions above + **one live calibration**: run `bin/finance-payment-mail` (dry-run, read-only) on the Mac against inbox 01 to calibrate the CALIBRATION regex block against real Wise/Stripe emails before `--apply`. The exact live email HTML is not verifiable in the headless build env, so parser patterns are built to the documented format and isolated for a ~10-min calibration.
