"""Payroll compute + reconcile (Phase 5) — PREP-ONLY, never disburses.

Computes a monthly run from the employees roster. Gross is split basic/hra/
special/lta in INTEGER PAISE (money.pct); `special` is the RESIDUAL (gross −
basic − hra − lta) so Σcomponents == gross exactly, no rounding drift. TDS and PT
are INPUTS pulled from the roster — NEVER derived from tax slabs (compliance-
sensitive; a missing figure is SURFACED, never guessed/computed).

Reconciliation (doctor #6): per employee Σcomponents == gross and gross −
(TDS+PT) == net; per run Σnet == run net total (+ gross/tds/pt totals).
No float anywhere.
"""
from __future__ import annotations

import calendar
import datetime
from typing import List, Optional

from modules.finance import money

# RazorpayX-default split (employees.json salary_split mirrors this). 'special' is
# computed as the residual, so only basic/hra/lta percentages are applied.
BASIC_PCT, HRA_PCT, LTA_PCT = 50, 25, 10


def last_working_day(year: int, month: int) -> str:
    """Last working day of the month (skip Sat/Sun) — the payroll run date."""
    d = datetime.date(year, month, calendar.monthrange(year, month)[1])
    while d.weekday() >= 5:                       # 5=Sat, 6=Sun
        d -= datetime.timedelta(days=1)
    return d.isoformat()


def compute_employee(emp: dict) -> dict:
    """One employee's line in integer paise, OR {'missing': [...]} when a required
    INPUT (TDS/PT) is absent — surfaced, never computed."""
    gross = money.to_minor(str(emp["gross_salary"]))
    basic = money.pct(gross, BASIC_PCT)
    hra = money.pct(gross, HRA_PCT)
    lta = money.pct(gross, LTA_PCT)
    special = gross - basic - hra - lta           # residual (== 15% + rounding)
    tds, pt = emp.get("monthly_tds"), emp.get("monthly_pt")
    missing = [k for k, v in (("monthly_tds", tds), ("monthly_pt", pt)) if v is None]
    if missing:
        return {"employee_id": emp.get("employee_id"), "name": emp.get("name"),
                "missing": missing}
    tds_m, pt_m = money.to_minor(str(tds)), money.to_minor(str(pt))
    earnings = {"basic": basic, "hra": hra, "special": special, "lta": lta}
    return {"employee_id": emp.get("employee_id"), "name": emp.get("name"),
            "earnings": earnings, "gross_minor": sum(earnings.values()),
            "tds_minor": tds_m, "pt_minor": pt_m,
            "net_minor": sum(earnings.values()) - tds_m - pt_m}


def reconcile_line(line: dict) -> Optional[str]:
    """#6 per-employee: Σcomponents == gross AND gross − (TDS+PT) == net."""
    if sum(line["earnings"].values()) != line["gross_minor"]:
        return "Σcomponents != gross"
    if line["gross_minor"] - line["tds_minor"] - line["pt_minor"] != line["net_minor"]:
        return "gross − (TDS+PT) != net"
    return None


def compute_run(employees: List[dict], period: str) -> dict:
    """Compute the run for active employees in `period` (YYYY-MM). Surfaces any
    employee with a missing INPUT; the run is NOT finalizable while `missing` is
    non-empty. Pay date = last working day. PREP-ONLY."""
    y, m = (int(x) for x in period.split("-"))
    active = [e for e in employees if e.get("status") == "Active"]
    lines, missing, recon_errors = [], [], []
    for e in active:
        line = compute_employee(e)
        if "missing" in line:
            missing.append(line)
            continue
        err = reconcile_line(line)
        if err:
            recon_errors.append({"employee_id": line["employee_id"], "error": err})
        lines.append(line)
    return {
        "period": period, "pay_date": last_working_day(y, m),
        "headcount": len(lines), "lines": lines, "missing": missing,
        "recon_errors": recon_errors,
        "gross_minor": sum(l["gross_minor"] for l in lines),
        "tds_minor": sum(l["tds_minor"] for l in lines),
        "pt_minor": sum(l["pt_minor"] for l in lines),
        "net_minor": sum(l["net_minor"] for l in lines),
        # finalizable only when every input is present AND every line reconciles
        "finalizable": not missing and not recon_errors,
    }
