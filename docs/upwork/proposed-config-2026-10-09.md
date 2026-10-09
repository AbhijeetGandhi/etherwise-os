# Proposed: switching on the three new agents (2026-10-09)

`core/config.py` is not agent-writable, so this is a proposal, not a change.
Nothing below is live until Abhijeet says go. All three need the Upwork
sign-ins renewed and AI credit (the reader and the win-back drafter make
model calls; the progress email does not).

## 1. Health lines (`core/config.py`, `SOURCES`, after the `upwork_outcomes` entry)

```python
    {"key": "upwork_progress", "label": "Progress email (Mon/Wed/Fri + Sunday)",
     "task_name": "upwork_progress", "max_age_hours": 80,      # longest gap Fri 08:30 -> Mon 08:30 + grace
     "degrade_kinds": (), "critical": False, "backs": "pipeline", "empty_ok": True,
     # catch_up is SAFE: the task refuses a second send the same day
     "launchd_label": "io.etherwise.v3.upwork-progress", "catch_up": True,
     "why": "the game's scoreboard in his inbox (his cadence, 2026-10-09);"
            " stale means three days of proposals nobody summed up"},
    {"key": "upwork_conversation_codes", "label": "Conversation reader (AI, every 2h)",
     "task_name": "upwork_conversation_codes", "max_age_hours": 6,
     "degrade_kinds": (), "critical": False, "backs": "pipeline", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.upwork-conversation-codes", "catch_up": False,
     "why": "why conversations did or did not become hires; capped at $25 for"
            " the first pass and $0.50 a run; never reads Globaltize"},
    {"key": "upwork_reactivation", "label": "Win-back drafts (Mondays)",
     "task_name": "upwork_reactivation", "max_age_hours": 192,
     "degrade_kinds": (), "critical": False, "backs": "pipeline", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.upwork-reactivation", "catch_up": True,
     "why": "at most five check-in drafts a week for past clients; he sends them"},
```

## 2. The schedules (already written, not installed)

`launchd/proposed/` holds the three plists. To switch one on:

```bash
git mv launchd/proposed/io.etherwise.v3.upwork-progress.plist launchd/
bash launchd/install.sh --only io.etherwise.v3.upwork-progress
```

(the same for `upwork-conversation-codes` and `upwork-reactivation`).

## 3. "Run now" in the cockpit (`modules/cockpit/actions.py`, `RERUN_JOBS`)

Add once the plists are installed (a kick needs a loaded job):

```python
    "upwork_progress": "io.etherwise.v3.upwork-progress",
    "upwork_conversation_codes": "io.etherwise.v3.upwork-conversation-codes",
    "upwork_reactivation": "io.etherwise.v3.upwork-reactivation",
```
