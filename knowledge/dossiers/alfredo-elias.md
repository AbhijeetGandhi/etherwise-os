# Alfredo Elias — Dossier

_Compiled 2026-08-13 17:22 IST · 23 client facts (23 trusted) from 3 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The client's workflow involves sending a physical mailer to leads first, then texting them later; leads should not be texted before receiving the mailer. [CONFIRMED · fathom/85765696 @ 00:04:00 - 'we first send the mailer, so the new list that he sent you to upload would be people that haven't even received a mailer yet, so we want them to get the mailer first before we text them']

## The numbers

- Alfredo sent four lead lists for upload; only one had been uploaded at the time of the call, and the first message was inadvertently sent to those leads before the no-message instruction was understood. [CONFIRMED · fathom/85765696 @ 00:12:11 - 'I think those are four lists, right? So only one was uploaded...when we upload the next three, those will be in a way that they will go into new lead with the tag of new lead']
- Contract: GHL Workflow + KPI Automation — status ACTIVE. [CONFIRMED · CRM:contracts/40747534]
- Revenue to date (GROSS, billed): $4,988.76 across 20 revenue transactions (2025-06-08 → 2025-10-19). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $4,484.87 — gross $4,988.76 less $498.90 Upwork service fees, $4.99 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The client's GHL pipeline currently contains 4,720 opportunities. [CONFIRMED · fathom/85765696 @ 00:08:48 - 'we have 4,720 opportunities']

## People

- Alfredo is a person on the client side who sends lead lists to the agency for upload into GHL and communicates upload/messaging instructions via email. [CONFIRMED · fathom/85765696 @ 00:01:01 - 'I think Alfredo sent an email about it today']
- Chloe Souchere is the client-side contact who manages lead outreach, adds notes in GHL, and handles follow-up calls. [CONFIRMED · fathom/85765696 @ 00:00:04]

## Architecture & systems

- A new workflow will be created to place uploaded leads into a 'New Lead' stage with a 'new lead' tag without sending any message, so leads can be held until ready to message. [CONFIRMED · fathom/85765696 @ 00:03:45 - 'that is something that we will create today that goes on in the new lead...once you want to start sending them, then you can just move them to ready to message.']
- After the accidental first message was sent to the most recently uploaded list, replies were disabled so that even if leads replied, no automated response was sent. [CONFIRMED · fathom/85765696 @ 00:12:03 - 'we actually had disabled the replies, so they just received the first message and not any replies, even if they have sent a reply']
- Bulk pipeline stage changes can be performed via the Contacts section in GHL: filter contacts by tag, select all, then use the 'Pipeline Change' bulk action to move them to a new stage (e.g. 'Ready to Message' in the 'Lead Messaging' pipeline). [CONFIRMED · fathom/85765696 @ 00:07:07 - 'pipeline change, you select the pipeline, which is lead messaging, and then your stage is like ready to message']
- Currently the system automatically sends the first text message upon upload to GHL; there is no existing flow to upload leads without triggering the initial message. [CONFIRMED · fathom/85765696 @ 00:03:38 - 'Right now there is no system for us to just upload to GHL because right now it directly starts sending them the first message.']
- GHL does not support filtering opportunities by pipeline stage for export; users can only sort by stage but cannot apply a stage-based filter, which is a known platform limitation requested by the community. [CONFIRMED · fathom/85765696 @ 00:05:17 - 'the drawback of using Go High Level is for some reason they don't have us filter the leads based on the status or like based on the stage...on their community, everyone's asking for this feature, but it's not there yet']
- Leads are uploaded from Google Sheets into GoHighLevel; the process first creates a contact, then immediately creates an opportunity for that contact. [CONFIRMED · fathom/85765696 @ 00:02:47 - 'when we upload from Google Sheet to GoHighLevel, it is first...to be able to create opportunity, it needs to be a contact in our database first. So we first create the content and right after that we add them to the application.']
- To export leads, the user goes to the Opportunities tab in GHL, clicks the three-dot menu, and exports; the resulting CSV is uploaded to Google Drive and filtered there (e.g. by stage or tag). [CONFIRMED · fathom/85765696 @ 00:08:32 - 00:09:22]

## Stack & tools

- The client uses GoHighLevel (GHL) as their CRM for managing leads and contacts. [CONFIRMED · fathom/85765696 @ 00:04:00]

## Commitments

- Abhijeet committed to setting up a task notification feature that fires whenever a task is created in GHL, sending a notification to the client's Google Calendar or Gmail. [CONFIRMED · fathom/85765696 @ 00:11:06 - 'we can set up that notification of, whenever a task is added, it would go to your Google Calendar or Gmail']
- Two features were noted by Abhijeet to build: (1) new lead tag on upload-only leads, and (2) appointment/task notifications. [CONFIRMED · fathom/85765696 @ 00:11:35 - 'So the new lead tag is there and then notification for the appointments.']

## Other

- Clicking a contact found via reference field search displays the conversations tab and option to view associated opportunities [CONFIRMED · https://www.loom.com/share/02d7e143d1cc48758f0386c25d866c12 @ 1:45-2:30]
- Reference field cannot be searched directly in the opportunity tab; users must search via the contacts tab instead [CONFIRMED · https://www.loom.com/share/02d7e143d1cc48758f0386c25d866c12 @ 0:15-0:35]
- Reference field is stored in additional data and is not visible by default in the contacts view [CONFIRMED · https://www.loom.com/share/02d7e143d1cc48758f0386c25d866c12 @ 3:40-3:55]
- Reference field is uploaded in the contacts system [CONFIRMED · https://www.loom.com/share/02d7e143d1cc48758f0386c25d866c12 @ 0:00-0:15]
- Users can enable the reference column visibility in contacts to search and filter by reference numbers [CONFIRMED · https://www.loom.com/share/02d7e143d1cc48758f0386c25d866c12 @ 2:50-3:10]

## Sources

- `crm/alfredo-elias` · — · crm · CRM numbers — Alfredo Elias
- `fathom/85765696` · 2025-09-08 · fathom · Abhi <> chloe
- `loom/02d7e143d1cc48758f0386c25d866c12` · 2025-09-29 · loom · Searching and Utilizing Reference Fields in Contacts 📊
