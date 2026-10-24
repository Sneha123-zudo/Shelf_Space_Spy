from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

MAX_KEYWORDS = 5  # one Google Trends batch, so every probe stays comparable
MAX_COMPETITORS = 20


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _clean_unique(values: list[str], *, lower: bool) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = _clean_text(value)
        if lower:
            text = text.lower()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned


def _check_country(value: str) -> str:
    country = _clean_text(value).upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("country must be a two-letter ISO code such as US, IN or GB")
    return country


def _check_keywords(values: list[str]) -> list[str]:
    keywords = _clean_unique(values, lower=True)
    if not 1 <= len(keywords) <= MAX_KEYWORDS:
        raise ValueError(f"provide 1-{MAX_KEYWORDS} distinct keywords (one comparable Google Trends batch)")
    for keyword in keywords:
        if not 2 <= len(keyword) <= 100:
            raise ValueError("each keyword must be 2-100 characters")
        if "," in keyword:
            raise ValueError("keywords cannot contain commas (Google Trends uses them to separate queries)")
    return keywords


def _check_competitors(values: list[str]) -> list[str]:
    competitors = _clean_unique(values, lower=False)
    if len(competitors) > MAX_COMPETITORS:
        raise ValueError(f"at most {MAX_COMPETITORS} competitors")
    for name in competitors:
        if len(name) > 80:
            raise ValueError("competitor names must be at most 80 characters")
    return competitors


# --------------------------------------------------------------------------- auth


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    name: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# --------------------------------------------------------------------------- markets


class MarketSummary(BaseModel):
    id: int
    name: str
    country: str
    category: str
    brand_name: str | None = None
    keywords: list[str]
    competitors: list[str]


class MarketCreate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    category: str = Field(min_length=2, max_length=80)
    country: str
    brand_name: str | None = Field(default=None, max_length=80)
    keywords: list[str]
    competitors: list[str] = Field(default_factory=list)

    @field_validator("name", "brand_name")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        text = _clean_text(value or "")
        return text or None

    @field_validator("category")
    @classmethod
    def _category(cls, value: str) -> str:
        return _clean_text(value)

    @field_validator("country")
    @classmethod
    def _country(cls, value: str) -> str:
        return _check_country(value)

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        return _check_keywords(value)

    @field_validator("competitors")
    @classmethod
    def _competitors(cls, value: list[str]) -> list[str]:
        return _check_competitors(value)


class MarketUpdate(BaseModel):
    """Partial update; omitted fields are left unchanged. Send brand_name null or "" to clear it."""

    name: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    country: str | None = None
    brand_name: str | None = Field(default=None, max_length=80)
    keywords: list[str] | None = None
    competitors: list[str] | None = None

    @field_validator("name", "category")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = _clean_text(value)
        if len(text) < 2:
            raise ValueError("must be at least 2 characters")
        return text

    @field_validator("brand_name")
    @classmethod
    def _brand(cls, value: str | None) -> str | None:
        return _clean_text(value or "") or None

    @field_validator("country")
    @classmethod
    def _country(cls, value: str | None) -> str | None:
        return None if value is None else _check_country(value)

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_keywords(value)

    @field_validator("competitors")
    @classmethod
    def _competitors(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_competitors(value)


class SuggestRequest(BaseModel):
    category: str = Field(min_length=2, max_length=80)
    country: str
    force_refresh: bool = False

    @field_validator("category")
    @classmethod
    def _category(cls, value: str) -> str:
        return _clean_text(value)

    @field_validator("country")
    @classmethod
    def _country(cls, value: str) -> str:
        return _check_country(value)


class BrandSuggestion(BaseModel):
    name: str
    count: int
    inferred: bool


class SuggestResponse(BaseModel):
    keywords: list[str]
    brands: list[BrandSuggestion]
    credits_used: int = 0


# --------------------------------------------------------------------------- scans


class ScanOptions(BaseModel):
    force: bool = False
    dry_run: bool = False
    refresh: bool = False


class ScanResponse(BaseModel):
    """Superset of the original scan contract (market_id, scanned_at, keyword_count,
    products_observed, message)."""

    market_id: int
    scanned_at: str | None
    keyword_count: int
    products_observed: int
    message: str
    scan_id: int | None = None
    products: int = 0
    trend_points: int = 0
    events: int = 0
    opportunities: int = 0
    credits_used: int = 0
    cached_calls: int = 0
    estimated_credits: int = 0
    shopping_calls: int = 0
    trends_calls: int = 0
    dry_run: bool = False
    guard_active: bool = False
    minutes_since_last_scan: float | None = None
    warnings: list[str] = Field(default_factory=list)
    alerts_created: int = 0
    # Daily SerpAPI credit budget (UTC day). daily_budget 0 = no cap; budget_remaining is None then.
    daily_budget: int = 0
    credits_used_today: int = 0
    budget_remaining: int | None = None


class DemoHistoryResponse(BaseModel):
    market_id: int
    created: bool
    message: str
    demo_scans: int = 0
    trend_points: int = 0
    entering_products: int = 0
    new_competitor: str | None = None
    price_movers: int = 0
    events_detected: int = 0


# --------------------------------------------------------------------------- dashboard


class TopOpportunity(BaseModel):
    keyword: str
    score: float
    label: str


class Kpis(BaseModel):
    observed_skus: int
    new_products: int
    demand_change_pct: float | None = None
    top_opportunity: TopOpportunity | None = None


class MarketEvent(BaseModel):
    id: int
    type: str
    severity: str
    confidence: str
    brand: str | None = None
    product: str | None = None
    keyword: str | None = None
    previous_value: float | None = None
    current_value: float | None = None
    # Percent points from previous_value to current_value (price and discount events only).
    change_pct: float | None = None
    detected_at: str
    is_demo: bool
    summary: str
    caveat: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class ShelfShareEntry(BaseModel):
    brand: str
    share_pct: float
    is_mine: bool
    listings: int = 0


class CompetitorRow(BaseModel):
    brand: str
    is_mine: bool
    is_tracked: bool
    listings: int
    share_pct: float
    best_position: int | None = None
    avg_position: float | None = None
    median_price: float | None = None
    # Percent points of this brand's priced listings that show a strike-through discount.
    discount_rate_pct: float | None = None
    avg_rating: float | None = None
    total_reviews: int | None = None
    new_products: int = 0
    price_moves: int = 0


class Opportunity(BaseModel):
    keyword: str
    score: float
    label: str
    demand_growth: float | None = None
    supply_growth: float | None = None
    reasons: list[str]
    caveats: list[str]
    components: dict[str, float] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)


class Report(BaseModel):
    id: int
    market_id: int
    content: str
    source: str
    created_at: str
    # Public summary of how the brief was made: fallback_reason, llm_attempts, model,
    # rejected_claims, data_includes_demo. Never the stored evidence payload.
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class DashboardResponse(BaseModel):
    kpis: Kpis
    events: list[MarketEvent]
    shelf_share: list[ShelfShareEntry]
    competitors: list[CompetitorRow]
    opportunities: list[Opportunity]
    has_demo_history: bool
    report: Report | None = None
    latest_scan_at: str | None = None
    scan_count: int = 0


# --------------------------------------------------------------------------- section endpoints
# Read-only payloads for /markets/{id}/<section>. `has_data: false` means "nothing stored yet", not an error.


class ScanRef(BaseModel):
    id: int
    scanned_at: str
    is_demo: bool


class ScanRow(BaseModel):
    id: int
    kind: str
    scanned_at: str
    is_demo: bool
    credits_used: int
    products: int
    trend_points: int


class ScansResponse(BaseModel):
    items: list[ScanRow]
    total_credits: int


class EventsPage(BaseModel):
    items: list[MarketEvent]
    total: int
    limit: int
    offset: int
    # Low-confidence events matching the filters that are stored but not shown.
    hidden_low_confidence: int
    include_low_confidence: bool


class ObservationRow(BaseModel):
    id: int
    keyword: str
    title: str
    brand: str | None = None
    brand_inferred: bool = False
    source: str | None = None
    price: float | None = None
    old_price: float | None = None
    position: int | None = None
    rating: float | None = None
    # SerpAPI occasionally returns a non-integer count (e.g. 2.3), so this is not forced to int.
    reviews: float | None = None
    product_link: str | None = None
    thumbnail: str | None = None
    canonical_key: str | None = None
    product_row_id: int | None = None


class ObservationsPage(BaseModel):
    has_data: bool
    scan: ScanRef | None = None
    items: list[ObservationRow]
    total: int
    limit: int
    offset: int
    keywords: list[str]
    brands: list[str]


class ProductInfo(BaseModel):
    id: int
    canonical_key: str
    brand: str | None = None
    title: str
    first_seen_at: str
    last_seen_at: str


class ProductPoint(BaseModel):
    scan_id: int
    scanned_at: str
    is_demo: bool
    best_position: int | None = None
    lowest_price: float | None = None
    listings: int
    retailers: list[str]


class ProductHistory(BaseModel):
    product: ProductInfo
    history: list[ProductPoint]
    listings_stored: int


class TrendPointOut(BaseModel):
    week_start: str
    interest: float
    is_partial: bool


class DemandKeyword(BaseModel):
    keyword: str
    signal: dict[str, Any] | None = None
    series: list[TrendPointOut]


class DemandSection(BaseModel):
    has_data: bool
    scan: ScanRef | None = None
    keywords: list[DemandKeyword]


class ShelfSection(BaseModel):
    has_data: bool
    scan: ScanRef | None = None
    overall: list[dict[str, Any]]
    by_probe: dict[str, list[dict[str, Any]]]
    stability: dict[str, float | None]
    scans_compared: int = 0
    method: str = ""


class SupplySection(BaseModel):
    has_data: bool
    scan: ScanRef | None = None
    probes: list[dict[str, Any]]
    compared_with: ScanRef | None = None


class PricingSection(BaseModel):
    has_data: bool
    scan: ScanRef | None = None
    overall: dict[str, Any] | None = None
    probes: list[dict[str, Any]]
    brands: list[dict[str, Any]]


class OpportunitiesSection(BaseModel):
    has_data: bool
    scan: ScanRef | None = None
    trends_scan: ScanRef | None = None
    items: list[Opportunity]


class ReportsSection(BaseModel):
    items: list[Report]


# --------------------------------------------------------------------------- jobs, budget, alerts, replay


class BudgetStatus(BaseModel):
    daily_budget: int
    credits_used_today: int
    budget_remaining: int | None = None


class ScanJob(BaseModel):
    id: int
    market_id: int
    status: str  # queued | running | succeeded | failed
    force: bool
    refresh: bool
    progress_done: int
    progress_total: int
    message: str | None = None
    error: str | None = None
    # budget | too_soon | in_progress | not_configured | quota | rate_limited | serpapi | timeout | interrupted | internal
    error_code: str | None = None
    result: dict[str, Any] | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class AlertRule(BaseModel):
    id: int
    key: str
    name: str
    event_types: list[str]
    min_severity: str
    min_confidence: str
    scope: str
    enabled: bool


class AlertRuleUpdate(BaseModel):
    enabled: bool | None = None
    min_severity: str | None = Field(default=None, pattern="^(low|medium|high)$")
    min_confidence: str | None = Field(default=None, pattern="^(low|medium|high)$")


class AlertRow(BaseModel):
    id: int
    rule_id: int
    rule_name: str
    scan_id: int
    event_type: str
    severity: str
    confidence: str
    title: str
    detail: str | None = None
    brand: str | None = None
    keyword: str | None = None
    created_at: str
    read_at: str | None = None


class AlertsPage(BaseModel):
    items: list[AlertRow]
    total: int
    unread: int
    limit: int
    offset: int


class AlertsReadRequest(BaseModel):
    ids: list[int] | None = None  # omit or empty = mark everything read


class AlertsSummary(BaseModel):
    unread: int
    by_market: dict[int, int]


class ReplayScan(BaseModel):
    id: int
    scanned_at: str
    is_demo: bool
    products: int
    events: int


class ReplaySection(BaseModel):
    scans: list[ReplayScan]


class EvidenceItem(BaseModel):
    label: str
    value: Any = None
    kind: str  # pct | pct_signed | price | score | count | text


class Recommendation(BaseModel):
    id: str
    kind: str  # investigate | collect_data | deprioritize | test_price | monitor_competitor | watch_entrant | check_exit
    priority: str  # high | medium | low
    probe: str | None = None
    title: str
    summary: str
    evidence: list[EvidenceItem]
    action: str
    confidence: str
    limitations: list[str]
    uses_demo_data: bool
    source: str  # opportunity | pricing | event | data


class RecommendationsSection(BaseModel):
    has_data: bool
    scan: ScanRef | None = None
    items: list[Recommendation]
    notes: list[str]
