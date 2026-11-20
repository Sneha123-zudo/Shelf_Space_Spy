import pytest

from app.intelligence.shelf_share import (
    collapse_tail,
    shelf_share,
    shelf_share_by_probe,
    top_n_share,
    visibility_weight,
)
from tests.factories import obs


def test_visibility_weight_formula():
    assert visibility_weight(1) == 1.0
    assert visibility_weight(11) == pytest.approx(0.5)
    assert visibility_weight(21) == pytest.approx(1 / 3)
    assert visibility_weight(None) == visibility_weight(40)


def test_share_is_position_weighted_with_unbranded_entry_and_own_brand():
    rows = [
        obs("A1", position=1, brand="Alpha"),
        obs("B1", position=2, brand="Beta"),
        obs("A2", position=11, brand="alpha"),  # same brand, different casing
        obs("X1", position=3, brand=None),
    ]
    share = shelf_share(rows, own_brand="BETA")
    by_brand = {row["brand"]: row for row in share}
    assert set(by_brand) == {"Alpha", "Beta", "Unbranded"} or set(by_brand) == {"alpha", "Beta", "Unbranded"}
    alpha = next(row for row in share if row["brand"].lower() == "alpha")
    total = 1 + 1 / 1.1 + 0.5 + 1 / 1.2
    assert alpha["share_pct"] == round(100 * 1.5 / total, 1)
    assert alpha["listings"] == 2
    assert by_brand["Beta"]["is_mine"] is True
    assert by_brand["Unbranded"]["is_unbranded"] is True
    assert sum(row["share_pct"] for row in share) == pytest.approx(100, abs=0.2)
    assert share[0]["brand"].lower() == "alpha"  # largest first


def test_share_by_probe_and_top_n():
    rows = [obs("A", keyword="k1", brand="A"), obs("B", keyword="k2", brand="B", position=1)]
    per_probe = shelf_share_by_probe(rows)
    assert per_probe["k1"][0]["share_pct"] == 100.0
    assert top_n_share(shelf_share(rows), 1) == pytest.approx(0.5)
    assert shelf_share([]) == []


def test_collapse_tail_keeps_own_brand():
    rows = [obs(f"P{i}", brand=f"Brand{i}", position=i + 1) for i in range(14)]
    share = shelf_share(rows, own_brand="Brand13")
    collapsed = collapse_tail(share, keep=5)
    names = [row["brand"] for row in collapsed]
    assert names[:5] == ["Brand0", "Brand1", "Brand2", "Brand3", "Brand4"]
    assert "Brand13" in names and names[-1] == "Other brands"
    assert sum(row["share_pct"] for row in collapsed) == pytest.approx(100, abs=0.5)
