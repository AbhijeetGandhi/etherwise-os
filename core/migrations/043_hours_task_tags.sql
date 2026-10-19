-- 043: the task's TAGS and STATUS on every time entry (2026-10-19).
--
-- How Abhijeet's own Sprint 22 export for Hermon is organised: by tag —
-- cashswipe, eskool, mc, spm, cashcow, vibe coding … — each tag a workstream
-- (for Hermon, one of its own sub-clients), task rows under it, a total per
-- group. That is the shape a client recognises their bill in, and neither the
-- folder nor the list carries it; only the task's tags do.
--
-- `include_task_tags=true` on the time-entries endpoint returns each entry's
-- task tags with ClickUp's own tag colours, and the task object carries its
-- current status, so both ride on the entry and need no extra request per task.
--
-- task_status is the task's CURRENT status, not its status when the time was
-- logged — it answers "where is this work now", which is what a report asks.
ALTER TABLE hours_entries ADD COLUMN task_tags_json TEXT;
ALTER TABLE hours_entries ADD COLUMN task_status TEXT;
ALTER TABLE hours_entries ADD COLUMN task_status_type TEXT;
ALTER TABLE hours_tasks ADD COLUMN tags_json TEXT;
