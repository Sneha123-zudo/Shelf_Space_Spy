"""Integration checks for the products table lifecycle and the event pipeline.

Scan sequences here are synthetic: they prove the code behaves as designed, not that real
Google Shopping shelves behave this way. See "Validation boundary" in the README.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from app import repo
from app.db import get_connection, initialize_database
from app.services.analytics_service import analyze_scan, normalize_pending, recompute_market
from tests.factories import obs

BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def market_id() -> int:
    initialize_database()
    user_id = repo.create_user("owner@example.com", "Owner", "x$y")
    return repo.create_market(
        user_id, name="Test", country="US", category="Widgets", brand_name=None,
        keywords=["probe"], competitors=[],
    )


def add_scan(market_id: int, day: int, rows: list[dict], *, demo: bool = False) -> int:
    scan_id = repo.save_shopping_scan(market_id, (BASE + timedelta(days=day)).isoformat(), rows, is_demo=demo)
    analyze_scan(market_id, scan_id)
    return scan_id


def shelf(*titles: str, price: float = 100.0, first_position: int = 1) -> list[dict]:
    return [obs(t, position=first_position + i, price=price, brand=t.split()[0]) for i, t in enumerate(titles)]


def events(market_id: int, kind: str | None = None) -> list[dict]:
    rows = repo.list_events(market_id, since="1970-01-01", limit=1000)
    return [e for e in rows if kind is None or e["type"] == kind]


def product_count(market_id: int | None = None) -> int:
    with get_connection() as conn:
        query, args = "SELECT COUNT(*) FROM products", ()
        if market_id is not None:
            query, args = query + " WHERE market_id = ?", (market_id,)
        return conn.execute(query, args).fetchone()[0]


# --------------------------------------------------------------------------- products lifecycle


def test_fresh_database_has_unique_products_and_no_unlinked_observations(market_id):
    add_scan(market_id, 0, shelf("Alpha one", "Beta two"))
    with get_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO products (market_id, canonical_key, title, first_seen_at, last_seen_at) "
                "VALUES (?, 'key-Alpha one', 't', 'a', 'b')", (market_id,),
            )
    with get_connection() as conn:
        unlinked = conn.execute("SELECT COUNT(*) FROM product_observations WHERE product_row_id IS NULL").fetchone()[0]
        mismatched = conn.execute(
            "SELECT COUNT(*) FROM product_observations o JOIN products p ON p.id = o.product_row_id "
            "WHERE p.canonical_key != o.canonical_key OR p.market_id != o.market_id"
        ).fetchone()[0]
    assert unlinked == 0 and mismatched == 0


def test_repeated_scans_keep_product_ids_stable_and_extend_the_seen_range(market_id):
    add_scan(market_id, 0, shelf("Alpha one", "Beta two"))
    with get_connection() as conn:
        first = {r["canonical_key"]: r["id"] for r in conn.execute("SELECT id, canonical_key FROM products")}
    add_scan(market_id, 5, shelf("Alpha one", "Gamma three"))
    with get_connection() as conn:
        after = {r["canonical_key"]: dict(r) for r in conn.execute("SELECT * FROM products")}
    assert set(after) == {"key-Alpha one", "key-Beta two", "key-Gamma three"}
    assert after["key-Alpha one"]["id"] == first["key-Alpha one"]
    assert after["key-Alpha one"]["last_seen_at"] > after["key-Alpha one"]["first_seen_at"]
    assert after["key-Beta two"]["last_seen_at"] == after["key-Beta two"]["first_seen_at"]


def test_normalization_backfill_links_legacy_rows(market_id):
    scan_id = repo.save_shopping_scan(market_id, BASE.isoformat(), [])
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO product_observations (market_id, keyword, observed_at, title, source, position, scan_id) "
            "VALUES (?, 'probe', ?, 'Legacy Gadget 10 ml', 'Shop', 1, ?)", (market_id, BASE.isoformat(), scan_id),
        )
    assert product_count(market_id) == 0
    assert normalize_pending(market_id) == 1
    assert product_count(market_id) == 1
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM product_observations WHERE product_row_id IS NULL").fetchone()[0] == 0


def test_deleting_demo_data_removes_only_products_that_lose_all_observations(market_id):
    add_scan(market_id, 0, shelf("Alpha one", "Beta two"), demo=True)
    add_scan(market_id, 3, shelf("Alpha one"))
    assert product_count(market_id) == 2
    assert repo.delete_demo_data(market_id) == 1
    with get_connection() as conn:
        left = [dict(r) for r in conn.execute("SELECT * FROM products")]
    assert [p["canonical_key"] for p in left] == ["key-Alpha one"]
    assert left[0]["first_seen_at"] == (BASE + timedelta(days=3)).isoformat()  # range recomputed without demo


def test_deleting_a_market_removes_its_products_and_not_another_markets(market_id):
    other = repo.create_market(
        repo.create_user("two@example.com", "Two", "x$y"), name="Other", country="US", category="Gadgets",
        brand_name=None, keywords=["probe"], competitors=[],
    )
    add_scan(market_id, 0, shelf("Alpha one"))
    add_scan(other, 0, shelf("Alpha one"))
    repo.delete_market(market_id)
    assert product_count(market_id) == 0 and product_count(other) == 1
    with get_connection() as conn:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM product_observations o "
            "WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id = o.product_row_id)"
        ).fetchone()[0]
    assert orphans == 0


def test_a_failed_save_leaves_no_partial_scan_or_products(market_id):
    bad = shelf("Alpha one")
    bad.append({**obs("Broken"), "title": None})  # title is NOT NULL
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_shopping_scan(market_id, BASE.isoformat(), bad)
    assert repo.list_scans(market_id, "shopping") == []
    assert product_count(market_id) == 0


# --------------------------------------------------------------------------- event pipeline


STABLE = ("Alpha one", "Beta two", "Gamma three", "Delta four", "Epsilon five", "Zeta six", "Eta seven", "Theta eight")


def volatile_shelf(day: int, *, tracked_position: int) -> list[dict]:
    """The tracked product plus seven products that exist only on this day."""
    others = [obs(f"Noise{day}x{i}", position=i + 1, brand=f"Noise{day}x{i}") for i in range(8)]
    others = [o for o in others if o["position"] != tracked_position][:7]
    return [*others, obs("Tracked item", position=tracked_position, brand="Tracked")]


def test_stable_shelf_gives_a_trusted_price_event_and_no_removals(market_id):
    for day in range(5):
        add_scan(market_id, day, shelf(*STABLE))
    add_scan(market_id, 5, shelf(*STABLE[1:], price=100.0) + [obs(STABLE[0], position=1, price=70.0, brand="Alpha")])
    drops = events(market_id, "PRICE_DECREASE")
    assert len(drops) == 1 and drops[0]["confidence"] == "high"
    assert events(market_id, "REMOVED_PRODUCT") == []


def test_volatile_probe_lowers_confidence_and_keeps_severity(market_id):
    other = repo.create_market(
        repo.create_user("v@example.com", "V", "x$y"), name="Volatile", country="US", category="Gadgets",
        brand_name=None, keywords=["probe"], competitors=[],
    )
    for day in range(4):  # stable market: tracked item at 12
        add_scan(market_id, day, [*shelf(*STABLE[:7]), obs("Tracked item", position=12, brand="Tracked")])
        add_scan(other, day, volatile_shelf(day, tracked_position=12))
    add_scan(market_id, 4, [obs("Tracked item", position=2, brand="Tracked"), *shelf(*STABLE[:7], first_position=3)])
    add_scan(other, 4, volatile_shelf(4, tracked_position=2))

    stable_event = [e for e in events(market_id, "RANK_GAIN")][0]
    volatile_event = [e for e in events(other, "RANK_GAIN")][0]
    assert stable_event["severity"] == volatile_event["severity"]  # a 10-place climb is just as big
    assert stable_event["confidence"] == "medium" and volatile_event["confidence"] == "low"
    assert volatile_event["metadata"]["probe_stability"] < 0.3
    assert volatile_event["metadata"]["confidence_lowered_from"] == "medium"


def test_three_consecutive_absences_after_being_established_emit_one_removal(market_id):
    full = shelf("Alpha one", "Beta two", "Gamma three")
    reduced = shelf("Alpha one", "Beta two")
    add_scan(market_id, 0, full)
    add_scan(market_id, 1, full)
    add_scan(market_id, 2, reduced)
    add_scan(market_id, 3, reduced)
    assert events(market_id, "REMOVED_PRODUCT") == []  # only two absences so far
    add_scan(market_id, 4, reduced)
    removed = events(market_id, "REMOVED_PRODUCT")
    assert len(removed) == 1 and removed[0]["product"] == "Gamma three"


def test_reruns_do_not_duplicate_events(market_id):
    full = shelf("Alpha one", "Beta two", "Gamma three")
    reduced = shelf("Alpha one", "Beta two")
    ids = [add_scan(market_id, d, rows) for d, rows in enumerate([full, full, reduced, reduced, reduced])]
    before = Counter(e["type"] for e in events(market_id))
    analyze_scan(market_id, ids[-1])
    recompute_market(market_id)
    recompute_market(market_id)
    assert Counter(e["type"] for e in events(market_id)) == before
    assert before["REMOVED_PRODUCT"] == 1


def test_failed_empty_scan_never_causes_a_removal(market_id):
    full = shelf("Alpha one", "Beta two", "Gamma three")
    reduced = shelf("Alpha one", "Beta two")
    for day, rows in enumerate([full, full, reduced, [], reduced, reduced]):
        add_scan(market_id, day, rows)
    # The empty scan breaks the run of three scans in a row where Gamma could be confirmed gone,
    # and no scan flags a removal while an empty (failed) fetch is inside the window.
    assert [e for e in events(market_id, "REMOVED_PRODUCT") if e["detected_at"] <= (BASE + timedelta(days=4)).isoformat()] == []
    add_scan(market_id, 6, reduced)
    assert len(events(market_id, "REMOVED_PRODUCT")) == 1  # confirmed once three real scans agree


def test_mass_disappearance_is_reported_with_low_confidence(market_id):
    first = shelf("Aa one", "Bb two", "Cc three", "Dd four", "Ee five", "Ff six")
    second = shelf("Gg one", "Hh two", "Ii three", "Jj four", "Kk five", "Ll six")
    for day, rows in enumerate([first, first, second, second, second]):
        add_scan(market_id, day, rows)
    removed = events(market_id, "REMOVED_PRODUCT")
    assert len(removed) == 6
    assert {e["confidence"] for e in removed} == {"low"}
    assert all(e["metadata"]["mass_disappearance"] for e in removed)


# --------------------------------------------------------------------------- key stability


def test_old_stored_keys_still_resolve_next_to_newly_normalized_titles():
    from app.services.normalize import normalize_products

    history = [
        {"product_id": None, "title": "Acme Cleanser 2 Pack 30 ML", "brand": "Acme", "canonical_key": "t:legacy-pack"},
        {"product_id": "991", "title": "Acme Balm 50 ml", "brand": "Acme", "canonical_key": "pid:991"},
    ]
    listings = [
        {"title": "Acme Cleanser 30 ml x 2", "source": "Shop A", "product_id": None},      # same pack, new spelling
        {"title": "Acme Cleanser 30 ml", "source": "Shop A", "product_id": None},          # single: a different product
        {"title": "Acme Balm 50 ml", "source": "Shop B", "product_id": "991"},
    ]
    keys = [p["canonical_key"] for p in normalize_products(listings, ["Acme"], history)]
    assert keys[0] == "t:legacy-pack"
    assert keys[1] not in {"t:legacy-pack", "pid:991"}
    assert keys[2] == "pid:991"


# --------------------------------------------------------------------------- brand repair


def test_renormalize_brands_fixes_old_rows_without_touching_product_identity(market_id):
    from app.services.analytics_service import renormalize_brands

    rows = shelf("Alpha one", "Beta two", "Gamma three")
    add_scan(market_id, 0, rows)
    add_scan(market_id, 1, rows)
    with get_connection() as conn:  # simulate rows saved by an older version: split spelling plus a junk guess
        conn.execute("UPDATE product_observations SET brand = 'Alpha' WHERE title = 'Alpha one' AND scan_id = 1")
        conn.execute("UPDATE product_observations SET brand = 'ALPHA' WHERE title = 'Alpha one' AND scan_id = 2")
        conn.execute("UPDATE product_observations SET brand = 'Refurbished', brand_inferred = 1 WHERE title = 'Gamma three'")
        keys_before = sorted(r[0] for r in conn.execute("SELECT canonical_key FROM product_observations"))
        products_before = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    result = renormalize_brands(market_id)
    assert result["brands_changed"] > 0
    with get_connection() as conn:
        brands = {r[0] for r in conn.execute("SELECT brand FROM product_observations WHERE title = 'Alpha one'")}
        gamma = conn.execute("SELECT brand, brand_inferred FROM product_observations WHERE title = 'Gamma three'").fetchone()
        keys_after = sorted(r[0] for r in conn.execute("SELECT canonical_key FROM product_observations"))
        products_after = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    assert len(brands) == 1  # one spelling now
    assert gamma[0] == "Gamma"  # the stale junk guess is replaced by the current detection
    assert keys_before == keys_after and products_before == products_after
    assert renormalize_brands(market_id)["brands_changed"] == 0  # idempotent
