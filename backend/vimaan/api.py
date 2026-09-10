"""
The public surface: a REST API, and an SDMX-JSON view of the same numbers.

Every index response embeds its own provenance — coverage, imputation share,
observation count, revision state and a reproducibility hash. That is the
point of the design: a figure that arrives without the means to check it is
not a statistic, it is a claim.

    uvicorn vimaan.api:app --reload
    open http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from . import db, pipeline
from .data import dgca

app = FastAPI(
    title="VIMAAN",
    version="0.2.0",
    description=(
        "Real-time airfare price index for India, for augmentation of the CPI. "
        "Smart India Hackathon 2026, problem SIH26056."
    ),
)

#: Origins allowed to read the API from a browser. The console is served from
#: the same deployment, so same-origin requests need no entry here at all —
#: this list exists for the local console and any preview deployment. A bare
#: "*" is not used: the API is read-only, but an allowlist costs nothing and
#: keeps the surface described rather than open.
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "VIMAAN_ALLOWED_ORIGINS",
        "https://vimaan-console.vercel.app,http://localhost:8000,"
        "http://127.0.0.1:8000,http://localhost:5173,null"
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://vimaan-console-[a-z0-9\-]+\.vercel\.app",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

#: Environment beats the bundled SQLite file: on Vercel DATABASE_URL is set by
#: the Neon integration and db.connect picks it up on its own.
DB_PATH = os.environ.get("VIMAAN_DB") or os.environ.get("DATABASE_URL")


def _conn():
    return db.connect(DB_PATH)


# --------------------------------------------------------------- throttling

#: Requests per window per client. Deliberately generous: this is a public
#: read-only statistical API and the point is to stop a runaway loop, not to
#: meter access.
RATE_LIMIT = int(os.environ.get("VIMAAN_RATE_LIMIT", "120"))
RATE_WINDOW_S = 60.0

_hits: dict[str, deque] = defaultdict(deque)


def throttle(request: Request) -> None:
    """A per-process token bucket.

    Stated limitation, because it matters and is easy to oversell: serverless
    runs many instances and each keeps its own counter, so the effective limit
    is this number times however many instances are warm. It is a guard
    against a client in a loop, not a security control. Real rate limiting
    belongs at the edge — Vercel's firewall, or a shared Redis counter — and
    that is where it should go if this ever carries real load.
    """
    client = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else "unknown"))
    now = time.monotonic()
    window = _hits[client]
    while window and now - window[0] > RATE_WINDOW_S:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(
            429, f"rate limit {RATE_LIMIT}/min exceeded; this API is public "
                 f"and read-only, please cache rather than poll")
    window.append(now)


# applies to every route below, including the ones defined before this line
app.router.dependencies.append(Depends(throttle))


def _describe_store() -> str:
    """What the store is, with nothing sensitive in it.

    This used to return DB_PATH verbatim, which on Postgres is a DSN with the
    password in it — published to anyone who called /v1/health. Never echo a
    connection string; the caller needs to know which kind of store answered,
    not how to connect to it.
    """
    if not DB_PATH:
        return "sqlite (bundled)"
    if DB_PATH.startswith(("postgres://", "postgresql://")):
        host = DB_PATH.split("@")[-1].split("/")[0].split("?")[0]
        return f"postgres ({host})"
    return "sqlite (file)"


@app.get("/v1/health", tags=["meta"])
def health():
    conn = _conn()
    try:
        return {"status": "ok", "store": _describe_store(), **db.stats(conn)}
    finally:
        conn.close()


@app.get("/v1/collection/stats", tags=["collection"])
def collection_stats():
    """What collection is actually achieving, as opposed to designed for.

    The design samples 517 routes three times a day. What a given deployment
    is really doing depends on which lanes are wired and what the upstream
    cache holds, and those two numbers are not the same. Publishing only the
    design figure would be a claim the system does not currently meet, so
    both are returned and the console shows the measured one.
    """
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT source, lane, COUNT(*) AS n,
                      MIN(collected_at) AS first_seen,
                      MAX(collected_at) AS last_seen
                 FROM silver_fare GROUP BY source, lane"""
        ).fetchall()
        by_source = [dict(r) for r in rows]

        real = [s for s in by_source if s["source"] != "simulated"]
        simulated = [s for s in by_source if s["source"] == "simulated"]

        routes_live = conn.execute(
            """SELECT COUNT(DISTINCT route) AS n FROM silver_fare
                 WHERE source <> 'simulated'"""
        ).fetchone()["n"]

        return {
            "design": {
                "routes": 517,
                "collections_per_day": 3,
                "fares_per_day": 151_000,
                "note": "the sampling design in SOLUTION_PLAN.md",
            },
            "actual": {
                "routes_with_collected_fares": int(routes_live or 0),
                "collected_fares": sum(int(s["n"]) for s in real),
                "simulated_fares": sum(int(s["n"]) for s in simulated),
                "note": ("what this deployment has actually collected. Lane A "
                         "tariff parsers are not written and Lane C is not "
                         "wired, so live collection is Lane B only."),
            },
            "by_source": by_source,
        }
    finally:
        conn.close()


@app.get("/v1/index/national", tags=["index"])
def national(period: Optional[str] = Query(None, description="YYYY-MM")):
    """The headline figure, with the evidence attached."""
    conn = _conn()
    try:
        series = db.get_series(conn)
        if not series:
            raise HTTPException(404, "no index published yet; run the pipeline")
        if period:
            match = [r for r in series if r["period"] == period]
            if not match:
                raise HTTPException(404, f"no figure for {period}")
            return match[0]
        return series[-1]
    finally:
        conn.close()


@app.get("/v1/index/national/series", tags=["index"])
def series(
    start: Optional[str] = Query(None, description="YYYY-MM"),
    end: Optional[str] = Query(None, description="YYYY-MM"),
):
    conn = _conn()
    try:
        rows = db.get_series(conn)
        if start:
            rows = [r for r in rows if r["period"] >= start]
        if end:
            rows = [r for r in rows if r["period"] <= end]
        return {"count": len(rows), "observations": rows}
    finally:
        conn.close()


@app.get("/v1/index/route/{route}", tags=["index"])
def route_index(route: str, period: Optional[str] = None):
    conn = _conn()
    try:
        sql = "SELECT * FROM gold_route_index WHERE route = ?"
        args: list = [route.upper()]
        if period:
            sql += " AND period = ?"
            args.append(period)
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY period", args)]
        if not rows:
            raise HTTPException(404, f"no index for route {route}")
        return {"route": route.upper(), "count": len(rows), "observations": rows}
    finally:
        conn.close()


@app.get("/v1/contributions", tags=["index"])
def contributions(period: str = Query(..., description="YYYY-MM")):
    """Which routes moved the national figure, in index points.

    These sum to the aggregate's deviation from base, which is asserted in the
    test suite rather than assumed here.
    """
    conn = _conn()
    try:
        c = pipeline.route_contributions(conn, period)
        if not c:
            raise HTTPException(404, f"no contributions for {period}")
        ranked = sorted(c.items(), key=lambda kv: -abs(kv[1]))
        return {
            "period": period,
            "total_points": round(sum(c.values()), 4),
            "routes": [{"route": r, "points": round(v, 4)} for r, v in ranked],
        }
    finally:
        conn.close()


@app.get("/v1/surface", tags=["data"])
def surface(route: str = Query(...), period: Optional[str] = None):
    """The price surface for one route: lead bucket by departure month."""
    conn = _conn()
    try:
        sql = "SELECT * FROM gold_surface WHERE route = ?"
        args: list = [route.upper()]
        if period:
            sql += " AND period = ?"
            args.append(period)
        rows = [dict(r) for r in conn.execute(
            sql + " ORDER BY period, lead_bucket", args)]
        if not rows:
            raise HTTPException(404, f"no surface for {route}")
        return {"route": route.upper(), "cells": len(rows), "surface": rows}
    finally:
        conn.close()


@app.get("/v1/weights", tags=["data"])
def weights(base_year: int = 2024, top: int = 50):
    """The DGCA passenger weights the aggregation actually uses."""
    conn = _conn()
    try:
        rows = [dict(r) for r in conn.execute(
            """SELECT route, passengers, share, source FROM route_weight
                 WHERE base_year = ? ORDER BY share DESC LIMIT ?""",
            (base_year, top))]
        if not rows:
            raise HTTPException(404, "no weights loaded")
        return {
            "base_year": base_year,
            "source": dgca.SOURCE_NAME,
            "licence": dgca.SOURCE_LICENCE,
            "count": len(rows),
            "weights": rows,
        }
    finally:
        conn.close()


@app.get("/v1/methodology/{period}", tags=["meta"])
def methodology(period: str):
    """Exactly how the figure for this period was produced.

    A published number should be able to explain itself without a person in
    the loop; this is that endpoint.
    """
    conn = _conn()
    try:
        rows = [r for r in db.get_series(conn) if r["period"] == period]
        if not rows:
            raise HTTPException(404, f"no figure for {period}")
        row = rows[0]
        hedonic = db.get_model(conn, "hedonic") or {}
        seasonal = db.get_model(conn, "seasonal") or {}
        return {
            "period": period,
            "value": row["value"],
            "formulae": {
                "elementary": "Jevons geometric mean, per route x lead bucket x cabin",
                "multilateral": f"{row['method']}, {row['window']}-month rolling "
                                f"window, mean spliced",
                "aggregation": "Lowe, weights = DGCA base-period passengers",
                "quality_adjustment": (
                    hedonic.get("form", "time-dummy hedonic")
                    + (f" (R^2 {hedonic['r_squared']}, "
                       f"{'applied' if hedonic.get('usable') else 'fit too weak to apply'})"
                       if hedonic else " (not fitted)")),
                "imputation": (
                    "targeted mean: a missing cell keeps its last observed "
                    "level and takes the movement of the nearest comparable "
                    "class of cells that were observed"),
                "seasonal_adjustment": (
                    f"{seasonal.get('engine', 'not run')}"
                    + (f", moving holidays: "
                       f"{', '.join(seasonal.get('holidays_modelled', []))}"
                       if seasonal.get("holidays_modelled") else "")),
            },
            "basis": row["basis"],
            "parallel_series": {
                "seasonally_adjusted": row["value_sa"],
                "seasonal_engine": row["sa_engine"],
                "acquisition_basis": row["value_acquisition"],
                "time_product_dummy": row["value_tpd"],
                "tpd_divergence_points": row["tpd_divergence"],
            },
            "quality": {
                "coverage_pct": row["coverage_pct"],
                "imputed_pct": row["imputed_pct"],
                "observation_count": row["observation_count"],
                "route_count": row["route_count"],
                "provisional": bool(row["provisional"]),
                "provisional_reason": row["provisional_reason"],
                "revision": row["revision"],
            },
            "repro_hash": row["repro_hash"],
            "weights_source": dgca.SOURCE_NAME,
            "references": [
                "ONS, Methodology Working Paper 12",
                "ILO/IMF/OECD/Eurostat, CPI Manual 2020",
                "MoSPI, CPI base 2024 = 100",
            ],
        }
    finally:
        conn.close()


@app.get("/v1/models/hedonic", tags=["meta"])
def hedonic_model():
    """The quality-adjustment model's coefficients and how well it fits.

    Published because a hedonic adjustment nobody can inspect is an assertion.
    The R-squared is the number to look at first: below the usable threshold
    the model is not removing quality, it is adding noise.
    """
    conn = _conn()
    try:
        model = db.get_model(conn, "hedonic")
        if not model:
            raise HTTPException(404, "no hedonic model fitted yet")
        return model
    finally:
        conn.close()


@app.get("/v1/models/seasonal", tags=["meta"])
def seasonal_model():
    """Which seasonal engine ran, and what it estimated for each festival.

    `engine` is either X-13ARIMA-SEATS or RegARIMA+STL and is not decorative:
    the X-13 binary is not present in a serverless deployment, so the fallback
    is what produces the published adjusted series there. The distinction is
    reported rather than smoothed over.
    """
    conn = _conn()
    try:
        model = db.get_model(conn, "seasonal")
        if not model:
            raise HTTPException(
                404, "no seasonal adjustment yet; it needs 24 monthly figures")
        return model
    finally:
        conn.close()


@app.get("/v1/revisions", tags=["meta"])
def revisions(period: Optional[str] = None):
    """Every published figure that was later restated, and by how much."""
    conn = _conn()
    try:
        rows = db.get_revisions(conn, period)
        return {"count": len(rows), "revisions": rows}
    finally:
        conn.close()


@app.get("/v1/drift", tags=["index"])
def drift(window: int = Query(25, ge=1, le=60)):
    """The chain-drift demonstration, computed rather than asserted.

    Runs both estimators over the published surface and reports how far each
    has wandered. This is the console's central claim and it is served from
    the same code path that produces the index, so the two cannot diverge.
    """
    from .index.multilateral import drift as drift_of
    from .index.multilateral import geks_jevons, time_product_dummy
    from .index.elementary import chained

    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT period, route, lead_bucket, cabin, median_fare
                 FROM gold_surface ORDER BY period""").fetchall()
        if not rows:
            raise HTTPException(404, "no surface built yet")

        panels = pipeline._panels(rows)
        out = {}
        for name, estimator in (("chained_jevons", chained),
                                ("geks_jevons", geks_jevons),
                                ("time_product_dummy", time_product_dummy)):
            drifts = []
            for per_period in panels.values():
                periods = sorted(per_period)
                if len(periods) < 2:
                    continue
                series = estimator([per_period[p] for p in periods])
                clean = [v for v in series if v == v]
                if len(clean) >= 2:
                    drifts.append(drift_of(clean))
            out[name] = round(sum(drifts) / len(drifts), 4) if drifts else None

        return {
            "window": window,
            "routes_measured": len(panels),
            "mean_drift_points": out,
            "reading": (
                "Drift is how far an index wandered from its own starting "
                "point. On matched samples with no true trend it should be "
                "near zero; a chained index on churning airline inventory is "
                "not, which is the argument for a multilateral estimator."),
        }
    finally:
        conn.close()


@app.get("/api/cron/collect", tags=["collection"], include_in_schema=False)
@app.get("/v1/cron/collect", tags=["collection"], include_in_schema=False)
def cron_collect(
    routes: int = Query(20, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    """The scheduler's entry point. Vercel Cron issues GET, so this exists
    alongside the POST form rather than instead of it."""
    return run_collection(routes=routes, authorization=authorization)


@app.post("/v1/collect/run", tags=["collection"])
def run_collection(
    routes: int = Query(20, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    """Trigger one collection pass. Called by the scheduler, not by browsers.

    Protected by a shared secret: collection spends a third-party rate budget
    and writes to the published store, so it is the one endpoint here that is
    not safe to leave open. Vercel Cron sends the secret as a bearer token.
    """
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        raise HTTPException(
            503, "CRON_SECRET is not configured; collection is disabled")
    if authorization != f"Bearer {secret}":
        raise HTTPException(401, "collection requires the scheduler secret")

    from .collect.run import collect_once

    conn = _conn()
    try:
        report = collect_once(conn, limit_routes=routes)
        conn.commit()
        result = report.summary()

        if report.fares_written:
            out = pipeline.run_all(conn)
            conn.commit()
            result["pipeline"] = {
                "published_periods": out["published_periods"],
                "revisions": len(out["revisions"]),
                "imputation": out["imputation"],
            }
        return result
    finally:
        conn.close()


@app.get("/v1/sdmx/CPI_AIRFARE", tags=["exchange"])
def sdmx(period: Optional[str] = None):
    """SDMX-JSON, the format MoSPI, the RBI and the IMF already exchange.

    Publishing in it is what makes this ingestible without an integration
    project on the other side.
    """
    conn = _conn()
    try:
        rows = db.get_series(conn)
        if period:
            rows = [r for r in rows if r["period"] == period]
        if not rows:
            raise HTTPException(404, "nothing to publish")
        latest = rows[-1]
        return {
            "meta": {
                "schema": "https://json.sdmx.org/2.1/sdmx-json.json",
                "id": "IN_AIRFARE_IDX",
                "prepared": latest["published_at"],
                "sender": {"id": "IN_MOSPI", "name": "VIMAAN prototype"},
            },
            "data": {
                "structure": {
                    "dimensions": {
                        "observation": [{
                            "id": "TIME_PERIOD",
                            "name": "Time period",
                            "values": [{"id": r["period"], "name": r["period"]}
                                       for r in rows],
                        }]
                    },
                    "attributes": {
                        "series": [
                            {"id": "BASIS", "name": "Attribution basis"},
                            {"id": "METHOD", "name": "Index method"},
                            {"id": "COVERAGE", "name": "Coverage percent"},
                            {"id": "REPRO_HASH", "name": "Reproducibility hash"},
                        ]
                    },
                },
                "dataSets": [{
                    "action": "Replace",
                    "series": {
                        "0": {
                            "attributes": [latest["basis"], latest["method"],
                                           latest["coverage_pct"],
                                           latest["repro_hash"]],
                            "observations": {
                                str(i): [round(r["value"], 4)]
                                for i, r in enumerate(rows)
                            },
                        }
                    },
                }],
            },
        }
    finally:
        conn.close()
