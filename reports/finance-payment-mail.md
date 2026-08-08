# Finance payment-mail rail — build report (2026-08-09, night shift)

## What was built
A read-only rail that reads Wise/Stripe **payment notifications** from inbox 01
and emits **PROVISIONAL** payment events, so a stale CSV import announces itself
instead of rendering a confidently-wrong monthly total (the bug that made Adelye
look delinquent on 2026-08-09 with August revenue shown as $550).

- `core/migrations/023_provisional_payments.sql` — a table DELIBERATELY separate
  from `transactions`; doctor/pnl/cash never read it, so a provisional row cannot
  enter the real ledger and cannot double-count.
- `modules/finance/payment_mail_rail.py` — pure parsers (Wise + Stripe, regexes
  isolated in ONE CALIBRATION block), registry-backed attribution, read-only IMAP
  transport reusing `imap_rail`'s proven substrate, DAL + reconcile + staleness +
  AR overdue math.
- `bin/finance-payment-mail` — dry-run (default, read-only), `--apply`,
  `--reconcile`, `--report`.
- `core/config.py` — `PAYMENT_MAIL_*` constants, sender allow-list, `PAYER_ALIASES`
  (billing entity → client slug), one `SOURCES` row (runs-based freshness).
- `modules/cockpit/data.py` — `money()` gains a `provisional` overlay (staleness
  banner + unmapped count); never raises, never changes the doctor gate.
- `launchd/io.etherwise.v3.finance-payment-mail.plist` — **STAGED, NOT LOADED**.

## Verification (this session, on the Mac)
- `tests/test_payment_mail_rail.py` — **26 tests GREEN**. Covers: Wise/Stripe
  parse; sender classify; attribution (4 billing entities + suffix-strip +
  unknown→None); provisional idempotency; reconcile match/flip; **the cardinal
  guarantee — a provisional row NEVER enters `transactions`**; unmatched-past-10d
  surfaced; staleness triggers when email is ahead of ledger and is silent when
  caught up; confirmed rows drop out of staleness; mapping queue; AR overdue
  (overdue with no email, NOT overdue when a provisional exists); read-only
  transport assertion (fake IMAP omitting STORE/EXPUNGE/APPEND/COPY); InboxSkip.
- **Full suite: 877 GREEN** (851 prior + 26). Migration 023 is additive; the
  float-ban and no-double-count invariants hold.

## The 60-day live report — PENDING one calibration run (read-only)
The live 60-day scan needs the real inbox 01 over IMAP, which is not reachable
from the headless build env; and the parser CALIBRATION block is built to the
documented notification format but not yet calibrated against a real Wise/Stripe
email. So the report GENERATOR is built and tested, but the live figures await
one hands-on run on the Mac:

```
PYTHONPATH=. /usr/bin/python3 bin/finance-payment-mail            # dry-run: see what parses, calibrate the regexes
PYTHONPATH=. /usr/bin/python3 bin/finance-payment-mail --report   # writes the 60-day table here
PYTHONPATH=. /usr/bin/python3 bin/finance-payment-mail --apply    # store provisional + reconcile (module='system', local-only)
```

### What it is designed to surface (from tonight's manual finding)
Once run, the report will list every parsed notification and flag the ones **ahead
of the ledger** (in email, not yet imported). Tonight's hand-check that motivated
the rail:
- **Adelye — paid Aug 7** — real money, absent from the ledger only because the
  Wise CSV was not imported. The rail will emit a provisional `adelye` row dated
  2026-08-07 → staleness banner fires; AR stops flagging Adelye as overdue.
- **Hermon** — last paid **Jul 15** (Consulting Systems B.V.) — genuinely overdue
  (~24d on Aug 9), confirmed by the ABSENCE of any newer notification. AR flags it.
- **Horizons** — last paid **Jul 3** — genuinely overdue (~36d). AR flags it.

The distinction the rail encodes: a client with a provisional email payment is
"paid, awaiting import" (not overdue); a client with neither email nor ledger is
genuinely overdue. That is the "stated vs observed" law applied to money-in.

## Open for Abhijeet (all reversible; see BUILD_BRIEF ◆ block)
1. The 4 spec defaults taken (AskUserQuestion didn't register): separate table;
   staleness = banner+caveat (not hard-block); build both Wise+Stripe parsers;
   reconcile window = 10d.
2. **One live calibration** of the CALIBRATION regex block against a real Wise and
   a real Stripe email in inbox 01, THEN load the staged plist.
3. Stripe redundancy: Beglaubigt is already on the daily `finance_stripe` rail, so
   Stripe email events will usually confirm-match fast — keep, or drop Stripe email?
