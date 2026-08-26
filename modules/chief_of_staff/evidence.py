"""Cross-channel evidence — "has anything happened with this counterparty since
I dealt with them?", answered across EVERY channel we observe.

Why this module exists (2026-08-12). Both producers of a "reply owed" card —
`rules.rule_owed_replies` and `cockpit.data.today()` — derived that claim from
`drafts.sent_status='pending' JOIN threads` and ranked it by
`threads.latest_message_dt`. That is an UPWORK-ONLY signal, and it was wrong in
two independent ways at once:

  1. It is not the counterparty. `latest_message_dt` is the newest row of ANY
     kind on the thread — including our own replies and Upwork's own system
     rows. Measured on the live DB: 90 of 139 threads had a `latest_message_dt`
     newer than their newest INBOUND message, and 21 threads had a `system` row
     as their latest. Julie Heffernan's card came back the day after a call
     purely because Upwork wrote a system row at 13:30Z.
  2. It is not the conversation. The Cradle Company has a signed, ACTIVE
     contract and a live Slack channel; its newest Upwork message is 2026-08-03
     and the pending draft is a relic. Read through Upwork alone that client
     is a cold lead who has ignored us for nine days. It is neither.

So: one place answers "when did they last speak", "when did WE last speak", and
"is this counterparty a contracted client or a prospect" — over every channel,
with the reliability of each channel written down rather than assumed.

CHANNEL RELIABILITY (verified against the live DB, 2026-08-12)
──────────────────────────────────────────────────────────────────────────────
upwork  `messages` keyed by thread_id. `direction` is USABLE but stored in
        MIXED CASE ('Inbound' 1143 / 'inbound' 458 / 'Outbound' 998 /
        'outbound' 388 / 'system' 76) — every comparison here lowercases, and a
        raw `direction='inbound'` filter silently drops 1143 rows. Every thread
        that has messages at all has at least one inbound (0 of 139 missing),
        so inbound-only is safe; the 2 threads with NO message rows fall back
        to `threads.latest_message_dt`.
slack   `events` where source='slack'. client_id is DETERMINISTIC (the client
        workspace IS the client, via registry slack.team_id) and `ts` is a real
        ISO instant. `direction` is set from Slack's own is_me cue or
        `config.JARVIS_SLACK_SELF_AUTHORS`, which since 2026-08-14 is the whole
        `config.INTERNAL_PEOPLE` roster, not just Abhijeet's two names. Until
        then a TEAMMATE answering a client in that client's own workspace was
        tagged 'inbound' — read as the CLIENT talking, which both re-showed a
        card that was already handled and moved "when did they last speak"
        forward on OUR message. The old miss biased toward SHOWING a card, never
        hiding one, so the correction can now hide one; that is intended, and it
        is why the roster is ONE list — a teammate missing from it re-creates the
        bug silently. A Slack inbound is still only proof of "activity".
email   `events` where source='email' and state='mapped'. client_id from the
        registry domain match. `direction` is NULL because the mail rails
        capture RECEIVED mail only (migration 022 says so explicitly), so a
        mapped email event IS inbound.
fathom  `sources`/`events`. A call is BILATERAL — it is contact with them, so
        it counts as us having engaged, never as an unanswered ask.
loom    `sources`/`events`. A Loom is something WE recorded and sent: ours.
crm / upwork rows in `sources` carry NO occurred_dt (71 + 2 rows) — unusable as
        a timestamp, so they are excluded rather than guessed at.

WHAT THIS MODULE WILL NOT DO
There is no cross-channel key for a thread with `threads.client_id IS NULL`
(86 of 139 threads today, and 6 of the 7 threads that currently hold a pending
draft). For those, evidence stays Upwork-only and `unproven` says so, so the
card can admit it rather than imply a search happened that did not. A wrong
suppression hides real work; an honest "we only looked at Upwork" does not.
"""
from __future__ import annotations

from typing import Optional, Sequence

from modules.chief_of_staff import nudges

def _newer(a: Optional[tuple], b: Optional[tuple]) -> Optional[tuple]:
    """Max of two (ts, channel) pairs by real UTC instant. Never a raw string
    compare — this codebase stores 'T...Z', ' ' -separated and +05:30 shapes and
    'T' sorts above ' ' (see nudges.ts_key)."""
    if a is None or not a[0]:
        return b
    if b is None or not b[0]:
        return a
    return a if nudges.ts_key(a[0]) >= nudges.ts_key(b[0]) else b


def _one(conn, sql: str, args: Sequence) -> Optional[str]:
    row = conn.execute(sql, tuple(args)).fetchone()
    return row[0] if row and row[0] else None


# ── Upwork (per thread) ──────────────────────────────────────────────────────

def upwork_signals(conn, thread_ids: Sequence[str]) -> dict:
    """{'inbound', 'outbound', 'any', 'oldest_inbound', 'has_messages'} for a
    set of threads.

    `inbound` is the NEWEST counterparty message across the group — the "has
    anything happened since?" question. `oldest_inbound` is the oldest of the
    per-thread newest inbounds — the "how long have I left this person hanging?"
    question, because one person can hold several Upwork rooms and ranking them
    by their freshest room hides real neglect. Two different questions; keeping
    one number for both is how a 30-day-old obligation reads as 1 day old.

    `any` is the raw newest row of any direction — kept ONLY as the fallback for
    threads we hold no message rows for. It is deliberately not the answer to
    "when did they last speak": that is what put a system row on the board."""
    ids = [t for t in (thread_ids or []) if t]
    if not ids:
        return {"inbound": None, "outbound": None, "any": None,
                "oldest_inbound": None, "has_messages": False}
    marks = ",".join("?" * len(ids))
    has = conn.execute(
        f"SELECT COUNT(*) FROM messages WHERE thread_id IN ({marks})",
        ids).fetchone()[0]
    if not has:
        # No message rows for these threads (2 of 139 live). The thread rollup
        # is all we have; using it is less precise but never hides an ask.
        newest = _one(conn, f"SELECT MAX(latest_message_dt) FROM threads"
                            f" WHERE id IN ({marks})", ids)
        return {"inbound": None, "outbound": None, "any": newest,
                "oldest_inbound": _one(
                    conn, f"SELECT MIN(latest_message_dt) FROM threads"
                          f" WHERE id IN ({marks}) AND latest_message_dt IS"
                          f" NOT NULL", ids),
                "has_messages": False}
    # Per-thread newest inbound, but ONLY for threads still waiting on us —
    # a room where WE spoke last holds no obligation. Before 2026-08-27 every
    # room counted, so once the group covered ALL of a person's rooms (which is
    # the fix for "the talking moved to the new chat and Today still nags about
    # the old one") a long-finished room would have dragged `waiting_since`
    # backwards and made an answered person look MORE overdue, not less.
    per_thread = [r[0] for r in conn.execute(
        f"SELECT MAX(CASE WHEN lower(direction)='inbound' THEN created_dt END) inb,"
        f" MAX(CASE WHEN lower(direction)='outbound' THEN created_dt END) outb"
        f" FROM messages WHERE thread_id IN ({marks}) GROUP BY thread_id", ids)
        if r[0] and not (r[1] and nudges.ts_key(r[1]) > nudges.ts_key(r[0]))]
    return {
        "inbound": _one(conn, f"SELECT MAX(created_dt) FROM messages WHERE"
                              f" thread_id IN ({marks}) AND"
                              f" lower(direction)='inbound'", ids),
        "outbound": _one(conn, f"SELECT MAX(created_dt) FROM messages WHERE"
                               f" thread_id IN ({marks}) AND"
                               f" lower(direction)='outbound'", ids),
        "any": _one(conn, f"SELECT MAX(created_dt) FROM messages WHERE"
                          f" thread_id IN ({marks})", ids),
        "oldest_inbound": min(per_thread, key=nudges.ts_key)
                          if per_thread else None,
        "has_messages": True}


# ── everything that is not Upwork (per client) ───────────────────────────────

def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone())


def client_signals(conn, client_id: Optional[str]) -> dict:
    """Newest inbound / ours signal per non-Upwork channel for one client.

    Returns {'inbound': (ts, channel)|None, 'ours': (ts, channel)|None,
             'channels': [names seen]}. Empty when the thread is unattributed —
    there is no key to search on and this module refuses to guess one."""
    out: dict = {"inbound": None, "ours": None, "channels": []}
    if not client_id:
        return out
    if _table_exists(conn, "events"):
        for r in conn.execute(
            "SELECT source, direction, MAX(ts) mx FROM events"
            " WHERE client_id=? AND ts IS NOT NULL AND ts!=''"
                " GROUP BY source, direction", (client_id,)):
            src = (r["source"] or "").lower()
            d = (r["direction"] or "").lower()
            if src not in out["channels"]:
                out["channels"].append(src)
            if src == "slack":
                slot = "ours" if d == "outbound" else "inbound"
            elif src == "email":
                slot = "inbound"        # the mail rails capture received mail only
            elif src in ("loom", "fathom"):
                slot = "ours"           # we recorded it / we were on the call
            else:
                continue                # unknown rail: do not invent a meaning
            out[slot] = _newer(out[slot], (r["mx"], src))
    if _table_exists(conn, "sources"):
        # sources is the knowledge-side mirror of the same touchpoints and is
        # often AHEAD of events (the bridges backfill it first). crm/upwork rows
        # carry no occurred_dt at all, so they are skipped, not defaulted.
        for r in conn.execute(
            "SELECT source_type, MAX(occurred_dt) mx FROM sources"
            " WHERE client_id=? AND occurred_dt IS NOT NULL AND occurred_dt!=''"
                " GROUP BY source_type", (client_id,)):
            src = (r["source_type"] or "").lower()
            if src not in out["channels"]:
                out["channels"].append(src)
            if src in ("loom", "fathom"):
                out["ours"] = _newer(out["ours"], (r["mx"], src))
            elif src == "email":
                out["inbound"] = _newer(out["inbound"], (r["mx"], src))
    return out


# ── engagement: is this counterparty a contracted client? ────────────────────

_ACTIVE = ("ACTIVE",)
_ENGAGED = ("ACTIVE", "PAUSED")


def engagement(conn, *, thread_ids: Sequence[str] = (),
               client_id: Optional[str] = None) -> dict:
    """Is this counterparty a contracted CLIENT or an open prospect?

    Answered from `contracts` (`upwork_status` ACTIVE|PAUSED|CLOSED), reached
    either through the thread's own `contract_id` or — when the thread is
    attributed — through any thread belonging to the same registry client. That
    is a real local fact with no inference in it: the Cradle Company's thread
    carries contract 44099615, ACTIVE since 2026-07-15.

    state: 'client'  a live contract exists            -> not a sales prospect
           'paused'  contract exists but is paused
           'past'    only closed contracts
           'unknown' no contract row reachable — we CANNOT prove either way,
                     and the caller must say so rather than assume 'prospect'.
    """
    out = {"state": "unknown", "active": False, "contract_id": None,
           "status": None, "since": None, "title": None, "via": None}
    if not _table_exists(conn, "contracts"):
        return out
    rows = []
    ids = [t for t in (thread_ids or []) if t]
    if ids:
        marks = ",".join("?" * len(ids))
        rows += [dict(r, via="thread") for r in conn.execute(
            f"SELECT id, upwork_status, start_dt, end_dt, title, paused"
            f" FROM contracts WHERE thread_id IN ({marks})", ids)]
    if client_id and _table_exists(conn, "threads"):
        rows += [dict(r, via="client") for r in conn.execute(
            "SELECT k.id, k.upwork_status, k.start_dt, k.end_dt, k.title,"
            " k.paused FROM contracts k JOIN threads t ON t.contract_id = k.id"
            " WHERE t.client_id = ?", (client_id,))]
    if not rows:
        return out
    # rank: a live contract anywhere beats a paused one beats a closed one
    def _rank(r):
        st = (r.get("upwork_status") or "").upper()
        return (2 if st in _ACTIVE and not r.get("paused") else
                1 if st in _ENGAGED else 0,
                nudges.ts_key(r.get("start_dt") or ""))
    best = max(rows, key=_rank)
    st = (best.get("upwork_status") or "").upper()
    out.update({"contract_id": best.get("id"), "status": st or None,
                "since": (best.get("start_dt") or "")[:10] or None,
                "title": best.get("title"), "via": best.get("via")})
    if st in _ACTIVE and not best.get("paused"):
        out.update({"state": "client", "active": True})
    elif st in _ENGAGED:
        out["state"] = "paused"
    else:
        out["state"] = "past"
    return out


# ── the one answer both producers ask for ────────────────────────────────────

def counterparty(conn, *, thread_ids: Sequence[str] = (),
                 client_id: Optional[str] = None,
                 thread_latest: Optional[str] = None) -> dict:
    """Everything a follow-up card needs to be honest about, in one read.

      ts / channel        when the COUNTERPARTY last spoke, on ANY channel.
                          This is the authority for "is a Done still valid".
                          Never our own voice, never a system row.
      waiting_since       what the card's AGE is measured from, which is a
                          DIFFERENT question: the oldest obligation this person
                          holds (they may have two Upwork rooms), unless a
                          non-Upwork channel shows them speaking more recently —
                          in which case they are demonstrably not sitting
                          waiting N days, whatever one room says.
      ours_ts / ours_channel  when WE last spoke to them, on any channel.
      answered_since      we spoke AFTER they last did -> no reply is owed.
                          Provable, not a guess: Cradle's Upwork message of
                          2026-08-03 14:41Z was answered on Slack at 15:35Z.
      channels            every channel that contributed a timestamp.
      unproven            set when we could look at Upwork ONLY (unattributed
                          thread). The card must show this, not imply a
                          cross-channel search that never happened.
    """
    up = upwork_signals(conn, thread_ids)
    cl = client_signals(conn, client_id)

    inbound = _newer(
        (up["inbound"], "upwork") if up["inbound"] else None, cl["inbound"])
    if inbound is None and up["any"]:
        # no directional data at all for this thread -> fall back to the raw
        # rollup. Less precise, but silence here would hide a real ask.
        inbound = (up["any"], "upwork")
    ours = _newer((up["outbound"], "upwork") if up["outbound"] else None,
                  cl["ours"])

    answered = bool(inbound and ours
                    and nudges.ts_key(ours[0]) > nudges.ts_key(inbound[0]))
    channels = (["upwork"] if up["any"] or up["inbound"] else []) + [
        c for c in cl["channels"] if c not in ("crm",)]
    # the age clock: the OLDEST Upwork obligation by default, overridden by a
    # newer non-Upwork sighting of the counterparty. Cradle's oldest Upwork
    # inbound is 08-03 (9 days) but they were on Slack on 08-08 (4) — nobody has
    # been ignored for nine days, and saying so was the whole complaint.
    waiting = up["oldest_inbound"] or (thread_latest if not up["has_messages"]
                                       else None)
    if cl["inbound"] and (waiting is None
                          or nudges.ts_key(cl["inbound"][0]) >
                          nudges.ts_key(waiting)):
        waiting = cl["inbound"][0]
    return {
        "ts": inbound[0] if inbound else (thread_latest or None),
        "channel": inbound[1] if inbound else ("upwork" if thread_latest else None),
        "waiting_since": waiting or (inbound[0] if inbound else thread_latest),
        "ours_ts": ours[0] if ours else None,
        "ours_channel": ours[1] if ours else None,
        "answered_since": answered,
        "channels": channels,
        "upwork_ts": up["any"] or thread_latest,
        "unproven": None if client_id else
                    "thread is not attributed to a registry client — only"
                    " Upwork was checked",
    }


def age_days(ts: Optional[str], now) -> Optional[int]:
    """Whole CALENDAR days from `ts` to `now`.

    `now` may be a date/datetime or a 'YYYY-MM-DD' string (the cockpit pins its
    reference day so `today(today=...)` is reproducible — a real-clock read here
    silently ignored that parameter and every fixture aged by months). This is
    deliberately date-vs-date, mirroring the SQL it replaced
    (`julianday(<now>) - julianday(substr(<ts>,1,10))` cast to INT): a card's age
    must not tick over mid-afternoon because a timezone boundary moved.

    None when there is no timestamp — an unmeasured age renders '—', never 0."""
    if not ts:
        return None
    from datetime import date as _date
    key = nudges.ts_key(ts)                  # -> 'YYYY-MM-DD HH:MM:SS' UTC
    if not key:
        return None
    try:
        then = _date.fromisoformat(key[:10])
    except ValueError:
        return None
    if isinstance(now, str):
        try:
            ref = _date.fromisoformat(now[:10])
        except ValueError:
            return None
    else:
        ref = now.date() if hasattr(now, "date") else now
    return max(0, (ref - then).days)


def describe(ev: dict, eng: dict) -> str:
    """One human line for the card detail: who they are and where the
    conversation actually is. Says what is unknown instead of padding it."""
    bits = []
    if eng.get("active"):
        bits.append(f"active client (contract {eng['contract_id']}"
                    + (f" since {eng['since']}" if eng.get("since") else "") + ")")
    elif eng.get("state") == "paused":
        bits.append(f"client, contract {eng['contract_id']} PAUSED")
    elif eng.get("state") == "past":
        bits.append(f"past client (contract {eng['contract_id']} closed)")
    else:
        # 'unknown', said as unknown: no contract row is reachable from this
        # thread, which is NOT proof they are a prospect. The card stays on the
        # prospect track because that is the safe default, and says why.
        bits.append("no contract reachable from this thread — shown as a"
                    " prospect because we cannot prove otherwise")
    if ev.get("ts"):
        bits.append(f"last heard from them {str(ev['ts'])[:10]} on {ev['channel']}")
    else:
        bits.append("never heard from them on any channel we read")
    if ev.get("ours_ts"):
        bits.append(f"last from us {str(ev['ours_ts'])[:10]}"
                    f" on {ev['ours_channel']}")
    if ev.get("unproven"):
        bits.append(ev["unproven"])
    return "; ".join(bits)
