"""
Load real DGCA passenger traffic into the database as index weights.

    python scripts/load_weights.py --year 2024 --top 500

This is the step that makes the aggregation real: after it runs, a route moves
the national figure by its actual share of Indian domestic passengers.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vimaan import db                                     # noqa: E402
from vimaan.data import dgca                              # noqa: E402
from vimaan.models import RouteWeight                     # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, default=2024,
                    help="base period; CPI 2024 uses calendar 2024")
    ap.add_argument("--top", type=int, default=500)
    ap.add_argument("--db", default=db.DEFAULT_PATH)
    ap.add_argument("--force", action="store_true", help="re-download the source")
    args = ap.parse_args()

    shares, pax = dgca.load_weights(year=args.year, top=args.top, force=args.force)
    weights = [
        RouteWeight(route=route, passengers=pax[route], share=share,
                    base_year=args.year, source=dgca.SOURCE_NAME)
        for route, share in shares.items()
    ]

    with db.session(args.db) as conn:
        n = db.put_weights(conn, weights)

    total = sum(pax.values())
    print(f"loaded {n} route weights for {args.year}")
    print(f"  passengers covered : {total:,.0f}")
    print(f"  source             : {dgca.SOURCE_NAME}")
    print(f"  licence            : {dgca.SOURCE_LICENCE}")
    print(f"  database           : {args.db}")
    top5 = sorted(shares.items(), key=lambda kv: -kv[1])[:5]
    print("\n  largest weights:")
    for route, share in top5:
        print(f"    {route:<30} {share*100:>6.2f}%   {pax[route]:>12,.0f} pax")


if __name__ == "__main__":
    main()
