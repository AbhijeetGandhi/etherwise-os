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
from modules.jarvis import events, gmail_rail, imap_rail, leads, mapping   # noqa: E402
from modules.upwork import room_link                       # noqa: E402
from modules.jarvis import resweep                          # noqa: E402
from modules.jarvis import sender_rules                     # noqa: E402
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


def is_low_base_rate_event(ev: dict) -> bool:
    """Was this row collected from a low-base-rate IMAP inbox? The sweep knows
    for the rows it just collected; for a row from an earlier run (the backlog,
    a re-proposal) the inbox is in raw_ref ('imap/<inbox>/<uid>'). contact@
    rows carry no imap prefix and are never low-base-rate."""
    ref = (ev.get("raw_ref") or "")
    if not ref.startswith("imap/"):
        return False
    return ref.split("/", 2)[1] in set(config.JARVIS_IMAP_LOW_BASE_RATE_INBOXES)


def apply_proposal(conn, eid: str, p: dict, *, low: bool, protected: bool,
                   subject, min_conf: float, author=None) -> str:
    """Write one proposer verdict to its event. Returns where it went:
    'lead' | 'filed' | 'imap-excluded' | 'queued'. One function so the sweep
    and `bin/jarvis-repropose` cannot route the same verdict two ways.

    ROUTE BY INTENT, not by the absence of a client id. Until 2026-08-20 every
    proposal that was not excluded here landed on the board, so "I cannot
    attribute this" and "a person must see this" were the same outcome — and
    the board filled with renewals, receipts, seat confirmations and contract
    notifications. The intent is decided in the same proposer call."""
    pid, conf = p["proposed_client_id"], p["confidence"]
    dest = mapping.route(p["intent"], protected=protected, subject=subject,
                         author=author)
    if dest == "lead" and not pid:
        # a prospect, not a client to map (2026-11-01): the leads table is its
        # home; the event is filed under the lead id. A "lead" the proposer
        # ALSO tied to a registry client is an existing relationship talking —
        # that one still queues.
        ev_row = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if ev_row:
            leads.from_event(conn, dict(ev_row), note=p.get("evidence"))
            return "lead"
        dest = "review"
    elif dest == "lead":
        dest = "review"
    if dest != "review":
        reason = (mapping.FILE_REASON.get(p["intent"], "noise:marketing")
                  if dest == "file" else "noise:marketing")
        if pid and dest == "file":
            reason = f"{reason}:{pid}"      # whose it was, kept
        events.set_excluded(conn, eid, reason)
        return "filed"
    # A low-base-rate inbox queues ONLY on a positive client signal (a proposer
    # id naming a registry client at >= min_conf). No signal => EXCLUDED, never
    # queued (keeps the queue from filling with personal / finance-alert
    # noise). contact@ keeps its original behaviour: even a null proposal
    # queues for human triage.
    if low and not (pid is not None and conf >= min_conf):
        events.set_excluded(conn, eid, "imap-low-base-rate:no-client-signal")
        return "imap-excluded"
    events.set_queued(conn, eid, pid, conf, p["evidence"])
    return "queued"


def drain_backlog(ctx, reg, *, dbp, budget: int, cand=None, valid=None,
                  min_conf=None) -> dict:
    """Take held rows from EARLIER sweeps to the proposer, newest first, within
    `budget`, and route each verdict through `apply_proposal`.

    THE BACKLOG (2026-11-02). "The rest stay held for the next sweep" was
    written three times and implemented never: `held_for_run` only ever saw the
    current run's rows, so a proposer call that failed, or a row past the cap,
    stayed held for good — 52 of them, back to 27 Aug, rendered on the Review
    board as questions for him ("pick the client yourself"). New mail keeps
    first claim on the budget; whatever is left comes here, and the empty runs
    (no new mail) spend the whole budget here, so a quiet inbox cannot leave
    the backlog standing. Returns the two metrics every run now carries:
    backlog_proposed (taken to the proposer this run) and backlog_remaining
    (still held after it — 0 is the steady state; a number that only grows
    means the proposer itself is failing)."""
    if cand is None:
        cand, valid = mapping.candidates(reg), set(reg.ids())
    if min_conf is None:
        min_conf = config.JARVIS_IMAP_QUEUE_MIN_CONFIDENCE
    with db.connect(dbp) as conn:
        backlog = [dict(r) for r in events.held_backlog(
            conn, exclude_run=ctx.run_id, limit=budget)]
    proposals = []
    for ev in backlog:
        prot = bool(sender_rules.transactional_reason(ev.get("author") or ""))
        proposals.append((ev, mapping.propose(ctx, ev, cand, valid), prot))
    outcomes: dict = {}
    with db.connect(dbp) as conn:
        for ev, p, prot in proposals:
            if p is None:
                outcomes["held"] = outcomes.get("held", 0) + 1
                continue                      # proposer failed -> stays held
            out = apply_proposal(conn, ev["id"], p, low=is_low_base_rate_event(ev),
                                 protected=prot, subject=ev.get("subject"),
                                 min_conf=min_conf, author=ev.get("author"))
            outcomes[out] = outcomes.get(out, 0) + 1
        remaining = conn.execute(
            "SELECT COUNT(*) FROM events WHERE state='held'").fetchone()[0]
    if backlog:
        ctx.log(f"sweep backlog: proposed {len(backlog)} earlier-run held rows ->"
                f" {outcomes}; {remaining} still held")
    return {"backlog_proposed": len(backlog), "backlog_outcomes": outcomes,
            "backlog_remaining": remaining}


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
        # No new mail is not "nothing to do": the held backlog (rows an
        # earlier sweep collected and never classified) drains on the empty
        # runs too, or a quiet inbox would leave it standing for ever.
        drained = drain_backlog(ctx, reg, dbp=dbp,
                                budget=config.JARVIS_SWEEP_MAX_PROPOSALS)
        if rs.get("changed") or drained["backlog_proposed"]:
            # A re-sweep that cleared 43 rows off the Review board is not an
            # empty run, and TaskSkip throws the metrics away (runner records
            # only skip_reason). Report it instead, so the work shows up in the
            # run log and in Review's "last sweep" panel rather than vanishing.
            with db.connect(dbp) as conn:
                counts = events.counts_by_state(conn)
            return {"collected": 0, "inserted": 0, "resweep_only": True,
                    "mapped": counts["mapped"], "queued": counts["queued"],
                    "held": counts["held"], "excluded": counts["excluded"],
                    "reconciled": True, **drained, **_resweep_metrics(rs)}
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
        mapped = echoed = room_linked = 0
        structural = {}
        still_held = []

        leads_made = 0

        def _gate(ev):
            """The intent gate for the zero-LLM paths. Until 2026-10-18 these
            two branches went straight to set_queued, so `mapping.route`'s
            whole purpose — keep notifications off the board — applied to the
            proposer path only. Returns a file-reason to exclude, or None."""
            prot = bool(sender_rules.transactional_reason(ev.get("author") or ""))
            dest, reason = mapping.route_structural(
                ev.get("author"), ev.get("subject"), protected=prot)
            return reason if dest == "file" else None

        def _shape(ev):
            return mapping.route_structural(ev.get("author"), ev.get("subject"))

        for ev in held:
            # THE WEBSITE FORM (2026-11-01, reworked 2026-11-02): a download
            # notice is filed here; an inquiry with a message falls through
            # to the proposer, whose intent gate reads it — `lead` makes the
            # row, a pitch is filed. (The 1 Nov rule made every form post a
            # lead; 11 of 12 were vendors, a writer, or downloads.)
            dest, shape = _shape(ev)
            if dest == "file" and shape == "filed:website-download":
                events.set_excluded(conn, ev["id"], shape)
                structural["website_download"] = structural.get("website_download", 0) + 1
                continue
            if dest == "lead":                       # no shape does this today
                leads.from_event(conn, ev, note="website form")
                leads_made += 1
                structural[shape] = structural.get(shape, 0) + 1
                continue
            cid = mapping.match_deterministic(reg, ev["author"])
            if cid:
                events.set_mapped(conn, ev["id"], cid); mapped += 1
                continue
            echo = mapping.domain_echo(reg, ev["author"])
            if echo:
                # KEEP THE ATTRIBUTION, DROP THE INTERRUPTION: a receipt or an
                # RSVP from a client's own domain is still that client's, so it
                # is filed under them (findable by exclude_reason) rather than
                # put in front of him.
                reason = _gate(ev)
                if reason:
                    events.set_excluded(
                        conn, ev["id"],
                        f"{reason}:{echo['proposed_client_id']}")
                    structural[reason] = structural.get(reason, 0) + 1
                    continue
                events.set_queued(conn, ev["id"], echo["proposed_client_id"],
                                  echo["confidence"], echo["evidence"])
                echoed += 1
                continue
            # A PROSPECT'S MAIL (2026-08-26): the sender is the counterparty of
            # an Upwork room active this month. That is a positive signal even
            # with no registry client behind it — the low-base-rate rule below
            # would otherwise exclude it, which is how David Séjourné's brief
            # vanished. Linked in room_links (the Upwork tab and the
            # draft-writer read that), queued for Review like any real mail.
            link = room_link.match_event(conn, ev)
            if link:
                room_link.record(conn, ev["id"], link)
                # The link is RECORDED either way — the Upwork tab and the
                # draft-writer read room_links, and that signal is why a
                # prospect's mail gets attributed at all. What the gate
                # decides is only whether a human is interrupted: "Mustafa A.
                # sent you a message" carries no content the thread rail does
                # not already hold.
                reason = _gate(ev)
                if reason:
                    events.set_excluded(conn, ev["id"],
                                        f"{reason}:{link['client_id']}")
                    structural[reason] = structural.get(reason, 0) + 1
                    continue
                room = conn.execute("SELECT room_name FROM threads WHERE id=?",
                                    (link["thread_id"],)).fetchone()
                events.set_queued(conn, ev["id"], link["client_id"], 0.9,
                                  room_link.evidence(link, room["room_name"] if room else None))
                room_linked += 1
                continue
            still_held.append(ev)

    # 3b. PROPOSER (LLM, OUTSIDE the transaction) ──────────────────────────────
    # contact@ (high base rate) gets the bounded proposer budget FIRST; the
    # low-base-rate personal inboxes take whatever is left, so their volume can
    # never starve contact@ of proposer calls.
    still_held.sort(key=lambda ev: ev["id"] in low_base_rate_ids)
    min_conf = config.JARVIS_IMAP_QUEUE_MIN_CONFIDENCE
    cand, valid = mapping.candidates(reg), set(reg.ids())
    budget = config.JARVIS_SWEEP_MAX_PROPOSALS
    this_run = still_held[:budget]
    proposals = []
    for ev in this_run:
        # a transactional sender's mail is never routed by intent — see
        # mapping.route. Carried alongside the proposal so the write loop does
        # not have to re-derive it.
        prot = bool(sender_rules.transactional_reason(ev.get("author") or ""))
        proposals.append((ev["id"], ev["id"] in low_base_rate_ids,
                          mapping.propose(ctx, ev, cand, valid), prot,
                          ev.get("subject"), ev.get("author")))

    with db.connect(dbp) as conn:
        queued = imap_excluded = 0
        filed: dict = {}
        for eid, low, p, prot, subj, author in proposals:
            if p is None:
                continue                          # proposer failed -> stays held
            outcome = apply_proposal(conn, eid, p, low=low, protected=prot,
                                     subject=subj, min_conf=min_conf,
                                     author=author)
            if outcome == "lead":
                leads_made += 1
                filed["lead"] = filed.get("lead", 0) + 1
            elif outcome == "filed":
                filed[p["intent"]] = filed.get(p["intent"], 0) + 1
            elif outcome == "imap-excluded":
                imap_excluded += 1
            elif outcome == "queued":
                queued += 1
        counts = events.counts_by_state(conn, ctx.run_id)

    # 3c. THE BACKLOG, with whatever budget this run's own mail left ──────────
    drained = drain_backlog(ctx, reg, dbp=dbp, budget=budget - len(this_run),
                            cand=cand, valid=valid, min_conf=min_conf)

    # 4. RECONCILE ─────────────────────────────────────────────────────────────
    parts = counts["mapped"] + counts["queued"] + counts["held"] + counts["excluded"]
    reconciled = (inserted == counts["total"] == parts)
    if not reconciled:
        ctx.anomaly("sweep_reconcile_mismatch", "critical",
                    {"inserted": inserted, "counts": counts})
    ctx.log(f"sweep: collected {len(collected)} (new {inserted}) -> mapped "
            f"{counts['mapped']} queued {counts['queued']} held {counts['held']} "
            f"excluded {counts['excluded']} (imap-no-signal {imap_excluded}) | "
            f"leads {leads_made} | "
            f"proposer {len(proposals)} | imap inboxes {len(imap_new_cursors)} "
            f"skipped {len(imap_skipped)}")
    return {"collected": len(collected), "inserted": inserted,
            "mapped": counts["mapped"], "queued": counts["queued"],
            "held": counts["held"], "excluded": counts["excluded"],
            "domain_echoed": echoed, "room_linked": room_linked,
            # what the structural gate kept off the board on the zero-LLM
            # paths, by shape — visible per run, so a gate that starts eating
            # real mail shows up as a number rather than as silence
            "filed_structural": structural,
            "leads": leads_made,
            "proposer_calls": len(proposals) + drained["backlog_proposed"],
            **drained,
            "imap_excluded_no_signal": imap_excluded,
            "filed_by_intent": filed,
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
