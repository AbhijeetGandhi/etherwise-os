# Dhanya Sangolli — Dossier

_Compiled 2026-08-21 14:09 IST · 32 client facts (32 trusted) from 3 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Abhijeet Gandhi runs an automation agency; Upwork is one of his client acquisition channels, which he started as a freelancer before growing into an agency. [CONFIRMED · fathom/159234042 @ I run an automation agency. So yeah, we do this stuff all the time... Upwork is one of the sources that we have clients from.]
- Client is deploying an AI Voice Coach system (version 2.0) within their organization, using DTMF (keypad-based) interaction and targeting a group of participants. [CONFIRMED · fathom/159234042 @ we're using the DTMF digital, the keypad thing. We have made some changes to that and we are launching it again in our organization.]

## The numbers

- Abhijeet previously built five to seven VAPI agents from scratch for the same VAPI organization account used by Dane/this client. [CONFIRMED · fathom/159234042 @ I think all the previous five, six agents or seven agents were there. It was just like from scratch I've been creating those though.]
- Contract: AI Voice Coach - Vapi, Sheets, Twilio, Zapier — status ACTIVE. [CONFIRMED · CRM:contracts/44052042]
- Contract: AI Voice Coach - Vapi, Sheets, Twilio, Zapier — status CLOSED. [CONFIRMED · CRM:contracts/44043381]
- Revenue to date (GROSS, billed): $120.00 across 1 revenue transaction (2026-07-25 → 2026-07-25). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $107.88 — gross $120.00 less $12.00 Upwork service fees, $0.12 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The client reported that the Twilio A2P registration review took three to five days before a rejection was issued, and resubmissions after seven days are no longer prioritized and treated as new submissions. [CONFIRMED · fathom/159881752 @ 00:01:55 - 00:02:20]
- Twilio number registration/approval typically takes approximately five days. [CONFIRMED · fathom/159234042 @ it takes like five days to like, to just to read [get approved].]

## People

- Client contact is Dhanya, based in Atlanta, USA. [CONFIRMED · fathom/159234042 @ No, I live in Atlanta.]
- Dhanya was referred by a person named Dane, who had previously worked with Abhijeet Gandhi on a VAPI voice agent setup. [CONFIRMED · fathom/159234042 @ I got your referral from Dane that he told me like, good things. You helped him with set up the BAPI call.]

## Architecture & systems

- Abhijeet proposed building an 'end of call' tool/trigger in Vapi so that every time a call ends, the summary and score are automatically written to Google Sheets and sent to the caller via SMS, without requiring the caller to explicitly request it. [CONFIRMED · fathom/159881752 @ 00:22:23 - 00:23:00]
- Abhijeet recommended migrating automation from Google Apps Script to Zapier for better log visibility and easier debugging. [CONFIRMED · fathom/159234042 @ the only reason I would lean towards Zapier is just the visibility that we have over the logs... Zapier is like much easier visibility wise by looking at what went wrong.]
- Client wants to send the call score and summary to participants via SMS (Twilio) after each call ends. [CONFIRMED · fathom/159234042 @ once the call ends, we want to send the score and the summary to the participants.]
- Client's workflow: participants fill out a Google Form (capturing ID, full name, phone number, location), are moved to a 'Ready for Voice Coach' Google Sheet, then receive a voice coaching call. Post-call, the full transcript, score, and summary are stored in Google Sheets via a Google Apps Script webhook. [CONFIRMED · fathom/159234042 @ So we have a Google form that our participants... they sign the form... they will be moved to this ready for voice coach sheet... the full transcript and it's the score summary and all that.]
- Current automation for storing call records and summaries into Google Sheets is handled by a Google Apps Script (not Zapier). [CONFIRMED · fathom/159234042 @ The Sheets, the Google Script. / It's an Apps Script.]
- The client has a Google Apps Script ('end of call report') intended to store call summary and score from Vapi into a Google Sheet, but it is not consistently triggering for all call types. [CONFIRMED · fathom/159881752 @ 00:20:50 - 00:21:28]
- The client uses a Google Form + Google Sheet flow for ID/consent verification: users fill a form, records are stored in a sheet, and a Google Apps Script checks whether the caller's ID is present in the verified sheet when they call the Vapi assistant. [CONFIRMED · fathom/159881752 @ 00:20:00 - 00:20:44]
- The client uses a Vapi AI voice assistant named 'AI Voice Coach 2.0' with three call options triggered by keypress: press 1 for interview questions, press 2 for role play scenario, and a third option. [CONFIRMED · fathom/159881752 @ 00:17:18 - 00:17:38]
- The client's Twilio registration failure was caused by submitting a Google Form consent URL that was restricted (not publicly accessible); the fix agreed upon is to make the form publicly accessible via link and add explicit SMS consent language to it. [CONFIRMED · fathom/159881752 @ 00:08:00 - 00:08:26]

## Stack & tools

- Client has a Twilio account with a purchased number that was rejected, likely due to a URL/registration problem (A2DLC registration process). [CONFIRMED · fathom/159234042 @ I did buy a number in the Twilio website, but I don't know for what reason they rejected the number. It said like some URL problem.]
- Client's tech stack includes: VAPI (voice agent platform), Twilio (SMS), Google Forms, Google Sheets, Google Apps Script. Zapier is not yet set up but is being considered. [CONFIRMED · fathom/159234042 @ right now I've not set up anything in Zapier... everything is working using the script that we wrote on the Excel sheet.]
- The client's automation stack includes Vapi (AI voice assistant), Twilio (SMS), Google Forms, Google Sheets, Google Apps Script, and Zapier. [CONFIRMED · fathom/159881752 @ 00:17:00 - 00:17:11]

## Timeline

- Twilio rejected the client's A2P SMS registration on June 23; the stated rejection reason was 'opt-in URL not accessible'. [CONFIRMED · fathom/159881752 @ On June 23, Twilio cannot approve... The reason is opt-in URL not access.]

## Commitments

- Abhijeet committed to reviewing the Vapi assistant setup and Google Apps Script and providing a time estimate for the build by end of day the following day. [CONFIRMED · fathom/159881752 @ 00:24:42 - 00:24:50]
- Abhijeet stated that once Twilio approval is received, the Zapier/SMS flow can be turned on in approximately 15 minutes. [CONFIRMED · fathom/159881752 @ 00:17:11 - 00:17:18]
- Abhijeet will first investigate and fix the Twilio number registration issue before addressing VAPI and Google Sheets integration. Estimated fix time ranges from 15 minutes to 4 hours depending on the issue. [CONFIRMED · fathom/159234042 @ first I'll just focus on the Twilio one... sometimes it can be fixed within like 15 minutes. Sometimes it takes, you know, four hours.]
- Contract and pricing to be determined on Upwork after Abhijeet reviews the Twilio issue and provides a rough estimate; both hourly and fixed-price options were discussed. [CONFIRMED · fathom/159234042 @ are you looking to have it as an hourly contract, or are you looking for a fixed price on this whole setup? / maybe I can take a look first and then give you a rough idea.]
- Cross-client reference: Abhijeet previously set up a post-call summary and SMS send-out workflow for Dane (another client) using a similar VAPI + Twilio setup. [CONFIRMED · fathom/159234042 @ for the DEN, we set up something very similar where, you know, he wanted summaries and everything to be sent out once the call ends, it seems to go out with a number.]

## Other

- Client has a separate VAPI project named 'AI Voice Coach 2.0' created for this new version. [CONFIRMED · fathom/159234042 @ we did create, like, a separate project this time... you will see something called as AI Voice Coach 2.0.]
- Dhanya described herself as junior in the organization and deferred to Dane's prior approvals for sharing credentials. [CONFIRMED · fathom/159234042 @ I'm a junior here and like I... If Dane has shared, I'm fine.]
- The client noted that the Vapi assistant's scoring is inconsistently lenient, giving scores of 4 out of 5 even for incomplete answers; Abhijeet suggested this can be fixed via prompt engineering. [CONFIRMED · fathom/159881752 @ 00:18:17 - 00:18:35]

## Sources

- `crm/dhanya-sangolli` · — · crm · CRM numbers — Dhanya Sangolli
- `fathom/159234042` · 2026-06-29 · fathom · Impromptu Zoom Meeting
- `fathom/159881752` · 2026-07-01 · fathom · Impromptu Zoom Meeting
