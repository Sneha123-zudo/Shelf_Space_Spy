"""Alerts: confidence-aware notifications built from stored market events.

Rules (per market, see ``repo.DEFAULT_ALERT_RULES``) choose event types, a minimum severity, a minimum
*confidence* and a scope (your brand / competitors / anyone). Only real scans alert: events measured
against demo history never do. Evaluation is idempotent (unique per rule + scan + event key), so a
re-run cannot duplicate an alert. Alerts are evaluated after a live scan, never when a page is read.
"""

from __future__ import annotations

from typing import Any

from app import repo

LEVELS = {"low": 0, "medium": 1, "high": 2}


def _dedupe_key(event: dict[str, Any]) -> str:
    meta = event.get("metadata") or {}
    identity = meta.get("offer_key") or meta.get("canonical_key") or event.get("product") or ""
    return "|".join(str(part) for part in (event["type"], identity, event.get("brand") or "", event.get("keyword") or ""))


def rule_matches(rule: dict[str, Any], event: dict[str, Any]) -> bool:
    if not rule["enabled"] or event["type"] not in rule["event_types"]:
        return False
    if LEVELS.get(event["severity"], 0) < LEVELS[rule["min_severity"]]:
        return False
    if LEVELS.get(event["confidence"], 0) < LEVELS[rule["min_confidence"]]:
        return False
    mine = bool((event.get("metadata") or {}).get("is_mine"))
    return rule["scope"] == "any" or (rule["scope"] == "mine" and mine) or (rule["scope"] == "competitors" and not mine)


def evaluate_alerts(market_id: int, scan_id: int) -> int:
    """Create alerts for the events of one real scan. Returns how many new alerts were stored."""
    scan = next((s for s in repo.list_scans(market_id, "shopping") if s["id"] == scan_id), None)
    if scan is None or scan["is_demo"]:
        return 0
    rules = [rule for rule in repo.list_alert_rules(market_id) if rule["enabled"]]
    if not rules:
        return 0
    alerts: list[dict[str, Any]] = []
    for event in repo.events_for_scan(scan_id):
        meta = event.get("metadata") or {}
        if event["is_demo"] or meta.get("compared_with_demo"):
            continue
        for rule in rules:
            if rule_matches(rule, event):
                alerts.append({
                    "market_id": market_id, "rule_id": rule["id"], "scan_id": scan_id, "dedupe_key": _dedupe_key(event),
                    "event_type": event["type"], "severity": event["severity"], "confidence": event["confidence"],
                    "title": meta.get("summary") or event["type"].replace("_", " ").title(),
                    "detail": meta.get("caveat"), "brand": event.get("brand"), "keyword": event.get("keyword"),
                })
    return repo.insert_alerts(alerts)
