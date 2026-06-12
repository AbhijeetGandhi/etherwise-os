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
| Outcome capture | Airtable form (prefilled links in Sunday 11AM email) | Airtable automation copies intake → Proposals; composer modules/upwork/outcome_capture.py | internal | **FIRST v3 RAIL — built Day 6, shadow until M1b cutover** | below |

## Rail: outcome-capture (M1b, designed 2026-06-12)
- trigger: weekly Sunday 11:00 IST launchd → outcome_capture.py composes the email (shadow_ledger intent until cutover)
- intake: Airtable "Outcome Submissions" table (form-created rows: linked Proposal [prefilled+hidden], Outcome Reason, Outcome Notes)
- executor: Airtable automation on form submission → writes Outcome Reason/Notes onto the linked Proposals record, marks submission Done
- prefill params: `prefill_Proposal=<airtable_record_id>&hide_Proposal=true&prefill_Source=sunday-email&hide_Source=true`
- form_url: PENDING (Abhijeet creates via Omni agent — prompt delivered Day 6; paste the shr… link here)
- approval tier: internal (his own CRM via his own form — auto-run)
- idempotency: one submission per proposal per week; composer only lists proposals with empty status_reason
- failure routing: composer failure → critical anomaly → notify; automation failure visible as intake rows stuck without Done
| Testimonial capture | nudge → form | Airtable automation | internal | P1 | — |
| Manual transcript drop | knowledge-inbox/ file drop | Python watcher | internal | M3 | — |

Per-rail spec template: trigger · input schema + validation · executor + owner (Make scenario ID / script path) · approval tier · idempotency key · failure routing · last reviewed.
