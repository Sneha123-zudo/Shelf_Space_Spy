"""Demo account and example markets.

Example markets are plain configuration (``DEMO_MARKET_*`` in backend/.env). The code
treats them exactly like markets a user creates; nothing elsewhere special-cases them.
"""

from typing import Any

from app import repo
from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.schemas import MAX_KEYWORDS


def example_markets(settings: Settings) -> list[dict[str, Any]]:
    """Starter markets for the demo account, all taken from configuration."""
    markets = [
        {
            "name": settings.demo_market_name,
            "country": settings.demo_market_country.upper(),
            "category": settings.demo_market_category,
            "brand_name": settings.demo_brand_name or None,
            "keywords": settings.market_keywords[:MAX_KEYWORDS],
            "competitors": settings.competitors,
        },
    ]
    if settings.demo_market_2_name and settings.market_2_keywords:
        markets.append({
            "name": settings.demo_market_2_name,
            "country": settings.demo_market_2_country.upper(),
            "category": settings.demo_market_2_category or settings.demo_market_2_name,
            "brand_name": settings.demo_market_2_brand or None,
            "keywords": settings.market_2_keywords[:MAX_KEYWORDS],
            "competitors": settings.market_2_competitors,
        })
    return markets


def seed_demo_data() -> None:
    """Create the documented hackathon account and its example markets (idempotent)."""
    settings = get_settings()
    email = settings.demo_user_email.lower()
    user = repo.get_user_by_email(email)
    user_id = (
        int(user["id"]) if user
        else repo.create_user(email, settings.demo_user_name, hash_password(settings.demo_user_password))
    )
    for example in example_markets(settings):
        if not example["name"] or not example["keywords"]:
            continue
        if repo.find_market_by_name(user_id, example["name"]) is None:
            repo.create_market(user_id, **example)
