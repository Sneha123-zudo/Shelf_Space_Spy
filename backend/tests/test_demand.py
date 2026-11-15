from datetime import date, timedelta

from app.intelligence.demand import demand_by_keyword, demand_signal


def points(values, keyword="probe", batch="b1", partial_last=False, start=date(2026, 1, 4)):
    rows = [
        {"keyword": keyword, "batch_id": batch, "week_start": (start + timedelta(days=7 * i)).isoformat(),
         "interest": v, "is_partial": False}
        for i, v in enumerate(values)
    ]
    if partial_last:
        rows[-1]["is_partial"] = True
    return rows


def test_recent_four_weeks_vs_prior_eight():
    signal = demand_signal(points([40] * 8 + [50] * 4))
    assert signal["prior_avg"] == 40.0
    assert signal["recent_avg"] == 50.0
    assert signal["growth_pct"] == 25.0
    assert signal["low_volume"] is False
    assert signal["weeks_used"] == 12


def test_partial_week_is_excluded():
    # the partial week (value 1) would drag the recent average down if it were used
    signal = demand_signal(points([40] * 8 + [50] * 4 + [1], partial_last=True))
    assert signal["growth_pct"] == 25.0
    assert signal["partial_weeks_excluded"] == 1


def test_low_volume_guard_and_insufficient_history():
    low = demand_signal(points([1] * 8 + [3] * 4))
    assert low["low_volume"] is True
    assert any("very low" in caveat for caveat in low["caveats"])
    short = demand_signal(points([10, 20, 30]))
    assert short["growth_pct"] is None and short["caveats"]


def test_zero_prior_interest_has_no_growth():
    signal = demand_signal(points([0] * 8 + [10] * 4))
    assert signal["growth_pct"] is None


def test_momentum_direction():
    assert demand_signal(points([40] * 8 + [40, 45, 50, 55]))["momentum"] == "rising"
    assert demand_signal(points([40] * 8 + [55, 50, 45, 40]))["momentum"] == "falling"
    assert demand_signal(points([40] * 12))["momentum"] == "flat"


def test_batches_are_never_mixed():
    old_batch = points([10] * 12, batch="old", start=date(2025, 1, 5))
    new_batch = points([40] * 8 + [60] * 4, batch="new", start=date(2026, 1, 4))
    signals = demand_by_keyword(old_batch + new_batch + points([20] * 12, keyword="other", batch="new"))
    assert signals["probe"]["batch_id"] == "new"
    assert signals["probe"]["growth_pct"] == 50.0
    assert any("batch" in caveat for caveat in signals["probe"]["caveats"])
    assert signals["other"]["growth_pct"] == 0.0
