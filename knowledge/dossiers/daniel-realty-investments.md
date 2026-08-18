# Daniel Realty & Investments — Dossier

_Compiled 2026-08-18 18:19 IST · 32 client facts (32 trusted) from 5 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Bulk message automation system is ready with phone lines 1, 2, 3, 4, 5, and 6 configured with numbers set. [CONFIRMED · https://www.loom.com/share/dc2a6f8dd58b40ae93f67efd0796ad75 @ 'bulk message automation where we have, like, all the numbers set for, like, phone 1, 2, 3, and 4, 5, 6']
- Phone lines 4, 5, and 6 are currently disabled and not live yet. [CONFIRMED · https://www.loom.com/share/dc2a6f8dd58b40ae93f67efd0796ad75 @ 'the 4, 5, 6 is currently disabled. It won't be live for now']

## The numbers

- Contract: Automation and scripting for Make, Airtable and third party  clicksend connection — status ACTIVE. [CONFIRMED · CRM:contracts/43601563]
- Contract: Automation and scripting for Make, Airtable and third party connections — status PAUSED. [CONFIRMED · CRM:contracts/42481834]
- Revenue to date (GROSS, billed): $937.91 across 9 revenue transactions (2025-12-28 → 2026-05-12). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $818.17 — gross $937.91 less $118.80 Upwork service fees, $0.94 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]

## Architecture & systems

- Automations for watching inbound and outbound messages have been completed and are ready for deployment [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:03:35 'the other automations for watching inbound and outbound messages are ready as well']
- Bulk SMS flow allows selection of multiple records before triggering send action [CONFIRMED · https://www.loom.com/share/7c0dec52c60043318dead828eb762c02 @ 'whatever records you want to send the SMS to, we will simply select those']
- Bulk SMS flow uses two columns: 'bulk SMS select' for selecting records and 'send bulk SMS' for sending SMS messages [CONFIRMED · https://www.loom.com/share/7c0dec52c60043318dead828eb762c02 @ 'The first column is named as bulk SMS select and second column is named as send bulk SMS']
- In one-by-one approach, system checks phone numbers in priority order (phone one, phone two, phone three) and sends to the first available matching number [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:02:15 'whichever available the earliest that is phone one, phone two, phone three, we check on the priority basis']
- Inbound SMS automation detects opt-out requests (user messaging to stop service) and updates SMS status to 'stop'. [CONFIRMED · https://www.loom.com/share/bb6a3c8b560f4816b4fd16f62a9553c8 @ "In case of inbound, we watch if the user has messaged us to stop the service or stop messaging them. In that case, then we update their SMS status to stop."]
- Inbound message monitoring detects if recipient requests to stop receiving messages [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:03:50 'in case of inbound we watch for the message they sent, if they have asked us to stop messaging them or not']
- Numbers are filtered by conditions: phone line type must not be fixed line or landline, and SMS status column must be empty [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:01:20 'phone line type should not be fixed line and should not be landline. And their SMS status column should be empty']
- Outbound SMS automation monitors delivery status and updates SMS status column based on success, failure, or number issues. [CONFIRMED · https://www.loom.com/share/bb6a3c8b560f4816b4fd16f62a9553c8 @ "In case of outbound, we watch if the message was delivered successfully or not, if there was any failure or if there was any issue with the number and update their SMS status accordingly."]
- Outbound message monitoring tracks whether messages were successfully delivered, and captures failure or issue information [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:03:40 'watching outbound message we watch if the message was successfully delivered or not, if there was any failure or any issue with the number']
- Phone line type field is available as a filtering mechanism to distinguish between mobile, landline, and other phone types. [CONFIRMED · https://www.loom.com/share/dc2a6f8dd58b40ae93f67efd0796ad75 @ 'we would be using this phone line type for filtering out, like, if it is a mobile or a landline or something else']
- SMS sending filters by phone line type (excludes landline/fixed line) and SMS status column (must be empty, not previously sent/stopped/invalid/failed/delivered). [CONFIRMED · https://www.loom.com/share/bb6a3c8b560f4816b4fd16f62a9553c8 @ "the phone line type for the respective number should not be landline or fixed line. And the SMS status column for the respective number should be empty. It should not be sent, it should not be stopped, invalid number, failed or delivered."]
- SMS sending flow includes a 'Bulk SMS Send' column in the interface [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:00:25 'one column named Bulk SMS Send']
- SMS sending system uses ClickSend integration with a Bulk SMS Select column where users mark checkboxes to select records for SMS dispatch. [CONFIRMED · https://www.loom.com/share/bb6a3c8b560f4816b4fd16f62a9553c8 @ "There's one column named Bulk SMS Select. You simply mark the checkboxes and select the records you want to send the SMS to."]
- SMS status column is updated with statuses: 'stop sent', 'invalid number', 'failed', or 'delivered' [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:04:10 'we update the SMS status for the respective number. With the stop sent invalid number. If the failed or delivered']
- System tracks nine distinct SMS status columns, one for each phone number on a record. [CONFIRMED · https://www.loom.com/share/bb6a3c8b560f4816b4fd16f62a9553c8 @ "we have nine different SMS status columns for for all the 9 numbers"]
- The automation requires filtering messages to send only to mobile phone numbers, not landlines. [CONFIRMED · https://www.loom.com/share/dc2a6f8dd58b40ae93f67efd0796ad75 @ 'it should only go to the mobiles, right?']
- Triggering the send action in bulk SMS flow initiates a flow that sends SMS to all selected records [CONFIRMED · https://www.loom.com/share/7c0dec52c60043318dead828eb762c02 @ 'it triggered the flow where all the records that were selected are first, and it will send to all of them']
- Two SMS sending approaches are being evaluated: (1) send SMS to all matching numbers in one batch, or (2) send SMS one-by-one with repeated selection required for each number. [CONFIRMED · https://www.loom.com/share/bb6a3c8b560f4816b4fd16f62a9553c8 @ "Now there are two approaches to be thought of. The one is where we send SMS to all the numbers at once for all the selected records... the other approach we thought of was sending the SMS one by one."]
- Two SMS sending approaches implemented: (1) send to all matching numbers at once, (2) send to numbers one by one based on priority [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:00:50 'Two different approaches. One is to all the numbers at once and the other one is sending to single number one by one']
- User can re-select a record and send again to additional phone numbers if previous attempts were made to other numbers on the same record [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:02:55 'SMS is sent to phone one and phone two and now you want to send it to let's say phone four, you would simply need to go here and select the record again and then press send']
- User selects multiple records and presses send on one selected record to process all selected records at once [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:00:35 'select all the records you want to process and press send on any one of these selected records and it will process all the records selected']

## Stack & tools

- ClickSend is the SMS platform being integrated for sending bulk SMS [CONFIRMED · https://www.loom.com/share/9487871ae43449d3a622e6896e41a803 @ 0:00:15 'flow for sending SMS via ClickSend']

## Commitments

- Client decision pending on which SMS sending approach (batch vs. one-by-one) to implement; other automations for inbound/outbound message watching are ready. [CONFIRMED · https://www.loom.com/share/bb6a3c8b560f4816b4fd16f62a9553c8 @ "please let us know which approach do you prefer and we will proceed with it accordingly. And the other automations for watching the inbound and outbound messages are also ready."]

## Other

- Client has multiple phone number columns available (referenced as 'phone to phone 9') and needs clarification on which column should be used for SMS sending [CONFIRMED · https://www.loom.com/share/7c0dec52c60043318dead828eb762c02 @ 'Now, my first question is exactly which phone number to use from which column, like there are phone to phone 9']
- Client is asking whether SMS sending logic should check phone status fields before sending [CONFIRMED · https://www.loom.com/share/7c0dec52c60043318dead828eb762c02 @ 'is there any other logic based on any of the phone statuses? Like, do we need to check any of those?']
- Question raised on filtering approach: whether to consider only mobile-selected entries or to discard landline entries and include all remaining types. [CONFIRMED · https://www.loom.com/share/dc2a6f8dd58b40ae93f67efd0796ad75 @ 'do we just simply cut out the landlines or simply just consider the ones where mobile is selected']

## Sources

- `crm/daniel-realty-investments` · — · crm · CRM numbers — Daniel Realty & Investments
- `loom/7c0dec52c60043318dead828eb762c02` · 2026-01-26 · loom · Understanding the Bulk SMS Flow Process
- `loom/dc2a6f8dd58b40ae93f67efd0796ad75` · 2026-01-28 · loom · Bulk Message Automation Setup and Mobile Filtering Discussion 📱
- `loom/bb6a3c8b560f4816b4fd16f62a9553c8` · 2026-04-05 · loom · Overview of SMS Sending Setup with ClickSend
- `loom/9487871ae43449d3a622e6896e41a803` · 2026-04-05 · loom · Overview of New SMS Sending Flow via ClickSend
