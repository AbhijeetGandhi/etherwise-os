# Diskover Data — Dossier

_Compiled 2026-08-10 17:56:25.909618+00:00 · 25 client facts (25 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Architecture & systems

- Campaign creation workflow supports templating with production item type selection (webinar, WordPress, or blank page) and allows users to specify which production items to create from the selected template. [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'you get the option to choose what type of production item template you want to use, uhm, like webinar, wordpress or blank page and then you can select here like for the webinar what are the production items that you can create']
- Client's Airtable nested structure has four hierarchical levels: Integrated Campaigns (top level) → Campaigns → Campaign Tactics → Production Items. [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'integrated campaigns at the top, which are like the highest level in the nested structure' and 'inside of the campaign, there will be campaign tactic. And then inside of the campaign tactic, you can have one of the production items']
- Drag-and-drop functionality allows users to move production items between campaign tactics, with a notification confirming the link and the ability to unlink by moving items back to the unlinked panel. [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'you have a drag and drop feature, so you can just move it there and you can get a small notification' and 'if you just want to unlink it, you can just move it back here']
- Multi-select and bulk delete functionality is available for managing production items and campaign elements. [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'you can do a multi-select and delete like this as well']
- Production item templates are specific to the selected type; webinar template generates a defined set of production items that can be customized before campaign creation. [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'if you select, like webinar...you can just modify what production items you want to create here']
- The system includes an 'unlinked' tab that shows all tactics, production items, and campaigns that have not yet been linked to a parent structure, with the expectation that these will be eliminated as linking is completed. [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'there is this unlinked tab here. Which basically says like all the tactics, all the production items and all the campaigns that are not linked yet. So over time, once everything becomes linked, these all would go away']
- The system maintains a data layer that reflects the nested relationships across tables: Integrated Campaigns, Campaigns, Campaign Tactics, and Production Items, with linked references visible in each table view. [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'let me quickly show you how these changes reflect in our data layer' and description of seeing 'test integrated campaigns' linked to 'test campaign' across multiple table views]
- UI maintains consistency across different dashboards with matching functionality patterns (e.g., HideEmpty and similar controls). [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'I'm trying to stay consistent with the UI so that when you navigate through different dashboards, you have the same sort of functionality']

## Stack & tools

- Approval dashboard displays all tasks requiring approval from all tables, with filtering by table source (content catalog, campaigns, blogs, social media, Josh, Natasha boards) [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:04:00]
- Approval workflow: task marked as approved in dashboard, then team member publishes and marks as complete [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:07:00]
- Client's primary platform for the campaign management system is Airtable. [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'I was working on the Airtable nested architecture']
- Dashboard built with workflow status column that replaced individual status columns across multiple tables (content catalog, campaigns, blogs, social media, and others) [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:00:30 - 00:02:15]
- Dashboard has two views: All Tasks (showing all tasks with filters and grouping) and My Work (showing only assigned tasks for user) [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:07:30]
- Dashboard includes bulk approval capability (select all and approve all selected) [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:06:30]
- Dashboard includes search functionality across owner and board fields [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:04:45]
- Dashboard supports filtering by due date (on and after specific dates) and other column-based conditions with logical combinations [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:05:45]
- Dashboard supports grouping by owner, type, product, approver, and stage [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:05:15]
- Hidden/empty columns can be toggled visible/invisible in dashboard views [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:08:15]
- Status values from legacy columns were mapped to new workflow statuses (e.g., draft → in progress, live/ready to go live → complete/approved) [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:01:30]
- Team members can send tasks for review (updates status to interview), which alerts approver [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:10:00]
- Team members cannot mark tasks as approved; only approver can approve [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:10:00]
- Workflow status column contains standardized options: to do, in progress, interview, approve, complete, stack, and pause [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:00:45]

## Commitments

- Additional training loom will be recorded for team on dashboard usage after client approval [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:00:15]
- Approval dashboard ready for rollout to full team once client confirmation received [CONFIRMED · https://www.loom.com/share/794dca83215c4f45a4dc1a9e3cc52cc8 @ 00:11:15]
- POC for nested Airtable architecture is ready for feedback before fine-tuning and finalization. [CONFIRMED · https://www.loom.com/share/d9ec5e406c8f40fb974afddbd24b717d @ 'I just wanted to confirm is the direction that I'm heading is correct and this is how you wanted it. If not, let me know so we can quickly make changes before I put too much time on finalizing and fine-tuning this']

## Sources

- `loom/794dca83215c4f45a4dc1a9e3cc52cc8` · 2026-07-28 · loom · Approval Dashboard Workflow Status Setup Walkthrough
- `loom/d9ec5e406c8f40fb974afddbd24b717d` · 2026-08-07 · loom · Airtable Nested Architecture Proof of Concept Update
