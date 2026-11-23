"""The other call recorders: Fireflies and ClickUp's AI Notetaker (2026-11-24).

Why this exists
---------------
Abhijeet: "the way you have the fathom recording being watched — use this
fireflies api key, all my globaltize calls are recorded here, and then use my
clickup api key to see all recording that go there now."

Fathom was the one recorder this system knew (`fathom_poll` -> knowledge-inbox
-> ingest -> extract -> dossier, and the Jarvis bridge that maps a call to a
client). It is no longer the only one on his calls:

  * FIREFLIES records the Globaltize calls — it is THEIR workspace, his seat is
    abhijeet@globaltize.com;
  * CLICKUP's AI Notetaker has written a meeting-notes doc (doc type 3) for his
    other calls since 16 Oct — one markdown page: attendees, duration, an AI
    summary, and the transcript;
  * and Fathom is still running beside both.

What this module does: reads the two new recorders, turns each recording into
the SAME payload shape a Fathom meeting has, and drops it in the same inbox —
so everything downstream (ingest, client attribution, fact extraction, the
dossier, promises, the Review queue for a caller nobody knows) treats it as
what it is, a recorded call, with no second pipeline to keep in step. The UI
already calls that source "a call"; `recorded_by` and the URL say which
recorder it came from, and the id keeps its prefix (`fireflies-…`, `clickup-…`).

Two rules that are NOT negotiable
---------------------------------
1. FIREFLIES IS SOMEBODY ELSE'S COMPANY. The key lists every recording in
   Globaltize's workspace — measured 24 Nov: fifty candidate interviews and
   client calls in three hours, none of them his. This module only ever asks
   for calls that one of `config.FIREFLIES_PARTICIPANT_EMAILS` took part in
   (the API filters, we do not list-and-discard), and re-checks the participant
   list on what comes back. 29 calls since 20 Aug, not thousands.
2. ONE COPY PER CALL. The same call is on Fathom AND Fireflies (13 Nov, "Nick x
   Abhijeet"), on Fathom AND ClickUp (the Hermon day-start, every morning).
   Ingesting both would double every fact and every promise. `find_twin`
   compares a new recording with every call already held around that day: the
   two overlap for at least half of the shorter one and share a participant.
   The copy that arrived first stays; Fathom is polled first, and a recording
   younger than CALL_RECORDER_SETTLE_MINUTES waits a run so Fathom's copy (the
   richest: invitee emails and timestamps) has had its chance to arrive.

`call_recordings` (migration 049) is the list of what the recorders showed us
and what became of each. Reads only, on both services.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core import config, db
from modules.knowledge import ingest

UTC = timezone.utc
FINAL_STATES = ("saved", "twin", "skipped")
MIN_LINES = 4                     # fewer transcript lines than this is not a call
_TIMEOUT = 60

# A label on a call that is a recorder, not a person ("Hermon Notes (Jaap's
# Bot) - transcribing", "Fireflies.ai Notetaker"). Never a participant.
BOT_LABEL = re.compile(
    r"\b(bot|note-?taker|notes?\s*taker|fireflies|fathom|otter|read\.ai|recorder)\b"
    r"|transcribing", re.I)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _instant(v) -> Optional[datetime]:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _hms(seconds: float) -> str:
    s = max(0, int(seconds or 0))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _segments(lines: list) -> list:
    """[(speaker, text, seconds|None)] -> Fathom transcript segments, with
    consecutive lines by one speaker folded into a single turn."""
    out = []
    for speaker, text, at in lines:
        speaker, text = (speaker or "").strip(), (text or "").strip()
        if not text:
            continue
        if out and out[-1]["speaker"]["display_name"] == speaker:
            out[-1]["text"] += " " + text
            continue
        seg = {"speaker": {"display_name": speaker, "matched_calendar_invitee_email": None},
               "text": text}
        if at is not None:
            seg["timestamp"] = _hms(at)
        out.append(seg)
    return out


# ── Fireflies ────────────────────────────────────────────────────────────────

_FF_LIST = ("query L($email: String, $limit: Int, $skip: Int) {"
            " transcripts(participant_email: $email, limit: $limit, skip: $skip)"
            " { id title date duration participants } }")
_FF_ONE = ("query T($id: String!) { transcript(id: $id) { id title date duration"
           " organizer_email host_email participants transcript_url"
           " meeting_attendees { displayName email name } speakers { id name }"
           " sentences { index speaker_name text start_time }"
           " summary { overview action_items } } }")
_FF_PAGE = 50


class Fireflies:
    """The two reads this module makes of Fireflies. The key is never logged,
    echoed or put in metrics."""

    def __init__(self, key: Optional[str] = None):
        self._key = key

    def key(self) -> str:
        if self._key is None:
            try:
                self._key = config.FIREFLIES_KEY_FILE.read_text().strip()
            except OSError as exc:
                raise RuntimeError(f"no Fireflies key on file ({config.FIREFLIES_KEY_FILE.name})") from exc
            if not self._key:
                raise RuntimeError("the Fireflies key file is empty")
        return self._key

    def _gql(self, query: str, variables: dict) -> dict:
        req = urllib.request.Request(
            config.FIREFLIES_API,
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Authorization": f"Bearer {self.key()}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            out = json.loads(resp.read().decode())
        if out.get("errors"):
            raise RuntimeError("Fireflies: " + json.dumps(out["errors"])[:300])
        return out.get("data") or {}

    def list(self, email: str) -> list:
        """Every call `email` took part in — the filter is the API's."""
        out, skip = [], 0
        while skip < _FF_PAGE * 20:
            page = self._gql(_FF_LIST, {"email": email, "limit": _FF_PAGE, "skip": skip}
                             ).get("transcripts") or []
            out += page
            if len(page) < _FF_PAGE:
                break
            skip += _FF_PAGE
        return out

    def get(self, native_id: str) -> dict:
        return self._gql(_FF_ONE, {"id": native_id}).get("transcript") or {}


def fireflies_window(t: dict) -> tuple:
    """(start, end) of a Fireflies call: `date` is the start in ms, `duration`
    is minutes."""
    try:
        start = datetime.fromtimestamp(int(t.get("date") or 0) / 1000, UTC)
    except (TypeError, ValueError):
        return None, None
    return start, start + timedelta(minutes=float(t.get("duration") or 0))


def is_ours(t: dict, emails=None) -> bool:
    """Did one of OUR addresses take part? The second lock on rule 1: even if
    the API's filter ever widened, a recording nobody of ours was on is not
    read into this system."""
    ours = {e.lower() for e in (emails or config.FIREFLIES_PARTICIPANT_EMAILS)}
    there = {str(p).strip().lower() for p in (t.get("participants") or [])}
    there |= {str(a.get("email") or "").strip().lower()
              for a in (t.get("meeting_attendees") or []) if isinstance(a, dict)}
    return bool(ours & there)


def normalize_fireflies(t: dict) -> Optional[dict]:
    """A Fireflies transcript -> the Fathom meeting shape; None when there is
    no transcript on it yet."""
    lines = [(s.get("speaker_name"), s.get("text"), s.get("start_time"))
             for s in (t.get("sentences") or []) if isinstance(s, dict)]
    segs = _segments(lines)
    if len(segs) < MIN_LINES:
        return None
    start, end = fireflies_window(t)
    people, seen = [], set()
    for a in (t.get("meeting_attendees") or []):
        if isinstance(a, dict) and (a.get("email") or a.get("displayName") or a.get("name")):
            em = (a.get("email") or "").strip().lower()
            people.append({"name": (a.get("displayName") or a.get("name") or "").strip(),
                           "email": em or None})
            seen.add(em)
    for p in (t.get("participants") or []):
        p = str(p).strip().lower()
        if p and p not in seen:
            people.append({"name": "", "email": p})
            seen.add(p)
    for sp in (t.get("speakers") or []):
        if isinstance(sp, dict) and sp.get("name"):
            people.append({"name": sp["name"].strip(), "email": None})
    summ = t.get("summary") or {}
    return {
        "recorder": "fireflies", "recording_id": f"fireflies-{t['id']}",
        "title": t.get("title"), "meeting_title": t.get("title"),
        "url": t.get("transcript_url") or f"https://app.fireflies.ai/view/{t['id']}",
        "created_at": _iso(end), "recording_start_time": _iso(start),
        "recording_end_time": _iso(end), "interval_estimated": False,
        "recorded_by": {"name": "Fireflies (Globaltize's workspace)"},
        "participants": people, "transcript": segs,
        "default_summary": {"template_name": "Fireflies",
                            "markdown_formatted": "\n\n".join(
                                x for x in (summ.get("overview"), summ.get("action_items")) if x)},
    }


# ── ClickUp AI Notetaker ─────────────────────────────────────────────────────

_CU = "https://api.clickup.com/api"
_CU_MIN_INTERVAL = 0.7
_ATTENDEES = re.compile(r"^\*\*Attendees:\*\*\s*(.+)$", re.M)
_DURATION = re.compile(r"^\*\*Duration:\*\*\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.M)
_RECORDING = re.compile(r"\((https://[^)\s]*clickup-attachments\.com/[^)\s]+)\)")
_TRANSCRIPT_MARK = re.compile(r"^(?:\*\s+|#{1,4}\s+)Transcript\s*$", re.M)
_TURN = re.compile(r"^\s*\*\*(.{1,80}?):\*\*\s*(.*)$")
# The doc appears a few minutes after the call ends (measured on one call:
# Fathom's recording ended 19:17, the doc was created 19:22). The end time is
# therefore an ESTIMATE, and `same_call` allows for it.
CLICKUP_DOC_LAG = timedelta(minutes=4)


class ClickUpNotes:
    """The reads this module makes of ClickUp: the workspace, its docs, one
    doc's pages. The token is the one every other ClickUp reader here uses."""

    def __init__(self, token: Optional[str] = None):
        self._tok = token
        self._ws = None
        self._last = 0.0

    def token(self) -> str:
        if self._tok is None:
            from modules.hours import client as hours_client
            self._tok = hours_client.token()
        return self._tok

    def _get(self, url: str) -> dict:
        # ClickUp answers 429 past ~100 calls a minute and four other readers
        # here share the token; a backfill is one read per doc, so pace it
        wait = _CU_MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        req = urllib.request.Request(url, headers={"Authorization": self.token()})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "{}")

    def workspace(self) -> str:
        if self._ws is None:
            teams = self._get(f"{_CU}/v2/team").get("teams") or []
            if not teams:
                raise RuntimeError("ClickUp returned no workspace for this token")
            self._ws = str(teams[0]["id"])
        return self._ws

    def docs(self) -> list:
        """Every AI Notetaker doc in the workspace (doc type 3), newest or not."""
        out, cursor, pages = [], None, 0
        while pages < 30:
            pages += 1
            q = {"limit": 100}
            if cursor:
                q["next_cursor"] = cursor
            page = self._get(f"{_CU}/v3/workspaces/{self.workspace()}/docs?"
                             + urllib.parse.urlencode(q))
            docs = page.get("docs") or []
            out += [d for d in docs if d.get("type") == config.CLICKUP_NOTETAKER_DOC_TYPE
                    and not d.get("deleted")]
            cursor = page.get("next_cursor")
            if not cursor or not docs:
                break
        return out

    def page(self, doc_id: str) -> tuple:
        """(page_id, markdown) of the doc's notes page; (None, '') if it has none."""
        pages = self._get(f"{_CU}/v3/workspaces/{self.workspace()}/docs/{doc_id}/pages?"
                          + urllib.parse.urlencode({"max_page_depth": -1,
                                                    "content_format": "text/md"}))
        pages = pages if isinstance(pages, list) else (pages.get("pages") or [])
        for p in pages:
            if (p.get("content") or "").strip():
                return p.get("id"), p["content"]
        return None, ""


def clickup_created(doc: dict) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(doc.get("date_created") or 0) / 1000, UTC)
    except (TypeError, ValueError):
        return None


def parse_notes(md: str) -> dict:
    """The attendees, duration and transcript turns out of a notetaker page."""
    m = _ATTENDEES.search(md or "")
    attendees = [a.strip() for a in (m.group(1).split(",") if m else []) if a.strip()]
    d = _DURATION.search(md or "")
    secs = (int(d.group(1) or 0) * 3600 + int(d.group(2) or 0) * 60 + int(d.group(3) or 0)) if d else 0
    mark = _TRANSCRIPT_MARK.search(md or "")
    lines = []
    if mark:
        for raw in md[mark.end():].splitlines():
            t = _TURN.match(raw)
            if t:
                lines.append([t.group(1).strip(), t.group(2).strip(), None])
            elif raw.strip() and lines:
                lines[-1][1] += " " + raw.strip()        # a wrapped line of the same turn
    rec = _RECORDING.search(md or "")
    return {"attendees": attendees, "seconds": secs, "lines": [tuple(x) for x in lines],
            "summary": (md[:mark.start()] if mark else md or "").strip(),
            "recording_url": rec.group(1) if rec else None}


def normalize_clickup(doc: dict, page_id: Optional[str], md: str) -> Optional[dict]:
    """A ClickUp notetaker doc -> the Fathom meeting shape; None while the page
    has no transcript on it."""
    n = parse_notes(md)
    segs = _segments(n["lines"])
    if len(segs) < MIN_LINES:
        return None
    created = clickup_created(doc)
    end = (created - CLICKUP_DOC_LAG) if created else None
    start = (end - timedelta(seconds=n["seconds"] or 1800)) if end else None
    ws = doc.get("workspace_id")
    title = re.sub(r"\s+-\s+\d{1,2}/\d{1,2}/\d{4}\s*$", "", doc.get("name") or "").strip()
    people = [{"name": a, "email": None} for a in n["attendees"] if not BOT_LABEL.search(a)]
    return {
        "recorder": "clickup", "recording_id": f"clickup-{doc['id']}",
        "title": title or doc.get("name"), "meeting_title": doc.get("name"),
        "url": f"https://app.clickup.com/{ws}/v/dc/{doc['id']}" + (f"/{page_id}" if page_id else ""),
        "created_at": _iso(end) if end else None,
        "recording_start_time": _iso(start) if start else None,
        "recording_end_time": _iso(end) if end else None, "interval_estimated": True,
        "recorded_by": {"name": "ClickUp AI Notetaker"},
        "recording_url": n["recording_url"],
        "participants": people, "transcript": segs,
        "default_summary": {"template_name": "ClickUp AI Notetaker",
                            "markdown_formatted": n["summary"]},
    }


# ── one copy per call ────────────────────────────────────────────────────────

def _key_of(label: str) -> Optional[str]:
    """A person's first-name key: 'Nick Esquivel' and nick@globaltize.com are
    both 'nick'. Crude on purpose — it only has to say 'the same people'."""
    s = (label or "").strip().lower()
    if not s or BOT_LABEL.search(s):
        return None
    if "@" in s:
        s = s.split("@", 1)[0]
    return next((t for t in re.split(r"[^a-z]+", s) if len(t) >= 2), None)


def _internal_keys() -> set:
    return {k for k in (_key_of(p) for p in config.INTERNAL_PEOPLE) if k}


def people(payload: dict) -> tuple:
    """(everyone's keys, the keys of people who are not us)."""
    ours = _internal_keys()
    keys, outside = set(), set()
    for label in ingest.parse_fathom(payload).get("participants") or []:
        k = _key_of(label)
        if not k:
            continue
        keys.add(k)
        if not (config.is_internal_person(label) or k in ours):
            outside.add(k)
    return keys, outside


def interval(payload: dict) -> Optional[tuple]:
    """(start, end) of a recording in either Fathom shape. The live API says
    `recording_start_time` / `recording_end_time` / `created_at`; the historical
    EXPORT says `recording_start` / `recording_end` / `date` — and missing that
    made every 2025 call look new on the first dry run (24 Nov: "Weekly Check
    In" of 29 Sep 2025 offered as a new call beside Fathom's own copy of it).
    A payload with only an end is given the hour before it."""
    end = (_instant(payload.get("recording_end_time")) or _instant(payload.get("recording_end"))
           or _instant(payload.get("created_at")) or _instant(payload.get("date")))
    start = (_instant(payload.get("recording_start_time"))
             or _instant(payload.get("recording_start")))
    if end is None:
        return None
    if start is None or start >= end:
        start = end - timedelta(hours=1)
    return start, end


def _overlap(a: tuple, b: tuple, shift: timedelta = timedelta(0)) -> float:
    lo = max(a[0] + shift, b[0])
    hi = min(a[1] + shift, b[1])
    return max(0.0, (hi - lo).total_seconds())


def same_call(a: dict, b: dict) -> bool:
    """Are two recordings the same call? They overlap for at least half of the
    shorter one and the same people are on both. When one clock is an estimate
    (ClickUp) it may drift by CALL_TWIN_CLOCK_SLACK_MINUTES — but only if the
    two share someone from OUTSIDE, so two short back-to-back calls with
    different clients are never folded into one."""
    ia, ib = interval(a), interval(b)
    if not ia or not ib:
        return False
    shorter = min((ia[1] - ia[0]).total_seconds(), (ib[1] - ib[0]).total_seconds())
    if shorter < 60:
        return False
    (ka, oa), (kb, ob) = people(a), people(b)
    if ka and kb and not (ka & kb):
        return False
    if _overlap(ia, ib) >= 0.5 * shorter:
        return True
    if (a.get("interval_estimated") or b.get("interval_estimated")) and (oa & ob):
        slack = timedelta(minutes=config.CALL_TWIN_CLOCK_SLACK_MINUTES)
        return max(_overlap(ia, ib, slack), _overlap(ia, ib, -slack)) >= 0.5 * shorter
    return False


def _read(path) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return None


def is_recorder_id(rid) -> bool:
    return str(rid).startswith(("fireflies-", "clickup-"))


def find_twin(conn, payload: dict, extra=(), inbox: Optional[Path] = None,
              fathom_only: bool = False) -> Optional[str]:
    """The recording_id of a call already held that is this same call, or None.
    Looks, within a day of it, at every call source, every recording this module
    has saved, EVERY FILE IN THE CALL INBOX, plus `extra` (payloads accepted
    earlier in this run).

    The inbox is the part that was missing on the first live run (24 Nov): the
    Fathom poll had just dropped three meetings there, they were not `sources`
    yet (ingest runs after both polls), and so two of ClickUp's copies of those
    very calls were saved beside them. "Fathom is polled first so its copy
    wins" was only true for Fathom calls of earlier days. `fathom_only` is for
    the repair pass: only a twin Fathom itself recorded counts."""
    iv = interval(payload)
    if not iv:
        return None
    days = [(iv[1] + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)]
    marks = ",".join("?" * len(days))
    paths = [r[0] for r in conn.execute(
        f"SELECT raw_path FROM sources WHERE source_type='fathom' AND raw_path IS NOT NULL"
        f" AND substr(occurred_dt,1,10) IN ({marks})", days)]
    paths += [r[0] for r in conn.execute(
        f"SELECT file FROM call_recordings WHERE state='saved' AND file IS NOT NULL"
        f" AND substr(ended_at,1,10) IN ({marks})", days)]
    inbox = inbox or (config.KNOWLEDGE_INBOX / "fathom")
    if inbox.exists():
        for day in days:                       # files are named <day>__<recording id>.json
            paths += [str(p) for p in sorted(inbox.glob(f"{day}__*.json"))]
    me = str(payload.get("recording_id"))
    seen = set()
    candidates = list(extra)
    for p in paths:
        key = str(Path(p).resolve()) if p else p
        if key in seen:
            continue
        seen.add(key)
        other = _read(p)
        if other:
            candidates.append(other)
    for other in candidates:
        rid = str(other.get("recording_id") or other.get("id") or "")
        if not rid or rid == me or (fathom_only and is_recorder_id(rid)):
            continue
        if same_call(payload, other):
            return rid
    return None


def retire_late_twins(db_path, *, inbox: Optional[Path] = None, dry_run: bool = False) -> list:
    """A recording this module saved that turns out to be a call FATHOM also
    recorded, and that nothing has read yet: take ours back out. Its file and
    its un-extracted `sources` row go, the registry says `twin`. Only a copy
    still waiting for extraction is ever retired — once facts have been drawn
    from one it stays, and the settle delay is what keeps that from mattering."""
    out = []
    with db.connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT recorder, native_id, file FROM call_recordings WHERE state='saved'"
            " AND file IS NOT NULL")]
        for r in rows:
            payload = _read(r["file"])
            if not payload:
                continue
            ref = f"fathom/{payload['recording_id']}"
            src = conn.execute("SELECT id, status FROM sources WHERE source_ref=?",
                               (ref,)).fetchone()
            if src and src["status"] != "ingested":
                continue                              # already read: it stays
            twin = find_twin(conn, payload, inbox=inbox, fathom_only=True)
            if not twin:
                continue
            out.append({"recording": payload["recording_id"], "twin_of": twin,
                        "title": payload.get("title")})
            if dry_run:
                continue
            if src:
                conn.execute("DELETE FROM source_fts WHERE source_id=?", (src["id"],))
                conn.execute("DELETE FROM sources WHERE id=? AND status='ingested'", (src["id"],))
            Path(r["file"]).unlink(missing_ok=True)
            conn.execute(
                "UPDATE call_recordings SET state='twin', twin_of=?, file=NULL,"
                " note='Fathom had this call too; our copy was taken back out before"
                " anything read it', checked_at=datetime('now')"
                " WHERE recorder=? AND native_id=?", (twin, r["recorder"], r["native_id"]))
    return out


# ── the poll ─────────────────────────────────────────────────────────────────

def _known(conn) -> dict:
    return {(r["recorder"], r["native_id"]): dict(r)
            for r in conn.execute("SELECT * FROM call_recordings")}


def _record(conn, recorder, native_id, *, title, start, end, state,
            twin_of=None, file=None, note=None) -> None:
    conn.execute(
        "INSERT INTO call_recordings (recorder, native_id, title, started_at, ended_at,"
        " state, twin_of, file, note) VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(recorder, native_id) DO UPDATE SET title=excluded.title,"
        " started_at=excluded.started_at, ended_at=excluded.ended_at, state=excluded.state,"
        " twin_of=excluded.twin_of, file=excluded.file, note=excluded.note,"
        " checked_at=datetime('now')",
        (recorder, native_id, title, _iso(start) if start else None,
         _iso(end) if end else None, state, twin_of, file, note))


def _listing(fireflies, clickup, errors: dict) -> list:
    """Every recording the two recorders have, as work items, oldest first.
    Fireflies before ClickUp at the same instant: when both hold a call, the
    copy with addresses and timestamps is the one to keep."""
    items = []
    try:
        seen = set()
        for email in config.FIREFLIES_PARTICIPANT_EMAILS:
            for t in fireflies.list(email):
                if t.get("id") and t["id"] not in seen:
                    seen.add(t["id"])
                    start, end = fireflies_window(t)
                    items.append({"recorder": "fireflies", "native_id": t["id"],
                                  "title": t.get("title"), "start": start, "end": end,
                                  "ready_at": end})
    except Exception as exc:                                   # noqa: BLE001
        errors["fireflies"] = repr(exc)[:200]
    try:
        for d in clickup.docs():
            created = clickup_created(d)
            items.append({"recorder": "clickup", "native_id": d["id"], "title": d.get("name"),
                          "start": None, "end": created, "ready_at": created, "doc": d})
    except Exception as exc:                                   # noqa: BLE001
        errors["clickup"] = repr(exc)[:200]
    far = datetime(1970, 1, 1, tzinfo=UTC)
    items.sort(key=lambda i: (i["end"] or far, 0 if i["recorder"] == "fireflies" else 1))
    return items


def _fetch(item: dict, fireflies, clickup) -> Optional[dict]:
    if item["recorder"] == "fireflies":
        t = fireflies.get(item["native_id"])
        if not t or not is_ours(t):
            raise PermissionError("not a call one of our addresses took part in")
        return normalize_fireflies(t)
    page_id, md = clickup.page(item["native_id"])
    return normalize_clickup(item["doc"], page_id, md)


def poll(ctx, *, fireflies=None, clickup=None, now: Optional[datetime] = None,
         dry_run: bool = False, inbox: Optional[Path] = None) -> dict:
    """Read both recorders; save what is new and not a twin. Never raises for
    one recorder being down — the other still runs, and the error is returned
    (and filed as an anomaly by the caller)."""
    dbp = getattr(ctx, "db_path", None) or config.DB_PATH
    now = now or datetime.now(UTC)
    fireflies = fireflies or Fireflies()
    clickup = clickup or ClickUpNotes()
    inbox = inbox or (config.KNOWLEDGE_INBOX / "fathom")
    settle = timedelta(minutes=config.CALL_RECORDER_SETTLE_MINUTES)
    give_up = timedelta(days=config.CALL_RECORDER_WAIT_DAYS)
    m = {"listed": {"fireflies": 0, "clickup": 0}, "saved": [], "twins": [],
         "waiting": 0, "skipped": 0, "settling": 0, "deferred": 0, "before_floor": 0,
         "errors": {}, "dry_run": bool(dry_run)}
    # a copy saved on an earlier run that Fathom has since turned out to hold too
    m["retired"] = retire_late_twins(dbp, inbox=inbox, dry_run=dry_run)
    floor = _instant(config.CALL_RECORDER_SINCE)
    items = []
    for i in _listing(fireflies, clickup, m["errors"]):
        m["listed"][i["recorder"]] += 1
        if floor and i["end"] and i["end"] < floor:
            m["before_floor"] += 1                  # left on the recorder, on purpose
            continue
        items.append(i)
    with db.connect(dbp) as conn:
        known = _known(conn)
    fetched = {"fireflies": 0, "clickup": 0}
    accepted = []                                   # this run's saves, for twin checks
    for item in items:
        rec, nid = item["recorder"], item["native_id"]
        if (known.get((rec, nid)) or {}).get("state") in FINAL_STATES:
            continue
        if item["ready_at"] and now - item["ready_at"] < settle:
            m["settling"] += 1                      # let Fathom's copy arrive first
            continue
        if fetched[rec] >= config.CALL_RECORDER_MAX_FETCH:
            m["deferred"] += 1
            continue
        fetched[rec] += 1
        try:
            payload = _fetch(item, fireflies, clickup)
        except PermissionError as exc:              # rule 1: not ours — never read again
            m["skipped"] += 1
            if not dry_run:
                with db.connect(dbp) as conn:
                    _record(conn, rec, nid, title=item["title"], start=item["start"],
                            end=item["end"], state="skipped", note=str(exc))
            continue
        except Exception as exc:                    # noqa: BLE001 — one bad recording
            m["errors"][f"{rec}:{nid}"] = repr(exc)[:160]
            continue
        with db.connect(dbp) as conn:
            if payload is None:
                old = item["ready_at"] and now - item["ready_at"] > give_up
                state = "skipped" if old else "waiting"
                m[state] += 1
                if not dry_run:
                    _record(conn, rec, nid, title=item["title"], start=item["start"],
                            end=item["end"], state=state,
                            note="no transcript on it" + ("" if old else " yet"))
                continue
            iv = interval(payload) or (item["start"], item["end"])
            twin = find_twin(conn, payload, extra=accepted, inbox=inbox)
            if twin:
                m["twins"].append({"recording": payload["recording_id"], "twin_of": twin,
                                   "title": payload.get("title")})
                if not dry_run:
                    _record(conn, rec, nid, title=payload.get("title"), start=iv[0], end=iv[1],
                            state="twin", twin_of=twin, note="the same call is already held")
                continue
            day = (iv[1] or now).strftime("%Y-%m-%d")
            path = inbox / f"{day}__{payload['recording_id']}.json"
            accepted.append(payload)
            m["saved"].append({"recording": payload["recording_id"], "title": payload.get("title"),
                               "day": day})
            if not dry_run:
                inbox.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, indent=1, default=str))
                _record(conn, rec, nid, title=payload.get("title"), start=iv[0], end=iv[1],
                        state="saved", file=str(path))
    return m
