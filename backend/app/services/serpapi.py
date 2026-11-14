"""SerpAPI access with a disk cache.

This is the only module that talks to SerpAPI. Every response is cached on disk by its
request parameters (the API key is never part of the cache key or the cached file), so
repeated calls inside the cache window cost no credits. ``force_refresh`` bypasses it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.db import database_path

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
TRENDS_BATCH_SIZE = 5  # Google Trends compares at most five queries per request
NO_RESULTS_MARKERS = ("hasn't returned any results", "has not returned any results")

# Google uses "uk" for the United Kingdom in gl, while Trends geo uses ISO "GB".
_GL_OVERRIDES = {"GB": "uk"}


class SerpApiConfigurationError(RuntimeError):
    pass


class SerpApiError(RuntimeError):
    """Any SerpAPI failure the caller should report (HTTP 502 from the API)."""


class SerpApiRateLimitError(SerpApiError):
    """HTTP 429 (per-hour throughput limit). Retried with backoff; ``retry_after`` is in seconds."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class SerpApiTransientError(SerpApiError):
    """Timeouts, connection problems and HTTP 5xx. Retried with backoff."""


class SerpApiQuotaError(SerpApiError):
    """The plan's searches are used up or the key is rejected. Retrying cannot help, so it never is."""


@dataclass
class SerpResponse:
    payload: dict[str, Any]
    from_cache: bool
    fetched_at: str

    @property
    def credits(self) -> int:
        return 0 if self.from_cache else 1


# --------------------------------------------------------------------------- params


def shopping_params(keyword: str, country: str) -> dict[str, str]:
    return {"engine": "google_shopping", "q": keyword, "gl": _gl(country), "hl": "en"}


def autocomplete_params(query: str, country: str) -> dict[str, str]:
    return {"engine": "google_autocomplete", "q": query, "gl": _gl(country), "hl": "en"}


def trends_params(keywords: list[str], country: str, date_range: str | None = None) -> dict[str, str]:
    if not 1 <= len(keywords) <= TRENDS_BATCH_SIZE:
        raise ValueError(f"A Google Trends batch needs 1-{TRENDS_BATCH_SIZE} keywords, got {len(keywords)}")
    return {
        "engine": "google_trends",
        "q": ",".join(keywords),
        "data_type": "TIMESERIES",
        "geo": country.upper(),
        "date": date_range or get_settings().trends_date_range,
        "hl": "en",
    }


def trends_batches(keywords: list[str]) -> list[list[str]]:
    return [keywords[i:i + TRENDS_BATCH_SIZE] for i in range(0, len(keywords), TRENDS_BATCH_SIZE)]


def _gl(country: str) -> str:
    return _GL_OVERRIDES.get(country.upper(), country.lower())


# --------------------------------------------------------------------------- cache


def cache_dir() -> Path:
    configured = get_settings().serpapi_cache_dir
    return Path(configured) if configured else database_path().parent / "serpapi_cache"


def cache_key(params: dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if k != "api_key"}
    return hashlib.sha256(json.dumps(clean, sort_keys=True).encode("utf-8")).hexdigest()


def _cache_path(params: dict[str, Any]) -> Path:
    engine = str(params.get("engine", "unknown")).replace("/", "_")
    return cache_dir() / engine / f"{cache_key(params)}.json"


def cache_get(params: dict[str, Any], max_age: timedelta | None = None) -> dict[str, Any] | None:
    """Cached entry ``{cached_at, params, payload}`` or None when missing or older than ``max_age``."""
    path = _cache_path(params)
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(entry["cached_at"])
    except (OSError, ValueError, KeyError):
        return None
    if max_age is not None and datetime.now(timezone.utc) - cached_at > max_age:
        return None
    return entry


def cache_put(params: dict[str, Any], payload: dict[str, Any]) -> str:
    path = _cache_path(params)
    path.parent.mkdir(parents=True, exist_ok=True)
    cached_at = datetime.now(timezone.utc).isoformat()
    clean = {k: v for k, v in params.items() if k != "api_key"}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cached_at": cached_at, "params": clean, "payload": payload}), encoding="utf-8")
    tmp.replace(path)
    return cached_at


def is_cached(params: dict[str, Any], max_age: timedelta | None = None) -> bool:
    return cache_get(params, max_age) is not None


# --------------------------------------------------------------------------- network


QUOTA_MARKERS = ("run out of searches", "out of searches", "searches for the month", "monthly", "invalid api key")


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        return None


async def _http_get(params: dict[str, Any]) -> dict[str, Any]:
    """The single network call. Tests replace this function. Raises typed errors so ``_fetch`` knows
    which failures are worth retrying."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(SERPAPI_URL, params=params)
    except httpx.TimeoutException as error:
        raise SerpApiTransientError("SerpAPI did not answer in time.") from error
    except httpx.TransportError as error:
        raise SerpApiTransientError(f"Could not reach SerpAPI ({type(error).__name__}).") from error
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    detail = str(payload.get("error", "")) if isinstance(payload, dict) else ""
    if response.status_code in (401, 403) or (response.status_code == 429 and any(m in detail.lower() for m in QUOTA_MARKERS)):
        raise SerpApiQuotaError(f"SerpAPI rejected the request: {detail or 'check the API key and plan'}")
    if response.status_code == 429:
        raise SerpApiRateLimitError("SerpAPI is rate limiting requests.", _retry_after(response))
    if response.status_code >= 500:
        raise SerpApiTransientError(f"SerpAPI returned HTTP {response.status_code}.")
    if not isinstance(payload, dict) or (not payload and response.status_code != 200):
        raise SerpApiError(f"SerpAPI returned HTTP {response.status_code} without JSON")
    if response.status_code >= 400 and not payload.get("error"):
        raise SerpApiError(f"SerpAPI returned HTTP {response.status_code}")
    return payload


async def _sleep(seconds: float) -> None:
    """Indirection so tests can run retries without waiting."""
    await asyncio.sleep(seconds)


def _backoff_delay(attempt: int, error: SerpApiError) -> float:
    """Exponential delay (base * 2^(attempt-1)) with jitter, capped; honors Retry-After within the cap."""
    settings = get_settings()
    delay = min(settings.serpapi_backoff_cap_seconds, settings.serpapi_backoff_base_seconds * 2 ** (attempt - 1))
    delay += random.uniform(0, delay * 0.25)
    retry_after = getattr(error, "retry_after", None)
    if retry_after:
        delay = max(delay, retry_after)
    return min(delay, settings.serpapi_backoff_cap_seconds)


async def _fetch(params: dict[str, Any]) -> dict[str, Any]:
    """``_http_get`` with bounded retries on rate limits and transient failures only. A request that
    ultimately fails costs no credit, and the key is never logged."""
    max_attempts = max(1, get_settings().serpapi_max_attempts)
    engine = str(params.get("engine", "unknown"))
    for attempt in range(1, max_attempts + 1):
        try:
            return await _http_get(params)
        except (SerpApiRateLimitError, SerpApiTransientError) as error:
            if attempt == max_attempts:
                logger.error("serpapi_giving_up", extra={"engine": engine, "attempts": attempt, "error": type(error).__name__})
                raise
            delay = _backoff_delay(attempt, error)
            logger.warning(
                "serpapi_retry",
                extra={"engine": engine, "attempt": attempt, "delay_seconds": round(delay, 2), "error": type(error).__name__},
            )
            await _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


async def search(
    params: dict[str, Any],
    *,
    max_age: timedelta | None = None,
    force_refresh: bool = False,
) -> SerpResponse:
    if not force_refresh:
        entry = cache_get(params, max_age)
        if entry is not None:
            return SerpResponse(entry["payload"], True, entry["cached_at"])

    api_key = get_settings().serpapi_api_key
    if not api_key:
        raise SerpApiConfigurationError("SERPAPI_API_KEY is missing. Add it to backend/.env and restart the API.")
    payload = await _fetch({**params, "api_key": api_key})
    if error := payload.get("error"):
        if not any(marker in str(error) for marker in NO_RESULTS_MARKERS):
            raise SerpApiError(f"SerpAPI error: {error}")
        payload = {**payload, "error": None}  # "no results" is a valid, empty answer worth caching
    payload.pop("search_metadata", None)  # contains account-specific URLs; not needed
    fetched_at = cache_put(params, payload)
    return SerpResponse(payload, False, fetched_at)


async def shopping_search(keyword: str, country: str, **options: Any) -> list[dict[str, Any]]:
    """Backward-compatible helper: Google Shopping results for one keyword."""
    response = await search(shopping_params(keyword, country), **options)
    return list(response.payload.get("shopping_results") or [])


# --------------------------------------------------------------------------- parsing


def _interest(value: dict[str, Any]) -> float:
    extracted = value.get("extracted_value")
    if isinstance(extracted, (int, float)):
        return float(extracted)
    raw = str(value.get("value", "0")).strip()
    if raw.startswith("<"):
        return 0.0
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return 0.0


def parse_trends(
    payload: dict[str, Any],
    keywords: list[str],
    batch_id: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Flatten ``interest_over_time.timeline_data`` into one point per keyword per period.

    A point is marked partial when Google flags it (``partial_data``) or when its period
    has not finished yet at ``now``.
    """
    now = now or datetime.now(timezone.utc)
    timeline = (payload.get("interest_over_time") or {}).get("timeline_data") or []
    by_query = {keyword.lower(): keyword for keyword in keywords}
    stamps = [int(entry["timestamp"]) for entry in timeline if str(entry.get("timestamp", "")).isdigit()]
    interval_days = 7
    if len(stamps) >= 2:
        interval_days = max(1, round((stamps[1] - stamps[0]) / 86400))
    points = []
    for entry in timeline:
        stamp = str(entry.get("timestamp", ""))
        if not stamp.isdigit():
            continue
        start: date = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date()
        unfinished = start + timedelta(days=interval_days) > now.date()
        partial = bool(entry.get("partial_data")) or unfinished
        for value in entry.get("values") or []:
            keyword = by_query.get(str(value.get("query", "")).lower())
            if keyword is None:
                continue
            points.append({
                "keyword": keyword,
                "batch_id": batch_id,
                "week_start": start.isoformat(),
                "interest": _interest(value),
                "is_partial": partial,
            })
    return points
