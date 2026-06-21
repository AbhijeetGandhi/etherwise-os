"""Nudge engine — the queue every module writes into. One ACTIVE nudge per
dedup_key (re-fire refreshes, never duplicates); urgency ranks the queue;
decay drops stale items silently. This module only SURFACES — the founder
acts (SOUL never-do). Pure stdlib; all timestamps IST.

API:
  write_nudge(...)         upsert by dedup_key (create or refresh)
  set_state(...)           done|dismissed|snoozed|open (cockpit/terminal action)
  clear(dedup_key, ...)    condition gone -> decayed
  decay_expired(...)       TTL/past-due sweep -> decayed
  active_nudges(...)       open + woken-snoozed, ranked by live urgency
  top_n(n)                 brief's top-N by urgency
  compute_urgency(...)     base(kind) * recency * stake
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from core import config, db

_ACTIVE = ("open", "snoozed")


# ── time helpers (IST throughout) ─────────────────────────────────────────────

def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(config.TZ)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    s = ts.strip().replace("Z", "").replace(" ", "T")[:19]
    try:
        return datetime.fromisoformat(s).replace(tzinfo=config.TZ)
    except ValueError:
        try:
            return datetime.fromisoformat(s[:10]).replace(tzinfo=config.TZ)
        except ValueError:
            return None


# ── urgency model (design §6) ─────────────────────────────────────────────────

def _recency_decay(age_days: float) -> float:
    """Half every COS_RECENCY_HALFLIFE_DAYS; >=0."""
    hl = config.COS_RECENCY_HALFLIFE_DAYS
    return 0.5 ** (max(0.0, age_days) / hl) if hl else 1.0


def _stake_mult(money_at_stake: Optional[float], score: Optional[int]) -> float:
    """Bounded multiplier: money dominates (up to 3x at >=$20k); else job/lead
    score (up to 2x at >=30); else 1.0."""
    if money_at_stake:
        return 1.0 + min(float(money_at_stake), 20000.0) / 20000.0 * 2.0
    if score:
        return 1.0 + min(float(score), 30.0) / 30.0
    return 1.0


def compute_urgency(kind: str, *, money_at_stake: Optional[float] = None,
                    score: Optional[int] = None, refreshed_at: Optional[str] = None,
                    now: Optional[datetime] = None, overdue: bool = False) -> float:
    """base(kind) * recency(age since last signal) * stake. Overdue commitments
    /deadlines get a 1.5x bump so they float above same-kind not-yet-due."""
    base = config.COS_URGENCY_BASE.get(kind, 25)
    ref = _parse(refreshed_at)
    age_days = ((_now(now) - ref).total_seconds() / 86400.0) if ref else 0.0
    urg = base * _recency_decay(age_days) * _stake_mult(money_at_stake, score)
    return round(urg * (1.5 if overdue else 1.0), 3)


def _is_overdue(due: Optional[str], now: Optional[datetime]) -> bool:
    d = _parse(due)
    return bool(d and d < _now(now))


# ── write / refresh ───────────────────────────────────────────────────────────

def write_nudge(db_path, *, source: str, dept: str, kind: str, title: str,
                dedup_key: str, action: Optional[str] = None,
                due: Optional[str] = None, money_at_stake: Optional[float] = None,
                score: Optional[int] = None, payload: Optional[dict] = None,
                now: Optional[datetime] = None) -> dict:
    """Upsert by dedup_key. If an ACTIVE (open|snoozed) nudge exists, REFRESH it
    (title/action/due/stake/urgency/refreshed_at) keeping its state + created_at;
    else INSERT a new open nudge. Never duplicates an active item."""
    ts = _iso(_now(now))
    overdue = _is_overdue(due, now)
    urgency = compute_urgency(kind, money_at_stake=money_at_stake, score=score,
                              refreshed_at=ts, now=now, overdue=overdue)
    pj = json.dumps(payload) if payload is not None else None
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM nudges WHERE dedup_key=? AND state IN ('open','snoozed')",
            (dedup_key,)).fetchone()
        if row:
            conn.execute(
                "UPDATE nudges SET source=?, dept=?, kind=?, title=?, action=?,"
                " due=?, urgency=?, money_at_stake=?, score=?, payload_json=?,"
                " refreshed_at=?, updated_at=? WHERE id=?",
                (source, dept, kind, title, action, due, urgency, money_at_stake,
                 score, pj, ts, ts, row["id"]))
            return {"id": row["id"], "op": "refreshed", "urgency": urgency}
        cur = conn.execute(
            "INSERT INTO nudges (source, dept, kind, title, action, due, urgency,"
            " money_at_stake, score, dedup_key, state, payload_json, created_at,"
            " refreshed_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'open',?,?,?,?)",
            (source, dept, kind, title, action, due, urgency, money_at_stake,
             score, dedup_key, pj, ts, ts, ts))
        return {"id": cur.lastrowid, "op": "created", "urgency": urgency}


# ── state transitions (the action layer) ──────────────────────────────────────

_VALID_TARGET = ("open", "snoozed", "done", "dismissed", "decayed")


def set_state(db_path, *, state: str, dedup_key: Optional[str] = None,
              nudge_id: Optional[int] = None, snooze_until: Optional[str] = None,
              note: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """Transition an ACTIVE nudge (by id or dedup_key) to a new state. done/
    dismissed/decayed are terminal; snoozed needs snooze_until; open re-activates."""
    if state not in _VALID_TARGET:
        raise ValueError(f"invalid nudge state: {state!r}")
    ts = _iso(_now(now))
    decayed = ts if state == "decayed" else None
    snz = snooze_until if state == "snoozed" else None
    with db.connect(db_path) as conn:
        if nudge_id is not None:
            where, arg = "id=?", nudge_id
        else:
            where, arg = "dedup_key=? AND state IN ('open','snoozed')", dedup_key
        cur = conn.execute(
            f"UPDATE nudges SET state=?, snooze_until=?, note=COALESCE(?, note),"
            f" decayed_at=?, updated_at=? WHERE {where}",
            (state, snz, note, decayed, ts, arg))
    return {"updated": cur.rowcount, "state": state}


def clear(db_path, dedup_key: str, *, now: Optional[datetime] = None) -> dict:
    """Condition no longer holds (rule says the nudge is moot) -> decayed."""
    return set_state(db_path, state="decayed", dedup_key=dedup_key, now=now)


# ── decay sweep (TTL + past-due) ──────────────────────────────────────────────

def decay_expired(db_path, *, now: Optional[datetime] = None) -> dict:
    """Mark OPEN nudges decayed per kind TTL (COS_DECAY_TTL_DAYS): None never
    decays (commitments), 0 clears once past `due`, N decays N days after the
    last signal (refreshed_at). Snoozed items are left to the founder."""
    nowdt = _now(now)
    ts = _iso(nowdt)
    decayed = 0
    with db.connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, kind, due, refreshed_at FROM nudges WHERE state='open'")]
        for r in rows:
            ttl = config.COS_DECAY_TTL_DAYS.get(r["kind"], 14)
            drop = False
            if ttl is None:
                drop = False
            elif ttl == 0:
                drop = _is_overdue(r["due"], now)
            else:
                ref = _parse(r["refreshed_at"])
                if ref and (nowdt - ref).total_seconds() / 86400.0 > ttl:
                    drop = True
            if drop:
                conn.execute("UPDATE nudges SET state='decayed', decayed_at=?,"
                             " updated_at=? WHERE id=?", (ts, ts, r["id"]))
                decayed += 1
    return {"decayed": decayed}


# ── reads (ranked) ────────────────────────────────────────────────────────────

def _rank(rows: List[dict], now: Optional[datetime]) -> List[dict]:
    for r in rows:
        r["urgency_live"] = compute_urgency(
            r["kind"], money_at_stake=r.get("money_at_stake"),
            score=r.get("score"), refreshed_at=r.get("refreshed_at"), now=now,
            overdue=_is_overdue(r.get("due"), now))
    return sorted(rows, key=lambda r: r["urgency_live"], reverse=True)


def active_nudges(db_path, *, dept: Optional[str] = None,
                  now: Optional[datetime] = None) -> List[dict]:
    """Open nudges + snoozed ones whose snooze_until has passed, ranked by live
    urgency (recency decay applied at read time)."""
    nowiso = _iso(_now(now))
    sql = ("SELECT * FROM nudges WHERE (state='open' OR (state='snoozed' AND"
           " (snooze_until IS NULL OR snooze_until<=?)))")
    args: list = [nowiso]
    if dept:
        sql += " AND dept=?"
        args.append(dept)
    with db.connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(sql, args)]
    return _rank(rows, now)


def top_n(db_path, n: Optional[int] = None, *,
          now: Optional[datetime] = None) -> List[dict]:
    """The brief's lead items: top-N active nudges by urgency."""
    n = n or config.COS_BRIEF_TOPN
    return active_nudges(db_path, now=now)[:n]


def grouped_queue(db_path, *, now: Optional[datetime] = None) -> dict:
    """Display queue for the cockpit/terminal: active nudges ranked by urgency,
    with high-volume kinds collapsed into ONE expandable group (e.g. '98 leads
    need Looms') so the queue stays scannable. Returns {entries, total}. Each
    entry is either a single nudge (grouped=False) or a group with items[]."""
    active = active_nudges(db_path, now=now)
    by_kind: dict = {}
    for n in active:
        by_kind.setdefault(n["kind"], []).append(n)
    entries = []
    for kind, items in by_kind.items():
        items.sort(key=lambda r: r["urgency_live"], reverse=True)
        if len(items) > config.COS_QUEUE_GROUP_MIN:
            entries.append({
                "grouped": True, "kind": kind, "count": len(items),
                "dept": items[0]["dept"],
                "title": f"{len(items)} {kind.replace('_', ' ')} items",
                "urgency": items[0]["urgency_live"],   # rank by the hottest
                "items": items})
        else:
            for it in items:
                entries.append({"grouped": False, "kind": kind,
                                "dept": it["dept"], "title": it["title"],
                                "action": it.get("action"), "due": it.get("due"),
                                "dedup_key": it["dedup_key"],
                                "urgency": it["urgency_live"]})
    entries.sort(key=lambda e: e["urgency"], reverse=True)
    return {"entries": entries, "total": len(active)}
