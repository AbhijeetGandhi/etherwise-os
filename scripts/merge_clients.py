#!/usr/bin/env python3
"""Merge one or more RETIRED registry client ids into a SURVIVING one.

    PYTHONPATH=. python3 scripts/merge_clients.py --into visa-gate \
        --from servicepeople --from raphael-mahir              # dry run
    PYTHONPATH=. python3 scripts/merge_clients.py --into visa-gate \
        --from servicepeople --from raphael-mahir --apply

Two client merges have happened (andrew-wang -> endura on 2026-08-13,
servicepeople + raphael-mahir -> visa-gate on 2026-08-14) and a third is likely,
so this is the general path. The first one was a throwaway script in
var/agent-scratch; running the same reasoning twice from memory is how a table
gets missed.

ORDER OF OPERATIONS. Edit `config/clients.yaml` FIRST — remove the retired
entries and fold their names into the survivor's aliases — then run this. It
REFUSES while a retired id is still loadable, because the registry is the spine:
if the file still claims the id, some other reader still resolves it.

WHAT IS MOVED, WHAT IS DELETED, AND WHAT IS DELIBERATELY LEFT ALONE
------------------------------------------------------------------
MOVED — every column that holds a REGISTRY client id (`_PLAN`). Display-name
columns move with them so a row never names a client that no longer exists.

DELETED — the derived `crm/<id>` source and its facts. `crm.ingest_crm` rebuilds
one per client from the ledger on every compile, keyed on
`content_hash('crm/<id>')`, and it only clears the facts of the source IT owns.
Repointing the retired one would leave the survivor with TWO crm sources and
double-count its revenue facts. The next `kb-backfill compile` re-derives the
survivor's from the same ledger rows — which now include the retired client's,
because the identifiers moved onto the survivor in clients.yaml.

DELETED — the retired `dossier_index` rows and their markdown files (the caller
does the `git rm`; the path is printed).

LEFT ALONE, on purpose:
  * `audit_log` and `runs.metrics_json` — the AUDIT TRAIL of what happened at the
    time. Rewriting history to match the present is what an audit trail must not
    do.
  * every EXTERNAL namespace that merely happens to be called `client_*`:
    `offers.client_id` is UPWORK's numeric company id (rewriting it with a
    registry slug would corrupt the row and the 2026-08-13 script would have
    done exactly that had a row matched), `contracts.client_company`,
    `messages.sender_org_name`, `proposals.client_*`, `scored_jobs.client_*`,
    `transactions.assignment_company` — these are what the outside world called
    the client, they are how the money bridge FINDS the client, and they are
    facts about the past. The registry's aliases are what make them resolve.

SAFETY. `_UNMAPPED_GUARD` re-scans the live schema for any column named like a
client id that this script does not know about, and REFUSES rather than silently
skipping it — the failure mode a hand-rolled merge has is a table nobody
remembered.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db, registry                            # noqa: E402
from modules.chief_of_staff import nudges                        # noqa: E402

# (label, table, column) — every column holding a REGISTRY client id.
_PLAN = [
    ("sources.client_id", "sources", "client_id"),
    ("facts.client_id", "facts", "client_id"),
    ("threads.client_id", "threads", "client_id"),
    ("events.client_id", "events", "client_id"),
    ("events.proposed_client_id", "events", "proposed_client_id"),
    ("commitments.client_id", "commitments", "client_id"),
    ("communications.client_id", "communications", "client_id"),
    ("people.client_id", "people", "client_id"),
    ("provisional_payments.client_id", "provisional_payments", "client_id"),
    ("extract_staging_facts.client_id", "extract_staging_facts", "client_id"),
    ("extract_staging_closures.client_id", "extract_staging_closures", "client_id"),
    ("extract_staging_commitments.client_id", "extract_staging_commitments",
     "client_id"),
    ("nudges.client", "nudges", "client"),
    # reclass_log is a REVERT PLAN, not history: `bin/jarvis-call-rules --revert`
    # writes sources.client_id = prior_client, so a retired id left in here would
    # resurrect a client that no longer exists the next time anyone reverts.
    ("reclass_log.prior_client", "reclass_log", "prior_client"),
    ("reclass_log.new_client", "reclass_log", "new_client"),
    # migration 039 (the sales engine): the conversation reader's note names the
    # conversation's client, and the Pipeline's win-back angles key on it
    ("conversation_codes.client_id", "conversation_codes", "client_id"),
]

# display-name columns that ride along with the id in the same table
_NAME_COLS = {"sources": "client_name", "offers": "client_name",
              "dossier_index": "client_name"}

# Columns deliberately NOT in `_PLAN`: either a different namespace that merely
# happens to be called `client_*`, or one this script handles on a different
# path. Listed so `_unmapped_columns` stays quiet about them and — the point —
# so the reason is written down where the next merge will read it.
_FOREIGN = {
    ("dossier_index", "client_id"): "DELETED, not moved (the retired dossier"
                                    " row goes; the compile rebuilds the"
                                    " survivor's)",
    ("offers", "client_id"): "Upwork's numeric company id, not a registry slug",
    ("offers", "client_name"): "Upwork's own label for the client",
    ("contracts", "client_company"): "Upwork's label — the money bridge keys on it",
    ("proposals", "client_company"): "Upwork job-post label",
    ("proposals", "client_country"): "not an id",
    ("proposals", "client_total_spent"): "not an id",
    ("scored_jobs", "client_company"): "Upwork job-post label",
    ("scored_jobs", "client_country"): "not an id",
    ("scored_jobs", "client_total_spent"): "not an id",
    ("scored_jobs", "client_hires"): "not an id",
    ("scored_jobs", "client_rating"): "not an id",
    ("scored_jobs", "client_payment_verified"): "not an id",
    # migration 038 (the Upwork tab's Funnel): facts read off each job post
    ("proposal_outcomes", "client_company"): "Upwork job-post label",
    ("proposal_outcomes", "client_did"): "what the client did on the job"
                                         " (hired_us / hired_other / …), not an id",
    ("proposal_outcomes", "client_country"): "not an id",
    ("proposal_outcomes", "client_spend"): "not an id",
    ("proposal_outcomes", "client_hire_rate"): "not an id",
    ("proposal_outcomes", "client_jobs"): "not an id",
    ("messages", "sender_org_name"): "Upwork's label on the message",
    ("upwork_invitations", "client_name"): "Upwork's label on the invitation",
    ("dossier_index", "client_name"): "display name, moved with the index row",
    ("sources", "client_name"): "display name, moved with the source row",
    ("audit_log", "entity"): "audit trail — never rewritten",
    ("audit_log", "entity_id"): "audit trail — never rewritten",
    ("shadow_ledger", "entity"): "shadow intent record",
    ("events", "subject"): "the subject line of the thing, not an id",
    ("commitments", "dedup_key"): "handled by the card re-key pass",
    ("extract_staging_commitments", "dedup_key"): "handled by the card re-key pass",
    ("nudges", "dedup_key"): "handled by the card re-key pass",
    ("nudge_state", "item_key"): "handled by the card re-key pass",
    ("card_subjects", "card_key"): "handled by the card re-key pass",
    ("transactions", "dedup_key"): "ledger identity, never a client id",
    # migration 039 (the sales engine)
    ("job_snapshots", "client_last_activity"): "a time off the job post, not an id",
    ("reactivation_log", "client_id"): "the weekly win-back log, keyed (client, week):"
                                       " moving it could collide two halves drafted"
                                       " the same week, so the old id's weeks stay"
                                       " behind (they only rest a client 8 weeks)",
}


def _unmapped_columns(conn) -> list:
    """Any column that looks like it could hold a client id and is in neither
    `_PLAN` nor `_FOREIGN`. A third merge must fail loudly on a new table rather
    than quietly leave rows behind."""
    known = {(t, c) for _l, t, c in _PLAN} | set(_FOREIGN)
    out = []
    for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if name.startswith("sqlite_") or "_fts" in name:
            continue
        for r in conn.execute(f"PRAGMA table_info({name})"):
            col = r[1]
            if "client" in col.lower() and (name, col) not in known:
                out.append(f"{name}.{col}")
    return out


def _card_keys(old: str, new: str) -> list:
    """(old_key, new_key) for every card-identity form that names a client id.

    Derived from `nudges.followup_key` rather than spelled out, so a new key form
    added there is picked up here — CLAUDE.md: never add a key form without
    adding it to `nudges.followup_key_forms`."""
    pairs = [(nudges.followup_key(old, None, ""), nudges.followup_key(new, None, "")),
             (f"stale_dossier:{old}", f"stale_dossier:{new}")]
    return [(a, b) for a, b in pairs if a != b]


def _tables(conn) -> set:
    """The tables that actually EXIST. `reclass_log` is created lazily by
    `bin/jarvis-call-rules` (CREATE TABLE IF NOT EXISTS), not by a migration, so
    it is absent on a fresh database — and a merge must not die on a table a
    rail has simply never run."""
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def merge(conn, old: str, new: str, new_name: str, *, apply: bool) -> dict:
    moved: dict = {}
    live = _tables(conn)

    crm_row = conn.execute("SELECT id FROM sources WHERE source_ref=?",
                           (f"crm/{old}",)).fetchone()
    crm_sid = crm_row["id"] if crm_row else None
    crm_facts = conn.execute("SELECT COUNT(*) FROM facts WHERE source_id=?",
                             (crm_sid,)).fetchone()[0] if crm_sid else 0

    for label, table, col in _PLAN:
        if table not in live:
            moved[label] = "table absent"
            continue
        # the derived crm source and its facts are DELETED, never moved
        extra, args = "", [old]
        if crm_sid is not None and table in ("sources", "facts"):
            extra = " AND id IS NOT ?" if table == "sources" else \
                    " AND source_id IS NOT ?"
            args.append(crm_sid)
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?{extra}",
                         args).fetchone()[0]
        moved[label] = n
        if n and apply:
            name_col = _NAME_COLS.get(table)
            sets = f"{col}=?" + (f", {name_col}=?" if name_col else "")
            vals = [new] + ([new_name] if name_col else [])
            conn.execute(f"UPDATE {table} SET {sets} WHERE {col}=?{extra}",
                         vals + args)

    for old_key, new_key in _card_keys(old, new):
        for table, col in (("nudges", "dedup_key"), ("nudge_state", "item_key"),
                           ("card_subjects", "card_key")):
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?",
                             (old_key,)).fetchone()[0]
            moved[f"{table}.{col} [{old_key}]"] = n
            if n and apply:
                conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?",
                             (new_key, old_key))

    di = conn.execute("SELECT dossier_path FROM dossier_index WHERE client_id=?",
                      (old,)).fetchone()
    moved["dossier_index (deleted)"] = 1 if di else 0
    moved["crm source (deleted)"] = 1 if crm_sid else 0
    moved["crm facts (deleted)"] = crm_facts
    if apply:
        if crm_sid:
            conn.execute("DELETE FROM facts WHERE source_id=?", (crm_sid,))
            conn.execute("DELETE FROM sources WHERE id=?", (crm_sid,))
        conn.execute("DELETE FROM dossier_index WHERE client_id=?", (old,))
        conn.execute(
            "INSERT INTO audit_log (actor, entity, entity_id, field, old_value,"
            " new_value, source, note) VALUES (?,?,?,?,?,?,?,?)",
            ("hand", "registry", old, "client_id", old, new, "client-merge",
             f"merged {old} -> {new}"))
    return {"moved": moved,
            "dossier_path": di["dossier_path"] if di else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--into", required=True, help="surviving client id")
    ap.add_argument("--from", dest="olds", action="append", required=True,
                    help="retired client id (repeatable)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    reg = registry.load(force=True)
    if args.into not in reg.by_id:
        print(f"ERROR: survivor {args.into!r} is not in the registry")
        return 2
    still = [o for o in args.olds if o in reg.by_id]
    if still:
        print(f"ERROR: {still} still in the registry — edit config/clients.yaml"
              f" first (remove the entry, fold its names into {args.into}'s"
              f" aliases), then re-run")
        return 2
    new_name = reg.by_id[args.into]["name"]
    print(f"merging {args.olds} -> {args.into} ({new_name})   apply={args.apply}\n")

    with db.connect(config.DB_PATH) as conn:
        unmapped = _unmapped_columns(conn)
        if unmapped:
            print("ERROR: columns this script does not know about:",
                  ", ".join(unmapped))
            print("       add each to _PLAN (it holds a registry id) or to"
                  " _FOREIGN (it does not), with the reason.")
            return 3

        paths = []
        for old in args.olds:
            res = merge(conn, old, args.into, new_name, apply=args.apply)
            print(f"  --- {old} -> {args.into}")
            for k, v in res["moved"].items():
                # every _PLAN line always prints (a zero is a measurement);
                # the per-key card lines only when they moved something
                if v or "[" not in k:
                    print(f"      {k:44} {v}")
            if res["dossier_path"]:
                paths.append(res["dossier_path"])

        if args.apply:
            left, live = {}, _tables(conn)
            for label, table, col in _PLAN:
                if table not in live:
                    continue
                for old in args.olds:
                    n = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {col}=?",
                        (old,)).fetchone()[0]
                    if n:
                        left[f"{label}[{old}]"] = n
            print(f"\n  residual rows carrying a retired id: {left or 'none'}")
            print("  survivor now holds:", {
                t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE client_id=?",
                                (args.into,)).fetchone()[0]
                for t in ("sources", "facts", "threads", "events")})

    if paths:
        print("\n  dossier files to `git rm` (the compile will not remove them):")
        for p in paths:
            print(f"      {p}   exists={Path(p).exists()}")
    if not args.apply:
        print("\n  DRY RUN — nothing written. re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
