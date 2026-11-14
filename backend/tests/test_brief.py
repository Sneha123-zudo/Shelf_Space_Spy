from app import repo
from app.ai.brief import (
    build_inputs,
    generate_text,
    generate_weekly_brief,
    numbers_in,
    template_brief,
    unsupported_numbers,
)
from app.db import initialize_database

MARKET = {"id": 1, "name": "Tents", "category": "Camping tents", "country": "US", "brand_name": "Summit",
          "keywords": ["camping tent"], "competitors": []}

DASHBOARD = {
    "kpis": {"observed_skus": 37, "new_products": 2, "demand_change_pct": 12.5,
             "top_opportunity": {"keyword": "camping tent", "score": 71.3, "label": "Attractive"}},
    "events": [{"type": "PRICE_DECREASE", "severity": "medium", "confidence": "high", "brand": "Coleman",
                "summary": "Coleman Sundome at REI dropped 12.5% (1,200 to 1,050).", "is_demo": True}],
    "shelf_share": [{"brand": "Coleman", "share_pct": 22.4, "is_mine": False, "listings": 6}],
    "opportunities": [{"keyword": "camping tent", "score": 71.3, "label": "Attractive", "demand_growth": 18.0,
                       "supply_growth": -4.2, "reasons": ["Search interest up 18% (last 4 weeks vs prior 8)."],
                       "caveats": ["Based on a sampled shelf."]}],
    "has_demo_history": True,
    "latest_scan_at": "2026-10-14T12:00:00+00:00",
    "scan_count": 5,
}


def test_number_extraction_normalizes_format():
    assert numbers_in({"a": 1200.0, "b": [-4.2, "dropped 12.5% (1,200 to 1,050)"], "c": True}) >= {
        "1200", "4.2", "12.5", "1050"
    }
    inputs = build_inputs(MARKET, DASHBOARD)
    assert unsupported_numbers("Demand rose 18% and 37 SKUs were seen.", inputs) == []
    assert unsupported_numbers("Demand rose 19% across 1,200 listings.", inputs) == ["19"]
    assert unsupported_numbers("1. First point\n2. Second point", inputs) == []  # list markers are ignored


def test_template_is_deterministic_and_uses_only_input_numbers():
    inputs = build_inputs(MARKET, DASHBOARD)
    text = template_brief(inputs)
    assert text == template_brief(inputs)
    assert "Coleman" in text and "Attractive" in text
    assert "demo" not in text.lower() and "credit" not in text.lower()  # no demo or credit wording in the brief
    assert unsupported_numbers(text, inputs) == []


def test_llm_output_accepted_when_numbers_check_out():
    inputs = build_inputs(MARKET, DASHBOARD)
    content, source, diagnostics = generate_text(inputs, lambda system, user: "Demand is up 18%. Demo data included.")
    assert source == "llm" and content.startswith("Demand") and diagnostics["llm_attempts"] == 1


def test_llm_invented_number_is_regenerated_once_then_template():
    inputs = build_inputs(MARKET, DASHBOARD)
    drafts = iter(["Demand is up 25%.", "Demand is up 18%."])
    prompts = []

    def narrator(system, user):
        prompts.append(user)
        return next(drafts)

    content, source, diagnostics = generate_text(inputs, narrator)
    assert source == "llm" and content == "Demand is up 18%."
    assert "25" in prompts[1] and diagnostics["attempt_1_unsupported_numbers"] == ["25"]

    content, source, _ = generate_text(inputs, lambda s, u: "Up 99% and 98%.")
    assert source == "template" and content == template_brief(inputs)


def test_llm_errors_fall_back_to_template():
    def failing(system, user):
        raise RuntimeError("overloaded")

    content, source, diagnostics = generate_text(build_inputs(MARKET, DASHBOARD), failing)
    assert source == "template" and diagnostics["llm_error"] == "overloaded"


def _mock_anthropic(handler):
    import httpx2
    from anthropic import DefaultHttpxClient

    return DefaultHttpxClient(transport=httpx2.MockTransport(handler))


def _message(text, stop_reason="end_turn"):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def test_anthropic_narrator_request_shape_without_network():
    import json as _json

    import httpx2

    from app.ai.brief import SYSTEM_PROMPT, anthropic_narrator

    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["beta"] = request.headers.get("anthropic-beta")
        seen["body"] = _json.loads(request.content)
        return httpx2.Response(200, json=_message("Demand is up 18%."))

    narrate = anthropic_narrator("test-key", "claude-sonnet-5-5", http_client=_mock_anthropic(handler))
    assert narrate(SYSTEM_PROMPT, "json here") == "Demand is up 18%."
    assert seen["url"].endswith("/v1/messages?beta=true") or seen["url"].endswith("/v1/messages")
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    body = seen["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["fallbacks"] == "default"
    assert body["system"] == SYSTEM_PROMPT and body["messages"][0]["role"] == "user"
    assert "thinking" not in body and "temperature" not in body


def test_anthropic_refusal_falls_back_to_template():
    import httpx2

    from app.ai.brief import anthropic_narrator

    narrate = anthropic_narrator(
        "test-key", "claude-sonnet-5-5",
        http_client=_mock_anthropic(lambda request: httpx2.Response(200, json=_message("", "refusal"))),
    )
    inputs = build_inputs(MARKET, DASHBOARD)
    content, source, diagnostics = generate_text(inputs, narrate)
    assert source == "template" and "declined" in diagnostics["llm_error"]


def test_no_api_key_persists_template_report():
    initialize_database()
    user_id = repo.create_user("o@example.com", "O", "x$y")
    market_id = repo.create_market(user_id, name="Tents", country="US", category="Camping tents",
                                   brand_name="Summit", keywords=["camping tent"], competitors=[])
    report = generate_weekly_brief({**MARKET, "id": market_id}, DASHBOARD)
    assert report["source"] == "template"
    assert repo.latest_report(market_id)["content"] == report["content"]


# --------------------------------------------------------------------------- guard: dates and claims


def _inputs_with_period():
    return build_inputs(MARKET, DASHBOARD)  # period 2026-10-07 .. 2026-10-14


def test_dates_matching_the_period_are_accepted_in_every_common_format():
    from app.ai.brief import unsupported_claims

    inputs = _inputs_with_period()
    for text in (
        "Covers 2026-10-07 to 2026-10-14.",
        "The week of October 7 through October 14, 2026.",
        "From Oct 7th to 14 October 2026 the shelf moved.",
        "Written in 2026.",
    ):
        assert unsupported_claims(text, inputs) == [], text


def test_invented_dates_are_rejected_even_when_the_day_number_appears_elsewhere():
    from app.ai.brief import unsupported_claims

    inputs = _inputs_with_period()
    assert unsupported_claims("Prices changed on October 9.", inputs) == ["October 9"]
    assert unsupported_claims("Since 2026-09-01 demand rose.", inputs) == ["2026-09-01"]
    # 12 appears in the inputs (12.5%), but "October 12" is not a date the input contains.
    assert unsupported_claims("On October 12 the shelf shifted.", inputs) == ["October 12"]
    assert unsupported_claims("Back in October 2025 it was calm.", inputs) == ["2025"]  # a year the input never states


def test_spelled_out_figures_and_computed_claims_are_rejected():
    from app.ai.brief import unsupported_claims

    inputs = _inputs_with_period()
    assert unsupported_claims("Prices fell by twenty percent.", inputs) == ["twenty percent"]
    assert unsupported_claims("Visibility doubled this week.", inputs) == ["doubled"]
    assert unsupported_claims("Demand is up eighteen percent.", inputs) == []  # 18 is in the data, so it is supported
    assert unsupported_claims("Demand is up thirty percent.", inputs) == ["thirty percent"]
    assert unsupported_claims("Two products entered.", inputs) == []  # no counted noun: not a figure claim
    assert unsupported_claims("Demand rose 18%.", inputs) == []


def test_llm_text_with_an_invented_date_falls_back_with_a_reason():
    inputs = _inputs_with_period()
    content, source, diagnostics = generate_text(inputs, lambda s, u: "Coleman cut prices on October 9.")
    assert source == "template" and diagnostics["fallback_reason"] == "unsupported_claims"
    assert diagnostics["attempt_2_unsupported_numbers"] == ["October 9"]


# --------------------------------------------------------------------------- inputs and diagnostics


def test_fallback_reasons_are_explicit_and_secrets_are_redacted():
    inputs = _inputs_with_period()
    _, source, diagnostics = generate_text(inputs, None)
    assert source == "template" and diagnostics == {"fallback_reason": "no_api_key"}
    _, _, diagnostics = generate_text(inputs, None, skip_reason="sdk_not_installed")
    assert diagnostics["fallback_reason"] == "sdk_not_installed"

    def failing(system, user):
        raise RuntimeError("401 invalid x-api-key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")

    _, _, diagnostics = generate_text(inputs, failing)
    assert diagnostics["fallback_reason"] == "llm_error"
    assert "ABCDEFGHIJKLMNOP" not in diagnostics["llm_error_summary"]
    assert diagnostics["llm_error_summary"].startswith("RuntimeError")


def test_inputs_carry_confidence_caveats_stability_hidden_count_and_demo_status():
    dashboard = {
        **DASHBOARD,
        "opportunities": [{**DASHBOARD["opportunities"][0], "confidence": {"level": "low", "points": 1}}],
        "events": [
            *[{"type": "PRICE_DECREASE", "severity": "low", "confidence": "high", "brand": "B", "summary": f"Cut {i}.",
               "keyword": "camping tent", "caveat": "Same offer.", "is_demo": False} for i in range(9)],
            {"type": "REMOVED_PRODUCT", "severity": "high", "confidence": "medium", "brand": "Coleman",
             "summary": "No longer on the shelf: Sundome.", "keyword": "camping tent",
             "caveat": "Sampled shelf.", "is_demo": False},
        ],
    }
    extras = {"probe_stability": {"camping tent": 0.12}, "low_confidence_events_hidden": 3, "latest_scan_is_demo": True}
    inputs = build_inputs(MARKET, dashboard, extras)
    assert any(e["type"] == "REMOVED_PRODUCT" for e in inputs["events"])  # not crowded out by 9 price events
    assert len(inputs["events"]) <= 8
    assert all("confidence" in e and "caveat" in e for e in inputs["events"])
    assert inputs["opportunities"][0]["confidence"] == {"level": "low", "points": 1}
    assert inputs["opportunities"][0]["caveats"]
    assert inputs["probe_stability"] == [{"keyword": "camping tent", "overlap": 0.12, "label": "volatile"}]
    assert inputs["low_confidence_events_hidden"] == 3
    assert inputs["latest_scan_is_demo"] is True and inputs["has_demo_history"] is True

    text = template_brief(inputs)
    assert "likely noise" in text and "low-confidence" in text and "confidence medium" in text
    assert unsupported_numbers(text, inputs) == []


def test_generate_weekly_brief_sends_stored_evidence_to_the_model_and_exposes_diagnostics():
    import json as _json

    from tests.test_integrity import BASE, add_scan, shelf  # reuse the synthetic scan helpers

    initialize_database()
    user_id = repo.create_user("brief@example.com", "B", "x$y")
    market_id = repo.create_market(
        user_id, name="Brief", country="US", category="Widgets", brand_name=None, keywords=["probe"], competitors=[]
    )
    full, reduced = shelf("Alpha one", "Beta two", "Gamma three"), shelf("Alpha one", "Beta two")
    for day, rows in enumerate([full, full, reduced, reduced, reduced]):
        add_scan(market_id, day, rows, demo=(day == 4))
    market = repo.get_market(market_id)
    from app.services.analytics_service import build_dashboard

    seen = {}

    def narrator(system, user):
        seen["system"], seen["user"] = system, user
        return "Gamma three left the shelf."

    report = generate_weekly_brief(market, build_dashboard(market, include_low_confidence=True), narrator)
    payload = _json.loads(seen["user"].split("JSON:\n\n", 1)[1])
    assert any(e["type"] == "REMOVED_PRODUCT" for e in payload["events"])
    assert payload["probe_stability"][0]["keyword"] == "probe"
    assert payload["latest_scan_is_demo"] is True
    assert "Never hide uncertainty" in seen["system"]
    assert report["source"] == "llm"
    assert report["diagnostics"]["data_includes_demo"] is True
    assert "inputs_json" not in report and "inputs" not in report

    assert repo.latest_report(market_id)["diagnostics"] == report["diagnostics"]
    assert BASE  # imported helper constants are intentionally shared with the integrity tests
