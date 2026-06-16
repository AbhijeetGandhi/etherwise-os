# Etherwise — Proof Points & Capabilities

_Compiled 2026-06-17 03:39 IST · 58 agency + 43 cross-client facts (101 trusted)._

> Agency-level knowledge extracted from client calls but NOT specific to one client. Cited; use CONFIRMED / CROSS-VERIFIED for outreach.

## Agency capabilities & method

### architecture
- Agency advises that Claude Chat's Projects feature can be used to upload MD and PRD files so they are accessible across multiple chat sessions without relying solely on conversation memory. [CONFIRMED · fathom/130269236 @ 00:10:10 - 'on the left panel, you will see there is projects... you ask the cloud chat to create multiple documents and add this to the memory and the files you see here']
- Agency advises that Claude Code has a context limit similar to Claude Chat; long single threads cause context loss, so milestone checkpoints should be written to MD files regularly. [CONFIRMED · fathom/130269236 @ 00:04:35 - 'Claude code has the context limit as well. So you cannot just keep going through one thread']
- Agency advises using Claude Code's plan mode for discussion and planning (no code execution), and edit/auto mode only when ready to implement changes. [CONFIRMED · fathom/130269236 @ 00:04:01 - 'there is a plan mode that is asked before editing mode... whenever you're planning something, you got to be in the plan mode']
- Agency recommends creating persistent markdown/context files in the working directory to serve as a knowledge base for Claude Code, preventing context loss across sessions. Suggested folder name: 'context'. [CONFIRMED · fathom/130269236 @ 00:04:24 - 'you want to try and create as many files as you can within your working directory' and 00:13:06 - 'You can create like a folder called as context']
- Agency recommends that after each major milestone or checkpoint in Claude Code, the client should instruct Claude to write a summary document into the context folder so it can be referenced in future sessions. [CONFIRMED · fathom/130269236 @ 00:04:53 - 'whenever you feel like, okay, there was a big milestone or a big checkpoint that we discussed, just say, hey, can we write this down? into a doc']
- Agency's standard discovery method: ask the client to walk through their manual workflow step-by-step (or provide a workflow diagram) before attempting to design any automation. [CONFIRMED · fathom/56778897 @ 00:02:45]
- Agency's voice agent missed-call forwarding approach: configure call forwarding on the client's existing phone provider so unanswered calls forward to the Vapi agent number, allowing the client to retain their existing number. [CONFIRMED · fathom/56778897 @ 00:15:44]
- Every Kixie call webhook includes a recording URL; verbatim transcripts can be obtained by running the recording through Whisper at approximately half a cent per minute as a fallback. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "every call webhook includes a recording URL, so we can run it through Whisper at roughly half a cent per minute as a fallback"]
- Kixie does not expose raw word-for-word transcripts via API; the CI Summary bundle is what is available programmatically and is sufficient for outcome classification. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "Kixie doesn't expose raw word-for-word transcripts via API. The CI Summary bundle is what's available programmatically"]
- Make supports a 'move email to folder/label' automation in Gmail, including the ability to keep moved emails marked as unread. [CONFIRMED · fathom/55505679 @ 00:06:00]
- The AI agent knowledge base can be implemented in two ways: (1) directly entering all information into the prompt, which consumes tokens every call, or (2) using an assistant knowledge base with similarity-based vector search, which retrieves only the top results and is more token-efficient. [CONFIRMED · fathom/55259235 @ 00:04:39 / 'Either it could be that we directly enter all the information into the prom directly...or we can put that into the assistant knowledge base where it doesn't take so much because it is doing first similarity based search']
- The agency has a system that significantly reduces voice agent setup time — from days or weeks down to a very quick turnaround — enabling rapid spinning up of new agents. [CONFIRMED · fathom/131067265 @ 00:03:23 - 00:03:28]
- The agency's email automation workflow (used across multiple clients) now has a reusable AMO template that can largely be copied and adapted for new clients by reconnecting email accounts and Airtable tables. [CONFIRMED · fathom/56272654 @ 00:15:03 – 00:15:54]
- The agency's outbound campaign approach combines LinkedIn and email in a multi-step automated sequence: visit profile → send email after one day → send LinkedIn connection request with note → if accepted, send follow-up LinkedIn message (or voice message); if not accepted, continue email follow-up sequence. [CONFIRMED · fathom/55259235 @ 00:17:14 / 'we first go and visit their profile then after one day we send them the email then once they get the email...after a day we send them the invitation']
- Voice agent integrations are built using Make.com automations as the middleware: the AI agent makes a tool call to a Make.com API endpoint to verify user details against a database and return structured information back to the agent during the live call. [CONFIRMED · fathom/114109770 @ 00:13:45]
- Voice agents can support up to 10 different tool calls per session, including verifying information, creating escalation calls, sending emails, and creating support tickets. [CONFIRMED · fathom/114109770 @ 00:14:17]

### commitment
- Abhijeet Gandhi committed to doing R&D on whether a VAPI-configured Twilio number can still send/receive SMS without breaking, and to report back. [CONFIRMED · fathom/56009113 @ 00:07:00]
- Abhijeet agreed to forward Andrew a voice-agent questionnaire document (covering sample conversations, third-party integrations, agent persona, call flow) that the agency uses for voice agent scoping. [CONFIRMED · fathom/56778897 @ 00:22:00]
- Abhijeet committed to tightening the backend of the cancellation tool over the next two days, as the focus had been primarily on UI up to this point. [CONFIRMED · fathom/130292857 @ 00:13:33]
- Abhijeet confirmed the Airtable client-history feature is technically doable; the approach requires a POC to validate data storage across multiple conversations before quoting. [CONFIRMED · fathom/56272654 @ 00:04:30 – 00:04:52]
- Abhijeet requested reducing daily 30–35 minute calls and moving to async communication (WhatsApp, shared docs, loom-style demo videos) with calls only when necessary. [CONFIRMED · fathom/56778897 @ 00:24:00]
- Abhijeet will share an existing Loom recording of the latest version of the cancellation tool with Michael so it can be shared with Jillian Kelly for her review. [CONFIRMED · fathom/130292857 @ 00:10:56]
- Agency policy going forward: require all clients to use Google Workspace accounts; if necessary the agency will absorb the $10/month cost and offset it against the client's fee. [CONFIRMED · fathom/55015029 @ over time the analysts obviously get more, so I'll just get them to buy space accounts for $10. If anything, I'll just purchase it, I'll just reduce it with FV]
- Andrew Wang expects to have a clearer picture of all upcoming work to hand off to the team by end of Friday. [CONFIRMED · fathom/56009113 @ 00:18:26]

### numbers
- Agency owner Abhijeet claims to have managed 3,000+ production workflows across clients, many involving the pattern of CRM sync + external API + structured logging. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "I've managed 3,000+ production workflows across clients"]
- Andrew Wang spoke to 3–4 people at a networking event who are interested in email automation; the team will be developing those projects. [CONFIRMED · fathom/55505679 @ 00:02:22]
- Daily calls are currently running approximately 30–35 minutes and Abhijeet flagged this as too time-intensive on a daily cadence. [CONFIRMED · fathom/56778897 @ 00:24:44]
- LinkedIn connection request limits restrict outreach to a maximum of 20–25 connection requests per day. [CONFIRMED · fathom/55259235 @ 00:17:14 / 'there is a limit on how many people you can reach out to like with it is like 20 to 25 connection request max a day']
- Setting up the data scraping and enrichment automation pipelines for an outbound campaign takes approximately 10–12 hours of work, typically spread over 2–3 days. [CONFIRMED · fathom/55259235 @ 00:18:34 / 'at least 10 to 12 hours to set up those pipelines for scraping and then enriching the data']

### other
- Andrew Wang instituted a standing process requirement: the agency team must ask him at least one question per meeting/day to increase communication and cross-team familiarity. [CONFIRMED · fathom/56527447 @ 00:07:43 – 00:08:30]
- Andrew Wang noted that approximately 60% of his clients (influencers and creators) use free personal Gmail accounts rather than Workspace accounts. [CONFIRMED · fathom/55015029 @ I reckon it's more 60, 40, that 60% don't use it. Move 40% use it if they've become a part of an agency]
- There is a timezone mismatch between Andrew Wang and the delivery team (Abhijeet and Jay); Andrew's working hours begin when the team is asleep, creating scheduling challenges for client meetings. [CONFIRMED · fathom/55505679 @ 00:00:27]

### overview
- Andrew Wang described a list of multiple prospects interested in automation that he needs to follow up on, indicating a busy near-term pipeline. [CONFIRMED · fathom/56009113 @ 00:17:35]
- Another email automation prospect is expected to sign on Friday. [CONFIRMED · fathom/56009113 @ 00:16:12]
- The agency is building voice AI agents for inbound customer support use cases, with a model where AI handles calls and escalates to a human agent when the situation is too complex. [CONFIRMED · fathom/114109770 @ 00:11:57]
- The agency's model is partnering with businesses to understand their problems and build no-code automation solutions deployable within weeks, rather than building custom platforms. [CONFIRMED · fathom/114109770 @ we are not like building like custom platforms for them like the tech edge is, but it's mainly like through no code]

### people
- Abhijeet Gandhi and Jay Pokharna are team members (likely developers/automation builders) on the agency side. [CONFIRMED · fathom/56009113 @ 00:00:05]
- Abhijeet Gandhi is the agency-side builder/developer demoing the cancellation and scheduling tool to Michael Apfel. [CONFIRMED · fathom/130292857 @ 00:00:49]
- Agency contact is Abhijeet G.; agency email is contact@etherwise.io; agency name is Etherwise. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "here you go: contact@etherwise.io" — 2026-04-23T23:23:40]
- Agency team members sometimes work until 4–5 AM to meet multiple client deadlines on the same day. [CONFIRMED · fathom/57309940 @ "you need to go for like at least four or five in the morning"]
- Andrew Wang is the agency lead; Abhijeet Gandhi (Abby) and Jay Pokharna (J) are the delivery team members on the call. [CONFIRMED · fathom/55505679 @ 00:01:00]
- Andrew Wang is the agency principal/sales lead managing client relationships and onboarding calls. [CONFIRMED · fathom/56009113 @ 00:00:05]
- Call participants are Andrew Wang, Abhijeet Gandhi, and Jay Pokharna. [CONFIRMED · fathom/56272654 @ speaker labels throughout]
- The agency team members on this call are Andrew Wang (account/client-facing), Jay Pokharna (technical), and Abhijeet Gandhi (technical), who left the call early. [CONFIRMED · fathom/57309940 @ Speaker labels throughout; Abhijeet: "I need to actually go to other calls... I'll be back in a few minutes" at 00:02:05]

### quote
- Alexander Apfel on an AI coding tool (referenced as 'plot code'): 'Plot code is the best thing since Plyspread, that's for sure, unbelievable, it's insane.' [CONFIRMED · fathom/131067265 @ 00:01:05]

### stack
- Agency uses GoHighLevel, Zapier, webhooks, and API integrations daily as core toolset; also has experience integrating Vapi and Twilio with CRMs. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "I run an automation agency and work with GoHighLevel, Zapier, webhooks, and API integrations daily" and A2 answer]
- LinkedIn automation tools used by the agency support CRM integration, allowing leads to be automatically pushed to a CRM of choice at defined pipeline stages (e.g., upon invitation acceptance). [CONFIRMED · fathom/55259235 @ 00:18:03 / 'you can actually connect your CRMs there...if let's say they accepted the invitation, they can move them to the CRM of choice']
- Make (formerly Integromat) has an auto-pay plan that automatically adds credits to prevent scenarios from stopping; the team discussed enabling this. [CONFIRMED · fathom/124767903 @ 00:14:50 – 00:14:57]
- The agency has built a voice agent product supported by an AI-powered cloud that scrapes leads daily, enriches data, adds records to a table, and sets next action items — enabling daily outbound cold email operations. [CONFIRMED · fathom/131067265 @ 00:02:52 - 00:03:17]
- The agency primarily uses Make (formerly Integromat) for workflow automation; N8N has been used occasionally, noted for its built-in custom JavaScript code block, but Make is the default tool. [CONFIRMED · fathom/55505679 @ 00:04:58]
- The agency primarily uses no-code and automation tools (including Make.com/Monday.com/Airtable) to build client solutions. [CONFIRMED · fathom/114109770 @ 00:09:29]
- The agency standardised on Airtable (replacing Google Sheets) as the database layer for new automations; Airtable free tier supports up to 10,000 records, after which a paid plan (~$10/month) is required. [CONFIRMED · fathom/55015029 @ if i want you to for the future um like future clients is air table better yeah and spray spray yeah it's free um like for most of the things]
- The agency uses Apollo for lead sourcing but notes its email verification accuracy is only 40–50%, prompting use of additional enrichment tools. [CONFIRMED · fathom/55259235 @ 00:12:26 / 'I've noticed with Apollo that you know the leads that we get...is not really verified So it's like only 40 to 50 percent says it's right']
- The agency uses Instantly as its preferred cold email outreach tool, with Smartreach and Lemlist also mentioned as alternatives. [CONFIRMED · fathom/55259235 @ 00:12:49 / 'We can go with instantly Smart reach Lem list you know all those different tools']
- The agency uses Make.com as the automation platform for building client workflows. [CONFIRMED · fathom/55015029 @ every seven days, I have to go back into make.com and I have to reauthorize her connection]
- The agency uses Perplexity and OpenAI to enrich lead data by pulling company and website information to create personalised emails. [CONFIRMED · fathom/55259235 @ 00:12:40 / 'we throw the perplexity if we throw the open AI into you know Get the latest company information get the website information for those things and then you know create a very personalized Email']
- Twilio was mentioned as a possible underlying infrastructure for the call-forwarding/missed-call-to-agent feature. [CONFIRMED · fathom/56778897 @ 00:10:49]
- Vapi (referred to as 'Bappy' in transcript) is the voice agent platform used by the agency; once a phone number is configured in Vapi for calls, it cannot also be used for manual inbound calls on that same number. [CONFIRMED · fathom/56778897 @ 00:07:00]

## Cross-client proof points

### architecture
- A second client ('carbon group') has an email categorisation automation built with three routes: FAQ, AC (likely account/cancellation), and product-related emails, each routed to a separate AI agent to generate draft responses using OpenAI. The scenario is built but is blocked pending the client providing example/training information. [CONFIRMED · fathom/55015029 @ Jay has already prepared a scenario where we have an AI model to identify whether it's FAQ, whether it's AC, whether it's, you know, a product. Related email and then have three different routes and you know three different agents to generate the responses]
- AI for the sound and lighting prospect would predict labour hours and material quantities for new jobs by referencing previous completed projects as a knowledge base, because field workers consistently under-record timesheets (e.g. logging 1 hour for a 10-hour job). [CONFIRMED · fathom/56272654 @ 00:08:03 – 00:09:10]
- Andrew Wang wants Breath House to use a single phone number for both the AI receptionist (via VAPI) and outbound SMS campaigns; R&D needed to confirm Twilio number can support both simultaneously without the voice configuration hijacking SMS functionality. [CONFIRMED · fathom/56009113 @ 00:06:33]
- Breath House's CRMs have no APIs, so integration is not feasible; the SMS campaign will be a simple one-message blast to cold leads rather than a multi-step workflow. [CONFIRMED · fathom/56009113 @ 00:05:27]
- Carbon Group's Make.com scenario showed no emails processed after 26th March; investigation revealed the client had connected an incorrect or subsequently disconnected email account. [CONFIRMED · fathom/56272654 @ 00:19:00 – 00:21:00]
- Carbon Group's email automation uses a watch module to detect new emails and ChatGPT to categorize them; most emails were being categorised as spam/other, causing the workflow to not proceed further. [CONFIRMED · fathom/56272654 @ 00:01:33 – 00:01:54]
- The sound and lighting prospect has a spreadsheet of materials with unit costs (e.g. TV pricing) and project quotes/descriptions that would serve as the AI's pricing database for invoice generation. [CONFIRMED · fathom/56272654 @ 00:10:45 – 00:11:08]

### commitment
- Andrew Wang is meeting the sound and lighting prospect on Monday to gather detailed requirements before preparing a proposal; Abhijeet will prepare a list of discovery questions needed to produce a quote. [CONFIRMED · fathom/56272654 @ 00:13:43 – 00:14:35]

### numbers
- A recent LinkedIn outreach pilot run by the agency for a client generated 4 leads from 60 outreach contacts, which the agency characterises as a high ROI compared to cold email (roughly 5–7 closes per 1,000 emails sent). [CONFIRMED · fathom/55259235 @ 00:14:39 / 'we only reached out to 60 people on LinkedIn out of those. They got four leads' / 00:14:49 / 'you do thousand emails, you probably see that response from like 50 or 60 people. And then at then you close like five to seven deals']
- Breath House prospect spends approximately $450 per day on ad spend. [CONFIRMED · fathom/56009113 @ 00:01:16]
- For the hiring/payroll client, the payroll process was reduced from 15-20 hours manually every two weeks to approximately 5 minutes (or up to 1 hour if issues arise) by automating technician logs into Airtable and exporting to CSV for the payroll platform. [CONFIRMED · fathom/114109770 @ 00:10:54]
- The breathwork company wants to send approximately 500 emails in the first month and plans to start on Instantly's lowest plan. [CONFIRMED · fathom/55259235 @ 00:15:55 / 'he wants to send 500 emails I'd say just total for the first month so we can go for the lowest plan']
- The email automation for the breathwork client is priced at $160 (described as including fine-tuning). [CONFIRMED · fathom/55259235 @ 00:16:14 / 'you charge me 164, the one with fine-tuning...let's do the fine-tuning as well. So 160 is all for fine-tuning']
- The prisoner training voice agent calls lasted approximately 17-18 minutes per session. [CONFIRMED · fathom/114109770 @ 00:14:54]
- The sound and lighting prospect currently takes two to three months to generate invoices after job completion; the goal of the automation is to significantly reduce that timeframe. [CONFIRMED · fathom/56272654 @ 00:11:44 – 00:11:56]

### other
- The carbon group client has not provided required FAQ/knowledge-base information needed for the AI agents to generate accurate responses, causing a delay; Andrew plans to follow up with the client the next day. [CONFIRMED · fathom/55015029 @ it's not our fault We can't make the deadline because he's giving us the information...I'll follow up with him tomorrow]

### overview
- 'Breath House' is another prospect; Andrew indicated he would update the team by end of week on whether that deal is progressing. The contact is described as keen but awaiting a decision with a partner. [CONFIRMED · fathom/56272654 @ 00:17:04 – 00:17:41]
- A UGC creator client is being onboarded the following day; Andrew Wang will present and connect her system on the call. [CONFIRMED · fathom/56009113 @ 00:14:39]
- A new incoming project involves a breathwork company (similar to pilates/yoga, described as 'a big thing in Australia') that wants a personalised cold outbound email system to reach large corporations; they currently do zero outreach and rely entirely on word of mouth and referrals. [CONFIRMED · fathom/55259235 @ 00:09:58 / 'it's like yoga' / 00:10:47 / 'that company doesn't do any sort of outreach, so it's all through word of mouth and referrals']
- A new prospect is a sound and lighting company that installs speakers, sound, and lighting equipment into events, clubs, and buildings. [CONFIRMED · fathom/56272654 @ 00:06:10 – 00:06:24]
- A prospect (Shaelyn) has an email automation project that is pending due to her slow replies; expected to progress mid-next-week. [CONFIRMED · fathom/55505679 @ 00:02:00]
- A prospect described as a mortgage consultant requires a Pipedrive-similar automation; Andrew was unable to reach her due to illness and plans to follow up next week. [CONFIRMED · fathom/55505679 @ 00:02:45]
- A prospect who is an accountant (currently getting married) also wants the same email automation as Carbon Group; described as near-identical/copy-paste build with different email types; likely to close within two weeks. [CONFIRMED · fathom/55505679 @ 00:07:45]
- A wellness/breathwork company prospect has a proposal submitted; meeting scheduled for next Wednesday; wants two separate email automations — one for current email account and one for a new app launching in approximately two weeks. [CONFIRMED · fathom/55505679 @ 00:08:26]
- Adrian's project is described as 'quite complex' — building an AI to analyze Excel sheets of quantities and materials. [CONFIRMED · fathom/56778897 @ 00:02:07]
- Andrew referenced a 'Better Loans' project as an example of connecting multiple software platforms together, comparable to the sound and lighting prospect's integration needs. [CONFIRMED · fathom/56272654 @ 00:09:10 – 00:09:16]
- Carbon Group is an active client with an email automation workflow built that watches for new emails, categorizes them, and generates draft responses. [CONFIRMED · fathom/56272654 @ 00:01:33]
- Prospect referred to as 'Breath House' is nearly closed; Andrew Wang expects a go-ahead by end of the week pending approval from a business partner. [CONFIRMED · fathom/56009113 @ 00:01:02]
- Shalines is an active client with an email automation workflow; the first follow-up cycle takes approximately five days to trigger, so results cannot be verified immediately. [CONFIRMED · fathom/56272654 @ 00:00:00 – 00:00:29]
- The UGC creator is well-known in the creator space and expects to refer multiple other creators needing similar email automation systems, representing a pipeline of future builds. [CONFIRMED · fathom/56009113 @ 00:15:13]
- The agency has implemented voice AI agents for healthcare clinics in Australia to handle appointment booking calls, including verifying patient details and pulling records from a database during the call. [CONFIRMED · fathom/114109770 @ 00:12:44]
- The agency is currently building automations for a client that involves hiring ~10-12 technicians per week, integrating Indeed applications into Monday.com as CRM, automating interview scheduling, custom form filling, and payroll processing via Airtable. [CONFIRMED · fathom/114109770 @ 00:09:43]
- The agency worked on a voice agent project for prisoners focused on training calls to improve skills and language, with calls lasting 17-18 minutes while the AI agent maintained controlled conversation flow. [CONFIRMED · fathom/114109770 @ 00:14:34]
- The breathwork company has existing relationships with Spotify and Google and wants to use those testimonials in outbound emails to attract other large corporations into their CRM. [CONFIRMED · fathom/55259235 @ 00:11:00 / 'They've already worked with big corporations. Spotify, Google, so they want to use that testimonial stuff as in the email']
- Two builds are scoped for Breath House: (1) hyper-personalized email automation targeting marketing or wellness coordinators at companies, and (2) an SMS campaign for cold leads. [CONFIRMED · fathom/56009113 @ 00:01:33]

### people
- Adrian is a prospective client who wants an AI that can analyze an Excel sheet of quantities and materials; a meeting is planned for Monday. [CONFIRMED · fathom/56778897 @ 00:01:00]
- The breathwork company currently operates with only two people: the owner and a business partner. [CONFIRMED · fathom/55259235 @ 00:16:04 / 'It's just him and a business partner at the moment']

### stack
- The payroll automation for the hiring client uses Airtable as the central data store, with CSV export to the client's payroll platform. [CONFIRMED · fathom/114109770 @ 00:11:08]
- The proposed tech stack for the sound and lighting prospect is HubSpot (lead/deal management) → Scora (job creation and management) → Scora payments/invoicing, plus a Slack integration for new-job notifications. [CONFIRMED · fathom/56272654 @ 00:09:35 – 00:12:23]
- The sound and lighting prospect currently uses Tradify, which has no API, and is planning to migrate to Scora (a field-service/job management platform) which does have an API, enabling automation builds. [CONFIRMED · fathom/56272654 @ 00:06:25 – 00:06:55]
- Twilio is the proposed SMS provider for Breath House's SMS campaign, to be integrated via Make or a similar platform. [CONFIRMED · fathom/56009113 @ 00:05:12]
- VAPI is the voice agent platform being used for the AI receptionist for Breath House. [CONFIRMED · fathom/56009113 @ 00:06:41]

### timeline
- Breath House has an app launching on April 21st; a second email automation for the app will be built after launch data is available. [CONFIRMED · fathom/56009113 @ 00:02:14]
