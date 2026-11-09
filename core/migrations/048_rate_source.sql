-- 048: where a job's posted rate and its text were read from (2026-11-10).
--
-- He caught a $15-20/hr job scored 26 and carded on 9 Nov. From 6 Nov Upwork
-- refuses `get` once the day's allowance of job opens is spent, and the scan
-- read the hourly rate from `get` alone — so for 19 hours a day every hourly
-- job looked RATE-UNSTATED, though its search row said "$15.00–$20.00/hr".
-- feeds/mcp_feed.py now reads the row too; these record which one spoke:
--   rate_source  get | search | none (no budget posted on the row) | unread
--                (a budget we could not read and no get: hard_rule_skip
--                skips it as rate_unread, never scores it blind)
--   post_source  get (the post) | search (the full text a keyword search
--                returns with include_full_details) | snippet (the first
--                ~300 characters of a Best Matches row)
-- NULL on every row before this migration and on Chrome-path rows.
ALTER TABLE scored_jobs ADD COLUMN rate_source TEXT;
ALTER TABLE scored_jobs ADD COLUMN post_source TEXT;
