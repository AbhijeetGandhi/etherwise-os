# Homestyling — Dossier

_Compiled 2026-08-10 10:39:09.479517+00:00 · 14 client facts (13 trusted) from 3 sources · 62 agency/other-client facts routed to proof-points._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Client operates two different businesses: Homestyling.se and Inredit [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'we have two different businesses, like the Homestylingse, Styling and Inredit']
- The client is running Facebook/Meta ads, likely targeting Swedish-speaking users (Airtable fields are in Swedish), and the goal is to close the feedback loop so Meta knows which leads actually purchased, improving ad targeting. [CONFIRMED · fathom/150793211 @ 00:02:11]

## The numbers

- Client receives more than hundreds of leads every week [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'we have more than hundreds of leads coming every, every week']

## Architecture & systems

- An automation is being built to trigger when a lead's status changes to 'Homestyling KOPT' (homestyling purchased) in Airtable, which will then fire a Meta Conversions API (CAPI) event to send purchase conversion data back to Meta/Facebook. [CONFIRMED · fathom/150793211 @ 00:01:33]

## Stack & tools

- Client uses Airtable as the database for lead management [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'Airtable is getting updated, with whatever the message that we send, the reply that we got from the customer']
- Client uses Brevo to send immediate messages to new leads [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'this client uses Brevo to send out the message']
- Client uses Meta Ads heavily with multiple Meta Lead forms running simultaneously [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'This is one of the clients that I've worked with and they use meta ads heavily']
- Client uses OpenAI assistant for generating lead responses based on business knowledge [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'we use the OpenAI, we have this assistant created, which knows all about the business and knows how to send the response']
- Client uses Talk.to as a unified messaging platform to connect email, SMS, and WhatsApp [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'Talk.to is a platform, basically, that we use, for example, this is the platform that we use to connect, you know, the different sources, whether it is email, SMS']
- The automation is being built in Make.com using the Facebook Conversions API 'Send Event' module, connected to the client's existing Airtable and Meta Ads account. [CONFIRMED · fathom/150793211 @ 00:02:33]

## Timeline

- Client handles over 100 leads within 3 hours on a typical day (June 20) [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'on the June 20, which is like just three hours ago, there are like more than you know a hundred leads already']

## Commitments

- Abhijeet (agency) will notify the client (mish) once the Make.com / Meta CAPI setup is complete; agency already has access to the client's Meta Ads platform. [CONFIRMED · fathom/150793211 @ 00:05:53]

## Other

- During live setup a technical error occurred when connecting the Facebook Conversions API module in Make.com; Abhijeet planned to retry after the call. [CONFIRMED · fathom/150793211 @ 00:05:32]
- The client's business appears to be based in Stockholm, Sweden, and operates a homestyling service sold via Meta ads. [_INFERRED_ · fathom/150793211 @ 00:04:25]

## Sources

- `fathom/150793211` · 2026-05-29 · fathom · Impromptu Google Meet Meeting
- `loom/2558f580bf3846eebb9095db4217c70b` · 2026-06-10 · loom · Senior Automation Engineer, Make, GoHighLevel, OpenAI
- `loom/6703f8e402264ab796597e321cc754c4` · 2026-06-19 · loom · Meta Ads Lead Response Automation, Built and Proven
