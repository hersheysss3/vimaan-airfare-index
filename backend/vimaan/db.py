"""
Storage, as a medallion: bronze, silver, gold.

SQLite rather than Postgres+TimescaleDB, deliberately. The production design in
SOLUTION_PLAN.md calls for Timescale, and it should — but a reviewer cloning
this repository should be able to run the whole pipeline with no server to
install. The schema is written so the move is a change of driver, not a
redesign.

  bronze   the raw payload exactly as fetched, with a SHA-256 of the bytes.
           Never edited. This is what makes a published figure reproducible.
  silver   parsed, normalised FareObservation rows.
  gold     the price-surface cube, route indices, and published index points.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, Iterator, Optional

from .models import FareObservation, IndexPoint, RouteWeight

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vimaan.db"
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ---------------------------------------------------------------- BRONZE
CREATE TABLE IF NOT EXISTS bronze_snapshot (
    id                INTEGER PRIMARY KEY,
    lane              TEXT    NOT NULL,
    source            TEXT    NOT NULL,
    url               TEXT,
    fetched_at        TEXT    NOT NULL,
    sha256            TEXT    NOT NULL UNIQUE,
    content_type      TEXT,
    byte_length       INTEGER NOT NULL,
    payload           BLOB    NOT NULL,
    collector_version TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bronze_fetched ON bronze_snapshot(fetched_at);

-- ---------------------------------------------------------------- SILVER
CREATE TABLE IF NOT EXISTS silver_fare (
    id               INTEGER PRIMARY KEY,
    snapshot_id      INTEGER REFERENCES bronze_snapshot(id),
    lane             TEXT NOT NULL,
    source           TEXT NOT NULL,
    collected_at     TEXT NOT NULL,
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    route            TEXT NOT NULL,
    depart_date      TEXT NOT NULL,
    lead_days        INTEGER NOT NULL,
    lead_bucket      INTEGER NOT NULL,
    carrier          TEXT NOT NULL,
    flight_no        TEXT,
    cabin            TEXT NOT NULL,
    stratum          TEXT NOT NULL,
    item_id          TEXT NOT NULL,
    price_total      REAL NOT NULL,
    base_fare        REAL,
    taxes_fees       REAL,
    currency         TEXT NOT NULL DEFAULT 'INR',
    stops            INTEGER NOT NULL DEFAULT 0,
    duration_min     INTEGER,
    depart_slot      TEXT,
    refundable       INTEGER NOT NULL DEFAULT 0,
    checked_bag_kg   INTEGER,
    seats_remaining  INTEGER,
    -- one price per item per collection; a re-run must not duplicate rows
    UNIQUE(item_id, collected_at, source)
);
CREATE INDEX IF NOT EXISTS ix_silver_stratum ON silver_fare(stratum, collected_at);
CREATE INDEX IF NOT EXISTS ix_silver_route   ON silver_fare(route, depart_date);

-- ------------------------------------------------------------------ GOLD
CREATE TABLE IF NOT EXISTS gold_surface (
    period       TEXT NOT NULL,      -- YYYY-MM, the departure month
    route        TEXT NOT NULL,
    lead_bucket  INTEGER NOT NULL,
    cabin        TEXT NOT NULL,
    median_fare  REAL NOT NULL,
    n_obs        INTEGER NOT NULL,
    PRIMARY KEY (period, route, lead_bucket, cabin)
);

CREATE TABLE IF NOT EXISTS gold_route_index (
    period  TEXT NOT NULL,
    route   TEXT NOT NULL,
    value   REAL NOT NULL,
    n_obs   INTEGER NOT NULL,
    PRIMARY KEY (period, route)
);

CREATE TABLE IF NOT EXISTS gold_index (
    period            TEXT PRIMARY KEY,
    value             REAL NOT NULL,
    basis             TEXT NOT NULL,
    method            TEXT NOT NULL,
    window            INTEGER NOT NULL,
    coverage_pct      REAL NOT NULL,
    imputed_pct       REAL NOT NULL,
    observation_count INTEGER NOT NULL,
    route_count       INTEGER NOT NULL,
    revision          TEXT NOT NULL DEFAULT 'none',
    provisional       INTEGER NOT NULL DEFAULT 0,
    repro_hash        TEXT,
    published_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_weight (
    route      TEXT NOT NULL,
    base_year  INTEGER NOT NULL,
    passengers REAL NOT NULL,
    share      REAL NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (route, base_year)
);
"""


def connect(path: str = DEFAULT_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path: str = DEFAULT_PATH) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------- bronze
def put_snapshot(
    conn: sqlite3.Connection,
    *,
    lane: str,
    source: str,
    payload: bytes,
    url: Optional[str] = None,
    content_type: Optional[str] = None,
    collector_version: str = "0.1.0",
) -> tuple[int, str]:
    """Store a raw payload, keyed by its hash.

    Re-fetching identical bytes is not an error and does not duplicate: the
    hash is the identity. Returns (row id, sha256).
    """
    digest = hashlib.sha256(payload).hexdigest()
    row = conn.execute(
        "SELECT id FROM bronze_snapshot WHERE sha256 = ?", (digest,)
    ).fetchone()
    if row:
        return int(row["id"]), digest
    cur = conn.execute(
        """INSERT INTO bronze_snapshot
             (lane, source, url, fetched_at, sha256, content_type,
              byte_length, payload, collector_version)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (lane, source, url, datetime.utcnow().isoformat(timespec="seconds"),
         digest, content_type, len(payload), payload, collector_version),
    )
    return int(cur.lastrowid), digest


# --------------------------------------------------------------- silver
def put_fares(
    conn: sqlite3.Connection,
    fares: Iterable[FareObservation],
    snapshot_id: Optional[int] = None,
) -> int:
    rows = []
    for f in fares:
        rows.append((
            snapshot_id, f.lane.value, f.source,
            f.collected_at.isoformat(timespec="seconds"),
            f.origin, f.destination, f.route, f.depart_date.isoformat(),
            f.lead_days, f.bucket, f.carrier, f.flight_no, f.cabin.value,
            f.stratum, f.item_id, f.price_total, f.base_fare, f.taxes_fees,
            f.currency, f.stops, f.duration_min, f.depart_slot,
            1 if f.refundable else 0, f.checked_bag_kg, f.seats_remaining,
        ))
    if not rows:
        return 0
    cur = conn.executemany(
        """INSERT OR IGNORE INTO silver_fare
             (snapshot_id, lane, source, collected_at, origin, destination,
              route, depart_date, lead_days, lead_bucket, carrier, flight_no,
              cabin, stratum, item_id, price_total, base_fare, taxes_fees,
              currency, stops, duration_min, depart_slot, refundable,
              checked_bag_kg, seats_remaining)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    return cur.rowcount


# ----------------------------------------------------------------- gold
def put_weights(conn: sqlite3.Connection, weights: Iterable[RouteWeight]) -> int:
    rows = [(w.route, w.base_year, w.passengers, w.share, w.source)
            for w in weights]
    if not rows:
        return 0
    cur = conn.executemany(
        """INSERT OR REPLACE INTO route_weight
             (route, base_year, passengers, share, source) VALUES (?,?,?,?,?)""",
        rows,
    )
    return cur.rowcount


def get_weights(conn: sqlite3.Connection, base_year: int = 2024) -> dict[str, float]:
    rows = conn.execute(
        "SELECT route, share FROM route_weight WHERE base_year = ?", (base_year,)
    ).fetchall()
    return {r["route"]: r["share"] for r in rows}


def put_index_point(conn: sqlite3.Connection, point: IndexPoint) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO gold_index
             (period, value, basis, method, window, coverage_pct, imputed_pct,
              observation_count, route_count, revision, provisional,
              repro_hash, published_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (point.period, point.value, point.basis, point.method, point.window,
         point.coverage_pct, point.imputed_pct, point.observation_count,
         point.route_count, point.revision, 1 if point.provisional else 0,
         point.repro_hash, datetime.utcnow().isoformat(timespec="seconds")),
    )


def get_series(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM gold_index ORDER BY period"
    ).fetchall()
    return [dict(r) for r in rows]


def stats(conn: sqlite3.Connection) -> dict:
    def one(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])
    return {
        "snapshots": one("SELECT COUNT(*) FROM bronze_snapshot"),
        "fares": one("SELECT COUNT(*) FROM silver_fare"),
        "surface_cells": one("SELECT COUNT(*) FROM gold_surface"),
        "route_indices": one("SELECT COUNT(*) FROM gold_route_index"),
        "published_periods": one("SELECT COUNT(*) FROM gold_index"),
        "weighted_routes": one("SELECT COUNT(*) FROM route_weight"),
    }
