#!/usr/bin/env python3
"""Ask the classifier what each row ON THE BOARD actually is, and route it.

    PYTHONPATH=. python3 scripts/triage_review_board.py              # dry run
    PYTHONPATH=. python3 scripts/triage_review_board.py --apply
    PYTHONPATH=. python3 scripts/triage_review_board.py --limit 20

Abhijeet, 2026-08-20: *"I don't know why I am having to tell you about
donotreply upwork emails and everything. Those are obviously not client 99% of
the time. Are you not using some sort of AI to categorize these?"*

It was. On one question. `mapping._SYSTEM` asked the model which client id an
email concerned and nothing else, so for a 1Password invoice the model answered
`null` — correctly — and `null` was read as "a human must decide". Ninety rows
were on the board and about eight needed him: three copies of one Regen thread,
the Globaltize contact-form inbound, an AWS-credits reply from a named human, an
interview invitation, and a paused background check. The other eighty-two were
renewals, receipts, seat confirmations, API-token notices, auto-purchased
credits, and platform notifications about contracts that had already started.

The proposer now returns `intent` in the SAME call, and `mapping.INTENT_ROUTE`
decides the destination. This script applies that to the backlog, which the
sweep will not do on its own: the sweep classifies rows arriving as `held`, and
these are already `queued`.

It cannot delete. Every non-review outcome is `excluded` with a reason naming
the intent and the client, so 'filed' stays findable and reversible with one
UPDATE — the distinction the whole 2026-08-20 restore turned on.

NEVER TOUCHED: a row Abhijeet resolved himself, and a transactional sender
(e-signature / background check / contract). 'Background check paused: more
information needed' reads exactly like an account_notice and is the thing
blocking the Globaltize start date. That judgement is too expensive to hand to
a classifier, so mapping.route refuses to.
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db, registry, runner                  # noqa: E402
from modules.jarvis import events, mapping                     # noqa: E402
from modules.jarvis import sender_rules as SR                  # noqa: E402

TASK = "jarvis_triage_board"


def main() -> int:
    reg = registry.load()
    cand, valid = mapping.candidates(reg), set(reg.ids())

    with db.connect(config.DB_PATH) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, author, subject, text, state, proposed_client_id,"
            " resolved_by FROM events WHERE source='email'"
            " AND state IN ('queued','held') ORDER BY ts DESC"
            + (f" LIMIT {LIMIT}" if LIMIT else ""))]

    decided, skipped = [], []

    def task(ctx):
        for r in rows:
            if events.is_human_decision(r.get("resolved_by")):
                skipped.append((r, "his own decision"))
                continue
            prot = bool(SR.transactional_reason(r.get("author") or ""))
            p = mapping.propose(ctx, r, cand, valid)
            if p is None:
                skipped.append((r, "proposer unavailable"))
                continue
            dest = mapping.route(p["intent"], protected=prot,
                                 subject=r.get("subject"))
            decided.append((r, p, dest, prot))
        return {"examined": len(rows), "decided": len(decided),
                "skipped": len(skipped)}

    runner.run_task(TASK, task, module="system")

    by = collections.Counter((p["intent"], dest) for _, p, dest, _ in decided)
    print(f"{len(rows)} row(s) on the board · {len(decided)} classified ·"
          f" {len(skipped)} skipped\n")
    for (intent, dest), n in by.most_common():
        print(f"  {n:>4}  {intent:<20} -> {dest}")

    print("\n  --- STAYS ON THE BOARD ---")
    for r, p, dest, prot in decided:
        if dest == "review":
            why = "transactional sender" if prot else p["intent"]
            print(f"    {(r['author'] or '')[:34]:<34} | "
                  f"{(r['subject'] or '')[:52]:<52} [{why}]")

    print("\n  --- FILED / DROPPED (kept, findable, off the board) ---")
    for r, p, dest, _ in decided:
        if dest != "review":
            print(f"    {p['intent']:<20} {(r['subject'] or '')[:60]}")

    if not APPLY:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    moved = 0
    with db.connect(config.DB_PATH) as conn:
        for r, p, dest, _ in decided:
            if dest == "review":
                continue
            reason = (mapping.FILE_REASON.get(p["intent"], "noise:marketing")
                      if dest == "file" else "noise:marketing")
            pid = p["proposed_client_id"]
            if pid and dest == "file":
                reason = f"{reason}:{pid}"
            conn.execute(
                "UPDATE events SET state='excluded', exclude_reason=?,"
                " proposal_evidence=? WHERE id=?",
                (reason, f"triaged as {p['intent']}: {p['evidence']}", r["id"]))
            moved += 1
    print(f"\nmoved {moved} row(s) off the board; "
          f"{len(decided) - moved} remain for you.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
