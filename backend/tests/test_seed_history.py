import asyncio
import random
from datetime import datetime, timezone

import pytest

from app import repo
from app.db import initialize_database
from app.seed_history import SeedHistoryError, build_demo_trends, plan_perturbations, seed_history
from app.services.analytics_service import build_dashboard
from app.services.scan import run_scan

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def scanned_market(fake_serpapi):
    """A generic market with exactly one real (fake-served) scan."""
    initialize_database()
    user_id = repo.create_user("owner@example.com", "Owner", "x$y")
    market_id = repo.create_market(
        user_id, name="Shoes", country="US", category="Running shoes", brand_name="Brooks",
        keywords=["running shoes", "trail running shoes"], competitors=["Nike"],
    )
    fake_serpapi.trends_error = "Trends temporarily unavailable"  # no real trends -> demo trends
    asyncio.run(run_scan(repo.get_market(market_id), now=NOW))
    return repo.get_market(market_id)


def test_requires_a_real_scan_first():
    initialize_database()
    user_id = repo.create_user("owner@example.com", "Owner", "x$y")
    market_id = repo.create_market(user_id, name="Empty", country="US", category="Kites", brand_name=None,
                                   keywords=["kites"], competitors=[])
    with pytest.raises(SeedHistoryError):
        seed_history(market_id)


def test_seed_history_creates_flagged_backdated_scans_and_events(scanned_market):
    result = seed_history(scanned_market["id"], weeks=4)
    assert result["created"] is True and result["demo_scans"] == 4
    scans = repo.list_scans(scanned_market["id"], "shopping")
    assert [s["is_demo"] for s in scans] == [1, 1, 1, 1, 0]  # demo weeks precede the real scan
    assert scans[0]["scanned_at"] < scans[-1]["scanned_at"]
    for scan in scans[:-1]:
        rows = repo.observations_for_scan(scan["id"])
        assert rows and all(row["is_demo"] == 1 for row in rows)
    trends = repo.list_scans(scanned_market["id"], "trends")
    assert len(trends) == 1 and trends[0]["is_demo"] == 1 and result["trend_points"] == 2 * 52

    dashboard = build_dashboard(scanned_market, include_low_confidence=True)
    assert dashboard["has_demo_history"] is True
    types = {event["type"] for event in dashboard["events"]}
    assert "NEW_PRODUCT" in types
    assert types & {"PRICE_DECREASE", "PRICE_INCREASE"}
    assert dashboard["kpis"]["new_products"] >= 1
    assert dashboard["kpis"]["demand_change_pct"] is not None
    assert any(o["demand_growth"] is not None for o in dashboard["opportunities"])
    assert not any("demo" in c.lower() for o in dashboard["opportunities"] for c in o["caveats"])  # no demo wording shown
    # changes measured against demo weeks are demo data too, even on the real scan
    assert dashboard["events"] and all(event["is_demo"] for event in dashboard["events"])
    # supply growth compares with the demo scan one week earlier, not "the previous scan"
    assert any("vs 7 days earlier" in reason for o in dashboard["opportunities"] for reason in o["reasons"])


def test_seed_history_is_idempotent_unless_forced(scanned_market):
    seed_history(scanned_market["id"])
    again = seed_history(scanned_market["id"])
    assert again["created"] is False
    assert len(repo.list_scans(scanned_market["id"], "shopping")) == 5
    forced = seed_history(scanned_market["id"], weeks=2, force=True)
    assert forced["created"] is True
    assert [s["is_demo"] for s in repo.list_scans(scanned_market["id"], "shopping")] == [1, 1, 0]


def test_plan_never_hides_own_brand_and_trends_have_one_riser():
    base = [
        {"canonical_key": f"k{i}", "brand": "Mine" if i < 3 else f"B{i % 7}", "keyword": "k", "position": i + 1,
         "price": 10.0 + i, "old_price": None, "product_id": str(i), "source": "S"}
        for i in range(40)
    ]
    plan = plan_perturbations(base, random.Random(1), own_brand="mine")
    hidden = plan["entering"] | plan["new_brand_keys"]
    assert not hidden & {"k0", "k1", "k2"}
    assert plan["entering"] and plan["price_shift"]

    points = build_demo_trends(["a", "b", "c"], NOW, random.Random(3), "demo-1")
    assert len(points) == 3 * 52 and max(p["interest"] for p in points) == 100
    assert all(p["week_start"] < "2026-10-11" for p in points)
