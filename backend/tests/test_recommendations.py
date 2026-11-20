"""Recommended actions: each rule, its guards, and the guarantee that it only reads stored data."""

from __future__ import annotations

from app.db import get_connection
from app.intelligence.recommendations import build_recommendations


def opp(keyword="widget", label="Attractive", score=70.0, level="high", demand=20.0, supply=-5.0, headroom=40.0, caveats=None):
    return {
        "keyword": keyword, "score": score, "label": label, "demand_growth": demand, "supply_growth": supply,
        "reasons": [], "caveats": caveats or [], "components": {"price_headroom": headroom},
        "confidence": {"level": level, "factors": {}},
    }


def demand(keyword="widget", growth=20.0, low=False):
    return {keyword: {"keyword": keyword, "signal": {"growth_pct": growth, "low_volume": low, "momentum": "rising", "caveats": []}, "series": []}}


def price(keyword="widget", count=30, discount=0.1):
    return {keyword: {"keyword": keyword, "count": count, "q1": 20.0, "median": 30.0, "q3": 45.0, "discount_rate": discount}}


def supply(keyword="widget"):
    return {keyword: {"keyword": keyword, "top3_share": 0.35, "skus": 20}}


def event(kind="PRICE_DECREASE", severity="high", confidence="high", brand="Rival", keyword="widget", demo=False, mine=False):
    return {
        "type": kind, "severity": severity, "confidence": confidence, "brand": brand, "keyword": keyword, "product": "Rival Widget",
        "is_demo": demo, "metadata": {"summary": f"{brand} cut the price.", "caveat": "Same offer.", "is_mine": mine},
    }


def run(opportunities=None, **overrides):
    args = {
        "opportunities": opportunities if opportunities is not None else [opp()], "demand": demand(), "pricing": price(), "supply": supply(),
        "stability": {"widget": 0.8}, "events": [], "scan_count": 5, "uses_demo_data": False,
    }
    args.update(overrides)
    return build_recommendations(**args)


def kinds(items):
    return [i["kind"] for i in items]


def test_a_strong_well_evidenced_probe_is_worth_investigating_with_traceable_evidence():
    items, _ = run([opp(label="Golden", score=82)])
    first = items[0]
    assert first["kind"] == "investigate" and first["priority"] == "high" and first["probe"] == "widget"
    labels = {e["label"] for e in first["evidence"]}
    assert {"Opportunity score", "Search interest change", "Competing products change", "Top 3 brands' share of shelf"} <= labels
    assert next(e for e in first["evidence"] if e["label"] == "Top 3 brands' share of shelf")["value"] == 35.0
    assert first["confidence"] == "high" and first["action"] and first["summary"]


def test_thin_evidence_turns_the_same_signal_into_collect_data_not_investigate():
    for overrides, expected in (
        ({"scan_count": 1}, "Only 1 scan"),
        ({"stability": {"widget": 0.1}}, "changes a lot between scans"),
        ({"demand": demand(low=True)}, "very low"),
        ({"demand": demand(growth=None)}, "No usable Google Trends"),
    ):
        items, _ = run(**overrides)
        assert "investigate" not in kinds(items) and "collect_data" in kinds(items)
        collect = next(i for i in items if i["kind"] == "collect_data")
        assert collect["confidence"] == "low" and any(expected in line for line in collect["limitations"])


def test_low_opportunity_confidence_blocks_investigate():
    items, _ = run([opp(level="low")])
    assert "investigate" not in kinds(items) and "collect_data" in kinds(items)


def test_crowded_probe_is_deprioritized_only_with_enough_confidence():
    assert "deprioritize" in kinds(run([opp(label="Crowded", score=20, level="medium")])[0])
    assert "deprioritize" not in kinds(run([opp(label="Crowded", score=20, level="low")])[0])


def test_price_hypothesis_needs_enough_priced_listings_and_warns_about_discounts():
    items, _ = run([opp(headroom=80)], pricing=price(discount=0.4))
    test = next(i for i in items if i["kind"] == "test_price")
    assert test["priority"] == "medium" and "premium" in test["summary"]
    assert any("40% of listings show a discount" in line for line in test["limitations"])
    assert any("margins" in line for line in test["limitations"])
    assert {e["label"] for e in test["evidence"]} >= {"Lower quartile", "Median", "Upper quartile"}
    assert "test_price" not in kinds(run(pricing=price(count=5))[0])
    assert "test_price" not in kinds(run([opp(label="Crowded", level="high", score=10)])[0])


def test_trusted_real_competitor_events_become_actions_and_mine_or_weak_ones_do_not():
    items, notes = run(events=[
        event("PRICE_DECREASE"), event("NEW_COMPETITOR", brand="Newco", severity="medium"), event("REMOVED_PRODUCT", severity="low"),
        event("PRICE_DECREASE", mine=True), event("PRICE_DECREASE", confidence="low"), event("RANK_GAIN"),
    ])
    got = kinds(items)
    assert got.count("monitor_competitor") == 1 and "watch_entrant" in got and "check_exit" in got
    assert notes == []
    monitor = next(i for i in items if i["kind"] == "monitor_competitor")
    assert "Rival" in monitor["title"] and monitor["source"] == "event" and monitor["limitations"] == ["Same offer."]


def test_demo_events_are_never_used_and_demo_history_caps_confidence():
    items, notes = run(events=[event(demo=True), {**event(), "metadata": {**event()["metadata"], "compared_with_demo": True}}])
    assert "monitor_competitor" not in kinds(items) and notes == []

    items, _ = run(uses_demo_data=True)
    assert items and all(i["uses_demo_data"] and i["confidence"] == "low" for i in items)
    assert not any("demo" in line.lower() for i in items for line in i["limitations"])  # capped silently, no demo wording


def test_other_probe_gaps_produce_one_market_level_note_not_noise():
    items, _ = run([opp(label="Monitor", score=45), opp(keyword="other", label="Monitor", score=40)], scan_count=1,
                   demand={**demand(), **demand("other")}, pricing={}, supply={}, stability={"widget": 0.8, "other": 0.8})
    collect = [i for i in items if i["kind"] == "collect_data"]
    assert len(collect) == 1 and collect[0]["probe"] is None and "Strengthen the evidence" in collect[0]["title"]


def test_ordering_is_priority_then_kind_and_is_deterministic():
    a = run([opp(label="Golden"), opp(keyword="b", label="Crowded", score=10, level="high")], demand={**demand(), **demand("b")},
            pricing={**price(), **price("b")}, supply={**supply(), **supply("b")}, stability={"widget": 0.8, "b": 0.8},
            events=[event()])[0]
    b = run([opp(label="Golden"), opp(keyword="b", label="Crowded", score=10, level="high")], demand={**demand(), **demand("b")},
            pricing={**price(), **price("b")}, supply={**supply(), **supply("b")}, stability={"widget": 0.8, "b": 0.8},
            events=[event()])[0]
    assert [i["id"] for i in a] == [i["id"] for i in b]
    order = {"high": 0, "medium": 1, "low": 2}
    assert [order[i["priority"]] for i in a] == sorted(order[i["priority"]] for i in a)


# --------------------------------------------------------------------------- endpoint

STABLE = ("Alpha one", "Beta two", "Gamma three", "Delta four", "Epsilon five", "Zeta six", "Eta seven", "Theta eight")


def counts():
    with get_connection() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("scans", "market_events", "alerts", "scan_jobs", "reports")}


def test_endpoint_empty_state_owner_scope_and_replay_validation(client, auth_headers, make_user):
    assert client.get("/api/markets/1/recommendations", headers=auth_headers).json() == {"has_data": False, "scan": None, "items": [], "notes": []}
    assert client.get("/api/markets/1/recommendations", headers=make_user("x@example.com")).status_code == 404
    assert client.get("/api/markets/1/recommendations?scan_id=999", headers=auth_headers).status_code == 404


def test_endpoint_after_a_scan_is_read_only_costs_no_credits_and_every_item_has_evidence(client, auth_headers, fake_serpapi):
    assert client.post("/api/markets/1/scan?force=true", headers=auth_headers).status_code == 200
    calls, before = len(fake_serpapi.calls), counts()
    body = client.get("/api/markets/1/recommendations", headers=auth_headers).json()
    assert body["has_data"] and body["items"]
    for item in body["items"]:
        assert item["evidence"] and item["action"] and item["limitations"] is not None
        assert item["confidence"] in {"low", "medium", "high"} and item["priority"] in {"high", "medium", "low"}
    assert any(i["kind"] == "collect_data" for i in body["items"])  # one scan is never enough to act on
    assert len(fake_serpapi.calls) == calls and counts() == before  # no SerpAPI call, nothing written


def test_endpoint_marks_demo_history_and_ignores_demo_events(client, auth_headers, fake_serpapi):
    client.post("/api/markets/1/scan?force=true", headers=auth_headers)
    client.post("/api/markets/1/demo-history", headers=auth_headers)
    body = client.get("/api/markets/1/recommendations", headers=auth_headers).json()
    assert body["items"] and all(i["uses_demo_data"] and i["confidence"] == "low" for i in body["items"])
    assert not any(i["source"] == "event" for i in body["items"])
    assert body["notes"] == [] and not any("demo" in line.lower() for i in body["items"] for line in i["limitations"])
