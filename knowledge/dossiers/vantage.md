# VANTAGE — Dossier

_Compiled 2026-08-14 01:12 IST · 15 client facts (15 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## The numbers

- Contract: Automations & Systems | N8N, Make.com, GHL — status ACTIVE. [CONFIRMED · CRM:contracts/40800346]
- Revenue to date (GROSS, billed): $468.00 across 3 revenue transactions (2025-06-15 → 2025-06-29). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $420.73 — gross $468.00 less $46.80 Upwork service fees, $0.47 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- Two SMS messages failed to send in initial testing due to missing phone number configuration [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ So, as you can see right now, it failed, uh, for two]

## Architecture & systems

- GoHighLevel dashboard provides real-time visibility into opportunity status, messaging analytics (delivered, failed, error, received, opted out), and all opportunity details within the pipeline [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ you can see, like, the opportunity status. Uh, you can see, like, you know, in what dates you on a C value...For the SMS settings, we can go to the settings and then, for now. And then, once we go to the phone number, there is this messaging analytics, which will show us, uh, what, you know, like, the sign delivered, failed, uh, error received, opted out]
- GoHighLevel opportunities have customizable pipeline stages; the current demo uses stages like 'New Lead' and 'SMS Send' [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ These are just the, you know, the demo stages that I created in the pipeline. These are not really, like, you know, we can obviously tweak based on, you know, whatever our flow would look like]
- The automation flow: scraper → Google Sheet → second automation creates contacts in GoHighLevel → on contact creation, an opportunity is created and an appointment booking conversation AI bot is triggered → upon appointment booking, opportunity status is updated [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ we have built, like, the ap file scraper...then we'll have this automation run, uhm, again after the first automation has finished executing. And then, you know, put these records as contacts in GoHighLevel...when the contact is created, we create a opportunity, uh, you know, know, send the appointment booking conversation AI bot. And then the appointment is booked. We just update the status accordingly]

## Stack & tools

- GoHighLevel is used as the CRM to store contacts created from scraped records [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ put these records as contacts in GoHighLevel]
- GoHighLevel native connection via OAuth is required; the system distinguishes between company-level and location-level OAuth scopes, and the client must select location rather than company [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ you actually need to, like, select the location, right? So, create a connection. And then when you go, you will need to select, like, you know, the OAuth location rather than the company]
- Make.com is the automation platform being used to orchestrate the workflows [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ I will go into this make scenario]
- SMS messaging is integrated into the workflow; current messaging limit is 100 per day, which unlocks a 250-message tier after daily limit is hit [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ the messaging limit, current limit is 100. We can operate that as we send the 100 messages in a day. It would automatically unlock the 250 messages level for us]
- The appointment booking bot includes customizable personality and additional instructions (prompt) fields that can pull dynamic data (e.g., opportunity name) to personalize messages [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ you can see there is personality and then there is additional instructions...we'll obviously, like fetch a lot of data here. Like, for example, if I go here, then we go to opportunity and then I'll have the opportunity name here]
- The automation uses an API file scraper that runs daily (typically in the morning) to pull ~100 new records into a Google Sheet [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ we have built, like, the, you know, the ap file scraper here. So this would be running, like, you know, every day, like, you know, maybe in the morning and then it gets a hundred new records in the Google sheet]

## Commitments

- Messages will be personalized based on business data once the appointment booking bot is properly configured; currently messages are generic because AI personalization is not yet activated [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ these messages are not personalized. It's because, only because, like, you know, we are not, uhm, yet using the AI. But obviously, all these messages will be personalized based on the data we received for the businesses]
- Next steps include: setting up a valid phone number for the client, testing the workflow to confirm first message delivery, and then configuring remaining SMS and messaging settings [CONFIRMED · https://www.loom.com/share/a1d93da999d9403a8464ed82c283d000 @ let's first set up the, you know, the phone number. So, we can actually make sure, like, this message. It's received correctly]

## Sources

- `crm/vantage` · — · crm · CRM numbers — VANTAGE
- `loom/a1d93da999d9403a8464ed82c283d000` · 2025-06-15 · loom · Videos | Library | Loom - 16 June 2025
