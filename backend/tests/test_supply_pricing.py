import pytest

from app.intelligence.pricing import is_discounted, price_headroom, price_stats
from app.intelligence.supply import shelf_stability, supply_by_keyword, supply_growth_pct, supply_metrics
from tests.factories import obs


def test_supply_metrics():
    rows = [
        obs("A", brand="Alpha", source="Shop 1", position=1),
        obs("A", brand="Alpha", source="Shop 2", position=2),  # same canonical product, 2nd seller
        obs("B", brand="Beta", source="Shop 1", position=3),
        obs("C", brand=None, source="Shop 3", position=4),
    ]
    metrics = supply_metrics(rows)
    assert metrics["results"] == 4
    assert metrics["skus"] == 3
    assert metrics["brands"] == 2
    assert metrics["retailers"] == 3
    assert metrics["density"] == 0.75
    assert 0 < metrics["top3_share"] <= 1
    assert supply_metrics([])["skus"] == 0
    assert set(supply_by_keyword(rows + [obs("Z", keyword="other")])) == {"probe", "other"}


def test_supply_growth_and_stability():
    assert supply_growth_pct({"skus": 12}, {"skus": 10}) == pytest.approx(20.0)
    assert supply_growth_pct({"skus": 12}, None) is None
    now = [obs("A"), obs("B"), obs("C")]
    before = [obs("A"), obs("B"), obs("D")]
    assert shelf_stability(now, before) == 0.5
    assert shelf_stability(now, []) is None


def test_price_stats_quartiles_bands_and_discounts():
    rows = [obs(f"P{i}", price=float(p), brand="A" if i < 3 else "B") for i, p in enumerate([10, 20, 30, 40, 50])]
    rows[0]["old_price"] = 15.0  # discounted
    rows[1]["old_price"] = 5.0   # bogus "old price" below the price: not a discount
    stats = price_stats(rows + [obs("free", price=0.0), obs("none", price=None)])
    assert stats["count"] == 5
    assert (stats["q1"], stats["median"], stats["q3"]) == (20.0, 30.0, 40.0)
    assert stats["bands"] == {"budget": 1, "mid": 3, "premium": 1}
    assert stats["discount_rate"] == 0.2
    assert stats["per_brand_median"] == {"A": 20.0, "B": 45.0}
    assert price_headroom(stats) == pytest.approx(1 / 3)
    assert is_discounted(rows[0]) and not is_discounted(rows[1])


def test_price_stats_empty_and_single():
    assert price_stats([])["median"] is None
    assert price_headroom(price_stats([])) is None
    single = price_stats([obs("only", price=99.0)])
    assert single["median"] == single["q1"] == single["q3"] == 99.0
