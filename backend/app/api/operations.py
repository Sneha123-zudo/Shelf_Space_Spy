"""Operations and action endpoints: credit budget, background scan jobs and alerts."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from app import repo
from app.api.deps import get_current_user
from app.api.markets import get_owned_market_row
from app.schemas import (
    AlertRule,
    AlertRuleUpdate,
    AlertsPage,
    AlertsReadRequest,
    AlertsSummary,
    BudgetStatus,
    ScanJob,
    ScanOptions,
)
from app.services import jobs, sections
from app.services.scan import BudgetExceededError, ScanGuardError, budget_status

router = APIRouter(tags=["operations"])

CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]


def _market(market_id: int, user: dict[str, Any]) -> dict[str, Any]:
    return get_owned_market_row(market_id, int(user["id"]))


@router.get("/credits", response_model=BudgetStatus)
def credits(user: CurrentUser) -> dict[str, Any]:
    """Today's live SerpAPI credit usage against DAILY_CREDIT_BUDGET."""
    del user  # authentication only
    return budget_status()


# --------------------------------------------------------------------------- scan jobs


@router.post("/markets/{market_id}/scan-jobs", response_model=ScanJob)
async def start_scan(
    market_id: int,
    user: CurrentUser,
    response: Response,
    options: Annotated[ScanOptions | None, Body()] = None,
) -> dict[str, Any]:
    """Start a background scan. 202 for a new job; 200 with the running job if one is already active.
    Refusals (recency guard 409, daily budget 429) happen here, before any job or credit exists.

    ``async`` because the job is an asyncio task on the server's event loop."""
    market = _market(market_id, user)
    options = options or ScanOptions()
    try:
        job, created = jobs.start_scan_job(market, force=options.force, refresh=options.refresh)
    except BudgetExceededError as error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error
    except ScanGuardError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
    return job


@router.get("/markets/{market_id}/scan-jobs/latest", response_model=ScanJob | None)
def latest_scan_job(market_id: int, user: CurrentUser) -> dict[str, Any] | None:
    _market(market_id, user)
    return repo.latest_scan_job(market_id)


@router.get("/markets/{market_id}/scan-jobs/{job_id}", response_model=ScanJob)
def get_scan_job(market_id: int, job_id: int, user: CurrentUser) -> dict[str, Any]:
    _market(market_id, user)
    job = repo.get_scan_job(job_id, market_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan job not found")
    return job


# --------------------------------------------------------------------------- alerts


@router.get("/alerts/summary", response_model=AlertsSummary)
def alerts_summary(user: CurrentUser) -> dict[str, Any]:
    counts = repo.unread_alert_counts(int(user["id"]))
    return {"unread": sum(counts.values()), "by_market": counts}


@router.get("/markets/{market_id}/alerts", response_model=AlertsPage)
def list_alerts(
    market_id: int,
    user: CurrentUser,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=sections.MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    return sections.alerts_section(_market(market_id, user), unread_only=unread_only, limit=limit, offset=offset)


@router.post("/markets/{market_id}/alerts/read")
def mark_read(market_id: int, user: CurrentUser, body: AlertsReadRequest | None = None) -> dict[str, int]:
    _market(market_id, user)
    return {"updated": repo.mark_alerts_read(market_id, (body.ids if body else None) or None)}


@router.get("/markets/{market_id}/alert-rules", response_model=list[AlertRule])
def list_rules(market_id: int, user: CurrentUser) -> list[dict[str, Any]]:
    _market(market_id, user)
    return repo.list_alert_rules(market_id)


@router.patch("/markets/{market_id}/alert-rules/{rule_id}", response_model=AlertRule)
def update_rule(market_id: int, rule_id: int, body: AlertRuleUpdate, user: CurrentUser) -> dict[str, Any]:
    _market(market_id, user)
    rule = repo.update_alert_rule(
        market_id, rule_id, enabled=body.enabled, min_severity=body.min_severity, min_confidence=body.min_confidence
    )
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")
    return rule
