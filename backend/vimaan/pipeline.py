"""
silver -> gold: fares to a published index number.

The order matters and is the same order the deck describes:

  1. build the price surface  (median per route x lead bucket x cabin, per month)
  2. impute the holes         (targeted mean, every filled cell flagged)
  3. fit the hedonic model    (so a substitution is priced, not published)
  4. elementary index         (Jevons within each stratum)
  5. multilateral             (GEKS-Jevons over a rolling window, mean spliced)
  6. cross-check              (Time-Product-Dummy over identical data)
  7. aggregate                (Lowe, weighted by real DGCA passengers)
  8. parallel basis           (the same month priced on acquisition)
  9. seasonal adjustment      (moving Indian holidays, then decomposition)
 10. attach provenance        (coverage, imputation, revision, a repro hash)

Every published figure carries the quality metadata computed here. A number
without it is not publishable, which is why IndexPoint requires the fields.
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Iterable, Mapping, Optional, Sequence

from .index.aggregate import contributions, lowe
from .index.hedonic import HedonicModel, fit as fit_hedonic_model
from .index.imputation import Cell, impute
from .index.multilateral import geks_jevons, rolling_window, time_product_dummy
from .index import seasonal as seasonal_mod
from .models import IndexPoint

#: Beyond this many index points apart, GEKS and TPD are not corroborating
#: each other and the month is flagged for a human to look at.
TPD_DIVERGENCE_FLAG = 2.0

#: Weighted coverage below which a figure is provisional rather than firm.
MIN_FIRM_COVERAGE_PCT = 70.0

#: Imputed share above which a figure is provisional however good its coverage
#: looks. These are different failures and the old code only caught the first:
#: coverage asks "did we see these routes at all", imputation asks "how much of
#: what we published did we actually measure". A month can have every heavy
#: route present and still be almost entirely inferred, which is exactly what
#: a thin collection window produces, and publishing that as a measurement is
#: the failure this index exists to avoid.
MAX_FIRM_IMPUTED_PCT = 50.0


# ------------------------------------------------------------------ surface

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

    payload = [(p, rt, lb, cb, statistics.median(v), len(v), 0, None)
               for (p, rt, lb, cb), v in cells.items() if v]
    conn.executemany(
        """INSERT OR REPLACE INTO gold_surface
             (period, route, lead_bucket, cabin, median_fare, n_obs,
              imputed, imputation_basis)
           VALUES (?,?,?,?,?,?,?,?)""",
        payload,
    )
    return len(payload)


def impute_surface(conn: sqlite3.Connection) -> dict:
    """Fill the holes in the surface, marking every cell that was filled.

    Imputed cells enter the index exactly like observed ones — that is the
    point, an index with a hole in it is not comparable period to period —
    but they are stored flagged so `imputed_pct` is a measured quantity
    rather than the zero it used to be.
    """
    rows = conn.execute(
        """SELECT period, route, lead_bucket, cabin, median_fare
             FROM gold_surface WHERE imputed = 0"""
    ).fetchall()
    observed = {
        Cell(r["period"], r["route"], int(r["lead_bucket"]), r["cabin"]):
            float(r["median_fare"])
        for r in rows
    }
    if not observed:
        return {"observed_cells": 0, "imputed_cells": 0, "dropped_cells": 0,
                "imputed_by_basis": {}}

    result = impute(observed)

    conn.executemany(
        """INSERT OR REPLACE INTO gold_surface
             (period, route, lead_bucket, cabin, median_fare, n_obs,
              imputed, imputation_basis)
           VALUES (?,?,?,?,?,?,1,?)""",
        [(i.cell.period, i.cell.route, i.cell.lead_bucket, i.cell.cabin,
          i.value, 0, i.basis) for i in result.imputed.values()],
    )
    return result.summary()


def _acquisition_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The same fares, attributed to the month they were *bought* in."""
    return conn.execute(
        """SELECT substr(collected_at,1,7) AS period, route, lead_bucket, cabin,
                  price_total
             FROM silver_fare"""
    ).fetchall()


# ------------------------------------------------------------------ hedonic

def fit_hedonic(conn: sqlite3.Connection) -> Optional[HedonicModel]:
    """Fit the quality model on the pooled silver panel.

    Returns None when there is not enough variation to fit anything — which is
    a real state, not a failure, and is recorded as such rather than papered
    over with a model nobody should trust.
    """
    rows = conn.execute(
        """SELECT substr(depart_date,1,7) AS period, price_total AS price,
                  stops, duration_min, depart_slot, refundable, checked_bag_kg,
                  carrier, cabin, lead_bucket
             FROM silver_fare"""
    ).fetchall()
    if len(rows) < 50:
        return None

    obs = [dict(r) for r in rows]
    for o in obs:
        o["refundable"] = bool(o["refundable"])
    try:
        model = fit_hedonic_model(obs)
    except ValueError:
        return None

    from .db import put_model
    put_model(conn, "hedonic", model.summary())
    return model


# ------------------------------------------------------- index construction

def _panels(rows: Iterable[Mapping]) -> dict[str, dict[str, dict[str, float]]]:
    """route -> period -> {stratum item: price}.

    The stratum key is the item identity for the elementary index: comparing
    lead bucket L7 with L7 across months, never L1 against L60.
    """
    out: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        key = f"L{r['lead_bucket']}|{r['cabin']}"
        out[r["route"]][r["period"]][key] = float(r["median_fare"])
    return out


def _route_series(
    panels: Mapping[str, Mapping[str, Mapping[str, float]]],
    window: int,
    estimator=geks_jevons,
) -> dict[str, dict[str, float]]:
    """A multilateral series per route, base 100 at its first observed period."""
    out: dict[str, dict[str, float]] = {}
    for route, per_period in panels.items():
        periods = sorted(per_period)
        if len(periods) < 2:
            continue
        panel = [per_period[p] for p in periods]
        series = rolling_window(panel, window=window, estimator=estimator)
        out[route] = {p: v * 100.0 for p, v in zip(periods, series)
                      if v == v}          # drop NaN
    return out


def _aggregate(
    route_series: Mapping[str, Mapping[str, float]],
    weights: Mapping[str, float],
) -> dict[str, float]:
    """Weighted national level per period."""
    by_period: dict[str, dict[str, float]] = defaultdict(dict)
    for route, series in route_series.items():
        for period, value in series.items():
            by_period[period][route] = value

    out: dict[str, float] = {}
    for period, indices in by_period.items():
        covered = {k: v for k, v in indices.items() if k in weights}
        if not covered:
            continue
        out[period] = lowe(covered, {k: weights[k] for k in covered})
    return out


def build_route_indices(conn: sqlite3.Connection, window: int = 25) -> int:
    """A GEKS-Jevons series per route, written to gold_route_index."""
    conn.execute("DELETE FROM gold_route_index")
    rows = conn.execute(
        """SELECT period, route, lead_bucket, cabin, median_fare
             FROM gold_surface ORDER BY period"""
    ).fetchall()
    panels = _panels(rows)
    series = _route_series(panels, window=window)

    counts = defaultdict(int)
    for r in rows:
        counts[(r["route"], r["period"])] += 1

    # one round trip, not one per row: against a hosted Postgres the
    # per-row version spent minutes on network latency alone
    payload = [(period, route, value, counts[(route, period)])
               for route, per_period in series.items()
               for period, value in per_period.items()]
    if payload:
        conn.executemany(
            """INSERT OR REPLACE INTO gold_route_index
                 (period, route, value, n_obs) VALUES (?,?,?,?)""",
            payload,
        )
    return len(payload)


# ------------------------------------------------------------------ publish

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

    # imputation share, measured off the flags the imputer left behind
    imputed_share: dict[str, float] = {}
    for r in conn.execute(
        """SELECT period,
                  SUM(CASE WHEN imputed = 1 THEN 1 ELSE 0 END) AS imp,
                  COUNT(*) AS total
             FROM gold_surface GROUP BY period"""
    ).fetchall():
        total = int(r["total"]) or 1
        imputed_share[r["period"]] = int(r["imp"]) / total * 100.0

    # the cross-check and the parallel basis, both over the same weights
    tpd_national = _tpd_national(conn, weights, window)
    acquisition = _acquisition_national(conn, weights, window)

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
        tpd = tpd_national.get(period)
        divergence = round(value - tpd, 4) if tpd is not None else None

        imputed_pct = round(imputed_share.get(period, 0.0), 2)
        reasons = []
        if coverage < MIN_FIRM_COVERAGE_PCT:
            reasons.append(f"coverage {coverage:.1f}% < {MIN_FIRM_COVERAGE_PCT}%")
        if imputed_pct > MAX_FIRM_IMPUTED_PCT:
            reasons.append(f"imputed {imputed_pct:.1f}% > {MAX_FIRM_IMPUTED_PCT}%")

        point = IndexPoint(
            period=period,
            value=value,
            method=method,
            window=window,
            coverage_pct=round(coverage, 2),
            imputed_pct=imputed_pct,
            observation_count=obs_by_period[period],
            route_count=len(covered),
            provisional=bool(reasons),
            provisional_reason="; ".join(reasons) or None,
            value_tpd=round(tpd, 4) if tpd is not None else None,
            tpd_divergence=divergence,
            value_acquisition=(round(acquisition[period], 4)
                               if period in acquisition else None),
        )
        point.repro_hash = point.compute_hash(
            f"routes={len(covered)}",
            f"obs={point.observation_count}",
            f"coverage={point.coverage_pct}",
            f"imputed={point.imputed_pct}",
            f"value={value:.6f}",
        )
        points.append(point)

    return points


def _tpd_national(
    conn: sqlite3.Connection,
    weights: Mapping[str, float],
    window: int,
) -> dict[str, float]:
    """The national series recomputed with Time-Product-Dummy.

    Same surface, same weights, different estimator. Where the two agree the
    figure is corroborated by a method that shares none of GEKS's machinery;
    where they diverge, something about that month deserves a look.

    Read the divergence honestly. On a fully matched panel — every cell present
    in every period, which is what the surface looks like once imputation has
    filled it — GEKS-Jevons and TPD are algebraically the same index, and the
    divergence is exactly zero. That is a property of the estimators, not
    evidence that the figure is right. The cross-check only carries
    information where the sample actually churns: cells that appear, vanish,
    or are dropped past the imputation limit. A run reporting zero divergence
    across the board is reporting that its panel is balanced.
    """
    rows = conn.execute(
        """SELECT period, route, lead_bucket, cabin, median_fare
             FROM gold_surface ORDER BY period"""
    ).fetchall()
    panels = _panels(rows)
    series = _route_series(panels, window=window, estimator=time_product_dummy)
    return _aggregate(series, weights)


def _acquisition_national(
    conn: sqlite3.Connection,
    weights: Mapping[str, float],
    window: int,
) -> dict[str, float]:
    """The national series on an acquisition basis, published in parallel.

    Departure basis asks what it cost to fly in month M. Acquisition basis
    asks what it cost to *buy* a ticket in month M, whenever the flight
    departs. Neither is wrong; they answer different questions, and a CPI
    compiler needs to know which one they are being handed. ONS publishes
    both for air fares and the reason is that the gap between them is itself
    informative — it is the advance-purchase behaviour of the market.
    """
    rows = _acquisition_rows(conn)
    cells: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        cells[(r["period"], r["route"], r["lead_bucket"], r["cabin"])].append(
            float(r["price_total"]))

    surface = [{"period": p, "route": rt, "lead_bucket": lb, "cabin": cb,
                "median_fare": statistics.median(v)}
               for (p, rt, lb, cb), v in cells.items() if v]
    if not surface:
        return {}
    panels = _panels(surface)
    series = _route_series(panels, window=window)
    return _aggregate(series, weights)


# -------------------------------------------------------- seasonal adjustment

def seasonally_adjust(
    conn: sqlite3.Connection,
    holidays: Sequence[str] = ("diwali", "holi", "dussehra"),
) -> Optional[dict]:
    """Adjust the published series and write the adjusted level back.

    Needs two full years of monthly figures; below that the seasonal factors
    are not identified and this returns None rather than inventing them.
    """
    rows = conn.execute(
        "SELECT period, value FROM gold_index ORDER BY period").fetchall()
    periods = [r["period"] for r in rows]
    values = [float(r["value"]) for r in rows]
    if len(periods) < 24:
        return None

    result = seasonal_mod.adjust(periods, values, holidays=holidays)
    conn.executemany(
        "UPDATE gold_index SET value_sa = ?, sa_engine = ? WHERE period = ?",
        [(round(sa, 4), result.engine, period)
         for period, sa in zip(result.periods, result.adjusted)],
    )

    from .db import put_model
    put_model(conn, "seasonal", result.summary())
    return result.summary()


# ---------------------------------------------------------------- reporting

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


def run_all(conn: sqlite3.Connection, base_year: int = 2024,
            window: int = 25) -> dict:
    """The whole silver -> gold path, as one call."""
    from .db import put_index_point

    cells = build_surface(conn)
    imputation = impute_surface(conn)
    hedonic = fit_hedonic(conn)
    routes = build_route_indices(conn, window=window)
    points = publish(conn, base_year=base_year, window=window)

    revisions = []
    for p in points:
        rev = put_index_point(conn, p)
        if rev:
            revisions.append(rev)

    sa = seasonally_adjust(conn)

    flagged = [p.period for p in points
               if p.tpd_divergence is not None
               and abs(p.tpd_divergence) > TPD_DIVERGENCE_FLAG]

    # read the latest row back rather than returning the in-memory point:
    # seasonal adjustment wrote value_sa after publish() built these objects,
    # and returning the stale one would report a null adjusted figure that
    # the database does in fact hold
    latest = None
    row = conn.execute(
        "SELECT * FROM gold_index ORDER BY period DESC LIMIT 1").fetchone()
    if row:
        latest = dict(row)

    return {
        "surface_cells": cells,
        "imputation": imputation,
        "hedonic": hedonic.summary() if hedonic else None,
        "route_index_rows": routes,
        "published_periods": len(points),
        "revisions": revisions,
        "seasonal": sa,
        "tpd_divergence_flagged": flagged,
        "latest": latest,
    }
