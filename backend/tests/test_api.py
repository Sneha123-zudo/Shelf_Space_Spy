"""HTTP contract tests, including backward compatibility with the original endpoints."""

from app import repo

NEW_MARKET = {
    "category": "Camping Tents",
    "country": "us",
    "keywords": ["Camping Tent", "camping tent", " backpacking tent ", "family tent"],
    "competitors": ["Coleman", "coleman", "REI Co-op"],
}

DASHBOARD_KEYS = {"kpis", "events", "shelf_share", "competitors", "opportunities", "has_demo_history", "report"}


def test_health_login_and_me(client, auth_headers):
    assert client.get("/api/health").json()["status"] == "ok"
    bad = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "wrong"})
    assert bad.status_code == 401
    me = client.get("/api/auth/me", headers=auth_headers)
    assert me.status_code == 200 and me.json() == {"id": 1, "email": "demo@example.com", "name": "Demo User"}
    assert client.get("/api/auth/me").status_code in (401, 403)


def test_seeded_example_market_keeps_original_contract(client, auth_headers):
    markets = client.get("/api/markets", headers=auth_headers).json()
    assert len(markets) == 1
    market = markets[0]
    assert set(market) == {"id", "name", "country", "category", "brand_name", "keywords", "competitors"}
    assert market["keywords"] == ["running shoes", "trail running shoes"]
    assert client.get(f"/api/markets/{market['id']}", headers=auth_headers).json() == market


def test_create_update_delete_market(client, auth_headers):
    created = client.post("/api/markets", json=NEW_MARKET, headers=auth_headers)
    assert created.status_code == 201, created.text
    market = created.json()
    assert market["country"] == "US"
    assert market["name"] == "Camping Tents (US)"
    assert market["brand_name"] is None  # researching without an own brand is allowed
    assert market["keywords"] == ["camping tent", "backpacking tent", "family tent"]
    assert market["competitors"] == ["Coleman", "REI Co-op"]

    duplicate = client.post("/api/markets", json=NEW_MARKET, headers=auth_headers)
    assert duplicate.status_code == 409

    updated = client.put(
        f"/api/markets/{market['id']}",
        json={"brand_name": "Summit Gear", "keywords": ["dome tent"]},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["brand_name"] == "Summit Gear"
    assert updated.json()["keywords"] == ["dome tent"]
    assert updated.json()["competitors"] == ["Coleman", "REI Co-op"]  # untouched
    cleared = client.put(f"/api/markets/{market['id']}", json={"brand_name": None}, headers=auth_headers)
    assert cleared.json()["brand_name"] is None

    assert client.delete(f"/api/markets/{market['id']}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/markets/{market['id']}", headers=auth_headers).status_code == 404


def test_market_validation(client, auth_headers):
    too_many = {**NEW_MARKET, "keywords": [f"tent {i}" for i in range(6)]}
    assert client.post("/api/markets", json=too_many, headers=auth_headers).status_code == 422
    none = {**NEW_MARKET, "keywords": ["  ", ""]}
    assert client.post("/api/markets", json=none, headers=auth_headers).status_code == 422
    bad_country = {**NEW_MARKET, "country": "USA"}
    assert client.post("/api/markets", json=bad_country, headers=auth_headers).status_code == 422
    comma = {**NEW_MARKET, "keywords": ["tent, large"]}
    assert client.post("/api/markets", json=comma, headers=auth_headers).status_code == 422


def test_markets_are_private_to_their_owner(client, auth_headers, make_user):
    other = make_user("other@example.com")
    mine = client.get("/api/markets", headers=auth_headers).json()[0]
    assert client.get("/api/markets", headers=other).json() == []
    for method, path in [
        ("get", f"/api/markets/{mine['id']}"),
        ("get", f"/api/markets/{mine['id']}/dashboard"),
        ("put", f"/api/markets/{mine['id']}"),
        ("delete", f"/api/markets/{mine['id']}"),
        ("post", f"/api/markets/{mine['id']}/scan"),
        ("post", f"/api/markets/{mine['id']}/reports/weekly"),
    ]:
        kwargs = {"json": {}} if method == "put" else {}
        response = getattr(client, method)(path, headers=other, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)


def test_suggest_endpoint(client, auth_headers, fake_serpapi):
    fake_serpapi.autocomplete = ["camping tent 4 person", "camping tent near me"]
    response = client.post("/api/markets/suggest", json={"category": "Camping Tent", "country": "US"}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["keywords"] == ["camping tent", "camping tent 4 person"]
    assert body["brands"] and set(body["brands"][0]) == {"name", "count", "inferred"}
    assert body["credits_used"] == 2


def test_suggest_without_key_is_503(client, auth_headers):
    response = client.post("/api/markets/suggest", json={"category": "tents", "country": "US"}, headers=auth_headers)
    assert response.status_code == 503
    assert "SERPAPI_API_KEY" in response.json()["detail"]


def test_scan_without_body_then_guard_then_dashboard(client, auth_headers, fake_serpapi):
    market_id = client.get("/api/markets", headers=auth_headers).json()[0]["id"]
    dry = client.post(f"/api/markets/{market_id}/scan?dry_run=true", headers=auth_headers).json()
    assert dry["dry_run"] is True and dry["estimated_credits"] == 3 and fake_serpapi.calls == []

    first = client.post(f"/api/markets/{market_id}/scan", headers=auth_headers)
    assert first.status_code == 200, first.text
    body = first.json()
    for legacy in ("market_id", "scanned_at", "keyword_count", "products_observed", "message"):
        assert legacy in body
    assert body["keyword_count"] == 2 and body["products_observed"] == 24 and body["credits_used"] == 3

    again = client.post(f"/api/markets/{market_id}/scan", headers=auth_headers)
    assert again.status_code == 409 and "force=true" in again.json()["detail"]
    forced = client.post(f"/api/markets/{market_id}/scan", json={"force": True}, headers=auth_headers)
    assert forced.status_code == 200 and forced.json()["credits_used"] == 0

    dashboard = client.get(f"/api/markets/{market_id}/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200
    data = dashboard.json()
    assert DASHBOARD_KEYS <= set(data)
    assert set(data["kpis"]) == {"observed_skus", "new_products", "demand_change_pct", "top_opportunity"}
    assert data["kpis"]["observed_skus"] == 12
    assert data["has_demo_history"] is False
    assert data["report"] is None
    share = data["shelf_share"]
    assert share and set(share[0]) >= {"brand", "share_pct", "is_mine"}
    assert not any(row["is_mine"] for row in share)  # own brand "Stride" is not on this shelf
    opp = data["opportunities"][0]
    assert set(opp) >= {"keyword", "score", "label", "demand_growth", "supply_growth", "reasons", "caveats"}
    tracked = {row["brand"] for row in data["competitors"] if row["is_tracked"]}
    assert tracked == {"Nike", "Hoka"}
    mine = [row for row in data["competitors"] if row["is_mine"]]
    assert mine == [{**mine[0], "brand": "Stride", "listings": 0, "share_pct": 0.0}]


def test_empty_dashboard_and_reports(client, auth_headers):
    market_id = client.get("/api/markets", headers=auth_headers).json()[0]["id"]
    dashboard = client.get(f"/api/markets/{market_id}/dashboard", headers=auth_headers).json()
    assert dashboard["kpis"] == {"observed_skus": 0, "new_products": 0, "demand_change_pct": None, "top_opportunity": None}
    assert dashboard["events"] == [] and dashboard["opportunities"] == []
    assert client.get(f"/api/markets/{market_id}/reports/latest", headers=auth_headers).json() is None

    created = client.post(f"/api/markets/{market_id}/reports/weekly", headers=auth_headers)
    assert created.status_code == 201
    report = created.json()
    assert report["source"] == "template" and "Weekly brief" in report["content"]
    latest = client.get(f"/api/markets/{market_id}/reports/latest", headers=auth_headers).json()
    assert latest["id"] == report["id"]
    assert client.get(f"/api/markets/{market_id}/dashboard", headers=auth_headers).json()["report"]["id"] == report["id"]
    assert repo.latest_report(market_id)["source"] == "template"


def test_login_is_throttled_after_repeated_failures(client):
    from app.api import auth

    auth._failed_logins.clear()
    for _ in range(auth.MAX_FAILED_LOGINS):
        assert client.post("/api/auth/login", json={"email": "demo@example.com", "password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "demo@example.com", "password": "bad"}).status_code == 429
    auth._failed_logins.clear()


def test_startup_refuses_example_secret_outside_development(monkeypatch):
    import pytest

    from app.core.config import check_startup_safety, get_settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "change-this-demo-secret-before-deploying")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        check_startup_safety(get_settings())
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    get_settings.cache_clear()
    check_startup_safety(get_settings())
