"""Demo history for ANY market.

Builds a few backdated weekly shopping scans (``is_demo=1``) by perturbing whatever the
market's latest real scan holds: drifting prices, a few price movers, products that are
absent in the past (so they show up as new), one brand that is new to the shelf, rank
moves and discounts that "start" in the real scan. When the market has no real Google
Trends data, a demo interest series is added too (one rising and one falling keyword).

Nothing here knows about any category. Run it with::

    python -m app.seed_history --market-id 1
    python -m app.seed_history --all --force
"""

from __future__ import annotations

import argparse
import math
import random
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from app import repo
from app.intelligence.common import brand_key, positive_price
from app.services.analytics_service import normalize_pending, recompute_market
from app.services.normalize import offer_key

DEFAULT_WEEKS = 4
TREND_WEEKS = 52

Row = dict[str, Any]


class SeedHistoryError(RuntimeError):
    pass


def _best_positions(base: Sequence[Row]) -> dict[str, int]:
    best: dict[str, int] = {}
    for obs in base:
        key = obs.get("canonical_key")
        raw = obs.get("position")
        position = raw if isinstance(raw, int) else 99
        if key and (key not in best or position < best[key]):
            best[key] = position
    return best


def plan_perturbations(base: Sequence[Row], rng: random.Random, own_brand: str | None = None) -> dict[str, Any]:
    """Decide which products enter, leave, change price or move rank. Pure function."""
    own = brand_key(own_brand)
    best = _best_positions(base)
    keys_by_brand: dict[str, set[str]] = defaultdict(set)
    for obs in base:
        if obs.get("brand") and obs.get("canonical_key"):
            keys_by_brand[brand_key(obs["brand"])].add(obs["canonical_key"])

    # One brand with a small footprint becomes "new to the shelf" in the real scan.
    candidates = sorted(
        (min(best[k] for k in keys), bkey)
        for bkey, keys in keys_by_brand.items()
        if bkey != own and 1 <= len(keys) <= 3 and min(best[k] for k in keys) <= 20
    )
    new_brand = rng.choice(candidates[:3])[1] if candidates else None
    new_brand_keys = keys_by_brand.get(new_brand, set()) if new_brand else set()
    own_keys = keys_by_brand.get(own, set()) if own else set()

    pool = sorted(k for k, pos in best.items() if 3 <= pos <= 25 and k not in new_brand_keys and k not in own_keys)
    entering = set(rng.sample(pool, min(len(pool), max(2, min(5, len(best) // 20)))))
    rest = sorted(k for k, pos in best.items() if pos > 10 and k not in entering | new_brand_keys | own_keys)
    leaving = set(rng.sample(rest, min(len(rest), max(1, min(3, len(best) // 30)))))

    presence_changed = entering | new_brand_keys | leaving
    offers = sorted({offer_key(obs) for obs in base if positive_price(obs.get("price"))})
    # Price movers must exist in the most recent demo week, or no comparison is possible.
    visible_offers = sorted({
        offer_key(obs) for obs in base
        if positive_price(obs.get("price")) and obs.get("canonical_key") not in presence_changed
    })
    movers = rng.sample(visible_offers, min(len(visible_offers), max(2, min(6, len(offers) // 15))))
    price_shift = {}
    for i, key in enumerate(movers):
        magnitude = rng.uniform(0.08, 0.18)
        # Two thirds were pricier before (a cut now), one third were cheaper (a rise now).
        price_shift[key] = magnitude if i % 3 != 2 else -magnitude
    drift = {key: rng.uniform(-0.012, 0.012) for key in offers}

    listings = sorted(
        {(obs.get("keyword", ""), obs["canonical_key"]): obs["position"]
         for obs in base if obs.get("canonical_key") and isinstance(obs.get("position"), int)}.items()
    )
    climbers = [item for item in listings if item[1] <= 12 and item[0][1] not in entering | new_brand_keys]
    fallers = [item for item in listings if 6 <= item[1] <= 20 and item[0][1] not in entering | new_brand_keys]
    rank_offset: dict[tuple[str, str], int] = {}
    for (listing, _) in rng.sample(climbers, min(len(climbers), 2)):
        rank_offset[listing] = rng.randint(6, 10)  # was lower on the shelf: climbs now
    for (listing, _) in rng.sample(fallers, min(len(fallers), 2)):
        rank_offset.setdefault(listing, -rng.randint(4, 5))  # was higher: falls now

    discount_started = {
        offer_key(obs) for obs in base
        if (positive_price(obs.get("old_price")) or 0) > (positive_price(obs.get("price")) or 0)
    }
    return {
        "new_brand": new_brand,
        "new_brand_keys": new_brand_keys,
        "entering": entering,
        "leaving": leaving,
        "price_shift": price_shift,
        "drift": drift,
        "rank_offset": rank_offset,
        "discount_started": discount_started,
    }


def build_demo_week(base: Sequence[Row], plan: dict[str, Any], weeks_back: int, rng: random.Random) -> list[Row]:
    """Observations for the demo scan ``weeks_back`` weeks before the real data."""
    rows = []
    hidden = plan["entering"] | plan["new_brand_keys"]
    for obs in base:
        key = obs.get("canonical_key")
        if key in hidden or (key in plan["leaving"] and weeks_back <= 2):
            continue
        offer = offer_key(obs)
        factor = (1 + plan["drift"].get(offer, 0.0)) ** weeks_back * (1 + plan["price_shift"].get(offer, 0.0))
        price = positive_price(obs.get("price"))
        old_price = None if offer in plan["discount_started"] else positive_price(obs.get("old_price"))
        position = obs.get("position") if isinstance(obs.get("position"), int) else None
        if position is not None:
            position += plan["rank_offset"].get((obs.get("keyword", ""), key), 0)
            position = max(1, position + rng.choice((-1, 0, 0, 1)))
        reviews = obs.get("reviews")
        rows.append({
            **{field: obs.get(field) for field in (
                "keyword", "product_id", "title", "source", "rating", "product_link", "thumbnail",
                "brand", "brand_inferred", "canonical_key",
            )},
            "price": round(price * factor, 2) if price else None,
            "old_price": round(old_price * factor, 2) if old_price else None,
            "position": position,
            "reviews": int(reviews * (1 - 0.02 * weeks_back)) if isinstance(reviews, int) else reviews,
        })
    return rows


def _week_start(day: date) -> date:
    """Google Trends weeks start on Sunday."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def build_demo_trends(keywords: Sequence[str], anchor: datetime, rng: random.Random, batch_id: str) -> list[Row]:
    last_complete = _week_start(anchor.date()) - timedelta(days=7)
    rising = rng.randrange(len(keywords))
    falling = (rising + 1) % len(keywords) if len(keywords) >= 3 else None
    series: dict[str, list[float]] = {}
    for i, keyword in enumerate(keywords):
        level, phase = rng.uniform(25, 70), rng.uniform(0, 2 * math.pi)
        values = []
        for t in range(TREND_WEEKS):
            ramp = max(0.0, (t - 40) / 11)
            factor = 1 + 0.8 * ramp if i == rising else 1 - 0.35 * ramp if i == falling else 1.0
            season = 1 + 0.08 * math.sin(2 * math.pi * t / TREND_WEEKS + phase)
            values.append(level * season * factor * (1 + rng.uniform(-0.06, 0.06)))
        series[keyword] = values
    peak = max(max(values) for values in series.values()) or 1.0
    points = []
    for keyword, values in series.items():
        for t, value in enumerate(values):
            week = last_complete - timedelta(days=7 * (TREND_WEEKS - 1 - t))
            points.append({
                "keyword": keyword, "batch_id": batch_id, "week_start": week.isoformat(),
                "interest": float(round(100 * value / peak)), "is_partial": False,
            })
    return points


def seed_history(market_id: int, *, weeks: int = DEFAULT_WEEKS, force: bool = False, seed: int | None = None) -> dict[str, Any]:
    market = repo.get_market(market_id)
    if market is None:
        raise SeedHistoryError(f"Market {market_id} not found.")
    live_scans = [s for s in repo.list_scans(market_id, "shopping") if not s["is_demo"]]
    if not live_scans:
        raise SeedHistoryError("Run one live scan for this market first; demo history perturbs real data.")
    if any(s["is_demo"] for s in repo.list_scans(market_id)):
        if not force:
            return {"market_id": market_id, "created": False, "message": "Demo history already exists (use force)."}
        repo.delete_demo_data(market_id)

    normalize_pending(market_id)  # legacy rows need brand/canonical keys before perturbing
    base_scan = live_scans[-1]
    anchor = datetime.fromisoformat(live_scans[0]["scanned_at"])  # demo weeks precede all real data
    base = repo.observations_for_scan(base_scan["id"])
    rng = random.Random(seed if seed is not None else 7919 * market_id + 17)
    plan = plan_perturbations(base, rng, market.get("brand_name"))

    oldest_demo_at = None
    for weeks_back in range(weeks, 0, -1):
        scanned_at = (anchor - timedelta(days=7 * weeks_back)).isoformat()
        oldest_demo_at = oldest_demo_at or scanned_at
        repo.save_shopping_scan(market_id, scanned_at, build_demo_week(base, plan, weeks_back, rng), is_demo=True)

    trend_points = 0
    if repo.latest_scan(market_id, "trends", live_only=True) is None and market["keywords"] and oldest_demo_at:
        points = build_demo_trends(market["keywords"], anchor, rng, f"demo-{market_id}")
        repo.save_trends_scan(market_id, oldest_demo_at, points, is_demo=True)
        trend_points = len(points)

    analysis = recompute_market(market_id)
    return {
        "market_id": market_id,
        "created": True,
        "demo_scans": weeks,
        "base_scan_id": base_scan["id"],
        "trend_points": trend_points,
        "entering_products": len(plan["entering"]),
        "new_competitor": next(
            (obs["brand"] for obs in base if brand_key(obs.get("brand")) == plan["new_brand"]), None
        ),
        "price_movers": len(plan["price_shift"]),
        "events_detected": analysis["events"],
        "message": f"Added {weeks} demo weekly scans before the first real scan.",
    }


def main() -> None:
    from app.db import initialize_database

    parser = argparse.ArgumentParser(description="Add demo historical scans (is_demo=1) to markets.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--market-id", type=int)
    target.add_argument("--all", action="store_true", help="every market that has a real scan")
    parser.add_argument("--weeks", type=int, default=DEFAULT_WEEKS)
    parser.add_argument("--force", action="store_true", help="replace existing demo history")
    args = parser.parse_args()

    initialize_database()
    market_ids = repo.all_market_ids() if args.all else [args.market_id]
    for market_id in market_ids:
        try:
            print(seed_history(market_id, weeks=args.weeks, force=args.force))
        except SeedHistoryError as error:
            print(f"market {market_id}: {error}")


if __name__ == "__main__":
    main()
