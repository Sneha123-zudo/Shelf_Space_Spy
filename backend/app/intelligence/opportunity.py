"""Demand-versus-supply opportunity score per probe keyword.

Score = weighted sum of four components, each normalized to 0-100:

=====================  ======  ==========================================================
component              weight  100 means
=====================  ======  ==========================================================
demand                 40      search interest growing fast (recent 4w vs prior 8w)
room                   25      fragmented shelf (top-3 brands hold little visibility) and
                               SKU count not growing
incumbent_pressure     20      weak incumbents (few reviews on top-10 listings)
price_headroom         15      wide gap between median and upper-quartile price, little
                               discounting
=====================  ======  ==========================================================

Labels: >=75 Golden, 55-74 Attractive, 35-54 Monitor, <35 Crowded. Missing inputs score a
neutral 50 and add a caveat. Confidence (data volume, scan count, shelf stability) is
reported separately and never changes the score.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from app.intelligence.common import Observation, clamp, fmt_number, round1
from app.intelligence.pricing import price_headroom

WEIGHTS = {"demand": 40, "room": 25, "incumbent_pressure": 20, "price_headroom": 15}
LABELS = ((75, "Golden"), (55, "Attractive"), (35, "Monitor"), (0, "Crowded"))
NEUTRAL = 50.0
SHELF_SAMPLE_CAVEAT = "Based on a sampled Google Shopping shelf; visibility is not market share."


def label_for(score: float) -> str:
    for threshold, label in LABELS:
        if score >= threshold:
            return label
    return "Crowded"


def incumbent_strength(observations: Sequence[Observation], top_n: int = 10) -> dict:
    """Review depth and rating of the best-placed listings."""
    ranked = sorted(
        (obs for obs in observations if isinstance(obs.get("position"), int)), key=lambda obs: obs["position"]
    )[:top_n]
    reviews = [int(obs["reviews"]) for obs in ranked if isinstance(obs.get("reviews"), (int, float))]
    ratings = [float(obs["rating"]) for obs in ranked if isinstance(obs.get("rating"), (int, float))]
    return {
        "listings": len(ranked),
        "median_reviews": statistics.median(reviews) if reviews else None,
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
    }


def _saturating(growth: float) -> float:
    """Map growth % to 0-100 around NEUTRAL without clipping: +50% no longer maxes the component,
    so strong probes keep their relative order. Roughly linear near zero, flattening toward 0/100."""
    return NEUTRAL + NEUTRAL * math.tanh(growth / 75.0)


def _demand_component(demand: dict | None, reasons: list, caveats: list) -> float:
    if not demand or demand.get("growth_pct") is None:
        caveats.append("No usable Google Trends comparison; demand scored as neutral.")
        if demand:
            caveats.extend(demand.get("caveats", []))
        return NEUTRAL
    growth = float(demand["growth_pct"])
    swing = growth / 2 if demand.get("low_volume") else growth  # halve the swing when volume is too low to trust
    value = _saturating(swing)
    if demand.get("low_volume"):
        caveats.extend(demand.get("caveats", []))
    momentum = demand.get("momentum")
    if momentum == "rising":
        value += 5
    elif momentum == "falling":
        value -= 5
    direction = "up" if growth >= 0 else "down"
    reasons.append(
        f"Search interest {direction} {fmt_number(abs(growth))}% (last 4 weeks vs prior 8)"
        + (f", momentum {momentum}" if momentum in {"rising", "falling"} else "")
        + "."
    )
    return clamp(value)


def _comparison_text(comparison_days: int | None) -> str:
    if comparison_days and comparison_days >= 1:
        return f"vs {comparison_days} day{'s' if comparison_days != 1 else ''} earlier"
    return "since the previous scan"


def _room_component(
    supply: dict | None, supply_growth: float | None, comparison: str, reasons: list, caveats: list
) -> float:
    if not supply or not supply.get("results"):
        caveats.append("No shelf data for this probe; room scored as neutral.")
        return NEUTRAL
    top3 = float(supply.get("top3_share") or 0.0)
    concentration_room = 100.0 * (1.0 - top3)
    reasons.append(f"Top 3 brands hold {fmt_number(top3 * 100)}% of shelf visibility.")
    if supply_growth is None:
        growth_room = NEUTRAL
        caveats.append("Only one scan for this probe; SKU growth unknown.")
    else:
        growth_room = clamp(NEUTRAL - supply_growth)
        if abs(supply_growth) < 0.05:
            reasons.append(f"Distinct SKUs unchanged {comparison}.")
        else:
            direction = "up" if supply_growth > 0 else "down"
            reasons.append(f"Distinct SKUs {direction} {fmt_number(abs(supply_growth))}% {comparison}.")
    return clamp(0.7 * concentration_room + 0.3 * growth_room)


def _incumbent_component(incumbents: dict | None, reasons: list, caveats: list) -> float:
    if not incumbents or incumbents.get("median_reviews") is None:
        caveats.append("Review counts unavailable for top listings; incumbent pressure scored as neutral.")
        return NEUTRAL
    median_reviews = float(incumbents["median_reviews"])
    pressure = clamp(25.0 * math.log10(1.0 + float(median_reviews)))  # 100 reviews ~ 50, 10k ~ 100
    avg_rating = incumbents.get("avg_rating")
    if avg_rating is not None and avg_rating >= 4.5:
        pressure = clamp(pressure + 10)
    reasons.append(
        f"Top-10 listings have a median of {fmt_number(median_reviews)} reviews"
        + (f" and an average rating of {fmt_number(avg_rating)}" if avg_rating is not None else "")
        + "."
    )
    return clamp(100.0 - pressure)


def _price_component(pricing: dict | None, reasons: list, caveats: list) -> float:
    headroom = price_headroom(pricing or {})
    if headroom is None:
        caveats.append("Not enough prices to measure headroom; scored as neutral.")
        return NEUTRAL
    discount_rate = float((pricing or {}).get("discount_rate") or 0.0)
    value = clamp(headroom * 200.0) * (1.0 - 0.5 * discount_rate)
    reasons.append(f"Upper-quartile price is {fmt_number(headroom * 100)}% above the median.")
    if discount_rate > 0:
        reasons.append(f"{fmt_number(discount_rate * 100)}% of listings show a discount.")
    return clamp(value)


def confidence_for(*, results: int, trend_weeks: int, scan_count: int, stability: float | None) -> dict:
    factors = {
        "data_volume": "ok" if results >= 20 and trend_weeks >= 12 else "thin",
        "scan_count": scan_count,
        "shelf_stability": stability,
    }
    points = 0
    points += results >= 20
    points += trend_weeks >= 12
    points += scan_count >= 3
    points += stability is not None and stability >= 0.5
    level = "high" if points >= 4 else "medium" if points >= 2 else "low"
    return {"level": level, "factors": factors}


def score_opportunity(
    keyword: str,
    *,
    demand: dict | None,
    supply: dict | None,
    supply_growth: float | None,
    pricing: dict | None,
    incumbents: dict | None,
    scan_count: int,
    stability: float | None,
    uses_demo_data: bool = False,
    comparison_days: int | None = None,
) -> dict:
    """Score one probe. ``comparison_days``: age of the scan that supply growth is measured against."""
    reasons: list[str] = []
    caveats: list[str] = []
    components = {
        "demand": _demand_component(demand, reasons, caveats),
        "room": _room_component(supply, supply_growth, _comparison_text(comparison_days), reasons, caveats),
        "incumbent_pressure": _incumbent_component(incumbents, reasons, caveats),
        "price_headroom": _price_component(pricing, reasons, caveats),
    }
    score = sum(components[name] * weight for name, weight in WEIGHTS.items()) / 100.0
    if scan_count < 3:
        caveats.append(f"Only {scan_count} scan{'s' if scan_count != 1 else ''} of history.")
    if stability is not None and stability < 0.5:
        caveats.append(f"The shelf changed a lot {_comparison_text(comparison_days)}; treat movements cautiously.")
    caveats.append(SHELF_SAMPLE_CAVEAT)
    return {
        "keyword": keyword,
        "score": round(score, 1),
        "label": label_for(score),
        "demand_growth": demand.get("growth_pct") if demand else None,
        "supply_growth": round1(supply_growth),
        "components": {name: round(value, 1) for name, value in components.items()},
        "reasons": reasons,
        "caveats": list(dict.fromkeys(caveats)),
        "confidence": confidence_for(
            results=int((supply or {}).get("results") or 0),
            trend_weeks=int((demand or {}).get("weeks_used") or 0),
            scan_count=scan_count,
            stability=stability,
        ),
    }
