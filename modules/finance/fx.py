"""FX pinning (§3.10). A non-USD row's USD presentation amount is pinned ONCE,
from the rate on its value date, and never re-fetched — USD-base is presentation
only and must never re-value a settled foreign-currency amount.

The fetch is injectable so the importers/tests stay deterministic and offline;
the default fetcher hits frankfurter (stdlib urllib, no dependency). Rates are
Decimal (never float). For an INR paise amount and a USD-per-INR rate the minor
units cancel (both x100), so money.apply_rate(amount_minor, rate) yields USD
cents directly.

A Wise transfer already carries its REALIZED rate in the statement (pinned at
parse time) — do not overwrite it here; pin_base only fills rows that have no
fx_rate yet.
"""
from __future__ import annotations

import json
import urllib.request
from decimal import Decimal
from typing import Callable, List, Optional

from modules.finance import money

FRANKFURTER = "https://api.frankfurter.app"


def frankfurter_fetch(date: str, base: str, quote: str) -> tuple[Decimal, str]:
    """Return (rate, realized_date) for 1 `base` -> `quote` on `date`.
    frankfurter snaps weekends/holidays to the last business day and reports the
    actual date in the payload, which we pin as fx_date. Decimal only."""
    url = f"{FRANKFURTER}/{date}?from={base}&to={quote}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    rate = data.get("rates", {}).get(quote)
    if rate is None:
        raise ValueError(f"no {base}->{quote} rate for {date}")
    return Decimal(str(rate)), data.get("date", date)


def pin_base(rows: List[dict], *, base_currency: str = "USD",
             fetch: Callable[[str, str, str], tuple] = frankfurter_fetch,
             date_field: str = "value_date") -> dict:
    """Pin amount_base_minor / fx_rate / fx_date on each row, in place.

    - currency == base_currency: rate '1', amount_base_minor == amount_minor.
    - already has fx_rate (e.g. a Wise realized transfer): respect it, only
      (re)derive amount_base_minor from the pinned rate.
    - else: fetch the rate on the row's value date (cached per (currency,date),
      so each date is fetched at most once — never re-fetched).
    Rows with amount_minor None (quarantined) are skipped.
    Returns {pinned, skipped, fetched_dates}.
    """
    cache: dict[tuple, tuple] = {}
    pinned = skipped = 0
    fetched: set = set()
    for r in rows:
        if r.get("amount_minor") is None:
            skipped += 1
            continue
        cur = r.get("currency") or base_currency
        if cur == base_currency:
            r["fx_rate"] = "1"
            r["fx_date"] = r.get(date_field) or r.get("date")
            r["amount_base_minor"] = r["amount_minor"]
            pinned += 1
            continue
        if r.get("fx_rate"):                       # realized rate already pinned
            rate = Decimal(str(r["fx_rate"]))
            r.setdefault("fx_date", r.get(date_field) or r.get("date"))
            r["amount_base_minor"] = money.apply_rate(r["amount_minor"], rate)
            pinned += 1
            continue
        when = r.get(date_field) or r.get("date")
        ck = (cur, when)
        if ck not in cache:
            cache[ck] = fetch(when, cur, base_currency)
            fetched.add(when)
        rate, realized_date = cache[ck]
        r["fx_rate"] = str(rate)
        r["fx_date"] = realized_date
        r["amount_base_minor"] = money.apply_rate(r["amount_minor"], rate)
        pinned += 1
    return {"pinned": pinned, "skipped": skipped,
            "fetched_dates": sorted(fetched)}
