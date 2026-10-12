-- 040: Upwork's screening questions on a scanned job (Abhijeet, 2026-10-12).
--
-- His words: "you are not answering the questions in the job post. that needs
-- to be answered seperately". The MCP `get` returns a post's screening
-- questions (feeds/mcp_feed.py puts them in mcp_extra), but the claim path
-- stored none of mcp_extra, so the draft writer never saw them. Written by
-- scan_pipeline._store_screening right after a job is claimed; read by
-- scoring.job_brief. A JSON list of strings; NULL when the post has none or
-- the job arrived without a `get`.
ALTER TABLE scored_jobs ADD COLUMN screening_questions_json TEXT;
