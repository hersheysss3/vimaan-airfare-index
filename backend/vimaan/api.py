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
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db, pipeline
from .data import dgca

app = FastAPI(
    title="VIMAAN",
    version="0.1.0",
    description=(
        "Real-time airfare price index for India, for augmentation of the CPI. "
        "Smart India Hackathon 2026, problem SIH26056."
    ),
)

# the console is a static page served from elsewhere; let it read this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

DB_PATH = os.environ.get("VIMAAN_DB", db.DEFAULT_PATH)


def _conn():
    return db.connect(DB_PATH)


@app.get("/v1/health", tags=["meta"])
def health():
    conn = _conn()
    try:
        return {"status": "ok", "database": DB_PATH, **db.stats(conn)}
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
        return {
            "period": period,
            "value": row["value"],
            "formulae": {
                "elementary": "Jevons geometric mean, per route x lead bucket x cabin",
                "multilateral": f"{row['method']}, {row['window']}-month rolling "
                                f"window, mean spliced",
                "aggregation": "Lowe, weights = DGCA base-period passengers",
            },
            "basis": row["basis"],
            "quality": {
                "coverage_pct": row["coverage_pct"],
                "imputed_pct": row["imputed_pct"],
                "observation_count": row["observation_count"],
                "route_count": row["route_count"],
                "provisional": bool(row["provisional"]),
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
