"""
The collection run: fetch, snapshot, parse, store.

This is the loop that turns an adapter into a panel. It is deliberately the
only place that writes collected fares, so the guarantees hold everywhere:

  * every response is hashed into bronze *before* anything parses it, so a
    published figure can be re-derived from bytes rather than from trust
  * a parse failure is recorded against the snapshot, not swallowed
  * the request budget is shared across the whole run, so a run cannot walk
    off and hammer a host because one route had many dates
  * an empty response is counted, not discarded — with a cache-backed source
    the *absence* of a price is data about coverage, and hiding it would
    quietly overstate how much of the panel was really collected

Designed to be run three times a day. It is idempotent in the sense that
matters: silver rows are unique on (item, collected_at, source), and bronze
is keyed by payload hash, so re-running an identical fetch adds nothing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence

from ..db import put_fares, put_snapshot
from ..models import LEAD_BUCKETS, Lane
from .base import CollectionRefused, SchemaDrift
from .travelpayouts import TravelpayoutsCollector


@dataclass
class CollectionReport:
    """What a run actually did. Published, not just logged."""

    started_at: str
    finished_at: str = ""
    source: str = "travelpayouts"
    lane: str = Lane.LICENSED.value
    routes_attempted: int = 0
    requests_made: int = 0
    responses_empty: int = 0
    snapshots_stored: int = 0
    fares_parsed: int = 0
    fares_written: int = 0
    errors: list[str] = field(default_factory=list)
    #: route -> how many fares that route contributed this run
    by_route: dict[str, int] = field(default_factory=dict)

    @property
    def hit_rate(self) -> float:
        """Share of requests that returned at least one fare."""
        if not self.requests_made:
            return 0.0
        return (self.requests_made - self.responses_empty) / self.requests_made * 100.0

    def summary(self) -> dict:
        return {
            "source": self.source,
            "lane": self.lane,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "routes_attempted": self.routes_attempted,
            "requests_made": self.requests_made,
            "responses_empty": self.responses_empty,
            "hit_rate_pct": round(self.hit_rate, 1),
            "snapshots_stored": self.snapshots_stored,
            "fares_parsed": self.fares_parsed,
            "fares_written": self.fares_written,
            "errors": self.errors[:20],
            "error_count": len(self.errors),
        }


def weighted_routes(conn, base_year: int = 2024, limit: int = 20) -> list[tuple[str, str, str]]:
    """The heaviest routes that carry a usable IATA pair, heaviest first.

    Collection follows the weights: a run that can only afford a subset of
    routes should spend its budget where the passengers are, because that is
    where coverage is measured.
    """
    rows = conn.execute(
        """SELECT route FROM route_weight
             WHERE base_year = ? ORDER BY share DESC""",
        (base_year,),
    ).fetchall()
    out: list[tuple[str, str, str]] = []
    for r in rows:
        parts = r["route"].split("-")
        if len(parts) != 2 or not all(len(p) == 3 and p.isalpha() for p in parts):
            continue
        out.append((r["route"], parts[0], parts[1]))
        if len(out) >= limit:
            break
    return out


def collect_once(
    conn,
    *,
    routes: Optional[Sequence[tuple[str, str, str]]] = None,
    buckets: Sequence[int] = LEAD_BUCKETS,
    limit_routes: int = 20,
    base_year: int = 2024,
    token: Optional[str] = None,
    max_requests: int = 400,
    min_interval_s: float = 1.1,
) -> CollectionReport:
    """One full pass: every route x every advance-purchase window.

    The lead buckets are queried as literal departure dates today + bucket,
    which is what makes the collected panel line up with the index's strata
    instead of needing to be bucketed after the fact.
    """
    from .base import RateBudget

    report = CollectionReport(
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    collector = TravelpayoutsCollector(
        token=token or os.environ.get("TRAVELPAYOUTS_TOKEN"),
        budget=RateBudget(max_requests=max_requests, min_interval_s=min_interval_s),
    )
    if not collector.configured:
        report.errors.append(
            "TRAVELPAYOUTS_TOKEN is not set; nothing was collected")
        report.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return report

    if routes is None:
        routes = weighted_routes(conn, base_year=base_year, limit=limit_routes)
    report.routes_attempted = len(routes)

    today = date.today()

    for route, origin, destination in routes:
        for bucket in buckets:
            depart = today + timedelta(days=int(bucket))
            try:
                payload = collector.search(origin, destination, depart)
                report.requests_made += 1
            except CollectionRefused as exc:
                report.errors.append(f"{route} L{bucket}: {exc}")
                # a refusal is terminal for the run: budget exhausted or a
                # rejected token will not fix itself on the next route
                report.finished_at = datetime.now(timezone.utc).isoformat(
                    timespec="seconds")
                return report
            except Exception as exc:                       # network, timeout
                report.errors.append(f"{route} L{bucket}: {type(exc).__name__}: {exc}")
                continue

            snapshot_id, digest = put_snapshot(
                conn,
                lane=collector.lane,
                source=collector.source,
                payload=payload,
                url=f"prices_for_dates?{origin}-{destination}@{depart.isoformat()}",
                content_type="application/json",
                collector_version=collector.version,
            )
            report.snapshots_stored += 1

            try:
                fares = collector.parse(payload, raw_sha256=digest)
            except SchemaDrift as exc:
                # loud, and the bytes are already in bronze to diagnose against
                report.errors.append(f"{route} L{bucket}: schema drift: {exc}")
                continue

            if not fares:
                report.responses_empty += 1
                continue

            report.fares_parsed += len(fares)
            written = put_fares(conn, fares, snapshot_id=snapshot_id)
            report.fares_written += written
            report.by_route[route] = report.by_route.get(route, 0) + written

    report.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return report
