"""The context pack — one grounded picture of the business, for every agent.

WHY THIS EXISTS. Etherwise runs as several dedicated Cowork chats (ops, upwork,
cofounder, people, …). Each is expert in its own domain and blind outside it,
which is the point — but an agent that cannot see the real numbers gives
GENERIC ADVICE, the exact failure Abhijeet named on 2026-08-26. So every chat
boots by running this: the same shared core, plus its own domain slice.

NO SECOND COMPUTATION. Every figure here comes from `modules.cockpit.data`,
the audited, doctor-gated, test-pinned readers the cockpit itself renders. This
module FORMATS; it does not compute. Before it existed, the cofounder skill
carried its own hand-written revenue SQL in its boot ritual — a second copy of
the P&L, already drifting from the one the Money tab shows. A briefing that
disagrees with the dashboard is worse than no briefing: it teaches him not to
trust either.

HONESTY RULES, inherited from the cockpit:
  * finance RED -> the money block says so and withholds figures, never prints
    a plausible wrong number;
  * a domain with NO DATA SURFACE (growth, today) says exactly that, rather than
    padding with narrative. That absence is the signal — it is how we decide
    whether an agent is ready to exist at all;
  * anything parsed out of a human file (STRATEGY.md bets) is marked as parsed,
    and a parse failure is reported, never silently empty.

READ-ONLY, ALWAYS. This is a briefing tool. It opens the DB through the same
read-only readers the cockpit uses and writes nothing anywhere.

RUN IT ON THE MAC (`bin/context-pack`), never from the Cowork sandbox: SQLite
WAL does not survive the FUSE mount (workspace CLAUDE.md, §non-negotiables).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from core import config

AGENTS = ("ops", "upwork", "cofounder", "people", "growth")

_BET_RE = re.compile(r"^\d+\.\s+\*\*(B\d+)\s*[—-]\s*(.+?)\.?\*\*", re.M)
_STATUS_RE = re.compile(r"\*\*Status:\s*([^*]+?)\*\*")


def _ist_now() -> str:
    return datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M IST")


def _money_block() -> dict:
    """Revenue pace, cash, runway — or an honest RED notice."""
    from modules.cockpit import data
    m = data.money()
    if m.get("blocked"):
        return {"available": False,
                "note": "finance-doctor is RED — figures withheld until the"
                        " ledger reconciles (same gate as the Money tab)",
                "reds": m.get("reds") or []}
    r, p, cash = m.get("revenue") or {}, m.get("pnl") or {}, m.get("cash") or {}
    month_usd = r.get("month_usd") or 0.0
    target = r.get("target_usd") or config.MONTHLY_REVENUE_TARGET_USD
    # pace: this month so far vs the SAME DAY last month — the only honest
    # month-to-date comparison (a full-month figure would flatter every 3rd).
    last_mtd = r.get("last_month_mtd_usd") or 0.0
    return {
        "available": True, "as_of": m.get("as_of"),
        "month": r.get("month"), "month_usd": month_usd,
        "target_usd": target, "gap_to_target_usd": round(target - month_usd, 2),
        "pct_to_target": r.get("pct_to_target"),
        "last_month_same_day_usd": last_mtd,
        "pace_vs_last_month_pct": (round(100.0 * month_usd / last_mtd, 1)
                                   if last_mtd else None),
        "last_month_full_usd": r.get("last_month_usd"),
        "by_month": (r.get("by_month") or [])[-6:],
        "cash_usd": cash.get("value_usd"),
        "runway_months": p.get("runway_months"),
        "monthly_operating_profit_usd": p.get("monthly_operating_profit_usd"),
    }


def _clients_block() -> dict:
    """Client count + concentration. Concentration is the standing risk the
    board memos track (one client was ~40% of revenue and its decay ate a
    year of growth), so it belongs in every agent's core, not just finance's."""
    from modules.cockpit import data
    c = data.clients()
    if c.get("blocked"):
        return {"available": False, "note": c.get("reason") or "finance RED"}
    rows = c.get("clients") or []
    total = c.get("revenue_total_usd") or 0.0
    top = max(rows, key=lambda x: x.get("revenue_usd") or 0.0, default=None)
    return {
        "available": True, "count": c.get("count"),
        "revenue_total_usd": total,
        "top_client": (top or {}).get("name"),
        "top_client_share_pct": (round(100.0 * (top.get("revenue_usd") or 0) / total, 1)
                                 if top and total else None),
        "share_basis": "lifetime attributed revenue",
        "unattributed_usd": (c.get("unlinked") or {}).get("usd"),
    }


def _pipeline_block() -> dict:
    """The acquisition machine, from the Upwork tab's own readers."""
    from modules.cockpit import data
    u, p = data.upwork(), data.pipeline()
    s = u.get("summary") or {}
    ap = p.get("applied") or {}
    wr = p.get("win_rate") or {}
    conn_week = (u.get("connects") or {}).get("week") or {}
    return {
        "hot_this_week": s.get("hot_week"),
        "applied_this_week": ap.get("week"), "applied_last_week": ap.get("last_week"),
        "interviewing_this_week": s.get("interviewing_week"),
        "won_this_week": s.get("won_week"),
        "contracts_started_this_week": s.get("contracts_started_week"),
        "win_rate_pct": wr.get("pct"), "win_rate_basis": wr.get("caveat"),
        "connects_spent_week": conn_week.get("spent_net"),
        "connects_per_application": conn_week.get("per_application"),
        "replies_owed": s.get("replies_owed"),
        "people_waiting": s.get("people_waiting"),
        "quota": "10 applications/week (STRATEGY B1)",
    }


def _health_block() -> dict:
    """Are the rails actually running? One line per unhealthy rail; a healthy
    estate is a COUNT, because a list nobody needs to read gets skimmed."""
    from modules.cockpit import data
    s, u = data.system(), data.upwork()
    checks = (s.get("doctor") or {}).get("checks") or []
    bad = [c for c in checks if c.get("status") not in ("PASS", "SKIP", "N/A")]
    rails = (u.get("rails") or {}).get("items") or []
    attention = [{"rail": r.get("label"), "status": r.get("status"),
                  "age_hours": r.get("age_hours")}
                 for r in rails if r.get("status") not in ("ok", None)]
    return {
        "doctor_worst": (s.get("doctor") or {}).get("worst"),
        "doctor_failing": [{"name": c.get("name"), "status": c.get("status"),
                            "detail": c.get("detail")} for c in bad][:6],
        "rails_total": len(rails), "rails_needing_attention": attention,
        "open_anomalies": len(s.get("anomalies") or []),
        "open_ops_alerts": len(s.get("ops_alerts") or []),
    }


def _strategy_block() -> dict:
    """Bets + the north star, PARSED from business/STRATEGY.md (Abhijeet's file,
    never written here). Parsed, so it is reported as parsed: if the format
    changes, this says so instead of quietly returning no bets — an agent that
    thinks there are no active bets will confidently advise into a vacuum."""
    path = config.WORKSPACE_ROOT / "business" / "STRATEGY.md"
    try:
        text = path.read_text()
    except OSError as exc:                                    # noqa: BLE001
        return {"available": False, "note": f"cannot read {path.name}: {exc}"}
    bets = []
    for m in _BET_RE.finditer(text):
        tail = text[m.end():m.end() + 900]
        st = _STATUS_RE.search(tail)
        bets.append({"id": m.group(1), "name": m.group(2).strip(),
                     "status": (st.group(1).strip() if st else "unstated")})
    log = [ln.strip("- ").strip() for ln in text.splitlines()
           if ln.startswith("- 20")]
    return {"available": True, "bets": bets,
            "bets_parsed": len(bets),
            "parse_warning": None if bets else
            "no bets parsed — STRATEGY.md format may have changed",
            "decision_log_entries": len(log),
            "recent_decisions": log[-5:],
            "target": f"${int(config.MONTHLY_REVENUE_TARGET_USD):,}/mo by 31 Dec 2026"}


# ── domain slices ────────────────────────────────────────────────────────────

def _slice_upwork() -> dict:
    from modules.cockpit import data
    u = data.upwork()
    f = (u.get("funnel") or {}).get("week") or {}
    return {"funnel_week": [{"stage": s.get("label"), "n": s.get("n"),
                             "measured": s.get("measured")}
                            for s in (f.get("stages") or [])],
            "needs_me_cards": len((u.get("needs_me") or {}).get("cards") or []),
            "inbox": u.get("inbox"), "digest": u.get("digest")}


def _slice_cofounder() -> dict:
    """What the board needs that the core does not carry: memo recency (an
    ungraded week is the cofounder's own failure) and the doctrine file."""
    memos = sorted((config.WORKSPACE_ROOT / "business" / "board-memos").glob("*.md")) \
        if (config.WORKSPACE_ROOT / "business" / "board-memos").exists() else []
    last = memos[-1] if memos else None
    days = None
    if last:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", last.name)
        if m:
            days = (datetime.now(config.TZ).date()
                    - datetime.strptime(m.group(1), "%Y-%m-%d").date()).days
    playbook = config.WORKSPACE_ROOT / "business" / "PLAYBOOK.md"
    return {"memo_count": len(memos),
            "last_memo": last.name if last else None,
            "days_since_last_memo": days,
            "playbook_exists": playbook.exists(),
            "playbook_note": None if playbook.exists() else
            "business/PLAYBOOK.md missing — the coach has no written doctrine"
            " to reason from; recommendations will drift toward generic"}


def _slice_people() -> dict:
    """Headcount + payroll from the canonical files, never re-typed. Open roles
    are whatever lives in business/hiring/ — file presence IS the pipeline
    until the People agent builds a better surface."""
    emp_path = config.WORKSPACE_ROOT / "Payroll" / "employees.json"
    people, err = [], None
    try:
        doc = json.loads(emp_path.read_text())
        people = doc if isinstance(doc, list) else (doc.get("employees") or [])
    except (OSError, ValueError) as exc:                       # noqa: BLE001
        err = f"cannot read Payroll/employees.json: {exc}"
    active = [p for p in people
              if str(p.get("status", "active")).lower() in ("active", "")]
    hiring_dir = config.WORKSPACE_ROOT / "business" / "hiring"
    roles = sorted(p.name for p in hiring_dir.glob("*")) if hiring_dir.exists() else []
    out = {"headcount_total": len(people), "headcount_active": len(active),
           "open_role_docs": roles, "error": err}
    from modules.cockpit import data
    m = data.money()
    if not m.get("blocked"):
        out["payroll_lifetime_usd"] = (m.get("buckets") or {}).get("payroll")
    return out


def _slice_growth() -> dict:
    """LinkedIn since 2026-10-28: the engagement tracker's local copy, through
    the SAME reader as the cockpit's LinkedIn tab (modules/cockpit/linkedin.py
    summary), so the pack and the tab cannot disagree. Until the copy exists
    the slice says so — an agent whose slice is empty is an agent whose first
    job is to build its surface, not to advise."""
    try:
        from modules.cockpit import linkedin as _li
        s = _li.summary()
        if s.get("has_data_surface"):
            return {"has_data_surface": True,
                    "channels": {"linkedin": s},
                    "note": "LinkedIn is the only measured channel besides"
                            " Upwork. Goal = suitable paying clients; views and"
                            " reactions are diagnostics (business/social/"
                            "CONTEXT.md §2)."}
    except Exception:  # noqa: BLE001 — fall through to the honest empty answer
        pass
    return {"has_data_surface": False,
            "note": "no non-Upwork lead store, content calendar or channel"
                    " metrics exist yet. Upwork is the only measured channel"
                    " (see pipeline). First job of any growth agent: build the"
                    " surface, then advise off it."}


def _slice_ops() -> dict:
    from modules.cockpit import data
    t = data.today()
    return {"today_cards": len(t.get("clusters") or []),
            "follow_ups": len(t.get("follow_ups") or []),
            "queue_unmapped": t.get("queue_total"),
            "captures": len(t.get("captures") or [])}


_SLICES = {"upwork": _slice_upwork, "cofounder": _slice_cofounder,
           "people": _slice_people, "growth": _slice_growth, "ops": _slice_ops}


def build(agent: Optional[str] = None) -> dict:
    """The shared core, plus one domain slice if `agent` names one."""
    pack = {"generated_at": _ist_now(), "agent": agent or "core",
            "money": _money_block(), "clients": _clients_block(),
            "pipeline": _pipeline_block(), "health": _health_block(),
            "strategy": _strategy_block()}
    if agent in _SLICES:
        pack["slice"] = _SLICES[agent]()
    elif agent:
        pack["slice"] = {"error": f"unknown agent {agent!r};"
                                  f" known: {', '.join(AGENTS)}"}
    return pack


# ── rendering ────────────────────────────────────────────────────────────────

def _usd(v) -> str:
    return "—" if v is None else f"${v:,.0f}"


def render_md(pack: dict) -> str:
    """Markdown for a chat's opening context. Compact ON PURPOSE: this is read
    at the top of every session, so it competes for the same attention the
    actual work needs. Numbers, not narrative."""
    m, c, p = pack["money"], pack["clients"], pack["pipeline"]
    h, s = pack["health"], pack["strategy"]
    L = [f"# Etherwise context pack · {pack['generated_at']}"
         f" · slice: {pack['agent']}", ""]

    L.append("## Money")
    if not m.get("available"):
        L.append(f"- **{m.get('note')}**"
                 + (f" (RED: {', '.join(m.get('reds') or [])})" if m.get("reds") else ""))
    else:
        pace = m.get("pace_vs_last_month_pct")
        L += [f"- **{m['month']}: {_usd(m['month_usd'])}** of {_usd(m['target_usd'])}"
              f" target · gap {_usd(m['gap_to_target_usd'])}"
              + (f" · {pace}% of last month's same-day pace" if pace else ""),
              f"- Last month full: {_usd(m.get('last_month_full_usd'))}"
              f" · cash {_usd(m.get('cash_usd'))}"
              f" · monthly op profit {_usd(m.get('monthly_operating_profit_usd'))}",
              "- Trailing: " + " · ".join(
                  f"{b['month']} {_usd(b['usd'])}" for b in m.get("by_month") or [])]

    L += ["", "## Clients"]
    if c.get("available"):
        L.append(f"- {c['count']} paying · lifetime {_usd(c['revenue_total_usd'])}"
                 f" · top client **{c.get('top_client')}**"
                 f" at {c.get('top_client_share_pct')}% ({c['share_basis']})")
    else:
        L.append(f"- {c.get('note')}")

    L += ["", "## Acquisition (Upwork — the only measured channel)",
          f"- This week: {p.get('hot_this_week')} hot · "
          f"**{p.get('applied_this_week')} applied** (last week"
          f" {p.get('applied_last_week')}) vs quota 10 · "
          f"{p.get('interviewing_this_week')} interviewing · "
          f"{p.get('won_this_week')} won",
          f"- Win rate {p.get('win_rate_pct')}% · connects/application"
          f" {p.get('connects_per_application')} · replies owed"
          f" {p.get('replies_owed')}"]

    L += ["", "## Health",
          f"- doctor: **{h.get('doctor_worst')}** · rails {h.get('rails_total')}"
          f" ({len(h.get('rails_needing_attention') or [])} need attention)"
          f" · anomalies {h.get('open_anomalies')}"
          f" · ops alerts {h.get('open_ops_alerts')}"]
    for r in (h.get("rails_needing_attention") or [])[:5]:
        L.append(f"  - {r['rail']}: {r['status']}")
    for c2 in (h.get("doctor_failing") or [])[:5]:
        L.append(f"  - doctor {c2['status']}: {c2['name']} — {c2['detail']}")

    L += ["", "## Strategy"]
    if s.get("available"):
        L.append(f"- Target: {s['target']}")
        for b in s.get("bets") or []:
            L.append(f"  - **{b['id']}** {b['name']} — _{b['status']}_")
        if s.get("parse_warning"):
            L.append(f"- ⚠ {s['parse_warning']}")
    else:
        L.append(f"- {s.get('note')}")

    if "slice" in pack:
        L += ["", f"## Slice: {pack['agent']}"]
        for k, v in pack["slice"].items():
            if v in (None, [], {}):
                continue
            if isinstance(v, (list, dict)):
                v = json.dumps(v, default=str)
                v = v[:400] + ("…" if len(v) > 400 else "")
            L.append(f"- **{k}**: {v}")
    return "\n".join(L) + "\n"
