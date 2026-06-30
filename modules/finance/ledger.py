"""Shared ledger writes for Upwork-CSV-sourced rows (Phase 1b/2). One place that
maps a parsed upwork_csv row -> the transactions table, so the importer and the
transfer rebuild never duplicate money-insert logic. USD base (amount_base_minor
== amount_minor, fx_rate '1'); canonical_key 'upwork:<ref>'. No float."""
from __future__ import annotations

import csv as _csv
import io

from modules.finance.importers import upwork_csv


def _profile_for(filename: str) -> str:
    return "Agency" if "agency" in filename.lower() else "Personal"


def load_fullhistory(inbox_dir) -> tuple:
    """Parse every *fullhistory*.csv in inbox_dir -> ({ref: row}, recon[]). Each
    row carries profile + source_file + source_row provenance; only rows with a
    non-blank amount are returned (blank-amount rows quarantine in the importer).
    Each CSV is reconciled (running balance) so callers can refuse a bad file."""
    by_ref, recon = {}, []
    for f in sorted(inbox_dir.glob("*fullhistory*.csv")):
        text = f.read_text()
        rows = upwork_csv.parse(text)
        r = upwork_csv.reconcile_running_balance(rows)
        recon.append({"file": f.name, "rows": len(rows), "ok": r["ok"],
                      "breaks": len(r["breaks"]), "quarantine": len(r["quarantine"])})
        line_of = {}
        for i, raw in enumerate(_csv.DictReader(io.StringIO(text)), start=2):
            ref = (raw.get("Ref ID") or "").strip()
            if ref:
                line_of.setdefault(ref, i)
        prof = _profile_for(f.name)
        for row in rows:
            if row["amount_minor"] is None:
                continue
            row = dict(row)
            row["profile"] = prof
            row["source_file"] = f.name
            row["source_row"] = line_of.get(row["ref_id"])
            by_ref[row["ref_id"]] = row
    return by_ref, recon


_COLS = ("record_id, type, upwork_type, accounting_subtype, description,"
         " currency, amount_minor, amount_credited_minor, amount_base_minor,"
         " fx_rate, fx_date, creation_dt, profile, source, source_file,"
         " source_row, canonical_key, dedup_key")


def _values(row: dict) -> tuple:
    ref = row["ref_id"]
    amt = row["amount_minor"]
    return (ref, row["type"], row.get("type_raw"), row.get("accounting_subtype"),
            row["description"], "USD", amt, amt, amt, "1", row["date"],
            (row["date"] or "") + "T00:00:00+0000", row.get("profile"),
            "Upwork-CSV", row.get("source_file"), row.get("source_row"),
            f"upwork:{ref}", f"upwork-csv:{ref}")


def insert_upwork_csv_row(conn, row: dict) -> None:
    """INSERT one parsed Upwork-CSV row. Caller guarantees the ref is new."""
    qs = ",".join("?" * (_COLS.count(",") + 1))
    conn.execute(f"INSERT INTO transactions ({_COLS}) VALUES ({qs})", _values(row))


def insert_bank_row(conn, row: dict, *, source_file: str) -> None:
    """INSERT one classified IDFC statement row (INR). amount_minor is signed INR
    paise; amount_base_minor/fx_rate/fx_date are the pinned USD presentation
    (set by fx.pin_base before this call). category drives the P&L bucket +
    transfers. record_id == canonical_key (the bank:<hash> line key), matching the
    existing bank-row convention."""
    ck = row["canonical_key"]
    conn.execute(
        "INSERT INTO transactions (record_id, type, accounting_subtype, category,"
        " description, currency, amount_minor, amount_credited_minor,"
        " amount_base_minor, fx_rate, fx_date, creation_dt, source, source_file,"
        " source_row, canonical_key, dedup_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ck, row["category"], row["category"], row["category"], row["description"],
         "INR", row["amount_minor"], row["amount_minor"], row.get("amount_base_minor"),
         row.get("fx_rate"), row.get("fx_date"), row["date"], "IDFC", source_file,
         row.get("source_row"), ck, ck))


def insert_wise_row(conn, row: dict, *, source_file: str) -> None:
    """INSERT one classified Wise row. For the USD account: amount_base_minor ==
    amount_minor, base-rate '1'. For a non-USD sub-account (e.g. AUD): the caller
    pins amount_base_minor (USD) + `_base_rate` (currency->USD) first. fx_rate here
    is the BASE-currency rate; the realized USD/AUD->INR rate is NOT stored (the
    INR-leg tie-out re-derives it from the CSV's Exchange To Amount). kind ->
    category (income|transfer_out|fee); transfer_out is excluded from P&L."""
    wid = row["wise_id"]
    amt = row["amount_minor"]
    base = row.get("amount_base_minor")
    base = amt if base is None else base
    conn.execute(
        "INSERT INTO transactions (record_id, type, accounting_subtype, category,"
        " description, currency, amount_minor, amount_credited_minor,"
        " amount_base_minor, fx_rate, fx_date, creation_dt, source, source_file,"
        " canonical_key, dedup_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (wid, row["kind"], row["kind"], row["kind"], row["description"],
         row.get("currency") or "USD", amt, amt, base, row.get("_base_rate") or "1",
         row.get("date"), row.get("date"), "Wise", source_file,
         f"wise:{wid}", f"wise:{wid}"))


def resolve_missing_amount(conn, canonical: str) -> int:
    """Recovery round-trip (CSV path). The API sync quarantines an amountless txn
    with reason 'upwork txn missing amount (<canonical>); ...' (see
    modules.upwork.sync_state._missing_amount_reason — ONE entry per canonical).
    When the authoritative CSV supplies that ref's amount, mark the pending entry
    imported (no delete — the resolved row is the audit trail). Idempotent: returns
    the number of entries flipped (0 if none pending). Matched on the stable
    '(<canonical>)' marker so it stays valid if the reason's tail text changes.
    This is the SAME resolver the API path uses (sync_state delegates here), so a
    CSV import and a later scoped API sync can't double-resolve or drift."""
    cur = conn.execute(
        "UPDATE quarantine SET resolved_at=datetime('now'), resolution='imported'"
        " WHERE source='upwork' AND resolved_at IS NULL"
        " AND reason LIKE 'upwork txn missing amount (' || ? || ')%'", (canonical,))
    return cur.rowcount


def correct_upwork_csv_row(conn, row: dict) -> None:
    """Re-derive an EXISTING row fully from the authoritative CSV (amount, type,
    description) — used to repair a mis-paired transfer leg. Keyed by record_id."""
    amt = row["amount_minor"]
    conn.execute(
        "UPDATE transactions SET type=?, upwork_type=?, accounting_subtype=?,"
        " description=?, amount_minor=?, amount_credited_minor=?,"
        " amount_base_minor=?, currency='USD', fx_rate='1',"
        " updated_at=datetime('now') WHERE record_id=?",
        (row["type"], row.get("type_raw"), row.get("accounting_subtype"),
         row["description"], amt, amt, amt, row["ref_id"]))
