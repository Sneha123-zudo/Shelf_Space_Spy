"""Every SQL statement in the application lives here.

Callers get plain dicts back, so swapping SQLite for Postgres later only touches this
module and :mod:`app.db`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from app.db import get_connection, sync_products


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _one(cursor: sqlite3.Cursor) -> dict[str, Any] | None:
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _placeholders(values: Sequence[Any]) -> str:
    return ",".join("?" for _ in values)


# --------------------------------------------------------------------------- users


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        return _one(conn.execute(
            "SELECT id, email, name, password_hash FROM users WHERE email = ?", (email,)
        ))


def get_user_by_id(user_id: int | str) -> dict[str, Any] | None:
    with get_connection() as conn:
        return _one(conn.execute("SELECT id, email, name FROM users WHERE id = ?", (user_id,)))


def create_user(email: str, name: str, password_hash: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)", (email, name, password_hash)
        )
        return int(cursor.lastrowid or 0)


# --------------------------------------------------------------------------- markets


def _hydrate_market(conn: sqlite3.Connection, market: dict[str, Any]) -> dict[str, Any]:
    market["keywords"] = [
        row["keyword"] for row in conn.execute(
            "SELECT keyword FROM market_keywords WHERE market_id = ? ORDER BY id", (market["id"],)
        )
    ]
    market["competitors"] = [
        row["brand_name"] for row in conn.execute(
            "SELECT brand_name FROM competitors WHERE market_id = ? ORDER BY id", (market["id"],)
        )
    ]
    return market


def list_markets(user_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        markets = _rows(conn.execute(
            "SELECT id, user_id, name, country, category, brand_name, created_at FROM markets "
            "WHERE user_id = ? ORDER BY id",
            (user_id,),
        ))
        return [_hydrate_market(conn, market) for market in markets]


def get_market(market_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    query = "SELECT id, user_id, name, country, category, brand_name, created_at FROM markets WHERE id = ?"
    params: tuple[Any, ...] = (market_id,)
    if user_id is not None:
        query += " AND user_id = ?"
        params = (market_id, user_id)
    with get_connection() as conn:
        market = _one(conn.execute(query, params))
        return _hydrate_market(conn, market) if market else None


def find_market_by_name(user_id: int, name: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        return _one(conn.execute("SELECT id FROM markets WHERE user_id = ? AND name = ?", (user_id, name)))


def all_market_ids() -> list[int]:
    with get_connection() as conn:
        return [row["id"] for row in conn.execute("SELECT id FROM markets ORDER BY id")]


def _replace_market_lists(
    conn: sqlite3.Connection, market_id: int, keywords: Iterable[str] | None, competitors: Iterable[str] | None
) -> None:
    if keywords is not None:
        conn.execute("DELETE FROM market_keywords WHERE market_id = ?", (market_id,))
        conn.executemany(
            "INSERT INTO market_keywords (market_id, keyword) VALUES (?, ?)",
            [(market_id, keyword) for keyword in keywords],
        )
    if competitors is not None:
        conn.execute("DELETE FROM competitors WHERE market_id = ?", (market_id,))
        conn.executemany(
            "INSERT INTO competitors (market_id, brand_name) VALUES (?, ?)",
            [(market_id, brand) for brand in competitors],
        )


def create_market(
    user_id: int,
    *,
    name: str,
    country: str,
    category: str,
    brand_name: str | None,
    keywords: Sequence[str],
    competitors: Sequence[str],
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO markets (user_id, name, country, category, brand_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, country, category, brand_name, utc_now()),
        )
        market_id = int(cursor.lastrowid or 0)
        _replace_market_lists(conn, market_id, keywords, competitors)
        ensure_default_alert_rules(conn, market_id)
        return market_id


_UNSET: Any = object()


def update_market(
    market_id: int,
    *,
    name: str | None = None,
    country: str | None = None,
    category: str | None = None,
    brand_name: str | None = _UNSET,
    keywords: Sequence[str] | None = None,
    competitors: Sequence[str] | None = None,
) -> None:
    fields: dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if country is not None:
        fields["country"] = country
    if category is not None:
        fields["category"] = category
    if brand_name is not _UNSET:
        fields["brand_name"] = brand_name
    with get_connection() as conn:
        if fields:
            assignments = ", ".join(f"{column} = ?" for column in fields)
            conn.execute(f"UPDATE markets SET {assignments} WHERE id = ?", (*fields.values(), market_id))
        _replace_market_lists(conn, market_id, keywords, competitors)


def delete_market(market_id: int) -> None:
    with get_connection() as conn:
        for table in (
            "alerts", "alert_rules", "scan_jobs", "reports", "opportunities", "market_events",
            "trend_observations", "product_observations", "products", "scans", "competitors", "market_keywords",
        ):
            conn.execute(f"DELETE FROM {table} WHERE market_id = ?", (market_id,))
        conn.execute("DELETE FROM markets WHERE id = ?", (market_id,))


# --------------------------------------------------------------------------- scans

_OBSERVATION_COLUMNS = (
    "market_id", "keyword", "observed_at", "product_id", "title", "source", "price", "old_price",
    "position", "rating", "reviews", "product_link", "thumbnail", "scan_id", "brand", "brand_inferred",
    "canonical_key", "is_demo",
)


def _insert_scan(conn: sqlite3.Connection, market_id: int, kind: str, scanned_at: str, is_demo: bool, credits: int) -> int:
    cursor = conn.execute(
        "INSERT INTO scans (market_id, kind, scanned_at, is_demo, credits_used) VALUES (?, ?, ?, ?, ?)",
        (market_id, kind, scanned_at, int(is_demo), credits),
    )
    return int(cursor.lastrowid or 0)


def save_shopping_scan(
    market_id: int,
    scanned_at: str,
    observations: Sequence[dict[str, Any]],
    *,
    is_demo: bool = False,
    credits_used: int = 0,
) -> int:
    """Insert one shopping scan and its observations atomically. Returns the scan id."""
    with get_connection() as conn:
        scan_id = _insert_scan(conn, market_id, "shopping", scanned_at, is_demo, credits_used)
        rows = []
        for item in observations:
            row = {column: item.get(column) for column in _OBSERVATION_COLUMNS}
            row.update(market_id=market_id, observed_at=scanned_at, scan_id=scan_id, is_demo=int(is_demo))
            row["brand_inferred"] = int(bool(item.get("brand_inferred")))
            rows.append(tuple(row[column] for column in _OBSERVATION_COLUMNS))
        conn.executemany(
            f"INSERT INTO product_observations ({', '.join(_OBSERVATION_COLUMNS)}) "
            f"VALUES ({_placeholders(_OBSERVATION_COLUMNS)})",
            rows,
        )
        sync_products(conn, market_id)
        return scan_id


def save_trends_scan(
    market_id: int,
    scanned_at: str,
    points: Sequence[dict[str, Any]],
    *,
    is_demo: bool = False,
    credits_used: int = 0,
) -> int:
    with get_connection() as conn:
        scan_id = _insert_scan(conn, market_id, "trends", scanned_at, is_demo, credits_used)
        conn.executemany(
            """INSERT INTO trend_observations
               (market_id, scan_id, keyword, batch_id, week_start, interest, is_partial)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (market_id, scan_id, p["keyword"], p["batch_id"], p["week_start"], float(p["interest"]),
                 int(bool(p.get("is_partial"))))
                for p in points
            ],
        )
        return scan_id


def list_scans(market_id: int, kind: str | None = None) -> list[dict[str, Any]]:
    """Scans in chronological order (oldest first)."""
    query = "SELECT id, market_id, kind, scanned_at, is_demo, credits_used FROM scans WHERE market_id = ?"
    params: list[Any] = [market_id]
    if kind:
        query += " AND kind = ?"
        params.append(kind)
    query += " ORDER BY scanned_at, id"
    with get_connection() as conn:
        return _rows(conn.execute(query, params))


def latest_scan(market_id: int, kind: str, *, live_only: bool = False) -> dict[str, Any] | None:
    query = "SELECT id, market_id, kind, scanned_at, is_demo, credits_used FROM scans WHERE market_id = ? AND kind = ?"
    if live_only:
        query += " AND is_demo = 0"
    query += " ORDER BY scanned_at DESC, id DESC LIMIT 1"
    with get_connection() as conn:
        return _one(conn.execute(query, (market_id, kind)))


def delete_demo_data(market_id: int) -> int:
    """Remove every demo scan of a market and the rows derived from it. Returns scans removed."""
    with get_connection() as conn:
        scan_ids = [row["id"] for row in conn.execute(
            "SELECT id FROM scans WHERE market_id = ? AND is_demo = 1", (market_id,)
        )]
        if not scan_ids:
            return 0
        marks = _placeholders(scan_ids)
        for table in ("alerts", "opportunities", "market_events", "trend_observations", "product_observations"):
            conn.execute(f"DELETE FROM {table} WHERE scan_id IN ({marks})", scan_ids)
        conn.execute(f"DELETE FROM scans WHERE id IN ({marks})", scan_ids)
        sync_products(conn, market_id)
        return len(scan_ids)


# --------------------------------------------------------------------------- observations


def observations_for_scans(scan_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {scan_id: [] for scan_id in scan_ids}
    if not scan_ids:
        return result
    with get_connection() as conn:
        rows = _rows(conn.execute(
            f"SELECT * FROM product_observations WHERE scan_id IN ({_placeholders(scan_ids)}) "
            "ORDER BY scan_id, keyword, position",
            list(scan_ids),
        ))
    for row in rows:
        result[row["scan_id"]].append(row)
    return result


def observations_for_scan(scan_id: int) -> list[dict[str, Any]]:
    return observations_for_scans([scan_id])[scan_id]


def product_history(market_id: int) -> list[dict[str, Any]]:
    """Previously normalized products of a market, used to keep canonical keys stable."""
    with get_connection() as conn:
        return _rows(conn.execute(
            """SELECT product_id, title, brand, canonical_key FROM product_observations
               WHERE market_id = ? AND canonical_key IS NOT NULL ORDER BY id""",
            (market_id,),
        ))


def observations_missing_normalization(market_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return _rows(conn.execute(
            "SELECT id, scan_id, product_id, title, source, keyword FROM product_observations "
            "WHERE market_id = ? AND canonical_key IS NULL ORDER BY scan_id, keyword, position",
            (market_id,),
        ))


def shopping_scans_without_analysis(market_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return _rows(conn.execute(
            """SELECT s.id, s.scanned_at FROM scans s
               WHERE s.market_id = ? AND s.kind = 'shopping'
                 AND NOT EXISTS (SELECT 1 FROM opportunities o WHERE o.scan_id = s.id)
               ORDER BY s.scanned_at, s.id""",
            (market_id,),
        ))


def update_observation_normalization(updates: Sequence[tuple[str | None, bool, str, int]]) -> None:
    """updates: (brand, brand_inferred, canonical_key, observation_id)."""
    with get_connection() as conn:
        conn.executemany(
            "UPDATE product_observations SET brand = ?, brand_inferred = ?, canonical_key = ? WHERE id = ?",
            [(brand, int(inferred), key, obs_id) for brand, inferred, key, obs_id in updates],
        )
        for (market_id,) in conn.execute(
            "SELECT DISTINCT market_id FROM product_observations WHERE product_row_id IS NULL AND canonical_key IS NOT NULL"
        ).fetchall():
            sync_products(conn, market_id)


def update_observation_brands(market_id: int, updates: Sequence[tuple[str | None, bool, int]]) -> None:
    """updates: (brand, brand_inferred, observation_id). Product identity (canonical_key) is never touched."""
    with get_connection() as conn:
        conn.executemany(
            "UPDATE product_observations SET brand = ?, brand_inferred = ? WHERE id = ?",
            [(brand, int(inferred), obs_id) for brand, inferred, obs_id in updates],
        )
        sync_products(conn, market_id)


# --------------------------------------------------------------------------- trends


def trend_points_for_scan(scan_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return _rows(conn.execute(
            "SELECT keyword, batch_id, week_start, interest, is_partial FROM trend_observations "
            "WHERE scan_id = ? ORDER BY keyword, week_start",
            (scan_id,),
        ))


# --------------------------------------------------------------------------- events


def replace_events(market_id: int, scan_id: int, detected_at: str, events: Sequence[dict[str, Any]]) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM market_events WHERE scan_id = ?", (scan_id,))
        conn.executemany(
            """INSERT INTO market_events
               (market_id, scan_id, type, severity, brand, product, keyword, previous_value,
                current_value, confidence, metadata_json, detected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    market_id, scan_id, e["type"], e["severity"], e.get("brand"), e.get("product"),
                    e.get("keyword"), e.get("previous_value"), e.get("current_value"),
                    e.get("confidence", "medium"),
                    json.dumps({**e.get("metadata", {}), "summary": e.get("summary")}, default=str),
                    detected_at,
                )
                for e in events
            ],
        )


def list_events(market_id: int, *, since: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    query = (
        "SELECT e.*, s.is_demo AS is_demo FROM market_events e JOIN scans s ON s.id = e.scan_id "
        "WHERE e.market_id = ?"
    )
    params: list[Any] = [market_id]
    if since:
        query += " AND e.detected_at >= ?"
        params.append(since)
    query += " ORDER BY e.detected_at DESC, e.id LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = _rows(conn.execute(query, params))
    for row in rows:
        row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
        row["is_demo"] = bool(row["is_demo"])
    return rows


# --------------------------------------------------------------------------- opportunities


def replace_opportunities(market_id: int, scan_id: int, opportunities: Sequence[dict[str, Any]]) -> None:
    created_at = utc_now()
    with get_connection() as conn:
        conn.execute("DELETE FROM opportunities WHERE scan_id = ?", (scan_id,))
        conn.executemany(
            """INSERT INTO opportunities
               (market_id, scan_id, keyword, score, label, components_json, explanation_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    market_id, scan_id, o["keyword"], o["score"], o["label"],
                    json.dumps(o.get("components", {})),
                    json.dumps({
                        key: o.get(key)
                        for key in ("reasons", "caveats", "confidence", "demand_growth", "supply_growth", "inputs")
                    }, default=str),
                    created_at,
                )
                for o in opportunities
            ],
        )


def opportunities_for_scan(scan_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = _rows(conn.execute(
            "SELECT keyword, score, label, components_json, explanation_json FROM opportunities "
            "WHERE scan_id = ? ORDER BY score DESC, keyword",
            (scan_id,),
        ))
    result = []
    for row in rows:
        explanation = json.loads(row["explanation_json"] or "{}")
        result.append({
            "keyword": row["keyword"],
            "score": row["score"],
            "label": row["label"],
            "components": json.loads(row["components_json"] or "{}"),
            **explanation,
        })
    return result


# --------------------------------------------------------------------------- reports


_PUBLIC_DIAGNOSTICS = ("fallback_reason", "llm_attempts", "model", "llm_error_summary")


def _report_row(row: dict[str, Any]) -> dict[str, Any]:
    """Report as the API returns it: content, source and a small public diagnostics summary
    (never the stored inputs, which can include the full evidence payload)."""
    stored = json.loads(row.pop("inputs_json", None) or "{}")
    diagnostics = stored.get("diagnostics") or {}
    summary = {key: diagnostics[key] for key in _PUBLIC_DIAGNOSTICS if key in diagnostics}
    unsupported = sorted(
        {item for key, value in diagnostics.items() if key.endswith("_unsupported_numbers") for item in value}
    )
    if unsupported:
        summary["rejected_claims"] = unsupported
    inputs = stored.get("inputs") or {}
    summary["data_includes_demo"] = bool(inputs.get("has_demo_history") or inputs.get("latest_scan_is_demo"))
    return {**row, "diagnostics": summary}


def insert_report(market_id: int, content: str, inputs: dict[str, Any], source: str) -> dict[str, Any]:
    created_at = utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO reports (market_id, content, inputs_json, source, created_at) VALUES (?, ?, ?, ?, ?)",
            (market_id, content, json.dumps(inputs, default=str), source, created_at),
        )
        report_id = int(cursor.lastrowid or 0)
    return _report_row({
        "id": report_id, "market_id": market_id, "content": content, "source": source, "created_at": created_at,
        "inputs_json": json.dumps(inputs, default=str),
    })


def latest_report(market_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = _one(conn.execute(
            "SELECT id, market_id, content, source, created_at, inputs_json FROM reports WHERE market_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (market_id,),
        ))
    return _report_row(row) if row else None


# --------------------------------------------------------------------------- read models for section endpoints

_OBSERVATION_SORTS = {
    "position": "o.keyword, o.position IS NULL, o.position",
    "price": "o.price IS NULL, o.price",
    "price_desc": "o.price IS NULL, o.price DESC",
    "rating": "o.rating IS NULL, o.rating DESC",
    "reviews": "o.reviews IS NULL, o.reviews DESC",
    "title": "o.title COLLATE NOCASE",
}


def scans_with_counts(market_id: int) -> list[dict[str, Any]]:
    """Every scan of a market, newest first, with the number of stored rows it produced."""
    with get_connection() as conn:
        return _rows(conn.execute(
            """SELECT s.id, s.kind, s.scanned_at, s.is_demo, s.credits_used,
                      (SELECT COUNT(*) FROM product_observations o WHERE o.scan_id = s.id) AS products,
                      (SELECT COUNT(*) FROM trend_observations t WHERE t.scan_id = s.id) AS trend_points
               FROM scans s WHERE s.market_id = ? ORDER BY s.scanned_at DESC, s.id DESC""",
            (market_id,),
        ))


def events_page(
    market_id: int,
    *,
    types: Sequence[str] = (),
    include_low_confidence: bool = False,
    scan_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int, int]:
    """(rows, total matching, hidden low-confidence count). Stored events are never filtered out of the DB."""
    where: list[str] = ["e.market_id = ?"]
    params: list[Any] = [market_id]
    if types:
        where.append(f"e.type IN ({_placeholders(types)})")
        params.extend(types)
    if scan_id is not None:
        where.append("e.scan_id = ?")
        params.append(scan_id)
    base = " FROM market_events e JOIN scans s ON s.id = e.scan_id WHERE " + " AND ".join(where)
    visible = base + ("" if include_low_confidence else " AND e.confidence != 'low'")
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*)" + visible, params).fetchone()[0]
        hidden = 0 if include_low_confidence else conn.execute(
            "SELECT COUNT(*)" + base + " AND e.confidence = 'low'", params
        ).fetchone()[0]
        rows = _rows(conn.execute(
            "SELECT e.*, s.is_demo AS is_demo" + visible + " ORDER BY e.detected_at DESC, e.id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ))
    for row in rows:
        row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
        row["is_demo"] = bool(row["is_demo"])
    return rows, int(total), int(hidden)


def observations_page(
    scan_id: int,
    *,
    keyword: str | None = None,
    brand: str | None = None,
    search: str | None = None,
    sort: str = "position",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where: list[str] = ["o.scan_id = ?"]
    params: list[Any] = [scan_id]
    if keyword:
        where.append("LOWER(o.keyword) = LOWER(?)")
        params.append(keyword)
    if brand:
        where.append("LOWER(COALESCE(o.brand, '')) = LOWER(?)")
        params.append(brand)
    if search:
        where.append("(o.title LIKE ? OR o.source LIKE ? OR o.brand LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    clause = " WHERE " + " AND ".join(where)
    order = _OBSERVATION_SORTS.get(sort, _OBSERVATION_SORTS["position"])
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM product_observations o" + clause, params).fetchone()[0]
        rows = _rows(conn.execute(
            "SELECT o.id, o.keyword, o.title, o.brand, o.brand_inferred, o.source, o.price, o.old_price, o.position, "
            "o.rating, o.reviews, o.product_link, o.thumbnail, o.canonical_key, o.product_row_id "
            f"FROM product_observations o{clause} ORDER BY {order}, o.id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ))
    for row in rows:
        row["brand_inferred"] = bool(row["brand_inferred"])
    return rows, int(total)


def get_product(market_id: int, product_row_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        return _one(conn.execute(
            "SELECT id, canonical_key, brand, title, first_seen_at, last_seen_at FROM products "
            "WHERE id = ? AND market_id = ?",
            (product_row_id, market_id),
        ))


def product_listings(product_row_id: int) -> list[dict[str, Any]]:
    """Every stored listing of a product with its scan time, oldest scan first."""
    with get_connection() as conn:
        return _rows(conn.execute(
            """SELECT o.scan_id, s.scanned_at, s.is_demo, o.keyword, o.position, o.price, o.old_price,
                      o.source, o.rating, o.reviews, o.product_id
               FROM product_observations o JOIN scans s ON s.id = o.scan_id
               WHERE o.product_row_id = ? ORDER BY s.scanned_at, o.keyword, o.position""",
            (product_row_id,),
        ))


def list_reports(market_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = _rows(conn.execute(
            "SELECT id, market_id, content, source, created_at, inputs_json FROM reports WHERE market_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (market_id, limit),
        ))
    return [_report_row(row) for row in rows]


# --------------------------------------------------------------------------- credits and scan jobs


def credits_used_since(since_iso: str) -> int:
    """Live (non-demo) SerpAPI credits recorded by scans taken at or after ``since_iso``."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(credits_used), 0) FROM scans WHERE is_demo = 0 AND scanned_at >= ?", (since_iso,)
        ).fetchone()
    return int(row[0])


_JOB_COLUMNS = (
    "id, market_id, status, force, refresh, progress_done, progress_total, message, error, error_code, "
    "result_json, created_at, started_at, finished_at"
)


def _job(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = row.pop("result_json", None)
    row["result"] = json.loads(result) if result else None
    row["force"], row["refresh"] = bool(row["force"]), bool(row["refresh"])
    return row


def create_scan_job(market_id: int, *, force: bool, refresh: bool) -> dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO scan_jobs (market_id, status, force, refresh, created_at) VALUES (?, 'queued', ?, ?, ?)",
            (market_id, int(force), int(refresh), utc_now()),
        )
        job_id = int(cursor.lastrowid or 0)
    return get_scan_job(job_id) or {}


def get_scan_job(job_id: int, market_id: int | None = None) -> dict[str, Any] | None:
    query, params = f"SELECT {_JOB_COLUMNS} FROM scan_jobs WHERE id = ?", [job_id]
    if market_id is not None:
        query, params = query + " AND market_id = ?", [*params, market_id]
    with get_connection() as conn:
        return _job(_one(conn.execute(query, params)))


def active_scan_job(market_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        return _job(_one(conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM scan_jobs WHERE market_id = ? AND status IN ('queued', 'running') "
            "ORDER BY id DESC LIMIT 1",
            (market_id,),
        )))


def latest_scan_job(market_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        return _job(_one(conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM scan_jobs WHERE market_id = ? ORDER BY id DESC LIMIT 1", (market_id,)
        )))


def update_scan_job(job_id: int, **fields: Any) -> None:
    allowed = {"status", "progress_done", "progress_total", "message", "error", "error_code", "started_at", "finished_at"}
    unknown = set(fields) - allowed - {"result"}
    if unknown:
        raise ValueError(f"unknown scan job fields: {sorted(unknown)}")
    columns = {k: v for k, v in fields.items() if k in allowed}
    if "result" in fields:
        columns["result_json"] = json.dumps(fields["result"], default=str)
    if not columns:
        return
    with get_connection() as conn:
        conn.execute(
            f"UPDATE scan_jobs SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ?", [*columns.values(), job_id]
        )


def fail_unfinished_scan_jobs(reason: str) -> int:
    """Startup cleanup: a job still queued/running belongs to a process that no longer exists."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE scan_jobs SET status = 'failed', error = ?, error_code = 'interrupted', finished_at = ? "
            "WHERE status IN ('queued', 'running')",
            (reason, utc_now()),
        )
        return cursor.rowcount


# --------------------------------------------------------------------------- alerts

DEFAULT_ALERT_RULES: tuple[dict[str, Any], ...] = (
    {"key": "competitor_price_cuts", "name": "Competitor price cuts and new discounts",
     "event_types": ["PRICE_DECREASE", "DISCOUNT_STARTED"], "min_severity": "medium", "min_confidence": "medium",
     "scope": "competitors"},
    {"key": "new_competitors", "name": "New competitors on the shelf",
     "event_types": ["NEW_COMPETITOR"], "min_severity": "low", "min_confidence": "medium", "scope": "any"},
    {"key": "new_competitor_products", "name": "New products from competitors",
     "event_types": ["NEW_PRODUCT"], "min_severity": "medium", "min_confidence": "medium", "scope": "competitors"},
    {"key": "my_brand_moves", "name": "Your brand's price and rank moves",
     "event_types": ["PRICE_DECREASE", "PRICE_INCREASE", "DISCOUNT_STARTED", "RANK_GAIN", "RANK_LOSS"],
     "min_severity": "low", "min_confidence": "medium", "scope": "mine"},
    {"key": "products_removed", "name": "Products leaving the shelf",
     "event_types": ["REMOVED_PRODUCT"], "min_severity": "low", "min_confidence": "medium", "scope": "any"},
)


def ensure_default_alert_rules(conn: sqlite3.Connection, market_id: int) -> None:
    """Add any default rule a market is missing. Rules are enabled/disabled, never deleted, so this
    cannot resurrect something the user switched off."""
    conn.executemany(
        "INSERT OR IGNORE INTO alert_rules (market_id, key, name, event_types, min_severity, min_confidence, scope) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (market_id, r["key"], r["name"], json.dumps(r["event_types"]), r["min_severity"], r["min_confidence"], r["scope"])
            for r in DEFAULT_ALERT_RULES
        ],
    )


def ensure_all_default_alert_rules() -> None:
    with get_connection() as conn:
        for (market_id,) in conn.execute("SELECT id FROM markets").fetchall():
            ensure_default_alert_rules(conn, market_id)


def _rule(row: dict[str, Any]) -> dict[str, Any]:
    row["event_types"] = json.loads(row["event_types"])
    row["enabled"] = bool(row["enabled"])
    return row


def list_alert_rules(market_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return [_rule(r) for r in _rows(conn.execute("SELECT * FROM alert_rules WHERE market_id = ? ORDER BY id", (market_id,)))]


def update_alert_rule(market_id: int, rule_id: int, **fields: Any) -> dict[str, Any] | None:
    columns = {k: v for k, v in fields.items() if k in {"enabled", "min_severity", "min_confidence"} and v is not None}
    with get_connection() as conn:
        if columns:
            conn.execute(
                f"UPDATE alert_rules SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ? AND market_id = ?",
                [*(int(v) if isinstance(v, bool) else v for v in columns.values()), rule_id, market_id],
            )
        row = _one(conn.execute("SELECT * FROM alert_rules WHERE id = ? AND market_id = ?", (rule_id, market_id)))
    return _rule(row) if row else None


def insert_alerts(alerts: Sequence[dict[str, Any]]) -> int:
    """Insert alerts, skipping any that already exist for the same rule, scan and event key."""
    created = utc_now()
    with get_connection() as conn:
        before = conn.total_changes
        conn.executemany(
            """INSERT OR IGNORE INTO alerts
               (market_id, rule_id, scan_id, dedupe_key, event_type, severity, confidence, title, detail,
                brand, keyword, created_at)
               VALUES (:market_id, :rule_id, :scan_id, :dedupe_key, :event_type, :severity, :confidence, :title,
                       :detail, :brand, :keyword, :created_at)""",
            [{**a, "created_at": created} for a in alerts],
        )
        return conn.total_changes - before


def alerts_page(market_id: int, *, unread_only: bool, limit: int, offset: int) -> tuple[list[dict[str, Any]], int, int]:
    """(rows newest first, total matching, unread count)."""
    unread_clause = " AND read_at IS NULL" if unread_only else ""
    with get_connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM alerts WHERE market_id = ?{unread_clause}", (market_id,)).fetchone()[0]
        unread = conn.execute("SELECT COUNT(*) FROM alerts WHERE market_id = ? AND read_at IS NULL", (market_id,)).fetchone()[0]
        rows = _rows(conn.execute(
            "SELECT a.*, r.name AS rule_name FROM alerts a JOIN alert_rules r ON r.id = a.rule_id "
            f"WHERE a.market_id = ?{unread_clause.replace('read_at', 'a.read_at')} "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT ? OFFSET ?",
            (market_id, limit, offset),
        ))
    return rows, int(total), int(unread)


def mark_alerts_read(market_id: int, ids: Sequence[int] | None) -> int:
    with get_connection() as conn:
        if ids:
            cursor = conn.execute(
                f"UPDATE alerts SET read_at = ? WHERE market_id = ? AND read_at IS NULL AND id IN ({_placeholders(ids)})",
                [utc_now(), market_id, *ids],
            )
        else:
            cursor = conn.execute("UPDATE alerts SET read_at = ? WHERE market_id = ? AND read_at IS NULL", (utc_now(), market_id))
        return cursor.rowcount


def unread_alert_counts(user_id: int) -> dict[int, int]:
    with get_connection() as conn:
        return {
            row["market_id"]: row["n"]
            for row in conn.execute(
                "SELECT a.market_id AS market_id, COUNT(*) AS n FROM alerts a JOIN markets m ON m.id = a.market_id "
                "WHERE m.user_id = ? AND a.read_at IS NULL GROUP BY a.market_id",
                (user_id,),
            )
        }


def events_for_scan(scan_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = _rows(conn.execute(
            "SELECT e.*, s.is_demo AS is_demo FROM market_events e JOIN scans s ON s.id = e.scan_id WHERE e.scan_id = ?",
            (scan_id,),
        ))
    for row in rows:
        row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
        row["is_demo"] = bool(row["is_demo"])
    return rows
