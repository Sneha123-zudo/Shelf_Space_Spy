"""Test isolation: temp SQLite file, temp SerpAPI cache, no network, no real keys."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest

# Must be set before the app (and its settings) are imported. Environment variables take
# precedence over backend/.env, so the developer's real keys are never used by tests.
TEST_ENV = {
    "APP_NAME": "Shelf Space Spy API (tests)",
    "ENVIRONMENT": "test",
    "FRONTEND_ORIGINS": "http://localhost:5173",
    "SERPAPI_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "JWT_SECRET": "test-only-secret-not-used-anywhere-else",
    "JWT_ALGORITHM": "HS256",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "60",
    "DEMO_USER_EMAIL": "demo@example.com",
    "DEMO_USER_PASSWORD": "Demo@123",
    "DEMO_USER_NAME": "Demo User",
    "DEMO_MARKET_NAME": "Example Market",
    "DEMO_MARKET_COUNTRY": "US",
    "DEMO_MARKET_CATEGORY": "Running Shoes",
    "DEMO_BRAND_NAME": "Stride",
    "DEMO_MARKET_KEYWORDS": "running shoes,trail running shoes",
    "DEMO_COMPETITORS": "Nike,Hoka",
    "SCAN_MIN_INTERVAL_MINUTES": "60",
    "SERPAPI_CACHE_DIR": "",
}
os.environ.update(TEST_ENV)

from app.core.config import get_settings  # noqa: E402
from app.services import serpapi  # noqa: E402
from tests import factories  # noqa: E402

# The real network function, kept so tests can exercise its status-code handling with a fake httpx.
REAL_HTTP_GET = serpapi._http_get


class NetworkBlocked(AssertionError):
    pass


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("SHELF_SPACE_SPY_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("SERPAPI_CACHE_DIR", str(tmp_path / "serpapi_cache"))
    get_settings.cache_clear()

    async def blocked(params: dict[str, Any]) -> dict[str, Any]:
        raise NetworkBlocked(f"Unexpected SerpAPI network call: {params.get('engine')}")

    monkeypatch.setattr(serpapi, "_http_get", blocked)
    yield
    get_settings.cache_clear()


class FakeSerpApi:
    """Stands in for the HTTP layer and records every would-be paid call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.shopping: dict[str, dict[str, Any]] = {}
        self.trends_factors: dict[str, float] = {}
        self.trends_error: str | None = None
        self.autocomplete: list[str] = []

    async def __call__(self, params: dict[str, Any]) -> dict[str, Any]:
        assert params.get("api_key") == "fake-test-key", "tests must never use a real key"
        self.calls.append(params)
        engine = params["engine"]
        if engine == "google_shopping":
            return self.shopping.get(params["q"]) or factories.shopping_payload(params["q"])
        if engine == "google_trends":
            if self.trends_error:
                return {"error": self.trends_error}
            return factories.trends_payload(params["q"].split(","), self.trends_factors)
        if engine == "google_autocomplete":
            return factories.autocomplete_payload(self.autocomplete)
        raise AssertionError(f"unknown engine {engine}")

    def count(self, engine: str) -> int:
        return sum(1 for call in self.calls if call["engine"] == engine)


@pytest.fixture
def fake_serpapi(monkeypatch) -> FakeSerpApi:
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")
    get_settings.cache_clear()
    fake = FakeSerpApi()
    monkeypatch.setattr(serpapi, "_http_get", fake)
    return fake


@pytest.fixture
def client() -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "Demo@123"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def make_user(client) -> Callable[[str], dict[str, str]]:
    """Create another user directly and return auth headers for them."""
    from app import repo
    from app.core.security import hash_password

    def _make(email: str) -> dict[str, str]:
        repo.create_user(email, "Other", hash_password("Other@123"))
        response = client.post("/api/auth/login", json={"email": email, "password": "Other@123"})
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _make
