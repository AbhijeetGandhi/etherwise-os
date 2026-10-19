"""Print the report document to PDF with headless Chrome.

WHY CHROME AND NOT A PDF LIBRARY. The report is one HTML document previewed in
the cockpit; rendering the PDF from that same document through the same engine
is the only way the two are guaranteed to match. A second renderer (reportlab,
WeasyPrint) would be a second layout to keep in step with the first, and the
first time they disagreed the client would get the one nobody looked at.

`--user-data-dir` is a throwaway profile: headless Chrome must not touch, lock
or read Abhijeet's real Chrome profile, and his Chrome is usually open.

Nothing is sent. The PDF is written under var/reports/eow/<client>/ and handed
back to the cockpit for download; mailing it is his decision (drafts-only law).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from core import config

CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
_TIMEOUT_S = 90


class PdfError(Exception):
    pass


def chrome() -> str:
    for p in CHROME_PATHS:
        if Path(p).exists():
            return p
    raise PdfError("no Chrome/Chromium found — cannot render the PDF")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "report"


def out_path(client_id: str, period: str) -> Path:
    d = config.VAR_DIR / "reports" / "eow" / slug(client_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{slug(client_id)}-{slug(period.split(' (')[0])}.pdf"


def render(html_text: str, dest: Path) -> Path:
    """Write `html_text` to a temp file and print it to `dest`."""
    work = Path(tempfile.mkdtemp(prefix="ew-pdf-"))
    try:
        src = work / "report.html"
        src.write_text(html_text, encoding="utf-8")
        tmp_out = work / "report.pdf"
        cmd = [chrome(), "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--hide-scrollbars",
               f"--user-data-dir={work / 'profile'}",
               # both spellings: the flag was renamed across Chrome versions,
               # and a default header/footer stamps a file:// path and a date
               # onto every page of a client document
               "--no-pdf-header-footer", "--print-to-pdf-no-header",
               f"--print-to-pdf={tmp_out}", src.as_uri()]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise PdfError("Chrome took too long to print the report") from exc
        if not tmp_out.exists() or tmp_out.stat().st_size < 1000:
            raise PdfError("Chrome produced no PDF: "
                           + proc.stderr.decode(errors="replace")[-300:])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_out), dest)
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)
