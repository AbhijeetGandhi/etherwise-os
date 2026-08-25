-- 029: room_links — an email (events row) tied to the Upwork room whose
-- counterparty sent it (Upwork chat, 2026-08-26).
--
-- WHY (Abhijeet, 04:58): David Séjourné emailed abhijeetgandhi01@gmail.com on
-- 24 Aug with the brief for the first workflow; the next day his Upwork
-- message said "I've sent you an email". The IMAP rail read the mail, the
-- sweep EXCLUDED it (imap-low-base-rate:no-client-signal — he is a prospect,
-- not a registry client), and the draft-writer, which only ever reads the
-- Upwork room, replied "I don't see the email". The room and the mail were
-- both in the DB and nothing joined them. This table is that join.
--
-- One row per event; a room can hold many. `how` records the rule that made
-- the link (name-match today), `matched_name` the Upwork display name it
-- matched ("David S."), so a wrong link can be found by reading. Nothing here
-- changes events.state — the sweep queues a linked mail like any other real
-- mail (Review still sees it); the link is what the Upwork tab and the
-- draft-writer read.
CREATE TABLE IF NOT EXISTS room_links (
    event_id     TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL,
    how          TEXT NOT NULL,
    matched_name TEXT,
    linked_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_room_links_thread ON room_links(thread_id, linked_at);
