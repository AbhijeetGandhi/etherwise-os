# Zeeshan Rasheed — Dossier

_Compiled 2026-08-18 19:12 IST · 23 client facts (23 trusted) from 4 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The client (Zeeshan Rasheed) has an existing AI code builder tool that was previously developed by a student associated with the agency. The tool generates code across multiple files but has bugs and needs new features added. [CONFIRMED · fathom/50909261 @ 00:00:06]

## The numbers

- The client offered $50 to cover the bug fix (link not generating) plus the two new features (requirements.txt generation and per-file update input box). [CONFIRMED · fathom/50909261 @ so it's a simple it's a requirement or TXT file he generate... I can $50 for that]
- Timeout value is being changed from 30 to 500. [CONFIRMED · fathom/114797497 @ 00:02:11 – 'here they have like timeout, it was 30. So I think 30 is, we have 500']
- Token size is being updated from the previous default of 50 to benchmark-specific values: BBH benchmark → 224 tokens, GSM8K → 256 tokens (client said '250' then '256'), ARC → ~224 tokens. [CONFIRMED · fathom/114797497 @ 00:00:37 – 'for BBH, we have around 224 token size'; 00:00:52 – 'For GSM8K, we set the token size for 256'; 00:00:59 – 'For ARK, we set the token size… 224']

## People

- Client contact is Zeeshan Rasheed; agency contact on the call is Abhijeet Gandhi. [CONFIRMED · fathom/114797497 @ 00:00:00 – speaker labels throughout the transcript]
- Two participants on the call: Zeeshan Rasheed and Abhijeet Gandhi. [CONFIRMED · fathom/117473152 @ 00:00:30 - 'Zeeshan Rasheed... Abhijeet Gandhi']

## Architecture & systems

- The system uses exactly two agents per framework (not more): one agent runs the reasoning task and the other verifies it. The framework referenced is OpenAI Swarm. [CONFIRMED · fathom/114797497 @ 00:04:02 – 'we have at least two agents'; 00:04:31 – 'We just use two for each framework']

## Stack & tools

- A new feature is requested: log the number of tokens consumed per query alongside the time taken, so token usage per query is tracked. [CONFIRMED · fathom/114797497 @ 00:05:11 – 'is it also possible that it shows that how many token they use for this for this query… we want to know that']
- The project includes an 'agency swarm' component that was throwing errors during the run. [CONFIRMED · fathom/117473152 @ 00:08:04 - 'एजन्सी स्वाम का क्या है']
- The project involves running benchmarks using a framework executed via a run.sh script on a Mac environment. [CONFIRMED · fathom/117473152 @ 00:00:59 - 'run.sh کرتی ہو... فریمورک سیٹ اپ لوڈ ہو گئے']
- The project makes API calls as part of its benchmark runs; API errors (400 error) were observed causing all benchmark tests to fail (75 out of 0 reported, meaning all failed). [CONFIRMED · fathom/117473152 @ 00:00:59 - 'سیونٹی فائف آؤٹ آف زیرو نا کہ سب فیل ہو گئے... فور ہنڈرڈ اے پی اے ایر آتا ہے']
- The project uses OpenAI GPT models; the client wants to switch from GPT-4.1 to GPT-5.2 (described as 'the recent one'), and also wants to add web search capability. [CONFIRMED · fathom/114797497 @ 00:02:28 – 'we don't use GPT 4.1, but we use GPT 5.2, the recent one' and 'this time we ate this web search as well']
- The system uses a Python virtual environment (venv) for dependency management; a mismatch between the virtual environment path and the project environment path was causing failures. [CONFIRMED · fathom/117473152 @ 00:21:00 - 'virtual environment does not match the project environment path .venv will be ignored']

## Commitments

- Abhijeet (agency) committed to completing all changes tonight or by tomorrow morning. Zeeshan (client) estimated the work at roughly one hour or less due to small changes. [CONFIRMED · fathom/114797497 @ 00:06:00 – 'we will try to get it done tonight only by tonight, but yeah, if not, was tomorrow morning']
- Abhijeet Gandhi committed to fixing the link-generation bug and adding both new features, estimating completion within approximately two days (by Tuesday). [CONFIRMED · fathom/50909261 @ I'll probably have it read in two days / probably have it ready by the end of date uh tuesday]
- Abhijeet agreed to record a short screen-recording tutorial on how to run the framework correctly so that repeated meetings for this purpose would not be needed. [CONFIRMED · fathom/117473152 @ 00:13:00 - 'چھوٹا سلوم ریکارڈ کر دے رن کرنے کے لئے... پھر بار بار نا یہ میٹنگ کے ضرورت نہیں ہوگی']
- Zeeshan committed to re-running the benchmarks the next morning (priority task) using the commands Abhijeet sent via Teams chat. [CONFIRMED · fathom/117473152 @ 00:21:00 - 'صبح پھر اس کو نا بس پریارٹی پہ انشاءاللہ']

## Other

- After applying the deactivate fix, some benchmarks began running successfully (not all failing), though the agency swarm component still had errors. [CONFIRMED · fathom/117473152 @ 00:20:00 - 'ابھی یہ کہ یہ چل رہا ہے پہلے تو فیل ہوا تھا... دوسرا بھی چل رہا ہے نا']
- Bug reported: The AI code builder fails to generate a download/copy link approximately 99% of the time, requiring users to manually copy files at the end of the generation process. [CONFIRMED · fathom/50909261 @ it does not generate the link that we can 99% he does not generate the link]
- Feature request 1: The AI code builder should be updated to also generate a requirements.txt file containing all required libraries alongside the other generated code files. [CONFIRMED · fathom/50909261 @ 00:00:36]
- Feature request 2: A new input box should be added allowing the user to specify a particular already-generated file (e.g. main.py) and request targeted modifications to only that file, without regenerating the entire project. [CONFIRMED · fathom/50909261 @ 00:02:02]
- The client is running the framework on a Mac (not Windows); Abhijeet noted that certain shell behaviors (e.g., steady output redirection) are Mac-specific and not needed on Windows. [CONFIRMED · fathom/117473152 @ 00:08:04 - 'यह विंडोस में नहीं होते हैं विंडोस में... यह मैक में']
- The root cause of benchmark failures was identified as an incorrectly activated Python virtual environment (venv manually activated externally); the fix required running 'deactivate' before executing run.sh so the code's own activation logic could work correctly. [CONFIRMED · fathom/117473152 @ 00:12:00 - 'deactivate ऐसे command डाल देना मतलब terminal पर और फिर run कर देना क्योंकि अपना जो code वो करता है activate']

## Sources

- `crm/zeeshan-rasheed` · — · crm · CRM numbers — Zeeshan Rasheed
- `fathom/50909261` · 2025-03-09 · fathom · Impromptu Zoom Meeting
- `fathom/114797497` · 2026-01-16 · fathom · Impromptu Zoom Meeting
- `fathom/117473152` · 2026-01-27 · fathom · Impromptu Zoom Meeting
