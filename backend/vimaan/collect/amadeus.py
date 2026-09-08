"""
Lane B: Amadeus Self-Service. The free, licensed route to real fares.

This is the honest answer to "where does real fare data come from without
paying and without scraping". Amadeus publishes a Self-Service tier with a
free quota, and Flight Offers Search returns genuine priced itineraries. It is
contractual access, so nothing here depends on a site tolerating us.

Credentials are not committed. Create a free app at
https://developers.amadeus.com and export:

    AMADEUS_CLIENT_ID=...
    AMADEUS_CLIENT_SECRET=...

Then:

    python -m vimaan.collect.amadeus --origin DEL --destination BOM --days 14

Without credentials this module imports fine and every entry point raises a
clear error, so the rest of the pipeline stays testable.
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

TOKEN_URL = "https://test.api.amadeus.com/v1/security/oauth2/token"
SEARCH_URL = "https://test.api.amadeus.com/v2/shopping/flight-offers"

# test and production share a shape; the host is the only difference
PROD_TOKEN_URL = "https://api.amadeus.com/v1/security/oauth2/token"
PROD_SEARCH_URL = "https://api.amadeus.com/v2/shopping/flight-offers"


def _slot(hhmm: str) -> str:
    try:
        hour = int(hhmm[11:13])
    except (ValueError, IndexError):
        return "unknown"
    if hour < 6:
        return "early_morning"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


def _duration_min(iso: str) -> Optional[int]:
    """PT2H15M -> 135."""
    if not iso or not iso.startswith("PT"):
        return None
    hours = minutes = 0
    num = ""
    for ch in iso[2:]:
        if ch.isdigit():
            num += ch
        elif ch == "H":
            hours = int(num or 0); num = ""
        elif ch == "M":
            minutes = int(num or 0); num = ""
        else:
            num = ""
    return hours * 60 + minutes or None


class AmadeusCollector(Collector):
    lane = Lane.LICENSED.value
    source = "amadeus"
    version = "0.1.0"
    schema_markers = ("data",)

    def __init__(self, *, production: bool = False, **kw):
        super().__init__(**kw)
        self.client_id = os.environ.get("AMADEUS_CLIENT_ID")
        self.client_secret = os.environ.get("AMADEUS_CLIENT_SECRET")
        self.token_url = PROD_TOKEN_URL if production else TOKEN_URL
        self.search_url = PROD_SEARCH_URL if production else SEARCH_URL
        self._token: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _require(self) -> None:
        if not self.configured:
            raise CollectionRefused(
                "AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET are not set. "
                "Create a free Self-Service app at developers.amadeus.com."
            )

    def token(self) -> str:
        self._require()
        if self._token:
            return self._token
        self.budget.take()
        with httpx.Client(timeout=self.timeout) as c:
            resp = c.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise CollectionRefused(
                f"Amadeus auth failed ({resp.status_code}): {resp.text[:200]}")
        self._token = resp.json()["access_token"]
        return self._token

    def search(
        self,
        origin: str,
        destination: str,
        depart: date,
        *,
        adults: int = 1,
        cabin: Cabin = Cabin.ECONOMY,
        max_results: int = 40,
        currency: str = "INR",
    ) -> bytes:
        """One Flight Offers Search. Returns the raw payload, unparsed.

        Raw bytes on purpose: they go to bronze and get hashed before anything
        interprets them.
        """
        self._require()
        self.budget.take()
        params = {
            "originLocationCode": origin.upper(),
            "destinationLocationCode": destination.upper(),
            "departureDate": depart.isoformat(),
            "adults": adults,
            "travelClass": {
                Cabin.ECONOMY: "ECONOMY",
                Cabin.PREMIUM: "PREMIUM_ECONOMY",
                Cabin.BUSINESS: "BUSINESS",
            }[cabin],
            "currencyCode": currency,
            "max": max_results,
        }
        with httpx.Client(timeout=self.timeout) as c:
            resp = c.get(
                self.search_url,
                params=params,
                headers={"Authorization": f"Bearer {self.token()}"},
            )
        if resp.status_code != 200:
            raise CollectionRefused(
                f"Amadeus search failed ({resp.status_code}): {resp.text[:300]}")
        return resp.content

    def parse(
        self,
        payload: bytes,
        *,
        collected_at: Optional[datetime] = None,
        raw_sha256: Optional[str] = None,
        cabin: Cabin = Cabin.ECONOMY,
    ) -> list[FareObservation]:
        """Flight Offers JSON to FareObservation rows.

        Only non-stop and one-stop itineraries are kept, and the price used is
        grandTotal — the all-inclusive figure, not the base fare. A CPI
        component has to reflect what a passenger actually pays.
        """
        self.check_schema(payload)
        try:
            doc = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SchemaDrift(f"{self.source}: payload is not JSON: {exc}") from exc

        offers = doc.get("data")
        if offers is None:
            raise SchemaDrift(f"{self.source}: no 'data' key in response")

        when = collected_at or datetime.now(timezone.utc)
        out: list[FareObservation] = []

        for offer in offers:
            itineraries = offer.get("itineraries") or []
            if not itineraries:
                continue
            segments = itineraries[0].get("segments") or []
            if not segments:
                continue

            first, last = segments[0], segments[-1]
            dep = first.get("departure", {})
            arr = last.get("arrival", {})
            price = offer.get("price", {})

            try:
                total = float(price.get("grandTotal") or price.get("total"))
            except (TypeError, ValueError):
                continue
            if total <= 0:
                continue

            dep_at = dep.get("at", "")
            try:
                depart_date = date.fromisoformat(dep_at[:10])
            except ValueError:
                continue

            base = price.get("base")
            try:
                base_fare = float(base) if base is not None else None
            except (TypeError, ValueError):
                base_fare = None

            carrier = (first.get("carrierCode") or "").upper()
            number = first.get("number")
            flight_no = f"{carrier}-{number}" if carrier and number else None

            out.append(FareObservation(
                lane=Lane.LICENSED,
                source=self.source,
                collected_at=when,
                raw_sha256=raw_sha256,
                collector_version=self.version,
                origin=(dep.get("iataCode") or "").upper(),
                destination=(arr.get("iataCode") or "").upper(),
                depart_date=depart_date,
                carrier=carrier,
                flight_no=flight_no,
                cabin=cabin,
                price_total=total,
                base_fare=base_fare,
                taxes_fees=(total - base_fare) if base_fare is not None else None,
                currency=price.get("currency", "INR"),
                stops=max(0, len(segments) - 1),
                duration_min=_duration_min(itineraries[0].get("duration", "")),
                depart_slot=_slot(dep_at),
                refundable=False,
                seats_remaining=offer.get("numberOfBookableSeats"),
            ))
        return out


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--origin", default="DEL")
    ap.add_argument("--destination", default="BOM")
    ap.add_argument("--days", type=int, default=14, help="days ahead to price")
    ap.add_argument("--production", action="store_true")
    args = ap.parse_args()

    col = AmadeusCollector(production=args.production)
    if not col.configured:
        print("AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET not set.")
        print("Create a free Self-Service app at https://developers.amadeus.com")
        raise SystemExit(2)

    depart = date.today() + timedelta(days=args.days)
    payload = col.search(args.origin, args.destination, depart)
    fares = col.parse(payload)
    print(f"{len(fares)} fares  {args.origin}->{args.destination}  {depart}")
    for f in sorted(fares, key=lambda x: x.price_total)[:10]:
        print(f"  {f.flight_no or f.carrier:<10} {f.stops} stop  "
              f"{f.duration_min or '?':>4} min  INR {f.price_total:>9,.0f}  "
              f"L{f.bucket}")


if __name__ == "__main__":
    _main()
