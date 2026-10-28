"""The LinkedIn tab, Today's LinkedIn block and the growth slice — read-only.

Reads ONLY the local copy (li_records / li_snapshots, migration 045) that
modules/linkedin/sync.py keeps of the Airtable tracker. No network, no model
call, no writes: this is the cockpit's view plane. Spec:
etherwise-v3-linkedin-brief.md.

WHAT IS COUNTED, AND THE TWO RULES IT OBEYS
  `None` is NOT MEASURED, `0` is a real zero — and every zero that could be
  misread carries a note. An empty Messages table is "no DMs sent yet", never
  a 0% reply rate; a day-90 count of 0 says how many days are left.
  Every figure ships with the rows behind it (`rows`), so the tab drills
  without a second route and the drill is the same data the figure came from.

ORDER OF THE PAGE = ORDER OF WHAT MATTERS (social CONTEXT §2): results first
(buyer conversations, the day-90 gate), then pace, then the commenting funnel,
relationships, our posts, the profile. Views and reactions are diagnostics.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core import config

# The People `Stage` options, in the order a relationship moves through them.
STAGES = ("Watching", "Engaged", "Warm", "Ready to DM", "DM sent",
          "In conversation", "Call booked", "Opportunity", "Client",
          "Paused", "Not a fit")
VERDICTS = ("Comment", "Brainstorm", "Revisit", "Posted", "Skipped")
# `Next action` values (the base's own formula, which encodes Aastha's DM rule)
# that ask something of HIM, as opposed to "wait".
ACT_NEXT = ("Ready to DM: reference their post", "Follow up on DM",
            "Keep the conversation going")


# ── plumbing ─────────────────────────────────────────────────────────────────

def _ro(db_path: Optional[Path]) -> sqlite3.Connection:
    """Same WAL-robust read-only open as data._ro (see its docstring)."""
    conn = sqlite3.connect(str(db_path or config.DB_PATH), timeout=10)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _ist_today() -> str:
    return datetime.now(config.TZ).strftime("%Y-%m-%d")


def _load(conn) -> dict:
    """{table_key: [{"id", "created", "f": fields}]} for live rows only."""
    out = {k: [] for k in config.LINKEDIN_TABLES}
    for r in conn.execute(
            "SELECT table_key, record_id, fields_json, created_time FROM"
            " li_records WHERE gone_at IS NULL ORDER BY record_id"):
        try:
            f = json.loads(r["fields_json"] or "{}")
        except ValueError:
            f = {}
        out.setdefault(r["table_key"], []).append(
            {"id": r["record_id"], "created": r["created_time"], "f": f})
    return out


def _synced(conn) -> dict:
    """When the copy was last refreshed — every figure on the tab carries it."""
    row = conn.execute(
        "SELECT MAX(completed_at) FROM runs WHERE task_name='linkedin_sync'"
        " AND status IN ('completed','skipped_empty')").fetchone()
    last = row[0] if row else None
    n = conn.execute("SELECT COUNT(*) FROM li_records").fetchone()[0]
    return {"as_of": last, "ever_synced": bool(n)}


def _ist_day(ts) -> Optional[str]:
    """Airtable dateTime ('2026-10-27T10:54:00.000Z', UTC) -> IST date. A plain
    date passes through."""
    if not ts:
        return None
    s = str(ts)
    if len(s) <= 10:
        return s
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:10]
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(config.TZ).strftime("%Y-%m-%d")


def _ist_stamp(ts) -> Optional[str]:
    if not ts or len(str(ts)) <= 10:
        return ts
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(config.TZ).strftime("%Y-%m-%d %H:%M")


def _days_between(a: Optional[str], b: str) -> Optional[int]:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(str(a)[:10])).days
    except (TypeError, ValueError):
        return None


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


# ── the rows (what every figure drills to) ───────────────────────────────────

def _people_rows(recs: list) -> list:
    out = []
    for r in recs:
        f = r["f"]
        out.append({
            "id": r["id"], "name": f.get("Name"), "title": f.get("Title"),
            "company": f.get("Company"), "segment": f.get("Segment"),
            "role": f.get("Role"), "icp": f.get("ICP fit"),
            "stage": f.get("Stage"), "connection": f.get("Connection"),
            "next_action": f.get("Next action"),
            "comments": f.get("Comments posted") or 0,
            "replied": f.get("Times they replied") or 0,
            "liked": f.get("Times they liked") or 0,
            "messages": f.get("Messages sent") or 0,
            "last_comment": f.get("Last comment"),
            "days_since_touch": f.get("Days since last touch"),
            "last_checked": f.get("Last checked for posts"),
            "added_on": f.get("Added on"),
            "followers": f.get("Followers"),
            "dnc": bool(f.get("Do not contact")),
            "url": f.get("LinkedIn URL"),
        })
    out.sort(key=lambda x: (x["name"] or "").lower())
    return out


def _comment_rows(recs: list, names: dict) -> list:
    out = []
    for r in recs:
        f = r["f"]
        who = [names.get(p, p) for p in (f.get("Person") or [])]
        hrs = _num(f.get("Hours after post"))
        out.append({
            "id": r["id"], "person": ", ".join(who) or f.get("Comment"),
            "day": _ist_day(f.get("Commented at")),
            "at": _ist_stamp(f.get("Commented at")),
            "post_date": f.get("Post date"), "post_type": f.get("Post type"),
            "angle": f.get("Angle"),
            "hours_after_post": int(hrs) if hrs is not None else None,
            "impressions": f.get("Impressions on our comment"),
            "reactions": f.get("Reactions on our comment"),
            "author_liked": bool(f.get("Author liked our comment")),
            "author_replied": bool(f.get("Author replied")),
            "author_reply": f.get("Author reply"),
            "other_replies": f.get("Other replies"),
            "our_comment": f.get("Our comment"),
            "post_summary": f.get("Post summary"),
            "last_checked": f.get("Last checked"),
            "url": f.get("Post URL"),
        })
    out.sort(key=lambda x: x["at"] or "", reverse=True)
    return out


def _candidate_rows(recs: list, today: str) -> list:
    out = []
    for r in recs:
        f = r["f"]
        out.append({
            "id": r["id"], "post": f.get("Post"), "who": f.get("Who"),
            "industry": f.get("Industry"), "verdict": f.get("Verdict"),
            "found_on": f.get("Found on"),
            "days_old": _days_between(f.get("Found on"), today),
            "age_when_found": f.get("Age when found"),
            "engagement": f.get("Engagement when found"),
            "saying": f.get("What they're saying"),
            "draft": f.get("Draft comment"),
            "why": f.get("Why it makes sense"),
            "question": f.get("Question for Abhijeet"),
            "url": f.get("Post URL"),
        })
    out.sort(key=lambda x: (x["found_on"] or "", x["post"] or ""), reverse=True)
    return out


def _message_rows(recs: list, names: dict) -> list:
    out = []
    for r in recs:
        f = r["f"]
        who = [names.get(p, p) for p in (f.get("Person") or [])]
        out.append({
            "id": r["id"], "person": ", ".join(who) or None,
            "person_ids": list(f.get("Person") or []),
            "type": f.get("Type"), "sent_on": f.get("Sent on"),
            "replied": bool(f.get("Replied")), "reply_date": f.get("Reply date"),
            "outcome": f.get("Outcome"),
            "buyer": bool(f.get("Buyer conversation")),
            "became": f.get("Became"), "attribution": f.get("Attribution"),
            "crm_name": f.get("CRM name"),
            "text": f.get("Text"), "reply_summary": f.get("Reply summary"),
            "label": f.get("Message"),
        })
    out.sort(key=lambda x: x["sent_on"] or "", reverse=True)
    return out


def _post_rows(recs: list) -> list:
    out = []
    for r in recs:
        f = r["f"]
        imp = _num(f.get("Impressions"))
        eng = sum(_num(f.get(k)) or 0 for k in ("Reactions", "Comments", "Reposts"))
        out.append({
            "id": r["id"], "post": f.get("Post"),
            "day": _ist_day(f.get("Posted at")), "at": _ist_stamp(f.get("Posted at")),
            "format": f.get("Format"), "pillar": f.get("Pillar"),
            "impressions": f.get("Impressions"),
            "impr_24h": f.get("Impressions after 24h"),
            "impr_7d": f.get("Impressions after 7 days"),
            "reached": f.get("Members reached"),
            "reactions": f.get("Reactions"), "comments": f.get("Comments"),
            "reposts": f.get("Reposts"), "saves": f.get("Saves"),
            "profile_viewers": f.get("Profile viewers"),
            "followers_gained": f.get("Followers gained"),
            "buyer_comments": f.get("Buyer comments"),
            "conversations": f.get("Conversations started"),
            # computed here, not read from the base's formula: the formula
            # comes back as display text ("5.1%") and a figure must be a number
            "engagement_rate": round(100.0 * eng / imp, 1) if imp else None,
            "learned": f.get("What we learned"),
            "updated": _ist_stamp(f.get("Last updated")),
            "url": f.get("Post URL"),
        })
    out.sort(key=lambda x: x["at"] or "", reverse=True)
    return out


def _profile_rows(recs: list) -> list:
    out = []
    for r in recs:
        f = r["f"]
        out.append({
            "id": r["id"], "week": f.get("Week"), "taken_on": f.get("Taken on"),
            "followers": f.get("Followers"), "connections": f.get("Connections"),
            "profile_viewers_90d": f.get("Profile viewers (90 days)"),
            "post_impressions_7d": f.get("Post impressions (7 days)"),
            "search_7d": f.get("Search appearances (7 days)"),
            "ssi": f.get("SSI score"), "notes": f.get("Notes"),
        })
    out.sort(key=lambda x: x["taken_on"] or x["week"] or "")
    return out


# ── the blocks ───────────────────────────────────────────────────────────────

def _results(msgs: list, today: str) -> dict:
    day0 = config.LINKEDIN_DAY0
    day_n = _days_between(day0, today)
    day90 = (date.fromisoformat(day0) + timedelta(days=90)).isoformat()
    buyer = [m for m in msgs if m["buyer"]]
    buyer_people = {pid for m in buyer for pid in (m["person_ids"] or [m["id"]])}
    by_type = {}
    for m in msgs:
        t = m["type"] or "(no type)"
        b = by_type.setdefault(t, {"sent": 0, "replied": 0})
        b["sent"] += 1
        b["replied"] += 1 if m["replied"] else 0
    calls = [m for m in msgs if m["outcome"] == "Call booked"]
    became = {}
    for m in msgs:
        if m["became"]:
            k = m["attribution"] or "unattributed"
            became.setdefault(m["became"], {}).setdefault(k, 0)
            became[m["became"]][k] += 1
    n = len(buyer_people)
    note = None
    if not msgs:
        note = ("no DMs or connection requests sent yet — the warm-up is"
                " still in comments")
    elif n == 0:
        note = "messages sent, none marked as a buyer conversation yet"
    return {
        "buyer_conversations": n, "target": config.LINKEDIN_DAY90_CONVERSATIONS,
        "day0": day0, "day_n": day_n, "day90": day90,
        "days_left": max(0, 90 - day_n) if day_n is not None else None,
        "messages": len(msgs),
        "replied": sum(1 for m in msgs if m["replied"]),
        "by_type": by_type, "calls_booked": len(calls), "became": became,
        "note": note,
        "rows": {"buyer": buyer, "messages": msgs, "calls": calls},
    }


def _pace(comments: list, posts: list, today: str) -> dict:
    d0 = date.fromisoformat(today)
    days = [(d0 - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    per_day = {d: 0 for d in days}
    for c in comments:
        if c["day"] in per_day:
            per_day[c["day"]] += 1
    last7 = days[-7:]
    n7 = sum(per_day[d] for d in last7)
    target = config.LINKEDIN_COMMENTS_PER_DAY
    monday = (d0 - timedelta(days=d0.weekday())).isoformat()
    posts_week = [p for p in posts if p["day"] and p["day"] >= monday]
    weeks = []
    for i in range(3, -1, -1):
        lo = d0 - timedelta(days=d0.weekday() + 7 * i)
        hi = lo + timedelta(days=6)
        weeks.append({"week_of": lo.isoformat(),
                      "posts": sum(1 for p in posts if p["day"] and
                                   lo.isoformat() <= p["day"] <= hi.isoformat())})
    return {
        "comments_today": per_day[today], "comments_target_day": target,
        "comments_7d": n7, "comments_target_7d": target * 7,
        "days_at_target_7d": sum(1 for d in last7 if per_day[d] >= target),
        "by_day": [{"day": d, "n": per_day[d]} for d in days],
        "posts_this_week": len(posts_week),
        "posts_target_week": config.LINKEDIN_POSTS_PER_WEEK,
        "posts_by_week": weeks, "week_of": monday,
        "rows": {"comments_today": [c for c in comments if c["day"] == today],
                 "comments_7d": [c for c in comments if c["day"] in last7],
                 "posts_this_week": posts_week},
    }


def _funnel(cands: list, comments: list) -> dict:
    by_v = {v: 0 for v in VERDICTS}
    for c in cands:
        by_v[c["verdict"] or "(none)"] = by_v.get(c["verdict"] or "(none)", 0) + 1
    replied = [c for c in comments if c["author_replied"]]
    liked = [c for c in comments if c["author_liked"]]
    hrs = [c["hours_after_post"] for c in comments if c["hours_after_post"] is not None]
    imps = [c["impressions"] for c in comments if c["impressions"] is not None]
    early = [c["impressions"] for c in comments if c["impressions"] is not None
             and c["hours_after_post"] is not None and c["hours_after_post"] <= 24]
    late = [c["impressions"] for c in comments if c["impressions"] is not None
            and c["hours_after_post"] is not None and c["hours_after_post"] > 24]
    stale_checks = [c for c in comments if c["last_checked"] is None]
    return {
        "candidates": len(cands), "by_verdict": by_v,
        "commented": len(comments),
        "author_replied": len(replied), "author_liked": len(liked),
        "reply_rate": _pct(len(replied), len(comments)),
        "median_hours_after_post": statistics.median(hrs) if hrs else None,
        "impressions_total": sum(imps) if imps else None,
        "impressions_median": statistics.median(imps) if imps else None,
        "early_vs_late": {
            "within_24h": {"n": len(early),
                           "median_impr": statistics.median(early) if early else None},
            "after_24h": {"n": len(late),
                          "median_impr": statistics.median(late) if late else None},
            # small samples say so rather than read as a rule
            "small_sample": len(early) < 5 or len(late) < 5},
        "liked_note": ("not ticked on any comment yet — either nobody liked one"
                       " or the box is not being filled")
                      if comments and not liked else None,
        "unchecked_comments": len(stale_checks),
        "rows": {"candidates": cands, "comments": comments,
                 "replied": replied, "liked": liked,
                 **{"verdict:" + v: [c for c in cands if (c["verdict"] or "(none)") == v]
                    for v in by_v}},
    }


def _relationships(people: list, today: str) -> dict:
    by_stage = {s: 0 for s in STAGES}
    for p in people:
        by_stage[p["stage"] or "(none)"] = by_stage.get(p["stage"] or "(none)", 0) + 1
    seg, icp, conn, nxt = {}, {}, {}, {}
    for p in people:
        seg[p["segment"] or "(none)"] = seg.get(p["segment"] or "(none)", 0) + 1
        icp[p["icp"] or "(not rated)"] = icp.get(p["icp"] or "(not rated)", 0) + 1
        conn[p["connection"] or "(unknown)"] = conn.get(p["connection"] or "(unknown)", 0) + 1
        nxt[p["next_action"] or "(none)"] = nxt.get(p["next_action"] or "(none)", 0) + 1
    unchecked = [p for p in people if p["stage"] not in ("Not a fit", "Paused")
                 and (p["last_checked"] is None
                      or (_days_between(p["last_checked"], today) or 0) > 7)]
    return {
        "people": len(people), "by_stage": by_stage,
        "by_segment": dict(sorted(seg.items(), key=lambda x: -x[1])),
        "by_icp": icp, "by_connection": conn,
        "by_next_action": dict(sorted(nxt.items(), key=lambda x: -x[1])),
        "act_now": [p for p in people if p["next_action"] in ACT_NEXT
                    and not p["dnc"]],
        "unchecked_7d": len(unchecked),
        "rows": {"people": people, "unchecked": unchecked,
                 **{"stage:" + s: [p for p in people if (p["stage"] or "(none)") == s]
                    for s in by_stage},
                 **{"segment:" + s: [p for p in people if (p["segment"] or "(none)") == s]
                    for s in seg},
                 **{"next:" + s: [p for p in people if (p["next_action"] or "(none)") == s]
                    for s in nxt}},
    }


def _posts_block(posts: list) -> dict:
    imps = [p["impressions"] for p in posts if p["impressions"] is not None]
    return {
        "posts": len(posts),
        "impressions_total": sum(imps) if imps else None,
        "followers_gained": sum(p["followers_gained"] or 0 for p in posts) if posts else None,
        "buyer_comments": sum(p["buyer_comments"] or 0 for p in posts) if posts else None,
        "conversations": sum(p["conversations"] or 0 for p in posts) if posts else None,
        "rows": posts,
    }


def _profile_block(rows: list) -> dict:
    latest = rows[-1] if rows else None
    return {"snapshots": len(rows), "latest": latest, "rows": rows,
            "note": None if rows else
            "no profile snapshot yet — one row a week in Airtable's"
            " 'Profile snapshots' table starts this chart"}


# ── public readers ───────────────────────────────────────────────────────────

def _assemble(conn, today: str) -> dict:
    t = _load(conn)
    names = {r["id"]: r["f"].get("Name") for r in t.get("people", [])}
    people = _people_rows(t.get("people", []))
    comments = _comment_rows(t.get("comments", []), names)
    cands = _candidate_rows(t.get("candidates", []), today)
    msgs = _message_rows(t.get("messages", []), names)
    posts = _post_rows(t.get("posts", []))
    prof = _profile_rows(t.get("profile", []))
    return {"people": people, "comments": comments, "cands": cands,
            "msgs": msgs, "posts": posts, "prof": prof}


def linkedin(db_path: Optional[Path] = None, today: Optional[str] = None) -> dict:
    """GET /api/linkedin — the whole tab. `today` is a TEST clock slot
    (positional, so no URL can reach it)."""
    day = today or _ist_today()
    conn = _ro(db_path)
    try:
        sync = _synced(conn)
        if not sync["ever_synced"]:
            return {"as_of": None, "never_synced": True,
                    "note": "the LinkedIn tracker has not been copied in yet —"
                            " run the sync (Refresh)"}
        a = _assemble(conn, day)
        return {
            "as_of": sync["as_of"], "today": day,
            "base_url": f"https://airtable.com/{config.LINKEDIN_BASE}",
            "results": _results(a["msgs"], day),
            "pace": _pace(a["comments"], a["posts"], day),
            "funnel": _funnel(a["cands"], a["comments"]),
            "relationships": _relationships(a["people"], day),
            "posts": _posts_block(a["posts"]),
            "profile": _profile_block(a["prof"]),
            "needs_you": _needs_you(a, day),
        }
    finally:
        conn.close()


def _needs_you(a: dict, day: str) -> dict:
    fresh = config.LINKEDIN_TODAY_FRESH_DAYS

    def _fresh(rows):
        keep = [r for r in rows if r["days_old"] is not None and r["days_old"] <= fresh]
        return keep, len(rows) - len(keep)

    brainstorm, b_old = _fresh([c for c in a["cands"] if c["verdict"] == "Brainstorm"])
    drafts, d_old = _fresh([c for c in a["cands"] if c["verdict"] == "Comment"])
    people = [p for p in a["people"] if p["next_action"] in ACT_NEXT and not p["dnc"]]
    today_n = sum(1 for c in a["comments"] if c["day"] == day)
    return {
        "brainstorm": brainstorm, "drafts": drafts, "people": people,
        "older": {"brainstorm": b_old, "drafts": d_old},
        "fresh_days": fresh,
        "total": len(brainstorm) + len(drafts) + len(people),
        "comments_today": today_n,
        "comments_target_day": config.LINKEDIN_COMMENTS_PER_DAY,
    }


def needs_you(db_path: Optional[Path] = None, today: Optional[str] = None) -> dict:
    """Today's LinkedIn block: only what asks something of HIM. Derived from
    the tracker's state, so an item clears itself when the tracker changes
    (a verdict moves to Posted) — no card keys, no Done button."""
    day = today or _ist_today()
    conn = _ro(db_path)
    try:
        sync = _synced(conn)
        if not sync["ever_synced"]:
            return {"never_synced": True, "total": 0}
        out = _needs_you(_assemble(conn, day), day)
        out["as_of"] = sync["as_of"]
        return out
    finally:
        conn.close()


def summary(db_path: Optional[Path] = None, today: Optional[str] = None) -> dict:
    """The growth slice of the context pack: the channel's shape in a dozen
    numbers, no rows. Same readers as the tab, so the two cannot disagree."""
    d = linkedin(db_path, today)
    if d.get("never_synced"):
        return {"has_data_surface": False, "note": d.get("note")}
    r, p, f, rel = d["results"], d["pace"], d["funnel"], d["relationships"]
    return {
        "has_data_surface": True, "as_of": d["as_of"],
        "source": "LinkedIn tracker (Airtable applkzewYyLbRmcss) via li_records",
        "day90": {"buyer_conversations": r["buyer_conversations"],
                  "target": r["target"], "day_n": r["day_n"],
                  "days_left": r["days_left"]},
        "messages": {"sent": r["messages"], "replied": r["replied"],
                     "calls_booked": r["calls_booked"], "became": r["became"]},
        "pace": {"comments_7d": p["comments_7d"],
                 "target_7d": p["comments_target_7d"],
                 "posts_this_week": p["posts_this_week"],
                 "posts_target_week": p["posts_target_week"]},
        "funnel": {"candidates": f["candidates"], "by_verdict": f["by_verdict"],
                   "commented": f["commented"],
                   "author_replied": f["author_replied"]},
        "relationships": {"people": rel["people"], "by_stage": rel["by_stage"],
                          "act_now": len(rel["act_now"])},
        "posts": {k: d["posts"][k] for k in ("posts", "impressions_total",
                                              "followers_gained",
                                              "buyer_comments", "conversations")},
    }
