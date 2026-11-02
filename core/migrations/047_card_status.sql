-- 047: the ClickUp card, read back (Abhijeet, 2026-11-02: "you have drafts
-- ready to send. check their clickup status — if I have skipped that or moved
-- them to another bucket it should be updated").
--
-- v3 PUSHES a card for every scored job (scripts/clickup_push.py) and never
-- read it again, except the crowd check on its own candidates. So a draft he
-- had skipped on the board, or moved to Low Priority, stayed "ready to send"
-- on Today until the applied rail happened to see it. These four columns are
-- the card's LIVE state, kept by modules/upwork/card_status.py (hourly in
-- upwork_sync, and on "Fetch latest"):
--   card_status       the status name as his lists spell it — new | applied |
--                     interview | won | skipped | expired | lost ('deleted'
--                     when the card is gone; NULL = never read)
--   card_status_type  ClickUp's type for it: open | custom | done | closed
--   card_list_id      which list it sits in NOW (icp / hot / standard / low /
--                     invites — config.CLICKUP_LISTS), so a move is visible
--   card_checked_at   when it was read (UTC)
ALTER TABLE scored_jobs ADD COLUMN card_status TEXT;
ALTER TABLE scored_jobs ADD COLUMN card_status_type TEXT;
ALTER TABLE scored_jobs ADD COLUMN card_list_id TEXT;
ALTER TABLE scored_jobs ADD COLUMN card_checked_at TEXT;
