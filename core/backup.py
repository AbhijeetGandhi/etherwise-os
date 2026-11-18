"""Nightly backup of the live database (2026-11-18).

Why this exists
---------------
Abhijeet, 18 Nov: "can you make sure everything is running correctly now."
Everything was — and the one thing nothing was checking is that the database
all of it runs on had no backup. The nightly job (`com.etherwise.backup-db`)
still copies the v2 database, which has not changed since v2 was retired (the
same 25,104,384 bytes every night); the newest copy of THIS database was a
pre-migration snapshot of 9 October, and Time Machine has no destination. The
code is on GitHub. The ledger, his decisions on every card, the audit log and
six months of imported statements were on one disk in one file.

What it does
------------
  1. copies the database with SQLite's online-backup API in a single step from
     a connection that only reads — a consistent snapshot while the rails keep
     writing (WAL: a reader never blocks a writer);
  2. PROVES the copy before keeping it: `PRAGMA quick_check` must say ok and
     the copy must hold as many `transactions` and `runs` rows as the snapshot
     could have (never fewer than the live count at the start). A copy that
     fails is deleted and the run fails loudly — a backup nobody could restore
     is worse than none, because it reads as safety;
  3. gzips it to `var/backups/nightly/etherwise-YYYY-MM-DD.db.gz` (written to a
     temp name and renamed, so a half-written file never has a backup's name);
  4. prunes ITS OWN files only: the newest BACKUP_KEEP_DAILY, plus the first
     backup of each of the newest BACKUP_KEEP_MONTHLY months. The hand-made
     `*.bak-*` snapshots beside it are not its to touch.

Restore: `gunzip -k <file>` and put the result at var/etherwise.db with every
job stopped. The same disk is NOT an off-site copy — that is his to choose
(BUILD_BRIEF, 18 Nov).
"""
from __future__ import annotations

import gzip
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from core import config

NIGHTLY_DIR_NAME = "nightly"
NAME_RE = re.compile(r"^etherwise-(\d{4})-(\d{2})-(\d{2})\.db\.gz$")
PROOF_TABLES = ("transactions", "runs")


def nightly_dir() -> Path:
    return config.BACKUP_DIR / NIGHTLY_DIR_NAME


def _counts(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in PROOF_TABLES}


def snapshot(db_path: Path, dest: Path) -> dict:
    """Copy `db_path` to `dest` (a plain .db) and prove it. Returns the proof;
    raises RuntimeError — after deleting `dest` — when the copy does not hold."""
    # A plain connection that only ever reads. Not `mode=ro`: a WAL database
    # cannot be opened read-only once its -shm file is gone (no reader left),
    # and "unable to open database file" at 03:20 is not a backup.
    src = sqlite3.connect(str(db_path), timeout=60)
    try:
        before = _counts(src)
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)                       # one step: one consistent read
        finally:
            out.close()
    finally:
        src.close()
    # the copy is ours and nobody else has it open: immutable, so checking it
    # leaves no -wal/-shm beside it and cannot change a byte of it
    chk = sqlite3.connect(f"file:{dest}?mode=ro&immutable=1", uri=True)
    try:
        verdict = chk.execute("PRAGMA quick_check").fetchone()[0]
        after = _counts(chk)
    finally:
        chk.close()
    short = {t: (before[t], after[t]) for t in PROOF_TABLES if after[t] < before[t]}
    if verdict != "ok" or short:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"backup copy did not verify: quick_check={verdict!r}, "
                           f"rows short={short}")
    return {"quick_check": verdict, "rows": after, "bytes": dest.stat().st_size}


def keep(names: list, daily: int, monthly: int) -> set:
    """Which backup file names survive: the newest `daily`, plus the first
    backup of each of the newest `monthly` months. Pure."""
    dated = sorted(n for n in names if NAME_RE.match(n))
    kept = set(dated[-daily:]) if daily > 0 else set()
    firsts = {}
    for n in dated:
        firsts.setdefault(n[len("etherwise-"):len("etherwise-") + 7], n)   # 'YYYY-MM' -> first
    for month in sorted(firsts)[-monthly:] if monthly > 0 else []:
        kept.add(firsts[month])
    return kept


def prune(target: Path, daily: int, monthly: int) -> list:
    names = [p.name for p in target.iterdir() if NAME_RE.match(p.name)]
    stay = keep(names, daily, monthly)
    gone = sorted(n for n in names if n not in stay)
    for n in gone:
        (target / n).unlink()
    return gone


def run(db_path: Optional[Path] = None, *, now: Optional[datetime] = None,
        target: Optional[Path] = None) -> dict:
    """One backup: snapshot, prove, gzip, prune. Returns the run's metrics."""
    db_path = Path(db_path or config.DB_PATH)
    now = now or datetime.now(config.TZ)
    target = target or nightly_dir()
    target.mkdir(parents=True, exist_ok=True)
    final = target / f"etherwise-{now.strftime('%Y-%m-%d')}.db.gz"
    raw = target / f".{final.name}.copying.db"
    tmp = target / f".{final.name}.tmp"
    for stale in (raw, tmp):                       # a run that died half-way
        stale.unlink(missing_ok=True)
    try:
        proof = snapshot(db_path, raw)
        with open(raw, "rb") as fin, gzip.open(tmp, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
        tmp.replace(final)
    finally:
        raw.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    pruned = prune(target, config.BACKUP_KEEP_DAILY, config.BACKUP_KEEP_MONTHLY)
    kept = sorted(p.name for p in target.iterdir() if NAME_RE.match(p.name))
    return {"file": str(final), "db_bytes": proof["bytes"], "gz_bytes": final.stat().st_size,
            "quick_check": proof["quick_check"], "rows": proof["rows"],
            "pruned": pruned, "kept": len(kept), "oldest": kept[0] if kept else None}
