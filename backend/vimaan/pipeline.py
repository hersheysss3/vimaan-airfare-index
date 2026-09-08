"""
silver -> gold: fares to a published index number.

The order matters and is the same order the deck describes:

  1. build the price surface  (median per route x lead bucket x cabin, per month)
  2. elementary index         (Jevons within each stratum)
  3. multilateral             (GEKS-Jevons over a rolling window, mean spliced)
  4. aggregate                (Lowe, weighted by real DGCA passengers)
  5. attach provenance        (coverage, imputation, a reproducibility hash)

Every published figure carries the quality metadata computed here. A number
without it is not publishable, which is why IndexPoint requires the fields.
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Iterable, Optional

from .index.aggregate import contributions, lowe
from .index.multilateral import geks_jevons, rolling_window
from .models import IndexPoint


def build_surface(conn: sqlite3.Connection) -> int:
    """Collapse raw fares into the price-surface cube.

    The median, not the mean: a single mispriced or stale quote should not move
    a cell. Attribution is by departure month, following ONS practice, because
    the index measures the price of taking the flight rather than of buying it.
    """
    conn.execute("DELETE FROM gold_surface")
    rows = conn.execute(
        """SELECT substr(depart_date,1,7) AS period, route, lead_bucket, cabin,
                  price_total
             FROM silver_fare"""
    ).fetchall()

    cells: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        cells[(r["period"], r["route"], r["lead_bucket"], r["cabin"])].append(
            float(r["price_total"]))

    payload = [(p, rt, lb, cb, statistics.median(v), len(v))
               for (p, rt, lb, cb), v in cells.items() if v]
    conn.executemany(
        """INSERT OR REPLACE INTO gold_surface
             (period, route, lead_bucket, cabin, median_fare, n_obs)
           VALUES (?,?,?,?,?,?)""",
        payload,
    )
    return len(payload)


def _periods_by_route(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, float]]]:
    """route -> period -> {stratum item: median fare}.

    The stratum key is the item identity for the elementary index: comparing
    lead bucket L7 with L7 across months, never L1 against L60.
    """
    rows = conn.execute(
        """SELECT period, route, lead_bucket, cabin, median_fare
             FROM gold_surface ORDER BY period"""
    ).fetchall()
    out: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        key = f"L{r['lead_bucket']}|{r['cabin']}"
        out[r["route"]][r["period"]][key] = float(r["median_fare"])
    return out


def build_route_indices(conn: sqlite3.Connection, window: int = 25) -> int:
    """A GEKS-Jevons series per route, base 100 at its first observed period."""
    conn.execute("DELETE FROM gold_route_index")
    by_route = _periods_by_route(conn)

    written = 0
    for route, per_period in by_route.items():
        periods = sorted(per_period)
        if len(periods) < 2:
            continue
        panel = [per_period[p] for p in periods]
        series = rolling_window(panel, window=window, estimator=geks_jevons)
        for period, value in zip(periods, series):
            if value != value:          # NaN
                continue
            conn.execute(
                """INSERT OR REPLACE INTO gold_route_index
                     (period, route, value, n_obs) VALUES (?,?,?,?)""",
                (period, route, value * 100.0, len(per_period[period])),
            )
            written += 1
    return written


def publish(
    conn: sqlite3.Connection,
    base_year: int = 2024,
    window: int = 25,
    method: str = "GEKS-Jevons",
) -> list[IndexPoint]:
    """Aggregate route indices into the national figure, with provenance."""
    weights = {
        r["route"]: r["share"]
        for r in conn.execute(
            "SELECT route, share FROM route_weight WHERE base_year = ?",
            (base_year,)).fetchall()
    }
    if not weights:
        raise ValueError(
            f"no route weights loaded for {base_year}; "
            "run scripts/load_weights.py first")

    rows = conn.execute(
        "SELECT period, route, value, n_obs FROM gold_route_index ORDER BY period"
    ).fetchall()
    by_period: dict[str, dict[str, float]] = defaultdict(dict)
    obs_by_period: dict[str, int] = defaultdict(int)
    for r in rows:
        by_period[r["period"]][r["route"]] = float(r["value"])
        obs_by_period[r["period"]] += int(r["n_obs"])

    total_weight = sum(weights.values())
    points: list[IndexPoint] = []

    for period in sorted(by_period):
        indices = by_period[period]
        covered = {k: v for k, v in indices.items() if k in weights}
        if not covered:
            continue

        # coverage is by weight, not by count: missing Delhi-Mumbai matters
        # far more than missing a thin regional route, and a headcount would
        # hide that.
        covered_weight = sum(weights[k] for k in covered)
        coverage = covered_weight / total_weight * 100.0 if total_weight else 0.0

        value = lowe(covered, {k: weights[k] for k in covered})

        point = IndexPoint(
            period=period,
            value=value,
            method=method,
            window=window,
            coverage_pct=round(coverage, 2),
            imputed_pct=0.0,
            observation_count=obs_by_period[period],
            route_count=len(covered),
            provisional=coverage < 70.0,
        )
        point.repro_hash = point.compute_hash(
            f"routes={len(covered)}",
            f"obs={point.observation_count}",
            f"coverage={point.coverage_pct}",
            f"value={value:.6f}",
        )
        points.append(point)

    return points


def route_contributions(
    conn: sqlite3.Connection, period: str, base_year: int = 2024
) -> dict[str, float]:
    """Which routes moved the figure in a given period, in index points."""
    weights = {
        r["route"]: r["share"]
        for r in conn.execute(
            "SELECT route, share FROM route_weight WHERE base_year = ?",
            (base_year,)).fetchall()
    }
    rows = conn.execute(
        "SELECT route, value FROM gold_route_index WHERE period = ?", (period,)
    ).fetchall()
    indices = {r["route"]: float(r["value"]) for r in rows if r["route"] in weights}
    if not indices:
        return {}
    return contributions(indices, {k: weights[k] for k in indices})


def run_all(conn: sqlite3.Connection, base_year: int = 2024, window: int = 25) -> dict:
    """The whole silver -> gold path, as one call."""
    cells = build_surface(conn)
    routes = build_route_indices(conn, window=window)
    points = publish(conn, base_year=base_year, window=window)
    from .db import put_index_point
    for p in points:
        put_index_point(conn, p)
    return {
        "surface_cells": cells,
        "route_index_rows": routes,
        "published_periods": len(points),
        "latest": points[-1].model_dump() if points else None,
    }
