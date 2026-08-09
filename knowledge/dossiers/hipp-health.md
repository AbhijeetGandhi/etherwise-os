# Hipp Health — Dossier

_Compiled 2026-08-10 00:57 IST · 52 client facts (52 trusted) from 6 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- A third payroll provider integration is planned: the provider is called Epsom. The automation will follow a similar pattern to the previous two, requiring an employee ID-to-HIP-ID mapping. Muneeza will share the Epsom payroll file by end of day Friday or Monday. [CONFIRMED · fathom/85347019 @ 00:05:12]
- An automation adds a new technician to HIP when a BTRBT (internal system term) is marked as therapy scheduled. [CONFIRMED · https://www.loom.com/share/22ee69f67e4f4ee8937a4a32db0e8080 @ 0:00:00 - 'automation is where we add a new technician that is provided to HIP' + 'triggered when the BTRBT is marked as therapy scheduled']
- Hip automations include a system for adding patients and their respective caregivers to HIP [CONFIRMED · https://www.loom.com/share/4a803cfb19c845ab91038d498941190d @ 00:00:05 'the first automation is about adding patient and their respective caregiver in hip']
- Payment has been received for both the HIP-to-Fingercheck and the Paycom automation builds. [CONFIRMED · fathom/85347019 @ 00:05:03]
- The agency built a HIP-to-Fingercheck payroll conversion flow that downloads a converted Excel file and uploads payroll data directly into the client's Supabase database; Alex confirmed it worked with no errors. [CONFIRMED · fathom/85347019 @ 00:01:19]
- The client (HIPP) is a health automation platform with API endpoints for managing patients, clinicians, and caregivers. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ opening: 'demo the API endpoints that we created for, ah, HIPP. Health automation thing']
- The only remaining manual step in the payroll automation is Alex manually exporting payroll data from HIP as a CSV; Alex requested a future HIP API endpoint to export that CSV automatically to fully close the loop. [CONFIRMED · fathom/85347019 @ 00:07:07]

## The numbers

- At least one test clinician record was successfully created and verified in the system during this automation walkthrough [CONFIRMED · https://www.loom.com/share/9c707a38f3f74c05b2f3ea35f16ea384 @ new clinician is added with all the proper details]
- Patient records include a patient ID (example: John 23 for patient John Dewey). [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'This is the patient ID... John 23. There it is.']

## People

- Abhijeet Gandhi (agency) and Muneeza Patel (client, HIP) are the two participants on this call. [CONFIRMED · fathom/85347019 @ 00:00:13]

## Architecture & systems

- An automation is triggered when onboarding form status is marked as done, which adds a new clinician record to a system called 'hip' (healthcare information platform) [CONFIRMED · https://www.loom.com/share/9c707a38f3f74c05b2f3ea35f16ea384 @ automation gets triggered when the onboarding form status is marked as done]
- Caregiver records require an active patient to be associated with them. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'the caregiver needs, uh, an active patient']
- Current HIP-to-Fingercheck mapping uses employee first and last names; a known risk is duplicate names. Muneeza agreed to share a list with HIP user IDs mapped to emails (always unique) to resolve this. [CONFIRMED · fathom/85347019 @ 00:02:00]
- Output fields produced by the automation include: pay type, date of service, actual start time, end time, duration, appointment tag, pay code, pay code name, rate, miles driven, and mileage rate. [CONFIRMED · fathom/81784668 @ 00:04:34 – 00:06:04]
- Patient deactivation is a mark-as-inactive operation rather than deletion; deactivated patients no longer appear in lookups. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'mark it as inactive, and, yep, it's deactivated. So, if I look up John, it's gone.']
- Provider email address cannot be changed via the current API; attempting to do so triggers additional pop-ups that are not yet handled. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'another thing you cannot change the email. Uh, it, uh, it shows, like, other pop-ups and stuff. We, we can add that feature, but it's an addition there.']
- Provider name cannot be changed via the update API; it must match exactly as listed in the provider listing. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'There are a few things you have to get the provider name right. Uh, exactly same as it's in the provider listing']
- Provider records support updating phone number, SSN, address, and credentials; credentials can include multiple selections beyond RPT and compliance credentials like HIPAA. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'other things can be added and changed, like phone number, SSN... Credentials, like, what credentials should be added... and multiple credentials other than RPT... compliance credentials... selected HIPAA']
- The automation attempts to look up a company staff ID for each provider by matching provider names from the HIP export to a mapping list; some employees are missing from the mapping list and therefore cannot be matched. [CONFIRMED · fathom/81784668 @ sometimes the names that we have here does not match with anything in the mapping list they provided, so then we are not able to find their company staff ID]
- The automation creates a new clinician record in heap with proper details including name and heap user ID [CONFIRMED · https://www.loom.com/share/9c707a38f3f74c05b2f3ea35f16ea384 @ new clinician is created with the name test test and the proper heap user ID]
- The automation maps appointment tags (e.g. 'direct therapy') to pay codes and rates from a mapping table provided by the client; pay type is determined by a compensation schedule column (salary vs. hourly). [CONFIRMED · fathom/81784668 @ 00:04:34 – 00:05:57]
- The automation workflow processes on Monday and completes execution automatically after being triggered. [CONFIRMED · https://www.loom.com/share/22ee69f67e4f4ee8937a4a32db0e8080 @ 'Monday. So, as you can see, our automation already started and completed']
- The patient and caregiver addition automation is triggered when client status is changed to 'benefits verified' [CONFIRMED · https://www.loom.com/share/4a803cfb19c845ab91038d498941190d @ 00:00:20 'this automation gets triggered when the client status is changed to benefits verified']
- The provider update endpoint uses an add-or-update pattern: if data does not exist, it is added; if it already exists, it is replaced. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'everything in here is on an add or update state, so if the data is not up there, this data just gets added. If the data is already there, it just gets replaced']
- The system searches for and displays existing records before allowing actions; for example, checking if a patient already exists before creation. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'Let's just quickly look up if the patient already exists. Okay, so the patient doesn't exist.']
- The system uses web-based form automation to fill patient and provider information fields (not direct API payloads); the automation takes time to complete field population. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'it goes to the page, and then starts filling the information. Takes a bit of time in filling the information.']
- Upon automation completion, both the patient and caregiver are added to HIP with user IDs generated [CROSS-VERIFIED · https://www.loom.com/share/4a803cfb19c845ab91038d498941190d @ 00:00:45 'our patient is added to hip' and 'the caregiver is added as well' and 'their hip user IDs are added as well']

## Stack & tools

- OpenAI (LLM) is used within the automation to fuzzy-match terminology from the HIP export to the client's compensation/mapping table, allowing close matches rather than requiring exact string matches. [CONFIRMED · fathom/81784668 @ I think we already handled it via OpenAI, like, you know, to make sure that, you know, it is, like, you know, a close match, not the exact match]
- Provider credential field includes a HIPAA compliance credential option accessible via dropdown with multiple select capability. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'compliance credentials... drop down with multiple selects... selected HIPAA']
- The Horizon automation UI is currently hosted on the agency's own Vercel deployment (agency org account), not on HIP infrastructure. [CONFIRMED · fathom/85347019 @ 00:03:22]
- The automation creates a user record in an external system and generates a user ID upon successful creation. [CONFIRMED · https://www.loom.com/share/22ee69f67e4f4ee8937a4a32db0e8080 @ 'user is created successfully and we also got their user ID']
- The automation uses a Make (formerly Integromat) workflow triggered by uploading a HIP export file (via Google Sheet or Excel) and processes each record to produce an updated output file. [CONFIRMED · fathom/81784668 @ we have a make automation that runs, basically]
- The client (HIP) maintains a complete payroll record in a Supabase database, which the automation writes to. [CONFIRMED · fathom/85347019 @ 00:01:38]
- The client uses Monday as a project/record management system that stores heap user IDs [CONFIRMED · https://www.loom.com/share/9c707a38f3f74c05b2f3ea35f16ea384 @ the heap user ID stored in Monday as well]
- The payroll automation in Make uses an OpenAI module and a Google module, both requiring API key configuration before scenarios can be transferred. [CONFIRMED · fathom/85347019 @ 00:02:41]
- The system includes REST API endpoints for creating patients, adding clinicians to patients, creating caregivers, deactivating patients, and updating provider information. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ Throughout demo; examples: 'creating a patient', 'adding a clinician to the patient', 'create this caregiver', 'deactivating the patient', 'updating the provider']
- The system sends an automated email notification when an NPI (National Provider Identifier) number is missing at the time of user creation. [CONFIRMED · https://www.loom.com/share/22ee69f67e4f4ee8937a4a32db0e8080 @ 'NPI missing email is sent properly' + 'NPN number was not available at the time of creation']

## Commitments

- Abhijeet committed to deploying the Paycom automation to Vercel (under a .vercel.app domain) as an interim solution so the client can start using and testing it immediately. [CONFIRMED · fathom/85347019 @ 00:04:05]
- Abhijeet committed to fixing remaining small issues (empty output bug, date format, name-matching issues) within approximately 30 minutes of the call and to recording a Loom walkthrough of the completed automation. [CONFIRMED · fathom/81784668 @ I'll get those fixed within, like, you know, next half an hour or so… I'll shoot your Loom]
- Abhijeet has a call with Alex (client-side contact) in 10 minutes (from call time) to obtain a HIP API key and test uploading employee data into HIP via API. [CONFIRMED · fathom/85347019 @ 00:06:58]
- Abhijeet has been added to the client's GitHub organisation and will push the automation code once it is fully clean. [CONFIRMED · fathom/81784668 @ I did receive the GitHub invite, so I'm in the organization. So I'll add this code once, you know, it looks all good]
- Abhijeet will build the third (Epsom) provider automation directly inside Make once all mapping pieces are confirmed. [CONFIRMED · fathom/85347019 @ 00:08:00]
- Abhijeet will record a Loom video showing what API keys and configuration (OpenAI module, Google module) need to be set up inside Make so the scenarios can be copied/transferred to the client's Make instance. [CONFIRMED · fathom/85347019 @ 00:03:00]
- Abhijeet will review the Epsom payroll file once received, identify missing mapping fields, and inform Muneeza so she can gather answers from the client before building the automation. [CONFIRMED · fathom/85347019 @ 00:06:17]
- Client (Muneeza) stated the long-term plan is to host the automation on HIP's own infrastructure and integrate it directly into the HIP platform. [CONFIRMED · fathom/85347019 @ 00:03:32]
- Muneeza committed to providing Abhijeet with the HIP IDs for Horizons and the billing address for the invoice, and to clarifying the hosting solution for the automation. [CONFIRMED · fathom/81784668 @ I also have to get you the HIP IDs, right? For Monday… I will then let you know about the hosting as well]
- The goal is to share the completed automation with the end client by end of the week; further feedback and fixes are anticipated after the client runs it a few times. [CONFIRMED · fathom/81784668 @ hopefully by the end of the week, we can just share this with client. I'm sure like once they run this like a couple of times, there will still be like some feedback]
- The team plans to add a deactivate caregiver endpoint, following the same pattern as patient deactivation. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'We can have, like, a deactivate calculator if needed. It should be as easy as, uh, deactivating a patient.']
- The team plans to handle the provider email update pop-up that currently appears when updating email addresses. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'When updating the email address, it shows an additional pop-up, which we can handle.']

## Other

- An API key for an unspecified platform (needed to add employees to it) had not yet been shared by the client contact; Abhijeet requested it via a messaging thread during the call. [CONFIRMED · fathom/81784668 @ about the API key for the platform… He hasn't shared it? No, he hasn't]
- Test/dummy HIP export data used during development contained fictitious employee names (e.g. 'Steph Curry'), meaning some matching failures during demo were artefacts of dummy data rather than real issues. [CONFIRMED · fathom/81784668 @ that is just a dummy data from hip so those employees don't exist]
- The system occasionally displays HIPAA-related pop-ups during updates that may require handling. [CONFIRMED · https://www.loom.com/share/3fcdc370ca6a49fa977e0456aaeb6627 @ 'there's sometimes, uh, hip-assist little pop-ups that sometimes pop up']

## Sources

- `fathom/81784668` · 2025-08-20 · fathom · Paycom Demo
- `fathom/85347019` · 2025-09-05 · fathom · Muneeza <> Abhi
- `loom/3fcdc370ca6a49fa977e0456aaeb6627` · 2025-11-19 · loom · API Demo for HIPP: Patient and Clinician Management 🚀
- `loom/4a803cfb19c845ab91038d498941190d` · 2025-12-03 · loom · Adding Patient and Caregiver to Hipp
- `loom/9c707a38f3f74c05b2f3ea35f16ea384` · 2025-12-03 · loom · Adding Clinician to Hipp
- `loom/22ee69f67e4f4ee8937a4a32db0e8080` · 2025-12-03 · loom · Add Technician to Hipp
