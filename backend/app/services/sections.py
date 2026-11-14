"""Read-only section payloads for ``/markets/{id}/...``.

Every function here only reads stored rows and calls the deterministic ``intelligence`` modules
that the dashboard already uses; nothing is recalculated in the API layer or the UI, and nothing is
written. A market with no data returns an empty, well-formed payload (``has_data: false``) instead of
an error, so the UI can show one consistent empty state.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from app import repo
from app.intelligence.common import brand_key
from app.intelligence.demand import demand_by_keyword
from app.intelligence.events import probe_stability
from app.intelligence.pricing import price_stats
from app.intelligence.recommendations import build_recommendations
from app.intelligence.shelf_share import collapse_tail, shelf_share, shelf_share_by_probe
from app.intelligence.supply import supply_by_keyword, supply_growth_pct
from app.services.analytics_service import (
    HISTORY_SCANS,
    format_event,
    reference_scan,
    trend_scan_for,
)

Row = dict[str, Any]
MAX_PAGE = 200


class UnknownScanError(LookupError):
    """``scan_id`` does not name a shopping scan of this market."""


def pick_scan(market_id: int, scan_id: int | None) -> Row | None:
    """The requested shopping scan (Market Replay) or the latest one. Raises for an id of another market."""
    if scan_id is None:
        return repo.latest_scan(market_id, "shopping")
    scan = next((s for s in repo.list_scans(market_id, "shopping") if s["id"] == scan_id), None)
    if scan is None:
        raise UnknownScanError(scan_id)
    return scan


def _latest_shopping(market_id: int, scan_id: int | None = None) -> tuple[Row, list[Row]] | None:
    scan = pick_scan(market_id, scan_id)
    return (scan, repo.observations_for_scan(scan["id"])) if scan else None


def _scan_ref(scan: Row | None) -> Row | None:
    return {"id": scan["id"], "scanned_at": scan["scanned_at"], "is_demo": bool(scan["is_demo"])} if scan else None


def clamp_page(limit: int, offset: int, default: int = 50) -> tuple[int, int]:
    return max(1, min(limit or default, MAX_PAGE)), max(0, offset)


# --------------------------------------------------------------------------- scans / events


def scans_section(market: Row) -> Row:
    scans = repo.scans_with_counts(market["id"])
    return {
        "items": [{**scan, "is_demo": bool(scan["is_demo"])} for scan in scans],
        "total_credits": sum(int(scan["credits_used"]) for scan in scans if not scan["is_demo"]),
    }


def events_section(
    market: Row, *, types: list[str], include_low_confidence: bool, limit: int, offset: int, scan_id: int | None = None
) -> Row:
    limit, offset = clamp_page(limit, offset)
    if scan_id is not None:
        pick_scan(market["id"], scan_id)  # validates ownership; replay shows what that scan detected
    rows, total, hidden = repo.events_page(
        market["id"], types=types, include_low_confidence=include_low_confidence, scan_id=scan_id,
        limit=limit, offset=offset,
    )
    return {
        "items": [format_event(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "hidden_low_confidence": hidden,
        "include_low_confidence": include_low_confidence,
    }


# --------------------------------------------------------------------------- scanner


def observations_section(
    market: Row, *, keyword: str | None, brand: str | None, search: str | None, sort: str, limit: int, offset: int,
    scan_id: int | None = None,
) -> Row:
    latest = pick_scan(market["id"], scan_id)
    if latest is None:
        return {"has_data": False, "scan": None, "items": [], "total": 0, "limit": limit, "offset": offset,
                "keywords": market["keywords"], "brands": []}
    limit, offset = clamp_page(limit, offset)
    rows, total = repo.observations_page(
        latest["id"], keyword=keyword, brand=brand, search=search, sort=sort, limit=limit, offset=offset
    )
    brands = sorted(
        {o["brand"] for o in repo.observations_for_scan(latest["id"]) if o.get("brand")}, key=str.lower
    )
    return {
        "has_data": True, "scan": _scan_ref(latest), "items": rows, "total": total, "limit": limit,
        "offset": offset, "keywords": market["keywords"], "brands": brands,
    }


def product_section(market: Row, product_row_id: int) -> Row | None:
    product = repo.get_product(market["id"], product_row_id)
    if product is None:
        return None
    listings = repo.product_listings(product_row_id)
    per_scan: dict[int, Row] = {}
    for item in listings:  # one point per scan: the best-positioned listing, with the lowest price shown
        point = per_scan.setdefault(item["scan_id"], {
            "scan_id": item["scan_id"], "scanned_at": item["scanned_at"], "is_demo": bool(item["is_demo"]),
            "best_position": None, "lowest_price": None, "listings": 0, "retailers": set(),
        })
        point["listings"] += 1
        if item.get("source"):
            point["retailers"].add(item["source"])
        if isinstance(item.get("position"), int):
            point["best_position"] = min(filter(None, [point["best_position"], item["position"]]))
        if isinstance(item.get("price"), (int, float)) and item["price"] > 0:
            point["lowest_price"] = min(filter(None, [point["lowest_price"], item["price"]]))
    history = [{**p, "retailers": sorted(p["retailers"])} for p in per_scan.values()]
    return {"product": product, "history": history, "listings_stored": len(listings)}


# --------------------------------------------------------------------------- demand


def demand_section(market: Row, scan_id: int | None = None) -> Row:
    if scan_id is not None:  # replay: the Trends snapshot that was current at that shopping scan
        shopping = pick_scan(market["id"], scan_id)
        trend_scan = trend_scan_for(market["id"], shopping["scanned_at"]) if shopping else None
    else:
        trend_scan = repo.latest_scan(market["id"], "trends")
    if trend_scan is None:
        return {"has_data": False, "scan": None, "keywords": []}
    points = repo.trend_points_for_scan(trend_scan["id"])
    signals = demand_by_keyword(points)
    keywords = []
    for keyword in market["keywords"]:
        signal = signals.get(keyword.lower())
        if signal is None:
            keywords.append({"keyword": keyword, "signal": None, "series": []})
            continue
        batch = signal.get("batch_id")
        series = [
            {"week_start": p["week_start"], "interest": p["interest"], "is_partial": bool(p["is_partial"])}
            for p in points if p["keyword"].lower() == keyword.lower() and str(p["batch_id"]) == str(batch)
        ]
        keywords.append({"keyword": keyword, "signal": signal, "series": series})
    return {"has_data": True, "scan": _scan_ref(trend_scan), "keywords": keywords}


# --------------------------------------------------------------------------- shelf / supply / pricing


def _history_for(market_id: int, latest: Row) -> tuple[list[Row], Row | None]:
    shopping = repo.list_scans(market_id, "shopping")
    position = next(i for i, s in enumerate(shopping) if s["id"] == latest["id"])
    return shopping[max(0, position - HISTORY_SCANS):position], reference_scan(
        shopping[:position], datetime.fromisoformat(latest["scanned_at"])
    )


def shelf_section(market: Row, scan_id: int | None = None) -> Row:
    found = _latest_shopping(market["id"], scan_id)
    if found is None:
        return {"has_data": False, "scan": None, "overall": [], "by_probe": {}, "stability": {}}
    latest, current = found
    prior, _ = _history_for(market["id"], latest)
    history = [repo.observations_for_scan(s["id"]) for s in prior]
    own = market.get("brand_name")
    return {
        "has_data": True,
        "scan": _scan_ref(latest),
        "overall": collapse_tail(shelf_share(current, own), keep=10),
        "by_probe": {kw: collapse_tail(rows, keep=8) for kw, rows in shelf_share_by_probe(current, own).items()},
        "stability": probe_stability(current, history),
        "scans_compared": len(history) + 1,
        "method": "Position-weighted visibility, 1 / (1 + 0.1 x (position - 1)); a sampled shelf, not market share.",
    }


def supply_section(market: Row, scan_id: int | None = None) -> Row:
    found = _latest_shopping(market["id"], scan_id)
    if found is None:
        return {"has_data": False, "scan": None, "probes": []}
    latest, current = found
    prior, reference = _history_for(market["id"], latest)
    now = supply_by_keyword(current)
    before = supply_by_keyword(repo.observations_for_scan(reference["id"])) if reference else {}
    probes = []
    for keyword in market["keywords"]:
        metrics = now.get(keyword) or now.get(keyword.lower())
        if metrics is None:
            continue
        previous = before.get(keyword) or before.get(keyword.lower())
        probes.append({"keyword": keyword, **metrics, "sku_growth_pct": supply_growth_pct(metrics, previous)})
    return {"has_data": True, "scan": _scan_ref(latest), "probes": probes,
            "compared_with": _scan_ref(reference)}


def pricing_section(market: Row, scan_id: int | None = None) -> Row:
    found = _latest_shopping(market["id"], scan_id)
    if found is None:
        return {"has_data": False, "scan": None, "overall": None, "probes": [], "brands": []}
    latest, current = found
    by_keyword: dict[str, list[Row]] = defaultdict(list)
    for obs in current:
        by_keyword[(obs.get("keyword") or "").lower()].append(obs)
    overall = price_stats(current)
    own = brand_key(market.get("brand_name"))
    brands = [
        {"brand": brand, "median_price": median,
         "discount_rate_pct": overall["per_brand_discount_rate_pct"].get(brand),
         "is_mine": bool(own) and brand_key(brand) == own}
        for brand, median in sorted(overall["per_brand_median"].items(), key=lambda item: item[1])
    ]
    probes = [{"keyword": kw, **price_stats(by_keyword.get(kw.lower(), []))} for kw in market["keywords"]]
    for probe in probes:  # the per-brand maps are served once, in `brands`
        probe.pop("per_brand_median", None)
        probe.pop("per_brand_discount_rate_pct", None)
    overall = {k: v for k, v in overall.items() if k not in {"per_brand_median", "per_brand_discount_rate_pct"}}
    return {"has_data": True, "scan": _scan_ref(latest), "overall": overall, "probes": probes, "brands": brands}


# --------------------------------------------------------------------------- opportunities / reports


def opportunities_section(market: Row, scan_id: int | None = None) -> Row:
    latest = pick_scan(market["id"], scan_id)
    if latest is None:
        return {"has_data": False, "scan": None, "items": []}
    trend_scan = trend_scan_for(market["id"], latest["scanned_at"])
    items = [
        {
            "keyword": o["keyword"], "score": o["score"], "label": o["label"],
            "demand_growth": o.get("demand_growth"), "supply_growth": o.get("supply_growth"),
            "reasons": o.get("reasons") or [], "caveats": o.get("caveats") or [],
            "components": o.get("components") or {}, "confidence": o.get("confidence") or {},
        }
        for o in repo.opportunities_for_scan(latest["id"])
    ]
    return {"has_data": bool(items), "scan": _scan_ref(latest), "trends_scan": _scan_ref(trend_scan), "items": items}


def reports_section(market: Row) -> Row:
    return {"items": repo.list_reports(market["id"])}


# --------------------------------------------------------------------------- replay / alerts


def replay_section(market: Row) -> Row:
    """Every stored shopping scan, oldest first, for the Market Replay slider."""
    scans = [s for s in repo.scans_with_counts(market["id"]) if s["kind"] == "shopping"]
    scans.sort(key=lambda s: (s["scanned_at"], s["id"]))
    event_counts = {s["id"]: len(repo.events_for_scan(s["id"])) for s in scans}
    return {"scans": [
        {"id": s["id"], "scanned_at": s["scanned_at"], "is_demo": bool(s["is_demo"]), "products": s["products"],
         "events": event_counts[s["id"]]}
        for s in scans
    ]}


def alerts_section(market: Row, *, unread_only: bool, limit: int, offset: int) -> Row:
    limit, offset = clamp_page(limit, offset)
    rows, total, unread = repo.alerts_page(market["id"], unread_only=unread_only, limit=limit, offset=offset)
    return {"items": rows, "total": total, "unread": unread, "limit": limit, "offset": offset}


def recommendations_section(market: Row, scan_id: int | None = None) -> Row:
    """Recommended actions for one scan. Built only from stored data via ``build_recommendations``: no
    SerpAPI call, no model, no writes."""
    latest = pick_scan(market["id"], scan_id)
    if latest is None:
        return {"has_data": False, "scan": None, "items": [], "notes": []}
    opportunities = opportunities_section(market, scan_id)["items"]
    if not opportunities:
        return {"has_data": False, "scan": _scan_ref(latest), "items": [], "notes": []}
    demand = {item["keyword"].lower(): item for item in demand_section(market, scan_id).get("keywords", [])}
    pricing = {item["keyword"].lower(): item for item in pricing_section(market, scan_id).get("probes", [])}
    supply = {item["keyword"].lower(): item for item in supply_section(market, scan_id).get("probes", [])}
    stability = shelf_section(market, scan_id).get("stability", {})
    shopping = repo.list_scans(market["id"], "shopping")
    position = next(i for i, scan in enumerate(shopping) if scan["id"] == latest["id"])
    window = shopping[max(0, position - HISTORY_SCANS): position + 1]
    items, notes = build_recommendations(
        opportunities=opportunities, demand=demand, pricing=pricing, supply=supply, stability=stability,
        events=repo.events_for_scan(latest["id"]), scan_count=position + 1,
        uses_demo_data=any(scan["is_demo"] for scan in window),
    )
    return {"has_data": True, "scan": _scan_ref(latest), "items": items, "notes": notes}
