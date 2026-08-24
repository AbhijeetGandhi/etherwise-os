-- 027: mcp_shadow_jobs — what the MCP feed WOULD have surfaced, run alongside
-- the Chrome scanner without touching it. The definitive Test B instrument:
-- after 24-48h, scripts/mcp_shadow_report.py compares this table against
-- scored_jobs per day (found-by-both / scraper-only / mcp-only) and the
-- cutover decision is made on those numbers rather than on argument.
--
-- Deliberately ITS OWN TABLE, not rows in scored_jobs: shadow data must be
-- unable to leak into ClickUp pushes, drafts, or the pipeline funnel. Same
-- shadow-mode principle as the ledger (§ SHADOW_MODE) — observe everything,
-- write into the live path nothing.
--
-- No model scoring here. hard_rule_skip is stored because it is free and
-- deterministic; a score column exists but stays NULL until someone runs a
-- deliberate, budgeted scoring pass over a day of shadow rows.
CREATE TABLE IF NOT EXISTS mcp_shadow_jobs (
    id            TEXT PRIMARY KEY,          -- marketplace job id
    title         TEXT,
    normalized_json TEXT NOT NULL,           -- the exact parse_job-shaped dict
    mcp_extra_json  TEXT,                    -- bid stats, connects, liveness…
    found_by      TEXT NOT NULL,             -- first query that surfaced it
    also_found_by TEXT,                      -- comma list, grows per run
    hard_rule_skip TEXT,                     -- free, deterministic
    score         INTEGER,                   -- NULL until a deliberate pass
    in_scored_jobs INTEGER NOT NULL DEFAULT 0, -- scraper saw it too (at insert)
    got_detail    INTEGER NOT NULL DEFAULT 0,  -- 1 when a `get` enriched it
    created_dt    TEXT,                      -- job's own publish time
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_shadow_first_seen
    ON mcp_shadow_jobs(first_seen_at);
