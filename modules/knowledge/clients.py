"""Client roster for classification + CRM matching. The clients table (Airtable
mirror) is empty until M1b lands, so the pilot roster is defined here. Each
entry's `identifiers` drive BOTH Fathom classification (participant emails/
names, title) and CRM revenue matching (txn description/company) — include
domain forms AND any accented/legal-name variants seen in the data.
"""
from __future__ import annotations

from typing import List

from core import db

PILOT_CLIENTS: List[dict] = [
    {"client_id": "horizons-aba", "name": "Horizons ABA",
     "identifiers": ["horizonsaba.com", "horizons aba", "horizons"]},
    {"client_id": "adelye", "name": "Adelye",
     # Adélye appears accented in CRM (assignment_company "Adélye")
     "identifiers": ["adelye.com", "adélye", "adelye"]},
    {"client_id": "endura", "name": "Endura Solutions",
     "identifiers": ["endurasolutions.com.au", "endura solutions", "endura"]},
]


def load_client_map(db_path=None) -> List[dict]:
    """Return the active client roster. Prefers the clients mirror once
    populated; falls back to the pilot roster (clients table empty pre-M1b)."""
    try:
        with db.connect(db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM clients")] if _has_clients(conn) else []
    except Exception:
        rows = []
    if not rows:
        return PILOT_CLIENTS
    return PILOT_CLIENTS  # mirror-derived map TBD at M1b; pilots remain source


def _has_clients(conn) -> bool:
    n = conn.execute("SELECT COUNT(*) c FROM clients").fetchone()["c"]
    return n > 0
