# Sale Pros Corp — Dossier

_Compiled 2026-08-12 05:15 IST · 42 client facts (39 trusted) from 8 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Client has a metrics tracking table in Airtable with formula fields for marketing and sales metrics (CPM, CPC, CTR, CPL, CPTS, CPQL, CPBC, CPSU, CPA, ROAS, CROS, RPNL). [CONFIRMED · https://www.loom.com/share/782730ea82c643d392b36b384409c73e @ 'all of these are formula fields' and listing of metrics throughout transcript]
- Form submission to Hubsford Automation is now complete with corrected field mapping [CONFIRMED · https://www.loom.com/share/a4459478348842e88507c796bb857321 @ 'form submission to Hubsford Automation, it's now done completely']
- Lead creation from contacts had a permission issue in Make that was not resolvable directly, so a workaround was built instead. [CONFIRMED · https://www.loom.com/share/eddf03edec554092ab08a55b45d29faf @ 00:00:07 – 'I wasn't able to find anything... regarding the lead permission issue']
- Multiple EINotes can be created for a single contact (more than two notes demonstrated) [CONFIRMED · https://www.loom.com/share/9999d718088f4fd19d976562635ed80b @ 'There are two of them, that's where two are appearing, but actually there are more than two']
- Notes are created with the title 'EINotes' and contain extracted data points from calls [CONFIRMED · https://www.loom.com/share/9999d718088f4fd19d976562635ed80b @ 'note that just got added with the title EINotes and all the points we needed']
- The client is Parc, owned by Abdul [CONFIRMED · https://www.loom.com/share/11aec99e928d4dd8ad91f18a4480ab4f @ 0:00:20 - 'Abdul, I'm the owner of Parc']

## The numbers

- Contract: AI Voice - Inbound Expert Needed - Vapi, HighLevel, Make — status ACTIVE. [CONFIRMED · CRM:contracts/41584201]
- Land owner reference and notes field previously incorrectly mapped as single line and multi-line fields [CONFIRMED · https://www.loom.com/share/a4459478348842e88507c796bb857321 @ 'land owner reference and notes, like previously it was coming into the single line and multi-line fields, ah, which was, because, you know, it was incorrectly mapped']
- Revenue to date: $1,609 across 11 earning transactions (2025-09-14 → 2025-11-23). [CONFIRMED · CRM:transactions]
- Test data: ad spend €2,000, impressions 1,000 (CPM €2), clicks 400 (CPC €5), CTR 40%, LP% 25%, leads 100 (CPL €20), lead percentage 10%, TS 10 (CPTS €200), LTS% 10%, QL 100 (CPQL €20), QBC 20%, booked calls 20 (CPBC €100), SU% 75%, show-ups 15 (CPSU €133.33), CO €93.33, sales 14, revenue €150, cash collected €100,000, CPA €142.86, ROAS 75, CROS 50, RPNL €1,500. [CONFIRMED · https://www.loom.com/share/782730ea82c643d392b36b384409c73e @ detailed metrics listing from 0:00 onwards with all numerical test values]
- When a lead is created from an email contact, only first name, last name, and pipeline stage are mapped; pipeline stage is set to 'interest expressed'. [CONFIRMED · https://www.loom.com/share/eddf03edec554092ab08a55b45d29faf @ 00:01:50 – 'While creating a lead, all I'm... creating is... the first name, last name, and the lead pipeline stage is interest expressed']
- OpenAI model replacements have been made in at least 4 locations where Render code blocks were being used [_INFERRED_ · https://www.loom.com/share/1a50280ca4654ed1acaf10acd9a6c9b0 @ 0:02:00 - 'there are multiple places where the code block was being used and now it's replaced everywhere. So it's like three and then maybe it was being used somewhere down here as well. Four']

## Architecture & systems

- A HubSpot workflow checks if a contact record is created with source equal to 'email' (set in Make), and if so, creates a lead. [CONFIRMED · https://www.loom.com/share/eddf03edec554092ab08a55b45d29faf @ 00:00:40 – 'I created a workflow inside of the HubSpot itself where... if... the source is... source is email, then it will create a lead']
- A new contact property called 'source' was created to track the origin of contacts, rather than using existing fields like 'latest traffic source' or 'original traffic source'. [CONFIRMED · https://www.loom.com/share/eddf03edec554092ab08a55b45d29faf @ 00:02:45 – 'I created a new property for the contact called source... I just didn't want to mess up with... anything that you already have']
- A workflow is triggered when a call ends [CONFIRMED · https://www.loom.com/share/9999d718088f4fd19d976562635ed80b @ 'That gets triggered when the call ends']
- Multiple forms exist across different pages: Maryland hero section, footer section, and forms for three other states (including New Jersey) [CONFIRMED · https://www.loom.com/share/a4459478348842e88507c796bb857321 @ 'every form that we have on every page' and 'Maryland, uh, hero section' and 'footer section and then the other three states' and 'new jersey hero form']
- Render code blocks are being kept running in parallel with new OpenAI models for 2-3 days to validate outputs match before removal [CONFIRMED · https://www.loom.com/share/1a50280ca4654ed1acaf10acd9a6c9b0 @ 0:00:50 - 'we haven't removed this render block because we want to keep this running the way it is right now for the next two, three days']
- The source parameter approach is designed to work with multiple automation sources (email, WAPI, etc.) to ensure leads are created when contacts come from those channels. [CONFIRMED · https://www.loom.com/share/eddf03edec554092ab08a55b45d29faf @ 00:03:20 – 'the leads that we have coming in from maybe... other automations like WAPI and stuff as well. We can use that source parameter there']
- The workflow includes a node that adds notes to a specific contact [CONFIRMED · https://www.loom.com/share/9999d718088f4fd19d976562635ed80b @ 'node that adds the note to the specific contact']
- When email automation runs and a contact is created, a source field is set in Make to indicate the contact came from email. [CONFIRMED · https://www.loom.com/share/eddf03edec554092ab08a55b45d29faf @ 00:00:25 – 'whenever... the email automation runs and the contact is created, what we'll do is... a source field which will basically tell... it came from the email']

## Stack & tools

- Client uses Google Sheets and Airtable for campaign metrics and reporting. [CONFIRMED · https://www.loom.com/share/782730ea82c643d392b36b384409c73e @ 'copy-pasting whatever the formulas we had, ah, in the Google sheet into the A table']
- Gmail to HubSpot automation uses an OpenAI model to extract details from email HTML content [CONFIRMED · https://www.loom.com/share/11aec99e928d4dd8ad91f18a4480ab4f @ 0:00:30 - 'we have added like a open AI model in between that gets, gets us details']
- OpenAI models have been added to replace Render code blocks throughout the system [CONFIRMED · https://www.loom.com/share/1a50280ca4654ed1acaf10acd9a6c9b0 @ 0:00:15 - 'we have now added like, you know, the open AI models everywhere, wherever the, you know, the render was being used']
- The automation extracts reference number, first name, last name, email, phone number, notes, and purpose from incoming emails [CONFIRMED · https://www.loom.com/share/11aec99e928d4dd8ad91f18a4480ab4f @ 0:01:35 - 'the information we fetch here is, is this, like, you know, the reference number, full name, email, phone number, notes, and purpose']
- The client uses Make (formerly Integromat) for automation with configurable column mappings [CONFIRMED · https://www.loom.com/share/55e6aa32d3844e5d9db6f1bf7826a1c0 @ 'in the trailers you go to configure column mappings']
- The system includes a chargeability module with a detail prompt to extract needed data from call transcripts [CONFIRMED · https://www.loom.com/share/9999d718088f4fd19d976562635ed80b @ 'chargeability module with a proper detail prompt. About all the data we need from the transcript']
- The system uses HubSpot for data storage and transfer automation [CONFIRMED · https://www.loom.com/share/a4459478348842e88507c796bb857321 @ 'form to helps for data transfer automation' and 'hub' and 'HubSpot']
- Lead creation from email-sourced contacts uses Make.com integration with HubSpot. [CROSS-VERIFIED · https://www.loom.com/share/eddf03edec554092ab08a55b45d29faf @ 00:01:20 – 'we set in make.com... through email automation']

## Commitments

- All form results expected to be visible starting tomorrow [CONFIRMED · https://www.loom.com/share/a4459478348842e88507c796bb857321 @ 'hopefully, from tomorrow you will start seeing all the results from all the forms']
- Field mapping corrections need to be applied to all remaining forms (footer and three other states); work to be completed after recording this update [CONFIRMED · https://www.loom.com/share/a4459478348842e88507c796bb857321 @ 'I just need to, you know, uh, delete these fields, create new ones for every form, which I'll do, right after recording this loop']
- Once approved, the automation will be run retroactively on all existing emails and then kept running in real-time for new incoming emails [CONFIRMED · https://www.loom.com/share/11aec99e928d4dd8ad91f18a4480ab4f @ 0:01:55 - 'once we give the thumbs up, we'll just run it for all the existing emails and then we can, you know, like keep it running, uh, on real time basis']
- Render subscription will be cancelled once OpenAI model replacement is validated and confirmed working correctly [CONFIRMED · https://www.loom.com/share/1a50280ca4654ed1acaf10acd9a6c9b0 @ 0:01:15 - 'then you can also, you know, cancel the subscription on render']
- The agency speaker will monitor for errors after the client maps the custom ID field with the A table record ID column in Make [CONFIRMED · https://www.loom.com/share/55e6aa32d3844e5d9db6f1bf7826a1c0 @ 'let me know once you do that and then I'll monitor if there are any errors']
- The email automation will be run once feedback on the lead-creation setup is provided, and the sender apologizes for delay. [CONFIRMED · https://www.loom.com/share/eddf03edec554092ab08a55b45d29faf @ 03:45 – 'once you give me a feedback on this, I... will run it... ASAP']
- There is a discrepancy between CPL and RPL formulas in the Google Sheet that may be incorrect; the agency will send a message explaining the suspected issue and will update the Airtable if the Google Sheet formulas need correction. [CONFIRMED · https://www.loom.com/share/782730ea82c643d392b36b384409c73e @ 'maybe the, the RPL and CPL are kind of, uh, like the formulas that we have to have in Google Sheet are exactly the same as what we have in here...I'll just write down a short message in WhatsApp explaining']
- Two additional Looms shared by client will be reviewed and work will commence tomorrow morning [CONFIRMED · https://www.loom.com/share/a4459478348842e88507c796bb857321 @ 'tomorrow morning I will look into the other two looms that you have shared and start working on those as well']

## Other

- Execution logs show the most recent automation execution successfully added records to HubSpot [CONFIRMED · https://www.loom.com/share/a4459478348842e88507c796bb857321 @ 'if I go into execution logs, you can see the most recent execution, it also added in the hub']
- In a recent Make execution, the custom ID field came in empty, preventing record lookup and update in a table [CONFIRMED · https://www.loom.com/share/55e6aa32d3844e5d9db6f1bf7826a1c0 @ opening statement: 'the custom ID came in empty as you can see here and that's why we're able to find any record in a table to update']
- The Airtable table with formulas is now ready for client use. [CONFIRMED · https://www.loom.com/share/782730ea82c643d392b36b384409c73e @ 'this table is now ready, um, and yeah, you can start using it']
- The automation logic updates an existing contact if email or phone number already exists in HubSpot, otherwise creates a new contact [CONFIRMED · https://www.loom.com/share/11aec99e928d4dd8ad91f18a4480ab4f @ 0:00:55 - 'if the contact already exists with the same phone number, it would, or the email, it would just update that contact. Otherwise it would, uhm, like, you know, add a new contact']
- A WAPI agent project is being considered for one of the client's other companies [_INFERRED_ · https://www.loom.com/share/1a50280ca4654ed1acaf10acd9a6c9b0 @ 0:02:40 - 'you mentioned that, you know, you had some, to build the WAPI agent for some, some, one of your other companies as well']
- The root cause of automation failures is believed to be the unmapped custom ID field; once the A table record ID is mapped to the custom ID field, the automations should work correctly [_INFERRED_ · https://www.loom.com/share/55e6aa32d3844e5d9db6f1bf7826a1c0 @ 'that would, uh, I don't know if this is reflected on your end or not, but if it's not, can you please do this and then we'll start getting the A table record ID which would then make sure the automations went correctly as well']

## Sources

- `crm/sale-pros-corp` · — · crm · CRM numbers — Sale Pros Corp
- `loom/1a50280ca4654ed1acaf10acd9a6c9b0` · 2025-09-25 · loom · Update on Open AI Model Integration and Render Transition
- `loom/a4459478348842e88507c796bb857321` · 2025-10-13 · loom · Form Submission Update and Automation Progress 🚀
- `loom/782730ea82c643d392b36b384409c73e` · 2025-10-20 · loom · Formula Implementation and Insights for Marketing Metrics 📊
- `loom/11aec99e928d4dd8ad91f18a4480ab4f` · 2025-10-28 · loom · Gmail to HubSpot Automation Demo 📧
- `loom/eddf03edec554092ab08a55b45d29faf` · 2025-11-06 · loom · Lead Creation Update and Workaround in HubSpot
- `loom/55e6aa32d3844e5d9db6f1bf7826a1c0` · 2026-01-07 · loom · Resolving Custom ID Mapping Issues for Automation Success 🔧
- `loom/9999d718088f4fd19d976562635ed80b` · 2026-02-02 · loom · Automating Call Transcripts and Note Management
