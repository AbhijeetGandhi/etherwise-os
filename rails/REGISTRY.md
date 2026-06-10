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
| Outcome capture | ClickUp drag / nudge response | Python | internal | M1 | — |
| Testimonial capture | nudge → form | Airtable automation | internal | P1 | — |
| Manual transcript drop | knowledge-inbox/ file drop | Python watcher | internal | M3 | — |

Per-rail spec template: trigger · input schema + validation · executor + owner (Make scenario ID / script path) · approval tier · idempotency key · failure routing · last reviewed.
