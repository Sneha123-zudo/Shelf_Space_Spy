"""Recommended actions: deterministic rules over data the app already collects.

No new SerpAPI calls and no language model. Every recommendation names the rule that produced it, lists
the exact figures it rests on (``evidence``), states what is missing (``limitations``) and carries a
confidence. They are hypotheses to investigate, not business advice: Google Shopping and Trends do not
show sales, margins or real customer demand.

Rules, in short (thresholds are the module constants below):

* ``investigate``      a probe scored Golden/Attractive with at least medium confidence and no blocking data gap
* ``collect_data``     the same signal, but the evidence is too thin to act on (few scans, unstable shelf,
                       very low search volume, no Trends comparison); also one market-level note for other gaps
* ``deprioritize``     a probe scored Crowded with at least medium confidence
* ``test_price``       enough priced listings to name a middle price band worth testing (a hypothesis)
* ``monitor_competitor`` / ``watch_entrant`` / ``check_exit``  trusted, real (never demo) competitor events
"""

from __future__ import annotations

from typing import Any

MIN_SCANS = 3
VOLATILE_STABILITY = 0.3
MIN_PRICED_LISTINGS = 10
HEAVY_DISCOUNT_RATE = 0.3
WIDE_HEADROOM_COMPONENT = 70.0
MAX_EVENT_RECOMMENDATIONS = 4

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
KIND_ORDER = {
    "investigate": 0,
    "monitor_competitor": 1,
    "watch_entrant": 2,
    "test_price": 3,
    "check_exit": 4,
    "collect_data": 5,
    "deprioritize": 6,
}
LEVELS = {"low": 0, "medium": 1, "high": 2}
SEVERITY = {"low": 0, "medium": 1, "high": 2}
COMPETITOR_PRICE_EVENTS = {"PRICE_DECREASE", "DISCOUNT_STARTED"}
STRONG_LABELS = {"Golden", "Attractive"}

Row = dict[str, Any]


def _ev(label: str, value: Any, kind: str) -> Row:
    """One traceable figure. ``kind`` tells the UI how to format it: pct, pct_signed, price, score, count, text."""
    return {"label": label, "value": value, "kind": kind}


def _level(opportunity: Row) -> str:
    level = str((opportunity.get("confidence") or {}).get("level") or "low").lower()
    return level if level in LEVELS else "low"


def _cap(level: str, ceiling: str) -> str:
    return level if LEVELS[level] <= LEVELS[ceiling] else ceiling


def _gaps(probe: str, *, scan_count: int, stability: dict[str, float | None], demand: Row | None) -> tuple[list[str], list[str]]:
    """(blocking gaps, informational gaps) for one probe. Blocking gaps stop an 'investigate' recommendation."""
    blocking: list[str] = []
    info: list[str] = []
    if scan_count < MIN_SCANS:
        blocking.append(f"Only {scan_count} scan{'s' if scan_count != 1 else ''} of history (at least {MIN_SCANS} recommended).")
    overlap = stability.get(probe.lower())
    if overlap is None:
        info.append("Shelf stability could not be measured yet.")
    elif overlap < VOLATILE_STABILITY:
        blocking.append(
            f"The shelf for this probe changes a lot between scans ({round(overlap * 100)}% of products carried over)."
        )
    signal = (demand or {}).get("signal") if demand else None
    if not signal or signal.get("growth_pct") is None:
        blocking.append("No usable Google Trends comparison for this probe.")
    elif signal.get("low_volume"):
        blocking.append("Search interest is very low, so relative changes are unreliable.")
    return blocking, info


def build_recommendations(
    *,
    opportunities: list[Row],
    demand: dict[str, Row],
    pricing: dict[str, Row],
    supply: dict[str, Row],
    stability: dict[str, float | None],
    events: list[Row],
    scan_count: int,
    uses_demo_data: bool,
) -> tuple[list[Row], list[str]]:
    """Return (recommendations, notes). All inputs are the dicts the section endpoints already produce,
    keyed by lower-cased probe keyword where a mapping."""
    items: list[Row] = []
    notes: list[str] = []
    market_gaps: dict[str, list[str]] = {}

    def add(
        kind: str,
        priority: str,
        probe: str | None,
        title: str,
        summary: str,
        evidence: list[Row],
        action: str,
        confidence: str,
        limitations: list[str],
        source: str,
        demo: bool = False,
    ) -> None:
        demo = demo or uses_demo_data
        items.append(
            {
                "id": f"{kind}:{(probe or 'market').lower()}:{len(items)}",
                "kind": kind,
                "priority": priority,
                "probe": probe,
                "title": title,
                "summary": summary,
                "evidence": evidence,
                "action": action,
                "confidence": _cap(confidence, "low") if demo else confidence,
                "limitations": limitations,
                "uses_demo_data": demo,
                "source": source,
            }
        )

    for opp in opportunities:
        probe = opp["keyword"]
        key = probe.lower()
        label, level = opp["label"], _level(opp)
        sup, price = supply.get(key) or {}, pricing.get(key) or {}
        blocking, info = _gaps(probe, scan_count=scan_count, stability=stability, demand=demand.get(key))
        evidence = [
            _ev("Opportunity score", round(opp["score"]), "score"),
            _ev("Search interest change", opp.get("demand_growth"), "pct_signed"),
            _ev("Competing products change", opp.get("supply_growth"), "pct_signed"),
        ]
        if sup:
            evidence.append(_ev("Top 3 brands' share of shelf", round(sup.get("top3_share", 0) * 100, 1), "pct"))
        base_limits = list(opp.get("caveats") or [])

        if label in STRONG_LABELS:
            if LEVELS[level] >= LEVELS["medium"] and not blocking:
                add(
                    "investigate",
                    "high" if label == "Golden" else "medium",
                    probe,
                    f"Investigate “{probe}”",
                    f"{label}: search interest is {'up' if (opp.get('demand_growth') or 0) >= 0 else 'down'} "
                    "while the observed shelf "
                    f"{'shows few new competing products' if (opp.get('supply_growth') or 0) <= 5 else 'is growing'}.",
                    evidence,
                    "Research this segment: read the top listings' reviews and prices, "
                    "and decide whether your range can compete.",
                    level,
                    base_limits + info,
                    "opportunity",
                )
            else:
                add(
                    "collect_data",
                    "medium",
                    probe,
                    f"Run another scan before acting on “{probe}”",
                    f"The score is {label}, but the evidence is too thin to rely on yet.",
                    evidence,
                    "Scan again after a few days and re-check this probe; confirm the signal holds before investing time.",
                    "low",
                    blocking + info + base_limits,
                    "opportunity",
                )
        elif label == "Crowded" and LEVELS[level] >= LEVELS["medium"]:
            add(
                "deprioritize",
                "low",
                probe,
                f"Deprioritize “{probe}”",
                "The shelf looks crowded relative to demand.",
                evidence,
                "Don't prioritize new entry here without a clear point of difference; "
                "keep it as a benchmark for pricing and competitors.",
                level,
                base_limits + info,
                "opportunity",
            )
        elif blocking or info:
            market_gaps[probe] = blocking + info

        # Pricing hypothesis: needs enough priced listings and a probe that is not crowded.
        if price.get("count", 0) >= MIN_PRICED_LISTINGS and label != "Crowded" and price.get("q1") and price.get("q3"):
            limits = ["Observed Shopping prices say nothing about margins, costs or whether shoppers will pay this."]
            heavy = (price.get("discount_rate") or 0) >= HEAVY_DISCOUNT_RATE
            if heavy:
                limits.append(
                    f"{round(price['discount_rate'] * 100)}% of listings show a discount, "
                    "so list prices may overstate what shoppers pay."
                )
            wide = (opp.get("components") or {}).get("price_headroom", 0) >= WIDE_HEADROOM_COMPONENT
            add(
                "test_price",
                "medium" if wide else "low",
                probe,
                f"Test a mid-band price for “{probe}”",
                "Half of the priced listings sit inside this range."
                + (" The gap above the median is wide, which may leave room for a premium option." if wide else ""),
                [
                    _ev("Priced listings", price["count"], "count"),
                    _ev("Lower quartile", price["q1"], "price"),
                    _ev("Median", price["median"], "price"),
                    _ev("Upper quartile", price["q3"], "price"),
                    _ev("Listings with a discount", round((price.get("discount_rate") or 0) * 100), "pct"),
                ],
                "Treat this as a hypothesis: test positioning inside the middle band before trying a premium or budget price.",
                _cap(level, "medium"),
                limits + info,
                "pricing",
            )

    # Competitor events: real, trusted (medium+ confidence) only.
    candidates: list[tuple[int, int, Row]] = []
    for event in events:
        meta = event.get("metadata") or {}
        if event.get("is_demo") or meta.get("compared_with_demo"):
            continue
        if meta.get("is_mine") or LEVELS.get(event["confidence"], 0) < LEVELS["medium"]:
            continue
        if event["type"] in COMPETITOR_PRICE_EVENTS | {"NEW_COMPETITOR", "REMOVED_PRODUCT"}:
            candidates.append((-SEVERITY.get(event["severity"], 0), -LEVELS.get(event["confidence"], 0), event))
    for _, _, event in sorted(candidates, key=lambda c: (c[0], c[1]))[:MAX_EVENT_RECOMMENDATIONS]:
        meta, summary, probe = (
            event.get("metadata") or {},
            (event.get("metadata") or {}).get("summary") or event["type"],
            event.get("keyword"),
        )
        who = event.get("brand") or "a competitor"
        evidence = [
            _ev("What happened", summary, "text"),
            _ev("Severity", event["severity"], "text"),
            _ev("Event confidence", event["confidence"], "text"),
        ]
        limits = [meta["caveat"]] if meta.get("caveat") else []
        priority = "high" if event["severity"] == "high" else "medium"
        if event["type"] in COMPETITOR_PRICE_EVENTS:
            add(
                "monitor_competitor",
                priority,
                probe,
                f"Review {who}'s price move" + (f" on “{probe}”" if probe else ""),
                summary,
                evidence,
                "Check the listing and the competitor's recent history before changing your own price.",
                event["confidence"],
                limits,
                "event",
            )
        elif event["type"] == "NEW_COMPETITOR":
            add(
                "watch_entrant",
                priority,
                probe,
                f"Look at the new brand {who}",
                summary,
                evidence,
                "Check what they sell, where they rank and at what price; rescan to see whether they stay.",
                event["confidence"],
                limits,
                "event",
            )
        else:
            add(
                "check_exit",
                "low",
                probe,
                f"Check why {event.get('product') or 'a product'} left the shelf",
                summary,
                evidence,
                "See whether it is out of stock or discontinued; on a sampled shelf it may only have dropped out of the results.",
                event["confidence"],
                limits,
                "event",
            )

    if market_gaps and not any(i["kind"] == "collect_data" for i in items):
        listed = "; ".join(f"“{probe}”: {gaps[0]}" for probe, gaps in list(market_gaps.items())[:3])
        add(
            "collect_data",
            "low",
            None,
            "Strengthen the evidence",
            "Some probes cannot be judged yet.",
            [_ev("Probes affected", len(market_gaps), "count")],
            "Run more scans over several days, or replace probes that have very low search interest.",
            "low",
            [listed],
            "data",
        )

    items.sort(key=lambda i: (PRIORITY_ORDER[i["priority"]], KIND_ORDER[i["kind"]], i["title"]))
    return items, notes
