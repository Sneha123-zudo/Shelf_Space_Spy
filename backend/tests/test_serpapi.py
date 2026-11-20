import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import serpapi
from tests import factories
from tests.conftest import NetworkBlocked


def run(coro):
    return asyncio.run(coro)


def test_cache_key_ignores_api_key_and_order():
    a = serpapi.cache_key({"engine": "google_shopping", "q": "x", "api_key": "secret"})
    b = serpapi.cache_key({"q": "x", "engine": "google_shopping"})
    assert a == b


def test_missing_key_raises_configuration_error_without_network():
    with pytest.raises(serpapi.SerpApiConfigurationError):
        run(serpapi.search(serpapi.shopping_params("tents", "US")))


def test_network_is_blocked_in_tests(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")
    serpapi.get_settings.cache_clear()
    with pytest.raises(NetworkBlocked):
        run(serpapi.search(serpapi.shopping_params("tents", "US")))


def test_second_call_is_served_from_disk_cache(fake_serpapi):
    params = serpapi.shopping_params("tents", "US")
    first = run(serpapi.search(params))
    second = run(serpapi.search(params))
    assert (first.from_cache, first.credits) == (False, 1)
    assert (second.from_cache, second.credits) == (True, 0)
    assert fake_serpapi.count("google_shopping") == 1
    assert second.payload["shopping_results"] == first.payload["shopping_results"]
    cached_files = list(serpapi.cache_dir().rglob("*.json"))
    assert len(cached_files) == 1
    assert "fake-test-key" not in cached_files[0].read_text(encoding="utf-8")


def test_force_refresh_and_max_age_bypass_cache(fake_serpapi):
    params = serpapi.shopping_params("tents", "US")
    run(serpapi.search(params))
    run(serpapi.search(params, force_refresh=True))
    assert fake_serpapi.count("google_shopping") == 2
    # age the cached entry beyond max_age
    path = next(serpapi.cache_dir().rglob("*.json"))
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["cached_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(entry), encoding="utf-8")
    assert serpapi.is_cached(params, timedelta(hours=1)) is False
    assert serpapi.is_cached(params) is True
    run(serpapi.search(params, max_age=timedelta(hours=1)))
    assert fake_serpapi.count("google_shopping") == 3


def test_no_results_error_is_an_empty_answer_but_other_errors_raise(fake_serpapi, monkeypatch):
    async def no_results(params):
        return {"error": "Google hasn't returned any results for this query."}

    monkeypatch.setattr(serpapi, "_http_get", no_results)
    assert run(serpapi.shopping_search("zzzz", "US")) == []

    async def broken(params):
        return {"error": "Invalid API key."}

    monkeypatch.setattr(serpapi, "_http_get", broken)
    with pytest.raises(serpapi.SerpApiError):
        run(serpapi.search(serpapi.shopping_params("other", "US")))


def test_trends_params_and_batches():
    params = serpapi.trends_params(["a", "b"], "gb", "today 12-m")
    assert params["q"] == "a,b" and params["geo"] == "GB" and params["data_type"] == "TIMESERIES"
    assert serpapi.shopping_params("x", "GB")["gl"] == "uk"
    assert serpapi.trends_batches(list("abcdefg")) == [list("abcde"), list("fg")]
    with pytest.raises(ValueError):
        serpapi.trends_params(list("abcdef"), "US")


def test_parse_trends_marks_partial_weeks():
    payload = factories.trends_payload(["Trail Shoes", "road shoes"], weeks=6, partial_last=True)
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    points = serpapi.parse_trends(payload, ["trail shoes", "road shoes"], "b1", now)
    assert len(points) == 14
    assert {p["keyword"] for p in points} == {"trail shoes", "road shoes"}
    partial = [p for p in points if p["is_partial"]]
    assert len(partial) == 2 and all(p["week_start"] == "2026-10-11" for p in partial)
    # a week that has not finished yet is partial even without Google's flag
    unflagged = factories.trends_payload(["x"], weeks=2, end=datetime(2026, 10, 11).date(), partial_last=False)
    assert serpapi.parse_trends(unflagged, ["x"], "b2", now)[-1]["is_partial"] is True
