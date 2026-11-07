"""Price distributions on the sampled shelf.

Listings are different offers of different products, so only distributions are compared
here (median, quartile bands, discount rate). Price *changes* are computed in
``events.py`` and only for the same offer (same product at the same retailer).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence

from app.intelligence.common import UNBRANDED, Observation, positive_price


def _quartiles(prices: Sequence[float]) -> tuple[float, float, float]:
    if len(prices) == 1:
        return prices[0], prices[0], prices[0]
    q1, median, q3 = statistics.quantiles(prices, n=4, method="inclusive")
    return q1, median, q3


def is_discounted(obs: Observation) -> bool:
    price = positive_price(obs.get("price"))
    old = positive_price(obs.get("old_price"))
    return price is not None and old is not None and old > price


def discount_rate_pct(observations: Sequence[Observation]) -> float | None:
    """Share of priced listings showing a strike-through discount, in percent points.

    ``None`` when no listing has a usable price (no basis for a rate).
    """
    priced = [obs for obs in observations if positive_price(obs.get("price")) is not None]
    if not priced:
        return None
    return round(100.0 * sum(1 for obs in priced if is_discounted(obs)) / len(priced), 1)


def price_stats(observations: Sequence[Observation]) -> dict:
    priced = [obs for obs in observations if positive_price(obs.get("price")) is not None]
    prices = sorted(float(obs["price"]) for obs in priced)
    if not prices:
        return {"count": 0, "median": None, "q1": None, "q3": None, "min": None, "max": None,
                "bands": {"budget": 0, "mid": 0, "premium": 0}, "discount_rate": 0.0, "per_brand_median": {},
                "per_brand_discount_rate_pct": {}}
    q1, median, q3 = _quartiles(prices)
    bands = {
        "budget": sum(1 for p in prices if p < q1),
        "mid": sum(1 for p in prices if q1 <= p <= q3),
        "premium": sum(1 for p in prices if p > q3),
    }
    per_brand: dict[str, list[float]] = defaultdict(list)
    per_brand_obs: dict[str, list[Observation]] = defaultdict(list)
    for obs in priced:
        per_brand[obs.get("brand") or UNBRANDED].append(float(obs["price"]))
        per_brand_obs[obs.get("brand") or UNBRANDED].append(obs)
    return {
        "count": len(prices),
        "median": round(median, 2),
        "q1": round(q1, 2),
        "q3": round(q3, 2),
        "min": prices[0],
        "max": prices[-1],
        "bands": bands,
        "discount_rate": round(sum(1 for obs in priced if is_discounted(obs)) / len(priced), 3),
        "per_brand_median": {brand: round(statistics.median(values), 2) for brand, values in per_brand.items()},
        "per_brand_discount_rate_pct": {brand: discount_rate_pct(rows) for brand, rows in per_brand_obs.items()},
    }


def price_headroom(stats: dict) -> float | None:
    """How far the upper quartile sits above the median, as a fraction of the median."""
    median, q3 = stats.get("median"), stats.get("q3")
    if not median or q3 is None:
        return None
    return max(0.0, (q3 - median) / median)
