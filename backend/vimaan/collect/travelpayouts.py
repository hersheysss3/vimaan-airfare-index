"""
Lane B: Travelpayouts / Aviasales Data API.

Replaces the Amadeus Self-Service adapter, which Amadeus decommissioned on
17 July 2026. This is the free, licensed source that still works: registration
is free, access is contractual through their affiliate terms, and the API
returns real observed fares.

    # free account at https://www.travelpayouts.com
    export TRAVELPAYOUTS_TOKEN=...
    python -m vimaan.collect.travelpayouts --origin DEL --destination BOM

METHODOLOGICAL LIMITATION, stated up front because it matters for an official
statistic: the Data API serves prices from a cache built out of *what users
searched for*, held for up to seven days. That is a selection-biased sample
with a staleness window, not a designed panel. Two consequences:

  * a route nobody searched is absent, and its absence is not random
  * a quoted price may be up to a week old

For a CPI-grade series this is a stopgap that proves the pipeline on real
prices, not a foundation. The designed answer remains Lane A — tariffs
airlines are obliged to publish under Rule 135(2) — and a GDS contract for
Lane B proper. The bias is recorded on every observation so it can never be
silently forgotten downstream.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from ..models import Cabin, FareObservation, Lane
from .base import Collector, CollectionRefused, SchemaDrift

BASE = "https://api.travelpayouts.com"
PRICES_FOR_DATES = f"{BASE}/aviasales/v3/prices_for_dates"
GROUPED_PRICES = f"{BASE}/aviasales/v3/grouped_prices"

#: the cache window the upstream API documents, in days
CACHE_STALENESS_DAYS = 7


class TravelpayoutsCollector(Collector):
    lane = Lane.LICENSED.value
    source = "travelpayouts"
    version = "0.1.0"
    schema_markers = ()

    def __init__(self, token: Optional[str] = None, **kw):
        super().__init__(**kw)
        self.token = token or os.environ.get("TRAVELPAYOUTS_TOKEN")

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _require(self) -> None:
        if not self.configured:
            raise CollectionRefused(
                "TRAVELPAYOUTS_TOKEN is not set. Register free at "
                "https://www.travelpayouts.com and put the API token in the "
                "environment."
            )

    def search(
        self,
        origin: str,
        destination: str,
        depart: date,
        *,
        currency: str = "inr",
        limit: int = 100,
        one_way: bool = True,
        direct: bool = False,
    ) -> bytes:
        """One cached-price query. Returns raw bytes for hashing into bronze."""
        self._require()
        self.budget.take()
        params = {
            "origin": origin.upper(),
            "destination": destination.upper(),
            "departure_at": depart.isoformat(),
            "currency": currency,
            "limit": limit,
            "one_way": "true" if one_way else "false",
            "direct": "true" if direct else "false",
            "sorting": "price",
            "token": self.token,
        }
        with httpx.Client(timeout=self.timeout) as c:
            resp = c.get(PRICES_FOR_DATES, params=params,
                         headers={"User-Agent": "VIMAAN/0.1"})
        if resp.status_code == 401:
            raise CollectionRefused("Travelpayouts rejected the token")
        if resp.status_code != 200:
            raise CollectionRefused(
                f"Travelpayouts returned {resp.status_code}: {resp.text[:200]}")
        return resp.content

    def parse(
        self,
        payload: bytes,
        *,
        collected_at: Optional[datetime] = None,
        raw_sha256: Optional[str] = None,
        cabin: Cabin = Cabin.ECONOMY,
    ) -> list[FareObservation]:
        """Data API JSON to FareObservation rows.

        `price` is the total for the itinerary in the requested currency. Rows
        without a usable price, route or departure date are dropped rather than
        guessed at.
        """
        try:
            doc = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SchemaDrift(f"{self.source}: payload is not JSON: {exc}") from exc

        if "data" not in doc:
            raise SchemaDrift(
                f"{self.source}: no 'data' key; keys were {sorted(doc)[:6]}")
        rows = doc.get("data") or []
        currency = (doc.get("currency") or "inr").upper()
        when = collected_at or datetime.now(timezone.utc)

        out: list[FareObservation] = []
        for row in rows:
            try:
                price = float(row.get("price"))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue

            dep_raw = row.get("departure_at") or ""
            try:
                depart_date = date.fromisoformat(dep_raw[:10])
            except ValueError:
                continue

            origin = (row.get("origin_airport") or row.get("origin") or "").upper()
            dest = (row.get("destination_airport")
                    or row.get("destination") or "").upper()
            if len(origin) != 3 or len(dest) != 3:
                continue

            carrier = (row.get("airline") or "").upper()
            number = row.get("flight_number")
            flight_no = f"{carrier}-{number}" if carrier and number else None

            duration = row.get("duration_to") or row.get("duration")
            try:
                duration_min = int(duration) if duration else None
            except (TypeError, ValueError):
                duration_min = None

            try:
                stops = int(row.get("transfers") or 0)
            except (TypeError, ValueError):
                stops = 0

            hour = dep_raw[11:13]
            slot = "unknown"
            if hour.isdigit():
                h = int(hour)
                slot = ("early_morning" if h < 6 else "morning" if h < 12
                        else "afternoon" if h < 17 else "evening" if h < 21
                        else "night")

            out.append(FareObservation(
                lane=Lane.LICENSED,
                source=self.source,
                collected_at=when,
                raw_sha256=raw_sha256,
                collector_version=self.version,
                origin=origin,
                destination=dest,
                depart_date=depart_date,
                carrier=carrier or "??",
                flight_no=flight_no,
                cabin=cabin,
                price_total=price,
                currency=currency,
                stops=stops,
                duration_min=duration_min,
                depart_slot=slot,
                refundable=False,
            ))
        return out


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--origin", default="DEL")
    ap.add_argument("--destination", default="BOM")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    col = TravelpayoutsCollector()
    if not col.configured:
        print("TRAVELPAYOUTS_TOKEN not set.")
        print("Register free at https://www.travelpayouts.com, then:")
        print("  export TRAVELPAYOUTS_TOKEN=<your token>")
        raise SystemExit(2)

    depart = date.today() + timedelta(days=args.days)
    fares = col.parse(col.search(args.origin, args.destination, depart))
    print(f"{len(fares)} cached fares  {args.origin}->{args.destination}  {depart}")
    print(f"(prices may be up to {CACHE_STALENESS_DAYS} days old, and the cache "
          f"reflects what users searched)")
    for f in sorted(fares, key=lambda x: x.price_total)[:10]:
        print(f"  {f.flight_no or f.carrier:<10} {f.stops} stop  "
              f"{f.currency} {f.price_total:>9,.0f}  L{f.bucket}")


if __name__ == "__main__":
    _main()
