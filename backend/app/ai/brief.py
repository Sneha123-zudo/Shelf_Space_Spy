"""Weekly market brief.

Input is the computed dashboard JSON only. With ``ANTHROPIC_API_KEY`` set, Claude narrates
it; the prompt forbids new numbers and a post-check verifies that every number in the
text appears in the input. A failed check regenerates once, then falls back to the
deterministic template. Without a key (or on any API error) the template is used.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from app import repo
from app.core.config import get_settings
from app.intelligence.common import fmt_number

logger = logging.getLogger(__name__)

MAX_EVENTS = 8
MAX_BRANDS = 6
EVENT_FIELDS = ("type", "severity", "confidence", "brand", "keyword", "summary", "caveat", "is_demo")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You write a short weekly market brief for a small consumer brand.

You receive one JSON object computed by a deterministic analytics pipeline from a sampled
Google Shopping shelf and Google Trends. Rules:
- Use only facts and numbers that appear in the JSON. Never calculate, estimate, round
  differently or invent any number, date, rank or percentage. If a number is not in the
  JSON, describe the point without a number.
- Keep every caveat that matters: sampled shelf visibility is not market share.
- Never hide uncertainty. Each event and opportunity has a confidence; say so when it is
  not high. If "probe_stability" marks a probe as volatile, say its rank and new-product
  signals are likely noise. If "low_confidence_events_hidden" is above zero, mention that
  some uncertain changes were left out. Do not call an event an exit or a launch unless its
  type says so.
- Structure: a one-line headline, then sections "What changed", "Shelf", "Opportunities"
  and "Watch-outs". Use "-" bullets, never numbered lists. At most 250 words.
- Plain, direct language for a busy founder. No hype."""

Narrator = Callable[[str, str], str]


# --------------------------------------------------------------------------- inputs


def _pick_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Up to MAX_EVENTS events, keeping the feed's order but making sure every event type present
    (including removals) is represented before any type gets a second slot."""
    chosen: list[dict[str, Any]] = []
    seen_types: set[str] = set()
    for event in events:
        if event.get("type") not in seen_types:
            seen_types.add(event.get("type", ""))
            chosen.append(event)
    for event in events:
        if len(chosen) >= MAX_EVENTS:
            break
        if event not in chosen:
            chosen.append(event)
    chosen = chosen[:MAX_EVENTS]
    return [event for event in events if event in chosen]


def stability_label(value: float | None) -> str:
    if value is None:
        return "unknown"
    return "stable" if value >= 0.6 else "moderate" if value >= 0.3 else "volatile"


def build_inputs(market: dict[str, Any], dashboard: dict[str, Any], extras: dict[str, Any] | None = None) -> dict[str, Any]:
    """The only data the narrator sees. ``extras`` carries probe stability, the hidden low-confidence
    count and the latest scan's demo flag (read-only values the dashboard does not include)."""
    extras = extras or {}
    latest = dashboard.get("latest_scan_at")
    period_end = latest[:10] if latest else None
    period_start = (
        (datetime.fromisoformat(latest) - timedelta(days=7)).date().isoformat() if latest else None
    )
    stability = extras.get("probe_stability") or {}
    return {
        "market": {
            "name": market["name"],
            "category": market["category"],
            "country": market["country"],
            "my_brand": market.get("brand_name"),
            "probe_keywords": market.get("keywords", []),
        },
        "period": {"start": period_start, "end": period_end, "days": 7},
        "method": {"recent_weeks": 4, "prior_weeks": 8, "score_max": 100},
        "kpis": dashboard.get("kpis", {}),
        "events": [
            {key: event.get(key) for key in EVENT_FIELDS}
            for event in _pick_events(dashboard.get("events", []))
        ],
        "low_confidence_events_hidden": int(extras.get("low_confidence_events_hidden") or 0),
        "probe_stability": [
            {"keyword": keyword, "overlap": value, "label": stability_label(value)}
            for keyword, value in stability.items()
        ],
        "shelf_share": dashboard.get("shelf_share", [])[:MAX_BRANDS],
        "opportunities": [
            {key: opp.get(key) for key in (
                "keyword", "score", "label", "demand_growth", "supply_growth", "reasons", "caveats", "confidence",
            )}
            for opp in dashboard.get("opportunities", [])
        ],
        "has_demo_history": bool(dashboard.get("has_demo_history")),
        "latest_scan_is_demo": bool(extras.get("latest_scan_is_demo")),
        "scan_count": dashboard.get("scan_count", 0),
    }


# --------------------------------------------------------------------------- number guard

_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)


def _canonical(number: str) -> str:
    value = float(number.replace(",", ""))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def numbers_in(value: Any) -> set[str]:
    """Every number found in a JSON-like value, in canonical form (no sign, no separators)."""
    found: set[str] = set()
    if isinstance(value, bool) or value is None:
        return found
    if isinstance(value, (int, float)):
        found.add(_canonical(f"{abs(value):.4f}"))
        found.add(_canonical(f"{abs(value):.1f}"))  # the UI shows one decimal: 12.34 -> 12.3
        return found
    if isinstance(value, str):
        return {_canonical(match) for match in _NUMBER.findall(value)}
    if isinstance(value, dict):
        for item in value.values():
            found |= numbers_in(item)
        return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found |= numbers_in(item)
    return found


_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_FIRST = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.IGNORECASE)
_DAY_FIRST = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\.?(?:,?\s+(\d{{4}}))?\b", re.IGNORECASE)

_SPELLED = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}
_SPELLED_COUNT = re.compile(
    rf"\b({'|'.join(_SPELLED)})(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?\s+"
    r"(?:percent|per cent|points?|positions?|places?|products?|listings?|brands?|retailers?|scans?|probes?)\b",
    re.IGNORECASE,
)
_COMPUTED_WORDS = re.compile(r"\b(doubled|tripled|quadrupled|halved|twice as|double the|half of)\b", re.IGNORECASE)


def _input_dates(inputs: dict[str, Any]) -> set[date]:
    dates: set[date] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for year, month, day in _ISO_DATE.findall(value):
                try:
                    dates.add(date(int(year), int(month), int(day)))
                except ValueError:
                    pass
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(inputs)
    return dates


def _check_dates(text: str, known: set[date]) -> tuple[str, list[str]]:
    """Remove date expressions from ``text``. A date is accepted only if it matches a date in the
    input (a year, when written, must match too); anything else is reported as an invented date."""
    invented: list[str] = []

    def accept(month: int, day: int, year: str | None) -> bool:
        return any(d.month == month and d.day == day and (year is None or d.year == int(year)) for d in known)

    def iso(match: re.Match[str]) -> str:
        try:
            ok = date(int(match.group(1)), int(match.group(2)), int(match.group(3))) in known
        except ValueError:
            ok = False
        if not ok:
            invented.append(match.group(0))
        return " "

    def month_first(match: re.Match[str]) -> str:
        if not accept(_MONTHS[match.group(1).lower()], int(match.group(2)), match.group(3)):
            invented.append(match.group(0))
        return " "

    def day_first(match: re.Match[str]) -> str:
        if not accept(_MONTHS[match.group(2).lower()], int(match.group(1)), match.group(3)):
            invented.append(match.group(0))
        return " "

    text = _ISO_DATE.sub(iso, text)
    text = _MONTH_FIRST.sub(month_first, text)
    text = _DAY_FIRST.sub(day_first, text)
    return text, invented


def unsupported_claims(text: str, inputs: dict[str, Any]) -> list[str]:
    """Everything in ``text`` that the input does not support: numbers, invented dates, spelled-out
    figures ("twenty percent") and arithmetic the model must not do ("doubled", "half of")."""
    allowed = numbers_in(inputs)
    remaining, dates = _check_dates(_LIST_MARKER.sub("", text), _input_dates(inputs))
    problems = set(dates)
    problems |= {_canonical(n) for n in _NUMBER.findall(remaining)} - allowed
    for match in _SPELLED_COUNT.finditer(remaining):
        if str(_SPELLED[match.group(1).lower()]) not in allowed:
            problems.add(match.group(0).lower())
    problems |= {match.group(0).lower() for match in _COMPUTED_WORDS.finditer(remaining)}
    return sorted(problems)


def unsupported_numbers(text: str, inputs: dict[str, Any]) -> list[str]:
    """Backward-compatible name for :func:`unsupported_claims`."""
    return unsupported_claims(text, inputs)


# --------------------------------------------------------------------------- template


def template_brief(inputs: dict[str, Any]) -> str:
    market, kpis = inputs["market"], inputs["kpis"]
    lines = [f"Weekly brief: {market['name']} ({market['category']}, {market['country']})"]
    if inputs["period"]["start"]:
        lines.append(f"Period: {inputs['period']['start']} to {inputs['period']['end']}")
    lines.append("")

    headline = f"{fmt_number(kpis.get('observed_skus'))} products observed on the sampled shelf"
    headline += f", {fmt_number(kpis.get('new_products'))} new this week"
    if kpis.get("demand_change_pct") is not None:
        headline += f"; average search interest change across probes {fmt_number(kpis['demand_change_pct'])}%"
    lines.append(headline + ".")
    top = kpis.get("top_opportunity")
    if top:
        lines.append(f"Top opportunity: \"{top['keyword']}\" ({top['label']}, {fmt_number(top['score'])}/100).")

    lines += ["", "What changed"]
    for event in inputs["events"]:
        note = f" (confidence {event['confidence']})" if event.get("confidence") not in (None, "high") else ""
        lines.append(f"- {event['summary']}{note}")
    if not inputs["events"]:
        lines.append("- No significant changes detected.")
    if inputs.get("low_confidence_events_hidden"):
        lines.append("- Some low-confidence changes were left out of this list.")

    lines += ["", "Shelf"]
    for row in inputs["shelf_share"]:
        mine = " (your brand)" if row.get("is_mine") else ""
        lines.append(f"- {row['brand']}{mine}: {fmt_number(row['share_pct'])}% of shelf visibility")
    if not inputs["shelf_share"]:
        lines.append("- No shelf data yet.")

    lines += ["", "Opportunities"]
    for opp in inputs["opportunities"]:
        reasons = " ".join(opp.get("reasons") or [])
        lines.append(f"- {opp['keyword']}: {opp['label']} ({fmt_number(opp['score'])}/100). {reasons}".rstrip())
    if not inputs["opportunities"]:
        lines.append("- Run a scan to score opportunities.")

    caveats = sorted({c for opp in inputs["opportunities"] for c in opp.get("caveats") or []})
    volatile = [p["keyword"] for p in inputs.get("probe_stability", []) if p.get("label") == "volatile"]
    lines += ["", "Watch-outs"]
    if volatile:
        lines.append(
            f"- The shelf for {', '.join(volatile)} changes a lot between scans, "
            "so rank and new-product signals there are likely noise."
        )
    lines += [f"- {caveat}" for caveat in caveats] or ["- Visibility on a sampled shelf is not market share."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- LLM


def anthropic_narrator(api_key: str, model: str, http_client: Any = None) -> Narrator:
    """Claude narrator. ``http_client`` (an ``anthropic.DefaultHttpxClient``) is for tests."""
    import anthropic  # imported lazily so the template path never needs the SDK

    options: dict[str, Any] = {"api_key": api_key, "timeout": 60.0, "max_retries": 2}
    if http_client is not None:
        options["http_client"] = http_client
    client = anthropic.Anthropic(**options)

    def narrate(system: str, user: str) -> str:
        response = client.beta.messages.create(
            model=model,
            max_tokens=2000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("The model declined to write this brief.")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise RuntimeError("The model returned no text.")
        return text

    return narrate


def _user_prompt(inputs: dict[str, Any], rejected: list[str] | None = None) -> str:
    prompt = "Write this week's brief from this JSON:\n\n" + json.dumps(inputs, indent=2, ensure_ascii=False)
    if rejected:
        prompt += (
            "\n\nYour previous draft used numbers that are not in the JSON: "
            + ", ".join(rejected)
            + ". Rewrite it using only numbers that appear in the JSON."
        )
    return prompt


_SECRET_LIKE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|[A-Za-z0-9_\-]{32,})")


def _safe_error(error: Exception) -> str:
    """Short, secret-free description of an SDK/network failure for diagnostics."""
    message = _SECRET_LIKE.sub("[redacted]", str(error))[:200]
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def generate_text(
    inputs: dict[str, Any], narrator: Narrator | None, *, skip_reason: str = "no_api_key"
) -> tuple[str, str, dict[str, Any]]:
    """Return (content, source, diagnostics). Tries the LLM twice, then the template.

    ``diagnostics["fallback_reason"]`` (template only) is one of ``no_api_key``, ``sdk_not_installed``,
    ``llm_error`` or ``unsupported_claims``.
    """
    diagnostics: dict[str, Any] = {}
    if narrator is None:
        diagnostics["fallback_reason"] = skip_reason
        return template_brief(inputs), "template", diagnostics
    rejected: list[str] | None = None
    for attempt in (1, 2):
        try:
            text = narrator(SYSTEM_PROMPT, _user_prompt(inputs, rejected))
        except Exception as error:  # any SDK/network failure falls back to the template
            logger.warning("LLM brief failed: %s", type(error).__name__)
            diagnostics["llm_error"] = str(error)[:300]
            diagnostics["fallback_reason"] = "llm_error"
            diagnostics["llm_error_summary"] = _safe_error(error)
            return template_brief(inputs), "template", diagnostics
        rejected = unsupported_claims(text, inputs)
        if not rejected:
            diagnostics["llm_attempts"] = attempt
            return text, "llm", diagnostics
        diagnostics[f"attempt_{attempt}_unsupported_numbers"] = rejected
    diagnostics["fallback_reason"] = "unsupported_claims"
    return template_brief(inputs), "template", diagnostics


def _extras(market: dict[str, Any]) -> dict[str, Any]:
    """Read-only evidence the dashboard lacks. Failures here never block a brief."""
    try:
        from app.services.sections import events_section, shelf_section

        shelf = shelf_section(market)
        events = events_section(market, types=[], include_low_confidence=False, limit=1, offset=0)
        return {
            "probe_stability": shelf.get("stability") or {},
            "low_confidence_events_hidden": events["hidden_low_confidence"],
            "latest_scan_is_demo": bool((shelf.get("scan") or {}).get("is_demo")),
        }
    except Exception:
        logger.exception("Could not collect extra brief evidence for market %s", market.get("id"))
        return {}


def generate_weekly_brief(market: dict[str, Any], dashboard: dict[str, Any], narrator: Narrator | None = None) -> dict[str, Any]:
    settings = get_settings()
    skip_reason = "no_api_key"
    if narrator is None and settings.anthropic_api_key:
        try:
            narrator = anthropic_narrator(settings.anthropic_api_key, settings.anthropic_model)
        except ImportError:
            logger.warning("anthropic package not installed; using the template brief")
            skip_reason = "sdk_not_installed"
    inputs = build_inputs(market, dashboard, _extras(market))
    content, source, diagnostics = generate_text(inputs, narrator, skip_reason=skip_reason)
    if source == "llm":
        diagnostics["model"] = settings.anthropic_model
    return repo.insert_report(market["id"], content, {"inputs": inputs, "diagnostics": diagnostics}, source)
