#!/usr/bin/env python3
"""Load the 8 Oct 2026 proposal research into the funnel stores (migration 038).

    PYTHONPATH=. python3 scripts/proposal_funnel_backfill.py            # dry run: counts only
    PYTHONPATH=. python3 scripts/proposal_funnel_backfill.py --apply

Reads, never fetches. Everything it loads was read from Upwork on 8 Oct 2026
(read-only) and analysed in var/prop/ (notes in var/prop/NOTES.md):

  var/prop/rows.json                     595 proposals: the proposal, its room,
                                         its job record, the derived kind
  var/prop/raw/get/<id>.json             the proposal's own `get` (room_id)
  var/prop/cloud/viewed_ui_chunks/*.json each proposal's page, read in Chrome
                                         16:53-17:50 IST: the open date + the
                                         job's live counts
  var/prop/cloud/blind_slim.json         the two blind readers' call on what
                                         each 2025-26 cold opening is about
  var/prop/cloud/insights_2026-10-08.json  the written findings (first entry)
  var/prop/cloud/invites_monthly.json    invitations not taken, per month

Idempotent: outcomes upsert on the proposal id, views are keyed on
(proposal, read time), the insights entry is inserted once, invitation months
replace. Letter facts are recomputed with modules/upwork/proposal_letter.py and
job tags with modules/upwork/funnel_tags.py, the rules the daily refresh will
use, so a backfilled row and a refreshed row are tagged alike; the dry run
prints any proposal where they disagree with the research coding (8 Oct: none).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from core import config, db
from modules.upwork import funnel_tags, proposal_letter

SOURCE = "research 2026-10-08"
ABOUT_SRC = "blind readers 2026-10-08"
INSIGHTS_AUTHOR = "research 2026-10-08"
# each chunk file was saved when its 100 pages had been read (UTC)
CHUNK_READ_AT = {
    "c000": "2026-10-08T11:34:08Z", "c100": "2026-10-08T11:43:13Z",
    "c200": "2026-10-08T11:51:52Z", "c300": "2026-10-08T12:01:09Z",
    "c400": "2026-10-08T12:11:30Z", "c500": "2026-10-08T12:19:46Z",
}
OUTCOME_COLS = (
    "proposal_id", "sent_dt", "kind", "job_id", "job_title", "job_url", "job_type", "job_level",
    "job_posted_dt", "hours_to_apply", "tool_in_title", "job_category", "bid_amount", "connects_bid",
    "connects_base", "boosted", "boost_outcome", "upwork_status", "status_reason", "room_id",
    "room_created_dt", "replied", "reply_lag_h", "hired", "client_did", "client_company",
    "client_country", "client_spend", "client_hire_rate", "client_jobs", "scanner_score", "letter",
    "letter_words", "contractions", "em_dashes", "has_loom", "questions", "opener_family",
    "first_lines", "opening_about", "opening_about_src", "source")
VIEW_COLS = ("proposal_id", "read_at", "page_state", "viewed_at", "total_proposals",
             "opened_proposals", "shortlisted", "messaged", "avg_bid", "avg_bid_messaged",
             "avg_earnings", "avg_jobs", "avg_years", "avg_jss", "source")


def _num(v):
    if v in (None, "", "None", "nan"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    f = _num(v)
    return None if f is None else int(round(f))


def _flag(v) -> Optional[int]:
    if v is None:
        return None
    return 1 if v is True or str(v).lower() in ("1", "true") else 0


def load_views(chunk_dir: Path) -> dict:
    """{proposal_id: view row} from the browser chunks (pipe-joined strings:
    id|viewedISO|total|opened|short|msg|avgBid|avgBidMsg|avgBidJSS90|avgEarn|
    avgJobs|avgYears|avgJSS|avgJSSmsg|notOpenedText|err)."""
    out = {}
    for f in sorted(chunk_dir.glob("c*.json")):
        read_at = CHUNK_READ_AT.get(f.stem)
        if not read_at:
            raise SystemExit(f"{f}: no read time known for this chunk")
        for s in json.loads(f.read_text()):
            p = s.split("|")
            if len(p) < 16 or p[1] == "pending" or p[0] in out:
                continue
            gone = bool(p[15])
            total = _int(p[2])
            counts_ok = (not gone) and bool(total)        # zeros = Upwork shows no counts
            c = (lambda v: _int(v) if counts_ok else None)
            r = (lambda v: _num(v) if counts_ok else None)
            out[p[0]] = {
                "proposal_id": p[0], "read_at": read_at,
                "page_state": "gone" if gone else ("opened" if p[1] else "not_opened"),
                "viewed_at": p[1] or None,
                "total_proposals": c(p[2]), "opened_proposals": c(p[3]), "shortlisted": c(p[4]),
                "messaged": c(p[5]), "avg_bid": r(p[6]), "avg_bid_messaged": r(p[7]),
                "avg_earnings": r(p[9]), "avg_jobs": r(p[10]), "avg_years": r(p[11]),
                "avg_jss": r(p[12]), "source": "chrome",
            }
    return out


def client_did(r: dict, view: Optional[dict]) -> Optional[str]:
    """What the client did on the job, from the job record (+ the page's
    'messaged' count): the same rule the research report used."""
    if not _flag(r.get("job_have")):
        return None
    if _flag(r.get("hired")):
        return "hired_us"
    if (_num(r.get("job_hired")) or 0) > 0:
        return "hired_other"
    msg = (view or {}).get("messaged") or 0
    if (_num(r.get("job_interviewed")) or 0) > 0 or msg > 0:
        return "interviewed"
    return "nobody"


def build_outcome(r: dict, view: Optional[dict], about: Optional[str],
                  room_id: Optional[str], company: Optional[str]) -> dict:
    letter = r.get("letter") or None
    lf = proposal_letter.letter_features(letter)
    boosted = _flag(r.get("boosted")) or 0
    base = _int(r.get("connects_cost"))
    total = _int(r.get("bid")) if boosted and _int(r.get("bid")) else base
    url = (r.get("url") or "").split("?")[0] or None
    return {
        "proposal_id": str(r["id"]), "sent_dt": r["created"], "kind": r["kind"],
        "job_id": str(r["job_id"]) if r.get("job_id") not in (None, "None") else None,
        "job_title": r.get("title"), "job_url": url,
        "job_type": r.get("j_type") or None, "job_level": r.get("j_exp") or None,
        "job_posted_dt": r.get("posted") if r.get("posted") not in (None, "None") else None,
        "hours_to_apply": _num(r.get("hours_to_apply")),
        "tool_in_title": funnel_tags.tool_in_title(r.get("title")),
        "job_category": funnel_tags.job_category(r.get("title")),
        "bid_amount": _num(r.get("rate")), "connects_bid": total, "connects_base": base,
        "boosted": boosted,
        "boost_outcome": r.get("boost_outcome") if r.get("boost_outcome") not in (None, "None") else None,
        "upwork_status": r.get("status"),
        "status_reason": r.get("reason") if r.get("reason") not in (None, "None") else None,
        "room_id": room_id,
        "room_created_dt": r.get("room_created") if r.get("room_created") not in (None, "None") else None,
        "replied": 1 if _flag(r.get("room")) else 0,
        "reply_lag_h": _num(r.get("reply_lag_h")),
        "hired": 1 if _flag(r.get("hired")) else 0,
        "client_did": client_did(r, view),
        "client_company": company,
        "client_country": r.get("j_country") or None,
        "client_spend": _num(r.get("client_spend")),
        "client_hire_rate": _num(r.get("client_hire_rate")),
        "client_jobs": _int(r.get("client_jobs")),
        "scanner_score": _num(r.get("sj_score")),
        "letter": letter,
        "letter_words": lf["words"], "contractions": lf["contractions"],
        "em_dashes": lf["em_dashes"], "has_loom": lf["has_loom"], "questions": lf["questions"],
        "opener_family": lf["opener_family"], "first_lines": lf["first_lines"],
        "opening_about": about, "opening_about_src": ABOUT_SRC if about else None,
        "source": SOURCE,
    }


def disagreements(r: dict, o: dict) -> list:
    """Where the production rules and the research coding differ."""
    out = []
    pairs = (("letter_words", "words"), ("contractions", "contractions"),
             ("em_dashes", "em_dashes"), ("questions", "questions"),
             ("opener_family", "opener_family"))
    for mine, theirs in pairs:
        a, b = o[mine], r.get(theirs)
        if (a or b) and str(a) != str(b):
            out.append(f"{mine}: {a!r} vs research {b!r}")
    if (o["has_loom"] or 0) != (_flag(r.get("loom")) or 0):
        out.append("has_loom")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--db", type=Path, default=None, help="DB path (default: config.DB_PATH)")
    ap.add_argument("--dir", type=Path, default=config.V3_ROOT / "var" / "prop",
                    help="the research folder (default: var/prop)")
    a = ap.parse_args(argv)
    base = a.dir
    rows = json.loads((base / "rows.json").read_text())
    views = load_views(base / "cloud" / "viewed_ui_chunks")
    blind = {str(x["id"]): x.get("about") for x in
             json.loads((base / "cloud" / "blind_slim.json").read_text())}
    insights = json.loads((base / "cloud" / "insights_2026-10-08.json").read_text())
    invites = json.loads((base / "cloud" / "invites_monthly.json").read_text())

    with db.connect(a.db) as conn:
        companies = {str(r["id"]): r["client_company"] for r in
                     conn.execute("SELECT id, client_company FROM proposals")}

    outcomes, odd = [], []
    for r in rows:
        pid = str(r["id"])
        room_id = None
        g = base / "raw" / "get" / f"{pid}.json"
        if g.exists():
            try:
                room_id = json.loads(g.read_text()).get("room_id") or None
            except ValueError:
                room_id = None
        about = blind.get(pid) if r.get("kind") == "cold" else None
        o = build_outcome(r, views.get(pid), about, room_id, companies.get(pid))
        d = disagreements(r, o)
        if d:
            odd.append((pid, d))
        outcomes.append(o)

    n_view = len(views)
    by_state = {}
    for v in views.values():
        by_state[v["page_state"]] = by_state.get(v["page_state"], 0) + 1
    print(f"outcomes {len(outcomes)} | kinds "
          + str({k: sum(1 for o in outcomes if o['kind'] == k) for k in ('cold', 'invite', 'arranged')})
          + f" | views {n_view} {by_state} | opening judged {sum(1 for o in outcomes if o['opening_about'])}"
          + f" | rooms with id {sum(1 for o in outcomes if o['room_id'])}")
    print(f"letter rules disagree with the research on {len(odd)} proposal(s)")
    for pid, d in odd[:10]:
        print("  ", pid, "; ".join(d))
    if not a.apply:
        print("dry run: nothing written (--apply to write)")
        return 0

    with db.connect(a.db) as conn:
        cols = ", ".join(OUTCOME_COLS)
        marks = ", ".join("?" for _ in OUTCOME_COLS)
        upd = ", ".join(f"{c} = excluded.{c}" for c in OUTCOME_COLS if c != "proposal_id")
        conn.executemany(
            f"INSERT INTO proposal_outcomes ({cols}) VALUES ({marks})"
            f" ON CONFLICT(proposal_id) DO UPDATE SET {upd}, updated_at = datetime('now')",
            [tuple(o[c] for c in OUTCOME_COLS) for o in outcomes])
        vcols = ", ".join(VIEW_COLS)
        vmarks = ", ".join("?" for _ in VIEW_COLS)
        conn.executemany(
            f"INSERT OR IGNORE INTO proposal_views ({vcols}) VALUES ({vmarks})",
            [tuple(v[c] for c in VIEW_COLS) for v in views.values()])
        w = insights.get("window") or {}
        have = conn.execute(
            "SELECT 1 FROM proposal_insights WHERE author = ? AND window_lo IS ? AND window_hi IS ?",
            (INSIGHTS_AUTHOR, w.get("lo"), w.get("hi"))).fetchone()
        if not have:
            conn.execute(
                "INSERT INTO proposal_insights (created_at, window_lo, window_hi, author, model, body_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ("2026-10-08 13:15:00", w.get("lo"), w.get("hi"), INSIGHTS_AUTHOR, None,
                 json.dumps(insights)))
        conn.executemany(
            "INSERT OR REPLACE INTO upwork_invites_monthly (month, not_taken, read_at, source)"
            " VALUES (?, ?, ?, ?)",
            [(m, int(n), invites["read_at"], invites["source"])
             for m, n in sorted(invites["not_taken"].items())])
        n_o = conn.execute("SELECT COUNT(*) FROM proposal_outcomes").fetchone()[0]
        n_v = conn.execute("SELECT COUNT(*) FROM proposal_views").fetchone()[0]
        n_i = conn.execute("SELECT COUNT(*) FROM proposal_insights").fetchone()[0]
    print(f"written: proposal_outcomes {n_o} · proposal_views {n_v} · proposal_insights {n_i}"
          f" · invitation months {len(invites['not_taken'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
