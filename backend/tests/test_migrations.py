"""The first release's schema must upgrade in place without losing data."""

import sqlite3

from app import repo
from app.db import database_path, initialize_database
from app.services.analytics_service import backfill_all, build_dashboard

LEGACY_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE markets (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, country TEXT NOT NULL,
    category TEXT NOT NULL, brand_name TEXT NOT NULL, FOREIGN KEY (user_id) REFERENCES users(id));
CREATE TABLE market_keywords (id INTEGER PRIMARY KEY, market_id INTEGER NOT NULL, keyword TEXT NOT NULL,
    FOREIGN KEY (market_id) REFERENCES markets(id));
CREATE TABLE competitors (id INTEGER PRIMARY KEY, market_id INTEGER NOT NULL, brand_name TEXT NOT NULL,
    FOREIGN KEY (market_id) REFERENCES markets(id));
CREATE TABLE product_observations (id INTEGER PRIMARY KEY, market_id INTEGER NOT NULL, keyword TEXT NOT NULL,
    observed_at TEXT NOT NULL, product_id TEXT, title TEXT NOT NULL, source TEXT, price REAL, old_price REAL,
    position INTEGER, rating REAL, reviews INTEGER, product_link TEXT, thumbnail TEXT,
    FOREIGN KEY (market_id) REFERENCES markets(id));
INSERT INTO users VALUES (1, 'a@example.com', 'A', 'x$y');
INSERT INTO markets VALUES (1, 1, 'Legacy', 'US', 'Kayaks', 'Paddle Co');
INSERT INTO market_keywords VALUES (1, 1, 'kayak'), (2, 1, 'fishing kayak');
INSERT INTO competitors VALUES (1, 1, 'Pelican');
"""


def legacy_rows(observed_at):
    return [
        (1, "kayak", observed_at, "1", "Pelican Maxim 100X Kayak", "Walmart", 299.0, None, 1, 4.5, 120),
        (1, "kayak", observed_at, "2", "Lifetime Tamarack 100 Kayak", "Amazon.com", 249.0, None, 2, 4.4, 900),
        (1, "fishing kayak", observed_at, "3", "Pelican Catch Mode 110", "Dick's Sporting Goods", 799.0, None, 1, 4.7, 300),
    ]


def make_legacy_db():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    for stamp in ("2026-10-01T10:00:00+00:00", "2026-10-08T10:00:00+00:00"):
        conn.executemany(
            "INSERT INTO product_observations (market_id, keyword, observed_at, product_id, title, source, price, "
            "old_price, position, rating, reviews) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            legacy_rows(stamp),
        )
    conn.commit()
    conn.close()


def test_legacy_database_upgrades_in_place_and_is_idempotent():
    make_legacy_db()
    initialize_database()
    initialize_database()
    conn = sqlite3.connect(database_path())
    conn.row_factory = sqlite3.Row
    columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(markets)")}
    assert columns["brand_name"]["notnull"] == 0
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()

    market = repo.get_market(1)
    assert market["brand_name"] == "Paddle Co" and market["keywords"] == ["kayak", "fishing kayak"]
    scans = repo.list_scans(1, "shopping")
    assert len(scans) == 2 and all(s["is_demo"] == 0 for s in scans)
    assert all(len(repo.observations_for_scan(s["id"])) == 3 for s in scans)

    backfill_all()
    rows = repo.observations_for_scan(scans[-1]["id"])
    assert all(row["canonical_key"] for row in rows)
    assert {row["brand"] for row in rows} >= {"Pelican", "Lifetime"}
    # the same products in both scans share canonical keys
    first_keys = {row["canonical_key"] for row in repo.observations_for_scan(scans[0]["id"])}
    assert first_keys == {row["canonical_key"] for row in rows}
    assert repo.opportunities_for_scan(scans[-1]["id"])
    dashboard = build_dashboard(market)
    assert dashboard["kpis"]["observed_skus"] == 3 and dashboard["scan_count"] == 2

    repo.update_market(1, brand_name=None)
    assert repo.get_market(1)["brand_name"] is None
