# Remotual — Dossier

_Compiled 2026-11-01 05:18 IST · 32 client facts (32 trusted) from 3 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The client discussed in this call is Edgylytics, which runs webinars using Zoom with registration and uses an email marketing platform called Spark (built on top of GoHighLevel) for branded reminder communications. [CONFIRMED · fathom/98858892 @ 00:00:03 / 'The first one is for Edgylytics actually' and 'this email marketing thing that's called Spark']

## The numbers

- Contract: Mastermind of AI Automation — status CLOSED. [CONFIRMED · CRM:contracts/38155369]
- Revenue to date (GROSS, billed): $24,952.63 across 28 revenue transactions (2024-08-27 → 2026-01-16) — Upwork $300.00, direct $24,652.63. [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $24,914.23 — gross $24,952.63 less $30.00 Upwork service fees, $8.40 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]

## People

- Key contacts on this call: Tatiana Reboredo, Abhijeet Gandhi (agency/builder), and Glenn (client-side user learning the automations) [CONFIRMED · fathom/63588387 @ 00:08:21]

## Architecture & systems

- A verification layer exists before sending the offer letter: a Teams notification is sent with a link to review the generated offer letter PDF on OneDrive; the reviewer must click Approve before the offer letter email is sent to the candidate [CONFIRMED · fathom/63588387 @ 00:17:51 – 00:18:28]
- Abhijeet noted the current Zoom-to-GoHighLevel setup is static: the join link is stored in a contact field named after a specific event. He plans to only update the registration URL for the new webinar and flagged that the architecture should be improved in future to be less confusing across events. [CONFIRMED · fathom/98858892 @ 00:14:46 / 'right now this is very static... I'm just going to keep the name as same and just update the registration URL. In future, we got to change that']
- Edgylytics' webinar reminder workflow in GoHighLevel is triggered by a contact tag being added; that tag is applied by a Zapier zap that fires when a new Zoom webinar registrant is created, passing the unique join link into a custom contact field which is then used in reminder email templates. [CONFIRMED · fathom/98858892 @ 00:07:08 / 'there must be another automation that is adding this tag' and 00:12:25 / Zoom zap with Lead Connector found in Zapier]
- Final step: Upon offer letter approval, an automation sends the offer letter PDF as an email attachment to the candidate via Outlook; email is sent from an IGT Colleges account [CONFIRMED · fathom/63588387 @ 00:19:00 – 00:19:35]
- Step 1 of the hiring automation: setting a candidate's status to 'Centuri' in Zoho Recruit triggers creation of a candidate folder in OneDrive and sends a Teams notification asking the approver to click Approve to send the TOD email via Outlook [CONFIRMED · fathom/63588387 @ 00:11:26]
- Step 2: Upon approval in Teams, an automation sends the candidate a Tour of Duty (TOD) email via Outlook from the career center email address, including a prefilled link for the candidate to submit their logged hours and bank details [CONFIRMED · fathom/63588387 @ 00:13:01 – 00:14:18]
- Step 3: When the candidate submits the TOD hours and bank details form, an automation sends a Teams message to the team with candidate name, logged hours, links to the TOD folder and candidate profile, and Accept/Reject buttons [CONFIRMED · fathom/63588387 @ 00:15:00 – 00:15:20]
- Step 4/5: If the candidate is accepted, the reviewer clicks Accept and then fills a 'TOD Offer Letter Form' with position, hourly rate, reporting manager, and start date; this triggers offer letter generation and upload to OneDrive [CONFIRMED · fathom/63588387 @ 00:15:40 – 00:17:00]
- The Zoho status-change 'Centuri' automation is scheduled to run once per hour, not in real time; users can trigger it immediately via 'Run Once' in Make.com [CONFIRMED · fathom/63588387 @ 00:09:12]
- The agency has built two automations for a client referred to in context as 'Dylan's' project: (1) a Google Sheets trigger that creates an Asana project, creates a client record in an unnamed CRM ('EverEver'), and sets up a templated Google Drive folder using an App Script; (2) a Gmail watch automation that detects incoming email attachments, uses an AI/OpenAI step to identify the client, searches for that client's Google Drive folder, and uploads the file there. [CONFIRMED · fathom/98858892 @ 00:17:15 / 'whenever a client detail is added to a Google sheet, we create a SANA project... create a client inside EverEver... set up a Google Drive folder' and 00:19:00 / 'Gmail watch model where it will watch for attachments... open air in between that decide for what client this file is']

## Stack & tools

- All automations are hosted in Make.com under a folder called 'POD automation folder' [CONFIRMED · fathom/63588387 @ So all the automations are here in the POD automation folder]
- Edgylytics uses Zoom for webinar registration (which generates unique per-registrant joining links), GoHighLevel (white-labeled as 'Spark') for CRM/automation workflows, and Zapier for connecting Zoom registrant events to GoHighLevel ('Lead Connector'). [CONFIRMED · fathom/98858892 @ 00:03:04 / 'this is go high level, right?' and 00:12:25 / Zapier Zoom zap found with 'Lead Connector' (GoHighLevel)]
- The agency uses Zapier, GoHighLevel (Lead Connector), Asana, Google Sheets, Google Drive, Gmail, and an OpenAI/AI step ('open air') across client automation builds. [CONFIRMED · fathom/98858892 @ 00:17:15 and 00:19:00 / workflow descriptions referencing these tools]
- The client uses Microsoft OneDrive for storing candidate folders and generated documents (TOD files, offer letter PDFs) [CONFIRMED · fathom/63588387 @ it creates the one folders in OneDrive and sends the message]
- The client uses Microsoft Outlook (career center email account) for sending TOD and offer letter emails to candidates [CONFIRMED · fathom/63588387 @ please click approve to send the QOD mail via Outlook]
- The client uses Microsoft Teams for internal team notifications and approval actions throughout the hiring automation workflow [CONFIRMED · fathom/63588387 @ we send a message in teams once again]
- The client uses Zoho Recruit as their ATS; candidate status changes in Zoho Recruit trigger Make.com automations [CONFIRMED · fathom/63588387 @ this scenario, which is for Zoho status change Centuri runs once in an hour]

## Timeline

- The next Edgylytics webinar is scheduled for the 18th and needs a new Zoom-to-Spark/GoHighLevel connection built for it. [CONFIRMED · fathom/98858892 @ 00:13:49 / 'we need to create a new one that's on the 18th']
- The reminder sequence for Edgylytics webinars is: confirmation on registration, then reminders at three days before, one day before, and one hour before the event. [CONFIRMED · fathom/98858892 @ 00:02:09 / 'I believe it's like three days before, one day before, one hour before']

## Commitments

- Abhijeet committed to sending a Loom video demoing both automations (Google Sheets onboarding trigger and Gmail attachment router) before the next day's meeting with Dylan. [CONFIRMED · fathom/98858892 @ 00:19:24 / 'I can provide you a loom' and 00:19:27 / 'Before you wake up tomorrow with demoing all the two automations']
- Abril (Brie) will build the skeleton, Spark reminder copy/design, and email templates for the new webinar; Abhijeet will connect the Zoom registration to the GoHighLevel workflow (updating the Zapier zap with the new registration URL, keeping changes minimal to avoid risk). [CONFIRMED · fathom/98858892 @ 00:15:42 / 'Bri, you do the skeleton, you'll do the spark update, reminders, whatever, the design and the copy. And then Abhijeet can help us connect it']
- Tatiana will send Abhijeet the correct Google Sheet (the actual client onboarding sheet) so he can connect the onboarding automation to the right source. [CONFIRMED · fathom/98858892 @ 00:18:07 / 'can I send you the correct sheet? Yeah, you can send me the correct sheet']

## Other

- Next steps after a candidate accepts an offer letter have not yet been built into the automation [CONFIRMED · fathom/63588387 @ we haven't kind of fixed the next steps after that, like when the candidate accepts]
- Sending the TOD automation to an existing candidate who has already completed the TOD would be incorrect and must be handled manually for that candidate [CONFIRMED · fathom/63588387 @ 00:21:37 – 00:22:00]
- Tatiana mentioned that the previous webinar for Edgylytics did not go well ('we screwed up last webinar'), motivating the decision to keep changes minimal for the new webinar setup. [CONFIRMED · fathom/98858892 @ 00:15:27 / 'we screwed up last webinar, Abhijeet' and 00:15:34 / 'I won't do any risky changes']
- The automations had not been used for approximately one month before Glenn began using them; Glenn had been triggering them incorrectly and using an outdated video tutorial [CONFIRMED · fathom/63588387 @ 00:04:08 – 00:04:25]
- The offer letter template had been updated/changed prior to this call and the new version needed to be swapped into the automation [CONFIRMED · fathom/63588387 @ 00:18:12 – 00:18:16]

## Sources

- `crm/remotual` · — · crm · CRM numbers — Remotual
- `fathom/63588387` · 2025-05-20 · fathom · Impromptu Microsoft Teams Meeting
- `fathom/98858892` · 2025-11-03 · fathom · Impromptu Microsoft Teams Meeting
