# Dhanya Sangolli — Dossier

_Compiled 2026-08-14 01:12 IST · 15 client facts (15 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## The numbers

- Contract: AI Voice Coach - Vapi, Sheets, Twilio, Zapier — status ACTIVE. [CONFIRMED · CRM:contracts/44052042]
- Contract: AI Voice Coach - Vapi, Sheets, Twilio, Zapier — status CLOSED. [CONFIRMED · CRM:contracts/44043381]
- Revenue to date (GROSS, billed): $120.00 across 1 revenue transaction (2026-07-25 → 2026-07-25). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $107.88 — gross $120.00 less $12.00 Upwork service fees, $0.12 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The client reported that the Twilio A2P registration review took three to five days before a rejection was issued, and resubmissions after seven days are no longer prioritized and treated as new submissions. [CONFIRMED · fathom/159881752 @ 00:01:55 - 00:02:20]

## Architecture & systems

- Abhijeet proposed building an 'end of call' tool/trigger in Vapi so that every time a call ends, the summary and score are automatically written to Google Sheets and sent to the caller via SMS, without requiring the caller to explicitly request it. [CONFIRMED · fathom/159881752 @ 00:22:23 - 00:23:00]
- The client has a Google Apps Script ('end of call report') intended to store call summary and score from Vapi into a Google Sheet, but it is not consistently triggering for all call types. [CONFIRMED · fathom/159881752 @ 00:20:50 - 00:21:28]
- The client uses a Google Form + Google Sheet flow for ID/consent verification: users fill a form, records are stored in a sheet, and a Google Apps Script checks whether the caller's ID is present in the verified sheet when they call the Vapi assistant. [CONFIRMED · fathom/159881752 @ 00:20:00 - 00:20:44]
- The client uses a Vapi AI voice assistant named 'AI Voice Coach 2.0' with three call options triggered by keypress: press 1 for interview questions, press 2 for role play scenario, and a third option. [CONFIRMED · fathom/159881752 @ 00:17:18 - 00:17:38]
- The client's Twilio registration failure was caused by submitting a Google Form consent URL that was restricted (not publicly accessible); the fix agreed upon is to make the form publicly accessible via link and add explicit SMS consent language to it. [CONFIRMED · fathom/159881752 @ 00:08:00 - 00:08:26]

## Stack & tools

- The client's automation stack includes Vapi (AI voice assistant), Twilio (SMS), Google Forms, Google Sheets, Google Apps Script, and Zapier. [CONFIRMED · fathom/159881752 @ 00:17:00 - 00:17:11]

## Timeline

- Twilio rejected the client's A2P SMS registration on June 23; the stated rejection reason was 'opt-in URL not accessible'. [CONFIRMED · fathom/159881752 @ On June 23, Twilio cannot approve... The reason is opt-in URL not access.]

## Commitments

- Abhijeet committed to reviewing the Vapi assistant setup and Google Apps Script and providing a time estimate for the build by end of day the following day. [CONFIRMED · fathom/159881752 @ 00:24:42 - 00:24:50]
- Abhijeet stated that once Twilio approval is received, the Zapier/SMS flow can be turned on in approximately 15 minutes. [CONFIRMED · fathom/159881752 @ 00:17:11 - 00:17:18]

## Other

- The client noted that the Vapi assistant's scoring is inconsistently lenient, giving scores of 4 out of 5 even for incomplete answers; Abhijeet suggested this can be fixed via prompt engineering. [CONFIRMED · fathom/159881752 @ 00:18:17 - 00:18:35]

## Sources

- `crm/dhanya-sangolli` · — · crm · CRM numbers — Dhanya Sangolli
- `fathom/159881752` · 2026-07-01 · fathom · Impromptu Zoom Meeting
