from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from app import repo
from app.ai.brief import generate_weekly_brief
from app.api.deps import get_current_user
from app.schemas import (
    DashboardResponse,
    DemoHistoryResponse,
    MarketCreate,
    MarketSummary,
    MarketUpdate,
    Report,
    ScanOptions,
    ScanResponse,
    SuggestRequest,
    SuggestResponse,
)
from app.seed_history import SeedHistoryError, seed_history
from app.services.analytics_service import analyze_scan, build_dashboard
from app.services.market_suggest import suggest_market
from app.services.scan import BudgetExceededError, ScanGuardError, run_scan
from app.services.serpapi import (
    SerpApiConfigurationError,
    SerpApiError,
    SerpApiQuotaError,
    SerpApiRateLimitError,
)

router = APIRouter(prefix="/markets", tags=["markets"])

CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]


def _summary(market: dict[str, Any]) -> MarketSummary:
    return MarketSummary(
        id=market["id"],
        name=market["name"],
        country=market["country"],
        category=market["category"],
        brand_name=market.get("brand_name"),
        keywords=market["keywords"],
        competitors=market["competitors"],
    )


def get_market_summaries(user_id: int) -> list[MarketSummary]:
    return [_summary(market) for market in repo.list_markets(user_id)]


def get_owned_market_row(market_id: int, user_id: int) -> dict[str, Any]:
    market = repo.get_market(market_id, user_id)
    if market is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found")
    return market


def get_owned_market(market_id: int, user_id: int) -> MarketSummary:
    return _summary(get_owned_market_row(market_id, user_id))


def _serpapi_http_error(error: Exception) -> HTTPException:
    if isinstance(error, (SerpApiConfigurationError, SerpApiQuotaError)):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))
    if isinstance(error, SerpApiRateLimitError):
        headers = {"Retry-After": str(int(error.retry_after))} if error.retry_after else None
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error), headers=headers)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error))


# --------------------------------------------------------------------------- CRUD


@router.get("", response_model=list[MarketSummary])
def list_markets(current_user: CurrentUser) -> list[MarketSummary]:
    return get_market_summaries(int(current_user["id"]))


@router.post("", response_model=MarketSummary, status_code=status.HTTP_201_CREATED)
def create_market(payload: MarketCreate, current_user: CurrentUser) -> MarketSummary:
    user_id = int(current_user["id"])
    name = payload.name or f"{payload.category.title()} ({payload.country})"
    if repo.find_market_by_name(user_id, name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"You already have a market named '{name}'")
    market_id = repo.create_market(
        user_id,
        name=name,
        country=payload.country,
        category=payload.category,
        brand_name=payload.brand_name,
        keywords=payload.keywords,
        competitors=payload.competitors,
    )
    return get_owned_market(market_id, user_id)


@router.post("/suggest", response_model=SuggestResponse)
async def suggest(payload: SuggestRequest, current_user: CurrentUser) -> SuggestResponse:
    del current_user  # authentication only
    try:
        result = await suggest_market(payload.category, payload.country, force_refresh=payload.force_refresh)
    except (SerpApiConfigurationError, SerpApiError) as error:
        raise _serpapi_http_error(error) from error
    return SuggestResponse(**result)


@router.get("/{market_id}", response_model=MarketSummary)
def get_market(market_id: int, current_user: CurrentUser) -> MarketSummary:
    return get_owned_market(market_id, int(current_user["id"]))


@router.put("/{market_id}", response_model=MarketSummary)
def update_market(market_id: int, payload: MarketUpdate, current_user: CurrentUser) -> MarketSummary:
    user_id = int(current_user["id"])
    existing = get_owned_market_row(market_id, user_id)
    if payload.name and payload.name != existing["name"] and repo.find_market_by_name(user_id, payload.name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"You already have a market named '{payload.name}'")
    changes: dict[str, Any] = {
        "name": payload.name,
        "country": payload.country,
        "category": payload.category,
        "keywords": payload.keywords,
        "competitors": payload.competitors,
    }
    if "brand_name" in payload.model_fields_set:
        changes["brand_name"] = payload.brand_name
    repo.update_market(market_id, **changes)
    analysis_inputs_changed = any(
        value is not None for value in (payload.keywords, payload.competitors)
    ) or "brand_name" in payload.model_fields_set
    latest = repo.latest_scan(market_id, "shopping")
    if analysis_inputs_changed and latest:
        analyze_scan(market_id, latest["id"])
    return get_owned_market(market_id, user_id)


@router.delete("/{market_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_market(market_id: int, current_user: CurrentUser) -> Response:
    get_owned_market_row(market_id, int(current_user["id"]))
    repo.delete_market(market_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- scanning


@router.post("/{market_id}/scan", response_model=ScanResponse)
async def scan_market(
    market_id: int,
    current_user: CurrentUser,
    force: Annotated[bool, Query(description="Scan even if the last live scan is recent")] = False,
    dry_run: Annotated[bool, Query(description="Only estimate the SerpAPI credit cost")] = False,
    refresh: Annotated[bool, Query(description="Bypass the SerpAPI response cache")] = False,
    options: Annotated[ScanOptions | None, Body()] = None,
) -> ScanResponse:
    market = get_owned_market_row(market_id, int(current_user["id"]))
    options = options or ScanOptions()
    try:
        result = await run_scan(
            market,
            force=force or options.force,
            dry_run=dry_run or options.dry_run,
            refresh=refresh or options.refresh,
        )
    except BudgetExceededError as error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error
    except ScanGuardError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except (SerpApiConfigurationError, SerpApiError) as error:
        raise _serpapi_http_error(error) from error
    return ScanResponse(**result)


@router.post("/{market_id}/demo-history", response_model=DemoHistoryResponse)
def create_demo_history(
    market_id: int,
    current_user: CurrentUser,
    force: Annotated[bool, Query(description="Replace existing demo history")] = False,
) -> DemoHistoryResponse:
    get_owned_market_row(market_id, int(current_user["id"]))
    try:
        result = seed_history(market_id, force=force)
    except SeedHistoryError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return DemoHistoryResponse(**result)


# --------------------------------------------------------------------------- insight


@router.get("/{market_id}/dashboard", response_model=DashboardResponse)
def dashboard(
    market_id: int,
    current_user: CurrentUser,
    include_low_confidence: Annotated[bool, Query(description="Also show low-confidence events")] = False,
) -> DashboardResponse:
    market = get_owned_market_row(market_id, int(current_user["id"]))
    return DashboardResponse(**build_dashboard(market, include_low_confidence=include_low_confidence))


@router.post("/{market_id}/reports/weekly", response_model=Report, status_code=status.HTTP_201_CREATED)
def create_weekly_report(market_id: int, current_user: CurrentUser) -> Report:
    market = get_owned_market_row(market_id, int(current_user["id"]))
    report = generate_weekly_brief(market, build_dashboard(market))
    return Report(**report)


@router.get("/{market_id}/reports/latest", response_model=Report | None)
def latest_report(market_id: int, current_user: CurrentUser) -> Report | None:
    get_owned_market_row(market_id, int(current_user["id"]))
    report = repo.latest_report(market_id)
    return Report(**report) if report else None
