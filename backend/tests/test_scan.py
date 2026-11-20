import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app import repo
from app.db import initialize_database
from app.services.scan import ScanGuardError, run_scan
from tests import factories

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def market():
    initialize_database()
    user_id = repo.create_user("owner@example.com", "Owner", "x$y")
    market_id = repo.create_market(
        user_id, name="Camping", country="US", category="Tents", brand_name="Hoka",
        keywords=["running shoes", "trail running shoes"], competitors=["Nike", "Brooks"],
    )
    return repo.get_market(market_id)


def scan(market, **kwargs):
    return asyncio.run(run_scan(market, **kwargs))


def test_dry_run_estimates_without_spending_or_writing(market, fake_serpapi):
    result = scan(market, dry_run=True, now=NOW)
    assert result["dry_run"] is True
    assert result["estimated_credits"] == 3  # 2 shopping probes + 1 trends batch
    assert (result["shopping_calls"], result["trends_calls"]) == (2, 1)
    assert fake_serpapi.calls == []
    assert repo.list_scans(market["id"]) == []


def test_scan_saves_normalized_products_trends_and_analysis(market, fake_serpapi):
    fake_serpapi.trends_factors = {"trail running shoes": 1.5}
    result = scan(market, now=NOW)
    assert result["credits_used"] == 3 and result["cached_calls"] == 0
    assert result["products"] == result["products_observed"] == 2 * len(factories.RUNNING_SHOES)
    assert result["trend_points"] > 0 and result["opportunities"] == 2
    assert result["events"] == 0  # first scan: nothing to compare with
    scans = repo.list_scans(market["id"])
    assert {s["kind"] for s in scans} == {"shopping", "trends"} and not any(s["is_demo"] for s in scans)
    rows = repo.observations_for_scan(result["scan_id"])
    assert all(row["canonical_key"] for row in rows)
    brands = {row["brand"] for row in rows}
    assert {"Nike", "Hoka", "Brooks"} <= brands
    assert any(row["brand_inferred"] for row in rows)  # e.g. Saucony is only guessed
    opportunities = repo.opportunities_for_scan(result["scan_id"])
    trail = next(o for o in opportunities if o["keyword"] == "trail running shoes")
    assert trail["demand_growth"] == 50.0


def test_guard_refuses_recent_scan_unless_forced_and_force_replays_cache(market, fake_serpapi):
    scan(market, now=NOW)
    with pytest.raises(ScanGuardError) as info:
        scan(market, now=NOW + timedelta(minutes=10))
    assert "force=true" in str(info.value)
    forced = scan(market, force=True, now=NOW + timedelta(minutes=10))
    assert forced["credits_used"] == 0 and forced["cached_calls"] == 3
    assert len(fake_serpapi.calls) == 3
    dry = scan(market, dry_run=True, now=NOW + timedelta(minutes=11))
    assert dry["guard_active"] is True and dry["estimated_credits"] == 0


def test_trends_failure_keeps_the_shopping_scan(market, fake_serpapi):
    fake_serpapi.trends_error = "Google Trends hasn't loaded properly"
    result = scan(market, now=NOW)
    assert result["products"] > 0 and result["trend_points"] == 0
    assert result["warnings"] and "Google Trends unavailable" in result["warnings"][0]
    assert result["credits_used"] == 2
    assert [s["kind"] for s in repo.list_scans(market["id"])] == ["shopping"]


def test_reference_scan_prefers_a_week_old_scan():
    from app.services.analytics_service import reference_scan

    scans = [{"id": i, "scanned_at": (NOW - timedelta(days=d)).isoformat()} for i, d in enumerate([14, 7, 0.01])]
    assert reference_scan(scans, NOW)["id"] == 1
    assert reference_scan(scans[2:], NOW)["id"] == 2  # nothing old enough: previous scan
    assert reference_scan([], NOW) is None


def test_second_real_scan_detects_changes(market, fake_serpapi):
    scan(market, now=NOW)
    scan(market, now=NOW + timedelta(days=7))
    changed = [list(row) for row in factories.RUNNING_SHOES]
    changed[0][2] = 99.0  # Nike Pegasus price cut at the same retailer
    for keyword in market["keywords"]:
        fake_serpapi.shopping[keyword] = factories.shopping_payload(keyword, [tuple(r) for r in changed])
    result = scan(market, refresh=True, now=NOW + timedelta(days=14))
    assert result["credits_used"] == 3
    events = repo.list_events(market["id"])
    cuts = [e for e in events if e["type"] == "PRICE_DECREASE"]
    assert len(cuts) == 2  # once per probe keyword, same offer each time
    assert cuts[0]["previous_value"] == 130.0 and cuts[0]["current_value"] == 99.0


def test_concurrent_scans_for_one_market_spend_credits_once(fake_serpapi):
    import asyncio

    from app import repo
    from app.db import initialize_database
    from app.seed import seed_demo_data
    from app.services.scan import ScanInProgressError, run_scan

    initialize_database()
    seed_demo_data()
    market = repo.get_market(1, 1)

    async def both():
        return await asyncio.gather(
            run_scan(market, force=True), run_scan(market, force=True), return_exceptions=True
        )

    results = asyncio.run(both())
    failures = [r for r in results if isinstance(r, ScanInProgressError)]
    successes = [r for r in results if isinstance(r, dict)]
    assert len(failures) == 1 and len(successes) == 1
    assert fake_serpapi.count("google_shopping") == len(market["keywords"])


def test_products_table_tracks_canonical_products(fake_serpapi):
    import asyncio
    from datetime import datetime, timedelta, timezone

    from app import repo
    from app.db import get_connection, initialize_database
    from app.seed import seed_demo_data
    from app.services.scan import run_scan

    initialize_database()
    seed_demo_data()
    market = repo.get_market(1, 1)
    start = datetime.now(timezone.utc) - timedelta(days=3)
    asyncio.run(run_scan(market, force=True, now=start))
    asyncio.run(run_scan(market, force=True, now=start + timedelta(days=1)))

    with get_connection() as conn:
        products = conn.execute("SELECT * FROM products").fetchall()
        orphans = conn.execute("SELECT COUNT(*) FROM product_observations WHERE product_row_id IS NULL").fetchone()[0]
        distinct = conn.execute("SELECT COUNT(DISTINCT canonical_key) FROM product_observations").fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM product_observations o JOIN products p ON p.id = o.product_row_id "
            "WHERE p.canonical_key = o.canonical_key AND p.market_id = o.market_id"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM product_observations").fetchone()[0]
    assert orphans == 0 and linked == total
    assert len(products) == distinct
    assert all(p["first_seen_at"] <= p["last_seen_at"] for p in products)

    repo.delete_market(1)
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
