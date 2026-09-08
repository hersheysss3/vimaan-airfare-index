"""
Elementary aggregate formulae.

These operate on a *stratum* — one route, one lead-time bucket, one cabin —
where the items being compared are individual flights. Everything above this
level is handled in aggregate.py.

The CPI Manual (ILO/IMF/OECD/Eurostat/UNECE/World Bank, 2020) prescribes a
geometric mean at this level. Carli and Dutot are implemented so the bias can
be demonstrated rather than asserted, not because we publish them.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence


def _matched(p0: Mapping[str, float], pt: Mapping[str, float]) -> list[tuple[float, float]]:
    """Only items priced in both periods can enter an elementary aggregate.

    An unmatched item is a new or departed product, not a price change; letting
    one in is how a sample-rotation artefact gets published as inflation.
    """
    pairs = []
    for k, base in p0.items():
        cur = pt.get(k)
        if cur is None:
            continue
        if base is None or base <= 0 or cur <= 0:
            continue
        pairs.append((float(base), float(cur)))
    return pairs


def jevons(p0: Mapping[str, float], pt: Mapping[str, float]) -> float:
    """Geometric mean of price relatives. The published elementary formula.

    Computed as exp(mean(log ratio)) rather than as a product of ratios, which
    would overflow on a stratum of any size.
    """
    pairs = _matched(p0, pt)
    if not pairs:
        raise ValueError("no matched items between the two periods")
    total = sum(math.log(cur / base) for base, cur in pairs)
    return math.exp(total / len(pairs))


def dutot(p0: Mapping[str, float], pt: Mapping[str, float]) -> float:
    """Ratio of arithmetic mean prices. Sensitive to the unit of quotation."""
    pairs = _matched(p0, pt)
    if not pairs:
        raise ValueError("no matched items between the two periods")
    base_sum = sum(b for b, _ in pairs)
    cur_sum = sum(c for _, c in pairs)
    return cur_sum / base_sum


def carli(p0: Mapping[str, float], pt: Mapping[str, float]) -> float:
    """Arithmetic mean of price relatives. Upward-biased; never published.

    Kept so the bias against Jevons can be shown on real dispersion.
    """
    pairs = _matched(p0, pt)
    if not pairs:
        raise ValueError("no matched items between the two periods")
    return sum(cur / base for base, cur in pairs) / len(pairs)


def jevons_from_series(prices: Sequence[Mapping[str, float]]) -> list[float]:
    """Fixed-base Jevons index for a sequence of periods, first period = 1.0."""
    if not prices:
        return []
    base = prices[0]
    out = []
    for period in prices:
        try:
            out.append(jevons(base, period))
        except ValueError:
            out.append(float("nan"))
    return out


def chained(prices: Sequence[Mapping[str, float]]) -> list[float]:
    """Period-on-period Jevons, multiplied together.

    This is the naive construction. It is here to be measured against, because
    on dynamic airline pricing it drifts: see multilateral.geks_jevons and the
    drift test in tests/test_drift.py.
    """
    if not prices:
        return []
    out = [1.0]
    for prev, cur in zip(prices, prices[1:]):
        try:
            link = jevons(prev, cur)
        except ValueError:
            link = 1.0
        out.append(out[-1] * link)
    return out
