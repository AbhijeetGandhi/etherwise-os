"""Quick Capture — zero-friction note inbox, and the one law it must obey:

    WHATEVER HE TYPES IS VISIBLE SOMEWHERE, WITHIN SECONDS, OR IT IS A BUG.

/note, cockpit quick-add, and (once Gmail is connected) email-to-self drop raw
text into `notes`. Each is classified (Sonnet) into a nudge / commitment /
KB-note / idea. Actionable buckets create the target row; kb_note and idea stay
in `notes` as the record — and, since 2026-08-14, are RENDERED there with their
verdict and a one-click override, because "the record" used to mean a table no
screen read. Shadow-safe: local only.

TWO DEFECTS THIS MODULE WAS REBUILT AROUND (2026-08-14)
------------------------------------------------------
Abhijeet: *"whenever I typed anything in the capture box, like things I wanted
to add to today, it didn't get added."*

1. LATENCY. `classify_new` ran only inside the 2-hourly `cos_sweep`. Note #3 was
   typed at 14:49 IST; its card appeared at 16:30. A toast said "captured" and
   Today did not change for 101 minutes, which reads exactly like a lost note.
   Fixed at the seam, not here: `/api/note` spawns `bin/note-classify` as a
   detached CHILD process the moment the row commits (the cockpit is a
   view+action plane — no model calls in the server process, the same rule
   `/api/ask` obeys by shelling out to `bin/kb-ask`). The sweep still calls
   `classify_new`, unchanged, as the backstop for a child that never ran.

2. SILENT NON-ACTIONABLE. Note #2 classified as `kb_note` and produced nothing
   visible. `unresolved()` is the fix: every note that did not become a card is
   returned WITH the verdict that stopped it, for Today to render, and stays
   returned until he either overrides it (`resolve(..., 'card')`) or agrees
   (`resolve(..., 'filed')`).

WHY THE CLAIM EXISTS. Two writers now reach for the same row — the child and
the sweep. `claim()` is an atomic lease so only one of them pays for the model
call, and the lease EXPIRES so a child killed mid-flight (a `bin/cockpit
reload`, a laptop sleep) releases the note rather than stranding it. Nothing in
this file may leave a note in a state that is neither classified nor claimable.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from core import config, db, runner
from modules.chief_of_staff import nudges

MODEL_KEY = "drafting"   # Sonnet (design: Sonnet for capture classification)
BUCKETS = ("nudge", "commitment", "kb_note", "idea")

# Buckets that produce something he can see on Today by themselves. The other
# two are the record — and are now rendered as one.
ACTIONABLE = ("nudge", "commitment")

SCHEMA = {
    "type": "object",
    "properties": {
        "bucket": {"type": "string", "enum": list(BUCKETS)},
        "title": {"type": "string"},
        "action": {"type": ["string", "null"]},
        "due": {"type": ["string", "null"]},          # ISO date or null
        "who": {"type": "string", "enum": ["us", "client"]},
    },
    "required": ["bucket", "title", "action", "due", "who"],
    "additionalProperties": False,
}

SYSTEM = (
    "Classify a captured note from the founder of an automation agency into ONE "
    "bucket:\n"
    "- nudge: an actionable to-do ('follow up with X', 'fix the Y automation').\n"
    "- commitment: a promise with an owner (who=us|client), often a due date "
    "('tell client the register ships Friday').\n"
    "- kb_note: a reference fact to remember about a client or the business "
    "(no action needed).\n"
    "- idea: a someday/maybe idea or thought.\n"
    "title: a short imperative title. action: the next step (nudge only, else "
    "null). due: ISO date if a timeframe is stated, else null. who: us unless "
    "clearly the client's promise."
)

# What each verdict MEANS, in the words the UI shows him. A bucket name is not
# an explanation, and "kb_note" on a screen is the same silence as no screen.
VERDICT_TEXT = {
    "nudge": "a to-do — it is on the queue above",
    "commitment": "a promise with an owner — it is in commitments",
    "kb_note": "a fact worth remembering, not a to-do — filed as a note",
    "idea": "a someday/maybe idea — filed, not scheduled",
}


# ── writing a note ───────────────────────────────────────────────────────────

def add_note(db_path, text: str, source: str = "terminal") -> dict:
    """Drop a raw note into the inbox (state='new'). The classifier runs next —
    from the child process the cockpit spawns, or from the sweep, whichever
    reaches it first.

    Refuses two things, LOUDLY, and never by truncating: an empty note (there is
    nothing to save) and one past CAPTURE_MAX_CHARS. Truncating would be the
    same class of bug as the one this module exists to fix — silently keeping
    part of what he typed and telling him it was saved."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty note")
    if len(text) > config.CAPTURE_MAX_CHARS:
        raise ValueError(
            f"note is {len(text):,} characters; the cap is "
            f"{config.CAPTURE_MAX_CHARS:,}. Nothing was saved — your text is"
            f" still in the box. Split it, or paste it into a doc and capture"
            f" the pointer.")
    with db.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO notes (text, source, state) VALUES (?,?, 'new')",
            (text, source))
    return {"id": cur.lastrowid, "text": text, "source": source,
            "state": "new"}


# ── the classifier lease ─────────────────────────────────────────────────────

def _iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(config.TZ)).strftime("%Y-%m-%dT%H:%M:%S")


def claim(db_path, *, limit: int = 20, ids: Optional[List[int]] = None,
          now: Optional[datetime] = None) -> List[dict]:
    """Atomically take the classifier lease on unclassified notes, and return
    the rows we actually won.

    `UPDATE ... WHERE state='new' AND (claimed_at IS NULL OR claimed_at < stale)`
    is the whole mutual exclusion: SQLite serialises the write, so the loser's
    rowcount is 0 and it simply moves on. A stale lease is re-claimable so a
    child that died mid-call cannot strand a note — the worst case is one extra
    Sonnet call, and the alternative is a note nobody ever classifies.

    Notes that have failed CAPTURE_MAX_ATTEMPTS times are NOT claimed: they stop
    costing money and start being shown to him instead (`unresolved`)."""
    nowdt = now or datetime.now(config.TZ)
    stale = _iso(nowdt - timedelta(minutes=config.CAPTURE_CLAIM_TTL_MINUTES))
    ts = _iso(nowdt)
    won: List[dict] = []
    with db.connect(db_path) as conn:
        if ids:
            cand = [dict(r) for r in conn.execute(
                "SELECT id, text, source, attempts FROM notes WHERE state='new'"
                " AND id IN ({}) ORDER BY id".format(",".join("?" * len(ids))),
                [int(i) for i in ids])]
        else:
            cand = [dict(r) for r in conn.execute(
                "SELECT id, text, source, attempts FROM notes WHERE state='new'"
                " ORDER BY id LIMIT ?", (limit,))]
        for row in cand:
            if (row.get("attempts") or 0) >= config.CAPTURE_MAX_ATTEMPTS:
                continue
            cur = conn.execute(
                "UPDATE notes SET claimed_at=? WHERE id=? AND state='new'"
                " AND (claimed_at IS NULL OR claimed_at < ?)",
                (ts, row["id"], stale))
            if cur.rowcount:
                won.append(row)
    return won


def _release(db_path, note_id: int, error: Optional[str]) -> None:
    """Give the lease back and record WHY. The note stays state='new', so the
    next child or the next sweep will try again — and `unresolved()` shows it
    in the meantime, so a note that cannot be classified is loud, not lost."""
    with db.connect(db_path) as conn:
        conn.execute(
            "UPDATE notes SET claimed_at=NULL, attempts=attempts+1,"
            " last_error=? WHERE id=?", ((error or "")[:300] or None, note_id))


# ── routing a classified note ────────────────────────────────────────────────

def _apply(db_path, note: dict, cls: dict,
           now: Optional[datetime] = None) -> dict:
    bucket = cls.get("bucket")
    ref = None
    if bucket == "nudge":
        out = nudges.write_nudge(
            db_path, now=now, source="capture", dept="sales", kind="capture",
            title=cls.get("title") or note["text"][:80],
            action=cls.get("action"), due=cls.get("due"),
            dedup_key=f"note:{note['id']}")
        ref = str(out["id"])
    elif bucket == "commitment":
        key = f"commitment:note:{note['id']}"
        with db.connect(db_path) as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO commitments (who, text, due, source_cite,"
                    " dedup_key, state) VALUES (?,?,?,?,?, 'open')",
                    (cls.get("who") or "us", cls.get("title") or note["text"],
                     cls.get("due"), f"note/{note['id']}", key))
                ref = str(cur.lastrowid)
            except Exception:
                ref = None
    # kb_note / idea: the note row itself is the record — and `unresolved()`
    # renders it, which is the difference between a record and a hiding place.
    with db.connect(db_path) as conn:
        conn.execute("UPDATE notes SET state='classified', classified=?,"
                     " classified_ref=?, claimed_at=NULL, last_error=NULL"
                     " WHERE id=?", (bucket, ref, note["id"]))
    return {"note_id": note["id"], "bucket": bucket, "ref": ref}


def classify_new(db_path, limit: int = 20, now: Optional[datetime] = None,
                 _classify=None, ids: Optional[List[int]] = None) -> dict:
    """Classify unclassified notes (Sonnet) and route them. Returns per-bucket
    counts plus `failed`.

    Called from THREE places and must behave identically in all of them: the
    cockpit's spawned child (`bin/note-classify`, seconds after he types), the
    2-hourly sweep (the backstop), and tests. `ids` narrows it to the note the
    child was spawned for — but the child still sweeps the rest of the inbox,
    because a note stranded by an earlier failure is exactly what nobody
    notices."""
    notes = claim(db_path, limit=limit, ids=ids, now=now)
    counts = {b: 0 for b in BUCKETS}
    failed = 0
    for note in notes:
        try:
            cls = (_classify(db_path, note) if _classify
                   else _classify_one(db_path, note))
        except Exception as exc:                # noqa: BLE001 — see _release
            _release(db_path, note["id"], f"{type(exc).__name__}: {exc}")
            failed += 1
            continue
        if not cls:
            # The model answered nothing parseable. Same treatment as a raised
            # error: the note goes back in the inbox with a reason attached,
            # never quietly stays claimed.
            _release(db_path, note["id"], "classifier returned no verdict")
            failed += 1
            continue
        _apply(db_path, note, cls, now=now)
        counts[cls.get("bucket", "idea")] = counts.get(cls.get("bucket"), 0) + 1
    return {"classified": len(notes) - failed, "claimed": len(notes),
            "failed": failed, **counts}


def _classify_one(db_path, note: dict) -> Optional[dict]:
    today = datetime.now(config.TZ).strftime("%Y-%m-%d")
    # Only the first CAPTURE_CLASSIFY_CHARS reach the model. add_note already
    # refuses anything past CAPTURE_MAX_CHARS, so this is a second, cheaper
    # bound on the prompt rather than a place where text goes missing — the
    # WHOLE note is stored, and the whole note is what the UI shows.
    text = note["text"][:config.CAPTURE_CLASSIFY_CHARS]
    result = runner.claude_call(
        task_name="cos_capture", model_key=MODEL_KEY, system=SYSTEM,
        schema=SCHEMA,
        user_content=f"Today is {today}.\nNote: {text}",
        purpose="capture classify", max_output_tokens=300, db_path=db_path)
    return result.parsed


# ── what is still waiting on HIM (the Today surface) ─────────────────────────

def _commitment_will_surface(conn, ref: Optional[str]) -> bool:
    """A captured commitment only becomes a card once it is DATED and inside
    the horizon (`rules.rule_commitments_due` requires `due IS NOT NULL`). An
    undated one is a third silent hole in this path — a real promise that will
    never appear on any screen — so it is treated exactly like a kb_note: shown
    with its verdict, with a one-click way to make it a card."""
    if not ref:
        return False
    row = conn.execute("SELECT due, state FROM commitments WHERE id=?",
                       (ref,)).fetchone()
    return bool(row) and bool(row["due"]) and row["state"] == "open"


def unresolved(db_path, *, limit: int = 12,
               now: Optional[datetime] = None) -> dict:
    """Every captured note that is NOT represented by something he can see, in
    the order he typed them (newest first), with the verdict that put it there.

    Three populations, and each one is a way a note used to vanish:
      · state='new'   — still classifying, or the classifier failed. Either way
                        the text is on screen within a second of the POST.
      · kb_note/idea  — a deliberate "no card" verdict, now stated rather than
                        implied by an empty Today.
      · commitment with no due date — accepted as actionable, then filtered out
                        by a rule he never saw.
    A note leaves this list only when HE decides (resolve()), or when the thing
    it produced is genuinely live. Nothing ages out on its own; that is the
    point."""
    nowdt = now or datetime.now(config.TZ)
    stale = _iso(nowdt - timedelta(minutes=config.CAPTURE_CLAIM_TTL_MINUTES))
    items: List[dict] = []
    with db.connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, text, source, state, classified, classified_ref,"
            " attempts, last_error, claimed_at, created_at FROM notes"
            " WHERE resolved_at IS NULL ORDER BY id DESC")]
        for r in rows:
            bucket = r["classified"]
            if r["state"] == "new":
                # A raw string compare is safe HERE and nowhere else in this
                # codebase (see nudges.ts_key): both sides are written by _iso()
                # in IST with one fixed shape, so they are the same clock in the
                # same format. `claimed_at` deliberately does NOT come from
                # SQLite's datetime('now'), which would be UTC and 5h30m adrift.
                classifying = bool(r["claimed_at"] and r["claimed_at"] >= stale)
                exhausted = (r["attempts"] or 0) >= config.CAPTURE_MAX_ATTEMPTS
                r["verdict"] = ("classifying…" if classifying
                                else "could not be classified" if exhausted
                                else "waiting to be classified")
                r["verdict_detail"] = (
                    r["last_error"] and
                    f"last attempt failed: {r['last_error']}"
                    f" (tried {r['attempts']}×)")
                r["needs_decision"] = exhausted
                r["pending"] = not exhausted
            elif bucket in ("kb_note", "idea"):
                r["verdict"] = VERDICT_TEXT[bucket]
                r["verdict_detail"] = None
                r["needs_decision"] = True
                r["pending"] = False
            elif bucket == "commitment" and not _commitment_will_surface(
                    conn, r["classified_ref"]):
                r["verdict"] = ("read as a promise, but with no date it will"
                                " never reach Today")
                r["verdict_detail"] = None
                r["needs_decision"] = True
                r["pending"] = False
            else:
                continue                      # it produced something live
            items.append(r)
    total = len(items)
    return {"items": items[:limit], "total": total,
            "shown": min(total, limit),
            "pending": sum(1 for i in items if i.get("pending")),
            "needs_decision": sum(1 for i in items if i.get("needs_decision"))}


def status(db_path, note_id: int) -> dict:
    """One note, for the capture box to poll while the child classifies.

    Answers the only question the box has: is it a card yet, and if not, what
    did the classifier decide instead? `done` is the poll's stop condition —
    true once there is a verdict of ANY kind, including a failure, because
    "still trying" and "tried and failed" must not look the same."""
    with db.connect(db_path) as conn:
        r = conn.execute(
            "SELECT id, text, source, state, classified, classified_ref,"
            " attempts, last_error, created_at FROM notes WHERE id=?",
            (int(note_id),)).fetchone()
        if not r:
            return {"error": f"no note {note_id}"}
        out = dict(r)
        out["bucket"] = r["classified"]
        out["verdict"] = VERDICT_TEXT.get(r["classified"] or "")
        out["card_title"] = None
        out["card_key"] = None
        if r["classified"] == "nudge" and r["classified_ref"]:
            n = conn.execute("SELECT title, dedup_key, state FROM nudges"
                             " WHERE id=?", (r["classified_ref"],)).fetchone()
            if n:
                out["card_title"] = n["title"]
                out["card_key"] = n["dedup_key"]
    exhausted = (out["attempts"] or 0) >= config.CAPTURE_MAX_ATTEMPTS
    out["failed"] = bool(out["state"] == "new" and out["last_error"])
    out["done"] = out["state"] == "classified" or exhausted
    out["actionable"] = out["classified"] in ACTIONABLE
    return out


# ── his override ─────────────────────────────────────────────────────────────

RESOLUTIONS = ("card", "filed")


def resolve(db_path, note_id: int, action: str,
            now: Optional[datetime] = None) -> dict:
    """He read the verdict and answered it.

    'card'  — he disagrees (or the note never got a verdict at all): mint the
              capture card with the SAME dedup_key the classifier would have
              used, so his override and the machine's verdict can never end up
              as two cards for one note.
    'filed' — he agrees: keep it as a note, stop showing it on Today. Reversible
              only by capturing it again, which is the honest cost of a "yes,
              I've seen it" button.

    Idempotent: resolving twice is a no-op that says so."""
    if action not in RESOLUTIONS:
        raise ValueError(f"resolve action must be one of {list(RESOLUTIONS)}")
    ts = _iso(now)
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, text, classified, classified_ref, resolved_at,"
            " resolved_by FROM notes WHERE id=?", (int(note_id),)).fetchone()
        if not row:
            raise ValueError(f"no note {note_id}")
        if row["resolved_at"]:
            return {"ok": True, "note_id": row["id"], "action": row["resolved_by"],
                    "already": True, "ref": row["classified_ref"]}
        note = dict(row)
    ref, bucket = note["classified_ref"], note["classified"]
    if action == "card":
        out = nudges.write_nudge(
            db_path, now=now, source="capture", dept="sales", kind="capture",
            title=(note["text"] or "")[:80],
            action="Captured note — you asked for this to be a card.",
            dedup_key=f"note:{note['id']}")
        ref, bucket = str(out["id"]), "nudge"
    else:
        # 'filed' on a note the classifier never reached: it is a kb_note by
        # his decision, not by a verdict nobody made. Recorded as such rather
        # than left NULL, so `classified` never means two different things.
        bucket = bucket or "kb_note"
    with db.connect(db_path) as conn:
        conn.execute(
            "UPDATE notes SET resolved_at=?, resolved_by=?, claimed_at=NULL,"
            " state='classified', classified=?, classified_ref=? WHERE id=?",
            (ts, action, bucket, ref, note["id"]))
    return {"ok": True, "note_id": note["id"], "action": action, "ref": ref,
            "already": False}


def ideas(db_path) -> List[dict]:
    with db.connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, text, created_at FROM notes WHERE classified='idea'"
            " ORDER BY id DESC")]
