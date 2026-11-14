"""Market scan pipeline: Google Shopping per probe + Google Trends, normalize, save, analyze.

Credit safety:
* A new live scan is refused while the previous live scan is younger than
  ``SCAN_MIN_INTERVAL_MINUTES`` unless ``force`` is set.
* Responses are disk-cached for the same window, so a forced re-run inside it replays
  cached data at zero credits; ``refresh`` bypasses the cache.
* ``dry_run`` only reports the estimated credit cost.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from app import repo
from app.core.config import get_settings
from app.services import serpapi
from app.services.alerts import evaluate_alerts
from app.services.analytics_service import analyze_scan, known_brands
from app.services.normalize import normalize_products

logger = logging.getLogger(__name__)

# Markets with a live scan running right now. The event loop is single-threaded, so the
# check-and-add below cannot interleave and two concurrent requests never both spend credits.
_in_flight: set[int] = set()


class ScanGuardError(RuntimeError):
    def __init__(self, message: str, *, minutes_since_last: float, estimate: dict[str, Any]):
        super().__init__(message)
        self.minutes_since_last = minutes_since_last
        self.estimate = estimate


class ScanInProgressError(ScanGuardError):
    pass


class BudgetExceededError(ScanGuardError):
    """The daily SerpAPI credit budget would be exceeded. ``force`` cannot override it."""


Progress = Callable[[int, int, str], None]


def day_start(now: datetime) -> datetime:
    utc = now.astimezone(timezone.utc)
    return utc.replace(hour=0, minute=0, second=0, microsecond=0)


def budget_status(now: datetime | None = None) -> dict[str, Any]:
    """Today's (UTC) live credit usage against the configured cap. ``daily_budget`` 0 means no cap."""
    now = now or datetime.now(timezone.utc)
    budget = max(0, get_settings().daily_credit_budget)
    used = repo.credits_used_since(day_start(now).isoformat())
    return {
        "daily_budget": budget,
        "credits_used_today": used,
        "budget_remaining": max(0, budget - used) if budget else None,
    }


def _cache_window() -> timedelta:
    return timedelta(minutes=max(1, get_settings().scan_min_interval_minutes))


def _planned_calls(market: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    shopping = [serpapi.shopping_params(keyword, market["country"]) for keyword in market["keywords"]]
    trends = [serpapi.trends_params(batch, market["country"]) for batch in serpapi.trends_batches(market["keywords"])]
    return shopping, trends


def estimate_scan(market: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
    shopping, trends = _planned_calls(market)
    window = _cache_window()
    calls = shopping + trends
    live = sum(1 for params in calls if refresh or not serpapi.is_cached(params, window))
    return {
        "shopping_calls": len(shopping),
        "trends_calls": len(trends),
        "estimated_credits": live,
        "cached_calls": len(calls) - live,
    }


def _minutes_since_last_live_scan(market_id: int, now: datetime) -> float | None:
    last = repo.latest_scan(market_id, "shopping", live_only=True)
    if last is None:
        return None
    return (now - datetime.fromisoformat(last["scanned_at"])).total_seconds() / 60.0


def shopping_row(product: dict[str, Any], keyword: str, fallback_position: int) -> dict[str, Any]:
    position = product.get("position")
    return {
        "keyword": keyword,
        "product_id": product.get("product_id"),
        "title": product.get("title") or "Untitled product",
        "source": product.get("source"),
        "price": product.get("extracted_price"),
        "old_price": product.get("extracted_old_price"),
        "position": position if isinstance(position, int) else fallback_position,
        "rating": product.get("rating"),
        "reviews": product.get("reviews"),
        "product_link": product.get("product_link") or product.get("link"),
        "thumbnail": product.get("thumbnail"),
    }


def plan_scan(
    market: dict[str, Any], *, force: bool, refresh: bool, now: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Gather the facts a scan decision needs: cost estimate, recency and today's credit budget.
    Returns (base response fields, estimate). Nothing is raised here; see :func:`enforce_scan_rules`."""
    settings = get_settings()
    estimate = estimate_scan(market, refresh=refresh)
    minutes_since = _minutes_since_last_live_scan(market["id"], now)
    guard_active = minutes_since is not None and minutes_since < settings.scan_min_interval_minutes
    budget = budget_status(now)
    base = {
        "market_id": market["id"],
        "keyword_count": len(market["keywords"]),
        "guard_active": guard_active,
        "minutes_since_last_scan": round(minutes_since, 1) if minutes_since is not None else None,
        **estimate,
        **budget,
    }
    return base, estimate


def enforce_scan_rules(base: dict[str, Any], estimate: dict[str, Any], *, force: bool) -> None:
    """Raise BudgetExceededError / ScanGuardError if a live scan must not start. The recency guard can
    be overridden with ``force``; the daily credit budget never can."""
    settings = get_settings()
    needed = estimate["estimated_credits"]
    if base["daily_budget"] and needed and base["credits_used_today"] + needed > base["daily_budget"]:
        raise BudgetExceededError(
            "The daily scan limit has been reached. It resets at midnight UTC; stored data is still available.",
            minutes_since_last=base["minutes_since_last_scan"] or 0.0,
            estimate=estimate,
        )
    if base["guard_active"] and not force:
        minutes = base["minutes_since_last_scan"] or 0.0
        raise ScanGuardError(
            f"The last live scan was {minutes:.0f} minute(s) ago (minimum interval "
            f"{settings.scan_min_interval_minutes} minutes). Re-run with force=true to scan anyway.",
            minutes_since_last=minutes,
            estimate=estimate,
        )


def precheck_scan(market: dict[str, Any], *, force: bool, refresh: bool, now: datetime | None = None) -> dict[str, Any]:
    """Validate a live scan before any work or job is created. Returns the cost/budget facts."""
    base, estimate = plan_scan(market, force=force, refresh=refresh, now=now or datetime.now(timezone.utc))
    enforce_scan_rules(base, estimate, force=force)
    return base


async def _run_scan(
    market: dict[str, Any],
    *,
    force: bool = False,
    dry_run: bool = False,
    refresh: bool = False,
    now: datetime | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    base, estimate = await asyncio.to_thread(plan_scan, market, force=force, refresh=refresh, now=now)
    if dry_run:
        return {
            **base, "dry_run": True, "scanned_at": None, "products_observed": 0,
            "message": "Dry run: nothing was fetched or saved.",
        }
    enforce_scan_rules(base, estimate, force=force)
    total_steps = estimate["shopping_calls"] + estimate["trends_calls"] + 1
    done_steps = 0

    def step(message: str) -> None:
        nonlocal done_steps
        done_steps += 1
        if progress:
            progress(done_steps, total_steps, message)

    if progress:
        progress(0, total_steps, "Starting scan")

    scanned_at = now.isoformat()
    window = _cache_window()
    credits = 0
    cached_calls = 0
    raw_rows: list[dict[str, Any]] = []
    for keyword in market["keywords"]:
        response = await serpapi.search(
            serpapi.shopping_params(keyword, market["country"]), max_age=window, force_refresh=refresh
        )
        credits += response.credits
        cached_calls += int(response.from_cache)
        step(f"Searched Google Shopping for \"{keyword}\"")
        results = response.payload.get("shopping_results") or []
        raw_rows.extend(shopping_row(product, keyword, i + 1) for i, product in enumerate(results))

    def _persist_shopping() -> tuple[list[dict[str, Any]], int]:
        rows = normalize_products(raw_rows, known_brands(market), repo.product_history(market["id"]))
        return rows, repo.save_shopping_scan(market["id"], scanned_at, rows, credits_used=credits)

    products, shopping_scan_id = await asyncio.to_thread(_persist_shopping)

    warnings: list[str] = []
    trend_points: list[dict[str, Any]] = []
    trends_credits = 0
    try:
        for index, batch in enumerate(serpapi.trends_batches(market["keywords"])):
            response = await serpapi.search(
                serpapi.trends_params(batch, market["country"]), max_age=window, force_refresh=refresh
            )
            trends_credits += response.credits
            cached_calls += int(response.from_cache)
            step("Fetched Google Trends")
            trend_points.extend(serpapi.parse_trends(response.payload, batch, f"{shopping_scan_id}-{index}", now))
    except (serpapi.SerpApiError, serpapi.SerpApiConfigurationError) as error:
        warnings.append(f"Google Trends unavailable, demand uses the previous trends data if any: {error}")
    if trend_points:
        await asyncio.to_thread(
            repo.save_trends_scan, market["id"], scanned_at, trend_points, credits_used=trends_credits
        )
    elif not warnings:
        warnings.append("Google Trends returned no data for these keywords.")
    credits += trends_credits

    if progress:
        progress(done_steps, total_steps, "Analyzing changes")
    analysis = await asyncio.to_thread(analyze_scan, market["id"], shopping_scan_id)
    alerts_created = await asyncio.to_thread(evaluate_alerts, market["id"], shopping_scan_id)
    step("Done")
    message = (
        "Scan completed from recently cached results; pass refresh=true for fresh data."
        if credits == 0 else
        "Live scan completed."
    )
    return {
        **base,
        "dry_run": False,
        "scan_id": shopping_scan_id,
        "scanned_at": scanned_at,
        "products_observed": len(products),
        "products": len(products),
        "trend_points": len(trend_points),
        "events": len(analysis["events"]),
        "opportunities": len(analysis["opportunities"]),
        "alerts_created": alerts_created,
        "credits_used": credits,
        "cached_calls": cached_calls,
        "warnings": warnings,
        "message": message,
    }


async def run_scan(
    market: dict[str, Any],
    *,
    force: bool = False,
    dry_run: bool = False,
    refresh: bool = False,
    now: datetime | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Public entry point: one live scan per market at a time (dry runs are always allowed)."""
    market_id = int(market["id"])
    if dry_run:
        return await _run_scan(market, force=force, dry_run=True, refresh=refresh, now=now)
    if market_id in _in_flight:
        raise ScanInProgressError(
            "A scan for this market is already running. Wait for it to finish.",
            minutes_since_last=0.0,
            estimate=estimate_scan(market, refresh=refresh),
        )
    _in_flight.add(market_id)
    started = time.perf_counter()
    logger.info("scan_started", extra={"market_id": market_id, "keywords": len(market["keywords"]), "refresh": refresh})
    try:
        result = await _run_scan(market, force=force, dry_run=False, refresh=refresh, now=now, progress=progress)
    except Exception as error:
        logger.warning("scan_failed", extra={"market_id": market_id, "error": type(error).__name__})
        raise
    finally:
        _in_flight.discard(market_id)
    logger.info("scan_finished", extra={
        "market_id": market_id, "scan_id": result.get("scan_id"), "credits_used": result.get("credits_used"),
        "cached_calls": result.get("cached_calls"), "products": result.get("products"),
        "events": result.get("events"), "duration_s": round(time.perf_counter() - started, 2),
    })
    return result
