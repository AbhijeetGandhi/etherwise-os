-- 024: card subjects — the durable identity behind a Today card.
--
-- WHY (third recurrence, 2026-08-14). A follow-up card's identity is a STRING
-- derived from attribution: `followup:c:<client_id>` once the thread is
-- attributed, `followup:p:<slug>` while it is not (nudges.followup_key). The
-- founder's Done/Dismiss/Snooze is filed under that string. So every time
-- attribution IMPROVES, the string changes and his decision is orphaned:
--
--   2026-08-12  room -> person   migrated by scripts/today_repair_migrate.py
--   2026-08-13  person -> client NOT migrated — Rocky Choy and Dhanya Sangolli
--                                came back the morning after being attributed
--
-- A one-off migration per re-keying is not a fix; it guarantees a fourth.
-- The card is about a COUNTERPARTY, and the durable handle for a counterparty
-- here is the set of Upwork THREAD IDS the card stands for — primary keys that
-- never change under merge, rename or re-attribution.
--
-- So: record what a card key is ABOUT, at the moment the card is minted and at
-- the moment a decision is taken. A later lookup finds the decision through the
-- subject under ANY future key, and no writer of `threads.client_id` has to
-- remember that a re-keying happened.
--
-- subject_kind is 'thread' today; the column exists so a commitment/job-keyed
-- card can join the same mechanism without another migration.

CREATE TABLE card_subjects (
    card_key      TEXT NOT NULL,     -- nudges.dedup_key / nudge_state.item_key
    subject_kind  TEXT NOT NULL,     -- 'thread'
    subject_id    TEXT NOT NULL,     -- threads.id
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (card_key, subject_kind, subject_id)
);

CREATE INDEX idx_card_subjects_subject ON card_subjects(subject_kind, subject_id);
