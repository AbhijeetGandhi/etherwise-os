# Amanda Thomas — Dossier

_Compiled 2026-08-13 17:22 IST · 18 client facts (18 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The client's social media approval workflow goal is to bulk-approve 20–30 days of posts in advance, with occasional one-off posts on a 24-hour approval turnaround, rather than the current reactive one-at-a-time process. [CONFIRMED · fathom/171833723 @ 00:10:06 – 'the goal here is to bulk approve, say, 20 or 30 days of social posts in advance, and then to just have occasional one offs that would be within a 24 hour approval']

## The numbers

- Adding Amanda as an approver in Sprout Social would cost approximately $6,000, which the client considers prohibitively expensive and wants to avoid. [CONFIRMED · fathom/171833723 @ 00:10:36 – 'We're talking $6,000 for me to be able to click the approve button']
- Contract: Airtable Configuration and Automation — status ACTIVE. [CONFIRMED · CRM:contracts/44145310]

## People

- Josh owns the content admin role for Airtable tag governance — any new tag values require his approval before being added. [CONFIRMED · fathom/171833723 @ 00:02:41 – 'without admin approval, which would be Josh' / 00:02:51 – 'Josh is the only one who can add those']

## Architecture & systems

- A planned Airtable-to-WordPress automation will populate a standard landing page template (fields: eyebrow, H1, H2, body copy, form code) from Airtable data and publish to WordPress via a manual button trigger — not automatically on field update — to prevent accidental live publishing. [CONFIRMED · fathom/171833723 @ 00:04:19 – 'Can we work together on automating what is in Airtable to replicate over into WordPress?' / 00:06:24 – 'We'll give you like a button to actually say, okay, now the content is good. Now you hit the button and then it gets published']
- Airtable column visibility will be used to hide internal columns (e.g. 'owner') from certain users rather than deleting those columns, to preserve workflow data without cluttering views. [CONFIRMED · fathom/171833723 @ 00:00:57 – 'I'll hide it from the view that you have. So you are not being bothered by that column value']
- Blog records in Airtable will be migrated to the content tab automatically when their status changes to 'live' (published), not at the approval stage. [CONFIRMED · fathom/171833723 @ 00:01:37 – 'once it is live, then we can move directly into the content tab once the status is changed to live']
- Resource library tags in Airtable will be locked as a fixed field set; team members cannot add new tag values without admin approval. Josh is the designated tag admin and the only person who can add new tag values. [CONFIRMED · fathom/171833723 @ 00:02:33 – 'the values below, we cannot add additional values to them without admin approval, which would be Josh' / 00:02:51 – 'Josh is the only one who can add those']
- The Airtable approval dashboard will be configured with 'group by owner' so Amanda can segment and prioritize approval requests by team member (Josh, Nathalie, Natasha) and prevent any one person's requests from being buried. [CONFIRMED · fathom/171833723 @ 00:11:23 – 'on the approval dashboard, if I can group by person, please' / 00:11:51 – 'And that's available today. Stage group by owner.']
- The agency has already built the Airtable-to-WordPress publish automation for other clients and will share a Loom walkthrough with the client before proceeding. [CONFIRMED · fathom/171833723 @ 00:05:48 – 'I will give you a short loom of like, you know, what it would look like when it's in production, because we have built it for the other clients']
- The webinar Airtable template is intended to auto-populate a predetermined list of internal production tasks (decks, speakers, registration landing pages) when campaign type equals 'webinar'. [CONFIRMED · fathom/171833723 @ 00:12:44 – 'the template should be a predetermined list of production tasks that would populate if, you know, if campaign equal webinar']
- Webinar registration count integration will filter out internal @discoverdata.com email addresses to avoid inflating registration metrics. [CONFIRMED · fathom/171833723 @ 00:14:00 – 'Is there a way that we can remove at discoverdata.com emails? So when we see a spike in its employees, we don't think it's our killer campaign?' / 00:14:13 – 'Yeah, we can add that logic in to ignore the discoverdata emails']

## Stack & tools

- The client uses Airtable as a central project management and content operations hub, with tabs for blogs, webinars, and a resource library. [CONFIRMED · fathom/171833723 @ 00:00:12 – 'We've got the webinar template. We've got a couple of adjusted columns.']
- The client uses HubSpot and Salesforce as CRM/marketing platforms; webinar registrant data is expected to flow into both rather than through Airtable. [CONFIRMED · fathom/171833723 @ 00:12:28 – 'Only in Airtable or also to HubSpot and Salesforce?' / 00:13:07 – 'I think there's an integration to either HubSpot or Salesforce that would allow us that visibility']
- The client uses Sprout Social for social media scheduling and approvals. [CONFIRMED · fathom/171833723 @ 00:09:00 – 'the next two items that I want to surface would be Sprout Social and the approvals process']
- The client uses WordPress for publishing landing pages and blog content. [CONFIRMED · fathom/171833723 @ 00:02:20 – 'when we're creating a WordPress page, we can go in and select any of these tags']
- The client uses Zoom for webinar hosting and registration; a Zoom–Airtable integration is planned to surface a live registration count per webinar campaign object. [CONFIRMED · fathom/171833723 @ 00:12:55 – 'And then integration with Zoom' / 00:13:34 – 'what we'll need to just make sure that the webinar object that we have on the Airtable, there is also a column that says like the webinar URL']

## Other

- Abhi sent a Loom to Amanda covering a nested structure on Upwork and is awaiting her feedback before investing further time in refining it. [CONFIRMED · fathom/171833723 @ 00:14:47 – 'I've sent you another loom on the nested structure on Upwork. So if you could just, you know, do it whenever you get time. I haven't put too much time on, you know, like fine tuning it yet because I wasn't sure if I'm planning in the right di']

## Sources

- `crm/amanda-thomas` · — · crm · CRM numbers — Amanda Thomas
- `fathom/171833723` · 2026-08-10 · fathom · Impromptu Zoom Meeting
