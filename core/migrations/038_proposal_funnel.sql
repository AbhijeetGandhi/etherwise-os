-- 038: the proposal funnel (Upwork brief Phase 2, Loop 1).
--
-- Abhijeet, 2026-10-08: "we applied to 100 jobs, out of which 20 were opened,
-- out of which we got three replies and out of those three replies we closed
-- one. So I want to see our percentage in every stage ... and then the
-- breakdown" (boosted or not, Loom or not, the first lines, the client).
-- Decisions of the same evening: a "Funnel" view inside the Upwork tab;
-- dollars allowed on it (connects spend, revenue per won client, from the
-- ledger); the "opened" step kept current by a daily Chrome read; tests are
-- detected after the fact from the letters, not planned per proposal.
--
-- Four stores, kept apart on purpose (brief law 2: no mixed provenance):
--
--   proposal_outcomes   one row per proposal: what we sent and what came of
--                       it, from Upwork's own answers (the proposal, its room,
--                       the job record) and the letter text. Idempotent upsert
--                       on the proposal id; `source` says who wrote the row.
--   proposal_views      what the proposal's OWN PAGE said when it was read:
--                       the moment the client first opened it, and the job's
--                       live counts. Only the logged-in browser has this (the
--                       API's viewedByClient is never filled in for a
--                       freelancer — all 595 false on 8 Oct, hires included),
--                       so it is a browser-read store, one row per read.
--   proposal_insights   the dated written analysis the Funnel view shows.
--                       Appended, never overwritten: last week's reading stays
--                       readable beside this week's.
--   upwork_invites_monthly  invitations received per month (accepted ones are
--                       proposals; the ones not taken exist only in Upwork's
--                       "Archived interviews" list).

CREATE TABLE IF NOT EXISTS proposal_outcomes (
    proposal_id       TEXT PRIMARY KEY,            -- Upwork's proposal id (= proposals.id)
    sent_dt           TEXT NOT NULL,               -- UTC ISO, when the proposal was submitted
    kind              TEXT NOT NULL CHECK (kind IN ('cold', 'invite', 'arranged')),
    job_id            TEXT,
    job_title         TEXT,
    job_url           TEXT,
    job_type          TEXT,                        -- HOURLY | FIXED
    job_level         TEXT,                        -- EXPERT | INTERMEDIATE | ENTRY_LEVEL
    job_posted_dt     TEXT,
    hours_to_apply    REAL,                        -- posting -> our proposal
    tool_in_title     INTEGER,                     -- 1 when the title names a tool we sell
    job_category      TEXT,                        -- the title's job type (Airtable, Make / Zapier / n8n, ...)
    bid_amount        REAL,                        -- hourly rate, or the fixed price, in USD
    connects_bid      INTEGER,                     -- connects put on the proposal: base + boost
    connects_base     INTEGER,                     -- the job's own connects cost
    boosted           INTEGER,
    boost_outcome     TEXT,                        -- charged | outbid, bid returned | job closed, bid returned | pending
    upwork_status     TEXT,
    status_reason     TEXT,
    room_id           TEXT,                        -- the conversation, once the client opened one
    room_created_dt   TEXT,
    replied           INTEGER NOT NULL DEFAULT 0,  -- the client wrote back (a room exists)
    reply_lag_h       REAL,                        -- proposal -> the client's first message
    hired             INTEGER NOT NULL DEFAULT 0,
    client_did        TEXT,                        -- nobody | interviewed | hired_other | hired_us (the job record)
    client_company    TEXT,
    client_country    TEXT,
    client_spend      REAL,
    client_hire_rate  REAL,
    client_jobs       INTEGER,
    scanner_score     REAL,
    letter            TEXT,
    letter_words      INTEGER,
    contractions      INTEGER,
    em_dashes         INTEGER,
    has_loom          INTEGER,
    questions         INTEGER,
    opener_family     TEXT,                        -- modules/upwork/proposal_letter.OPEN_RULES
    first_lines       TEXT,                        -- what the client's list card shows
    opening_about     TEXT,                        -- client | self | fit | plan | video | mixed | other
    opening_about_src TEXT,                        -- who judged it (e.g. 'blind readers 2026-10-08')
    source            TEXT NOT NULL,               -- 'research 2026-10-08' | 'refresh'
    first_seen_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_proposal_outcomes_sent ON proposal_outcomes(sent_dt);

CREATE TABLE IF NOT EXISTS proposal_views (
    proposal_id       TEXT NOT NULL,
    read_at           TEXT NOT NULL,               -- UTC ISO, when the page was read
    page_state        TEXT NOT NULL CHECK (page_state IN ('opened', 'not_opened', 'gone')),
    viewed_at         TEXT,                        -- viewedByClientDate: the client's first open
    total_proposals   INTEGER,                     -- the job's counts as the page showed them;
    opened_proposals  INTEGER,                     -- NULL when Upwork shows none (archived, or
    shortlisted       INTEGER,                     -- older than 2026: the page reads all zeros)
    messaged          INTEGER,
    avg_bid           REAL,                        -- page field avgConnectsUsed (it is the average BID)
    avg_bid_messaged  REAL,                        -- avgConnectsUsedWithMessage (average bid of those messaged)
    avg_earnings      REAL,
    avg_jobs          REAL,
    avg_years         REAL,
    avg_jss           REAL,
    source            TEXT NOT NULL DEFAULT 'chrome',
    PRIMARY KEY (proposal_id, read_at)
);
CREATE INDEX IF NOT EXISTS idx_proposal_views_read ON proposal_views(read_at);

CREATE TABLE IF NOT EXISTS proposal_insights (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    window_lo         TEXT,                        -- the proposals it read: sent on/after
    window_hi         TEXT,                        -- ... and on/before
    author            TEXT NOT NULL,               -- 'research 2026-10-08' | 'weekly runner'
    model             TEXT,
    body_json         TEXT NOT NULL,               -- {title, summary, items[], next_tests[], cautions[]}
    run_id            INTEGER
);

CREATE TABLE IF NOT EXISTS upwork_invites_monthly (
    month             TEXT PRIMARY KEY,            -- YYYY-MM, as Upwork's list dates them
    not_taken         INTEGER NOT NULL,            -- declined by us, closed, or the job removed
    read_at           TEXT NOT NULL,
    source            TEXT NOT NULL                -- 'chrome: archived interviews'
);
