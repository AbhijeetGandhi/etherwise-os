"""events DAL — idempotent, canonical-keyed, nothing silently dropped (J1 §4.3).

Discipline mirrors transactions: SELECT-by-id (`evt:<source>:<native_id>`) before
INSERT; a re-collected event is a no-op (first classification wins, never clobbered).
State transitions are explicit; every open event is in exactly one of the four
states so per-run counts reconcile.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

STATES = ("mapped", "queued", "held", "excluded")

# Every `resolved_by` written by a HUMAN pressing a button in the cockpit. The
# UI passes 'cockpit-approval' (mapping queue) and 'cockpit-thread-review'
# (thread screen); 'hand' is a direct sqlite correction.
HUMAN_RESOLVER_PREFIXES = ("cockpit-", "hand")


def canonical_id(source: str, native_id: str) -> str:
    return f"evt:{source}:{native_id}"


def is_human_decision(resolved_by: Optional[str]) -> bool:
    """True when this row's verdict was reached by Abhijeet, not by a rule or a
    model.

    THE POINT: a retroactive rule may re-examine its own past verdicts, and
    should -- an exclusion is only as good as the evidence behind it. It may
    NEVER re-examine his. A rule that re-opens a call he personally marked 'not
    a client' has decided that its evidence outranks his judgement, which is
    backwards, and the symptom is a row he already answered reappearing on the
    Review board with a machine's opinion attached. `resolved_by` is the only
    column that records who decided, so it is the only thing this can key on;
    a rule/model verdict leaves it NULL or names the classifier."""
    r = (resolved_by or "").strip().lower()
    return bool(r) and r.startswith(HUMAN_RESOLVER_PREFIXES)


def store_event(conn: sqlite3.Connection, ev: dict, run_id: Optional[int]) -> str:
    """Insert a normalized event if new. `ev` carries an initial `state`
    ('held' for to-be-mapped, 'excluded' for noise). Returns inserted|exists."""
    eid = ev["id"]
    row = conn.execute("SELECT id FROM events WHERE id=?", (eid,)).fetchone()
    if row:
        return "exists"
    state = ev["state"]
    if state not in STATES:
        raise ValueError(f"bad initial event state {state!r}")
    conn.execute(
        "INSERT INTO events (id, source, native_id, ts, author, subject,"
        " client_id, state, text, permalink, raw_ref, sweep_run_id,"
        " exclude_reason, direction) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, ev["source"], ev["native_id"], ev.get("ts"), ev.get("author"),
         ev.get("subject"), ev.get("client_id"), state, ev.get("text"),
         ev.get("permalink"), ev.get("raw_ref"), run_id, ev.get("exclude_reason"),
         ev.get("direction")))
    return "inserted"


def set_mapped(conn: sqlite3.Connection, eid: str, client_id: str) -> None:
    conn.execute("UPDATE events SET state='mapped', client_id=?,"
                 " updated_at=datetime('now') WHERE id=?", (client_id, eid))


def set_queued(conn: sqlite3.Connection, eid: str, proposed: Optional[str],
               confidence: Optional[float], evidence: Optional[str]) -> None:
    conn.execute(
        "UPDATE events SET state='queued', proposed_client_id=?,"
        " proposal_confidence=?, proposal_evidence=?, updated_at=datetime('now')"
        " WHERE id=?", (proposed, confidence, evidence, eid))


def set_excluded(conn: sqlite3.Connection, eid: str, reason: str) -> None:
    conn.execute("UPDATE events SET state='excluded', exclude_reason=?,"
                 " updated_at=datetime('now') WHERE id=?", (reason, eid))


def set_auto_mapped(conn: sqlite3.Connection, eid: str, client_id: str,
                    confidence: Optional[float], evidence: Optional[str],
                    by: str) -> None:
    """A PROVISIONAL, machine-made map (loom classifier high-confidence path).
    Distinct from a deterministic heal: it writes ONLY the events ledger (state,
    client_id, provenance) and NEVER touches sources/facts/dossiers, so a model
    guess can never inject an uncited client-facing fact. `resolved_by` (e.g.
    'loom_classifier:high') marks it as auto, not human, so it stays reversible
    and distinguishable from a confirmed map."""
    conn.execute(
        "UPDATE events SET state='mapped', client_id=?, proposed_client_id=?,"
        " proposal_confidence=?, proposal_evidence=?, resolved_by=?,"
        " resolved_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (client_id, client_id, confidence, evidence, by, eid))


def held_for_run(conn: sqlite3.Connection, run_id: Optional[int]) -> list[sqlite3.Row]:
    """This run's events still awaiting a proposer call (state='held')."""
    return conn.execute(
        "SELECT * FROM events WHERE state='held' AND sweep_run_id IS ?"
        " ORDER BY ts", (run_id,)).fetchall()


def held_backlog(conn: sqlite3.Connection, exclude_run: Optional[int],
                 limit: int) -> list[sqlite3.Row]:
    """Held events from EARLIER sweeps, newest first — the ones every docstring
    said would be picked up "by the next sweep" and never were, because
    `held_for_run` scopes to the run that collected them (2026-11-02: 52 rows,
    the oldest from 27 Aug, sat on the Review board as "pick the client
    yourself"; a proposer call that failed or fell outside the per-sweep cap
    left its row held for good). Newest first because recent mail is the mail
    that can still matter; the stale tail drains behind it."""
    if limit <= 0:
        return []
    return conn.execute(
        "SELECT * FROM events WHERE state='held' AND sweep_run_id IS NOT ?"
        " ORDER BY ts DESC LIMIT ?", (exclude_run, limit)).fetchall()


def counts_by_state(conn: sqlite3.Connection,
                    run_id: Optional[int] = None) -> dict:
    q = "SELECT state, COUNT(*) n FROM events"
    args: tuple = ()
    if run_id is not None:
        q += " WHERE sweep_run_id=?"
        args = (run_id,)
    q += " GROUP BY state"
    out = {s: 0 for s in STATES}
    for r in conn.execute(q, args):
        out[r["state"]] = r["n"]
    out["total"] = sum(out[s] for s in STATES)
    return out


# ── approval-flow mutations (used by the mapping queue, D3) ──────────────────
def resolve(conn: sqlite3.Connection, eid: str, client_id: str, by: str) -> None:
    conn.execute(
        "UPDATE events SET state='mapped', client_id=?, resolved_by=?,"
        " resolved_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (client_id, by, eid))


def ignore(conn: sqlite3.Connection, eid: str, reason: str, by: str,
           evidence: Optional[str] = None) -> None:
    """`evidence` keeps WHY next to WHAT. `exclude_reason` is a category
    ('fathom-kind:internal-product'); it says which bucket the row went into and
    nothing about the call that put it there. That is precisely the state the
    2026-08-14 post-mortem found 114 rows in: correctly-labelled exclusions that
    nobody could audit without re-reading the transcript, so nobody did. A
    classifier that writes its deciding phrase here makes a wrong verdict
    findable by reading, not by re-spending."""
    conn.execute(
        "UPDATE events SET state='excluded', exclude_reason=?, resolved_by=?,"
        " proposal_evidence=COALESCE(?, proposal_evidence),"
        " resolved_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (reason, by, evidence, eid))


def queued_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events WHERE state='queued' ORDER BY"
        " proposed_client_id IS NULL, proposed_client_id, ts DESC").fetchall()
