-- 041: ClickUp time entries, so a period's hours can be defended (2026-10-19).
--
-- Abhijeet bills by the hour and the hours live in ClickUp. Working out what to
-- invoice meant opening the sprint list and adding up by eye, which is why a
-- period total was only ever approximately known.
--
-- ENTRIES, NEVER TOTALS. One row per ClickUp time entry, keyed on ClickUp's own
-- entry id so a re-sync is idempotent. Totals are computed at read time. Storing
-- pre-aggregated period sums would make the carry-over rule unverifiable (you
-- could no longer see WHICH entries a number was made of) and would make a
-- re-sync destructive rather than repeatable.
--
-- WHY start_ms AND created_ms ARE BOTH KEPT. `start` is when the work happened;
-- `at` is when the entry was written. A period is decided by start_ms — that is
-- the rule that stops a task carried into the next sprint from being billed
-- twice. created_ms exists because hours keep ARRIVING after a period closes:
-- measured 2026-10-19, 13.6h landed at 03:45-03:52 against a sprint that ended
-- the previous day, and Horizons 43's total was 26.05h when read on 7 Oct and
-- 32.55h twelve days later. Nothing was broken either time — the number moves.
-- Keeping created_ms is what lets a report say what was known when.
--
-- space_id is the client spine: space -> client via config/clients.yaml, which
-- is the only roster in this codebase. folder/list are kept because "70 of those
-- hours were Vibe Coding Projects" has to be answerable without reopening
-- ClickUp.
CREATE TABLE IF NOT EXISTS hours_entries (
    id             TEXT PRIMARY KEY,          -- ClickUp time-entry id
    task_id        TEXT,
    task_name      TEXT,
    space_id       TEXT NOT NULL,
    space_name     TEXT,
    folder_id      TEXT,
    folder_name    TEXT,
    list_id        TEXT,
    list_name      TEXT,
    client_id      TEXT,                      -- resolved from the registry
    user_id        TEXT NOT NULL,
    user_name      TEXT,
    duration_ms    INTEGER NOT NULL,
    start_ms       INTEGER NOT NULL,          -- when the work happened
    end_ms         INTEGER,
    created_ms     INTEGER,                   -- when the entry was written
    billable       INTEGER DEFAULT 0,         -- ClickUp's own flag (unused as of
                                              -- 2026-10-19: false on every entry)
    tags_json      TEXT,                      -- the "on us" mark, once the team
                                              -- starts tagging (from this week)
    description    TEXT,
    source         TEXT,                      -- clickup | api
    -- a row ClickUp no longer returns is marked, never deleted: a number that
    -- was reported to a client has to stay explicable afterwards
    gone_at        TEXT,
    first_seen_at  TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hours_entries_start
    ON hours_entries (start_ms);
CREATE INDEX IF NOT EXISTS idx_hours_entries_client_start
    ON hours_entries (client_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_hours_entries_list
    ON hours_entries (list_id);

-- The sprint lists, with their windows parsed from the list NAME
-- ("Horizons 44 (10/5 - 10/18)"). Stored rather than re-parsed per read: the
-- names carry no year, Horizons skips 3/33/34/37 and changed cadence weekly ->
-- fortnightly at 19, so the parse is fiddly enough to be worth doing once and
-- auditing. parse_ok=0 with the raw name kept is a VISIBLE error on the page,
-- never a silently skipped period.
CREATE TABLE IF NOT EXISTS hours_periods (
    list_id        TEXT PRIMARY KEY,
    list_name      TEXT NOT NULL,
    space_id       TEXT NOT NULL,
    client_id      TEXT,
    starts_on      TEXT,                      -- YYYY-MM-DD, IST
    ends_on        TEXT,
    parse_ok       INTEGER NOT NULL DEFAULT 0,
    parse_note     TEXT,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hours_periods_client
    ON hours_periods (client_id, starts_on);
