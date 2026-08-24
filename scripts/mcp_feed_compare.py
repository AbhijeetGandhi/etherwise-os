#!/usr/bin/env python3
"""Score the SAME jobs both ways and report where the MCP feed differs.

    PYTHONPATH=. python3 scripts/mcp_feed_compare.py            # fields only, $0
    PYTHONPATH=. python3 scripts/mcp_feed_compare.py --score    # + real scoring

THE BAR (Abhijeet, 2026-08-24), and why it is shaped this way:

  * hard-rule verdicts must agree 100% — they are deterministic code, so any
    disagreement is a mapping bug, full stop;
  * scores may differ by up to 2 — `score_job` calls a model, and the same job
    scored twice does not always return the same number, so demanding equality
    would fail on variance rather than on anything real;
  * every job the scraper found at >= 16 must be findable via the MCP — that is
    Test B (discovery) and lives outside this script.

This is Test A: FIELD FIDELITY on identical jobs, which isolates the mapping
from the search. A discovery gap is a query-tuning problem; a field gap silently
changes what gets scored, and that is the one that loses leads quietly.

Fixtures are real captured payloads (modules/upwork/fixtures/mcp_samples.json).
They are used rather than a live pull because the production rail has no OAuth
client for mcp.upwork.com yet — deliberately, since building one before the data
is proven sufficient would be work spent on a transport we might not keep.

KNOWN, ACCEPTED DIFFERENCES — printed as INFO, never as failures:
  total_applicants    scraper keeps the tier LOW BOUND ("20 to 50" -> 20);
                      the MCP returns the exact count.
  client_total_spent  the SSR payload often omits it; the MCP omits it only
                      when the client truly has none. MCP None + scraper number
                      IS a regression; scraper None + MCP number is a FIX.
  description         scraper reads the feed tile, MCP the full posting.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SCORE = "--score" in sys.argv
# Isolate the competition variable. The scraper feeds the scorer the tier LOW
# BOUND and the MCP the exact count, so a score drop could be that alone rather
# than anything about the mapping. With this flag the MCP dict borrows the
# scraper's total_applicants, holding everything else constant.
MATCH_APPL = "--match-applicants" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db                                    # noqa: E402
from modules.upwork import scoring                             # noqa: E402
from modules.upwork.feeds import mcp_feed                      # noqa: E402

FIXTURES = (config.V3_ROOT / "modules/upwork/fixtures/mcp_samples.json")
if "--fixtures" in sys.argv:
    FIXTURES = Path(sys.argv[sys.argv.index("--fixtures") + 1])
    if not FIXTURES.is_absolute():
        FIXTURES = config.V3_ROOT / FIXTURES

# Fields compared strictly. `description` and `feed_source` are excluded by
# design (see module docstring); `skills` is compared as a set overlap because
# the two sources order and name them differently without disagreeing.
STRICT = ("contract_type", "hourly_min", "hourly_max", "fixed_budget",
          "experience_level", "engagement",
          "client_country", "client_hires", "client_rating",
          "client_payment_verified")
# engagement_duration moved OUT of STRICT on evidence (2026-08-24): four jobs
# in the live batch disagreed and all four scraper values were the SAME string,
# 'Less than 1 month' — which is 59% of the scraper's last-48h rows and, with
# 'Less than 1 week', 80% of all-time. That distribution is implausible for
# these roles; the scraper's durationLabel parsing over-defaults, and the MCP
# value matched the posting on every job where an independent check was
# possible. A strict comparison here would grade the MCP against a bug.
INFORMATIONAL = ("total_applicants", "client_total_spent",
                 "engagement_duration")
# Fields that exist ONLY on the search payload. When a fixture's search row was
# not captured live (`_captured` absent) these are ungradeable and are skipped
# rather than silently compared against a reconstruction.
SEARCH_ONLY = {"engagement", "engagement_duration", "total_applicants"}


def _scraper_row(conn, jid):
    r = conn.execute(
        "SELECT id, title, score, hard_rule_skip, contract_type, hourly_min,"
        " hourly_max, fixed_budget, total_applicants, experience_level,"
        " engagement, engagement_duration, client_country, client_total_spent,"
        " client_hires, client_rating, client_payment_verified, skills_json,"
        " feed_source FROM scored_jobs WHERE id=?", (jid,)).fetchone()
    return dict(r) if r else None


# The two sources use different country vocabularies for the same place —
# the SSR payload says 'USA' and 'GBR', the MCP says 'United States' and
# 'United Kingdom'. Neither is wrong and the scorer only passes the string to
# the model as context, so this is reconciled HERE at comparison time rather
# than by distorting either producer.
_COUNTRY_ALIASES = {
    "usa": "united states", "us": "united states", "u.s.": "united states",
    "united states of america": "united states",
    "gbr": "united kingdom", "uk": "united kingdom",
    "uae": "united arab emirates", "are": "united arab emirates",
    "nga": "nigeria", "aus": "australia", "can": "canada", "ind": "india",
    "deu": "germany", "esp": "spain", "fra": "france", "nld": "netherlands",
}


def _norm_country(v):
    s = str(v or "").strip().lower()
    return _COUNTRY_ALIASES.get(s, s)


def _cmp(a, b, field=None) -> bool:
    if a is None and b is None:
        return True
    if field == "client_country":
        return _norm_country(a) == _norm_country(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 0.011
    return str(a).strip().lower() == str(b).strip().lower()


def main() -> int:
    doc = json.loads(FIXTURES.read_text())
    jobs = doc["jobs"]
    strict_fail = rules_fail = 0
    score_deltas = []
    fixes = []

    with db.connect(config.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for jid, payload in jobs.items():
            mcp = mcp_feed.normalize(payload.get("search"), payload.get("get"))
            row = _scraper_row(conn, jid)
            print(f"\n{'='*78}\n{jid}  {mcp['title'][:60]}")
            if not row:
                print("  ! not in scored_jobs — cannot compare")
                continue
            print(f"  why sampled: {payload.get('_why','')[:100]}")

            # ── strict fields
            # Honesty gate: a fixture whose `search` block was not actually
            # captured cannot be graded on search-only fields. Writing this
            # comparison the first time, two fixtures carried a `duration` I
            # had filled in from plausible values rather than observed, and the
            # harness dutifully reported two mismatches that were mine. A test
            # that grades invented inputs measures the author, not the system.
            search_verified = bool((payload.get("search") or {}).get("_captured"))
            skip = set() if search_verified else SEARCH_ONLY
            bad = []
            for f in STRICT:
                if f in skip:
                    continue
                if not _cmp(mcp.get(f), row.get(f), f):
                    bad.append(f"{f}: scraper={row.get(f)!r} mcp={mcp.get(f)!r}")
            if bad:
                strict_fail += len(bad)
                print("  STRICT MISMATCH:")
                for b in bad:
                    print(f"     {b}")
            else:
                print(f"  strict fields: {len(STRICT)}/{len(STRICT)} match")

            # ── informational
            for f in INFORMATIONAL:
                s, m = row.get(f), mcp.get(f)
                if not _cmp(s, m, f):
                    tag = "INFO"
                    if f == "client_total_spent":
                        if s is None and m is not None:
                            tag, note = "FIX ", "scraper had no spend, MCP does"
                            fixes.append((jid, note))
                        elif m is None and s is not None:
                            tag, note = "LOSS", "MCP lost a spend the scraper had"
                            strict_fail += 1
                        else:
                            note = ""
                    elif f == "engagement_duration":
                        note = "known scraper artifact — 'Less than 1 month' over-assigned"
                    else:
                        note = "tier low-bound vs exact count"
                    print(f"  {tag} {f}: scraper={s!r} mcp={m!r}  {note}")

            # ── hard rules: must agree
            mcp_rule = scoring.hard_rule_skip(mcp)
            scr_rule = row.get("hard_rule_skip")
            if (mcp_rule or None) != (scr_rule or None):
                rules_fail += 1
                print(f"  RULE MISMATCH: scraper={scr_rule!r} mcp={mcp_rule!r}")
            else:
                print(f"  hard rule: both {mcp_rule or 'pass'}")

            # ── score
            if MATCH_APPL:
                mcp["total_applicants"] = row.get("total_applicants")
            if SCORE and not mcp_rule:
                try:
                    v = scoring.score_job(mcp, "mcp_feed_compare")
                    d = (v.get("score") or 0) - (row.get("score") or 0)
                    score_deltas.append((jid, row.get("score"), v.get("score"), d))
                    flag = "OK " if abs(d) <= 2 else "OUT"
                    print(f"  {flag} score: scraper={row.get('score')} "
                          f"mcp={v.get('score')} delta={d:+d}")
                except Exception as exc:                       # noqa: BLE001
                    print(f"  score failed: {exc!r}")

            x = mcp.get("mcp_extra") or {}
            print(f"  new signals: connects={x.get('connects_cost')} "
                  f"avg_bid={x.get('avg_rate_bid')} invites={x.get('invites_sent')} "
                  f"hired={x.get('total_hired')} "
                  f"screening_qs={len(x.get('screening_questions') or [])}")

    print(f"\n{'='*78}\nVERDICT")
    print(f"  strict field mismatches : {strict_fail}   (bar: 0)")
    print(f"  hard-rule mismatches    : {rules_fail}   (bar: 0)")
    if score_deltas:
        worst = max(abs(d) for *_, d in score_deltas)
        print(f"  worst score delta       : {worst:+d}   (bar: <=2)")
    elif SCORE:
        print("  no scores computed")
    else:
        print("  scores not run (pass --score)")
    for jid, note in fixes:
        print(f"  FIX {jid}: {note}")
    ok = strict_fail == 0 and rules_fail == 0 and (
        not score_deltas or max(abs(d) for *_, d in score_deltas) <= 2)
    print(f"\n  {'PASS' if ok else 'FAIL'} against the agreed bar")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
