# Dhanya Sangolli — Dossier

_Compiled 2026-11-01 05:18 IST · 56 client facts (56 trusted) from 5 sources · 2 agency/other-client facts routed to proof-points._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Abhijeet Gandhi runs an automation agency; Upwork is one of his client acquisition channels, which he started as a freelancer before growing into an agency. [CONFIRMED · fathom/159234042 @ I run an automation agency. So yeah, we do this stuff all the time... Upwork is one of the sources that we have clients from.]
- Client is deploying an AI Voice Coach system (version 2.0) within their organization, using DTMF (keypad-based) interaction and targeting a group of participants. [CONFIRMED · fathom/159234042 @ we're using the DTMF digital, the keypad thing. We have made some changes to that and we are launching it again in our organization.]
- The client (referred to as the Winton project) requires an AI-powered automation to scan conversation transcripts for sensitive/red-flag information (health records, criminal records, social security numbers) and produce a redacted report that omits those red flags. [CONFIRMED · fathom/177622263 @ 00:00:09 – 00:01:17]

## The numbers

- Abhijeet previously built five to seven VAPI agents from scratch for the same VAPI organization account used by Dane/this client. [CONFIRMED · fathom/159234042 @ I think all the previous five, six agents or seven agents were there. It was just like from scratch I've been creating those though.]
- Contract: AI Voice Coach - Vapi, Sheets, Twilio, Zapier — status ACTIVE. [CONFIRMED · CRM:contracts/44052042]
- Contract: AI Voice Coach - Vapi, Sheets, Twilio, Zapier — status CLOSED. [CONFIRMED · CRM:contracts/44043381]
- Contract: Automated Zap - Weekly report — status ACTIVE. [CONFIRMED · CRM:contracts/44379117]
- Revenue to date (GROSS, billed): $570.00 across 3 revenue transactions (2026-07-25 → 2026-09-14). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $512.43 — gross $570.00 less $57.00 Upwork service fees, $0.57 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The client reported that the Twilio A2P registration review took three to five days before a rejection was issued, and resubmissions after seven days are no longer prioritized and treated as new submissions. [CONFIRMED · fathom/159881752 @ 00:01:55 - 00:02:20]
- Twilio number registration/approval typically takes approximately five days. [CONFIRMED · fathom/159234042 @ it takes like five days to like, to just to read [get approved].]

## People

- Client contact is Dhanya, based in Atlanta, USA. [CONFIRMED · fathom/159234042 @ No, I live in Atlanta.]
- Dhanya was referred by a person named Dane, who had previously worked with Abhijeet Gandhi on a VAPI voice agent setup. [CONFIRMED · fathom/159234042 @ I got your referral from Dane that he told me like, good things. You helped him with set up the BAPI call.]

## Architecture & systems

- A context field (sourced from a description-next-to-improvement column in the original sheet) has been added above each improvement quote in the report, to provide background on each improvement suggestion for the staff reader. [CONFIRMED · fathom/180087171 @ 00:06:43 – 00:07:14]
- A known bug where only one row was written to Google Sheets (instead of all staff rows) was resolved by reformatting the AI output into an array of values per column so Zapier's 'Create Multiple Rows' step receives parallel arrays. [CONFIRMED · fathom/177622263 @ 00:06:27 – 00:07:09]
- Abhijeet proposed building an 'end of call' tool/trigger in Vapi so that every time a call ends, the summary and score are automatically written to Google Sheets and sent to the caller via SMS, without requiring the caller to explicitly request it. [CONFIRMED · fathom/159881752 @ 00:22:23 - 00:23:00]
- Abhijeet recommended migrating automation from Google Apps Script to Zapier for better log visibility and easier debugging. [CONFIRMED · fathom/159234042 @ the only reason I would lean towards Zapier is just the visibility that we have over the logs... Zapier is like much easier visibility wise by looking at what went wrong.]
- Client wants to send the call score and summary to participants via SMS (Twilio) after each call ends. [CONFIRMED · fathom/159234042 @ once the call ends, we want to send the score and the summary to the participants.]
- Client's workflow: participants fill out a Google Form (capturing ID, full name, phone number, location), are moved to a 'Ready for Voice Coach' Google Sheet, then receive a voice coaching call. Post-call, the full transcript, score, and summary are stored in Google Sheets via a Google Apps Script webhook. [CONFIRMED · fathom/159234042 @ So we have a Google form that our participants... they sign the form... they will be moved to this ready for voice coach sheet... the full transcript and it's the score summary and all that.]
- Current automation for storing call records and summaries into Google Sheets is handled by a Google Apps Script (not Zapier). [CONFIRMED · fathom/159234042 @ The Sheets, the Google Script. / It's an Apps Script.]
- The 'What Went Well' (highlights) section requires a new Python step to feed an AI step, because it draws from two separate sheets — the main data sheet and a new tab called 'Vinton Report with Highlights'. [CONFIRMED · fathom/180087171 @ 00:04:33 – 'This is going to be another Python step that I think we need to feed to the AI step because it's coming from two separate sheets now']
- The AI module in the Zapier workflow produces per-staff output fields: staff name, week of date, average coaching score, progress summary, and trends; the 'trends' field is not currently written to CougarSuite because no corresponding column exists there. [CONFIRMED · fathom/177622263 @ 00:05:56 – 00:06:15]
- The AI step is instructed to identify the top three most common highlights (from column D) and pair each with a specific example (from column C), mirroring the structure already used for the top three areas of improvement. [CONFIRMED · fathom/180087171 @ 00:05:27 – 00:06:13]
- The Vinton Report with Highlights sheet has approximately 500 rows; the main data sheet has approximately 1,700 rows. Data is filtered by a 'week of date' column and grouped by staff name (column K). Highlights are in column D; specific examples are in column C. [CONFIRMED · fathom/180087171 @ 00:05:00 – 00:05:27]
- The Vinton staff report system generates individual, unique report links per staff member (instead of a shared link accessible to all), so each staff member only sees their own data. [CONFIRMED · fathom/180087171 @ 00:00:53 – 'we've created a unique link for each of the staff that they can access']
- The client has a Google Apps Script ('end of call report') intended to store call summary and score from Vapi into a Google Sheet, but it is not consistently triggering for all call types. [CONFIRMED · fathom/159881752 @ 00:20:50 - 00:21:28]
- The client uses a 'week of the date' column in the Google Sheet to enable single-value lookups that return all rows for a given week; this column was added by the client and proved critical to the workflow design. [CONFIRMED · fathom/177622263 @ 00:04:06 – 00:04:21]
- The client uses a Google Form + Google Sheet flow for ID/consent verification: users fill a form, records are stored in a sheet, and a Google Apps Script checks whether the caller's ID is present in the verified sheet when they call the Vapi assistant. [CONFIRMED · fathom/159881752 @ 00:20:00 - 00:20:44]
- The client uses a Vapi AI voice assistant named 'AI Voice Coach 2.0' with three call options triggered by keypress: press 1 for interview questions, press 2 for role play scenario, and a third option. [CONFIRMED · fathom/159881752 @ 00:17:18 - 00:17:38]
- The client's Twilio registration failure was caused by submitting a Google Form consent URL that was restricted (not publicly accessible); the fix agreed upon is to make the form publicly accessible via link and add explicit SMS consent language to it. [CONFIRMED · fathom/159881752 @ 00:08:00 - 00:08:26]
- The per-staff-member Vinton report link is mapped in Zapier, with Python steps handling the data transformation and mapping. [CONFIRMED · fathom/180087171 @ 00:01:35 – 'it should be mapped in Zapier, typically in the Python stuff']
- The quantitative summary section of the Vinton report includes: number of evaluated interactions (unique calls), this week's average score, previous week's average score, baseline score (from the coaching score field), median score, and score range (bottom and top of this week's scores). [CONFIRMED · fathom/180087171 @ 00:02:35 – 00:03:57]
- The weekly coaching-score automation workflow is built in Zapier and triggers on a Monday schedule (currently set to run at 9 AM per the organization's timezone). [CONFIRMED · fathom/177622263 @ 00:03:30 – 00:03:47]
- Transcripts for the Winton project are currently stored in Salesforce (with reports generated from Salesforce), not in a spreadsheet; the client planned to clarify exact storage details the following week. [CONFIRMED · fathom/177622263 @ 00:01:44 – 00:02:00]

## Stack & tools

- Client has a Twilio account with a purchased number that was rejected, likely due to a URL/registration problem (A2DLC registration process). [CONFIRMED · fathom/159234042 @ I did buy a number in the Twilio website, but I don't know for what reason they rejected the number. It said like some URL problem.]
- Client's tech stack includes: VAPI (voice agent platform), Twilio (SMS), Google Forms, Google Sheets, Google Apps Script. Zapier is not yet set up but is being considered. [CONFIRMED · fathom/159234042 @ right now I've not set up anything in Zapier... everything is working using the script that we wrote on the Excel sheet.]
- The Zapier workflow uses embedded Python scripts (generated with Claude/AI) for: (1) calculating the prior week's date range, (2) reformatting Google Sheets lookup data into a structured dictionary keyed by owner before passing it to the AI module, and (3) formatting AI output into an array of rows for multi-row insertion back into Google Sheets. [CONFIRMED · fathom/177622263 @ 00:03:48 – 00:07:09]
- The client's automation stack includes Vapi (AI voice assistant), Twilio (SMS), Google Forms, Google Sheets, Google Apps Script, and Zapier. [CONFIRMED · fathom/159881752 @ 00:17:00 - 00:17:11]

## Timeline

- Staff-facing Vinton report emails are scheduled to send every Thursday at 5 a.m. Pacific. A second internal preview email is also scheduled every Tuesday at 5 a.m. Pacific, sent only to two internal email addresses, so the team has two days to review and flag issues before the Thursday deployment. [CONFIRMED · fathom/180087171 @ 00:07:28 – 00:08:08]
- Twilio rejected the client's A2P SMS registration on June 23; the stated rejection reason was 'opt-in URL not accessible'. [CONFIRMED · fathom/159881752 @ On June 23, Twilio cannot approve... The reason is opt-in URL not access.]

## Commitments

- Abhijeet committed to reviewing the Vapi assistant setup and Google Apps Script and providing a time estimate for the build by end of day the following day. [CONFIRMED · fathom/159881752 @ 00:24:42 - 00:24:50]
- Abhijeet stated that once Twilio approval is received, the Zapier/SMS flow can be turned on in approximately 15 minutes. [CONFIRMED · fathom/159881752 @ 00:17:11 - 00:17:18]
- Abhijeet will first investigate and fix the Twilio number registration issue before addressing VAPI and Google Sheets integration. Estimated fix time ranges from 15 minutes to 4 hours depending on the issue. [CONFIRMED · fathom/159234042 @ first I'll just focus on the Twilio one... sometimes it can be fixed within like 15 minutes. Sometimes it takes, you know, four hours.]
- Contract and pricing to be determined on Upwork after Abhijeet reviews the Twilio issue and provides a rough estimate; both hourly and fixed-price options were discussed. [CONFIRMED · fathom/159234042 @ are you looking to have it as an hourly contract, or are you looking for a fixed price on this whole setup? / maybe I can take a look first and then give you a rough idea.]
- Cross-client reference: Abhijeet previously set up a post-call summary and SMS send-out workflow for Dane (another client) using a similar VAPI + Twilio setup. [CONFIRMED · fathom/159234042 @ for the DEN, we set up something very similar where, you know, he wanted summaries and everything to be sent out once the call ends, it seems to go out with a number.]
- Dhanya is assigned to follow up with 'Dane' to monitor Zapier token/task capacity to ensure running the Zap twice per week (Tuesday + Thursday) does not exceed the monthly Zapier plan limits. [CONFIRMED · fathom/180087171 @ 00:10:44 – 00:10:58 and 00:13:04 – 00:13:08]
- Target deployment is Monday; the agency (Abhijeet) committed to being available Monday morning for a pre-deployment email automation test, with test emails to be sent to the client's own address and Abhijeet's address before switching to the full staff list. [CONFIRMED · fathom/177622263 @ 00:13:20 – 00:15:49]
- The Tuesday preview email will go to every generated staff report (not just a sample), allowing the internal team to spot-check output before Thursday deployment. [CONFIRMED · fathom/180087171 @ 00:11:07 – 00:11:18]
- The client (Kevin) committed to adding staff email addresses, manager email addresses, and individual baseline coaching scores as new columns in the existing Google Sheet by the next day so the agency (Abhijeet) can map them in the current Zap without requiring an additional lookup step. [CONFIRMED · fathom/177622263 @ 00:12:09 – 00:13:33]
- The client wants each staff member's baseline coaching score (a static historical aggregate) included in the AI-generated summary so it can be compared against the current week's score (e.g. 'baseline 42, this week 46'). [CONFIRMED · fathom/177622263 @ 00:10:24 – 00:11:03]
- The final outstanding automation step to build is sending personalised coaching-score emails to each staff member (and CC their manager); the agency needs the email/baseline columns in the sheet before completing this step. [CONFIRMED · fathom/177622263 @ 00:07:24 – 00:07:51]

## Other

- A coach who helped develop the scoring rubric has flagged inconsistencies in the coaching tab data — specifically that some feedback is not being captured correctly in the emails or is skewing email content. The team plans to review this feedback and spot-check emails against the core report before the next deployment. [CONFIRMED · fathom/180087171 @ 00:11:18 – 00:12:05]
- Client has a separate VAPI project named 'AI Voice Coach 2.0' created for this new version. [CONFIRMED · fathom/159234042 @ we did create, like, a separate project this time... you will see something called as AI Voice Coach 2.0.]
- Dhanya described herself as junior in the organization and deferred to Dane's prior approvals for sharing credentials. [CONFIRMED · fathom/159234042 @ I'm a junior here and like I... If Dane has shared, I'm fine.]
- Kevin's stated approach is to get the system to ~90% complete, deploy, then refine and fine-tune prompts and directions based on real output inconsistencies. [CONFIRMED · fathom/180087171 @ 00:12:36 – 00:12:48]
- The client noted that the Vapi assistant's scoring is inconsistently lenient, giving scores of 4 out of 5 even for incomplete answers; Abhijeet suggested this can be fixed via prompt engineering. [CONFIRMED · fathom/159881752 @ 00:18:17 - 00:18:35]

## Open promises

_From call transcripts; `who` says whose it is. Not the Today tab's list — this is everything still open for this client, including the team's._

**Theirs (awaiting the client)**

- Get proper details on how transcripts are stored in Salesforce and discuss further next week · due 2026-09-04
- Add Abhijeet to the pre-deployment meeting scheduled for next Monday · due 2026-08-31
- Add staff emails, manager emails, and baseline numbers to the same sheet so Abhijeet can map them in the Zap · due 2026-08-28
- Dhanya to follow up with Dane to check Zapier token/zap capacity for sending emails twice a week
- Provide screenshots for the project discussion

## Sources

- `crm/dhanya-sangolli` · — · crm · CRM numbers — Dhanya Sangolli
- `fathom/159234042` · 2026-06-29 · fathom · Impromptu Zoom Meeting
- `fathom/159881752` · 2026-07-01 · fathom · Impromptu Zoom Meeting
- `fathom/177622263` · 2026-08-27 · fathom · Impromptu Google Meet Meeting
- `fathom/180087171` · 2026-09-04 · fathom · Vinton Automation
