-- 033: the money that exists but no invoice has been written for yet, and the
-- invitations nothing in v3 could see (Upwork chat 2026-08-26 22:45; his
-- "money in flight + offers" and "the dashboard heartbeat").
--
-- WHY. `transactions` is invoiced money. Hours he logged this week are real
-- work at a known rate that Upwork will invoice on Sunday, and a fixed-price
-- milestone is money a client either funded or did not — neither reaches the
-- ledger, so Money could not answer "what am I owed". The first read found
-- both halves: $565.83 of Mon-Wed hours uninvoiced, and Mustafa's contract
-- 43986743 with $3,000 across two milestones NOT FUNDED and six weeks past
-- due, invisible to every screen.
--
-- Nothing here is the ledger and nothing here is revenue: these tables are
-- what is COMING, kept beside the ledger, never summed into the P&L.
CREATE TABLE IF NOT EXISTS upwork_timesheets (
    cursor_id     TEXT PRIMARY KEY,             -- the wire's own cursor: unique per entry (a contract can have two on one day)
    contract_id   TEXT NOT NULL,
    term_id       TEXT,                         -- can differ from contract_id (44315695 logs against 44336214)
    contract_title TEXT,
    date_worked   TEXT NOT NULL,                -- YYYY-MM-DD
    week_worked   TEXT,                         -- the Monday of the Upwork week
    hours         REAL NOT NULL,
    charges_minor INTEGER NOT NULL,             -- USD cents, always positive (it is earnings-to-be)
    online_hours  REAL NOT NULL DEFAULT 0,      -- tracker time vs manual: every row so far is manual
    memo          TEXT,                         -- his own work note for the day
    invoiced_at   TEXT,                         -- set when a transactions row covers this week (the doctor's tie)
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_upwork_timesheets_week ON upwork_timesheets(week_worked, contract_id);
CREATE INDEX IF NOT EXISTS idx_upwork_timesheets_date ON upwork_timesheets(date_worked);

-- One row per milestone of a fixed-price contract. `state` is the authority,
-- NOT funded_minor: the wire reports fundedAmount == the milestone amount even
-- on a NotFunded milestone (checked on 43986743).
CREATE TABLE IF NOT EXISTS upwork_milestones (
    id            TEXT PRIMARY KEY,
    contract_id   TEXT NOT NULL,
    sequence_id   INTEGER,
    description   TEXT,
    due_dt        TEXT,
    amount_minor  INTEGER,                      -- depositAmount, USD cents
    funded_minor  INTEGER,
    paid_minor    INTEGER,
    state         TEXT,                         -- Paid | NotFunded | Active | Submitted | ...
    state_label   TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_upwork_milestones_contract ON upwork_milestones(contract_id, sequence_id);
CREATE INDEX IF NOT EXISTS idx_upwork_milestones_state ON upwork_milestones(state, due_dt);

-- Invitations a client sent him. list_freelancer_proposals action=invitations
-- and the dashboard's invitations block both report them; v3 has never held
-- one. An invitation is a lead that skipped the queue - it belongs on Today.
CREATE TABLE IF NOT EXISTS upwork_invitations (
    id            TEXT PRIMARY KEY,             -- invitation_uid
    job_id        TEXT,
    job_title     TEXT,
    client_name   TEXT,
    state         TEXT,
    created_dt    TEXT,
    payload_json  TEXT,
    seen_at       TEXT,                         -- when a human dealt with it (a card Done)
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_upwork_invitations_created ON upwork_invitations(created_dt);
