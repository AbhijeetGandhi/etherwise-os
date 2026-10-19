-- 042: ClickUp tasks, so the weekly client report can say what was DONE
-- (2026-10-19).
--
-- Migration 041 brought in time entries, which answer "how many hours". The
-- EOW report a client actually reads answers "what did you do with them":
-- what shipped, what is still open, what is waiting on them. That is tasks,
-- and tasks are a different object with a different lifecycle — a task is
-- edited, moved between sprints and closed long after its time was logged.
--
-- STATUS AND ITS DATE ARE BOTH KEPT. "Shipped this week" is not "tasks that
-- are now complete" — a task completed three sprints ago is still complete
-- today, and listing it every week would make the report a lie by repetition.
-- `status_changed_ms` is what puts a completion in the week it happened.
--
-- Read-only, like 041: modules/hours/client.py refuses anything but GET.
CREATE TABLE IF NOT EXISTS hours_tasks (
    id                 TEXT PRIMARY KEY,       -- ClickUp task id
    name               TEXT,
    status             TEXT,                   -- raw ClickUp status
    status_type        TEXT,                   -- open | custom | closed | done
    space_id           TEXT NOT NULL,
    space_name         TEXT,
    folder_id          TEXT,
    folder_name        TEXT,
    list_id            TEXT,
    list_name          TEXT,
    client_id          TEXT,
    assignees_json     TEXT,
    created_ms         INTEGER,
    updated_ms         INTEGER,
    status_changed_ms  INTEGER,                -- when it entered this status
    closed_ms          INTEGER,
    due_ms             INTEGER,
    url                TEXT,
    -- a task ClickUp stops returning is marked, never deleted: a report that
    -- went to a client has to stay explicable afterwards (same rule as 041)
    gone_at            TEXT,
    first_seen_at      TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hours_tasks_client_status
    ON hours_tasks (client_id, status_type);
CREATE INDEX IF NOT EXISTS idx_hours_tasks_list
    ON hours_tasks (list_id);
CREATE INDEX IF NOT EXISTS idx_hours_tasks_changed
    ON hours_tasks (status_changed_ms);
