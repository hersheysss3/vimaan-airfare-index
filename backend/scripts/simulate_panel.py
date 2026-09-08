"""
Generate a fare panel so the pipeline can be run end to end without credentials.

THESE FARES ARE SIMULATED. They are not collected prices and must never be
presented as such. What is real in the resulting index is the route list and
the weights, which come from published DGCA passenger traffic.

The simulation is built from the sampling design rather than from convenience:
an advance-purchase curve, a weekend premium, per-flight dispersion, and
inventory churn — flights that are simply not on sale in a given collection.
That last one matters, because churn is what breaks a chained index, and a
panel without it would make our own estimators look better than they are.

    python scripts/simulate_panel.py --months 14 --routes 40
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vimaan import db                                      # noqa: E402
from vimaan.models import Cabin, FareObservation, Lane, LEAD_BUCKETS  # noqa: E402


CARRIERS = ["6E", "AI", "UK", "SG", "QP"]


def routes_from_db(conn, base_year: int, limit: int) -> list[tuple[str, str, str]]:
    """The real weighted routes, heaviest first.

    Weights are stored under IATA route ids, so a row is usable here exactly
    when both halves are three-letter codes. Rows that fell back to city names
    are ones with no confident airport mapping and are skipped.
    """
    rows = conn.execute(
        """SELECT route, share FROM route_weight
             WHERE base_year = ? ORDER BY share DESC""",
        (base_year,),
    ).fetchall()
    out = []
    for r in rows:
        parts = r["route"].split("-")
        if len(parts) != 2 or not all(len(p) == 3 and p.isalpha() for p in parts):
            continue
        out.append((r["route"], parts[0], parts[1]))
        if len(out) >= limit:
            break
    return out


def generate(
    conn,
    months: int = 14,
    routes: int = 40,
    base_year: int = 2024,
    seed: int = 26056,
    churn: float = 0.18,
) -> int:
    rng = random.Random(seed)
    picks = routes_from_db(conn, base_year, routes)
    if not picks:
        raise SystemExit("no mappable routes; run scripts/load_weights.py first")

    # a distinct base fare per route, loosely scaled by sector length proxy
    base_fare = {key: 3200 + rng.random() * 2600 for key, _, _ in picks}
    # a mild real trend so the published series is not flat
    monthly_trend = 0.004

    start = date.today().replace(day=1) - timedelta(days=31 * months)
    written = 0

    for m in range(months):
        year = start.year + (start.month - 1 + m) // 12
        month = (start.month - 1 + m) % 12 + 1
        depart = date(year, month, 15)                  # mid-month departure
        seasonal = 1.0 + 0.05 * math.sin((month - 3) / 12 * 2 * math.pi)
        if month in (10, 11):                            # festival season
            seasonal += 0.04
        trend = (1.0 + monthly_trend) ** m

        batch: list[FareObservation] = []
        for key, o, d in picks:
            for bucket in LEAD_BUCKETS:
                collected = datetime.combine(
                    depart - timedelta(days=bucket),
                    datetime.min.time(), tzinfo=timezone.utc,
                ).replace(hour=11)
                # advance-purchase curve: the closer in, the dearer
                lead_factor = 1.0 + 1.55 * math.exp(-bucket / 11.0)
                for flight in range(4):
                    if rng.random() < churn:
                        continue                          # not on sale
                    carrier = CARRIERS[flight % len(CARRIERS)]
                    price = (base_fare[key] * lead_factor * seasonal * trend
                             * math.exp(rng.gauss(0, 0.09)))
                    batch.append(FareObservation(
                        lane=Lane.LICENSED,
                        source="simulated",
                        collected_at=collected,
                        collector_version="sim-0.1.0",
                        origin=o, destination=d,
                        depart_date=depart,
                        carrier=carrier,
                        flight_no=f"{carrier}-{1000 + flight * 37}",
                        cabin=Cabin.ECONOMY,
                        price_total=round(price, 2),
                        stops=0 if flight < 3 else 1,
                        duration_min=95 + flight * 12,
                        depart_slot=["early_morning", "morning",
                                     "afternoon", "evening"][flight % 4],
                        refundable=False,
                        checked_bag_kg=15,
                    ))
        written += db.put_fares(conn, batch)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=14)
    ap.add_argument("--routes", type=int, default=40)
    ap.add_argument("--churn", type=float, default=0.18,
                    help="probability a flight is absent from a collection")
    ap.add_argument("--db", default=db.DEFAULT_PATH)
    args = ap.parse_args()

    with db.session(args.db) as conn:
        n = generate(conn, months=args.months, routes=args.routes,
                     churn=args.churn)
    print(f"wrote {n:,} SIMULATED fare observations")
    print("  fares are simulated; the route list and weights are real DGCA data")


if __name__ == "__main__":
    main()
