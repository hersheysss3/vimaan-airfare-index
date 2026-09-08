"""
Real DGCA city-pair passenger traffic, used to weight the index.

This is the one part of the pipeline that runs on genuinely published data
today. DGCA releases monthly domestic city-pair traffic; the figures here come
from a parsed mirror of those releases:

    https://github.com/Vonter/india-aviation-traffic   (ODbL)

Why it matters: the upper-level aggregation is only as honest as its weights.
A route index weighted by invented shares is a guess with arithmetic on top.
These are the actual passenger counts DGCA published, so Delhi-Mumbai moves the
national figure by its real share of flyers and not by a number we chose.

    python -m vimaan.data.dgca --year 2024 --top 40
"""
from __future__ import annotations

import argparse
import csv
import io
import os
from dataclasses import dataclass
from typing import Iterable

import httpx

SOURCE_URL = (
    "https://raw.githubusercontent.com/Vonter/india-aviation-traffic/"
    "main/aggregated/domestic/city.csv"
)
SOURCE_NAME = "DGCA monthly domestic city-pair traffic"
SOURCE_LICENCE = "ODbL, via github.com/Vonter/india-aviation-traffic"

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache")


@dataclass(frozen=True)
class RoutePax:
    """Passengers carried on one city pair over the aggregation period."""
    city_a: str
    city_b: str
    passengers: float

    @property
    def key(self) -> str:
        """Direction-independent route id, in IATA so it joins to fare data.

        Falls back to city names when a city has no confident IATA mapping, so
        the row is still counted in national totals even though it cannot be
        matched against collected fares.
        """
        from .airports import route_key
        mapped = route_key(self.city_a, self.city_b)
        if mapped:
            return mapped
        a, b = sorted((self.city_a, self.city_b))
        return f"{a}-{b}"


def fetch(force: bool = False, timeout: float = 90.0) -> str:
    """Download the traffic CSV, cached on disk so a rebuild is offline."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "dgca_city_pairs.csv")
    if os.path.exists(path) and not force:
        with io.open(path, encoding="utf-8") as fh:
            return fh.read()
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(SOURCE_URL)
        resp.raise_for_status()
        text = resp.text
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def parse(text: str, year: int | None = None, month: int | None = None) -> list[RoutePax]:
    """Sum both directions of travel into one figure per city pair.

    DGCA reports PaxToCity2 and PaxFromCity2 separately. An index on a route
    covers travel in both directions, so they are added.
    """
    totals: dict[tuple[str, str], float] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            y = int(row["Year"])
            m = int(row["Month"])
        except (TypeError, ValueError):
            continue
        if year is not None and y != year:
            continue
        if month is not None and m != month:
            continue
        a = (row.get("City1") or "").strip().upper()
        b = (row.get("City2") or "").strip().upper()
        if not a or not b or a == b:
            continue

        def num(field: str) -> float:
            raw = row.get(field)
            try:
                v = float(raw) if raw not in (None, "") else 0.0
            except ValueError:
                return 0.0
            return v if v > 0 else 0.0

        pax = num("PaxToCity2") + num("PaxFromCity2")
        if pax <= 0:
            continue
        key = tuple(sorted((a, b)))
        totals[key] = totals.get(key, 0.0) + pax

    return [RoutePax(a, b, p) for (a, b), p in totals.items()]


def top_routes(routes: Iterable[RoutePax], n: int = 500) -> list[RoutePax]:
    return sorted(routes, key=lambda r: r.passengers, reverse=True)[:n]


def weights(routes: Iterable[RoutePax]) -> dict[str, float]:
    """Passenger counts to shares summing to one, keyed by route id."""
    rs = list(routes)
    total = sum(r.passengers for r in rs)
    if total <= 0:
        raise ValueError("no passenger volume in the selected period")
    return {r.key: r.passengers / total for r in rs}


def load_weights(
    year: int = 2024,
    top: int = 500,
    force: bool = False,
) -> tuple[dict[str, float], dict[str, float]]:
    """Convenience: (weight shares, raw passenger counts) for the base period.

    The base period for CPI 2024 is the calendar year 2024, which is why that
    is the default here rather than the most recent year available.
    """
    routes = top_routes(parse(fetch(force=force), year=year), n=top)
    return weights(routes), {r.key: r.passengers for r in routes}


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--force", action="store_true", help="ignore the disk cache")
    args = ap.parse_args()

    w, pax = load_weights(year=args.year, top=args.top, force=args.force)
    total = sum(pax.values())
    print(f"{SOURCE_NAME} — {args.year}")
    print(f"routes: {len(w)}   passengers: {total:,.0f}")
    print(f"source: {SOURCE_LICENCE}\n")
    print(f"{'route':<34}{'passengers':>14}{'weight':>10}")
    for key in sorted(w, key=lambda k: -w[k]):
        print(f"{key:<34}{pax[key]:>14,.0f}{w[key]*100:>9.2f}%")


if __name__ == "__main__":
    _main()
