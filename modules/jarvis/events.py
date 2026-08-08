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


def canonical_id(source: str, native_id: str) -> str:
    return f"evt:{source}:{native_id}"


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


def held_for_run(conn: sqlite3.Connection, run_id: Optional[int]) -> list[sqlite3.Row]:
    """This run's events still awaiting a proposer call (state='held')."""
    return conn.execute(
        "SELECT * FROM events WHERE state='held' AND sweep_run_id IS ?"
        " ORDER BY ts", (run_id,)).fetchall()


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


def ignore(conn: sqlite3.Connection, eid: str, reason: str, by: str) -> None:
    conn.execute(
        "UPDATE events SET state='excluded', exclude_reason=?, resolved_by=?,"
        " resolved_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (reason, by, eid))


def queued_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events WHERE state='queued' ORDER BY"
        " proposed_client_id IS NULL, proposed_client_id, ts DESC").fetchall()
