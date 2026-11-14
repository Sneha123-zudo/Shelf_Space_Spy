"""Guided market setup: turn a free-text category into probe keywords and brand candidates.

Costs at most two SerpAPI credits (one Google autocomplete, one Google Shopping search),
and zero when both responses are still in the disk cache.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from app.core.config import get_settings
from app.intelligence.common import brand_key
from app.services import serpapi
from app.services.normalize import brand_candidates

MAX_KEYWORDS = serpapi.TRENDS_BATCH_SIZE  # one comparable Google Trends batch
MAX_KEYWORD_WORDS = 6
MAX_KEYWORD_LENGTH = 60

# Search-intent noise that makes a poor shelf probe in any category.
_NOISE = re.compile(
    r"\b(near me|how to|how do|what is|what are|meaning|definition|reddit|wiki|wikipedia|"
    r"diy|vs|versus|png|clipart|drawing|images?|pictures?|meme|quotes?|synonyms?|jobs?|"
    r"course|tutorial|login|app|youtube|pdf)\b"
)


def clean_keyword(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def propose_keywords(category: str, suggestions: list[str], brands: list[dict[str, Any]]) -> list[str]:
    """The category itself first, then generic autocomplete phrasings, max one Trends batch.

    Suggestions that name a confirmed brand are dropped: a probe like "<brand> <category>"
    measures one brand's shelf, not the market.
    """
    brand_keys = {brand_key(b["name"]) for b in brands if not b.get("inferred")}
    brand_keys.discard("")
    proposed: list[str] = []
    seen: set[str] = set()
    for raw in [category, *suggestions]:
        keyword = clean_keyword(raw)
        key = re.sub(r"[^a-z0-9]", "", keyword)
        if not keyword or key in seen:
            continue
        too_long = len(keyword) > MAX_KEYWORD_LENGTH or len(keyword.split()) > MAX_KEYWORD_WORDS
        if too_long or "," in keyword or _NOISE.search(keyword):
            continue
        tokens = {brand_key(token) for token in keyword.split()}
        joined = brand_key(keyword)
        if any(bk in tokens or (len(bk) > 3 and bk in joined) for bk in brand_keys):
            continue
        seen.add(key)
        proposed.append(keyword)
        if len(proposed) >= MAX_KEYWORDS:
            break
    return proposed


async def suggest_market(category: str, country: str, *, force_refresh: bool = False) -> dict[str, Any]:
    ttl = timedelta(hours=get_settings().suggest_cache_ttl_hours)
    autocomplete = await serpapi.search(
        serpapi.autocomplete_params(category, country), max_age=ttl, force_refresh=force_refresh
    )
    shopping = await serpapi.search(
        serpapi.shopping_params(category, country), max_age=ttl, force_refresh=force_refresh
    )
    suggestions = [
        str(item.get("value", "")) for item in autocomplete.payload.get("suggestions") or [] if item.get("value")
    ]
    products = list(shopping.payload.get("shopping_results") or [])
    brands = brand_candidates(products)
    return {
        "keywords": propose_keywords(category, suggestions, brands),
        "brands": brands,
        "credits_used": autocomplete.credits + shopping.credits,
    }
