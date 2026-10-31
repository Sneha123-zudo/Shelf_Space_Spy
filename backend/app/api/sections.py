"""Read-only, market-scoped section endpoints: ``GET /markets/{id}/<section>``.

All of them check ownership (404 for another user's market), never write, and reuse the
deterministic calculations in ``app.services.sections``.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.api.markets import get_owned_market_row
from app.schemas import (
    DemandSection,
    EventsPage,
    ObservationsPage,
    OpportunitiesSection,
    PricingSection,
    ProductHistory,
    RecommendationsSection,
    ReplaySection,
    ReportsSection,
    ScansResponse,
    ShelfSection,
    SupplySection,
)
from app.services import sections

router = APIRouter(prefix="/markets/{market_id}", tags=["sections"])

CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]


def _market(market_id: int, user: dict[str, Any]) -> dict[str, Any]:
    return get_owned_market_row(market_id, int(user["id"]))


ScanId = Annotated[int | None, Query(description="Market Replay: show this stored shopping scan instead of the latest")]


def _replay(call, *args, **kwargs) -> dict[str, Any]:
    """Run a section builder, turning an unknown replay scan id into a 404."""
    try:
        return call(*args, **kwargs)
    except sections.UnknownScanError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found for this market") from error


@router.get("/scans", response_model=ScansResponse)
def scans(market_id: int, user: CurrentUser) -> dict[str, Any]:
    return sections.scans_section(_market(market_id, user))


@router.get("/events", response_model=EventsPage)
def events(
    market_id: int,
    user: CurrentUser,
    type: Annotated[list[str] | None, Query(description="Only these event types; repeat for several")] = None,
    include_low_confidence: Annotated[bool, Query(description="Also return low-confidence events")] = False,
    limit: Annotated[int, Query(ge=1, le=sections.MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    scan_id: ScanId = None,
) -> dict[str, Any]:
    return _replay(
        sections.events_section, _market(market_id, user), types=type or [],
        include_low_confidence=include_low_confidence, limit=limit, offset=offset, scan_id=scan_id,
    )


@router.get("/observations", response_model=ObservationsPage)
def observations(
    market_id: int,
    user: CurrentUser,
    keyword: str | None = None,
    brand: str | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Search title, retailer or brand")] = None,
    sort: Annotated[str, Query(pattern="^(position|price|price_desc|rating|reviews|title)$")] = "position",
    limit: Annotated[int, Query(ge=1, le=sections.MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    scan_id: ScanId = None,
) -> dict[str, Any]:
    return _replay(
        sections.observations_section, _market(market_id, user), keyword=keyword, brand=brand, search=q,
        sort=sort, limit=limit, offset=offset, scan_id=scan_id,
    )


@router.get("/products/{product_id}", response_model=ProductHistory)
def product(market_id: int, product_id: int, user: CurrentUser) -> dict[str, Any]:
    result = sections.product_section(_market(market_id, user), product_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return result


@router.get("/demand", response_model=DemandSection)
def demand(market_id: int, user: CurrentUser, scan_id: ScanId = None) -> dict[str, Any]:
    return _replay(sections.demand_section, _market(market_id, user), scan_id)


@router.get("/shelf", response_model=ShelfSection)
def shelf(market_id: int, user: CurrentUser, scan_id: ScanId = None) -> dict[str, Any]:
    return _replay(sections.shelf_section, _market(market_id, user), scan_id)


@router.get("/supply", response_model=SupplySection)
def supply(market_id: int, user: CurrentUser, scan_id: ScanId = None) -> dict[str, Any]:
    return _replay(sections.supply_section, _market(market_id, user), scan_id)


@router.get("/pricing", response_model=PricingSection)
def pricing(market_id: int, user: CurrentUser, scan_id: ScanId = None) -> dict[str, Any]:
    return _replay(sections.pricing_section, _market(market_id, user), scan_id)


@router.get("/opportunities", response_model=OpportunitiesSection)
def opportunities(market_id: int, user: CurrentUser, scan_id: ScanId = None) -> dict[str, Any]:
    return _replay(sections.opportunities_section, _market(market_id, user), scan_id)


@router.get("/reports", response_model=ReportsSection)
def reports(market_id: int, user: CurrentUser) -> dict[str, Any]:
    return sections.reports_section(_market(market_id, user))


@router.get("/replay", response_model=ReplaySection)
def replay(market_id: int, user: CurrentUser) -> dict[str, Any]:
    return sections.replay_section(_market(market_id, user))


@router.get("/recommendations", response_model=RecommendationsSection)
def recommendations(market_id: int, user: CurrentUser, scan_id: ScanId = None) -> dict[str, Any]:
    """Deterministic recommended actions with their evidence. Read-only; spends no SerpAPI credits."""
    return _replay(sections.recommendations_section, _market(market_id, user), scan_id)
