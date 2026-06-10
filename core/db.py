"""SQLite connection + migration runner. WAL mode, foreign keys, tight transactions.

Conventions (carried from v2, the parts that worked):
- always `with connect() as conn:` — keep transactions tight
- Claude calls happen OUTSIDE the with-block
- every task is idempotent: lookup-by-canonical-key before insert
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core import config

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(db_path or config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    return {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}


def migrate(db_path: Path | None = None) -> list[str]:
    """Apply pending .sql files in lexical order. Returns versions applied."""
    applied: list[str] = []
    with connect(db_path) as conn:
        done = applied_migrations(conn)
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = sql_file.stem
            if version in done:
                continue
            conn.executescript(sql_file.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            applied.append(version)
    return applied


if __name__ == "__main__":
    print("applied:", migrate() or "nothing pending")
