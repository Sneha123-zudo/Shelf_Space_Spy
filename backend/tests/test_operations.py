"""Daily credit budget, background scan jobs, alerts and Market Replay."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import repo
from app.core.config import get_settings
from app.db import get_connection, initialize_database
from app.services import jobs, serpapi
from app.services.alerts import evaluate_alerts
from tests.test_integrity import add_scan, shelf

TERMINAL = {"succeeded", "failed"}


def wait_for_job(client, headers, market_id, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/markets/{market_id}/scan-jobs/{job_id}", headers=headers).json()
        if job["status"] in TERMINAL:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job did not finish: {job}")


def set_env(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()


# --------------------------------------------------------------------------- daily credit budget


def test_dry_run_reports_budget_and_the_cap_is_a_hard_limit_that_force_cannot_bypass(client, auth_headers, fake_serpapi, monkeypatch):
    set_env(monkeypatch, DAILY_CREDIT_BUDGET=4)  # the demo market has 2 probes = 3 credits per live scan
    dry = client.post("/api/markets/1/scan?dry_run=true", headers=auth_headers).json()
    assert dry["daily_budget"] == 4 and dry["credits_used_today"] == 0 and dry["budget_remaining"] == 4

    first = client.post("/api/markets/1/scan?force=true", headers=auth_headers)
    assert first.status_code == 200 and first.json()["credits_used"] == 3
    assert client.get("/api/credits", headers=auth_headers).json() == {
        "daily_budget": 4, "credits_used_today": 3, "budget_remaining": 1,
    }

    calls_before = len(fake_serpapi.calls)
    refused = client.post("/api/markets/1/scan?force=true&refresh=true", headers=auth_headers)
    assert refused.status_code == 429 and "daily scan limit" in refused.json()["detail"] and "credit" not in refused.json()["detail"].lower()
    assert len(fake_serpapi.calls) == calls_before  # nothing was spent
    refused_job = client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True, "refresh": True})
    assert refused_job.status_code == 429


def test_cached_replays_cost_nothing_and_are_allowed_when_the_budget_is_spent(client, auth_headers, fake_serpapi, monkeypatch):
    set_env(monkeypatch, DAILY_CREDIT_BUDGET=3)
    assert client.post("/api/markets/1/scan?force=true", headers=auth_headers).status_code == 200
    replay = client.post("/api/markets/1/scan?force=true", headers=auth_headers)  # answered from the disk cache
    assert replay.status_code == 200 and replay.json()["credits_used"] == 0


def test_budget_zero_disables_the_cap_and_demo_scans_do_not_count(client, auth_headers, fake_serpapi, monkeypatch):
    set_env(monkeypatch, DAILY_CREDIT_BUDGET=0)
    assert client.post("/api/markets/1/scan?force=true&refresh=true", headers=auth_headers).status_code == 200
    assert client.post("/api/markets/1/scan?force=true&refresh=true", headers=auth_headers).status_code == 200
    status = client.get("/api/credits", headers=auth_headers).json()
    assert status["daily_budget"] == 0 and status["budget_remaining"] is None and status["credits_used_today"] == 6
    client.post("/api/markets/1/demo-history", headers=auth_headers)
    assert client.get("/api/credits", headers=auth_headers).json()["credits_used_today"] == 6


def test_budget_counts_only_the_current_utc_day(client, auth_headers, fake_serpapi, monkeypatch):
    set_env(monkeypatch, DAILY_CREDIT_BUDGET=3)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1, minutes=5)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO scans (market_id, kind, scanned_at, is_demo, credits_used) VALUES (1, 'shopping', ?, 0, 99)",
            (yesterday.isoformat(),),
        )
    assert client.get("/api/credits", headers=auth_headers).json()["credits_used_today"] == 0
    assert client.post("/api/markets/1/scan?force=true", headers=auth_headers).status_code == 200


# --------------------------------------------------------------------------- background scan jobs


def test_job_runs_with_progress_and_finishes_with_the_scan_result(client, auth_headers, fake_serpapi):
    response = client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True})
    assert response.status_code == 202
    job = wait_for_job(client, auth_headers, 1, response.json()["id"])
    assert job["status"] == "succeeded" and job["error"] is None
    assert job["progress_total"] == 4 and job["progress_done"] == 4  # 2 shopping + 1 trends + analysis
    assert job["result"]["scan_id"] and job["result"]["credits_used"] == 3 and job["finished_at"]
    assert client.get("/api/markets/1/scan-jobs/latest", headers=auth_headers).json()["id"] == job["id"]
    assert len(client.get("/api/markets/1/observations", headers=auth_headers).json()["items"]) > 0


def test_a_second_start_while_running_returns_the_same_job_and_spends_nothing_extra(client, auth_headers, fake_serpapi, monkeypatch):
    release = threading.Event()
    original = serpapi._http_get

    async def slow(params):
        while not release.is_set():
            await asyncio.sleep(0.01)
        return await original(params)

    monkeypatch.setattr(serpapi, "_http_get", slow)  # the gate sits in front of the fake HTTP layer
    first = client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True})
    second = client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True, "refresh": True})
    assert first.status_code == 202 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert client.get("/api/markets/1/scan-jobs/latest", headers=auth_headers).json()["status"] in {"queued", "running"}
    release.set()
    job = wait_for_job(client, auth_headers, 1, first.json()["id"])
    assert job["status"] == "succeeded"
    assert fake_serpapi.count("google_shopping") == 2  # one scan's worth, not two


def test_refusals_happen_before_a_job_exists(client, auth_headers, fake_serpapi):
    first = client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True})
    wait_for_job(client, auth_headers, 1, first.json()["id"])
    too_soon = client.post("/api/markets/1/scan-jobs", headers=auth_headers)  # no force, last scan seconds ago
    assert too_soon.status_code == 409
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scan_jobs").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (serpapi.SerpApiQuotaError("out of searches"), "quota"),
        (serpapi.SerpApiRateLimitError("slow down"), "rate_limited"),
        (serpapi.SerpApiTransientError("HTTP 500"), "serpapi"),
    ],
)
def test_job_failures_end_in_a_failed_state_with_a_safe_message(client, auth_headers, fake_serpapi, monkeypatch, error, code):
    async def broken(params):
        raise error

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(serpapi, "_http_get", broken)
    monkeypatch.setattr(serpapi, "_sleep", no_sleep)
    job = wait_for_job(client, auth_headers, 1, client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True}).json()["id"])
    assert job["status"] == "failed" and job["error_code"] == code and job["error"]
    assert "Traceback" not in job["error"] and job["finished_at"]
    # a failed job releases the market: a new one can start
    assert client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True}).status_code == 202


def test_unexpected_crash_is_reported_generically_not_with_internals(client, auth_headers, fake_serpapi, monkeypatch):
    async def broken(params):
        raise ValueError("secret internal detail /Users/someone/path")

    monkeypatch.setattr(serpapi, "_http_get", broken)
    job = wait_for_job(client, auth_headers, 1, client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True}).json()["id"])
    assert job["error_code"] == "internal" and "secret internal detail" not in job["error"]


def test_a_hung_job_is_cancelled_by_the_timeout(client, auth_headers, fake_serpapi, monkeypatch):
    set_env(monkeypatch, SCAN_JOB_TIMEOUT_SECONDS=1)

    async def hang(params):
        await asyncio.sleep(60)

    monkeypatch.setattr(serpapi, "_http_get", hang)
    job = wait_for_job(client, auth_headers, 1, client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True}).json()["id"], timeout=8)
    assert job["status"] == "failed" and job["error_code"] == "timeout"
    assert client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True}).status_code == 202  # market is free again


def test_jobs_left_unfinished_by_a_restart_are_failed_at_startup(client, auth_headers):
    queued = repo.create_scan_job(1, force=False, refresh=False)
    running = repo.create_scan_job(1, force=False, refresh=False)
    repo.update_scan_job(running["id"], status="running")
    assert jobs.recover_interrupted_jobs() == 2
    for job_id in (queued["id"], running["id"]):
        job = repo.get_scan_job(job_id)
        assert job["status"] == "failed" and job["error_code"] == "interrupted"
    assert repo.active_scan_job(1) is None


def test_jobs_are_owner_scoped(client, auth_headers, make_user, fake_serpapi):
    other = make_user("intruder@example.com")
    job_id = client.post("/api/markets/1/scan-jobs", headers=auth_headers, json={"force": True}).json()["id"]
    assert client.post("/api/markets/1/scan-jobs", headers=other).status_code == 404
    assert client.get(f"/api/markets/1/scan-jobs/{job_id}", headers=other).status_code == 404
    assert client.get(f"/api/markets/1/scan-jobs/{job_id + 99}", headers=auth_headers).status_code == 404
    wait_for_job(client, auth_headers, 1, job_id)


# --------------------------------------------------------------------------- alerts

STABLE = ("Alpha one", "Beta two", "Gamma three", "Delta four", "Epsilon five", "Zeta six", "Eta seven", "Theta eight")


@pytest.fixture
def alert_market() -> int:
    initialize_database()
    user_id = repo.create_user("alerts@example.com", "A", "x$y")
    return repo.create_market(
        user_id, name="Alerts", country="US", category="Widgets", brand_name="Alpha", keywords=["probe"], competitors=["Beta"]
    )


def stable_history(market_id, days=5):
    for day in range(days):
        add_scan(market_id, day, shelf(*STABLE))


def cut_price(market_id, brand_title, new_price, day=5):
    rows = shelf(*STABLE)
    for row in rows:
        if row["title"] == brand_title:
            row["price"] = new_price
    return add_scan(market_id, day, rows)


def alerts_for(market_id):
    return repo.alerts_page(market_id, unread_only=False, limit=100, offset=0)[0]


def test_default_rules_exist_for_every_market_and_are_not_recreated_after_a_change(alert_market):
    rules = repo.list_alert_rules(alert_market)
    assert {r["key"] for r in rules} >= {"competitor_price_cuts", "new_competitors", "my_brand_moves", "products_removed"}
    repo.update_alert_rule(alert_market, rules[0]["id"], enabled=False)
    repo.ensure_all_default_alert_rules()
    again = {r["key"]: r for r in repo.list_alert_rules(alert_market)}
    assert again[rules[0]["key"]]["enabled"] is False and len(again) == len(rules)


def test_a_competitor_price_cut_alerts_once_and_reruns_do_not_duplicate(alert_market):
    stable_history(alert_market)
    scan_id = cut_price(alert_market, "Beta two", 60.0)
    assert evaluate_alerts(alert_market, scan_id) == 1
    assert evaluate_alerts(alert_market, scan_id) == 0
    (alert,) = alerts_for(alert_market)
    assert alert["event_type"] == "PRICE_DECREASE" and alert["rule_name"].startswith("Competitor price cuts")
    assert alert["confidence"] == "high" and "Beta two" in alert["title"] and alert["read_at"] is None


def test_scope_separates_my_brand_from_competitors(alert_market):
    stable_history(alert_market)
    scan_id = cut_price(alert_market, "Alpha one", 60.0)
    evaluate_alerts(alert_market, scan_id)
    (alert,) = alerts_for(alert_market)
    assert alert["rule_name"].startswith("Your brand")  # not "Competitor price cuts"


def test_demo_scans_and_demo_comparisons_never_alert(alert_market):
    for day in range(5):
        add_scan(alert_market, day, shelf(*STABLE), demo=True)
    demo_scan = cut_price(alert_market, "Beta two", 60.0, day=5)
    assert evaluate_alerts(alert_market, demo_scan) == 0  # compared against demo history
    for day in range(6, 11):
        add_scan(alert_market, day, shelf(*STABLE))
    scan_id = cut_price(alert_market, "Beta two", 50.0, day=11)
    assert evaluate_alerts(alert_market, scan_id) == 1


def test_low_confidence_events_do_not_alert_until_the_rule_allows_it(alert_market):
    from tests.factories import obs

    for day in range(4):  # a volatile shelf: nothing carries over, so a rank move is low confidence
        add_scan(alert_market, day, [
            *[obs(f"Noise{day}x{i}", position=i + 2, brand=f"N{day}x{i}") for i in range(7)],
            obs("Alpha tracked", position=12, brand="Alpha"),
        ])
    scan_id = add_scan(alert_market, 4, [
        obs("Alpha tracked", position=2, brand="Alpha"),
        *[obs(f"Noise4x{i}", position=i + 3, brand=f"N4x{i}") for i in range(7)],
    ])
    rank = [e for e in repo.events_for_scan(scan_id) if e["type"] == "RANK_GAIN"]
    assert rank and rank[0]["confidence"] == "low"
    rule = next(r for r in repo.list_alert_rules(alert_market) if r["key"] == "my_brand_moves")
    assert evaluate_alerts(alert_market, scan_id) == 0  # my brand's move, but too uncertain to alert
    repo.update_alert_rule(alert_market, rule["id"], min_confidence="low")
    assert evaluate_alerts(alert_market, scan_id) == 1  # the user chose to accept low-confidence moves


def test_a_disabled_rule_is_silent(alert_market):
    stable_history(alert_market)
    for rule in repo.list_alert_rules(alert_market):
        repo.update_alert_rule(alert_market, rule["id"], enabled=False)
    assert evaluate_alerts(alert_market, cut_price(alert_market, "Beta two", 60.0)) == 0


def test_scan_creates_alerts_and_the_api_lists_reads_and_summarizes_them(client, auth_headers, fake_serpapi):
    market_id = 1
    for day in range(5):
        add_scan(market_id, day, shelf(*STABLE))
    scan_id = cut_price(market_id, "Beta two", 40.0)
    evaluate_alerts(market_id, scan_id)

    page = client.get(f"/api/markets/{market_id}/alerts", headers=auth_headers).json()
    assert page["total"] == 1 and page["unread"] == 1 and page["items"][0]["title"]
    assert client.get("/api/alerts/summary", headers=auth_headers).json() == {"unread": 1, "by_market": {"1": 1}}
    assert client.post(f"/api/markets/{market_id}/alerts/read", headers=auth_headers, json={"ids": [9999]}).json() == {"updated": 0}
    assert client.post(f"/api/markets/{market_id}/alerts/read", headers=auth_headers).json() == {"updated": 1}
    assert client.get("/api/alerts/summary", headers=auth_headers).json()["unread"] == 0
    assert client.get(f"/api/markets/{market_id}/alerts?unread_only=true", headers=auth_headers).json()["total"] == 0


def test_rules_api_updates_validates_and_is_owner_scoped(client, auth_headers, make_user):
    other = make_user("intruder@example.com")
    rules = client.get("/api/markets/1/alert-rules", headers=auth_headers).json()
    rule_id = rules[0]["id"]
    patched = client.patch(f"/api/markets/1/alert-rules/{rule_id}", headers=auth_headers, json={"enabled": False, "min_confidence": "high"})
    assert patched.status_code == 200 and patched.json()["enabled"] is False and patched.json()["min_confidence"] == "high"
    assert client.patch(f"/api/markets/1/alert-rules/{rule_id}", headers=auth_headers, json={"min_severity": "extreme"}).status_code == 422
    assert client.patch("/api/markets/1/alert-rules/99999", headers=auth_headers, json={"enabled": True}).status_code == 404
    assert client.get("/api/markets/1/alert-rules", headers=other).status_code == 404
    assert client.get("/api/markets/1/alerts", headers=other).status_code == 404


def test_deleting_a_market_with_jobs_alerts_and_rules_leaves_nothing_behind(client, auth_headers, fake_serpapi):
    for day in range(5):
        add_scan(1, day, shelf(*STABLE))
    evaluate_alerts(1, cut_price(1, "Beta two", 40.0))
    repo.create_scan_job(1, force=False, refresh=False)
    assert client.delete("/api/markets/1", headers=auth_headers).status_code == 204
    with get_connection() as conn:
        for table in ("alerts", "alert_rules", "scan_jobs", "scans", "products"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE market_id = 1").fetchone()[0] == 0, table


# --------------------------------------------------------------------------- market replay


def probe_market(client, auth_headers) -> int:
    """A market whose probe keyword matches the synthetic scans ("probe")."""
    return client.post("/api/markets", headers=auth_headers, json={
        "category": "Widgets", "country": "US", "keywords": ["probe"], "competitors": [],
    }).json()["id"]


def test_replay_lists_scans_and_every_section_can_show_an_older_one(client, auth_headers):
    market = probe_market(client, auth_headers)
    for day in range(3):
        add_scan(market, day, shelf(*STABLE[:4 + day]))
    scans = client.get(f"/api/markets/{market}/replay", headers=auth_headers).json()["scans"]
    assert len(scans) == 3 and [s["products"] for s in scans] == [4, 5, 6]
    oldest, newest = scans[0], scans[-1]

    old_obs = client.get(f"/api/markets/{market}/observations?scan_id={oldest['id']}&limit=200", headers=auth_headers).json()
    new_obs = client.get(f"/api/markets/{market}/observations?limit=200", headers=auth_headers).json()
    assert old_obs["total"] == 4 and new_obs["total"] == 6 and old_obs["scan"]["id"] == oldest["id"]
    assert new_obs["scan"]["id"] == newest["id"]

    for section in ("shelf", "supply", "pricing", "opportunities", "demand"):
        response = client.get(f"/api/markets/{market}/{section}?scan_id={oldest['id']}", headers=auth_headers)
        assert response.status_code == 200, section
    assert client.get(f"/api/markets/{market}/supply?scan_id={oldest['id']}", headers=auth_headers).json()["probes"][0]["results"] == 4
    assert client.get(f"/api/markets/{market}/supply?scan_id={newest['id']}", headers=auth_headers).json()["probes"][0]["results"] == 6


def test_replay_events_are_those_detected_at_that_scan(client, auth_headers):
    for day in range(5):
        add_scan(1, day, shelf(*STABLE))
    cut_id = cut_price(1, "Beta two", 40.0)
    at_cut = client.get(f"/api/markets/1/events?scan_id={cut_id}&include_low_confidence=true", headers=auth_headers).json()
    assert [e["type"] for e in at_cut["items"]] == ["PRICE_DECREASE"]
    scans = client.get("/api/markets/1/replay", headers=auth_headers).json()["scans"]
    earlier_url = f"/api/markets/1/events?scan_id={scans[1]['id']}&include_low_confidence=true"
    earlier = client.get(earlier_url, headers=auth_headers).json()
    assert earlier["total"] == 0
    assert scans[-1]["events"] == 1


def test_replay_rejects_unknown_scans_and_other_markets_scans(client, auth_headers):
    other_market = client.post("/api/markets", headers=auth_headers, json={
        "category": "Lamps", "country": "US", "keywords": ["lamp"], "competitors": [],
    }).json()["id"]
    foreign = add_scan(other_market, 0, shelf(*STABLE))
    add_scan(1, 0, shelf(*STABLE))
    for section in ("shelf", "supply", "pricing", "opportunities", "demand", "observations", "events"):
        assert client.get(f"/api/markets/1/{section}?scan_id={foreign}", headers=auth_headers).status_code == 404, section
        assert client.get(f"/api/markets/1/{section}?scan_id=99999", headers=auth_headers).status_code == 404, section
    assert client.get(f"/api/markets/{other_market}/shelf?scan_id={foreign}", headers=auth_headers).status_code == 200


def test_replay_reads_never_write(client, auth_headers):
    for day in range(3):
        add_scan(1, day, shelf(*STABLE))
    before = {t: get_row_count(t) for t in ("scans", "market_events", "alerts", "opportunities", "scan_jobs")}
    scan_id = client.get("/api/markets/1/replay", headers=auth_headers).json()["scans"][0]["id"]
    for section in ("shelf", "supply", "pricing", "opportunities", "demand", "observations", "events"):
        client.get(f"/api/markets/1/{section}?scan_id={scan_id}", headers=auth_headers)
    assert before == {t: get_row_count(t) for t in before}


def get_row_count(table):
    with get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
