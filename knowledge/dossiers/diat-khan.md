# Diat Khan — Dossier

_Compiled 2026-08-14 00:15 IST · 36 client facts (35 trusted) from 6 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- An automation was built to merge contacts based on phone number matching, triggered by form submissions. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ whenever there is a form submission, it checks whether there is a contact, uh, already existing by the same phone number. And if there is, then it would merge it]
- The contact merge automation now operates in real-time mode. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ we'll also have the automation that, you know, is like real time now]

## The numbers

- Contract: AI Agent Engineer — status CLOSED. [CONFIRMED · CRM:contracts/40883094]
- Make.com affiliate partner program requires $2,000 per month in affiliate income as a prerequisite for the partner program; the team discussed this as a potential path to reduce operational costs by shifting client automations under an affiliate account. [CONFIRMED · fathom/141917008 @ [00:23:28] 'affiliate income should be $2,000 per month' and [00:24:01] '2000 dollars bada amount hai']
- Revenue to date (GROSS, billed): $496.30 across 2 revenue transactions (2025-12-19 → 2026-03-17). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $475.40 — gross $496.30 less $20.90 Upwork service fees. [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]

## People

- A person named 'Alex' was mentioned as a cross-client example who has one automation that uses a very high number of Make.com operations. [CONFIRMED · fathom/141917008 @ [00:24:01] 'jaise abhi Alex hai, uska ek hai jiska itna jaata hai']
- A person named 'John Dicard' was mentioned; he is not a developer and was invited to the Fort Knox developer environment but is based in a remote office. [CONFIRMED · fathom/141917008 @ [00:22:02] 'John Dicard is not on the developer... He was invited to John Dicard to developer... he is on the remote office']

## Architecture & systems

- A ClickSend SMS validation flow was built: when a contact's call status changes, the system first sends a ClickSend SMS to validate the number; if delivered, it then triggers the Kuo (QR) automation to send the actual message. This prevents invalid numbers from reaching the Kuo flow and avoids duplicate messages. [CONFIRMED · fathom/141917008 @ [00:28:00] 'jab bhi inaka call state change hoga... pehle click send ka SMS jayega... agar deliver hua hai toh... Kuo se bhej dega' and [00:33:31] 'once click send has been used, the Qo automation gets updated, so it checks the click send SMS status before sending']
- An Assembly AI integration is being set up: recording URLs are downloaded and sent to Assembly AI for transcription; a test sheet is currently in use, with actual sheet ID and Assembly AI access credentials still pending. [CONFIRMED · fathom/141917008 @ [00:39:00] 'recording URL se actual recording download hoti hai aur woh apna Assembly AI ko bhej sakte hain' and [00:37:00] 'abhi toh main test wale sheet use kar raha hoon... actual sheet ID aur doosra Assembly aayega access']
- An automation called 'Auto Generate Invoices' was identified as triggering other automations unintentionally; the trigger source was traced to a webhook URL stored in Airtable under an 'Open URL' field (yellow color) referencing a WGL endpoint. [CONFIRMED · fathom/141917008 @ [00:09:27] 'Airtable mein yeh URL kahan par hai... open URL par ja raha hai... yellow mein likhe aaha hai']
- Email modules in the Freedo and call SMS automation have an address section where recipients can be added by including multiple items [CONFIRMED · https://www.loom.com/share/a23a5a7ea3404606bee1dfcfc7eda1eb @ 0:00:20 'two address section. In this, add one more item or the number of items. You want to add the emails']
- Multiple Make.com scenarios share the same webhook URL, causing unintended cross-triggers between automations (e.g., 'Auto Generate Invoices' triggering unrelated scenarios). [CONFIRMED · fathom/141917008 @ [00:01:33] 'multiple scenarios mein eekhi webhook use ho raha hai']
- Task list for Fort Knox invoice automation includes: (1) initiate Fort Knox new company connection, (2) set up a dedicated automation user, (3) create an automation to generate inter-company invoices with correct rent/hire amount and description, (4) handle no-duplicate logic and logging of created invoices, (5) basic error handling, (6) fix PDF download issue and apply same fix to existing automations. [CONFIRMED · fathom/141917008 @ [00:18:02] 'Fort Knox New Company Connection... Use a new dedicated user... create an automation that then there's an invoice from a new company to the existing one... Make sure it handles no duplicates. Logging of created invoices. Basic error handling. Address the issue you identified with the Fort Knox PDF downloads.']
- The ClickSend automation and the Kuo automation share the same call-disposition trigger; the ClickSend flow adds an extra SMS status verification step to prevent conflicts and duplicate messages when both are active. [CONFIRMED · fathom/141917008 @ [00:34:00] 'same triggers but with different added clicks and status check, just want to make sure they don't conflict when you toggle between them']
- The invoice generation automation runs at 5:30 PM daily and every 5 minutes, and is associated with a 'Fort Knox' folder; there are multiple versions (V1, V2) with known failure issues. [CONFIRMED · fathom/141917008 @ [00:04:29] 'Look, it's 5.30pm daily. Every 5 minutes.' and [00:05:02] 'V2 bhi hai, V1 bhi hai, Markham, woh toh fail hone wala hai']
- The planned invoice flow is company-to-company (new company billing old/existing company), not customer-facing; it does not require a 'create customer' step. The flow will generate a PDF invoice, upload to Google Drive (in a dedicated folder), and send via email. [CONFIRMED · fathom/141917008 @ [00:21:03] 'you don't create customer because it's always going to be the new company to old company' and [00:16:00] 'drive pe bhi upload karenge, toh folder kaun sa rakhna hai']

## Stack & tools

- Client uses make.com for automation scenarios [CONFIRMED · https://www.loom.com/share/10626e31ed614a2aad9535ad00202528 @ in make.com, it's in their platform itself]
- Client's system has an API explorer accessible via developer settings [CONFIRMED · https://www.loom.com/share/10626e31ed614a2aad9535ad00202528 @ go into developer, then API explorer]
- The automation maintains activity logs and notes during the contact merge process. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ All the activity and everything is maintained, like, along with the notes and everything]
- The client uses an automation named 'Freedo and call SMS' with three email modules [CONFIRMED · https://www.loom.com/share/a23a5a7ea3404606bee1dfcfc7eda1eb @ 0:00:05 'automation named Freedo and call SMS. Here we have three email modules']
- The contact merging automation preserves form submission details, call recording links, and call-related information in a single contact record. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ it has all the details that came through form. Uhm, but then it also has the details of the calls. Like, you know, the, the review recording]
- The team is using Make.com (with webhook modules and scenarios) for automation workflows, and Airtable as a data store where webhook URLs and configuration data are stored. [CONFIRMED · fathom/141917008 @ [00:01:33] 'Mujhe lagta hai multiple scenarios mein eekhi webhook use ho raha hai']

## Commitments

- After adding email recipients to all three email modules, the automation must be saved to apply changes [CONFIRMED · https://www.loom.com/share/a23a5a7ea3404606bee1dfcfc7eda1eb @ 0:00:45 'once you are done adding it to like adding, adding those emails to all three of these email modules, then simply save the automation']
- Client is experiencing invalid JWT token errors in their platform [CONFIRMED · https://www.loom.com/share/10626e31ed614a2aad9535ad00202528 @ we are getting the invalid, uhm, JW token issue]
- Jay plans to use Fathom (the meeting tool) to document automation discoveries and build documentation for things identified during debugging sessions. [CONFIRMED · fathom/141917008 @ [00:16:00] 'yeh toh kya yeh Fathom hai na, yeh taal dunga main usme office... documentation jo bhi aisi cheezein pata chal rahi hain']
- Jay will clone the current automation folder and share a workspace/text with Abhijeet; a call is scheduled for the following night to review Assembly AI integration with actual sheet ID and access credentials. [CONFIRMED · fathom/141917008 @ [00:37:00] 'clone kar le aur mujhe utna textspace de... kal call bhi mera raat ko hai']
- The automation is ready to be applied to all existing contacts to eliminate duplicates once the client confirms the current test output. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ if everything looks good that I could just go ahead and run this for, you know, all the contacts and make sure there are no duplicates]
- The client needs two changes: CAPTCHA must be removed from the meeting link, and the slug must be changed from a multi-part format (slash separated) to a single slug without slashes for HubSpot API compatibility [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:03:00 'We need two things changed. One is the, uh, the set of the captcha and second one is the slug that we have here should be just one slug. thing without the slash in between as it messes up with the HubSpot API']

## Other

- API explorer testing in sandbox account shows the same invalid JWT token error as the production issue [CONFIRMED · https://www.loom.com/share/10626e31ed614a2aad9535ad00202528 @ execute the request in the sandbox account... you can see it also gives the error]
- The agency discussed a cold outreach strategy recommendation: avoid cold email outreach currently; focus on LinkedIn and direct calling. To acquire first 2-5 clients for testimonials, offer a 60-day free trial with no payment if no client is generated within that period. [CONFIRMED · fathom/141917008 @ [00:41:00] 'directly 60 days ka free trial offer karo... ek client bhi nahi milta 60 days mein toh no payout... cold outreach bhi mat karo, email... directly LinkedIn, direct calling pe focus karo']
- The client has three meeting links in their output: a test link, one for the Azul application, and one for the AI business consultation [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:00:00 'we have three different links here. One is a test one that we created, one for the Azul application. And then one is for the actual, the AI business consultation link']
- The meeting link slug is formatted as slash AI business slash, with a slash in between that breaks HubSpot API requests [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:01:05 'the slug is set up. It is, I mean, it's the same slash AI business, and then the slash, there is nothing on the internet. That allows, you know, how to handle that while making a request']
- The meeting link website has CAPTCHA enabled, which prevents the HubSpot API from sending CAPTCHA tokens and retrieving needed details [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:01:25 'this website has a capture enabled. And if you look into the documentation, it says that the following features are being supported with the Mutancy API and one is sending the capture tokens. You cannot really send the capture tokens and get the details that we need']
- The meeting links use a custom domain that cannot be used within HubSpot [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:00:40 'there is the custom domain that is set up, which we can't really use within the, uh, HubPod']
- When the platform's JWT token issue is fixed, API queries will return results and make.com scenarios will run fine [_INFERRED_ · https://www.loom.com/share/10626e31ed614a2aad9535ad00202528 @ When the platform is working correctly, this does return the result... when they fix the issue, this will show up the results... you will see the make scenarios will run fine as well]

## Sources

- `crm/diat-khan` · — · crm · CRM numbers — Diat Khan
- `loom/10626e31ed614a2aad9535ad00202528` · 2025-09-24 · loom · Troubleshooting Invalid JW Token Issues in API Requests 🔧
- `loom/02e7674808694e91b2900d5597bd5584` · 2025-10-20 · loom · Videos | Library | Loom - 20 October 2025
- `loom/8e61dd6b687148b380c055aa87e23850` · 2025-12-02 · loom · Meeting Link Issues and Required Changes 🔗
- `loom/a23a5a7ea3404606bee1dfcfc7eda1eb` · 2026-04-08 · loom · How to Add Email Recipients Easily ✉️
- `fathom/141917008` · 2026-04-28 · fathom · Impromptu Google Meet Meeting
