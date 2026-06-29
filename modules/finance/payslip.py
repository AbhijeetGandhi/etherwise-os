"""Salary-slip renderer (Phase 5) — ports payslip-template/payslip_html.py to the
v3 regime: amounts are INTEGER PAISE from the payroll line (no float; the compute
lives in payroll.py). HTML/CSS -> PDF via WeasyPrint (the one sanctioned dep,
scoped to this slip utility; imported lazily so the kernel/tests don't need it).
Brand assets (logo, fonts) come from the approved payslip-template/ dir.
PREP-ONLY: renders a document; moves no money.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from modules.finance import money

TEMPLATE_DIR = Path(__file__).resolve().parents[2].parent / "payslip-template"
INR = "₹"


def fmt(paise):
    """Integer paise -> '₹1,20,000.00' (Indian grouping), via Decimal. No float."""
    if paise is None:
        return ""
    d = money.from_minor(paise)
    neg = d < 0
    whole, _, frac = f"{abs(d):.2f}".partition(".")
    if len(whole) > 3:
        last3, rest, groups = whole[-3:], whole[:-3], []
        while len(rest) > 2:
            groups.insert(0, rest[-2:]); rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        whole = ",".join(groups) + "," + last3
    return f"{'-' if neg else ''}{INR}{whole}.{frac}"


def words(paise):
    num = int(money.from_minor(paise))            # whole rupees; no float
    if num == 0:
        return "Zero Rupees Only"
    o = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
         "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
         "Seventeen", "Eighteen", "Nineteen"]
    t = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    two = lambda n: o[n] if n < 20 else t[n // 10] + (" " + o[n % 10] if n % 10 else "")

    def three(n):
        h, r = divmod(n, 100)
        return " ".join(([o[h] + " Hundred"] if h else []) + ([two(r)] if r else []))
    cr, r = divmod(num, 10000000); la, r = divmod(r, 100000); th, r = divmod(r, 1000)
    parts = []
    if cr: parts.append(two(cr) + " Crore")
    if la: parts.append(two(la) + " Lakh")
    if th: parts.append(two(th) + " Thousand")
    if r:  parts.append(three(r))
    return " ".join(parts).strip() + " Rupees Only"


def _safe(v, d="—"):
    return d if v in (None, "") else str(v)


def mask_acct(a):
    """Privacy: only the last 4 of the bank account on the slip (full number stays
    in Airtable for disbursement, never printed). '97700100011472' -> '••••1472'."""
    a = str(a or "").strip()
    return "••••" + a[-4:] if len(a) >= 4 else "—"


def pdf_password(emp):
    """Payslip PDF lock convention: employee DOB as DDMMYYYY (e.g. 2002-06-27 ->
    27062002). Returns None if DOB absent — caller SURFACES it, never fabricates."""
    dob = str(emp.get("date_of_birth") or "").strip()
    try:
        yy, mm, dd = dob.split("-")
        return f"{dd}{mm}{yy}"
    except ValueError:
        return None


def _font_face():
    f = TEMPLATE_DIR / "fonts"
    def ff(w, s, fn):
        p = f / fn
        return (f"@font-face{{font-family:'Etw';font-weight:{w};font-style:{s};"
                f"src:url('file://{p}');}}") if p.exists() else ""
    return (ff(400, "normal", "DejaVuSans.ttf") + ff(700, "normal", "DejaVuSans-Bold.ttf")
            + ff(400, "italic", "DejaVuSans-Oblique.ttf"))


CSS = """
@page { size: A4; margin: 0; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Etw','DejaVu Sans',sans-serif; color: #0E1223; font-size: 9.7pt; line-height: 1.4; }
.sheet { padding: 14mm 15mm 10mm; }
.header { display: flex; justify-content: space-between; align-items: center; }
.brand img.logo { height: 8.5mm; }
.meta { text-align: right; }
.meta .doc { font-size: 13pt; font-weight: 700; color: #1260CC; letter-spacing: 2px; }
.meta .per { font-size: 10.5pt; color: #0E1223; font-weight: 700; margin-top: 2px; }
.meta .ref { font-size: 8pt; color: #8A94A6; margin-top: 3px; letter-spacing: .4px; }
.rule { height: 3px; margin: 11px 0 0; background: linear-gradient(90deg,#1260CC 0%,#3A9BDC 100%); }
.info { display: flex; margin-top: 15px; gap: 26px; }
.info .col { flex: 1; }
.info h3 { font-size: 7.5pt; letter-spacing: 1.3px; text-transform: uppercase; color: #1260CC;
           font-weight: 700; padding-bottom: 5px; border-bottom: 1px solid #E3E9F2; margin-bottom: 7px; }
.kv { display: flex; justify-content: space-between; padding: 3px 0; font-size: 9.4pt; }
.kv .k { color: #6B7686; } .kv .v { font-weight: 700; text-align: right; max-width: 64%; }
.days { display: flex; margin-top: 15px; border: 1px solid #E3E9F2; border-radius: 3px; overflow: hidden; }
.days .cell { flex: 1; padding: 7px 10px; text-align: center; }
.days .cell + .cell { border-left: 1px solid #E3E9F2; }
.days .cell .n { font-size: 12pt; font-weight: 700; }
.days .cell .l { font-size: 7.5pt; color: #6B7686; text-transform: uppercase; letter-spacing: .8px; margin-top: 1px; }
table.ed { width: 100%; border-collapse: collapse; margin-top: 16px; table-layout: fixed; }
table.ed col.l { width: 30%; } table.ed col.a { width: 20%; }
table.ed th { background: #0E1223; color: #fff; font-size: 8.3pt; letter-spacing: .6px;
              text-transform: uppercase; font-weight: 700; padding: 9px 11px; text-align: left; }
table.ed th.r, table.ed td.r { text-align: right; font-variant-numeric: tabular-nums; }
table.ed th.mid { border-left: 3px solid #fff; }
table.ed td { padding: 7px 11px; font-size: 9.4pt; border-bottom: 1px solid #EEF2F7; }
table.ed td.mid { border-left: 1px solid #E3E9F2; }
table.ed tr.total td { font-weight: 700; background: #EEF7FC; border-top: 2px solid #0E1223; border-bottom: none; }
.net { display: flex; justify-content: space-between; align-items: center; color: #fff;
       background: linear-gradient(90deg,#1260CC 0%,#3A9BDC 100%); padding: 13px 18px; margin-top: 16px; }
.net .lbl { font-size: 11.5pt; font-weight: 700; letter-spacing: 1.6px; }
.net .val { font-size: 16pt; font-weight: 700; font-variant-numeric: tabular-nums; }
.words { font-size: 9.3pt; color: #334155; margin-top: 9px; font-style: italic; }
.recon { font-size: 8.4pt; color: #8A94A6; margin-top: 3px; }
.bottom { display: flex; justify-content: space-between; align-items: flex-end; margin-top: 30px; }
.note { font-size: 8.2pt; color: #8A94A6; max-width: 58%; line-height: 1.5; }
.sign { text-align: center; min-width: 56mm; }
.sign .sp { height: 13mm; } .sign .for { font-size: 8.6pt; color: #6B7686; }
.sign .ln { border-top: 1px solid #9AA4B4; padding-top: 4px; margin-top: 2px; }
.sign .nm { font-size: 9.6pt; font-weight: 700; } .sign .ti { font-size: 8.2pt; color: #6B7686; }
.legal { margin-top: 16px; padding-top: 8px; border-top: 1px solid #E3E9F2; text-align: center;
         font-size: 7.8pt; color: #98A2B3; line-height: 1.5; }
"""


def render_html(line: dict, emp: dict, period: str, pay_date: str, co: dict) -> str:
    """Build the slip HTML from a payroll LINE (integer paise) + employee + company."""
    e = line["earnings"]
    earn = [("Basic", e["basic"]), ("HRA", e["hra"]),
            ("Special Allowance", e["special"]), ("LTA", e["lta"])]
    ded = [("Income Tax (TDS)", line["tds_minor"]), ("Professional Tax (PT)", line["pt_minor"])]
    te, td, net = line["gross_minor"], line["tds_minor"] + line["pt_minor"], line["net_minor"]
    ref = f"{co.get('slip_prefix', 'ETW')}/{period}/{emp.get('employee_id')}"
    pl = datetime.datetime.strptime(period, "%Y-%m").strftime("%B %Y")

    rows = ""
    for i in range(max(len(earn), len(ded))):
        el, ea = earn[i] if i < len(earn) else ("", None)
        dl, da = ded[i] if i < len(ded) else ("", None)
        rows += (f"<tr><td>{el}</td><td class='r'>{fmt(ea)}</td>"
                 f"<td class='mid'>{dl}</td><td class='r'>{fmt(da)}</td></tr>")

    def kv(k, v):
        return f"<div class='kv'><span class='k'>{k}</span><span class='v'>{v}</span></div>"

    left = ("<h3>Employee</h3>" + kv("Name", _safe(emp.get("name")))
            + kv("Employee ID", _safe(emp.get("employee_id")))
            + kv("Designation", _safe(emp.get("designation")))
            + kv("Department", _safe(emp.get("department")))
            + kv("Date of Joining", _safe(emp.get("date_of_joining")))
            + kv("PAN", _safe(emp.get("pan"))))
    right = ("<h3>Pay Details</h3>" + kv("Pay Period", pl) + kv("Pay Date", pay_date)
             + kv("Mode of Payment", co.get("pay_mode", "Bank Transfer"))
             + kv("Bank A/C", mask_acct(emp.get("bank_account"))))   # last 4 only; no IFSC

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_font_face()}{CSS}</style></head><body><div class="sheet">
  <div class="header"><div class="brand"><img class="logo" src="brand/logo_actual.png"></div>
    <div class="meta"><div class="doc">SALARY SLIP</div><div class="per">{pl}</div>
      <div class="ref">Ref: {ref}</div></div></div>
  <div class="rule"></div>
  <div class="info"><div class="col">{left}</div><div class="col">{right}</div></div>
  <table class="ed"><colgroup><col class="l"><col class="a"><col class="l"><col class="a"></colgroup>
    <thead><tr><th>Earnings</th><th class="r">Amount</th>
      <th class="mid">Deductions</th><th class="r">Amount</th></tr></thead>
    <tbody>{rows}
      <tr class="total"><td>Total Earnings</td><td class="r">{fmt(te)}</td>
        <td class="mid">Total Deductions</td><td class="r">{fmt(td)}</td></tr></tbody></table>
  <div class="net"><span class="lbl">NET PAY</span><span class="val">{fmt(net)}</span></div>
  <div class="words"><b>In words:</b> {words(net)}</div>
  <div class="recon">Net Pay = Total Earnings ({fmt(te)}) &minus; Total Deductions ({fmt(td)})</div>
  <div class="bottom"><div class="note">This is a computer-generated salary slip and is valid without
      a physical signature. For any discrepancy, contact {co.get('email','')} within 7 days of issue.</div></div>
  <div class="legal">Generated {datetime.date.today().strftime('%d %B %Y')} &nbsp;·&nbsp; {co.get('email','')}</div>
</div></body></html>"""


def render_pdf(line: dict, emp: dict, period: str, pay_date: str, co: dict, out_path) -> dict:
    """Render the slip to a PDF, then PASSWORD-PROTECT it with the employee's DOB
    (DDMMYYYY) via pypdf. Lazy imports (WeasyPrint + pypdf are scoped payroll deps).
    Returns {path, locked}; locked is False when DOB is absent (surfaced upstream,
    never fabricated)."""
    from weasyprint import HTML
    html = render_html(line, emp, period, pay_date, co)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(out_path))
    pwd = pdf_password(emp)
    if pwd:
        from pypdf import PdfWriter
        w = PdfWriter(clone_from=str(out_path))
        w.encrypt(pwd)
        with open(out_path, "wb") as fh:
            w.write(fh)
    return {"path": str(out_path), "locked": bool(pwd)}
