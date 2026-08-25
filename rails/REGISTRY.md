# Rail Registry
Every repeatable business operation is a rail: ONE structured entry point (Airtable form/record or agent-filed record) → validation → approval gate → deterministic executor → status writeback → nudge. Agents gather inputs and file records; they never execute. (Architecture §4b, decisions #21–23.)

**Status flow:** `Requested → Approved → Executing → Done | Failed` (external tier needs the human flip to Approved; internal tier auto-runs on valid input).

| Rail | Intake | Executor | Tier | Status | Spec |
|---|---|---|---|---|---|
| Offer letter → eSignature | HR base · Offers | Make.com (existing flow, formalized) | external | v2-live, registry-pending | rails/offer-letter.md (M-HR) |
| Contractor agreement → eSignature | HR base · Offers | Make.com (existing) | external | v2-live, registry-pending | rails/contractor-agreement.md |
| Onboarding instantiation | HR base · Candidates (Stage=Hired) | Airtable automation (existing) | internal | v2-live, registry-pending | — |
| Payroll run | Payroll base · Payroll Runs | Python (scripts exist) + Razorpay | external | v2-live, M5 | rails/payroll-run.md |
| Statement import commit | Cockpit import view | Python watcher | internal | v2-live (cockpit flow), M5 | — |
| Invoice reminder → send | CRM · (new) | Make.com | external | M5 | — |
| CA monthly pack | schedule-triggered | Python | internal | M5 | — |
| Proposal logging | agent/scanner-filed | Python | internal | M1 | — |
| Outcome capture | Airtable form (prefilled links in Sunday 11AM email) | Airtable automation copies intake → Proposals; composer modules/upwork/outcome_capture.py | internal | **FIRST v3 RAIL — built Day 6; SENDS since 2026-08-25** (the live branch was a guard with no send call until then — finding 14, Upwork chat) | below |

## Rail: outcome-capture (M1b, designed 2026-06-12)
- trigger: weekly Sunday 11:00 IST launchd → outcome_capture.py composes AND sends the email via core.notify.send_gmail (shadow_ledger intent while the upwork module is shadowed; a failed send fails the run → critical anomaly; the same week is never sent twice)
- intake: Airtable "Outcome Submissions" table (form-created rows: linked Proposal [prefilled+hidden], Outcome Reason, Outcome Notes)
- executor: Airtable automation on form submission → writes Outcome Reason/Notes onto the linked Proposals record, marks submission Done
- prefill params: `prefill_Proposal=<airtable_record_id>&hide_Proposal=true&prefill_Source=sunday-email&hide_Source=true`
- form_url: https://airtable.com/appgE1QoEOXvbrUE4/pag9IZ4lgwmIZA47g/form (Airtable form page "Proposal Outcome" on Outcome Submissions, verified rendering 2026-08-25; the prefill params below apply to this page URL — check the first Sunday email's link lands with the Proposal filled)
- approval tier: internal (his own CRM via his own form — auto-run)
- idempotency: one submission per proposal per week; composer only lists proposals with empty status_reason
- failure routing: composer or send failure → critical anomaly → notify (the cockpit's `/api/rail outcome-capture` button is the deliberate re-send; the rail is `catch_up: False` in config.SOURCES, so bin/catch-up never re-fires it); automation failure visible as intake rows stuck without Done
| Testimonial capture | nudge → form | Airtable automation | internal | P1 | — |
| Manual transcript drop | knowledge-inbox/ file drop | Python watcher | internal | M3 | — |

Per-rail spec template: trigger · input schema + validation · executor + owner (Make scenario ID / script path) · approval tier · idempotency key · failure routing · last reviewed.
