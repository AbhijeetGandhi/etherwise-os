-- 044: the crowd check (Abhijeet, 2026-10-23): "Re-read each job's proposal
-- count when the card is made and again before you apply. Flag 50+, and skip
-- 150+ unless it's Make.com or Airtable."
--
-- total_applicants stays what it always was: the count when the scan first
-- saw the job. These three are the LIVE reading, kept by
-- modules/upwork/crowd.py:
--   proposals_now         the latest proposal count read off Upwork (NULL =
--                         never read, which is not the same as zero)
--   proposals_checked_at  when it was read (UTC, SQLite datetime format)
--   crowd_state           NULL while the card is being watched; 'skipped' when
--                         the check moved the card to skipped; 'closed' when
--                         the card had already left `new` (applied, skipped
--                         by hand, ...) so it is no longer re-read
ALTER TABLE scored_jobs ADD COLUMN proposals_now INTEGER;
ALTER TABLE scored_jobs ADD COLUMN proposals_checked_at TEXT;
ALTER TABLE scored_jobs ADD COLUMN crowd_state TEXT;
