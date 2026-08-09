# Diat Khan — Dossier

_Compiled 2026-08-09T20:30:38.608503+00:00 · 15 client facts (15 trusted) from 1 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## The numbers

- Make.com affiliate partner program requires $2,000 per month in affiliate income as a prerequisite for the partner program; the team discussed this as a potential path to reduce operational costs by shifting client automations under an affiliate account. [CONFIRMED · fathom/141917008 @ [00:23:28] 'affiliate income should be $2,000 per month' and [00:24:01] '2000 dollars bada amount hai']

## People

- A person named 'Alex' was mentioned as a cross-client example who has one automation that uses a very high number of Make.com operations. [CONFIRMED · fathom/141917008 @ [00:24:01] 'jaise abhi Alex hai, uska ek hai jiska itna jaata hai']
- A person named 'John Dicard' was mentioned; he is not a developer and was invited to the Fort Knox developer environment but is based in a remote office. [CONFIRMED · fathom/141917008 @ [00:22:02] 'John Dicard is not on the developer... He was invited to John Dicard to developer... he is on the remote office']

## Architecture & systems

- A ClickSend SMS validation flow was built: when a contact's call status changes, the system first sends a ClickSend SMS to validate the number; if delivered, it then triggers the Kuo (QR) automation to send the actual message. This prevents invalid numbers from reaching the Kuo flow and avoids duplicate messages. [CONFIRMED · fathom/141917008 @ [00:28:00] 'jab bhi inaka call state change hoga... pehle click send ka SMS jayega... agar deliver hua hai toh... Kuo se bhej dega' and [00:33:31] 'once click send has been used, the Qo automation gets updated, so it checks the click send SMS status before sending']
- An Assembly AI integration is being set up: recording URLs are downloaded and sent to Assembly AI for transcription; a test sheet is currently in use, with actual sheet ID and Assembly AI access credentials still pending. [CONFIRMED · fathom/141917008 @ [00:39:00] 'recording URL se actual recording download hoti hai aur woh apna Assembly AI ko bhej sakte hain' and [00:37:00] 'abhi toh main test wale sheet use kar raha hoon... actual sheet ID aur doosra Assembly aayega access']
- An automation called 'Auto Generate Invoices' was identified as triggering other automations unintentionally; the trigger source was traced to a webhook URL stored in Airtable under an 'Open URL' field (yellow color) referencing a WGL endpoint. [CONFIRMED · fathom/141917008 @ [00:09:27] 'Airtable mein yeh URL kahan par hai... open URL par ja raha hai... yellow mein likhe aaha hai']
- Multiple Make.com scenarios share the same webhook URL, causing unintended cross-triggers between automations (e.g., 'Auto Generate Invoices' triggering unrelated scenarios). [CONFIRMED · fathom/141917008 @ [00:01:33] 'multiple scenarios mein eekhi webhook use ho raha hai']
- Task list for Fort Knox invoice automation includes: (1) initiate Fort Knox new company connection, (2) set up a dedicated automation user, (3) create an automation to generate inter-company invoices with correct rent/hire amount and description, (4) handle no-duplicate logic and logging of created invoices, (5) basic error handling, (6) fix PDF download issue and apply same fix to existing automations. [CONFIRMED · fathom/141917008 @ [00:18:02] 'Fort Knox New Company Connection... Use a new dedicated user... create an automation that then there's an invoice from a new company to the existing one... Make sure it handles no duplicates. Logging of created invoices. Basic error handling. Address the issue you identified with the Fort Knox PDF downloads.']
- The ClickSend automation and the Kuo automation share the same call-disposition trigger; the ClickSend flow adds an extra SMS status verification step to prevent conflicts and duplicate messages when both are active. [CONFIRMED · fathom/141917008 @ [00:34:00] 'same triggers but with different added clicks and status check, just want to make sure they don't conflict when you toggle between them']
- The invoice generation automation runs at 5:30 PM daily and every 5 minutes, and is associated with a 'Fort Knox' folder; there are multiple versions (V1, V2) with known failure issues. [CONFIRMED · fathom/141917008 @ [00:04:29] 'Look, it's 5.30pm daily. Every 5 minutes.' and [00:05:02] 'V2 bhi hai, V1 bhi hai, Markham, woh toh fail hone wala hai']
- The planned invoice flow is company-to-company (new company billing old/existing company), not customer-facing; it does not require a 'create customer' step. The flow will generate a PDF invoice, upload to Google Drive (in a dedicated folder), and send via email. [CONFIRMED · fathom/141917008 @ [00:21:03] 'you don't create customer because it's always going to be the new company to old company' and [00:16:00] 'drive pe bhi upload karenge, toh folder kaun sa rakhna hai']

## Stack & tools

- The team is using Make.com (with webhook modules and scenarios) for automation workflows, and Airtable as a data store where webhook URLs and configuration data are stored. [CONFIRMED · fathom/141917008 @ [00:01:33] 'Mujhe lagta hai multiple scenarios mein eekhi webhook use ho raha hai']

## Commitments

- Jay plans to use Fathom (the meeting tool) to document automation discoveries and build documentation for things identified during debugging sessions. [CONFIRMED · fathom/141917008 @ [00:16:00] 'yeh toh kya yeh Fathom hai na, yeh taal dunga main usme office... documentation jo bhi aisi cheezein pata chal rahi hain']
- Jay will clone the current automation folder and share a workspace/text with Abhijeet; a call is scheduled for the following night to review Assembly AI integration with actual sheet ID and access credentials. [CONFIRMED · fathom/141917008 @ [00:37:00] 'clone kar le aur mujhe utna textspace de... kal call bhi mera raat ko hai']

## Other

- The agency discussed a cold outreach strategy recommendation: avoid cold email outreach currently; focus on LinkedIn and direct calling. To acquire first 2-5 clients for testimonials, offer a 60-day free trial with no payment if no client is generated within that period. [CONFIRMED · fathom/141917008 @ [00:41:00] 'directly 60 days ka free trial offer karo... ek client bhi nahi milta 60 days mein toh no payout... cold outreach bhi mat karo, email... directly LinkedIn, direct calling pe focus karo']

## Sources

- `fathom/141917008` · 2026-04-28 · fathom · Impromptu Google Meet Meeting
