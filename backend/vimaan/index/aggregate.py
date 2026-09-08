"""
Upper-level aggregation: many route indices into one national number.

Weights are DGCA base-period passengers, so a route moves the national figure
in proportion to how many people actually fly it. Delhi-Mumbai is not one vote
among 517.

Lowe is what the published series uses. Laspeyres is computed alongside it for
comparability with the existing manual method, and the gap between them is
reported rather than hidden.
"""
from __future__ import annotations

from typing import Mapping


def _check(indices: Mapping[str, float], weights: Mapping[str, float]) -> list[tuple[float, float]]:
    pairs = []
    for key, idx in indices.items():
        w = weights.get(key)
        if w is None or w <= 0:
            continue
        if idx is None or idx != idx:  # NaN
            continue
        pairs.append((float(idx), float(w)))
    if not pairs:
        raise ValueError("no route has both an index and a positive weight")
    return pairs


def lowe(indices: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Weighted arithmetic mean of route indices, base-period quantity weights.

        Index(t) = sum_i w_i * I_i(t) / sum_i w_i

    The route indices are already expressed on their own base (100 = base
    period), so this returns a figure on that same scale. Rescaling here would
    multiply the base in twice.
    """
    pairs = _check(indices, weights)
    num = sum(idx * w for idx, w in pairs)
    den = sum(w for _, w in pairs)
    return num / den


# Young and Lowe coincide when the weights are already expenditure shares held
# fixed at the base period, which is how DGCA passenger weights are applied
# here. Kept as a distinct name so call sites read honestly.
young = lowe


def laspeyres(
    base_prices: Mapping[str, float],
    cur_prices: Mapping[str, float],
    quantities: Mapping[str, float],
    base: float = 100.0,
) -> float:
    """Classic fixed-basket Laspeyres, for comparison with the manual series."""
    num = den = 0.0
    for key, q in quantities.items():
        p0, pt = base_prices.get(key), cur_prices.get(key)
        if not p0 or not pt or q <= 0:
            continue
        num += pt * q
        den += p0 * q
    if den == 0:
        raise ValueError("base-period basket has no value")
    return num / den * base


def contributions(
    indices: Mapping[str, float],
    weights: Mapping[str, float],
    base: float = 100.0,
) -> dict[str, float]:
    """How many index points each route contributed to the level.

    This is the chart an economist actually asks for: not which route is most
    expensive, but which route moved the number.
    """
    pairs = {k: (indices[k], weights[k]) for k in indices if weights.get(k, 0) > 0}
    total_w = sum(w for _, w in pairs.values())
    if total_w == 0:
        raise ValueError("weights sum to zero")
    # each route's deviation from base, scaled by its share of passengers.
    # These sum to (aggregate - base), which the tests assert.
    return {k: (idx - base) * (w / total_w) for k, (idx, w) in pairs.items()}


def normalise_weights(raw: Mapping[str, float]) -> dict[str, float]:
    """Passenger counts to shares summing to one."""
    total = sum(v for v in raw.values() if v and v > 0)
    if total <= 0:
        raise ValueError("no positive weights")
    return {k: v / total for k, v in raw.items() if v and v > 0}
