"""Background scan jobs: a scan runs as an asyncio task and the UI polls its progress.

* One active job per market: starting another returns the running one instead of spending credits twice.
* Everything that can refuse a scan (recency guard, daily credit budget) is checked *before* a job is
  created, so a refusal is an immediate HTTP error rather than a failed job.
* A job is cancelled after ``SCAN_JOB_TIMEOUT_SECONDS`` and a job left unfinished by a restart is
  marked failed at start-up. Errors are stored as a short code plus a safe message, never a traceback.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app import repo
from app.core.config import get_settings
from app.services.scan import BudgetExceededError, ScanGuardError, ScanInProgressError, precheck_scan, run_scan
from app.services.serpapi import SerpApiConfigurationError, SerpApiError, SerpApiQuotaError, SerpApiRateLimitError

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task[None]] = set()


def error_info(error: BaseException) -> tuple[str, str]:
    """(code, message safe to show a user)."""
    if isinstance(error, BudgetExceededError):
        return "budget", str(error)
    if isinstance(error, ScanInProgressError):
        return "in_progress", str(error)
    if isinstance(error, ScanGuardError):
        return "too_soon", str(error)
    if isinstance(error, SerpApiConfigurationError):
        return "not_configured", str(error)
    if isinstance(error, SerpApiQuotaError):
        return "quota", str(error)
    if isinstance(error, SerpApiRateLimitError):
        return "rate_limited", str(error)
    if isinstance(error, SerpApiError):
        return "serpapi", str(error)
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return "timeout", "The scan took too long and was cancelled. Any data already saved is kept."
    return "internal", "The scan failed unexpectedly. Check the server logs."


async def _run(job_id: int, market: dict[str, Any], force: bool, refresh: bool) -> None:
    repo.update_scan_job(job_id, status="running", started_at=repo.utc_now(), message="Starting scan")

    def progress(done: int, total: int, message: str) -> None:
        repo.update_scan_job(job_id, progress_done=done, progress_total=total, message=message)

    try:
        result = await asyncio.wait_for(
            run_scan(market, force=force, refresh=refresh, progress=progress),
            timeout=get_settings().scan_job_timeout_seconds,
        )
    except Exception as error:  # a job must always end in a terminal state
        code, message = error_info(error)
        if code == "internal":
            logger.exception("scan_job_crashed", extra={"job_id": job_id, "market_id": market["id"]})
        repo.update_scan_job(job_id, status="failed", error=message, error_code=code, finished_at=repo.utc_now())
        return
    repo.update_scan_job(
        job_id, status="succeeded", result=result, message=result.get("message"), finished_at=repo.utc_now()
    )


def start_scan_job(market: dict[str, Any], *, force: bool = False, refresh: bool = False) -> tuple[dict[str, Any], bool]:
    """Create and start a job. Returns (job, created). Raises ScanGuardError / BudgetExceededError up front.

    Synchronous on purpose: with no ``await`` between the active-job check and the insert, two requests
    can never both create a job on the single event loop.
    """
    active = repo.active_scan_job(market["id"])
    if active is not None:
        return active, False
    precheck_scan(market, force=force, refresh=refresh, now=datetime.now(timezone.utc))
    job = repo.create_scan_job(market["id"], force=force, refresh=refresh)
    task = asyncio.get_running_loop().create_task(_run(job["id"], market, force, refresh))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job, True


def recover_interrupted_jobs() -> int:
    count = repo.fail_unfinished_scan_jobs("The server restarted while this scan was running.")
    if count:
        logger.warning("scan_jobs_interrupted", extra={"count": count})
    return count
