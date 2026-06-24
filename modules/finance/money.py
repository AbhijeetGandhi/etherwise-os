"""Money primitives — the single rounding/representation authority (§3.1).

Money is a signed INTEGER count of minor units (USD cents, INR paise). The ONLY
rounding rule, applied everywhere money is created from a ratio/percentage/FX:
ROUND_HALF_UP to the minor unit. There is NO float anywhere in this module
(the float-ban test greps for it); Decimal is the only non-integer type, used
transiently at conversion boundaries and immediately quantized to int minor.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Union

MINOR_PER_MAJOR = 100          # cents/paise per unit for USD & INR (2 dp)
Numeric = Union[int, str, Decimal]


def to_minor(value: Numeric) -> int:
    """Major-unit amount (Decimal/str/int, e.g. '285.17') -> signed int minor
    units (28517), ROUND_HALF_UP. NEVER pass a float (banned); pass a string or
    Decimal so the value is exact."""
    if isinstance(value, float):                      # defensive: the ban
        raise TypeError("float is banned in money paths — pass str/Decimal/int")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return int((d * MINOR_PER_MAJOR).quantize(Decimal("1"), ROUND_HALF_UP))


def from_minor(minor: int) -> Decimal:
    """Int minor units -> exact Decimal major-unit (for display/formatting)."""
    return (Decimal(int(minor)) / MINOR_PER_MAJOR).quantize(Decimal("0.01"))


def fmt(minor: int, symbol: str = "") -> str:
    """Display string with thousands separators, 2 dp, sign-aware."""
    d = from_minor(minor)
    return f"{symbol}{d:,.2f}"


def apply_rate(minor: int, rate: Numeric) -> int:
    """minor-unit amount × a Decimal rate (e.g. FX, a salary percentage) ->
    int minor, ROUND_HALF_UP. rate must be str/Decimal (never float)."""
    if isinstance(rate, float):
        raise TypeError("float rate banned — pass str/Decimal")
    r = rate if isinstance(rate, Decimal) else Decimal(str(rate))
    return int((Decimal(int(minor)) * r).quantize(Decimal("1"), ROUND_HALF_UP))


def pct(minor: int, percent: Numeric) -> int:
    """percent of a minor amount (percent as '50' for 50%), ROUND_HALF_UP int."""
    if isinstance(percent, float):
        raise TypeError("float percent banned — pass str/Decimal")
    p = percent if isinstance(percent, Decimal) else Decimal(str(percent))
    return int((Decimal(int(minor)) * p / 100).quantize(Decimal("1"),
                                                         ROUND_HALF_UP))
