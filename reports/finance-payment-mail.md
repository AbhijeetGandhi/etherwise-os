# Finance payment-mail rail — build + LIVE CALIBRATION report

**Updated:** 2026-08-09 (night shift) — mailbox bug fixed, parsers **live-calibrated
against real inbox-01 email**, full suite GREEN. `--apply` + plist load still gated
on Abhijeet (writes the live DB; needs migration 023 applied first).

## What this rail is
A read-only rail that reads Wise/Stripe **payment notifications** from inbox 01 and
emits **PROVISIONAL** payment events into their own table (`provisional_payments`),
so a stale CSV import announces itself instead of rendering a confidently-wrong
monthly total (the bug that made Adelye look delinquent — Aug revenue shown $550
while Adelye had paid Aug 7). Provisional rows NEVER enter `transactions`; the CSV
import stays the only ledger writer. Same "stated vs observed" law as the
applied-tile contradiction guard.

## The mailbox bug (root cause, fixed)
`--days 3` returned **"0 payment notification(s) parsed, 0 unparseable"** even
though Adelye's "just paid you" email arrived Aug 7. Cause: the rail selected
`imap_rail.MAILBOX = "INBOX"`, but **Wise/Stripe notifications are archived, not in
INBOX** — a read-only probe found the payment mail only under All Mail, none in
INBOX. Fix:
- Mailbox is now **per-rail**: `imap_rail._uidvalidity(client, mailbox)` takes the
  mailbox; the jarvis client-mail rail keeps INBOX (unread client mail), the
  payment rail reads `config.PAYMENT_MAIL_MAILBOX = '"[Gmail]/All Mail"'`.
- The name is **quoted** — imaplib does not quote a mailbox itself, so the space in
  `[Gmail]/All Mail` makes an unquoted SELECT/EXAMINE a `BAD Could not parse
  command` (verified by inspecting `imaplib.IMAP4._command`).

## Live calibration — 3 things that could NOT be assumed (all found + fixed live)
Read-only probe (EXAMINE + BODY.PEEK) against real inbox-01 mail:
1. **Real Wise amount format** is `"You received 682.50 USD from <payer>"` —
   amount-**then**-currency, and integers carry comma thousands (`"1,615 USD"`). The
   documented `"USD 3,500.00"` form was wrong-order. Wise emails are **HTML-only**
   (no text/plain), so the ~1 KB inline `<style>` CSS block leaked into the body
   text; the Wise amount patterns are now **anchored to "received"** and a
   payment-rail body cleaner strips `<style>/<script>/<head>` first.
2. **Stripe is EUR, not USD, and German uses comma-decimal.** Real receipts:
   English `"Amount paid €0.00"` and German `"Betrag 0,00 €"` / line items
   `"349,00 €"`. The old parser defaulted currency to USD and would have read
   German `349,00` as `34900.00`. Now: currency comes from the €/£/$/₹ symbol or ISO
   code (never a default); `_to_minor` is locale-aware (US `3,500.00` AND German
   `1.234,56` / `349,00`); the German subject `"Ihr Zahlungsbeleg [#…] von
   <merchant>"` is parsed (merchant after "von").
3. **Two live bugs the calibration run itself exposed:**
   - Over All Mail the SEARCH was unfiltered (SINCE only) → a bounded fetch reached
     only the mailbox's **oldest** mail (June 2) and never the recent payments.
     Fixed: the IMAP SEARCH is now **filtered to the payment senders** (nested `OR
     FROM` chain), so the candidate set is tens, not the whole mailbox.
   - Currency read as **"QUE"** on one receipt: under `re.I`, `[A-Z]{3}` grabbed
     "Que" from a trailing "**Que**stions?" and overrode the real `€`. Currency
     codes are now case-sensitive (`(?-i:[A-Z]{3})`).

## Guardrail (learned the hard way — a 60-day fetch wedged twice at 0% CPU)
Every `collect()` now has a **wall-clock cap** (`PAYMENT_MAIL_MAX_SECONDS = 90`):
past the cap the loop stops and the cursor advances only over what was processed
(oldest-first), so a slow mailbox **degrades** (partial, resumes next run) instead
of hanging the sweep. `--max` bounds the fetch on every path (dry-run/apply/report).
The subject-gate also means non-payment senders never trigger a body fetch.

## LIVE 70-day dry-run (read-only, on the Mac — nothing stored)
`bin/finance-payment-mail --days 70 --max 400` → **19 parsed (16 Wise + 3 Stripe),
0 unparseable.** All 19 attributed to the right client; every Wise marketing /
"Your money's been sent" / e-FIRC mail was **skipped, not quarantined**. Ledger
newest income = 2026-08-05, so **1 payment is ahead of the ledger**:

| provider | client | amount | paid | in ledger? |
|---|---|---|---|---|
| wise | hermon | USD 2320.00 | 2026-06-02 | yes/older |
| wise | horizons-aba | USD 2354.00 | 2026-06-05 | yes/older |
| stripe | beglaubigt | EUR 0.00 | 2026-06-09 | yes/older |
| wise | hermon | USD 2615.00 | 2026-06-10 | yes/older |
| stripe | beglaubigt | EUR 0.00 | 2026-06-11 | yes/older |
| wise | adelye | USD 877.50 | 2026-06-12 | yes/older |
| wise | cmf-publishing | USD 2382.60 | 2026-06-15 | yes/older |
| wise | horizons-aba | USD 2497.00 | 2026-06-19 | yes/older |
| stripe | beglaubigt | EUR 0.00 | 2026-06-22 | yes/older |
| wise | adelye | USD 812.50 | 2026-06-26 | yes/older |
| wise | cmf-publishing | USD 1563.70 | 2026-06-30 | yes/older |
| wise | hermon | USD 2820.75 | 2026-07-01 | yes/older |
| wise | horizons-aba | USD 2406.80 | 2026-07-03 | yes/older |
| wise | adelye | USD 1007.50 | 2026-07-12 | yes/older |
| wise | hermon | USD 2975.00 | 2026-07-15 | yes/older |
| wise | adelye | USD 1235.00 | 2026-07-24 | yes/older |
| wise | cmf-publishing | USD 1615.00 | 2026-07-31 | yes/older |
| wise | cmf-publishing | USD 1626.40 | 2026-07-31 | yes/older |
| **wise** | **adelye** | **USD 682.50** | **2026-08-07** | **NO — awaiting import** |

Notes:
- **Adelye Aug-7 $682.50** is the flagship: real money, not yet in the ledger →
  staleness banner fires, AR stops flagging Adelye overdue. (The ledger has since
  caught up to Aug-5, so this is now the single stale row, not the whole month.)
- **CMF twice on Jul-31** ($1,615.00 and $1,626.40) are **distinct payments**
  (different amounts), correctly kept as two rows via Message-ID canonical keys.
- The 3 Stripe rows are **€0.00** (fully-discounted Beglaubigt receipts) — parsed
  correctly but not real money-in; Beglaubigt is also on the daily `finance_stripe`
  rail (redundancy Q still open).

The `--report` generator writes the same table to the workspace `reports/`
(`parents[2]`, the convention shared by all finance report bins); this file is the
tracked build report.

## Tests
`tests/test_payment_mail_rail.py` — **39 tests GREEN** (26 prior + 13 new): real
Wise amount-then-currency + integer thousands; Wise outgoing "money's been sent" is
NOT a payment; Stripe EUR (English + German comma-decimal + German subject + €0.00);
currency never captured from a trailing word ("Que"); locale-aware `_to_minor`;
allow-listed marketing is skipped not quarantined; payment-subject-but-no-amount is
surfaced; mailbox is the configured quoted All Mail; sender-filtered OR-search; the
wall-clock cap bounds the fetch. **Full suite: 917 GREEN.**

## Open for Abhijeet (all reversible)
1. **`--apply` + load the plist is still gated on you** — it writes the live DB.
   Prereq: apply migration 023 (`PYTHONPATH=. /usr/bin/python3 -m core.db`), then
   `bin/finance-payment-mail --apply`, then load
   `launchd/io.etherwise.v3.finance-payment-mail.plist`.
2. Stripe redundancy — Beglaubigt is already on the daily `finance_stripe` rail;
   keep the Stripe-email cross-check or drop it?
3. 4 spec defaults unchanged (separate table; staleness banner not hard-block; both
   parsers; reconcile window 10d).
