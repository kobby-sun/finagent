"""Deterministic calculation helpers (FIN-POL-002 s5).

All arithmetic is ``decimal.Decimal`` with explicit ROUND_HALF_UP to cents.
Every calculation that influences a decision is recorded as a Calculation
(inputs, formula, result, rounding, threshold, pass/fail) so it is auditable
and reproducible. The model never performs arithmetic.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def q(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def s(v: Decimal | None) -> str:
    return "null" if v is None else format(q(v), "f")


def calc(name: str, formula: str, inputs: dict, result, threshold=None, passed=None,
         rounding: str = "ROUND_HALF_UP to 0.01") -> dict:
    return {
        "name": name, "formula": formula,
        "inputs": {k: (format(v, "f") if isinstance(v, Decimal) else str(v)) for k, v in inputs.items()},
        "result": format(result, "f") if isinstance(result, Decimal) else str(result),
        "rounding": rounding,
        "threshold": None if threshold is None else (format(threshold, "f") if isinstance(threshold, Decimal) else str(threshold)),
        "passed": passed,
    }


# ----------------------------------------------------------------- business days
def is_business_day(d: date) -> bool:
    return d.weekday() < 5  # public holidays: see known limitations


def add_business_days(d: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        d = d + timedelta(days=step)
        if is_business_day(d):
            remaining -= 1
    return d


def business_days_between(start: date, end: date) -> int:
    """Number of business days after ``start`` up to and including ``end``."""
    if end <= start:
        return 0
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if is_business_day(d):
            n += 1
    return n


def to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date() if "T" in str(v) else date.fromisoformat(str(v))
