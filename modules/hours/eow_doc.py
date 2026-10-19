"""The weekly client report as a document — Etherwise-branded, print-first.

WHAT THIS IS FOR (2026-10-19). The first report was a cockpit screen: KPI tiles
and tables in the dark operator UI, plus a markdown blob. His verdict: "it needs
to be super awesome UI wise … proper section for hours which is collapsible …
a summary part … Etherwise branded … super professional … export as pdf".
A client does not read the cockpit. They read a document, so this renders one.

ONE DOCUMENT, TWO OUTPUTS. The same HTML is previewed inside the cockpit and
printed to PDF by headless Chrome (modules/hours/pdf.py). Nothing is styled for
the screen that cannot survive the page: backgrounds are forced to print, the
collapsible workstreams are rendered OPEN for print (a closed <details> prints
nothing, which would make the PDF's hours section silently empty), and table
headers repeat across page breaks.

BRAND. Tokens from repos/etherwise-ops/brand/tokens.css — the palette already
validated for the ops platform, so the report and the platform a client may
also see look like one company: blue-700 #1060C8, sky-400 #3898D8, navy-900
#202860 for headings on light, ink #181818, the warm #F6F4EF canvas. Violet
(#7A5AF8, the validated cat-3) is EDITORIAL per the token notes — used here for
the one thing that asks the client to act, "waiting on your review", which his
own ClickUp export also renders in purple. The wordmark is
assets/brand/etherwise_wordmark.png, inlined so the file is self-contained.

SELF-CONTAINED, NO NETWORK. Inline CSS, inline image, no web font: the PDF is
rendered offline and the preview frame cannot fetch. The type stack falls back
from Figtree (the brand face, when installed) to the system UI face.

EVERY STRING IS ESCAPED. Task names are typed by humans into ClickUp and can
contain anything, including markup.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import re
from functools import lru_cache
from typing import Optional

from core import config

MS_H = 3_600_000
# ClickUp tag colours when a tag has one; otherwise the validated categorical
# palette in its fixed order (tokens.css: "fixed order, never cycled")
_PALETTE = ("#2680D4", "#0E9384", "#7A5AF8", "#C11574", "#B45309", "#1060C8",
            "#3898D8", "#4A5568")


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


@lru_cache(maxsize=1)
def _wordmark() -> str:
    p = config.WORKSPACE_ROOT / "assets" / "brand" / "etherwise_wordmark.png"
    try:
        return "data:image/png;base64," + base64.b64encode(
            p.read_bytes()).decode()
    except OSError:
        return ""


def hm(ms: int) -> str:
    """'16h 05m' — minutes padded so a column of times lines up."""
    if not ms:
        return "—"
    total = int(round(ms / 60_000))
    h, m = divmod(total, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _span(lo: str, hi: str) -> str:
    a, b = dt.date.fromisoformat(lo), dt.date.fromisoformat(hi)
    if a.year == b.year and a.month == b.month:
        return f"{a.day} – {b.day} {b:%b %Y}"
    if a.year == b.year:
        return f"{a.day} {a:%b} – {b.day} {b:%b %Y}"
    return f"{a.day} {a:%b %Y} – {b.day} {b:%b %Y}"


def _as_of(stamp: Optional[str]) -> str:
    try:
        d = dt.datetime.strptime(stamp[:16], "%Y-%m-%d %H:%M")
        return f"{d.day} {d:%b %Y}, {d:%H:%M} IST"
    except (TypeError, ValueError):
        return stamp or "—"


def _color(g: dict, i: int) -> str:
    # the catch-all is grey on purpose: falling through to the palette gave it
    # VIOLET, which on this document means "waiting on your review"
    if g.get("catch_all"):
        return "#A0AEC0"
    c = (g.get("color") or "").strip()
    return c if c.startswith("#") and len(c) in (4, 7) else _PALETTE[i % len(_PALETTE)]


def _pill(status: str, cls: str) -> str:
    # a BLOCKED task is still ours (it is not under "waiting on your review"),
    # but painting it the same calm blue as IN PROGRESS hides that it is stuck
    tone = cls
    if cls == "active" and any(k in (status or "").lower()
                                for k in ("blocked", "on hold", "paused")):
        tone = "hold"
    return f'<span class="pill pill-{e(tone)}">{e((status or "—").upper())}</span>'


# ── sections ─────────────────────────────────────────────────────────────────

def _hero(rep: dict) -> str:
    wm = _wordmark()
    logo = (f'<img class="wordmark" src="{wm}" alt="Etherwise">' if wm
            else '<span class="wordmark-text">ETHERWISE</span>')
    status = "In progress" if rep.get("open") else "Period closed"
    return f"""
<header class="hero">
  <div class="hero-shape" aria-hidden="true"></div>
  <div class="hero-top">
    {logo}
    <span class="eyebrow">Weekly delivery report</span>
  </div>
  <h1>{e(rep['client_name'])}</h1>
  <div class="hero-meta">
    <span><b>{e(rep['period'].split(' (')[0])}</b> · {e(_span(rep['starts_on'], rep['ends_on']))}</span>
    <span class="dot-sep">•</span>
    <span>{e(status)}</span>
    <span class="dot-sep">•</span>
    <span>Prepared {e(dt.datetime.now(config.TZ).strftime('%-d %b %Y'))}</span>
  </div>
</header>"""


def _section(num: str, title: str, body: str, *, sub: str = "",
             cls: str = "") -> str:
    return f"""
<section class="sec {e(cls)}">
  <div class="sec-head">
    <span class="sec-num">{e(num)}</span>
    <h2>{e(title)}</h2>
    {f'<span class="sec-sub">{e(sub)}</span>' if sub else ''}
  </div>
  {body}
</section>"""


def _summary(rep: dict) -> str:
    ws = rep["workstreams"]
    c = rep.get("caught") or {}
    hours = ws["total_ms"] / MS_H
    review_n = len(ws["review"])
    fourth = (("Alerts resolved", str(c.get("resolved", 0)),
               f"of {c.get('total_alerts', 0)} caught")
              if c.get("total_alerts") else
              ("Workstreams", str(len(ws["groups"])),
               f"{len(ws['people'])} people on the team"))
    tiles = [
        ("Hours logged", f"{hours:,.1f}", "h", _span(rep["starts_on"],
                                                     rep["ends_on"]), ""),
        ("Tasks worked", str(ws["task_count"]), "", f"{ws['done']} complete",
         ""),
        ("Needs your review", str(review_n), "",
         "see below" if review_n else "nothing waiting on you",
         "kpi-review" if review_n else ""),
        (fourth[0], fourth[1], "", fourth[2], ""),
    ]
    kpis = "".join(
        f'<div class="kpi {cls}"><div class="kpi-label">{e(lbl)}</div>'
        f'<div class="kpi-value">{e(val)}<span class="kpi-unit">{e(unit)}</span></div>'
        f'<div class="kpi-sub">{e(sub)}</div></div>'
        for lbl, val, unit, sub, cls in tiles)

    groups = [g for g in ws["groups"] if g["ms"] > 0]
    top = groups[:8]
    rest = groups[8:]
    mx = max((g["ms"] for g in top), default=1) or 1
    bars = "".join(
        f'<div class="bar-row"><span class="bar-name"><i style="background:'
        f'{_color(g, i)}"></i>{e(g["name"])}</span>'
        f'<span class="bar-track"><span class="bar-fill" style="width:'
        f'{max(2, round(100 * g["ms"] / mx))}%"></span></span>'
        f'<span class="bar-val">{e(hm(g["ms"]))}</span></div>'
        for i, g in enumerate(top))
    if rest:
        bars += (f'<div class="bar-more">+ {len(rest)} more workstreams ·'
                 f' {e(hm(sum(g["ms"] for g in rest)))}</div>')
    where = (f'<div class="card bars"><div class="card-title">Where the time'
             f' went</div>{bars}</div>') if top and len(ws["groups"]) > 1 else ""

    body = (f'<div class="kpis">{kpis}</div>'
            f'<p class="lede">{e(rep["summary"])}</p>{where}')
    return _section("01", "Summary", body)


def _waiting(rep: dict, n: str) -> str:
    items = rep["workstreams"]["review"]
    extra = [x for x in (rep.get("needs_input") or [])
             if x.get("source") == "commitment"]
    if not items and not extra:
        return ""
    rows = "".join(
        f'<li><div class="w-main"><span class="w-name">{e(x["name"])}</span>'
        f'<span class="chip">{e(x["workstream"])}</span></div>'
        f'<span class="w-time">{e(hm(x["ms"]))}</span></li>' for x in items)
    rows += "".join(
        f'<li><div class="w-main"><span class="w-name">{e(x["what"])}</span>'
        f'<span class="chip">promised to us</span></div></li>' for x in extra)
    body = (f'<div class="callout"><p class="callout-lead">These are built and'
            f' waiting for you to review. Nothing below moves forward until'
            f' they are signed off.</p><ul class="waiting">{rows}</ul></div>')
    return _section(n, "Waiting on your review", body,
                    sub=f"{len(items) + len(extra)} items", cls="sec-review")


def _delivered(rep: dict, n: str) -> str:
    ws = rep["workstreams"]
    cards = []
    for i, g in enumerate(ws["groups"]):
        done = [x for x in g["rows"] if x["cls"] == "done"]
        if not done:
            continue
        shown = done[:6]
        more = len(done) - len(shown)
        li = "".join(f"<li>{e(x['name'])}</li>" for x in shown)
        if more > 0:
            li += f'<li class="more">and {more} more</li>'
        cards.append(
            f'<div class="dcard"><div class="dcard-head"><i style="background:'
            f'{_color(g, i)}"></i>{e(g["name"])}<span>{len(done)}</span></div>'
            f'<ul>{li}</ul></div>')
    if not cards:
        body = '<p class="empty">No tasks were completed in this period.</p>'
    else:
        body = f'<div class="dgrid">{"".join(cards)}</div>'
    return _section(n, "Delivered", body,
                    sub=f"{ws['done']} tasks complete")


def _hours(rep: dict, n: str, *, for_print: bool) -> str:
    ws = rep["workstreams"]
    if not ws["groups"]:
        return _section(n, "Hours by workstream",
                        '<p class="empty">No time was logged in this'
                        ' period.</p>')
    # open on screen when there are few groups; ALWAYS open for print, or the
    # PDF's hours section prints as a list of headings with nothing under them
    open_all = for_print or len(ws["groups"]) <= 2
    blocks = []
    for i, g in enumerate(ws["groups"]):
        rows = "".join(
            f'<tr><td class="t-name">{e(x["name"])}</td>'
            f'<td class="t-team">{e(", ".join(x["team"]) or "—")}</td>'
            f'<td class="t-status">{_pill(x["status"], x["cls"])}</td>'
            f'<td class="t-time">{e(hm(x["ms"]))}</td></tr>'
            for x in g["rows"])
        share = (100 * g["ms"] / ws["total_ms"]) if ws["total_ms"] else 0
        blocks.append(f"""
<details class="ws"{' open' if open_all else ''}>
  <summary>
    <span class="chev" aria-hidden="true"></span>
    <i style="background:{_color(g, i)}"></i>
    <span class="ws-name">{e(g['name'])}</span>
    <span class="ws-count">{g['task_count']} task{'s' if g['task_count'] != 1 else ''}</span>
    <span class="ws-share"><span style="width:{(max(1, round(share)) if g['ms'] else 0)}%"></span></span>
    <span class="ws-total">{e(hm(g['ms']))}</span>
  </summary>
  <table class="tasks">
    <thead><tr><th>Task</th><th>Team</th><th>Status</th><th class="r">Time</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</details>""")
    tools = ("" if for_print else
             '<div class="ws-tools noprint"><button type="button"'
             ' onclick="ewToggle(true)">Expand all</button><button'
             ' type="button" onclick="ewToggle(false)">Collapse all</button>'
             '</div>')
    total = (f'<div class="grand"><span>Total</span><span>'
             f'{e(hm(ws["total_ms"]))}<small>'
             f'{ws["total_ms"] / MS_H:,.2f} h</small></span></div>')
    return _section(n, "Hours by workstream", tools + "".join(blocks) + total,
                    sub=f"{len(ws['groups'])} workstreams · "
                        f"{ws['task_count']} tasks")


def _caught(rep: dict, n: str) -> str:
    c = rep.get("caught") or {}
    if not c.get("scenarios"):
        return ""
    rows = "".join(
        f'<tr><td>{e(x["scenario"])}</td><td class="r">{x["occurrences"]}</td>'
        f'<td class="r">{x["resolved"]}</td></tr>' for x in c["scenarios"][:12])
    more = len(c["scenarios"]) - 12
    if more > 0:
        rows += (f'<tr><td colspan="3" class="muted">and {more} more'
                 f' automations</td></tr>')
    body = (f'<p class="note">Automation alerts that reached us during the'
            f' period, grouped by the automation they came from. Each was'
            f' triaged by the team; most were fixed before they affected'
            f' anyone.</p><table class="tasks compact"><thead><tr>'
            f'<th>Automation</th><th class="r">Alerts</th><th class="r">'
            f'Resolved</th></tr></thead><tbody>{rows}</tbody></table>')
    return _section(n, "Caught before you noticed", body,
                    sub=f"{c['total_alerts']} alerts · {c['resolved']}"
                        f" resolved")


def _next(rep: dict, n: str) -> str:
    act = rep["workstreams"]["active"]
    if not act:
        return ""
    shown = act[:15]
    rows = "".join(
        f'<tr><td class="t-name">{e(x["name"])}</td>'
        f'<td><span class="chip">{e(x["workstream"])}</span></td>'
        f'<td class="t-status">{_pill(x["status"], x["cls"])}</td></tr>'
        for x in shown)
    if len(act) > len(shown):
        rows += (f'<tr><td colspan="3" class="muted">and {len(act) - len(shown)}'
                 f' more in progress</td></tr>')
    body = (f'<table class="tasks"><thead><tr><th>Task</th><th>Workstream</th>'
            f'<th>Status</th></tr></thead><tbody>{rows}</tbody></table>')
    return _section(n, "In progress & up next", body,
                    sub=f"{len(act)} open")


def _footer(rep: dict) -> str:
    wm = _wordmark()
    return f"""
<footer class="foot">
  <div class="foot-brand">{f'<img src="{wm}" alt="Etherwise">' if wm else 'ETHERWISE'}
    <span>Prepared by Etherwise · contact@etherwise.io</span></div>
  <p>Hours count toward a period by the date the work was logged. Figures are
  as of {e(_as_of(rep.get('as_of')))}; time logged after that moment will
  appear in the next version of this report.</p>
</footer>"""


def render(rep: dict, *, for_print: bool = False) -> str:
    """The full standalone HTML document."""
    if rep.get("error"):
        return f"<!doctype html><p>{e(rep['error'])}</p>"
    parts, k = [_summary(rep)], 2
    for fn in (_waiting, _delivered):
        s = fn(rep, f"{k:02d}")
        if s:
            parts.append(s)
            k += 1
    parts.append(_hours(rep, f"{k:02d}", for_print=for_print))
    k += 1
    for fn in (_caught, _next):
        s = fn(rep, f"{k:02d}")
        if s:
            parts.append(s)
            k += 1
    title = f"{rep['client_name']} — {rep['period']} — Etherwise"
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title><style>{_CSS}</style></head>"
            f"<body class='{'print' if for_print else 'screen'}'>"
            f"<div class='topbar'></div><main class='page'>{_hero(rep)}"
            f"<div class='content'>{''.join(parts)}</div>{_footer(rep)}"
            f"</main><script>{_JS}</script></body></html>")


def render_text(rep: dict) -> str:
    """The same report as plain text — "Copy as text" in the Reports tab, for
    a Slack message or an email body.

    Built from the SAME model as the document, section for section. The first
    text version came from the older five-section model (its own "shipped"
    list, its own counts) and would have put one number in the PDF and another
    in the message sent beside it. Plain text, not markdown: Slack and mail
    clients disagree about asterisks, and nobody can misrender a bullet.
    Task-level hours stay in the PDF; a 60-row table is not a message.
    """
    if rep.get("error"):
        return rep["error"]
    ws = rep["workstreams"]
    c = rep.get("caught") or {}
    sprint = rep["period"].split(" (")[0]
    L = [f"{rep['client_name']} — Weekly delivery report",
         f"{sprint} · {_span(rep['starts_on'], rep['ends_on'])} · "
         f"{'In progress' if rep.get('open') else 'Period closed'}", ""]

    L += ["SUMMARY", rep["summary"],
          f"• Hours logged: {ws['total_ms'] / MS_H:,.1f} h",
          f"• Tasks worked: {ws['task_count']} ({ws['done']} complete)",
          f"• Waiting on your review: {len(ws['review'])}"]
    if c.get("total_alerts"):
        L.append(f"• Automation alerts handled: {c['total_alerts']}"
                 f" ({c.get('resolved', 0)} resolved)")

    extra = [x for x in (rep.get("needs_input") or [])
             if x.get("source") == "commitment"]
    if ws["review"] or extra:
        L += ["", "WAITING ON YOUR REVIEW",
              "These are built and waiting for you to review. Nothing below"
              " moves forward until they are signed off."]
        L += [f"• {x['name']} — {x['workstream']}" for x in ws["review"]]
        L += [f"• {x['what']}" for x in extra]

    L += ["", f"DELIVERED ({ws['done']} tasks complete)"]
    any_done = False
    for g in ws["groups"]:
        done = [x for x in g["rows"] if x["cls"] == "done"]
        if not done:
            continue
        any_done = True
        L.append(f"{g['name']}:")
        L += [f"  • {x['name']}" for x in done[:6]]
        if len(done) > 6:
            L.append(f"  … and {len(done) - 6} more")
    if not any_done:
        L.append("No tasks were completed in this period.")

    L += ["", "HOURS BY WORKSTREAM"]
    if ws["groups"]:
        L += [f"• {g['name']} — {hm(g['ms'])} ({g['task_count']} task"
              f"{'s' if g['task_count'] != 1 else ''})" for g in ws["groups"]]
        L.append(f"Total — {hm(ws['total_ms'])} ({ws['total_ms'] / MS_H:,.2f} h)."
                 " Task-level breakdown in the attached PDF.")
    else:
        L.append("No time was logged in this period.")

    if c.get("scenarios"):
        L += ["", "CAUGHT BEFORE YOU NOTICED",
              f"{c['total_alerts']} automation alerts, {c['resolved']} resolved:"]
        L += [f"• {x['scenario']} — {x['occurrences']} alert"
              f"{'s' if x['occurrences'] != 1 else ''}"
              for x in c["scenarios"][:12]]
        if len(c["scenarios"]) > 12:
            L.append(f"… and {len(c['scenarios']) - 12} more automations")

    if ws["active"]:
        L += ["", "IN PROGRESS & UP NEXT"]
        L += [f"• {x['name']} — {x['workstream']}" for x in ws["active"][:15]]
        if len(ws["active"]) > 15:
            L.append(f"… and {len(ws['active']) - 15} more in progress")

    L += ["", "—", "Prepared by Etherwise · contact@etherwise.io",
          f"Hours count toward a period by the date the work was logged."
          f" Figures as of {_as_of(rep.get('as_of'))}."]
    # task names arrive with stray double spaces ("Creating Automations for
    # sending"); HTML collapses them, text does not. Leading indent is kept.
    return "\n".join(re.sub(r"(?<=\S) {2,}", " ", x) for x in L)


_JS = """
function ewToggle(open){document.querySelectorAll('details.ws').forEach(function(d){d.open=open;});}
window.addEventListener('beforeprint',function(){ewToggle(true);});
"""

_CSS = """
:root{
  --blue7:#1060C8;--blue5:#2680D4;--sky:#3898D8;--navy:#202860;--ink:#181818;
  --t2:#4A5568;--t3:#8A94A6;--border:#E5EAF2;--panel:#F6F8FB;--canvas:#F6F4EF;
  --ok:#1E8E5A;--ok-bg:#E8F6EE;--violet:#7A5AF8;--violet-ink:#5B3FD6;
  --violet-bg:#F1EDFE;--info-bg:#EAF2FC;
}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:var(--canvas);color:var(--ink);
  font-family:Figtree,-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",
  "Helvetica Neue",Arial,sans-serif;font-size:13px;line-height:1.55;
  font-feature-settings:"tnum" 1}
.topbar{height:5px;background:linear-gradient(90deg,var(--sky),var(--blue7) 55%,var(--navy))}
.page{max-width:860px;margin:28px auto 48px;background:#fff;border-radius:14px;
  box-shadow:0 1px 2px rgba(16,24,40,.05),0 12px 32px rgba(16,24,40,.08);
  overflow:hidden}

/* hero */
.hero{position:relative;padding:34px 44px 28px;border-bottom:1px solid var(--border);
  overflow:hidden;background:linear-gradient(180deg,#fff,#FBFCFE)}
.hero-shape{position:absolute;top:0;right:0;width:260px;height:100%;
  background:linear-gradient(135deg,var(--sky),var(--blue7));
  clip-path:polygon(62% 0,100% 0,100% 100%,22% 100%);opacity:.95}
.hero-shape::after{content:"";position:absolute;inset:0;
  background:linear-gradient(135deg,rgba(255,255,255,.18),transparent 60%)}
.hero-top{position:relative;display:flex;align-items:center;gap:16px;
  margin-bottom:26px;max-width:calc(100% - 140px)}
.wordmark{height:17px;width:auto;display:block}
.wordmark-text{font-weight:800;letter-spacing:.14em}
.eyebrow{font-size:10.5px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:var(--blue7);padding:4px 10px;border:1px solid #CFE0F6;border-radius:999px;
  background:var(--info-bg)}
.hero h1{position:relative;margin:0 0 8px;font-size:32px;line-height:1.15;
  letter-spacing:-.02em;color:var(--navy);font-weight:750;max-width:calc(100% - 170px)}
.hero-meta{position:relative;display:flex;flex-wrap:wrap;gap:6px 10px;color:var(--t2);
  font-size:12.5px;max-width:calc(100% - 170px)}
.hero-meta b{color:var(--ink);font-weight:650}
.dot-sep{color:#C5CCD8}

/* sections */
.content{padding:6px 44px 8px}
.sec{padding:26px 0 6px;border-bottom:1px solid #EEF1F6}
.sec:last-child{border-bottom:0}
.sec-head{display:flex;align-items:baseline;gap:10px;margin-bottom:16px}
.sec-num{font-size:10.5px;font-weight:700;letter-spacing:.08em;color:var(--blue7);
  background:var(--info-bg);border-radius:6px;padding:2px 7px;line-height:1.6}
.sec h2{margin:0;font-size:17px;letter-spacing:-.01em;color:var(--navy);font-weight:700}
.sec-sub{margin-left:auto;font-size:12px;color:var(--t3)}
.sec-review .sec-num{color:var(--violet-ink);background:var(--violet-bg)}

/* summary */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;break-inside:avoid}
.kpi{border:1px solid var(--border);border-radius:10px;padding:13px 14px 12px;
  background:linear-gradient(180deg,#fff,#FAFBFD)}
.kpi-label{font-size:10.5px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;
  color:var(--t3)}
.kpi-value{font-size:26px;font-weight:750;letter-spacing:-.02em;color:var(--ink);
  margin-top:4px;line-height:1.1}
.kpi-unit{font-size:14px;font-weight:600;color:var(--t2);margin-left:2px}
.kpi-sub{font-size:11.5px;color:var(--t2);margin-top:4px}
.kpi-review{border-color:#D9CFFC;background:linear-gradient(180deg,#fff,var(--violet-bg))}
.kpi-review .kpi-value{color:var(--violet-ink)}
.lede{font-size:14px;line-height:1.65;color:#2D3748;margin:18px 0 18px;max-width:72ch}
.card{border:1px solid var(--border);border-radius:10px;padding:14px 16px;break-inside:avoid}
.card-title{font-size:10.5px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;
  color:var(--t3);margin-bottom:10px}
.bar-row{display:grid;grid-template-columns:minmax(150px,34%) 1fr 72px;gap:12px;
  align-items:center;padding:4px 0}
.bar-name{display:flex;align-items:center;gap:8px;font-weight:550;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.bar-name i,.ws summary i,.dcard-head i{flex:none;width:9px;height:9px;border-radius:3px}
.bar-track{height:9px;background:#EEF2F8;border-radius:999px;overflow:hidden}
.bar-fill{display:block;height:100%;border-radius:999px;
  background:linear-gradient(90deg,var(--sky),var(--blue7))}
.bar-val{text-align:right;font-weight:650;color:var(--ink)}
.bar-more{margin-top:6px;font-size:12px;color:var(--t3)}

/* waiting on review */
/* the callout FLOWS across a page break; only its rows are kept whole. Marked
   break-inside:avoid it was too tall for the space under the summary, so Chrome
   pushed all of it to page 2 and left a third of page 1 blank. */
.callout{border:1px solid #DCD2FD;border-left:4px solid var(--violet);border-radius:10px;
  background:linear-gradient(180deg,#FCFBFF,#F6F3FF);padding:14px 16px}
.waiting li{break-inside:avoid}
.callout-lead{margin:0 0 10px;color:#3D2E8C;font-size:13px}
.waiting{list-style:none;margin:0;padding:0}
.waiting li{display:flex;align-items:center;gap:12px;padding:8px 0;
  border-top:1px solid #E7E0FD}
.waiting li:first-child{border-top:0}
.w-main{flex:1;display:flex;flex-wrap:wrap;align-items:center;gap:8px;min-width:0}
.w-name{font-weight:600}
.w-time{font-weight:650;color:var(--t2);white-space:nowrap}
.chip{display:inline-block;font-size:11px;font-weight:600;color:var(--t2);
  background:var(--panel);border:1px solid var(--border);border-radius:999px;
  padding:1px 8px;white-space:nowrap}

/* delivered */
.dgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.dcard{border:1px solid var(--border);border-radius:10px;padding:12px 14px;break-inside:avoid}
.dcard-head{display:flex;align-items:center;gap:8px;font-weight:650;color:var(--navy);
  margin-bottom:8px}
.dcard-head span{margin-left:auto;font-size:11px;font-weight:700;color:var(--ok);
  background:var(--ok-bg);border-radius:999px;padding:1px 8px}
.dcard ul{margin:0;padding:0;list-style:none}
.dcard li{position:relative;padding:3px 0 3px 18px;color:#2D3748;font-size:12.5px}
.dcard li::before{content:"";position:absolute;left:2px;top:9px;width:8px;height:4px;
  border-left:2px solid var(--ok);border-bottom:2px solid var(--ok);transform:rotate(-45deg)}
.dcard li.more{color:var(--t3);font-style:italic}
.dcard li.more::before{display:none}

/* hours by workstream */
.ws-tools{display:flex;gap:6px;justify-content:flex-end;margin:-6px 0 10px}
.ws-tools button{font:inherit;font-size:11.5px;font-weight:600;color:var(--blue7);
  background:#fff;border:1px solid var(--border);border-radius:7px;padding:4px 10px;cursor:pointer}
.ws-tools button:hover{background:var(--info-bg)}
details.ws{border:1px solid var(--border);border-radius:10px;margin-bottom:8px;
  overflow:hidden;background:#fff}
details.ws summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;
  padding:11px 14px;background:linear-gradient(180deg,#FBFCFE,#F5F7FB);
  break-inside:avoid;break-after:avoid}
details.ws summary::-webkit-details-marker{display:none}
details.ws[open] summary{border-bottom:1px solid var(--border)}
.chev{width:7px;height:7px;border-right:2px solid var(--t3);border-bottom:2px solid var(--t3);
  transform:rotate(-45deg);transition:transform .15s;flex:none;margin-right:2px}
details.ws[open] .chev{transform:rotate(45deg)}
.ws-name{font-weight:650;color:var(--navy)}
.ws-count{font-size:11.5px;color:var(--t3)}
.ws-share{flex:1;max-width:160px;margin-left:auto;height:6px;background:#EEF2F8;
  border-radius:999px;overflow:hidden}
.ws-share span{display:block;height:100%;background:linear-gradient(90deg,var(--sky),var(--blue7))}
.ws-total{font-weight:700;min-width:70px;text-align:right}
table.tasks{width:100%;border-collapse:collapse}
table.tasks thead{display:table-header-group}
table.tasks th{font-size:10.5px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;
  color:var(--t3);text-align:left;padding:8px 14px;background:#fff;border-bottom:1px solid var(--border)}
table.tasks td{padding:8px 14px;border-bottom:1px solid #F0F2F6;vertical-align:top}
table.tasks tr:last-child td{border-bottom:0}
table.tasks tr{break-inside:avoid}
table.tasks .r,.t-time{text-align:right;white-space:nowrap}
.t-name{font-weight:550;color:#1F2937}
.t-team{color:var(--t2);font-size:12px;width:28%}
.t-status{width:150px}
.t-time{font-weight:650;width:80px}
table.compact td,table.compact th{padding:7px 10px}
/* no outer box on the free-standing tables: with border-collapse a rounded
   border renders as a lone right-hand rule in Chrome's print, which looked like
   a layout bug on the last page. Row rules + the header line carry it. */
.sec > table.tasks{border-top:1px solid var(--border)}
.pill{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.05em;
  border-radius:6px;padding:3px 7px;white-space:nowrap}
.pill-done{color:var(--ok);background:var(--ok-bg)}
.pill-review{color:var(--violet-ink);background:var(--violet-bg)}
.pill-active{color:var(--blue7);background:var(--info-bg)}
.pill-hold{color:#B45309;background:#FEF3E2}
.grand{display:flex;justify-content:space-between;align-items:baseline;margin:12px 0 4px;
  padding:12px 16px;border-radius:10px;background:var(--navy);color:#fff;break-inside:avoid}
.grand span:first-child{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:#C7D2FE}
.grand span:last-child{font-size:18px;font-weight:750}
.grand small{font-size:11.5px;font-weight:600;color:#C7D2FE;margin-left:10px}
.note{color:var(--t2);margin:0 0 12px;max-width:72ch}
.muted{color:var(--t3)}
.empty{color:var(--t3);font-style:italic;margin:0 0 12px}

/* footer */
.foot{margin-top:8px;padding:20px 44px 26px;background:var(--panel);border-top:1px solid var(--border)}
.foot-brand{display:flex;align-items:center;gap:14px;color:var(--t2);font-size:12px;font-weight:600}
.foot-brand img{height:12px;width:auto}
.foot p{margin:8px 0 0;font-size:11.5px;color:var(--t3);max-width:80ch}

/* print */
@page{size:A4;margin:12mm 11mm 14mm}
@media print{
  body{background:#fff;font-size:11.5px}
  .topbar{display:none}
  .page{margin:0;box-shadow:none;border-radius:0;max-width:none}
  .hero{padding:22px 26px 20px}
  .content{padding:0 26px}
  .foot{padding:14px 26px 16px}
  .noprint{display:none!important}
  .chev{display:none}
  .sec{break-inside:auto}
  .sec-head{break-after:avoid}
  .callout-lead{break-after:avoid}
  /* a workstream heading must not be stranded at the foot of a page with its
     tasks on the next one */
  details.ws{break-inside:auto}
  details.ws summary{break-after:avoid}
  .dcard,.kpi{break-inside:avoid}
  .kpi-value{font-size:22px}
}
@media (max-width:720px){
  .kpis{grid-template-columns:repeat(2,1fr)}
  .dgrid{grid-template-columns:1fr}
  .hero-shape{width:140px}
  .content,.hero,.foot{padding-left:22px;padding-right:22px}
}
"""
