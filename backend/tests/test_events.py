from app.intelligence.events import (
    detect_events,
    discount_events,
    new_competitor_events,
    new_product_events,
    price_events,
    rank_events,
)
from tests.factories import obs


def types(events):
    return sorted(event["type"] for event in events)


def test_no_history_means_no_events():
    assert detect_events([obs("A")], []) == []


def test_price_change_threshold_and_same_offer_only():
    before = [obs("A", price=100.0, source="Shop 1"), obs("B", price=100.0), obs("C", price=100.0, source="Shop 1")]
    after = [
        obs("A", price=95.0, source="Shop 1"),    # exactly 5% -> event
        obs("B", price=95.1),                     # 4.9% -> no event
        obs("C", price=50.0, source="Shop 2"),    # same product, different retailer -> not comparable
    ]
    events = price_events(after, before)
    assert types(events) == ["PRICE_DECREASE"]
    event = events[0]
    assert event["previous_value"] == 100.0 and event["current_value"] == 95.0
    assert event["metadata"]["change_pct"] == -5.0
    assert event["confidence"] == "high" and event["severity"] == "low"
    assert event["metadata"]["caveat"]


def test_price_increase_severity():
    events = price_events([obs("A", price=130.0)], [obs("A", price=100.0)])
    assert events[0]["type"] == "PRICE_INCREASE" and events[0]["severity"] == "high"


def test_discount_started_only_when_new_and_real():
    before = [obs("A", price=100.0), obs("B", price=80.0, old_price=100.0), obs("C", price=100.0)]
    after = [
        obs("A", price=80.0, old_price=100.0),   # discount started
        obs("B", price=80.0, old_price=100.0),   # already discounted
        obs("C", price=100.0, old_price=90.0),   # "old price" below price: not a discount
    ]
    events = discount_events(after, before)
    assert [e["product"] for e in events] == ["A"]
    assert events[0]["metadata"]["discount_pct"] == 20.0


def test_rank_change_threshold():
    before = [obs("A", position=10), obs("B", position=5), obs("C", position=30)]
    after = [obs("A", position=7), obs("B", position=7), obs("C", position=25)]
    events = rank_events(after, before)
    assert types(events) == ["RANK_GAIN"]  # A moved 3; B moved 2; C is too deep on the shelf
    assert events[0]["metadata"]["positions_moved"] == 3
    loss = rank_events([obs("A", position=15)], [obs("A", position=4)])
    assert loss[0]["type"] == "RANK_LOSS" and loss[0]["severity"] == "high"


def test_rank_is_compared_per_probe_keyword():
    before = [obs("A", keyword="k1", position=1), obs("A", keyword="k2", position=10)]
    after = [obs("A", keyword="k1", position=1), obs("A", keyword="k2", position=10)]
    assert rank_events(after, before) == []


def test_new_product_needs_two_scans_of_history():
    history = [[obs("A")], [obs("A")]]
    current = [obs("A"), obs("NEW", position=2, brand="Gamma")]
    assert new_product_events(current, history[:1]) == []
    events = new_product_events(current, history)
    assert len(events) == 1 and events[0]["product"] == "NEW"
    assert events[0]["severity"] == "high" and events[0]["confidence"] == "medium"
    # seen in any earlier scan of the window -> not new (shelf volatility)
    assert new_product_events(current, [[obs("NEW")], [obs("A")]]) == []


def test_new_competitor_excludes_own_brand_and_flags_inferred():
    history = [[obs("A", brand="Alpha")], [obs("A", brand="Alpha")]]
    current = [
        obs("A", brand="Alpha"),
        obs("G", brand="Gamma", position=3, brand_inferred=True),
        obs("M", brand="Mine", position=1),
    ]
    events = new_competitor_events(current, history, own_brand="mine")
    assert [e["brand"] for e in events] == ["Gamma"]
    assert events[0]["confidence"] == "low"  # single listing, brand only guessed
    assert new_competitor_events(current, history[:1]) == []


def test_detect_events_orders_and_marks_own_brand():
    history = [[obs("A", price=100.0, brand="Mine")], [obs("A", price=100.0, brand="Mine")]]
    current = [obs("A", price=70.0, brand="Mine"), obs("B", position=40, brand="Other")]
    events = detect_events(current, history, own_brand="Mine")
    assert events[0]["type"] == "PRICE_DECREASE" and events[0]["metadata"]["is_mine"] is True
    assert {"NEW_PRODUCT", "NEW_COMPETITOR"} <= set(types(events))
    low = [e for e in events if e["type"] == "NEW_PRODUCT"][0]
    assert low["confidence"] == "low"  # deep in the shelf


# --------------------------------------------------------------------------- price offers


def test_price_events_need_a_product_id_and_a_retailer():
    no_id_before, no_id_after = obs("A", price=100.0), obs("A", price=50.0)
    no_id_before["product_id"] = no_id_after["product_id"] = None
    assert price_events([no_id_after], [no_id_before]) == []
    assert discount_events([{**no_id_after, "old_price": 90.0}], [no_id_before]) == []

    no_shop_before, no_shop_after = obs("B", price=100.0, source=""), obs("B", price=50.0, source="")
    assert price_events([no_shop_after], [no_shop_before]) == []

    assert len(price_events([obs("C", price=50.0)], [obs("C", price=100.0)])) == 1


# --------------------------------------------------------------------------- removal


def shelf(*titles, keyword="probe"):
    return [obs(title, keyword=keyword, position=i + 1) for i, title in enumerate(titles)]


def test_single_missing_listing_is_not_a_removal():
    from app.intelligence.events import removed_product_events

    history = [shelf("A", "B", "C")] * 4
    assert removed_product_events(shelf("A", "B"), history) == []  # C missing once only


def test_product_missing_for_three_scans_after_being_established_is_removed():
    from app.intelligence.events import removed_product_events

    seen = shelf("A", "B", "C")
    history = [seen, seen, shelf("A", "B"), shelf("A", "B")]
    events = removed_product_events(shelf("A", "B"), history)
    assert [e["type"] for e in events] == ["REMOVED_PRODUCT"]
    event = events[0]
    assert event["metadata"]["canonical_key"] == "key-C"
    assert event["previous_value"] == 3.0 and event["current_value"] is None
    assert event["metadata"]["absent_scans"] == 3 and event["metadata"]["caveat"]


def test_no_removal_without_enough_history_or_with_an_empty_scan():
    from app.intelligence.events import removed_product_events

    seen = shelf("A", "B", "C")
    assert removed_product_events(shelf("A", "B"), [seen, shelf("A", "B"), shelf("A", "B")]) == []
    assert removed_product_events([], [seen, seen, shelf("A"), shelf("A")]) == []  # failed fetch, not an exit


def test_returning_product_is_not_removed():
    from app.intelligence.events import removed_product_events

    seen = shelf("A", "B", "C")
    history = [seen, seen, shelf("A", "B"), shelf("A", "B", "C")]
    assert removed_product_events(shelf("A", "B"), history) == []


def test_mass_disappearance_is_flagged_low_confidence():
    from app.intelligence.events import removed_product_events

    full = shelf("A", "B", "C", "D", "E", "F")
    history = [full, full, shelf("Z"), shelf("Z")]
    events = removed_product_events(shelf("Z"), history)
    assert len(events) == 6
    assert all(e["confidence"] == "low" and e["metadata"]["mass_disappearance"] for e in events)


# --------------------------------------------------------------------------- stability


def test_volatile_probe_lowers_confidence_but_not_severity():
    from app.intelligence.events import apply_stability

    def event(kind, confidence):
        return {"type": kind, "severity": "high", "confidence": confidence, "keyword": "probe", "metadata": {}}

    events = [event("RANK_GAIN", "medium"), event("NEW_PRODUCT", "high"), event("PRICE_DECREASE", "high")]
    apply_stability(events, {"probe": 0.1})
    assert [e["confidence"] for e in events] == ["low", "low", "medium"]
    assert all(e["severity"] == "high" for e in events)
    assert events[1]["metadata"]["confidence_lowered_from"] == "high"
    assert events[1]["metadata"]["probe_stability"] == 0.1


def test_stable_probe_keeps_confidence_and_unknown_stability_is_explicit():
    from app.intelligence.events import apply_stability

    stable = {"type": "NEW_PRODUCT", "severity": "low", "confidence": "high", "keyword": "probe", "metadata": {}}
    apply_stability([stable], {"probe": 0.8})
    assert stable["confidence"] == "high" and "confidence_lowered_from" not in stable["metadata"]

    unknown_rank = {"type": "RANK_LOSS", "severity": "low", "confidence": "medium", "keyword": "probe", "metadata": {}}
    apply_stability([unknown_rank], {})
    assert unknown_rank["confidence"] == "low" and unknown_rank["metadata"]["probe_stability"] is None
    assert "not enough scans" in unknown_rank["metadata"]["confidence_note"]


def test_probe_stability_is_mean_overlap_between_consecutive_scans():
    from app.intelligence.events import probe_stability

    a, b = shelf("A", "B"), shelf("A", "B")
    assert probe_stability(b, [a])["probe"] == 1.0
    assert probe_stability(shelf("X", "Y"), [a])["probe"] == 0.0
    assert probe_stability(a, [])["probe"] is None


def test_detect_events_downgrades_rank_events_on_a_volatile_shelf():
    stable_before = [obs("A", position=12), *shelf("B", "C", "D")]
    stable_after = [obs("A", position=2), *shelf("B", "C", "D")]
    stable = [e for e in detect_events(stable_after, [stable_before]) if e["type"] == "RANK_GAIN"]
    assert stable and stable[0]["confidence"] == "medium"

    volatile_before = [obs("A", position=12), *shelf("P", "Q", "R", "S", "T", "U", "V")]
    volatile_after = [obs("A", position=2), *shelf("W", "X", "Y", "Z", "M", "N", "O")]
    volatile = [e for e in detect_events(volatile_after, [volatile_before]) if e["type"] == "RANK_GAIN"]
    assert volatile and volatile[0]["confidence"] == "low"
