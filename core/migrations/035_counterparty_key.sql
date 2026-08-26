-- One person, several rooms (Abhijeet, 2026-08-27: "these are five different
-- [rooms] for the same person and then draw a context out of it, wherever the
-- latest message is sent").
--
-- Upwork opens a NEW room per job, so a client who hires twice holds two
-- conversations and the second one is where the talking actually is. The cards
-- already grouped by person, but every reader that needed "which rooms are
-- this person's" recomputed it from the room name in its own way — and the
-- owed-reply evidence read did not do it at all, so it judged "have we
-- answered them?" using only the rooms that happened to carry a pending draft.
--
-- Live proof on 2026-08-27: a draft from 30 July on one of Dhanya Sangolli's
-- four rooms kept "Client comms: Dhanya Sangolli" on Today, while he had
-- spoken to her on 26 August and started a $300 contract with her that day.
-- Same for Rocky Choy (draft on a room he never replied in; spoke to him
-- 25 Aug elsewhere) and Royan Stomberg.
--
-- This column is that identity, written once and read by everyone:
--   c:<client_id>   the thread is attributed to a registry client
--   p:<name-slug>   otherwise, the counterparty's name off the room
--   t:<thread_id>   no usable name — a group of one, which is the old behaviour
--
-- It is DERIVED FROM MUTABLE DATA (attributing a thread changes it), so it is
-- refreshed on every message pass rather than trusted as stable.

ALTER TABLE threads ADD COLUMN counterparty_key TEXT;
CREATE INDEX idx_threads_counterparty ON threads(counterparty_key);
