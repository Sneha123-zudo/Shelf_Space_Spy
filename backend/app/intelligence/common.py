"""Small helpers shared by the analytics modules."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

Observation = dict[str, Any]

UNBRANDED = "Unbranded"


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def brand_key(name: str | None) -> str:
    """Comparison key for brand names: ``Dot & Key`` == ``dot and key`` == ``DOT-AND-KEY``."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", ascii_fold(name).lower().replace("&", "and"))


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def round1(value: float | None) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), 1)


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def positive_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def fmt_number(value: float | None) -> str:
    """Human formatting used in reasons/summaries: integers stay integers, else 1 decimal."""
    if value is None:
        return "n/a"
    rounded = round(float(value), 1)
    if rounded == int(rounded):
        return f"{int(rounded):,}"
    return f"{rounded:,.1f}"
