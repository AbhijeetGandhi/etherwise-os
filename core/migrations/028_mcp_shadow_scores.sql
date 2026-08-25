-- 028: mcp_shadow_scores — the deliberate, budgeted scoring pass over shadow
-- rows (Upwork chat, 2026-08-25). Migration 027 left mcp_shadow_jobs.score NULL
-- "until someone runs a deliberate, budgeted scoring pass"; this is that pass's
-- home. Same principle as 027: its own table, never a row in scored_jobs, so a
-- shadow score can never reach a ClickUp push, a draft or the pipeline funnel.
--
-- Two questions it answers, both inputs to the cutover decision:
--   A. of the MCP-only jobs that pass the hard rules, how many would be hot
--      (scraper_score IS NULL rows);
--   B. how far does the MCP-described job score from the scraper-described
--      one — the drift that sets the recalibrated bands (scraper_score kept
--      beside the shadow score for the rows both feeds saw).
CREATE TABLE IF NOT EXISTS mcp_shadow_scores (
    id             TEXT PRIMARY KEY,          -- = mcp_shadow_jobs.id
    score          INTEGER NOT NULL,          -- effective (post-gate), scored_jobs.score scale
    gated          INTEGER NOT NULL DEFAULT 0,
    loom_flag      INTEGER NOT NULL DEFAULT 0,
    breakdown_json TEXT,
    scraper_score  INTEGER,                   -- scored_jobs.score when the pass ran; NULL = MCP-only
    model          TEXT NOT NULL,
    run_id         INTEGER,                   -- runs.id of the chunk that scored it
    scored_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mcp_shadow_scores_scraper
    ON mcp_shadow_scores(scraper_score);
