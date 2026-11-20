"""Section endpoints: contracts, empty states, ownership, pagination and read-only behavior."""

from __future__ import annotations

import pytest

from app.db import get_connection

SECTIONS = ["scans", "events", "observations", "demand", "shelf", "supply", "pricing", "opportunities", "reports"]


def counts() -> dict[str, int]:
    tables = ["scans", "product_observations", "products", "market_events", "opportunities", "reports", "trend_observations"]
    with get_connection() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def scan(client, headers, market_id=1):
    response = client.post(f"/api/markets/{market_id}/scan?force=true", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def get(client, headers, path, market_id=1, **params):
    return client.get(f"/api/markets/{market_id}/{path}", headers=headers, params=params)


# --------------------------------------------------------------------------- no data


@pytest.mark.parametrize("section", SECTIONS)
def test_every_section_has_a_well_formed_empty_state_before_any_scan(client, auth_headers, section):
    response = get(client, auth_headers, section)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("has_data", False) is False or "items" in body
    for key in ("items", "keywords", "probes"):
        if key in body:
            assert body[key] in ([], ["running shoes", "trail running shoes"])  # observations echoes the probes


def test_unknown_market_and_other_users_market_are_404_for_every_section(client, auth_headers, make_user):
    other = make_user("intruder@example.com")
    for section in SECTIONS:
        assert get(client, auth_headers, section, market_id=999).status_code == 404
        assert get(client, other, section).status_code == 404, section
    assert get(client, auth_headers, "products/1", market_id=999).status_code == 404


def test_sections_require_authentication(client):
    for section in SECTIONS:
        assert client.get(f"/api/markets/1/{section}").status_code in (401, 403)


def test_invalid_query_parameters_are_rejected(client, auth_headers):
    assert get(client, auth_headers, "events", limit=0).status_code == 422
    assert get(client, auth_headers, "events", limit=10_000).status_code == 422
    assert get(client, auth_headers, "observations", sort="drop table").status_code == 422
    assert get(client, auth_headers, "events", offset=-1).status_code == 422


# --------------------------------------------------------------------------- with a scan


def test_sections_after_one_scan(client, auth_headers, fake_serpapi):
    result = scan(client, auth_headers)

    scans = get(client, auth_headers, "scans").json()
    assert {s["kind"] for s in scans["items"]} == {"shopping", "trends"}
    assert scans["total_credits"] == result["credits_used"]

    observations = get(client, auth_headers, "observations").json()
    assert observations["has_data"] and observations["total"] == result["products_observed"]
    assert observations["scan"]["is_demo"] is False
    assert len(observations["items"]) == min(50, observations["total"])
    assert all(item["product_row_id"] for item in observations["items"])

    shelf = get(client, auth_headers, "shelf").json()
    assert shelf["has_data"] and shelf["overall"] and set(shelf["by_probe"]) == {"running shoes", "trail running shoes"}
    assert "not market share" in shelf["method"]

    supply = get(client, auth_headers, "supply").json()
    assert supply["has_data"] and {p["keyword"] for p in supply["probes"]} == {"running shoes", "trail running shoes"}

    pricing = get(client, auth_headers, "pricing").json()
    assert pricing["has_data"] and pricing["overall"]["count"] > 0 and pricing["brands"]
    assert "per_brand_median" not in pricing["overall"]

    demand = get(client, auth_headers, "demand").json()
    assert demand["has_data"]
    assert all(k["series"] for k in demand["keywords"]) and all(k["signal"] for k in demand["keywords"])

    opportunities = get(client, auth_headers, "opportunities").json()
    assert opportunities["has_data"] and len(opportunities["items"]) == 2
    assert all("confidence" in item and "caveats" in item for item in opportunities["items"])

    events = get(client, auth_headers, "events").json()
    assert events["items"] == [] and events["total"] == 0  # one scan cannot show change


def test_observation_filters_search_sort_and_paging(client, auth_headers, fake_serpapi):
    scan(client, auth_headers)
    everything = get(client, auth_headers, "observations", limit=200).json()
    first_keyword = everything["keywords"][0]

    one_probe = get(client, auth_headers, "observations", keyword=first_keyword, limit=200).json()
    assert 0 < one_probe["total"] < everything["total"]
    assert {i["keyword"] for i in one_probe["items"]} == {first_keyword}

    searched = get(client, auth_headers, "observations", q="hoka", limit=200).json()
    haystacks = [(i["title"] + (i["source"] or "") + (i["brand"] or "")).lower() for i in searched["items"]]
    assert searched["items"] and all("hoka" in text for text in haystacks)

    by_price = get(client, auth_headers, "observations", sort="price", limit=200).json()["items"]
    prices = [i["price"] for i in by_price if i["price"] is not None]
    assert prices == sorted(prices)

    page_one = get(client, auth_headers, "observations", limit=5, offset=0).json()
    page_two = get(client, auth_headers, "observations", limit=5, offset=5).json()
    assert len(page_one["items"]) == 5 and {i["id"] for i in page_one["items"]}.isdisjoint({i["id"] for i in page_two["items"]})


def test_product_history_and_404(client, auth_headers, fake_serpapi):
    scan(client, auth_headers)
    item = get(client, auth_headers, "observations").json()["items"][0]
    detail = get(client, auth_headers, f"products/{item['product_row_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["product"]["canonical_key"] == item["canonical_key"]
    assert body["history"] and body["history"][0]["listings"] >= 1
    assert get(client, auth_headers, "products/999999").status_code == 404


# --------------------------------------------------------------------------- with history


def test_events_page_confidence_gate_filter_and_pagination(client, auth_headers, fake_serpapi):
    scan(client, auth_headers)
    assert client.post("/api/markets/1/demo-history", headers=auth_headers).status_code == 200

    default = get(client, auth_headers, "events", limit=200).json()
    everything = get(client, auth_headers, "events", limit=200, include_low_confidence=True).json()
    assert default["total"] + default["hidden_low_confidence"] == everything["total"]
    assert all(e["confidence"] != "low" for e in default["items"])
    assert everything["total"] > 0

    kind = everything["items"][0]["type"]
    filtered = get(client, auth_headers, "events", limit=200, include_low_confidence=True, type=kind).json()
    assert filtered["total"] > 0 and {e["type"] for e in filtered["items"]} == {kind}

    first = get(client, auth_headers, "events", limit=2, offset=0, include_low_confidence=True).json()
    second = get(client, auth_headers, "events", limit=2, offset=2, include_low_confidence=True).json()
    assert not {e["id"] for e in first["items"]} & {e["id"] for e in second["items"]}
    assert first["total"] == second["total"] == everything["total"]

    shelf = get(client, auth_headers, "shelf").json()
    assert shelf["scans_compared"] > 1
    assert get(client, auth_headers, "scans").json()["items"][0]["is_demo"] is False


def test_get_requests_never_write(client, auth_headers, fake_serpapi):
    scan(client, auth_headers)
    client.post("/api/markets/1/demo-history", headers=auth_headers)
    before = counts()
    for _ in range(2):
        for section in SECTIONS:
            assert get(client, auth_headers, section).status_code == 200
        assert client.get("/api/markets/1/dashboard", headers=auth_headers).status_code == 200
    assert counts() == before


def test_reports_history_lists_newest_first(client, auth_headers, fake_serpapi):
    scan(client, auth_headers)
    for _ in range(2):
        assert client.post("/api/markets/1/reports/weekly", headers=auth_headers).status_code == 201
    items = get(client, auth_headers, "reports").json()["items"]
    assert len(items) == 2 and items[0]["id"] > items[1]["id"]


def test_new_market_without_scans_is_empty_not_an_error(client, auth_headers):
    created = client.post("/api/markets", headers=auth_headers, json={
        "category": "Desk Lamps", "country": "US", "keywords": ["desk lamp"], "competitors": [],
    })
    market_id = created.json()["id"]
    for section in SECTIONS:
        assert get(client, auth_headers, section, market_id=market_id).status_code == 200, section
    assert get(client, auth_headers, "shelf", market_id=market_id).json()["has_data"] is False


def test_observations_accept_fractional_review_counts_from_real_data(client, auth_headers, fake_serpapi):
    scan(client, auth_headers)
    with get_connection() as conn:
        conn.execute("UPDATE product_observations SET reviews = 2.3 WHERE id = (SELECT MIN(id) FROM product_observations)")
    response = get(client, auth_headers, "observations", limit=200)
    assert response.status_code == 200, response.text
    assert any(item["reviews"] == 2.3 for item in response.json()["items"])
