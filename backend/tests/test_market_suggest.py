import asyncio

from app.services.market_suggest import propose_keywords, suggest_market


def test_propose_keywords_filters_noise_brands_and_duplicates():
    suggestions = [
        "running shoes for men",
        "Running Shoes",               # duplicate of the category
        "running shoes near me",       # intent noise
        "nike running shoes",          # names a confirmed brand
        "how to choose running shoes",
        "trail running shoes",
        "running shoes women wide fit size guide extra",  # too many words
        "zero drop running shoes",
        "carbon plate running shoes",
        "cushioned running shoes",
    ]
    brands = [{"name": "Nike", "count": 5, "inferred": False}, {"name": "Zero", "count": 1, "inferred": True}]
    keywords = propose_keywords("Running Shoes", suggestions, brands)
    assert keywords == [
        "running shoes",
        "running shoes for men",
        "trail running shoes",
        "zero drop running shoes",   # inferred brands do not filter
        "carbon plate running shoes",
    ]
    assert len(keywords) <= 5


def test_suggest_market_uses_two_credits_then_cache(fake_serpapi):
    fake_serpapi.autocomplete = ["running shoes for men", "hoka running shoes", "trail running shoes"]
    result = asyncio.run(suggest_market("running shoes", "US"))
    assert result["credits_used"] == 2
    assert result["keywords"][0] == "running shoes"
    assert "hoka running shoes" not in result["keywords"]  # Hoka seen at several retailers
    names = {brand["name"]: brand for brand in result["brands"]}
    assert {"Nike", "Hoka", "Brooks", "Asics"} <= set(names)
    assert names["Brooks"]["count"] == 2
    assert all(set(brand) == {"name", "count", "inferred"} for brand in result["brands"])
    assert "Amazon" not in names and "Zappos" not in names  # retailers are not brands

    again = asyncio.run(suggest_market("running shoes", "US"))
    assert again["credits_used"] == 0
    assert len(fake_serpapi.calls) == 2
