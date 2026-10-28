-- 045: the LinkedIn engagement tracker, copied in read-only (2026-10-28).
--
-- The tracker lives in Airtable (base applkzewYyLbRmcss, "LinkedIn Engagement")
-- and is OWNED by the social sessions, which edit it by hand while they work.
-- v3 only reads it, every two hours (modules/linkedin/sync.py). Spec:
-- etherwise-v3-linkedin-brief.md.
--
-- GENERIC ON PURPOSE. One row per Airtable record, the fields kept as JSON
-- under their Airtable NAMES. The social agent adds fields as the process
-- evolves (the funnel table appeared four days after the base did; four
-- attribution fields and a whole table landed on the 28th), and a column per
-- field would turn every one of those into a migration and a broken read in
-- between. The readers pick fields by name and the sync files
-- `linkedin_schema_drift` when one they depend on goes missing — so a rename
-- is loud instead of a quiet zero.
--
-- table_key is OUR stable name for the table (people, comments, messages,
-- candidates, posts, profile), mapped to Airtable table IDs in
-- config.LINKEDIN_TABLES. Table ids survive a rename in Airtable; names do not.
--
-- A record Airtable stops returning is marked gone_at, never deleted: a figure
-- that was on the screen yesterday must stay explicable today.
CREATE TABLE IF NOT EXISTS li_records (
    table_key      TEXT NOT NULL,
    record_id      TEXT NOT NULL,             -- Airtable rec…
    fields_json    TEXT NOT NULL,             -- {field name: value}, as returned
    created_time   TEXT,                      -- Airtable createdTime (UTC ISO)
    first_seen_at  TEXT NOT NULL,             -- IST wall clock, first sync that saw it
    updated_at     TEXT NOT NULL,             -- IST wall clock, last sync that saw it
    changed_at     TEXT,                      -- IST wall clock, last time fields differed
    gone_at        TEXT,                      -- set when Airtable stops returning it
    PRIMARY KEY (table_key, record_id)
);

-- HISTORY IS OURS. Airtable keeps only the latest value of a metric — a post's
-- impressions, a person's stage — so the curve is lost the moment someone
-- edits a cell. One row per record per IST day; the day's last sync wins.
-- This is what makes "followers over time" and "how did that post grow"
-- answerable without asking anyone to keep a spreadsheet of spreadsheets.
CREATE TABLE IF NOT EXISTS li_snapshots (
    day            TEXT NOT NULL,             -- IST calendar date YYYY-MM-DD
    table_key      TEXT NOT NULL,
    record_id      TEXT NOT NULL,
    fields_json    TEXT NOT NULL,
    PRIMARY KEY (day, table_key, record_id)
);

CREATE INDEX IF NOT EXISTS idx_li_records_live ON li_records (table_key, gone_at);
CREATE INDEX IF NOT EXISTS idx_li_snapshots_rec ON li_snapshots (table_key, record_id);
