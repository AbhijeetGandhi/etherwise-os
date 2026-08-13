#!/usr/bin/env python3
"""Backfill `transactions.source` on the 17 legacy Upwork rows where it is NULL.

WHY. On 2026-08-13 the Clients drill rendered a literal `None` in the Source
column for four revenue rows — and did it on exactly the clients whose
attribution was under investigation (NETFLY, Josh Vann, Tomas Eklund), which is
why a $2,031.75 misnaming stayed invisible behind a broken traceability
contract. The rows are real, correctly-amounted, non-duplicate Upwork earnings;
only the `source` column was never written. They were minted 2026-06-12..24 by
the pre-fix Upwork sync path that keyed on `record_id='personal:<id>'` (the
"stub-twin minter", fixed in modules/upwork/sync_state.upsert_transaction, which
now writes source='Upwork' on every INSERT). A later CSV pass stamped their
`source_file`/`source_row` but never the `source` column.

WHY 'Upwork' IS NOT A GUESS. Every targeted row satisfies all three of:
  canonical_key LIKE 'upwork:%'   -- a namespace only the Upwork path mints
  record_id     LIKE 'personal:%' -- the pre-fix Upwork sync's own record id
  source_file   LIKE 'upwork-personal-%' -- the Upwork personal-profile export
No other rail writes any of these, let alone all three.

BLAST RADIUS (measured before running). `pnl_bucket` is a PERSISTED column and
pnl.compute() prefers it, so no bucket and no money changes. The only P&L field
that reads `source` for these rows is `legacy_v2_rows`, a COUNT of rows outside
RECONCILED_SOURCES — it drops by 17, which is the correct answer: these rows do
have a reconciled Upwork source. `BANK_SOURCES` does not contain 'Upwork', so
no bank-spend reclassification can fire.

Idempotent (the WHERE clause matches nothing on a second run). Dry-run default.

    PYTHONPATH=. python3 scripts/backfill_upwork_source.py [--apply]

Run on the Mac, never through the Cowork FUSE mount (SQLite WAL).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, db                                    # noqa: E402

WHERE = ("source IS NULL"
         " AND canonical_key LIKE 'upwork:%'"
         " AND record_id LIKE 'personal:%'"
         " AND source_file LIKE 'upwork-personal-%'")


def main() -> int:
    apply = "--apply" in sys.argv
    with db.connect(config.DB_PATH) as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT record_id, canonical_key, creation_dt, pnl_bucket,"
            f" amount_base_minor, source_file FROM transactions WHERE {WHERE}"
            f" ORDER BY creation_dt, record_id")]
        # anything source-NULL that does NOT satisfy the identity test is left
        # alone and REPORTED — never swept in by a looser rule.
        others = conn.execute(
            f"SELECT COUNT(*) n FROM transactions WHERE source IS NULL"
            f" AND NOT ({WHERE})").fetchone()["n"]
        for r in rows:
            print(f"  {r['creation_dt'][:10]}  {r['record_id']:<22}"
                  f"  {r['pnl_bucket']:<14} {r['amount_base_minor']:>9}")
        print(f"{'APPLY' if apply else 'DRY-RUN'}: {len(rows)} row(s) -> "
              f"source='Upwork'; {others} other source-NULL row(s) left alone")
        if apply and rows:
            conn.execute(
                f"UPDATE transactions SET source='Upwork',"
                f" updated_at=datetime('now') WHERE {WHERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
