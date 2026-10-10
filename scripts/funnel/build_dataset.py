"""Read-only: one record per proposal, joining today's Upwork read (var/prop/raw) with what the
ledger knows (rooms, messages, contracts, the scanned job, connects). -> var/prop/dataset.json"""
import glob
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")
from core import config                                    # noqa: E402

c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=30)
c.row_factory = sqlite3.Row
census = json.loads(Path("var/prop/census.json").read_text())
gets = {Path(f).stem: json.loads(Path(f).read_text()) for f in glob.glob("var/prop/raw/get/*.json")}

ledger = {str(r["id"]): dict(r) for r in c.execute("SELECT * FROM proposals")}
threads = {r["id"]: dict(r) for r in c.execute("SELECT * FROM threads")}
thread_by_prop = {str(r["proposal_id"]): r["id"] for r in c.execute(
    "SELECT id, proposal_id FROM threads WHERE proposal_id IS NOT NULL")}
contracts_by_thread, contracts_by_job = {}, {}
for r in c.execute("SELECT * FROM contracts"):
    d = dict(r)
    if d.get("thread_id"):
        contracts_by_thread.setdefault(d["thread_id"], []).append(d)
    if d.get("job_id"):
        contracts_by_job.setdefault(str(d["job_id"]), []).append(d)
connects = {}
for r in c.execute("SELECT job_id, kind, SUM(connects_delta) d, COUNT(*) n, MIN(event_date) first FROM connects_history"
                   " WHERE job_id IS NOT NULL GROUP BY job_id, kind"):
    connects.setdefault(str(r["job_id"]), {})[r["kind"]] = {"delta": r["d"], "n": r["n"], "first": r["first"]}


def iso(v):
    if isinstance(v, dict):
        v = v.get("displayValue") or v.get("rawValue")
    return str(v) if v else None


def msgs(thread_id):
    rows = [dict(r) for r in c.execute(
        "SELECT created_dt, lower(direction) dir, sender_user_name who, message FROM messages"
        " WHERE thread_id=? ORDER BY created_dt, rowid", (thread_id,))]
    inbound = [m for m in rows if m["dir"] == "inbound"]
    outbound = [m for m in rows if m["dir"] == "outbound"]
    first = rows[0] if rows else None
    return {"n": len(rows), "n_inbound": len(inbound), "n_outbound": len(outbound),
            "first_dir": first["dir"] if first else None,
            "first_dt": first["created_dt"] if first else None,
            "first_inbound_dt": inbound[0]["created_dt"] if inbound else None,
            "first_inbound": (inbound[0]["message"] or "")[:600] if inbound else None,
            "first_outbound": (outbound[0]["message"] or "")[:300] if outbound else None,
            "last_dt": rows[-1]["created_dt"] if rows else None}


out = []
for pid, entry in census.items():
    node = entry.get("node") or {}
    g = gets.get(pid) or {}
    vp = ((g.get("data") or {}).get("vendorProposal") or {})
    src = vp or node
    led = ledger.get(pid) or {}
    status = src.get("status") or {}
    terms = src.get("terms") or {}
    rate = (terms.get("chargeRate") or {})
    job = src.get("marketplaceJobPosting") or {}
    job_id = str(job.get("id") or led.get("marketplace_job_id") or "") or None
    thread_id = led.get("thread_id") or thread_by_prop.get(pid)
    rec = {
        "id": pid,
        "created": iso((src.get("auditDetails") or {}).get("createdDateTime")) or led.get("created_dt"),
        "modified": iso((src.get("auditDetails") or {}).get("modifiedDateTime")) or led.get("modified_dt"),
        "status": status.get("status") if isinstance(status, dict) else status,
        "status_label": status.get("status_label") if isinstance(status, dict) else None,
        "reason": ((status.get("reason") or {}).get("reason") if isinstance(status, dict) else None),
        "reason_id": ((status.get("reason") or {}).get("id") if isinstance(status, dict) else None),
        "lists": entry.get("lists") or [],
        "ledger_only": bool(entry.get("ledger_only")),
        "got_detail": bool(vp),
        "boosted": src.get("boosted"),
        "connects_bid": terms.get("connectsBid"),
        "rate": rate.get("rawValue"),
        "duration": (terms.get("estimatedDuration") or {}).get("label"),
        "job_id": job_id,
        "job_title": ((job.get("content") or {}).get("title")) or led.get("job_title"),
        "job_url": job.get("url"),
        "cover_letter": vp.get("proposalCoverLetter") or led.get("cover_letter"),
        "cover_source": "upwork" if vp.get("proposalCoverLetter") else ("ledger" if led.get("cover_letter") else None),
        "qa": [{"q": re.sub(r"</?untrusted_participant_content>", "", (x.get("question") or "")).strip(),
                "a": x.get("answer") or ""} for x in (vp.get("questionsAnswers") or [])],
        "insights": g.get("insights"),
        "insights_available": (g.get("insights_available") if "insights_available" in g else (bool(g.get("insights")) if g else None)),
        "insights_note": g.get("insights_note"),
        "thread_id": thread_id,
        "room_type": (threads.get(thread_id) or {}).get("room_type") if thread_id else None,
        "msgs": msgs(thread_id) if thread_id else None,
        "contracts": [{"id": x["id"], "status": x["upwork_status"], "start": x["start_dt"], "type": x["contract_type"],
                       "rate": x["hourly_rate"], "title": x["title"]}
                      for x in (contracts_by_thread.get(thread_id, []) if thread_id else [])
                      or contracts_by_job.get(job_id or "", [])],
        "ledger": {k: led.get(k) for k in ("status", "upwork_status", "upwork_status_label", "mcp_list_status",
                                           "client_company", "client_country", "client_total_spent")} if led else None,
        "connects": connects.get(job_id or "") or None,
    }
    j = c.execute("SELECT * FROM scored_jobs WHERE id=?", (job_id,)).fetchone() if job_id else None
    if j:
        j = dict(j)
        rec["job"] = {k: j.get(k) for k in (
            "description", "created_dt", "fetched_at", "contract_type", "hourly_min", "hourly_max", "fixed_budget",
            "total_applicants", "experience_level", "engagement", "engagement_duration", "category", "subcategory",
            "skills_json", "client_country", "client_total_spent", "client_hires", "client_rating",
            "client_payment_verified", "score", "icp_tier", "draft_proposal", "draft_word_count", "loom_flag",
            "status", "first_scored_at", "feed_source")}
    else:
        rec["job"] = None
    out.append(rec)

out.sort(key=lambda r: r["created"] or "", reverse=True)
Path("var/prop/dataset.json").write_text(json.dumps(out))
n = len(out)
print("records:", n, "| with detail:", sum(r["got_detail"] for r in out),
      "| with cover letter:", sum(bool(r["cover_letter"]) for r in out),
      "| with room:", sum(bool(r["thread_id"]) for r in out),
      "| with scanned job:", sum(bool(r["job"]) for r in out),
      "| with insights:", sum(bool(r["insights"]) for r in out))
