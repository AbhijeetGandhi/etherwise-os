# Optimize IS — Dossier

_Compiled 2026-08-21 14:09 IST · 19 client facts (19 trusted) from 3 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## The numbers

- Revenue to date (GROSS, billed): $1,162.36 across 4 revenue transactions (2026-07-02 → 2026-08-13). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $1,162.36 — the same as gross; the ledger carries no fee, tax or refund rows for this client. [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]

## People

- A person named 'Mia Kara' (Miyakara) has a pending phone number that needs to be collected during intake. [CONFIRMED · fathom/152038994 @ 00:36:07 - 'मियाकरा का फोन नमबर चाहिए']
- A person named 'Sachin' was assigned various tasks related to the project but was reported to not know how to do some simple things. [CONFIRMED · fathom/152038994 @ 00:12:42 - 'मैं अब कैसे लोकिंगे में देखा ना, इसको में काफी सारे variety of tasks दिये, सचिन को']
- A person named Alex (Alexander) is referenced in context of a WhatsApp message and email thread on the project. [CONFIRMED · fathom/152038994 @ 00:46:06 - 'आलेक्स ने बोला है ना सेंट सी वाट्साप करके']
- A person named Jessica is referenced as the Clinical Director; her email was not available earlier but has since been added. [CONFIRMED · fathom/152038994 @ 00:35:00 - 'clinical director जैसिका है गया, clinical director ना... जैसिका का email नालना है, तभी जैसिका कभी email नहीं था अपने पास']
- Jay Pokharna is a team member (likely on the agency/implementation side) working on the automation build alongside Abhijeet Gandhi. [CONFIRMED · fathom/152038994 @ 00:04:22 - Jay Pokharna speaking throughout the call alongside Abhijeet Gandhi]

## Architecture & systems

- A data store solution is proposed to handle weekend follow-up emails: emails scheduled on weekends are added to a data store and sent every Monday instead, so follow-ups only go out on business days. [CONFIRMED · fathom/152038994 @ 00:37:00 - 'एक data story create करता हमें, और सारे जो weekend वाले email से, वो आप add करते जाऊंगा, ताकि मंदे को send हो जाएंगे']
- Follow-up email cadence needs to be restricted to business days only; the proposed solution uses a data store to queue weekend emails and flush them every Monday. [CONFIRMED · fathom/152038994 @ 00:37:00 - 'is there a way to set up those cadence to, so that they only send on business days' / 00:38:00 - 'Every Monday, he will if there data store, then he will send it']
- HubSpot search module cannot handle empty values — searching with empty/null fields is not supported, requiring conditional logic to only include available values in search filters. [CONFIRMED · fathom/152038994 @ 00:15:00 - 'empty values नहीं चलते सर्च करने के लिए कभी भी']
- HubSpot tickets are being closed and new tickets created as part of the workflow; a person named Juliet is involved in ticket management. [CONFIRMED · fathom/152038994 @ 00:45:00 - 'there is a ticket close, that means launch the ticket basically' and 00:45:13 - 'We didn't ask Juliet to do this, he does it']
- HubSpot trigger-based normal workflow automation triggers once per record status change, so bulk operations (e.g. 300 records) would trigger the automation 300 times sequentially; sales sequences require Sales Professional access. [CONFIRMED · fathom/152038994 @ 00:10:20 - 'is automation 300 times trigger होएगा... sales sequence... उसका access सब को नहीं रहता है, जिसका sales professional वाला है']
- The HubSpot email sending setup involves configuring From name, From email, Reply-To, Subject, and BCC fields; multiple email accounts for sending are being explored. [CONFIRMED · fathom/152038994 @ 00:31:02 - 'Also, in the HubSpot campaign, is it possible to send via multiple emails?' and 00:41:02 - 'from name में और BCC ka bhi mujhe list béjnay']

## Stack & tools

- Claude desktop app and ClickUp are both mentioned as being used and noted as very resource-heavy applications. [CONFIRMED · fathom/152038994 @ 00:39:21 - 'क्लॉड का डेस्टप आप चालू किया ना... क्लॉड का डेस्टप आप भी और क्लिक अप दोनों बहुत खतरना खेवी']
- HubSpot is being used for deal/case search using Case ID, Company ID, and Provider as search/filter parameters with AND logic when multiple values are present. [CONFIRMED · fathom/152038994 @ 00:15:00 - 'अगर case ID हो तो case ID से, और उसके साथ company ID हो तो company ID से और provider हो तो provider हो']
- The project uses HubSpot for email campaigns, including regular/bulk email sends and email templates with HTML blocks and drag-and-drop editor. [CONFIRMED · fathom/152038994 @ 00:18:00 - 'create email regular send to a list of companies can be sent immediately or schedule']
- The project uses Make (formerly Integromat) scenarios for automation, including bulk email workflows with templates. [CONFIRMED · fathom/152038994 @ 00:20:19 - 'make scenario में तुन्हे आभी बीच में bulk email वाला किया था ना']

## Timeline

- Email/automation activity appears to have started around April (not March), with zero subscriber/open data still accumulating. [CONFIRMED · fathom/152038994 @ 00:22:00 - 'Hey, sorry, April, I'm to start' and 00:21:00 - 'मार्च में चालू हो आता है एक दिल']

## Commitments

- A team member committed to send a list of workflows by the next day (Thursday), with a Friday follow-up deadline mentioned. [CONFIRMED · fathom/152038994 @ 00:18:01 - 'we are going to get back to you by Friday I will send you the list of workflows by tomorrow']

## Sources

- `crm/optimize-is` · — · crm · CRM numbers — Optimize IS
- `fathom/152038994` · 2026-06-03 · fathom · Impromptu Google Meet Meeting
- `email/19f8fa76af6df725` · 2026-07-23 · email · Accept your invitation to join shared calendar: ‘Abhijeet Gandhi’
