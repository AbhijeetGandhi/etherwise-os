"""Tests — "the game" (Upwork brief Phase 2; plan docs/upwork/proposal-game-plan-2026-10-09.md).

Abhijeet, 2026-10-09: "I always want to make this a game ... we see these
metrics, we think there is a potential if we change our proposal writing in
this way ... let's just try it ... and then we see what the results were, if it
worked or not ... and then we document that as well".

The rules he approved the same morning:
  * one change at a time; every other DRAFT gets the new version, so both
    versions meet the same weeks and the same kinds of jobs;
  * a test is called once each side has ~40 proposals AND 7 days have passed
    since the last one (opens and replies take days), at most 6 weeks;
  * the verdict is said in plain words — "clearly better", "a lean", "no
    difference", "too early" — never a p-value (the cockpit's copy rule);
  * every decision goes in the log with what we learned and what to try next.

Pure where it can be, the DB where it must:
  seed_t1(conn)               the first test, as approved (idempotent)
  running(conn, scope)        the test drafts in this scope feed, if any
  arm_for_draft(conn, ...)    which version a new draft gets (alternating)
  record_draft(conn, ...)     the assignment, once a draft exists
  detect_arm(exp_id, letter)  which version a SENT letter looks like
  link_sent(conn, proposals)  proposals -> their assignments, by job id
  scoreboard(exp, rows, ...)  per version: sent, opened, wrote back, hired,
                              connects — and the verdict
  chance_better(...)          the exact chance one rate beats the other

A test must never cost a draft: every caller in the scan path wraps these in
try/except and drafts as before when anything here fails.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Iterable, Optional

from modules.upwork import proposal_letter

T1_ID = "t1-volume-openings"

# "you" in the opening: the thing arm B leaves out of the first two sentences
_SELF = re.compile(
    r"\b(?:i|i['’](?:m|ve|d|ll)|me|my|mine|we|we['’](?:re|ve|d|ll)|our|ours|us|etherwise)\b", re.I)

T1_B_INSTRUCTION = (
    "TEST T1, VERSION B — the opening. For this draft only, the first two"
    " sentences follow this rule, which takes precedence over the reference's"
    " guidance on openings:\n"
    "- Sentences one and two are about the client's situation, in their own"
    " terms: what they are trying to get done and what is in the way, using the"
    " specifics of the job post.\n"
    "- No credentials, no years of experience, no proof points, and no \"I\","
    " \"we\", \"my\", \"our\" or \"Etherwise\" in those two sentences. Proof and"
    " who you are come after.\n"
    "Everything else follows the reference unchanged: voice, length, rate,"
    " no em dashes, no contractions.")

T1 = {
    "id": T1_ID,
    "title": "Aimed volume, and the client's situation first",
    "hypothesis": (
        "Two things, one test. (1) Volume aimed at the jobs that convert — every"
        " 28+ job first, then the best 19-27, applied within 24 hours — brings"
        " more clients without the per-proposal rate collapsing (2026: 4x the"
        " volume brought 8 clients against 6, at a third of the rate per"
        " proposal, because the extra went to crowded, late and low-band jobs)."
        " (2) Letters that open with the client's situation get written back"
        " more often than letters that open with credentials (8 Oct research,"
        " a lean: 54% against 30% once opened; since late August almost every"
        " letter has opened with credentials)."),
    "change": (
        "Every other cold draft opens with the client's situation (version B);"
        " the others are written as today (version A). Volume: about 25 cold"
        " proposals a week, top band first, within 24 hours of the post."),
    "scope": "cold",
    "metric": "replied",
    "min_per_arm": 40, "wait_days": 7, "max_weeks": 6,
    "status": "running",
    "started_at": "2026-10-09",
    "approved_by": "Abhijeet 2026-10-09 (\"Volume + openings\")",
}
T1_ARMS = (
    ("A", "As today",
     "The draft writer as it is: the master reference alone. Since late August"
     " almost every letter has opened with credentials.",
     None, "the first two sentences mention you (I / we / my / our / Etherwise)"),
    ("B", "The client's situation first",
     "The first two sentences describe the client's situation from the post; no"
     " credentials and no I/we until the third sentence. Everything else as today.",
     T1_B_INSTRUCTION, "the first two sentences do not mention you"),
)

METRICS = {
    "opened": "opened",
    "replied": "wrote back",
    "replied_of_opened": "wrote back, of those opened",
    "hired": "hired",
}


# ── seeding and lookup ───────────────────────────────────────────────────────

def seed_t1(conn) -> bool:
    """Insert T1 and its two versions if absent. True when it was inserted."""
    have = conn.execute("SELECT 1 FROM experiments WHERE id=?", (T1_ID,)).fetchone()
    if have:
        return False
    cols = ("id", "title", "hypothesis", "change", "scope", "metric", "min_per_arm",
            "wait_days", "max_weeks", "status", "started_at", "approved_by")
    conn.execute(f"INSERT INTO experiments ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 tuple(T1[c] for c in cols))
    for arm, label, desc, instr, rule in T1_ARMS:
        conn.execute(
            "INSERT INTO experiment_arms (experiment_id, arm, label, description,"
            " draft_instruction, detect_rule) VALUES (?, ?, ?, ?, ?, ?)",
            (T1_ID, arm, label, desc, instr, rule))
    return True


def _has_tables(conn) -> bool:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {"experiments", "experiment_arms", "experiment_assignments"} <= have


def running(conn, scope: str = "cold") -> Optional[dict]:
    """The running test whose scope covers `scope`, with its versions."""
    if not _has_tables(conn):
        return None
    row = conn.execute(
        "SELECT * FROM experiments WHERE status='running' AND scope IN (?, 'all')"
        " ORDER BY started_at, id LIMIT 1", (scope,)).fetchone()
    if not row:
        return None
    exp = dict(row)
    exp["arms"] = {r["arm"]: dict(r) for r in conn.execute(
        "SELECT * FROM experiment_arms WHERE experiment_id=? ORDER BY arm", (exp["id"],))}
    return exp if exp["arms"] else None


def arm_for_draft(conn, job_id: str, kind: str = "cold") -> Optional[dict]:
    """Which version this job's draft gets: the side with fewer drafts so far
    (A on a tie), so the two sides stay level week by week. A job drafted
    before keeps its version. None when no test covers this kind of job."""
    exp = running(conn, kind)
    if not exp:
        return None
    prior = conn.execute(
        "SELECT arm FROM experiment_assignments WHERE experiment_id=? AND job_id=?",
        (exp["id"], str(job_id))).fetchone()
    if prior:
        pick = prior["arm"]
    else:
        counts = {r["arm"]: r["n"] for r in conn.execute(
            "SELECT arm, COUNT(*) n FROM experiment_assignments"
            " WHERE experiment_id=? AND via='draft' GROUP BY arm", (exp["id"],))}
        pick = min(exp["arms"], key=lambda a: (counts.get(a, 0), a))
    a = exp["arms"][pick]
    return {"experiment_id": exp["id"], "arm": pick, "label": a["label"],
            "instruction": a["draft_instruction"]}


def record_draft(conn, experiment_id: str, job_id: str, arm: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO experiment_assignments (experiment_id, job_id, arm, via)"
        " VALUES (?, ?, ?, 'draft')", (experiment_id, str(job_id), arm))


# ── what a sent letter looks like ────────────────────────────────────────────

def detect_arm(experiment_id: str, letter: Optional[str]) -> Optional[str]:
    """Which version a SENT letter looks like, or None when it cannot tell
    (no letter). T1: B when the first two sentences do not mention you."""
    if experiment_id != T1_ID:
        return None
    fl = proposal_letter.first_lines(letter)
    if not fl:
        return None
    return "A" if _SELF.search(fl) else "B"


def link_sent(conn, proposals: Iterable[dict]) -> dict:
    """Tie sent proposals to their test versions, by job id.

    `proposals`: dicts with id, job_id, sent (ISO), kind, letter. A proposal on
    a job drafted under a test gets its proposal id and `sent_arm` (what the
    sent letter looks like). A proposal in a test's scope and window with no
    draft assignment (he wrote it himself, or the draft predates the test) is
    recorded as 'detected' — counted in the volume read, never in the A/B
    comparison, because nobody chose its version by alternation."""
    out = {"linked": 0, "detected": 0, "rewritten": 0}
    if not _has_tables(conn):
        return out
    exps = [dict(r) for r in conn.execute(
        "SELECT * FROM experiments WHERE status IN ('running', 'decided', 'stopped')"
        " AND started_at IS NOT NULL")]
    for p in proposals:
        sent = (p.get("sent") or "")[:10]
        for e in exps:
            if e["scope"] not in (p.get("kind"), "all") or sent < e["started_at"][:10]:
                continue
            if e.get("ended_at") and sent > e["ended_at"][:10]:
                continue
            seen = detect_arm(e["id"], p.get("letter"))
            row = conn.execute(
                "SELECT arm, via, proposal_id FROM experiment_assignments"
                " WHERE experiment_id=? AND job_id=?", (e["id"], str(p.get("job_id")))).fetchone()
            if row:
                if row["proposal_id"] != p["id"]:
                    conn.execute(
                        "UPDATE experiment_assignments SET proposal_id=?, sent_arm=?"
                        " WHERE experiment_id=? AND job_id=?",
                        (p["id"], seen, e["id"], str(p.get("job_id"))))
                    out["linked"] += 1
                    if seen and row["via"] == "draft" and seen != row["arm"]:
                        out["rewritten"] += 1
            elif p.get("job_id") and seen:
                conn.execute(
                    "INSERT OR IGNORE INTO experiment_assignments"
                    " (experiment_id, job_id, arm, via, proposal_id, sent_arm)"
                    " VALUES (?, ?, ?, 'detected', ?, ?)",
                    (e["id"], str(p["job_id"]), seen, p["id"], seen))
                out["detected"] += 1
    return out


# ── the arithmetic of a verdict ──────────────────────────────────────────────

def _lbeta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def chance_better(hits_a: int, n_a: int, hits_b: int, n_b: int) -> Optional[float]:
    """Chance that B's true rate is above A's, from what was seen: the exact
    sum for two Beta(1 + hits, 1 + misses) curves (no simulation, so the same
    numbers always give the same answer). None with nothing on either side."""
    if not n_a or not n_b:
        return None
    aa, ba = 1 + hits_a, 1 + n_a - hits_a
    ab, bb = 1 + hits_b, 1 + n_b - hits_b
    total = 0.0
    for i in range(ab):
        total += math.exp(_lbeta(aa + i, ba + bb) - math.log(bb + i)
                          - _lbeta(1 + i, bb) - _lbeta(aa, ba))
    return max(0.0, min(1.0, total))


def _days(a: Optional[str], b: Optional[str]) -> Optional[int]:
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except (TypeError, ValueError):
        return None


def _hit(r: dict, metric: str) -> Optional[int]:
    seen = 1 if (r.get("op") == 1 or r.get("rep") == 1) else 0
    if metric == "opened":
        return seen
    if metric == "replied":
        return 1 if r.get("rep") == 1 else 0
    if metric == "replied_of_opened":
        return (1 if r.get("rep") == 1 else 0) if seen else None
    if metric == "hired":
        return 1 if r.get("hired") == 1 else 0
    return None


def scoreboard(exp: dict, rows_by_arm: dict, today: str) -> dict:
    """Per version: the counts, the deciding rate, and the verdict in words.

    `rows_by_arm`: {"A": [fact rows], "B": [...]} — only proposals whose
    version was CHOSEN by alternation (via 'draft'); detected ones are
    reported beside, never mixed in."""
    metric = exp["metric"]
    arms = {}
    for arm, rows in sorted(rows_by_arm.items()):
        hits = [h for h in (_hit(r, metric) for r in rows) if h is not None]
        arms[arm] = {
            "sent": len(rows),
            "opened": sum(1 for r in rows if r.get("op") == 1 or r.get("rep") == 1),
            "replied": sum(1 for r in rows if r.get("rep") == 1),
            "hired": sum(1 for r in rows if r.get("hired") == 1),
            "n": len(hits), "hits": sum(hits),
            "rate": (sum(hits) / len(hits)) if hits else None,
            "rewritten": sum(1 for r in rows if r.get("_rewritten")),
        }
    a, b = arms.get("A"), arms.get("B")
    p = chance_better(a["hits"], a["n"], b["hits"], b["n"]) if a and b else None
    last = max((r.get("sent") or "" for rows in rows_by_arm.values() for r in rows), default="")
    weeks = (_days(exp.get("started_at"), today) or 0) / 7
    need = exp["min_per_arm"]
    smallest = min((x["sent"] for x in arms.values()), default=0)
    waited = _days(last, today) if last else None
    full = smallest >= need and waited is not None and waited >= exp["wait_days"]
    timed_out = weeks >= exp["max_weeks"]
    broken = [arm for arm, x in arms.items() if x["sent"] >= 15 and x["opened"] == 0]
    if broken:
        words, tone = (f"version {broken[0]} looks broken: no opens in its first {arms[broken[0]]['sent']}",
                       "bad")
    elif not (full or timed_out) or p is None:
        left = max(0, need - smallest)
        words = (f"too early: {smallest} of {need} on the smaller side"
                 + (f", {left} to go" if left else "")
                 + ("" if waited is None or waited >= exp["wait_days"] or left
                    else f", {exp['wait_days'] - waited} days of waiting left"))
        tone = "info"
    elif p >= 0.95:
        words, tone = "B is clearly better", "good"
    elif p <= 0.05:
        words, tone = "A is clearly better", "good"
    elif p >= 0.80:
        words, tone = "a lean towards B", "info"
    elif p <= 0.20:
        words, tone = "a lean towards A", "info"
    else:
        words, tone = "no difference", "neutral"
    return {"metric": metric, "metric_words": METRICS.get(metric, metric), "arms": arms,
            "chance_b_better": None if p is None else round(p, 3),
            "verdict": words, "tone": tone, "ready": bool(full or timed_out),
            "timed_out": timed_out, "weeks": round(weeks, 1), "last_sent": last or None,
            "waited_days": waited}


def assignments(conn) -> list:
    if not _has_tables(conn):
        return []
    return [dict(r) for r in conn.execute(
        "SELECT * FROM experiment_assignments ORDER BY assigned_at")]


def tests(conn) -> list:
    """Every test with its versions — the log, newest first."""
    if not _has_tables(conn):
        return []
    out = []
    for r in conn.execute("SELECT * FROM experiments ORDER BY COALESCE(started_at, created_at) DESC"):
        e = dict(r)
        e["arms"] = [dict(a) for a in conn.execute(
            "SELECT arm, label, description, draft_instruction IS NOT NULL AS has_instruction,"
            " detect_rule FROM experiment_arms WHERE experiment_id=? ORDER BY arm", (e["id"],))]
        out.append(e)
    return out


def prov_note(test: Optional[dict]) -> Optional[dict]:
    """What goes in draft_evidence_json about the test, for the card and audits."""
    if not test:
        return None
    return {"id": test["experiment_id"], "arm": test["arm"], "label": test["label"]}


__all__ = ["T1_ID", "seed_t1", "running", "arm_for_draft", "record_draft", "detect_arm",
           "link_sent", "chance_better", "scoreboard", "assignments", "tests", "prov_note",
           "METRICS", "json"]
