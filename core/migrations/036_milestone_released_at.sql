-- When a fixed-price milestone was RELEASED by the client.
--
-- Abhijeet, 2026-08-27: "we can consider that fifteen hundred as the revenue,
-- because it's a fixed amount and it was released by the client. That means
-- it's gonna come out and, for now, I've been working with that for three
-- years, and I never had this situation where a fixed price was reversed."
--
-- So a released milestone is revenue the day it is released. Upwork writes the
-- earning transaction days later, dated by the PAYOUT — his milestone 1 on the
-- same contract was released in July and the Fixed Earning lands 30 July — so
-- without a release date the same $1,500 would be counted in the month it was
-- released AND again in the month Upwork paid it.
--
-- Upwork's milestone node carries no paid-at timestamp, so this is stamped the
-- first time the rail SEES the state become Paid. That is accurate to the run
-- interval (the money-in-motion contracts are now re-read every run), and it
-- is never back-dated on a guess: a milestone already Paid before this column
-- existed keeps NULL and is left to the ledger.

ALTER TABLE upwork_milestones ADD COLUMN released_at TEXT;
