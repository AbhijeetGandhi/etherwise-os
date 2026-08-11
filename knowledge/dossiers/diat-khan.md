# Diat Khan — Dossier

_Compiled 2026-08-10 17:56:25.909618+00:00 · 13 client facts (13 trusted) from 3 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- An automation was built to merge contacts based on phone number matching, triggered by form submissions. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ whenever there is a form submission, it checks whether there is a contact, uh, already existing by the same phone number. And if there is, then it would merge it]
- The contact merge automation now operates in real-time mode. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ we'll also have the automation that, you know, is like real time now]

## Architecture & systems

- Email modules in the Freedo and call SMS automation have an address section where recipients can be added by including multiple items [CONFIRMED · https://www.loom.com/share/a23a5a7ea3404606bee1dfcfc7eda1eb @ 0:00:20 'two address section. In this, add one more item or the number of items. You want to add the emails']

## Stack & tools

- The automation maintains activity logs and notes during the contact merge process. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ All the activity and everything is maintained, like, along with the notes and everything]
- The client uses an automation named 'Freedo and call SMS' with three email modules [CONFIRMED · https://www.loom.com/share/a23a5a7ea3404606bee1dfcfc7eda1eb @ 0:00:05 'automation named Freedo and call SMS. Here we have three email modules']
- The contact merging automation preserves form submission details, call recording links, and call-related information in a single contact record. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ it has all the details that came through form. Uhm, but then it also has the details of the calls. Like, you know, the, the review recording]

## Commitments

- After adding email recipients to all three email modules, the automation must be saved to apply changes [CONFIRMED · https://www.loom.com/share/a23a5a7ea3404606bee1dfcfc7eda1eb @ 0:00:45 'once you are done adding it to like adding, adding those emails to all three of these email modules, then simply save the automation']
- The automation is ready to be applied to all existing contacts to eliminate duplicates once the client confirms the current test output. [CONFIRMED · https://www.loom.com/share/02e7674808694e91b2900d5597bd5584 @ if everything looks good that I could just go ahead and run this for, you know, all the contacts and make sure there are no duplicates]
- The client needs two changes: CAPTCHA must be removed from the meeting link, and the slug must be changed from a multi-part format (slash separated) to a single slug without slashes for HubSpot API compatibility [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:03:00 'We need two things changed. One is the, uh, the set of the captcha and second one is the slug that we have here should be just one slug. thing without the slash in between as it messes up with the HubSpot API']

## Other

- The client has three meeting links in their output: a test link, one for the Azul application, and one for the AI business consultation [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:00:00 'we have three different links here. One is a test one that we created, one for the Azul application. And then one is for the actual, the AI business consultation link']
- The meeting link slug is formatted as slash AI business slash, with a slash in between that breaks HubSpot API requests [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:01:05 'the slug is set up. It is, I mean, it's the same slash AI business, and then the slash, there is nothing on the internet. That allows, you know, how to handle that while making a request']
- The meeting link website has CAPTCHA enabled, which prevents the HubSpot API from sending CAPTCHA tokens and retrieving needed details [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:01:25 'this website has a capture enabled. And if you look into the documentation, it says that the following features are being supported with the Mutancy API and one is sending the capture tokens. You cannot really send the capture tokens and get the details that we need']
- The meeting links use a custom domain that cannot be used within HubSpot [CONFIRMED · https://www.loom.com/share/8e61dd6b687148b380c055aa87e23850 @ 0:00:40 'there is the custom domain that is set up, which we can't really use within the, uh, HubPod']

## Sources

- `loom/02e7674808694e91b2900d5597bd5584` · 2025-10-20 · loom · Videos | Library | Loom - 20 October 2025
- `loom/8e61dd6b687148b380c055aa87e23850` · 2025-12-02 · loom · Meeting Link Issues and Required Changes 🔗
- `loom/a23a5a7ea3404606bee1dfcfc7eda1eb` · 2026-04-08 · loom · How to Add Email Recipients Easily ✉️
