"""
The shapes that move through the pipeline.

A FareObservation is deliberately fat: everything the hedonic model needs to
strip quality out of a price has to survive collection, or the adjustment
cannot be made later. Anything dropped here is lost for good.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Lane(str, Enum):
    """Where an observation came from, which determines how defensible it is."""
    STATUTORY = "A_statutory"     # Rule 135(2) published tariff pages
    LICENSED = "B_licensed"       # contracted distribution APIs
    PORTAL = "C_portal"           # public consumer sites, robots-respecting


class Cabin(str, Enum):
    ECONOMY = "economy"
    PREMIUM = "premium_economy"
    BUSINESS = "business"


# The advance-purchase windows the index samples. Fixed, because a moving
# definition of "booked early" would make the series incomparable over time.
LEAD_BUCKETS = (1, 3, 7, 14, 21, 30, 45, 60)


def lead_bucket(days: int) -> int:
    """Snap a raw lead time to its bucket. Below one day is treated as one."""
    if days <= LEAD_BUCKETS[0]:
        return LEAD_BUCKETS[0]
    for lo, hi in zip(LEAD_BUCKETS, LEAD_BUCKETS[1:]):
        if days <= hi:
            return hi if (hi - days) <= (days - lo) else lo
    return LEAD_BUCKETS[-1]


class FareObservation(BaseModel):
    """One price, for one flight, seen at one moment."""

    # provenance
    lane: Lane
    source: str = Field(description="collector id, e.g. amadeus or 6e_tariff")
    collected_at: datetime
    raw_sha256: Optional[str] = Field(
        default=None,
        description="hash of the raw payload this was parsed from, so the "
                    "figure can be re-derived years later",
    )
    collector_version: str = "0.1.0"

    # what was priced
    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    depart_date: date
    carrier: str
    flight_no: Optional[str] = None
    cabin: Cabin = Cabin.ECONOMY

    # the price
    price_total: float = Field(gt=0, description="all-inclusive, in INR")
    base_fare: Optional[float] = None
    taxes_fees: Optional[float] = None
    currency: str = "INR"

    # quality characteristics the hedonic model needs
    stops: int = 0
    duration_min: Optional[int] = None
    depart_slot: Optional[str] = None      # early_morning | morning | ...
    refundable: bool = False
    checked_bag_kg: Optional[int] = None
    seats_remaining: Optional[int] = None

    @field_validator("origin", "destination", "carrier")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @property
    def route(self) -> str:
        """Direction-independent route id, matching how DGCA reports traffic."""
        a, b = sorted((self.origin, self.destination))
        return f"{a}-{b}"

    @property
    def lead_days(self) -> int:
        return (self.depart_date - self.collected_at.date()).days

    @property
    def bucket(self) -> int:
        return lead_bucket(self.lead_days)

    @property
    def stratum(self) -> str:
        """Route x lead bucket x cabin: the level an elementary index runs at.

        Comparing a 60-day-out economy fare with a same-day business fare is
        not a price change, so they must never share a stratum.
        """
        return f"{self.route}|L{self.bucket}|{self.cabin.value}"

    @property
    def item_id(self) -> str:
        """Identity of the thing being priced, stable across collections.

        The matched-sample logic depends on this: if it is not stable, every
        period looks like an entirely new basket and the index is meaningless.
        """
        flight = self.flight_no or f"{self.carrier}-?"
        # the route belongs in the identity: the same flight number is reused
        # on different sectors, and without it two unrelated products collide
        return (f"{self.route}|{flight}|{self.depart_date.isoformat()}"
                f"|{self.cabin.value}")


class IndexPoint(BaseModel):
    """A published figure, with everything needed to defend it."""

    period: str = Field(description="YYYY-MM")
    value: float
    basis: str = "departure_month"
    method: str = "GEKS-Jevons"
    window: int = 25

    coverage_pct: float = Field(ge=0, le=100)
    imputed_pct: float = Field(ge=0, le=100)
    observation_count: int = 0
    route_count: int = 0

    revision: str = "none"
    provisional: bool = False
    repro_hash: Optional[str] = None

    def compute_hash(self, *parts: str) -> str:
        """Fingerprint tying this figure to the exact inputs that produced it."""
        h = hashlib.sha256()
        h.update(f"{self.period}|{self.method}|{self.window}|{self.basis}".encode())
        for p in parts:
            h.update(b"|")
            h.update(p.encode())
        return h.hexdigest()


class RouteWeight(BaseModel):
    """A route's share of base-period passengers, from DGCA."""
    route: str
    passengers: float
    share: float = Field(ge=0, le=1)
    base_year: int
    source: str = "DGCA monthly domestic city-pair traffic"
