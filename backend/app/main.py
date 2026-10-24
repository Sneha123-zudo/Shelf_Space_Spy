from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import repo
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.markets import router as markets_router
from app.api.operations import router as operations_router
from app.api.sections import router as sections_router
from app.core.config import check_startup_safety, get_settings
from app.core.logging import RequestLogMiddleware, configure_logging
from app.db import initialize_database
from app.seed import seed_demo_data
from app.services.analytics_service import backfill_all
from app.services.jobs import recover_interrupted_jobs

settings = get_settings()
configure_logging(settings.log_level, settings.log_format)


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_startup_safety(settings)
    initialize_database()
    repo.ensure_all_default_alert_rules()
    recover_interrupted_jobs()
    if settings.is_local:
        seed_demo_data()  # the documented demo account exists only in development/test
    backfill_all()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="Shelf Space Spy: Google Shopping + Google Trends market intelligence for any category.",
    lifespan=lifespan,
)

app.add_middleware(RequestLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(markets_router, prefix="/api")
app.include_router(sections_router, prefix="/api")
app.include_router(operations_router, prefix="/api")
