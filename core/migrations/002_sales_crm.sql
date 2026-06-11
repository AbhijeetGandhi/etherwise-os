-- 002_sales_crm.sql — sales + crm domains (import-ready, Day 3).
-- Clean v3 names mapped from v2 by bin/import-v2:
--   scored_jobs <- jobs · proposals <- vendor_proposals · threads <- rooms
--   messages <- stories (room_id -> thread_id) · contracts/offers/
--   transactions/drafts/clients/communications keep their names.
-- Columns carried faithfully from v2 (they ARE the working shape); per-module
-- redesign happens at each design session, not here. people is a kernel stub
-- (populated at CRM module session; decision #8 merge needs Abhijeet's review).

-- ops linkage that should have been in 001:
ALTER TABLE claude_usage ADD COLUMN run_id INTEGER REFERENCES runs(id);

CREATE TABLE scored_jobs (
  id TEXT PRIMARY KEY,             -- bare numeric marketplaceJobPostingId
  title TEXT,
  description TEXT,
  created_dt TEXT,
  fetched_at TEXT,
  feed_source TEXT,                -- most_recent|best_matches|my_feed|firehose|curated|previousClients
  contract_type TEXT,
  hourly_min REAL, hourly_max REAL, fixed_budget REAL, weekly_budget REAL,
  total_applicants INTEGER,
  experience_level TEXT, engagement TEXT, engagement_duration TEXT,
  category TEXT, subcategory TEXT, skills_json TEXT,
  client_company TEXT, client_country TEXT, client_total_spent REAL,
  client_hires INTEGER, client_rating REAL, client_payment_verified INTEGER,
  applied INTEGER DEFAULT 0,
  score INTEGER, score_breakdown_json TEXT, hard_rule_skip TEXT,
  draft_proposal TEXT, draft_word_count INTEGER,
  clickup_task_id TEXT, clickup_task_url TEXT, airtable_record_id TEXT,
  loom_flag INTEGER DEFAULT 0,
  status TEXT,                     -- New|Skipped|Scored|Drafted|ClickUp Created|Repost|Fetched|Phantom
  score_override INTEGER, manual_note TEXT, job_url TEXT,
  first_scored_at TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_scored_jobs_status ON scored_jobs(status);
CREATE INDEX idx_scored_jobs_fetched ON scored_jobs(fetched_at DESC);

CREATE TABLE proposals (
  id TEXT PRIMARY KEY,
  status TEXT,                     -- terminal: Won|Lost|Expired|Withdrawn|Skipped — NEVER flipped
  upwork_status TEXT,
  status_reason TEXT,
  charge_rate REAL, charge_currency TEXT,
  cover_letter TEXT,
  marketplace_job_id TEXT,
  job_title TEXT,
  client_company TEXT, client_country TEXT, client_total_spent REAL,
  created_dt TEXT, modified_dt TEXT,
  thread_id TEXT,                  -- v2: room_id
  airtable_record_id TEXT,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE contracts (
  id TEXT PRIMARY KEY,
  title TEXT, upwork_status TEXT, airtable_status TEXT,
  contract_type TEXT,
  start_dt TEXT, end_dt TEXT,
  weekly_hours_limit INTEGER, hourly_rate REAL, weekly_charge REAL,
  paused INTEGER DEFAULT 0,
  job_id TEXT, job_title TEXT, freelancer_id TEXT, client_company TEXT,
  profile TEXT,
  thread_id TEXT,                  -- v2: room_id
  airtable_record_id TEXT,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE offers (
  id TEXT PRIMARY KEY,
  title TEXT, state TEXT, type TEXT,
  job_id TEXT, client_id TEXT, client_name TEXT,
  message_to_contractor TEXT,
  thread_id TEXT,                  -- v2: room_id
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE transactions (
  record_id TEXT PRIMARY KEY,
  type TEXT, upwork_type TEXT, accounting_subtype TEXT,
  description TEXT, description_ui TEXT,
  amount REAL, currency TEXT, amount_credited REAL,
  creation_dt TEXT, fully_paid_dt TEXT,
  status TEXT,
  assignment_developer TEXT, assignment_company TEXT,
  assignment_team_company_id TEXT,
  related_transaction_id TEXT,
  payment_guaranteed INTEGER,
  profile TEXT, wise_id TEXT,
  period_start DATE, period_end DATE,
  dedup_key TEXT, source TEXT,
  canonical_key TEXT,
  airtable_record_id TEXT,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_transactions_canonical_key
  ON transactions(canonical_key);

CREATE TABLE threads (                -- v2: rooms
  id TEXT PRIMARY KEY,
  room_name TEXT, topic TEXT, room_type TEXT,
  contract_id TEXT,
  proposal_id TEXT,                   -- v2: vendor_proposal_id
  offer_ids_json TEXT,
  num_unread INTEGER, num_unread_mentions INTEGER,
  last_visited_dt TEXT, latest_message_id TEXT, latest_message_dt TEXT,
  awaiting_reply_from TEXT,
  bucket TEXT, tier TEXT, snooze_until DATE,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE messages (               -- v2: stories
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES threads(id),
  message TEXT, created_dt TEXT,
  sender_user_id TEXT, sender_user_name TEXT,
  sender_org_id TEXT, sender_org_name TEXT,
  direction TEXT,
  has_attachment INTEGER DEFAULT 0, attachments_json TEXT,
  ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_messages_thread ON messages(thread_id, created_dt);

CREATE TABLE drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id TEXT,                     -- v2: room_id
  job_id TEXT,
  draft_kind TEXT NOT NULL,
  tier TEXT,
  generated_at TEXT NOT NULL DEFAULT (datetime('now')),
  body TEXT, word_count INTEGER, rationale TEXT,
  sent_status TEXT DEFAULT 'pending'
);

CREATE TABLE clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  folder_name TEXT,
  status TEXT,
  upwork_person_names_json TEXT,
  airtable_record_id TEXT,
  total_spent REAL, total_hours REAL, total_paid REAL,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE people (                 -- kernel stub; CRM session fills design
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  role TEXT,
  client_id INTEGER REFERENCES clients(id),
  emails_json TEXT,
  airtable_record_id TEXT,
  notes TEXT,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE communications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id TEXT UNIQUE NOT NULL,
  date TEXT, channel TEXT, direction TEXT,
  sender_name TEXT, sender_identifier TEXT,
  body_preview TEXT, body_full TEXT, thread_url TEXT,
  client_id INTEGER REFERENCES clients(id),
  related_proposal_id TEXT, related_contract_id TEXT,
  has_attachment INTEGER DEFAULT 0,
  snooze_until DATE,
  airtable_record_id TEXT,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);
