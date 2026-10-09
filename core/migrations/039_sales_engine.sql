-- 039: the sales engine — tests, conversation readings, job snapshots,
-- reactivation (Upwork brief Phase 2; Abhijeet, 2026-10-09).
--
-- His words: "now Upwork is our sales engine ... the scanner, then people we
-- apply to, then the people who opened our proposal, then the people who
-- replied, then the people who actually hired us and then how much they are
-- paying ... each of these metrics can then reflect back to the scanner ...
-- and the proposal writer". And, the morning before: "I always want to make
-- this a game ... we try it ... and then we see what the results were".
--
-- Five stores, each with one writer and one meaning:
--
--   experiments            one row per test: the hypothesis, the one number
--                          that decides it, the size and end rule, and — once
--                          called — the decision and the lesson. The log of
--                          everything tried lives here, so nothing is retried
--                          by accident and nothing is forgotten.
--   experiment_arms        the versions a test compares, with the exact
--                          instruction the draft writer gets for each (NULL =
--                          unchanged) and how a SENT letter is recognised.
--   experiment_assignments which job got which version, and how we know: at
--                          draft time ('draft'), from the sent letter
--                          ('detected'), or by hand ('manual'). The proposal
--                          id is filled once the proposal is seen; a letter he
--                          rewrote away from its version is kept and flagged
--                          (`sent_arm`), never silently moved to the other side.
--   conversation_codes     one AI reading per conversation version: how far it
--                          got, why it stalled, who went quiet, the objections,
--                          the price talk, a reactivation angle. Keyed on the
--                          newest message it saw, so a conversation that moves
--                          on is read again and the old reading stays.
--   job_snapshots          what a job looked like at a moment that matters —
--                          when the scanner claimed it, when we applied — so
--                          "was it crowded, was the client active?" is known
--                          BEFORE the money is spent, not only after the job
--                          closed (the 8 Oct reads saw only final counts).
--   reactivation_log       which past client got a check-in draft which week
--                          (at most 5 a week, his rule), so nobody is drafted
--                          twice and the list moves on.

CREATE TABLE IF NOT EXISTS experiments (
  id            TEXT PRIMARY KEY,               -- 't1-volume-openings'
  title         TEXT NOT NULL,
  hypothesis    TEXT,
  change        TEXT,                           -- what differs, in words
  scope         TEXT NOT NULL DEFAULT 'cold'
                CHECK (scope IN ('cold', 'invite', 'all')),
  metric        TEXT NOT NULL
                CHECK (metric IN ('opened', 'replied', 'replied_of_opened', 'hired')),
  min_per_arm   INTEGER NOT NULL DEFAULT 40,
  wait_days     INTEGER NOT NULL DEFAULT 7,
  max_weeks     INTEGER NOT NULL DEFAULT 6,
  status        TEXT NOT NULL DEFAULT 'planned'
                CHECK (status IN ('planned', 'running', 'decided', 'stopped')),
  started_at    TEXT,
  ended_at      TEXT,
  decision      TEXT CHECK (decision IS NULL OR decision IN ('keep', 'revert', 'variation')),
  lesson        TEXT,
  next_idea     TEXT,
  decided_by    TEXT,
  approved_by   TEXT,                           -- who said go ('Abhijeet 2026-10-09')
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS experiment_arms (
  experiment_id     TEXT NOT NULL REFERENCES experiments(id),
  arm               TEXT NOT NULL,              -- 'A' | 'B'
  label             TEXT NOT NULL,
  description       TEXT,
  draft_instruction TEXT,                       -- added to the draft's user message; NULL = unchanged
  detect_rule       TEXT,                       -- how a sent letter is recognised (proposal_letter)
  PRIMARY KEY (experiment_id, arm)
);

CREATE TABLE IF NOT EXISTS experiment_assignments (
  experiment_id TEXT NOT NULL REFERENCES experiments(id),
  job_id        TEXT NOT NULL,
  arm           TEXT NOT NULL,
  via           TEXT NOT NULL CHECK (via IN ('draft', 'detected', 'manual')),
  assigned_at   TEXT NOT NULL DEFAULT (datetime('now')),
  proposal_id   TEXT,
  sent_arm      TEXT,                           -- what the SENT letter looks like
  note          TEXT,
  PRIMARY KEY (experiment_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_exp_assign_proposal ON experiment_assignments(proposal_id);

CREATE TABLE IF NOT EXISTS conversation_codes (
  thread_id          TEXT NOT NULL,
  coded_through      TEXT NOT NULL,             -- newest message time the reading saw
  client_id          TEXT,
  proposal_id        TEXT,
  stage_reached      TEXT,                      -- see modules/upwork/conversation_codes.py STAGES
  outcome            TEXT,                      -- 'hired' | 'lost' | 'open' | 'ended'
  lost_reason        TEXT,                      -- one of LOST_REASONS, or NULL
  lost_detail        TEXT,
  went_quiet         TEXT,                      -- 'client' | 'us' | 'neither'
  objections_json    TEXT,
  price_talk         TEXT,
  next_step          TEXT,
  tone               TEXT,
  reactivation_angle TEXT,
  summary            TEXT,
  model              TEXT,
  cost_usd           REAL,
  coder              TEXT NOT NULL,             -- task name, or 'session' for a hand-run reading
  coded_at           TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (thread_id, coded_through)
);

CREATE TABLE IF NOT EXISTS job_snapshots (
  job_id             TEXT NOT NULL,
  taken_at           TEXT NOT NULL,
  reason             TEXT NOT NULL CHECK (reason IN ('claim', 'apply', 'read')),
  total_applicants   INTEGER,
  interviewing       INTEGER,
  invites_sent       INTEGER,
  unanswered_invites INTEGER,
  hired              INTEGER,
  offered            INTEGER,
  avg_rate_bid       REAL,
  min_rate_bid       REAL,
  max_rate_bid       REAL,
  connects_cost      INTEGER,
  client_last_activity TEXT,
  raw_json           TEXT,                      -- the rest of what the read carried
  source             TEXT NOT NULL,             -- 'mcp_claim' | 'mcp_get' | 'chrome'
  PRIMARY KEY (job_id, taken_at, reason)
);

CREATE TABLE IF NOT EXISTS reactivation_log (
  client_id   TEXT NOT NULL,
  week        TEXT NOT NULL,                    -- ISO year-week the draft was made
  thread_id   TEXT,
  draft_id    INTEGER,
  score       REAL,
  why         TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (client_id, week)
);
