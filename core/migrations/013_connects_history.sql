-- 013: connects_history — the applied-truth rail (2026-07-08). Upwork's
-- connects-history page has NO export and its API is scope-blocked, so a daily
-- Chrome scrape (same rails as the scanner) captures the visible window
-- (~8-10 days; the UI offers no deeper history either). Every connects event:
-- applications (spend with a job link), boosts, refunds, purchases, renewals,
-- availability-badge spends. Canonical id dedupes across daily overlapping
-- scrapes. 'applied' rows give the Pipeline a true applications trend; with
-- the rail live, an applied count of 0 is a TRUE zero, not missing data.
CREATE TABLE connects_history (
    id TEXT PRIMARY KEY,              -- md5(date|delta|label|job|occ)[:16]
    event_date TEXT NOT NULL,         -- YYYY-MM-DD (page shows no time)
    kind TEXT NOT NULL,               -- applied|boost|boost_refund|refund|purchase|renewal|badge|other
    label TEXT NOT NULL,              -- the page's action text, normalized
    job_id TEXT,                      -- from the row's /jobs/~02<id>/ link
    job_title TEXT,
    connects_delta INTEGER NOT NULL,  -- +/- connects
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_connects_history_date ON connects_history(event_date);
CREATE INDEX idx_connects_history_kind ON connects_history(kind, event_date);
