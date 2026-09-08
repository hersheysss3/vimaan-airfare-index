# VIMAAN backend

The index engine, the data layer and the API behind
[VIMAAN](https://vimaan-console.vercel.app).

```bash
pip install -r requirements.txt

python scripts/load_weights.py --year 2024 --top 500   # real DGCA weights
python scripts/simulate_panel.py --months 14 --routes 40
python -c "from vimaan import db,pipeline; \
           conn=db.connect(); print(pipeline.run_all(conn)); conn.commit()"

uvicorn vimaan.api:app --reload      # http://127.0.0.1:8000/docs
pytest -q                            # 56 tests
```

## What is real, and what is not

Being precise about this matters more than the demo looking impressive.

| | Status |
|---|---|
| **Route weights** | **Real.** Published DGCA monthly domestic city-pair traffic — 2024 calendar year, 500 routes, 146.3 million passengers. |
| **Route list** | **Real.** Derived from the same DGCA release, heaviest first. |
| **Index estimators** | **Real.** Jevons, GEKS-Jevons, TPD, mean splicing, Lowe. 56 tests assert their properties. |
| **CPI structure** | **Real.** Base 2024 = 100, transport group weight 9.43% (MoSPI). |
| **Fares** | **Simulated.** `scripts/simulate_panel.py`, clearly labelled everywhere it appears. |

So the aggregation is honest — Delhi–Mumbai moves the national figure by its
actual 4.29% share of Indian domestic passengers, not by a number we chose.
The prices being aggregated are not yet collected.

### Getting real fares

Live collection needs credentials, not more code. The collector is written and
waiting:

```bash
# free Self-Service tier at https://developers.amadeus.com
export AMADEUS_CLIENT_ID=...
export AMADEUS_CLIENT_SECRET=...
python -m vimaan.collect.amadeus --origin DEL --destination BOM --days 14
```

Amadeus is Lane B in the design: contractual access, so nothing depends on a
site tolerating us. Lanes A (Rule 135(2) tariff pages) and C (public portals)
use the same `Collector` base, which enforces robots.txt, a per-host request
budget, content-addressed snapshots and schema-drift detection.

Indian airline hosts were unreachable from the machine this was built on, so
Lane A and C adapters are not written against live pages yet. That is a
deliberate gap, not an oversight — writing a parser against a page you cannot
fetch produces fiction.

## Layout

```
vimaan/
├── index/
│   ├── elementary.py    Jevons, Dutot, Carli
│   ├── multilateral.py  GEKS-Jevons, TPD, rolling window, mean splicing
│   └── aggregate.py     Lowe/Young, Laspeyres, route contributions
├── data/
│   ├── dgca.py          real DGCA city-pair traffic -> index weights
│   └── airports.py      city name -> IATA, in one place
├── collect/
│   ├── base.py          robots gate, rate budget, snapshots, drift detection
│   └── amadeus.py       Lane B adapter (free tier)
├── models.py            FareObservation, IndexPoint, RouteWeight
├── db.py                SQLite medallion: bronze / silver / gold
├── pipeline.py          silver -> gold -> published figure
└── api.py               FastAPI + SDMX-JSON
```

SQLite rather than Postgres + TimescaleDB so the whole thing runs from a clone
with no server to install. The schema is written so that move is a change of
driver, not a redesign.

## Endpoints

| | |
|---|---|
| `GET /v1/index/national` | headline figure with its provenance |
| `GET /v1/index/national/series` | full series |
| `GET /v1/index/route/{route}` | one route, e.g. `BOM-DEL` |
| `GET /v1/contributions?period=` | which routes moved the figure |
| `GET /v1/surface?route=` | the price surface cube |
| `GET /v1/weights` | the DGCA weights actually in use |
| `GET /v1/methodology/{period}` | how that figure was produced |
| `GET /v1/sdmx/CPI_AIRFARE` | SDMX-JSON for MoSPI ingestion |

Every index response carries coverage, imputation share, observation count,
revision state and a reproducibility hash. A figure below 70% weighted
coverage is flagged `provisional` automatically.

## On chain drift

The test suite pins down something the pitch states loosely. Chain drift is
**not** caused by price volatility: on a fixed matched sample a chained Jevons
index returns exactly to its starting value when prices do, and
`tests/test_drift.py` asserts it.

Drift appears when the **matched sample churns** — a flight sells out, a route
is not scheduled, a portal omits a carrier. On prices that return exactly to
base:

| Sample churn | Chained | GEKS-Jevons |
|---|---|---|
| 0% | +0.00 | +0.00 |
| 15% | −61.26 | +0.00 |
| 30% | −36.92 | +0.00 |
| 45% | +546.62 | +0.00 |

Airline inventory churns constantly, which is the actual argument for a
multilateral estimator.

## Attribution

DGCA traffic data via [Vonter/india-aviation-traffic](https://github.com/Vonter/india-aviation-traffic),
ODbL. Sourced from DGCA monthly domestic city-pair releases.
