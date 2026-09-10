"""
Run one collection pass against the live Travelpayouts Data API.

    export TRAVELPAYOUTS_TOKEN=...
    python scripts/collect.py --routes 20            # a full pass
    python scripts/collect.py --routes 2 --dry-run   # cheap smoke test

THE FARES THIS WRITES ARE REAL, and they carry the source's limitation with
them: the Data API serves a cache built from what users searched for, held up
to seven days. Rows land in silver tagged `travelpayouts` so they can always
be separated from the simulated panel, which is tagged `simulated`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vimaan import db                                   # noqa: E402
from vimaan.collect.run import collect_once, weighted_routes  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--routes", type=int, default=20,
                    help="how many of the heaviest routes to collect")
    ap.add_argument("--buckets", type=str, default="",
                    help="comma-separated lead days, default all eight")
    ap.add_argument("--max-requests", type=int, default=400)
    ap.add_argument("--db", default=db.DEFAULT_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be fetched, fetch nothing")
    args = ap.parse_args()

    buckets = None
    if args.buckets:
        buckets = [int(b) for b in args.buckets.split(",") if b.strip()]

    with db.session(args.db) as conn:
        if args.dry_run:
            picks = weighted_routes(conn, limit=args.routes)
            from vimaan.models import LEAD_BUCKETS
            bs = buckets or list(LEAD_BUCKETS)
            print(f"would fetch {len(picks)} routes x {len(bs)} windows = "
                  f"{len(picks) * len(bs)} requests")
            for route, o, d in picks:
                print(f"  {route:<9} {o}->{d}")
            return

        kwargs = {"limit_routes": args.routes, "max_requests": args.max_requests}
        if buckets:
            kwargs["buckets"] = buckets
        report = collect_once(conn, **kwargs)

    print(json.dumps(report.summary(), indent=2))
    if report.fares_written:
        print(f"\n{report.fares_written:,} REAL fares written "
              f"(hit rate {report.hit_rate:.0f}%). Run the pipeline to publish:")
        print("  python -c \"from vimaan import db,pipeline; "
              "c=db.connect(); print(pipeline.run_all(c)); c.commit()\"")
    else:
        print("\nnothing written. With a cache-backed source an empty pass is a "
              "real outcome, not necessarily a fault — check hit_rate above.")


if __name__ == "__main__":
    main()
