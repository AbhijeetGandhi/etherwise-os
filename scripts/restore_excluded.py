#!/usr/bin/env python3
"""Re-examine `excluded` mail against the CURRENT gate and bring back what the
rules no longer exclude.

    PYTHONPATH=. python3 scripts/restore_excluded.py               # dry run
    PYTHONPATH=. python3 scripts/restore_excluded.py --apply
    PYTHONPATH=. python3 scripts/restore_excluded.py --since 2026-07-01
    PYTHONPATH=. python3 scripts/restore_excluded.py --domain upwork.com

WHY A SEPARATE SCRIPT. `modules/jarvis/resweep.py` made the rules retroactive,
but only over `queued`/`held` — the rows still ON the board. That is the safe
default and should stay the default: it runs at the top of every sweep, and a
job that silently un-excludes mail on every rule tweak would be a way to flood
Review by accident. Restoring from `excluded` is a deliberate act, so it is a
deliberate command.

WHAT IT IS FOR. When a rule was WRONG rather than missing. Measured 2026-08-20:
the noise rules match the sender's LOCAL PART and ignore the domain, so
`donotreply@upwork.com` matched noreply-local `donotreply` and
`noreply@{eu1,eu2,us2}.make.com` matched `noreply`. Those are not bulk senders
that happen to mention a client — they are the ONLY addresses those platforms
send from, so the rule was not filtering Upwork's marketing, it was deleting
Upwork. 28 of 30 non-job-alert Upwork rows went, including "Ryan Stomberg hired
you for" — the Globaltize win — plus every contract change and client message,
and 139 Make.com client-automation failures naming DoulaMatch, All Pandadoc -
Sign Watcher, [AUTOFILL] BT RBT Self and Hipp Follow-up.

It reuses `resweep.decide` unchanged, so restoration and ingest can never
disagree about a message: one rule set, one code path, three callers.

SAFETY, in the order it is enforced:
  1. a row Abhijeet resolved himself is never touched (events.is_human_decision);
  2. only rows whose exclusion is now REVERSED come back, and they return to
     `queued` — the board — never to `mapped`;
  3. it will not restore what the rules still exclude, which is the whole test:
     Upwork's 85 'New job alert' rows must stay gone (the scanner reads those
     directly) while the hire comes back. If that number moves, the rules are
     wrong, not the backlog.
Idempotent: a second run restores nothing.
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

APPLY = "--apply" in sys.argv


def _arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


SINCE = _arg("--since")
DOMAIN = _arg("--domain")
LIMIT = int(_arg("--limit", "0") or 0)
# Restore ONLY rows a protection tier now covers (transactional / operational).
# Without this the script answers a much wider question — "what would the rules
# say about everything ever excluded" — and re-examining 2000 rows surfaces
# every exclusion whose original rule has since been edited away. Measured
# 2026-08-20: 229 rows came back, 182 of them the platform mail this fix is
# about and ~47 of them archaeology (pharmeasy, cred.club, samsung, airbnb).
# Dumping the second group on the board would be the same mistake in reverse —
# spending his attention to prove a point about rule history. So the reversal
# is scoped to what it actually reverses, and the rest is REPORTED, not acted
# on, which leaves him the choice instead of making it for him.
PROTECTED_ONLY = "--all" not in sys.argv
# `registry` belongs in this set and is not a protection tier at all: it means
# the current rules resolve the sender to a REGISTRY CLIENT. A client's own mail
# sitting in `excluded` is not a judgement call to leave for later — it is
# simply wrong, and it is how 'Julie H. sent you a message' and 'John B. sent
# you a message' (both resolving to real clients through their Upwork room
# addresses) were found. Restoring these is the least debatable thing here.
_PROTECTED_RULES = ("transactional", "operational", "registry")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db, registry                          # noqa: E402
from modules.jarvis import resweep, events                     # noqa: E402
from modules.jarvis import sender_rules as SR                  # noqa: E402


def main() -> int:
    reg = registry.load()
    rules, learned = SR.load_rules(), SR.load_learned()

    where, args = ["state = 'excluded'"], []
    if SINCE:
        where.append("ts >= ?")
        args.append(SINCE)
    if DOMAIN:
        where.append("lower(author) LIKE ?")
        args.append(f"%@%{DOMAIN.lower()}")

    with db.connect(config.DB_PATH) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, source, state, author, subject, text,"
            " proposed_client_id, resolved_by, exclude_reason, ts"
            f" FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC"
            + (f" LIMIT {LIMIT}" if LIMIT else ""), args)]

    restore, still, human, other = [], [], [], []
    for r in rows:
        if events.is_human_decision(r.get("resolved_by")):
            human.append(r)
            continue
        d = resweep.decide(r, reg=reg, rules=rules, learned=learned)
        if d["action"] not in ("keep", "propose"):
            still.append((r, d))
            continue
        prot = str(d.get("rule", "")).startswith(_PROTECTED_RULES)
        (restore if (prot or not PROTECTED_ONLY) else other).append((r, d))

    print(f"{len(rows)} excluded row(s) examined"
          + (f" since {SINCE}" if SINCE else "")
          + (f" for {DOMAIN}" if DOMAIN else ""))
    print(f"  {len(restore)} would RETURN to the board"
          + (" (protected senders only)" if PROTECTED_ONLY else " (--all)"))
    print(f"  {len(still)} still excluded by the current rules")
    print(f"  {len(human)} decided by Abhijeet — never touched")
    if other:
        doms = collections.Counter(SR.domain_of(r["author"]) for r, _ in other)
        print(f"  {len(other)} REPORTED ONLY — no rule excludes these any more,"
              f" but no protection covers them either;")
        print(f"      old exclusions whose rule has since been edited away."
              f" `--all` restores them too.")
        print("      " + ", ".join(f"{d}({n})" for d, n in doms.most_common(10)))
    print()

    by_dom = collections.Counter()
    for r, _ in restore:
        by_dom[SR.domain_of(r["author"])] += 1
    for dom, n in by_dom.most_common():
        print(f"  +{n:>4}  {dom}")

    print("\n  --- what STAYS excluded, by rule (the safety check) ---")
    by_rule = collections.Counter(d["rule"] for _, d in still)
    for rule, n in by_rule.most_common(12):
        print(f"  -{n:>4}  {rule}")

    if not restore:
        print("\nnothing to restore.")
        return 0
    if not APPLY:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    with db.connect(config.DB_PATH) as conn:
        for r, d in restore:
            # back to the BOARD, not to mapped. A restored row is a question
            # again, which is exactly what it should have been all along; the
            # old exclude_reason is kept in proposal_evidence so the reversal
            # is auditable by reading rather than by re-running anything.
            ev = (f"restored {r['exclude_reason']!r} -> {d['rule']}:"
                  f" {d['reason']}")
            if d["action"] == "propose" and d.get("client_id"):
                conn.execute(
                    "UPDATE events SET state='queued', exclude_reason=NULL,"
                    " proposed_client_id=?, proposal_confidence=?,"
                    " proposal_evidence=? WHERE id=?",
                    (d["client_id"], resweep.MENTION_CONFIDENCE, ev, r["id"]))
            else:
                conn.execute(
                    "UPDATE events SET state='queued', exclude_reason=NULL,"
                    " proposal_evidence=COALESCE(proposal_evidence,?)"
                    " WHERE id=?", (ev, r["id"]))
    print(f"\nrestored {len(restore)} row(s) to `queued`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
