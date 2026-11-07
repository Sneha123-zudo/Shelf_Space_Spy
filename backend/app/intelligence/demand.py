"""Demand signals from Google Trends interest-over-time.

Rules:
* Recent window (last 4 complete weeks) vs prior window (the 8 weeks before it).
* Partial (still running) weeks are excluded.
* Trends values are scaled per request batch (max 100 within a batch), so a keyword is
  only ever compared with points from its own batch. If a keyword appears in several
  batches, only the newest batch is used. Growth ratios are scale-free, so they can be
  shown side by side; absolute levels from different batches must not be compared.
* Very low interest makes relative changes meaningless: flagged ``low_volume``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from app.intelligence.common import pct_change, round1

RECENT_WEEKS = 4
PRIOR_WEEKS = 8
LOW_VOLUME_THRESHOLD = 5.0
MOMENTUM_THRESHOLD_PCT = 3.0

TrendPoint = dict[str, Any]


def _slope(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    numerator = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    return numerator / denominator if denominator else 0.0


def demand_signal(
    points: Sequence[TrendPoint],
    recent_weeks: int = RECENT_WEEKS,
    prior_weeks: int = PRIOR_WEEKS,
) -> dict:
    """Signal for ONE keyword from ONE batch. ``points`` need ``week_start`` and ``interest``."""
    complete = sorted((p for p in points if not p.get("is_partial")), key=lambda p: p["week_start"])
    partial_excluded = len(points) - len(complete)
    caveats: list[str] = []
    base: dict[str, Any] = {
        "recent_avg": None, "prior_avg": None, "growth_pct": None, "momentum": "unknown",
        "slope_pct_per_week": None, "low_volume": False, "weeks_used": len(complete),
        "partial_weeks_excluded": partial_excluded, "caveats": caveats,
    }
    if len(complete) < recent_weeks + 2:
        caveats.append("Not enough Google Trends history to compare recent and prior weeks.")
        return base
    recent = [float(p["interest"]) for p in complete[-recent_weeks:]]
    prior = [float(p["interest"]) for p in complete[-(recent_weeks + prior_weeks):-recent_weeks]]
    recent_avg = sum(recent) / len(recent)
    prior_avg = sum(prior) / len(prior)
    if len(prior) < prior_weeks:
        caveats.append(f"Prior window has only {len(prior)} of {prior_weeks} weeks.")
    low_volume = max(recent_avg, prior_avg) < LOW_VOLUME_THRESHOLD
    if low_volume:
        caveats.append("Search interest is very low; relative changes are unreliable.")
    growth = pct_change(recent_avg, prior_avg)
    if growth is None:
        caveats.append("No prior search interest to compare against.")
    slope = _slope(recent)
    slope_pct = slope / recent_avg * 100.0 if recent_avg else 0.0
    if slope_pct > MOMENTUM_THRESHOLD_PCT:
        momentum = "rising"
    elif slope_pct < -MOMENTUM_THRESHOLD_PCT:
        momentum = "falling"
    else:
        momentum = "flat"
    base.update(
        recent_avg=round1(recent_avg),
        prior_avg=round1(prior_avg),
        growth_pct=round1(growth),
        momentum=momentum,
        slope_pct_per_week=round1(slope_pct),
        low_volume=low_volume,
    )
    return base


def demand_by_keyword(points: Sequence[TrendPoint]) -> dict[str, dict]:
    """One signal per keyword, never mixing Trends batches."""
    by_keyword: dict[str, dict[str, list[TrendPoint]]] = defaultdict(lambda: defaultdict(list))
    for point in points:
        by_keyword[point["keyword"].lower()][str(point.get("batch_id", ""))].append(point)
    signals: dict[str, dict] = {}
    for keyword, batches in by_keyword.items():
        # Newest batch = the one whose latest week is most recent (ties: lexical batch id).
        batch_id = max(batches, key=lambda b: (max(p["week_start"] for p in batches[b]), b))
        signal = demand_signal(batches[batch_id])
        signal["batch_id"] = batch_id
        if len(batches) > 1:
            signal["caveats"].append("Only the newest Google Trends batch is used; batches are not comparable.")
        signals[keyword] = signal
    return signals
