# Brendan Jansen — Dossier

_Compiled 2026-11-02 05:16 IST · 37 client facts (37 trusted) from 3 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The automation system has two main parts: (1) prospecting job posts based on filters, and (2) searching for recruiters at companies posting those jobs. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'We have mainly, like, you know, set up the two parts']

## The numbers

- After cost optimization, the scraping automation was reduced to approximately $0.50 per job scraped (yielding ~20 contacts). [CONFIRMED · fathom/98594850 @ 00:17:05]
- An early version of the contact-scraping automation cost $1 per 20 contacts scraped (i.e. ~$25 per 100 jobs / 2,000 contacts). [CONFIRMED · fathom/98594850 @ 00:16:36]
- Contract: AI Cold Outreach agent — status CLOSED. [CONFIRMED · CRM:contracts/41233897]
- Contract: VAPI AI agent — status CLOSED. [CONFIRMED · CRM:contracts/40856821]
- Job post data extracted includes salary, job title, job URL, company name, company URL, location, posting time, and application count. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'we get the salary, we get the title, uh, we get the job URL, the company name, the company URL location... posted time, application count']
- Job search is currently scoped to locations in New York. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'all are in the New York now']
- OpenAI provides a confidence score indicating how confident it is that a profile matches the recruiter criteria. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'there is this confidence of OpenAI to, you know, uh, like, you know, how confident it is to saying that, you know, this was the right person']
- Revenue to date (GROSS, billed): $150.00 across 1 revenue transaction (2025-09-29 → 2025-09-29). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $120.00 — gross $150.00 less $30.00 Upwork service fees. [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- Sending 6,000 emails with personalized videos was estimated to cost approximately 2,000 euros at the evaluated Kling API pricing. [CONFIRMED · fathom/98594850 @ 00:05:22]
- Switching from VideoGen API to the Kling direct API was estimated to take approximately 3–4 hours of rebuild work. [CONFIRMED · fathom/98594850 @ 00:02:52]
- The client's target email volume by end of year is 6,000–7,000 emails. [CONFIRMED · fathom/98594850 @ 00:15:32]

## People

- Abhijeet Gandhi is the agency-side lead on this project; a team member named Mikey also worked on the automation build. [CONFIRMED · fathom/98594850 @ 00:16:54]
- Brendan Jansen op de Haar is the client; he flagged that project delays are causing him to miss his year-end targets. [CONFIRMED · fathom/98594850 @ 00:12:08]

## Architecture & systems

- Job post prospecting uses filters for job titles, max job level, and location; additional filters can be added based on client request. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'we added only a few filters, like, you know, the job titles and the max job and then the location. Obviously, you can let us know what filters you want']
- OpenAI is used to apply additional filtering to recruiter results to improve match quality and reduce unsuitable candidates. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'we were getting too many people, like, you know, and then those were mostly not 100% suitable, so we have added OpenAI here... OpenAI can use additional filtering']
- The automation system includes at least two distinct automations: (1) scraping contacts from job listings, and (2) a video generation/email outreach pipeline. [CONFIRMED · fathom/98594850 @ 00:12:43]
- When new data is added to the job posts table, an automation triggers to search for recruiters at those companies. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'whenever there is a new data in this table, right, we, this, this automation will run']

## Stack & tools

- Filtered recruiter data is written to a Google Sheet in a 'Contacts' tab. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'we finally, you know, write these recruiters into the Google Sheet... in, in here, you can see there is this second tab called Contacts']
- Kling 2.5 has its own direct API that could be used as a fallback if VideoGen API remains unavailable, producing equivalent results. [CONFIRMED · fathom/98594850 @ the Kling actually has their own API, so like, you know, we don't need to use, like, you know, VideoGen API to use this model]
- Recruiter search uses an Apify actor for LinkedIn company and profile bulk search, targeting HR and Talent Acquisition roles. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'this automation runs another AP5 actor for the job. Which is for LinkedIn companies and profile bulk search... we send the titles, like, HR and Talent Acquisition']
- The client's automation pipeline uses VideoGen API as a unified platform to access AI video generation models including Kling and Sora. [CONFIRMED · fathom/98594850 @ we are using like Kling 2.5 model to generate these videos, right? And obviously what videos in API, what this platform is doing is like, you know, you just provide us work unified platform to access different AI models]
- The current video generation model in use is Kling 2.5, accessed via the VideoGen API. [CONFIRMED · fathom/98594850 @ 00:01:25]
- The system uses Make.com and Apify for automation; Apify costs $30 per month per actor due to LinkedIn data access requirements. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'right now we are using like, you know, our make.com and then, you know, this API as a, as a, as a tool, but this API actually, that costs like, you know, $30 a month each to run... if you want to get, like, you know, the trust-fed results, we have to pay a certain price']
- The system uses an API actor to retrieve job posts from the LinkedIn platform via Apify. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'we use this API actor to, you know, uhm, get the, you know, the job post from the platform called AP file... this is mainly regarding the LinkedIn']

## Commitments

- Abhijeet committed to proactively communicating blockers, delays, and difficulties to the client rather than staying silent when behind schedule. [CONFIRMED · fathom/98594850 @ 00:18:46]
- Client expressed interest in expanding the engagement beyond email outreach into machine learning / AI work in future phases. [CONFIRMED · fathom/98594850 @ 00:15:49]
- Client needs to provide access to required tools and credentials for setup; a follow-up call is planned to configure logins and accounts. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'if you could give us the access to these tools that we'll need, uh, maybe we can get on a call and I can help you']
- Decision made to immediately begin sending outreach emails without video (no video attached) while waiting up to 48 hours for VideoGen API to recover, after which the team will decide on a permanent alternative. [CONFIRMED · fathom/98594850 @ 00:08:18]
- Next step involves setting up email campaigns using Instantly with email templates, once client provides login credentials. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'the next step of like, you know, creating the campaigns and stuff... once you put in the mail with the instantly login. And, you know, the templates you want for the email']
- Payment will be handled via Wise invoice rather than through Upwork, as the Upwork contract has already lapsed due to inactivity. [CONFIRMED · fathom/98594850 @ 00:18:00]

## Notable quotes

- Client stated: 'If this works, I think we can sell it to more people.' [CONFIRMED · fathom/98594850 @ 00:15:16]
- Client stated: 'The year is ending, and I'm probably not going to hit my target anymore. So it has real consequences.' [CONFIRMED · fathom/98594850 @ 00:12:08]

## Other

- Abhijeet acknowledged the team has limited experience specifically in lead generation automation, which contributed to underestimating complexity and delays. [CONFIRMED · fathom/98594850 @ 00:13:23]
- Apify job search scraping is time-intensive and not demonstrated live; results shown are from previous automated runs. [CONFIRMED · https://www.loom.com/share/780cb77282974686aa88d7707d07dd91 @ 'I would have loved to, like, you know, demo it on call, but it just, you know, takes time to, you know, scrape these details']
- VideoGen appears to be operated by a single developer based in France, running from a personal Gmail account, raising concerns about reliability. [CONFIRMED · fathom/98594850 @ 00:00:27]

## Sources

- `crm/brendan-jansen` · — · crm · CRM numbers — Brendan Jansen
- `loom/780cb77282974686aa88d7707d07dd91` · 2025-08-18 · loom · Automating Job Prospecting and Recruitment Strategies 🚀
- `fathom/98594850` · 2025-11-03 · fathom · Impromptu Google Meet Meeting
