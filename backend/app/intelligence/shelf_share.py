"""Position-weighted shelf visibility per brand.

A listing at position ``p`` gets weight ``1 / (1 + 0.1 * (p - 1))``: position 1 counts 1.0,
position 11 counts 0.5. Listings without a detected brand form their own ``Unbranded``
entry. This measures visibility on a sampled search shelf, not market share.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from app.intelligence.common import UNBRANDED, Observation, brand_key

MISSING_POSITION = 40


def visibility_weight(position: int | None) -> float:
    p = position if isinstance(position, int) and position >= 1 else MISSING_POSITION
    return 1.0 / (1.0 + 0.1 * (p - 1))


def _share(observations: Sequence[Observation], own_brand: str | None) -> list[dict]:
    weights: dict[str, float] = defaultdict(float)
    listings: Counter[str] = Counter()
    names: dict[str, Counter[str]] = defaultdict(Counter)
    for obs in observations:
        key = brand_key(obs.get("brand")) or ""
        weights[key] += visibility_weight(obs.get("position"))
        listings[key] += 1
        names[key][obs.get("brand") or UNBRANDED] += 1
    total = sum(weights.values())
    if total <= 0:
        return []
    own_key = brand_key(own_brand)
    rows: list[dict[str, Any]] = [
        {
            "brand": names[key].most_common(1)[0][0] if key else UNBRANDED,
            "share_pct": round(100.0 * weight / total, 1),
            "visibility": round(weight, 3),
            "listings": listings[key],
            "is_mine": bool(own_key) and key == own_key,
            "is_unbranded": key == "",
        }
        for key, weight in weights.items()
    ]
    rows.sort(key=lambda row: (-row["visibility"], row["brand"].lower()))
    return rows


def shelf_share(observations: Sequence[Observation], own_brand: str | None = None) -> list[dict]:
    """Overall visibility share per brand across every probe keyword, largest first."""
    return _share(observations, own_brand)


def shelf_share_by_probe(observations: Sequence[Observation], own_brand: str | None = None) -> dict[str, list[dict]]:
    by_keyword: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        by_keyword[obs.get("keyword", "")].append(obs)
    return {keyword: _share(rows, own_brand) for keyword, rows in by_keyword.items()}


def top_n_share(rows: Sequence[dict], n: int = 3) -> float:
    """Combined share (0-1) of the ``n`` most visible real brands (unbranded excluded)."""
    branded = [row for row in rows if not row.get("is_unbranded")]
    return sum(row["share_pct"] for row in branded[:n]) / 100.0


def collapse_tail(rows: Sequence[dict], keep: int = 10) -> list[dict]:
    """Keep the ``keep`` largest entries (plus the user's own brand) and fold the rest into 'Other brands'."""
    head = list(rows[:keep])
    rest = list(rows[keep:])
    mine = [row for row in rest if row.get("is_mine")]
    head.extend(mine)
    rest = [row for row in rest if not row.get("is_mine")]
    if rest:
        head.append({
            "brand": "Other brands",
            "share_pct": round(sum(row["share_pct"] for row in rest), 1),
            "visibility": round(sum(row["visibility"] for row in rest), 3),
            "listings": sum(row["listings"] for row in rest),
            "is_mine": False,
            "is_unbranded": False,
        })
    return head
