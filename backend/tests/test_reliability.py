"""SerpAPI retries/backoff, typed errors, log redaction and foreign-key enforcement."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sqlite3

import pytest

from app.core.logging import RedactingFilter, configure_logging, redact
from app.services import serpapi
from tests.conftest import REAL_HTTP_GET


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def no_sleep(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(serpapi, "_sleep", fake_sleep)
    return delays


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")
    serpapi.get_settings.cache_clear()


def scripted(monkeypatch, outcomes):
    """Replace the network call with a script: exceptions are raised, dicts are returned."""
    calls = []

    async def fake(params):
        calls.append(params)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(serpapi, "_http_get", fake)
    return calls


# --------------------------------------------------------------------------- retries


def test_transient_failures_are_retried_with_growing_bounded_delays(monkeypatch, keyed, no_sleep):
    calls = scripted(monkeypatch, [
        serpapi.SerpApiTransientError("HTTP 503"),
        serpapi.SerpApiRateLimitError("slow down"),
        {"shopping_results": [{"title": "ok"}]},
    ])
    response = run(serpapi.search(serpapi.shopping_params("tents", "US")))
    assert response.payload["shopping_results"] and len(calls) == 3
    assert len(no_sleep) == 2 and 1.0 <= no_sleep[0] <= 1.25 and 2.0 <= no_sleep[1] <= 2.5
    assert response.credits == 1  # a retried request is still one credit


def test_retries_are_bounded_then_raise_the_last_error(monkeypatch, keyed, no_sleep):
    calls = scripted(monkeypatch, [serpapi.SerpApiTransientError("HTTP 500")])
    with pytest.raises(serpapi.SerpApiTransientError):
        run(serpapi.search(serpapi.shopping_params("tents", "US")))
    assert len(calls) == 3 and len(no_sleep) == 2  # max_attempts=3 -> two waits, never an endless loop


def test_retry_after_is_honored_but_capped(monkeypatch, keyed, no_sleep):
    scripted(monkeypatch, [serpapi.SerpApiRateLimitError("limit", retry_after=7), {"shopping_results": []}])
    run(serpapi.search(serpapi.shopping_params("tents", "US")))
    assert no_sleep == [7]
    no_sleep.clear()
    scripted(monkeypatch, [serpapi.SerpApiRateLimitError("limit", retry_after=900), {"shopping_results": []}])
    run(serpapi.search(serpapi.shopping_params("other", "US")))
    assert no_sleep == [20.0]  # SERPAPI_BACKOFF_CAP_SECONDS


def test_quota_auth_and_ordinary_api_errors_are_never_retried(monkeypatch, keyed, no_sleep):
    for error in (serpapi.SerpApiQuotaError("out of searches"), serpapi.SerpApiError("bad request")):
        calls = scripted(monkeypatch, [error])
        with pytest.raises(type(error)):
            run(serpapi.search(serpapi.shopping_params(f"q-{type(error).__name__}", "US")))
        assert len(calls) == 1
    assert no_sleep == []


def test_cache_hits_never_touch_the_network_or_sleep(monkeypatch, keyed, no_sleep):
    calls = scripted(monkeypatch, [{"shopping_results": []}])
    params = serpapi.shopping_params("tents", "US")
    run(serpapi.search(params))
    run(serpapi.search(params))
    assert len(calls) == 1 and no_sleep == []


def test_real_http_layer_classifies_status_codes(monkeypatch):
    import httpx

    def fake_client(status, body, headers=None):
        class Client:
            def __init__(self, *a, **k): ...
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, params=None):
                return httpx.Response(status, json=body, headers=headers or {})
        return Client

    cases = [
        (429, {"error": "Too many requests"}, {"retry-after": "5"}, serpapi.SerpApiRateLimitError),
        (429, {"error": "Your account has run out of searches."}, None, serpapi.SerpApiQuotaError),
        (401, {"error": "Invalid API key."}, None, serpapi.SerpApiQuotaError),
        (503, {}, None, serpapi.SerpApiTransientError),
    ]
    for status, body, headers, expected in cases:
        monkeypatch.setattr(httpx, "AsyncClient", fake_client(status, body, headers))
        with pytest.raises(expected):
            run(REAL_HTTP_GET({"engine": "google_shopping", "api_key": "k"}))

    class Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    with pytest.raises(serpapi.SerpApiTransientError):
        run(REAL_HTTP_GET({"engine": "x"}))


def test_scan_endpoint_maps_rate_limit_and_quota_errors(client, auth_headers, monkeypatch, no_sleep):
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")
    serpapi.get_settings.cache_clear()
    scripted(monkeypatch, [serpapi.SerpApiRateLimitError("slow down", retry_after=3)])
    response = client.post("/api/markets/1/scan?force=true", headers=auth_headers)
    assert response.status_code == 429 and response.headers["retry-after"] == "3"
    scripted(monkeypatch, [serpapi.SerpApiQuotaError("out of searches")])
    assert client.post("/api/markets/1/scan?force=true", headers=auth_headers).status_code == 503
    scripted(monkeypatch, [serpapi.SerpApiTransientError("HTTP 500")])
    assert client.post("/api/markets/1/scan?force=true", headers=auth_headers).status_code == 502


# --------------------------------------------------------------------------- logging


SECRETS = [
    "SERPAPI key sk-ant-api03-ABCDEFGHIJKLMNOP",
    "GET https://serpapi.com/search.json?engine=x&api_key=abcdef123456&q=tents",
    "Authorization: Bearer abc.def.ghi",
    "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
    'password="hunter2hunter2"',
]


@pytest.mark.parametrize("text", SECRETS)
def test_redact_masks_common_secret_shapes(text):
    cleaned = redact(text)
    for fragment in ("ABCDEFGHIJKLMNOP", "abcdef123456", "abc.def.ghi", "c2lnbmF0dXJl", "hunter2"):
        assert fragment not in cleaned


def test_json_logs_are_structured_and_scrubbed_including_extra_fields_and_exceptions():
    stream = io.StringIO()
    configure_logging("INFO", "json", stream)
    log = logging.getLogger("app.test")
    log.info(
        "scan_finished",
        extra={"market_id": 3, "credits_used": 2, "api_key": "abc123secret", "note": "key sk-ant-XXXXXXXXXXXX"},
    )
    try:
        raise RuntimeError("failed calling https://serpapi.com/search.json?api_key=TOPSECRETKEY1234&q=x")
    except RuntimeError:
        log.exception("boom")
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    first, second = lines
    assert first["event"] == "scan_finished" and first["market_id"] == 3 and first["credits_used"] == 2
    assert first["api_key"] == "[redacted]" and "XXXXXXXXXXXX" not in json.dumps(first)
    assert "TOPSECRETKEY1234" not in json.dumps(second) and "exception" in second


def test_request_logging_records_path_status_and_id_but_not_query_or_body(client, auth_headers):
    stream = io.StringIO()
    configure_logging("INFO", "json", stream)
    response = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "Demo@123"})
    client.get("/api/markets?token=SHOULDNOTAPPEAR", headers=auth_headers)
    logs = stream.getvalue()
    assert response.headers["x-request-id"]
    entries = [json.loads(line) for line in logs.splitlines() if '"request"' in line]
    assert {"method", "path", "status", "duration_ms", "request_id"} <= set(entries[0])
    assert "Demo@123" not in logs and "SHOULDNOTAPPEAR" not in logs and auth_headers["Authorization"].split()[1] not in logs


def test_filter_never_raises_on_odd_records():
    record = logging.LogRecord("x", logging.INFO, "f", 1, "value %s %d", ("a", 2), None)
    assert RedactingFilter().filter(record) and record.msg == "value a 2"


# --------------------------------------------------------------------------- foreign keys


def test_foreign_keys_are_enforced_for_normal_connections():
    from app.db import get_connection, initialize_database

    initialize_database()
    with get_connection() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO markets (user_id, name, country, category) VALUES (999, 'x', 'US', 'y')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO scans (market_id, kind, scanned_at) VALUES (999, 'shopping', 'now')")


def test_existing_violations_are_reported_not_fatal(tmp_path, monkeypatch, caplog):
    from app.db import foreign_key_violations, initialize_database

    path = tmp_path / "legacy.db"
    monkeypatch.setenv("SHELF_SPACE_SPY_DB", str(path))
    initialize_database()
    raw = sqlite3.connect(path)  # foreign keys off: simulates old data written before enforcement
    raw.execute("INSERT INTO competitors (market_id, brand_name) VALUES (4242, 'Ghost')")
    raw.commit()
    assert foreign_key_violations(raw)
    raw.close()
    caplog.set_level(logging.WARNING)
    initialize_database()  # must not raise
    assert any("foreign_key_violations" in record.getMessage() for record in caplog.records)
