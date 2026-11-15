"""Fields the frontend renders: competitor discount rate and top-level event change_pct."""

import asyncio
from datetime import datetime, timezone

import pytest

from app import repo
from app.db import initialize_database
from app.intelligence.pricing import discount_rate_pct, price_stats
from app.schemas import DashboardResponse
from app.seed_history import seed_history
from app.services.analytics_service import build_dashboard, competitor_table, event_change_pct, format_event
from app.services.scan import run_scan
from tests.factories import obs

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def test_discount_rate_pct_is_percent_points_of_priced_listings():
    rows = [
        obs("A", price=80.0, old_price=100.0),   # discounted
        obs("B", price=100.0),
        obs("C", price=90.0, old_price=90.0),    # same list price is not a discount
        obs("D", price=50.0, old_price=70.0),    # discounted
        obs("E", price=None, old_price=70.0),    # unpriced: not part of the base
    ]
    assert discount_rate_pct(rows) == 50.0
    assert discount_rate_pct([obs("X", price=None)]) is None
    assert discount_rate_pct([]) is None
    stats = price_stats([obs("A", brand="Alpha", price=80.0, old_price=100.0), obs("B", brand="Alpha", price=10.0),
                         obs("C", brand="Beta", price=20.0)])
    assert stats["per_brand_discount_rate_pct"] == {"Alpha": 50.0, "Beta": 0.0}
    assert price_stats([])["per_brand_discount_rate_pct"] == {}


def test_competitor_table_reports_discount_rate_per_brand():
    market = {"brand_name": "Mine", "competitors": ["Alpha", "Ghost"]}
    current = [
        obs("A1", brand="Alpha", price=80.0, old_price=100.0, position=1),
        obs("A2", brand="Alpha", price=90.0, position=2),
        obs("A3", brand="Alpha", price=95.0, position=3),
        obs("A4", brand="Alpha", price=70.0, old_price=75.0, position=4),
        obs("M1", brand="Mine", price=40.0, position=5),
        obs("M2", brand="Mine", price=None, position=6),
    ]
    share = [
        {"brand": "Alpha", "share_pct": 70.0, "is_mine": False, "is_unbranded": False},
        {"brand": "Mine", "share_pct": 30.0, "is_mine": True, "is_unbranded": False},
    ]
    rows = {row["brand"]: row for row in competitor_table(market, current, share, [])}
    assert rows["Alpha"]["discount_rate_pct"] == 50.0
    assert rows["Mine"]["discount_rate_pct"] == 0.0           # one priced listing, not discounted
    assert rows["Ghost"]["discount_rate_pct"] is None         # tracked but absent: no basis
    assert rows["Alpha"]["new_products"] == 0 and rows["Alpha"]["avg_position"] == 2.5


def _stored(kind: str, **metadata):
    return {"id": 1, "type": kind, "severity": "low", "confidence": "high", "brand": "Alpha", "product": "A",
            "keyword": "probe", "previous_value": 100.0, "current_value": 80.0,
            "detected_at": "2026-10-01T00:00:00+00:00", "is_demo": 0,
            "metadata": {"summary": "s", "caveat": "c", **metadata}}


@pytest.mark.parametrize(("kind", "metadata", "expected"), [
    ("PRICE_DECREASE", {"change_pct": -20.0}, -20.0),
    ("PRICE_INCREASE", {"change_pct": 12.345}, 12.3),
    ("DISCOUNT_STARTED", {"discount_pct": 20.0}, -20.0),
    ("RANK_GAIN", {"positions_moved": 4}, None),
    ("NEW_PRODUCT", {"listings": 2}, None),
])
def test_event_change_pct_is_lifted_from_evidence(kind, metadata, expected):
    assert event_change_pct(kind, metadata) == expected
    formatted = format_event(_stored(kind, **metadata))
    assert formatted["change_pct"] == expected
    assert formatted["summary"] == "s" and formatted["caveat"] == "c"
    assert "summary" not in formatted["evidence"] and "caveat" not in formatted["evidence"]


def test_seeded_dashboard_carries_change_pct_and_discount_rate(fake_serpapi):
    initialize_database()
    user_id = repo.create_user("owner@example.com", "Owner", "x$y")
    market_id = repo.create_market(
        user_id, name="Shoes", country="US", category="Running shoes", brand_name="Brooks",
        keywords=["running shoes", "trail running shoes"], competitors=["Nike"],
    )
    fake_serpapi.trends_error = "Trends temporarily unavailable"
    asyncio.run(run_scan(repo.get_market(market_id), now=NOW))
    seed_history(market_id, weeks=4)

    dashboard = build_dashboard(repo.get_market(market_id), include_low_confidence=True)
    DashboardResponse.model_validate(dashboard)  # the response model accepts the payload as built

    price_events = [e for e in dashboard["events"] if e["type"] in {"PRICE_DECREASE", "PRICE_INCREASE"}]
    assert price_events
    for event in price_events:
        assert event["change_pct"] == event["evidence"]["change_pct"]
        assert (event["change_pct"] < 0) == (event["type"] == "PRICE_DECREASE")
    for event in dashboard["events"]:
        assert "change_pct" in event
        if event["type"] in {"RANK_GAIN", "RANK_LOSS", "NEW_PRODUCT", "NEW_COMPETITOR"}:
            assert event["change_pct"] is None

    assert dashboard["competitors"]
    for row in dashboard["competitors"]:
        assert "discount_rate_pct" in row and "new_products" in row and "avg_position" in row
        if row["discount_rate_pct"] is not None:
            assert 0.0 <= row["discount_rate_pct"] <= 100.0
    brooks = next(row for row in dashboard["competitors"] if row["brand"] == "Brooks")
    assert brooks["listings"] > 0 and brooks["discount_rate_pct"] is not None
