#!/usr/bin/env python3
"""Record Abhijeet's three attribution answers as HIS decisions (2026-08-14).

    PYTHONPATH=. python3 scripts/apply_call_answers.py            # dry run
    PYTHONPATH=. python3 scripts/apply_call_answers.py --apply

Verbatim, the whole message:

    "ishant vats ignore all of it. all devnagri call with jay are internal.
     tanmay is new emplyee so its internal as well."

WHY A SCRIPT AND NOT A HANDFUL OF UPDATEs. `resolved_by` is the only column
that records WHO decided, and `events.is_human_decision` keys every "no rule may
re-open this" guard on it. A hand-written UPDATE gets that wrong once and the
row goes back on the Review board next week with a machine's opinion attached —
which is the system telling him his answer did not count. So each row goes
through `approve.resolve`, the same path the cockpit button uses, with a
resolver string that reads as human ('hand:...' — see HUMAN_RESOLVER_PREFIXES)
and the deciding phrase stored as evidence.

Idempotent and dry-run by default: re-running reports 'already his' and writes
nothing. Answers 2 and 3 are mostly NOT here — they are a rule
(`call_language_rules` rule 2) and a roster derivation (`config.INTERNAL_PEOPLE`
from Payroll/employees.json), because a per-row fix answers today's queue and
nothing else. What IS here for them is the single row he named.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db                                    # noqa: E402
from modules.jarvis import approve, events                     # noqa: E402

BY = "hand:abhijeet-2026-08-14"          # is_human_decision() -> True

# ── answer 1: "ishant vats ignore all of it" ─────────────────────────────────
# The three UNATTRIBUTED calls he can have meant. The fourth Ishan call,
# fathom/48927603, is deliberately untouched: Patrick Nesbitt himself speaks on
# it, it is already attributed to patrick-nesbitt, and it carries no event row —
# "ignore Ishan" is not "un-attribute a call the client was audibly on".
ISHAN = [
    ("evt:fathom:107962336", "not a client"),
    ("evt:fathom:50558163", "not a client"),
    ("evt:fathom:49543186", "not a client"),
]
ISHAN_EVIDENCE = ('Abhijeet, 2026-08-14: "ishant vats ignore all of it."'
                  " Ishan Vats is not a client and these calls are not client"
                  " signal.")

# ── answer 3: the one row he pointed at ──────────────────────────────────────
# 41% Devanagari; speakers Jay Pokharna (E001) and "Tanmay" (E008, hired
# 2026-08-10). The classifier had it right for the wrong reason — it could not
# see that Tanmay was one of us, so it read the call as "an unknown human, in
# Hindi". Now both are on the roster, the language rule reaches its STRONG form
# and this is recorded as his verdict so nothing re-examines it.
TANMAY_EVENT = "evt:fathom:170653696"
TANMAY_EVIDENCE = ('Abhijeet, 2026-08-14: "all devnagri call with jay are'
                   ' internal. tanmay is new emplyee so its internal as well."'
                   " 41.2% Devanagari; the only speakers were Jay Pokharna and"
                   " Tanmay Bora (E008, hired 2026-08-10) — both Etherwise.")


def _row(conn, eid):
    r = conn.execute("SELECT id, state, exclude_reason, resolved_by, client_id"
                     " FROM events WHERE id=?", (eid,)).fetchone()
    return dict(r) if r else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    out = {"apply": bool(args.apply), "actions": []}

    plan = [(eid, "ignore", reason, ISHAN_EVIDENCE) for eid, reason in ISHAN]
    plan.append((TANMAY_EVENT, "internal", None, TANMAY_EVIDENCE))

    for eid, action, reason, evidence in plan:
        with db.connect(config.DB_PATH) as conn:
            before = _row(conn, eid)
        if not before:
            out["actions"].append({"event_id": eid, "skipped": "unknown event"})
            continue
        # ALREADY HIS? Then leave it alone — re-resolving would restamp
        # resolved_at and rewrite an audit trail that is already correct.
        if (events.is_human_decision(before["resolved_by"])
                and before["state"] == "excluded"):
            out["actions"].append({"event_id": eid, "skipped": "already his",
                                   "resolved_by": before["resolved_by"],
                                   "exclude_reason": before["exclude_reason"]})
            continue
        rec = {"event_id": eid, "action": action, "was": before,
               "will_be": {"state": "excluded", "resolved_by": BY,
                           "exclude_reason": (approve.INTERNAL_REASON
                                              if action == "internal"
                                              else reason)}}
        if args.apply:
            rec["result"] = approve.resolve(config.DB_PATH, eid, action,
                                            reason=reason, by=BY,
                                            evidence=evidence)
        out["actions"].append(rec)

    if not args.apply:
        out["note"] = "DRY RUN — nothing written. Re-run with --apply."
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
