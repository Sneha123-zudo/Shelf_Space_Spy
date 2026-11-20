import pytest

from app.intelligence.opportunity import WEIGHTS, incumbent_strength, label_for, score_opportunity
from tests.factories import obs


def test_weights_and_labels():
    assert sum(WEIGHTS.values()) == 100
    assert label_for(75) == "Golden"
    assert label_for(74.9) == "Attractive"
    assert label_for(55) == "Attractive"
    assert label_for(54.9) == "Monitor"
    assert label_for(35) == "Monitor"
    assert label_for(34.9) == "Crowded"


def test_missing_inputs_score_neutral_with_caveats():
    result = score_opportunity(
        "probe", demand=None, supply=None, supply_growth=None, pricing=None, incumbents=None,
        scan_count=1, stability=None,
    )
    assert result["score"] == 50.0
    assert result["label"] == "Monitor"
    assert result["demand_growth"] is None and result["supply_growth"] is None
    assert len(result["caveats"]) >= 4
    assert result["confidence"]["level"] == "low"


def test_golden_versus_crowded():
    golden = score_opportunity(
        "rising niche",
        demand={"growth_pct": 45.0, "momentum": "rising", "low_volume": False, "weeks_used": 52, "caveats": []},
        supply={"results": 40, "top3_share": 0.15},
        supply_growth=-10.0,
        pricing={"median": 100.0, "q3": 150.0, "discount_rate": 0.0},
        incumbents={"median_reviews": 12, "avg_rating": 4.0},
        scan_count=5,
        stability=0.8,
    )
    crowded = score_opportunity(
        "saturated",
        demand={"growth_pct": -30.0, "momentum": "falling", "low_volume": False, "weeks_used": 52, "caveats": []},
        supply={"results": 40, "top3_share": 0.85},
        supply_growth=40.0,
        pricing={"median": 100.0, "q3": 102.0, "discount_rate": 0.6},
        incumbents={"median_reviews": 20000, "avg_rating": 4.8},
        scan_count=5,
        stability=0.8,
    )
    assert golden["label"] == "Golden" and golden["score"] >= 75
    assert crowded["label"] == "Crowded" and crowded["score"] < 35
    assert golden["demand_growth"] == 45.0 and golden["supply_growth"] == -10.0
    assert any("Search interest up 45%" in reason for reason in golden["reasons"])
    assert golden["confidence"]["level"] == "high"
    for component in golden["components"].values():
        assert 0 <= component <= 100


def test_low_volume_halves_demand_swing():
    common = dict(supply=None, supply_growth=None, pricing=None, incumbents=None, scan_count=3, stability=None)
    normal = score_opportunity("a", demand={"growth_pct": 40.0, "momentum": "flat", "low_volume": False}, **common)
    low = score_opportunity("b", demand={"growth_pct": 40.0, "momentum": "flat", "low_volume": True,
                                         "caveats": ["Search interest is very low"]}, **common)
    assert 50 < low["components"]["demand"] < normal["components"]["demand"] < 100
    assert "Search interest is very low" in low["caveats"]


def test_strong_growth_does_not_saturate_demand_component():
    common = dict(supply=None, supply_growth=None, pricing=None, incumbents=None, scan_count=3, stability=None)
    scores = [
        score_opportunity("k", demand={"growth_pct": g, "momentum": "flat", "low_volume": False}, **common)
        ["components"]["demand"]
        for g in (50.0, 100.0, 200.0)
    ]
    assert scores[0] < scores[1] < scores[2] < 100


def test_confidence_does_not_change_score():
    kwargs = dict(demand=None, supply={"results": 40, "top3_share": 0.5}, supply_growth=0.0, pricing=None,
                  incumbents=None)
    a = score_opportunity("k", scan_count=1, stability=0.1, **kwargs)
    b = score_opportunity("k", scan_count=9, stability=0.9, **kwargs)
    assert a["score"] == b["score"]
    assert a["confidence"]["level"] != b["confidence"]["level"]


def test_supply_growth_wording():
    common = dict(demand=None, pricing=None, incumbents=None, scan_count=3, stability=None)
    same = score_opportunity("k", supply={"results": 10, "top3_share": 0.5}, supply_growth=0.0, **common)
    assert "Distinct SKUs unchanged since the previous scan." in same["reasons"]
    week = score_opportunity("k", supply={"results": 10, "top3_share": 0.5}, supply_growth=12.5,
                             comparison_days=7, **common)
    assert "Distinct SKUs up 12.5% vs 7 days earlier." in week["reasons"]


def test_demo_data_caveat_and_incumbents():
    result = score_opportunity("k", demand=None, supply=None, supply_growth=None, pricing=None, incumbents=None,
                               scan_count=1, stability=None, uses_demo_data=True)
    assert not any("demo" in caveat.lower() for caveat in result["caveats"])  # no demo wording is shown
    rows = [obs(f"P{i}", position=i + 1, reviews=r, rating=4.0) for i, r in enumerate([10, 100, 1000])]
    strength = incumbent_strength(rows)
    assert strength["median_reviews"] == 100 and strength["avg_rating"] == pytest.approx(4.0)
