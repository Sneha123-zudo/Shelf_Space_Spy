"""Thin glue between the database (``repo``) and the pure ``intelligence`` modules.

Loads rows, calls the deterministic analytics, persists events and opportunities, and
assembles the dashboard payload. No scoring logic lives here.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from app import repo
from app.intelligence.common import brand_key, positive_price, round1
from app.intelligence.demand import demand_by_keyword
from app.intelligence.events import SEVERITY_ORDER, detect_events
from app.intelligence.opportunity import incumbent_strength, score_opportunity
from app.intelligence.pricing import discount_rate_pct, price_stats
from app.intelligence.shelf_share import collapse_tail, shelf_share
from app.intelligence.supply import shelf_stability, supply_growth_pct, supply_metrics
from app.services.normalize import CanonicalIndex, detect_brands, preferred_names

logger = logging.getLogger(__name__)

HISTORY_SCANS = 6          # earlier shopping scans used for change detection
REFERENCE_MIN_AGE = timedelta(days=6)  # supply growth compares with a scan about a week older
PRESENCE_EVENTS = {"NEW_PRODUCT", "NEW_COMPETITOR", "REMOVED_PRODUCT"}
WINDOW_SCANS = 6           # scans that count as "the analysis window" (demo badge)
EVENT_WINDOW_DAYS = 30     # events feed horizon, relative to the latest scan
KPI_WINDOW_DAYS = 7        # "new products this week"
MAX_FEED_EVENTS = 50

Row = dict[str, Any]


def known_brands(market: Row) -> list[str]:
    brands = list(market.get("competitors") or [])
    if market.get("brand_name"):
        brands.append(market["brand_name"])
    return brands


def _group_by_keyword(observations: Sequence[Row]) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for obs in observations:
        grouped[(obs.get("keyword") or "").lower()].append(obs)
    return grouped


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


# --------------------------------------------------------------------------- normalization


def normalize_pending(market_id: int) -> int:
    """Fill brand/canonical_key for observations stored before normalization existed."""
    market = repo.get_market(market_id)
    pending = repo.observations_missing_normalization(market_id)
    if not market or not pending:
        return 0
    index = CanonicalIndex.from_history(repo.product_history(market_id))
    by_scan: dict[int, list[Row]] = defaultdict(list)
    for row in pending:
        by_scan[row["scan_id"] or 0].append(row)
    updates = []
    for scan_id in sorted(by_scan):
        rows = by_scan[scan_id]
        for row, match in zip(rows, detect_brands(rows, known_brands(market)), strict=True):
            brand = match.name if match else None
            key = index.assign(row.get("product_id") or None, row["title"], brand)
            updates.append((brand, bool(match and match.inferred), key, row["id"]))
    repo.update_observation_normalization(updates)
    return len(updates)


def renormalize_brands(market_id: int) -> dict[str, int]:
    """Re-run brand detection over every stored scan of a market with the current rules, then re-analyze.

    Only ``brand`` and ``brand_inferred`` change: raw listings and canonical product keys are untouched, so
    product history stays intact. Use it after the brand rules improve to fix markets scanned earlier.
    """
    market = repo.get_market(market_id)
    if market is None:
        return {"scans": 0, "brands_changed": 0}
    scans = repo.list_scans(market_id, "shopping")
    by_scan = repo.observations_for_scans([scan["id"] for scan in scans])
    everything = [row for scan in scans for row in by_scan[scan["id"]]]
    preferred = preferred_names(everything)
    updates: list[tuple[str | None, bool, int]] = []
    for scan in scans:
        rows = by_scan[scan["id"]]
        for row, match in zip(rows, detect_brands(rows, known_brands(market), preferred), strict=True):
            brand, inferred = (match.name, match.inferred) if match else (None, False)
            if brand != row.get("brand") or inferred != bool(row.get("brand_inferred")):
                updates.append((brand, inferred, row["id"]))
    if updates:
        repo.update_observation_brands(market_id, updates)
        recompute_market(market_id)
    return {"scans": len(scans), "brands_changed": len(updates)}


# --------------------------------------------------------------------------- analysis


def trend_scan_for(market_id: int, scanned_at: str) -> Row | None:
    """Most recent trends scan taken at or before ``scanned_at``."""
    candidates = [scan for scan in repo.list_scans(market_id, "trends") if scan["scanned_at"] <= scanned_at]
    return candidates[-1] if candidates else None


def compute_opportunities(
    market: Row,
    current: Sequence[Row],
    previous: Sequence[Row] | None,
    trend_points: Sequence[Row],
    *,
    scan_count: int,
    uses_demo_data: bool,
    comparison_days: int | None = None,
) -> list[Row]:
    demand = demand_by_keyword(trend_points)
    current_by_kw = _group_by_keyword(current)
    previous_by_kw = _group_by_keyword(previous or [])
    opportunities = []
    for keyword in market["keywords"]:
        rows = current_by_kw.get(keyword.lower(), [])
        prev_rows = previous_by_kw.get(keyword.lower(), [])
        supply = supply_metrics(rows)
        prev_supply = supply_metrics(prev_rows) if prev_rows else None
        opportunities.append(score_opportunity(
            keyword,
            demand=demand.get(keyword.lower()),
            supply=supply,
            supply_growth=supply_growth_pct(supply, prev_supply),
            pricing=price_stats(rows),
            incumbents=incumbent_strength(rows),
            scan_count=scan_count,
            stability=shelf_stability(rows, prev_rows) if prev_rows else None,
            uses_demo_data=uses_demo_data,
            comparison_days=comparison_days,
        ))
    opportunities.sort(key=lambda o: (-o["score"], o["keyword"]))
    return opportunities


def reference_scan(earlier: Sequence[Row], scan_at: datetime) -> Row | None:
    """Most recent earlier scan at least ~a week older; else the immediately previous one."""
    cutoff = scan_at - REFERENCE_MIN_AGE
    for candidate in reversed(earlier):
        if _parse_time(candidate["scanned_at"]) <= cutoff:
            return candidate
    return earlier[-1] if earlier else None


def analyze_scan(market_id: int, scan_id: int) -> dict[str, Any]:
    """Detect events and score opportunities for one shopping scan, then persist both."""
    market = repo.get_market(market_id)
    if market is None:
        raise ValueError(f"Market {market_id} not found")
    shopping = repo.list_scans(market_id, "shopping")
    position = next((i for i, scan in enumerate(shopping) if scan["id"] == scan_id), None)
    if position is None:
        raise ValueError(f"Shopping scan {scan_id} not found for market {market_id}")
    scan = shopping[position]
    scan_at = _parse_time(scan["scanned_at"])
    prior = shopping[max(0, position - HISTORY_SCANS):position]
    reference = reference_scan(shopping[:position], scan_at)
    ids = {s["id"] for s in prior} | ({reference["id"]} if reference else set())
    observations = repo.observations_for_scans(sorted(ids | {scan_id}))
    current = observations[scan_id]
    history = [observations[s["id"]] for s in prior]

    events = detect_events(current, history, own_brand=market.get("brand_name"))
    last_is_demo = bool(prior and prior[-1]["is_demo"])
    any_demo = any(s["is_demo"] for s in prior)
    for event in events:
        # A change measured against demo history is itself demo data.
        event["metadata"]["compared_with_demo"] = any_demo if event["type"] in PRESENCE_EVENTS else last_is_demo
    repo.replace_events(market_id, scan_id, scan["scanned_at"], events)

    trend_scan = trend_scan_for(market_id, scan["scanned_at"])
    points = repo.trend_points_for_scan(trend_scan["id"]) if trend_scan else []
    used = [*prior, scan, *([reference] if reference else [])]
    uses_demo = any(s["is_demo"] for s in used) or bool(trend_scan and trend_scan["is_demo"])
    comparison_days = (scan_at - _parse_time(reference["scanned_at"])).days if reference else None
    opportunities = compute_opportunities(
        market, current, observations[reference["id"]] if reference else None, points,
        scan_count=position + 1, uses_demo_data=uses_demo, comparison_days=comparison_days,
    )
    repo.replace_opportunities(market_id, scan_id, opportunities)
    return {"events": events, "opportunities": opportunities, "trend_points": len(points)}


def recompute_market(market_id: int) -> dict[str, int]:
    """Re-run normalization and analysis for every shopping scan, oldest first."""
    normalized = normalize_pending(market_id)
    scans = repo.list_scans(market_id, "shopping")
    events = 0
    for scan in scans:
        events += len(analyze_scan(market_id, scan["id"])["events"])
    return {"normalized": normalized, "scans_analyzed": len(scans), "events": events}


def backfill_all() -> None:
    """Startup hook: normalize legacy rows and analyze scans that were never analyzed."""
    for market_id in repo.all_market_ids():
        try:
            normalize_pending(market_id)
            for scan in repo.shopping_scans_without_analysis(market_id):
                analyze_scan(market_id, scan["id"])
        except Exception:  # never block API start-up on analytics
            logger.exception("Backfill failed for market %s", market_id)


# --------------------------------------------------------------------------- dashboard


def event_change_pct(event_type: str, evidence: Row) -> float | None:
    """Percent change from ``previous_value`` to ``current_value``, in percent points.

    Price events carry it as ``change_pct``. A started discount moves the price from the
    list price down to the offer price, so it is reported as ``-discount_pct``. Rank and
    presence events have no percent change.
    """
    value = evidence.get("change_pct")
    if isinstance(value, (int, float)):
        return round(float(value), 1)
    depth = evidence.get("discount_pct")
    if event_type == "DISCOUNT_STARTED" and isinstance(depth, (int, float)):
        return round(-float(depth), 1)
    return None


def format_event(event: Row) -> Row:
    metadata = dict(event.get("metadata") or {})
    summary = metadata.pop("summary", None)
    caveat = metadata.pop("caveat", None)
    return {
        "id": event["id"],
        "type": event["type"],
        "severity": event["severity"],
        "confidence": event["confidence"],
        "brand": event.get("brand"),
        "product": event.get("product"),
        "keyword": event.get("keyword"),
        "previous_value": event.get("previous_value"),
        "current_value": event.get("current_value"),
        "change_pct": event_change_pct(event["type"], metadata),
        "detected_at": event["detected_at"],
        "is_demo": bool(event.get("is_demo")) or bool(metadata.get("compared_with_demo")),
        "summary": summary or event["type"].replace("_", " ").title(),
        "caveat": caveat,
        "evidence": metadata,
    }


def competitor_table(market: Row, current: Sequence[Row], share: Sequence[Row], recent_events: Sequence[Row]) -> list[Row]:
    own = brand_key(market.get("brand_name"))
    tracked = {brand_key(name): name for name in market.get("competitors") or []}
    share_by_key = {brand_key(row["brand"]): row for row in share if not row.get("is_unbranded")}
    listings: dict[str, list[Row]] = defaultdict(list)
    for obs in current:
        key = brand_key(obs.get("brand"))
        if key:
            listings[key].append(obs)
    new_products: dict[str, int] = defaultdict(int)
    price_moves: dict[str, int] = defaultdict(int)
    for event in recent_events:
        key = brand_key(event.get("brand"))
        if event["type"] == "NEW_PRODUCT":
            new_products[key] += 1
        elif event["type"] in {"PRICE_DECREASE", "PRICE_INCREASE", "DISCOUNT_STARTED"}:
            price_moves[key] += 1

    keys = list(share_by_key)[:15]
    keys += [key for key in tracked if key not in keys]
    if own and own not in keys:
        keys.append(own)
    rows: list[Row] = []
    for key in keys:
        brand_obs = listings.get(key, [])
        positions = [o["position"] for o in brand_obs if isinstance(o.get("position"), int)]
        prices = [p for p in (positive_price(o.get("price")) for o in brand_obs) if p is not None]
        ratings = [float(o["rating"]) for o in brand_obs if isinstance(o.get("rating"), (int, float))]
        reviews = [int(o["reviews"]) for o in brand_obs if isinstance(o.get("reviews"), (int, float))]
        name = (
            share_by_key[key]["brand"] if key in share_by_key
            else tracked.get(key) or market.get("brand_name") or key
        )
        rows.append({
            "brand": name,
            "is_mine": bool(own) and key == own,
            "is_tracked": key in tracked,
            "listings": len(brand_obs),
            "share_pct": share_by_key[key]["share_pct"] if key in share_by_key else 0.0,
            "best_position": min(positions) if positions else None,
            "avg_position": round1(sum(positions) / len(positions)) if positions else None,
            "median_price": round(statistics.median(prices), 2) if prices else None,
            "discount_rate_pct": discount_rate_pct(brand_obs),
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "total_reviews": sum(reviews) if reviews else None,
            "new_products": new_products.get(key, 0),
            "price_moves": price_moves.get(key, 0),
        })
    rows.sort(key=lambda r: (-r["share_pct"], not r["is_mine"], r["brand"].lower()))
    return rows


def _empty_dashboard(report: Row | None) -> dict[str, Any]:
    return {
        "kpis": {"observed_skus": 0, "new_products": 0, "demand_change_pct": None, "top_opportunity": None},
        "events": [],
        "shelf_share": [],
        "competitors": [],
        "opportunities": [],
        "has_demo_history": False,
        "report": report,
        "latest_scan_at": None,
        "scan_count": 0,
    }


def build_dashboard(market: Row, *, include_low_confidence: bool = False) -> dict[str, Any]:
    market_id = market["id"]
    report = repo.latest_report(market_id)
    shopping = repo.list_scans(market_id, "shopping")
    if not shopping:
        return _empty_dashboard(report)
    latest = shopping[-1]
    current = repo.observations_for_scan(latest["id"])
    opportunities = repo.opportunities_for_scan(latest["id"])  # read-only; scans and start-up backfill analyze

    latest_at = _parse_time(latest["scanned_at"])
    feed_since = (latest_at - timedelta(days=EVENT_WINDOW_DAYS)).isoformat()
    kpi_since = (latest_at - timedelta(days=KPI_WINDOW_DAYS)).isoformat()
    events = [
        e for e in repo.list_events(market_id, since=feed_since, limit=1000)
        if include_low_confidence or e["confidence"] != "low"
    ]
    events.sort(key=lambda e: (e["detected_at"], -SEVERITY_ORDER.get(e["severity"], 3)), reverse=True)
    recent = [e for e in events if e["detected_at"] >= kpi_since]

    share = shelf_share(current, market.get("brand_name"))
    trend_scan = trend_scan_for(market_id, latest["scanned_at"])
    window = shopping[-WINDOW_SCANS:]
    has_demo = any(s["is_demo"] for s in window) or bool(trend_scan and trend_scan["is_demo"])

    growths = [o["demand_growth"] for o in opportunities if o.get("demand_growth") is not None]
    top = opportunities[0] if opportunities else None
    kpis = {
        "observed_skus": len({o.get("canonical_key") or o.get("title") for o in current}),
        "new_products": sum(1 for e in recent if e["type"] == "NEW_PRODUCT"),
        "demand_change_pct": round1(sum(growths) / len(growths)) if growths else None,
        "top_opportunity": (
            {"keyword": top["keyword"], "score": top["score"], "label": top["label"]} if top else None
        ),
    }
    return {
        "kpis": kpis,
        "events": [format_event(e) for e in events[:MAX_FEED_EVENTS]],
        "shelf_share": [
            {"brand": row["brand"], "share_pct": row["share_pct"], "is_mine": row["is_mine"], "listings": row["listings"]}
            for row in collapse_tail(share, keep=10)
        ],
        "competitors": competitor_table(market, current, share, recent),
        "opportunities": [
            {
                "keyword": o["keyword"],
                "score": o["score"],
                "label": o["label"],
                "demand_growth": o.get("demand_growth"),
                "supply_growth": o.get("supply_growth"),
                "reasons": o.get("reasons") or [],
                "caveats": o.get("caveats") or [],
                "components": o.get("components") or {},
                "confidence": o.get("confidence") or {},
            }
            for o in opportunities
        ],
        "has_demo_history": has_demo,
        "report": report,
        "latest_scan_at": latest["scanned_at"],
        "scan_count": len(shopping),
    }
