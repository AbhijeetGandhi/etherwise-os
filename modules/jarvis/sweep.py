"""Jarvis sweep (J1 = COLLECT + MAP only; extract/card land in J2).

Pipeline, each stage a clean step with a reconciliation gate (§4.3):
  1. COLLECT  Gmail contact@ read-only, metadata-first (gmail_rail). Empty => SKIP
              before any LLM call (heartbeat economics).
  2. STORE    idempotent event rows (deterministic, no LLM).
  3. MAP      registry match -> mapped; unmatched -> Haiku proposer (READER) -> queued;
              proposer cap/failure -> stays held for the next sweep.
  4. RECONCILE  collected(inserted) == mapped + queued + held + excluded, else a
              critical anomaly (source-health turns amber).

module="system": the sweep makes ZERO external writes in J1 (local events + metered
Haiku only), so it runs live like the other local collectors. The queue + cards go
live only after Abhijeet's checkpoint.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core import config, db, registry, runner              # noqa: E402
from modules.jarvis import events, gmail_rail, imap_rail, mapping   # noqa: E402
from modules.jarvis import resweep                          # noqa: E402
from modules.knowledge import email_poll as ep             # noqa: E402

TASK_NAME = "jarvis_sweep"


def _resweep_metrics(rs: dict) -> dict:
    """Flatten the re-sweep result into the run's metrics.

    Flat scalars on purpose: Review's "last sweep" panel and Activity's run log
    both render `runs.metrics_json` as a key/value table, so this is the surface
    on which a retroactive exclusion becomes visible without inventing a screen.
    An automatic sweep that quietly removes rows from a human's queue and says
    nothing anywhere is the silent-zero failure, just wearing a helpful face.
    """
    return {"resweep_ran": bool(rs.get("ran")),
            "resweep_examined": rs.get("examined", 0),
            "resweep_excluded": rs.get("excluded", 0),
            "resweep_proposed": rs.get("proposed", 0),
            "resweep_kept": rs.get("keep", 0),
            "resweep_skipped": rs.get("skip", 0),
            "resweep_rules": json.dumps(rs.get("by_rule") or {}, sort_keys=True),
            "resweep_fingerprint": rs.get("fingerprint")}


def run_sweep(ctx, *, token_fn=ep.access_token, list_fn=gmail_rail._list,
              meta_fn=gmail_rail._meta, full_fn=gmail_rail._full,
              imap_inboxes=(), imap_collect=imap_rail.collect, reg=None) -> dict:
    reg = reg or registry.load()
    dbp = ctx.db_path or config.DB_PATH

    # 0. RE-SWEEP ──────────────────────────────────────────────────────────────
    # Rules are RETROACTIVE. A new rule re-examines the rows that already exist,
    # not just the mail that arrives after it. This sits FIRST, before the
    # collect and before the empty-skip, because the failure it fixes is
    # precisely a rule that was written to describe a backlog and then never run
    # over it (see modules/jarvis/resweep.py). One hash compare when nothing
    # changed; no model call ever.
    with db.connect(dbp) as conn:
        rs = resweep.run_if_rules_changed(conn, reg=reg, log=ctx.log)
    if rs.get("excluded", 0) >= config.JARVIS_RESWEEP_ANOMALY_THRESHOLD:
        # A rule edit that removes this much from a human's board is worth his
        # eye even when it is correct — he must be able to disagree with a sweep
        # he did not watch happen. Never fires in steady state (a re-sweep after
        # a one-line rule change clears a handful); the whole backlog going at
        # once is the case that needs saying out loud.
        ctx.anomaly("jarvis_resweep_bulk_exclude", "warn",
                    {"excluded": rs.get("excluded"),
                     "proposed": rs.get("proposed"),
                     "examined": rs.get("examined"),
                     "by_rule": rs.get("by_rule"),
                     "fingerprint": rs.get("fingerprint"),
                     "reversible": "each row records the deciding phrase in"
                                   " events.proposal_evidence; reverse by rule"})

    # 1. COLLECT ───────────────────────────────────────────────────────────────
    # contact@ over the Gmail API (OAuth) + the extra inboxes over read-only IMAP,
    # joined into ONE union. Each rail carries its own cursor; an IMAP inbox that
    # can't be read degrades (skip / warn) without taking down contact@ or its
    # siblings — the same classify gate runs on every rail (mail_pipeline).
    with db.connect(dbp) as conn:
        cursor = gmail_rail.read_cursor(conn)
        imap_cursors = {ib: imap_rail.read_cursor(conn, ib) for ib in imap_inboxes}
    token = token_fn()
    collected, new_cursor = gmail_rail.collect(
        token, cursor, max_items=config.JARVIS_SWEEP_MAX_EVENTS,
        _list=list_fn, _meta=meta_fn, _full=full_fn)

    # Personal inboxes (01/02) are low base rate — track their event ids so the
    # MAP stage can default unmatched mail from them to EXCLUDED, never queued.
    low_base_rate = set(config.JARVIS_IMAP_LOW_BASE_RATE_INBOXES)
    low_base_rate_ids: set[str] = set()
    imap_new_cursors, imap_skipped = {}, []
    for ib in imap_inboxes:
        try:
            evs, ncur = imap_collect(
                ib, imap_cursors[ib], max_items=config.JARVIS_SWEEP_MAX_EVENTS,
                first_fetch_days=config.JARVIS_IMAP_FIRST_FETCH_DAYS)
        except imap_rail.InboxSkip as skip:              # no app password: expected
            imap_skipped.append(str(skip))
            ctx.log(f"sweep imap: {skip}")
            continue
        except Exception as exc:                         # auth/network: degrade
            ctx.anomaly(f"jarvis_imap_error_{ep.sanitize_inbox(ib)}", "warning",
                        {"inbox": ib, "error": repr(exc)})
            ctx.log(f"sweep imap {ib} failed, skipping this inbox: {exc!r}")
            continue
        collected.extend(evs)
        imap_new_cursors[ib] = ncur
        if ib in low_base_rate:
            low_base_rate_ids.update(e["id"] for e in evs)

    if not collected:
        if rs.get("changed"):
            # A re-sweep that cleared 43 rows off the Review board is not an
            # empty run, and TaskSkip throws the metrics away (runner records
            # only skip_reason). Report it instead, so the work shows up in the
            # run log and in Review's "last sweep" panel rather than vanishing.
            with db.connect(dbp) as conn:
                counts = events.counts_by_state(conn)
            return {"collected": 0, "inserted": 0, "resweep_only": True,
                    "mapped": counts["mapped"], "queued": counts["queued"],
                    "held": counts["held"], "excluded": counts["excluded"],
                    "reconciled": True, **_resweep_metrics(rs)}
        raise runner.TaskSkip("no new mail since cursor")

    # 2. STORE (deterministic) ─────────────────────────────────────────────────
    with db.connect(dbp) as conn:
        inserted = sum(1 for ev in collected
                       if events.store_event(conn, ev, ctx.run_id) == "inserted")
        if new_cursor and new_cursor != cursor:
            gmail_rail.write_cursor(conn, new_cursor)
        for ib, ncur in imap_new_cursors.items():
            if ncur and ncur != imap_cursors[ib]:
                imap_rail.write_cursor(conn, ib, ncur)

    # 3a. MAP deterministic: registry match, then domain-echo (both zero-LLM) ───
    with db.connect(dbp) as conn:
        held = [dict(r) for r in events.held_for_run(conn, ctx.run_id)]
        mapped = echoed = 0
        still_held = []
        for ev in held:
            cid = mapping.match_deterministic(reg, ev["author"])
            if cid:
                events.set_mapped(conn, ev["id"], cid); mapped += 1
                continue
            echo = mapping.domain_echo(reg, ev["author"])
            if echo:
                events.set_queued(conn, ev["id"], echo["proposed_client_id"],
                                  echo["confidence"], echo["evidence"])
                echoed += 1
                continue
            still_held.append(ev)

    # 3b. PROPOSER (LLM, OUTSIDE the transaction) ──────────────────────────────
    # contact@ (high base rate) gets the bounded proposer budget FIRST; the
    # low-base-rate personal inboxes take whatever is left, so their volume can
    # never starve contact@ of proposer calls.
    still_held.sort(key=lambda ev: ev["id"] in low_base_rate_ids)
    min_conf = config.JARVIS_IMAP_QUEUE_MIN_CONFIDENCE
    cand, valid = mapping.candidates(reg), set(reg.ids())
    proposals = []
    for ev in still_held[: config.JARVIS_SWEEP_MAX_PROPOSALS]:
        proposals.append((ev["id"], ev["id"] in low_base_rate_ids,
                          mapping.propose(ctx, ev, cand, valid)))

    with db.connect(dbp) as conn:
        queued = imap_excluded = 0
        for eid, low, p in proposals:
            if p is None:
                continue                          # proposer failed -> stays held
            pid, conf = p["proposed_client_id"], p["confidence"]
            # A low-base-rate inbox queues ONLY on a positive client signal (a
            # proposer id naming a registry client at >= min_conf). No signal =>
            # EXCLUDED, never queued (keeps the queue from filling with personal /
            # finance-alert noise). contact@ keeps its original behaviour: even a
            # null proposal queues for human triage.
            if low and not (pid is not None and conf >= min_conf):
                events.set_excluded(conn, eid, "imap-low-base-rate:no-client-signal")
                imap_excluded += 1
                continue
            events.set_queued(conn, eid, pid, conf, p["evidence"])
            queued += 1
        counts = events.counts_by_state(conn, ctx.run_id)

    # 4. RECONCILE ─────────────────────────────────────────────────────────────
    parts = counts["mapped"] + counts["queued"] + counts["held"] + counts["excluded"]
    reconciled = (inserted == counts["total"] == parts)
    if not reconciled:
        ctx.anomaly("sweep_reconcile_mismatch", "critical",
                    {"inserted": inserted, "counts": counts})
    ctx.log(f"sweep: collected {len(collected)} (new {inserted}) -> mapped "
            f"{counts['mapped']} queued {counts['queued']} held {counts['held']} "
            f"excluded {counts['excluded']} (imap-no-signal {imap_excluded}) | "
            f"proposer {len(proposals)} | imap inboxes {len(imap_new_cursors)} "
            f"skipped {len(imap_skipped)}")
    return {"collected": len(collected), "inserted": inserted,
            "mapped": counts["mapped"], "queued": counts["queued"],
            "held": counts["held"], "excluded": counts["excluded"],
            "domain_echoed": echoed, "proposer_calls": len(proposals),
            "imap_excluded_no_signal": imap_excluded,
            "imap_inboxes": len(imap_new_cursors), "imap_skipped": imap_skipped,
            "reconciled": reconciled, **_resweep_metrics(rs)}


def main() -> int:
    # Production wires the real IMAP inboxes (config); tests inject their own.
    result = runner.run_task(
        TASK_NAME,
        lambda ctx: run_sweep(ctx, imap_inboxes=config.JARVIS_IMAP_INBOXES),
        module="system")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    raise SystemExit(main())
