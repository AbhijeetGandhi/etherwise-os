-- 003_cockpit.sql — cockpit-local nudge state (M2 v1).
-- v1 nudges are proto-nudges; M4 Chief of Staff owns the real Airtable
-- Nudges/Commitments schema. This local table persists the founder's
-- done/snooze/dismiss decisions across restarts until then (decision B1b).
CREATE TABLE nudge_state (
    item_key   TEXT PRIMARY KEY,   -- 'followup:<thread_id>', 'hotlead:<job_id>', 'nudge:<ref>'
    state      TEXT NOT NULL,      -- done | snoozed | dismissed
    snooze_until TEXT,             -- ISO date (snoozed only)
    note       TEXT,
    source     TEXT,               -- 'cockpit'
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_nudge_state ON nudge_state(state);
