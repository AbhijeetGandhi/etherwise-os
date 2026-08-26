-- Phase 3, the hiring side (Abhijeet, 2026-08-27 00:30: "In — the applicant
-- pull"). His OWN job postings on the CLIENT org (1715797263477194752) and the
-- people who applied to them. Nothing here is money and nothing here is a
-- freelancer-side row: `contracts`, `proposals` and `offers` are what HE bid
-- on, these are what people bid on HIM. Kept apart on purpose — one table
-- holding both sides is how a win rate ends up counting the wrong direction.

CREATE TABLE upwork_job_postings (
    id TEXT PRIMARY KEY,                 -- the posting id (get_job_posting list)
    title TEXT,
    status TEXT,                         -- PUBLISHED | FILLED | CANCELLED | DRAFT
    posting_type TEXT,                   -- HOURLY | FIXED
    access TEXT,                         -- PUBLIC_INDEX | ACCESS_PUBLIC | ...
    created_dt TEXT,
    updated_dt TEXT,
    premium INTEGER,
    -- the funnel, straight off the list call; no second read needed
    applicants INTEGER,
    new_applicants INTEGER,              -- applied and not yet opened by him
    shortlisted INTEGER,
    messaged INTEGER,
    offered INTEGER,
    hired INTEGER,
    pending_invitations INTEGER,
    -- what the market pays for this kind of work (get_rate_insights, client org)
    rate_low REAL,
    rate_high REAL,
    rate_occupation TEXT,
    rate_sample INTEGER,
    rate_fetched_at TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_job_postings_status ON upwork_job_postings(status, updated_dt DESC);

CREATE TABLE upwork_applicants (
    id TEXT PRIMARY KEY,                 -- the proposal id on HIS posting
    job_posting_id TEXT NOT NULL,
    freelancer_id TEXT,
    freelancer_name TEXT,                -- "Mark Y." — Upwork abbreviates the surname
    org_name TEXT,                       -- "Mark Young" — the full name lives here
    bid_display TEXT,                    -- "USD 15.0" verbatim; rawValue is EMPTY on this side
    bid_minor INTEGER,                   -- parsed out of the display value, or NULL
    status TEXT,                         -- Created (applied) | Activated (invite accepted) | Archived
    status_label TEXT,
    -- Read / Shortlisted / Recommended / Sponsored live in ANNOTATIONS, not in
    -- the status. A shortlisted applicant is still status 'Created'.
    annotations_json TEXT,
    shortlisted INTEGER NOT NULL DEFAULT 0,
    seen_by_him INTEGER NOT NULL DEFAULT 0,
    cover_snippet TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_applicants_job ON upwork_applicants(job_posting_id, shortlisted DESC);
