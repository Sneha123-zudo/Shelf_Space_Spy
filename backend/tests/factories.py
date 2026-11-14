"""Hand-made SerpAPI-shaped payloads and observation rows. No network, no real credits."""

from __future__ import annotations

import zlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

# A generic, non-demo category on purpose: tests prove nothing is tied to the seeded market.
RUNNING_SHOES = [
    # (title, source, price, old_price, rating, reviews)
    ("Nike Pegasus 41 Road Running Shoes", "Nike", 130.0, None, 4.7, 5400),
    ("Hoka Clifton 9 Running Shoe", "Zappos", 145.0, None, 4.8, 3100),
    ("Brooks Ghost 16 Men's Running Shoes", "Amazon.com", 140.0, 160.0, 4.7, 8800),
    ("Asics Gel-Nimbus 26 Running Shoe", "Dick's Sporting Goods", 160.0, None, 4.6, 1200),
    ("New Balance Fresh Foam X 1080v13", "Zappos", 165.0, None, 4.5, 900),
    ("Saucony Ride 17 Running Shoes", "Amazon.com", 120.0, None, 4.6, 2100),
    ("Nike Vomero 17 Running Shoes", "Dick's Sporting Goods", 150.0, None, 4.4, 600),
    ("Hoka Bondi 8 Running Shoe", "REI", 165.0, None, 4.7, 4300),
    ("Brooks Adrenaline GTS 24", "REI", 140.0, None, 4.8, 7000),
    ("Lightweight Mesh Running Sneakers for Men", "Walmart", 29.99, 45.0, 4.1, 300),
    ("Asics Gel-Kayano 31 Running Shoe", "Amazon.com", 165.0, None, 4.7, 2500),
    ("On Cloudmonster Running Shoe", "Zappos", 170.0, None, 4.5, 1500),
]


def shopping_payload(keyword: str, rows: list[tuple] | None = None, id_prefix: str = "") -> dict[str, Any]:
    rows = RUNNING_SHOES if rows is None else rows
    results = []
    for i, (title, source, price, old_price, rating, reviews) in enumerate(rows, start=1):
        item = {
            "position": i,
            "title": title,
            "product_id": f"{id_prefix}{zlib.crc32(f'{title}|{keyword}'.encode())}",
            "source": source,
            "price": f"${price:,.2f}",
            "extracted_price": price,
            "rating": rating,
            "reviews": reviews,
            "product_link": f"https://example.test/p/{i}",
            "thumbnail": f"https://example.test/t/{i}.jpg",
        }
        if old_price:
            item["extracted_old_price"] = old_price
        results.append(item)
    return {"search_parameters": {"engine": "google_shopping", "q": keyword}, "shopping_results": results}


def weekly_series(weeks: int, base: float, recent_factor: float = 1.0) -> list[float]:
    """Flat series whose last four weeks are multiplied by ``recent_factor``."""
    return [base * (recent_factor if i >= weeks - 4 else 1.0) for i in range(weeks)]


def trends_payload(keywords: list[str], factors: dict[str, float] | None = None, weeks: int = 20,
                   end: date | None = None, partial_last: bool = True) -> dict[str, Any]:
    factors = factors or {}
    end = end or date(2026, 10, 4)  # a Sunday
    timeline = []
    series = {kw: weekly_series(weeks, 40.0, factors.get(kw, 1.0)) for kw in keywords}
    for i in range(weeks):
        week = end - timedelta(days=7 * (weeks - 1 - i))
        stamp = int(datetime(week.year, week.month, week.day, tzinfo=timezone.utc).timestamp())
        entry: dict[str, Any] = {
            "date": week.isoformat(),
            "timestamp": str(stamp),
            "values": [
                {"query": kw, "value": str(int(series[kw][i])), "extracted_value": int(series[kw][i])}
                for kw in keywords
            ],
        }
        timeline.append(entry)
    if partial_last:
        last = end + timedelta(days=7)
        timeline.append({
            "date": last.isoformat(),
            "timestamp": str(int(datetime(last.year, last.month, last.day, tzinfo=timezone.utc).timestamp())),
            "partial_data": True,
            "values": [{"query": kw, "value": "5", "extracted_value": 5} for kw in keywords],
        })
    return {"interest_over_time": {"timeline_data": timeline}}


def autocomplete_payload(values: list[str]) -> dict[str, Any]:
    return {"suggestions": [{"value": v, "relevance": 600 - i, "type": "QUERY"} for i, v in enumerate(values)]}


def obs(title: str, *, keyword: str = "probe", position: int = 1, price: float | None = 100.0,
        old_price: float | None = None, source: str = "Shop A", brand: str | None = None,
        product_id: str | None = None, canonical_key: str | None = None, reviews: int | None = None,
        rating: float | None = None, brand_inferred: bool = False) -> dict[str, Any]:
    """An already-normalized observation row as the analytics modules receive it."""
    return {
        "title": title, "keyword": keyword, "position": position, "price": price, "old_price": old_price,
        "source": source, "brand": brand, "product_id": product_id or f"pid-{title}",
        "canonical_key": canonical_key or f"key-{title}", "reviews": reviews, "rating": rating,
        "brand_inferred": brand_inferred,
    }
