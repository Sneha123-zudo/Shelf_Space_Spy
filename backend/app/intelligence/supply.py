"""Supply-side shelf metrics per probe keyword.

* ``skus``: distinct canonical products on the sampled shelf.
* ``brands`` / ``retailers``: distinct detected brands and selling sources.
* ``top3_share``: visibility share (0-1) held by the three most visible brands.
* ``density``: distinct SKUs per shelf slot (0-1). Close to 1 means every slot is a
  different product, i.e. a crowded, fragmented shelf; low values mean the same few
  products repeat across sellers.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from app.intelligence.common import Observation, brand_key, pct_change
from app.intelligence.shelf_share import shelf_share, top_n_share
from app.services.normalize import source_key


def supply_metrics(observations: Sequence[Observation]) -> dict:
    results = len(observations)
    if results == 0:
        return {"results": 0, "skus": 0, "brands": 0, "retailers": 0, "top3_share": 0.0, "density": 0.0}
    skus = {obs.get("canonical_key") or obs.get("title") for obs in observations}
    brands = {brand_key(obs.get("brand")) for obs in observations if obs.get("brand")}
    retailers = {source_key(obs.get("source")) for obs in observations if obs.get("source")} - {""}
    return {
        "results": results,
        "skus": len(skus),
        "brands": len(brands),
        "retailers": len(retailers),
        "top3_share": round(top_n_share(shelf_share(observations), 3), 3),
        "density": round(len(skus) / results, 3),
    }


def supply_by_keyword(observations: Sequence[Observation]) -> dict[str, dict]:
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        grouped[obs.get("keyword", "")].append(obs)
    return {keyword: supply_metrics(rows) for keyword, rows in grouped.items()}


def supply_growth_pct(current: dict | None, previous: dict | None) -> float | None:
    """Change in distinct SKUs between two scans of the same probe, in percent."""
    if not current or not previous:
        return None
    return pct_change(current.get("skus"), previous.get("skus"))


def shelf_stability(current: Sequence[Observation], previous: Sequence[Observation]) -> float | None:
    """Jaccard overlap (0-1) of canonical products between two scans of the same probe."""
    a = {obs.get("canonical_key") for obs in current if obs.get("canonical_key")}
    b = {obs.get("canonical_key") for obs in previous if obs.get("canonical_key")}
    if not a or not b:
        return None
    return round(len(a & b) / len(a | b), 3)
