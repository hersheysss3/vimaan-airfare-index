"""
Multilateral index estimators, and the window mechanics around them.

Why this module exists at all: airline fares bounce between fare buckets many
times a day. A chained bilateral index over that behaviour does not return to
its starting value when prices do — it ratchets. That is chain drift, and it is
the single largest source of error in a naive web-scraped price index.

GEKS makes the comparison transitive by construction, so the drift cancels.
Time-Product-Dummy reaches a similar place by regression and is kept as an
independent cross-check: agreement between two different estimators is
evidence, divergence is a flag.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np

from .elementary import jevons

Period = Mapping[str, float]


def geks_jevons(periods: Sequence[Period]) -> list[float]:
    """GEKS index over the whole supplied window, first period = 1.0.

    For each period t, take every possible route from 0 to t through an
    intermediate period l, and average them geometrically:

        P(0,t) = prod over l of [ P_J(0,l) * P_J(l,t) ] ^ (1/T)

    Because every pair is used and the formula is symmetric, the result is
    transitive: P(0,t) does not depend on the path taken to reach t.
    """
    n = len(periods)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    # bilateral Jevons between every ordered pair, once
    bil = np.full((n, n), np.nan)
    for i in range(n):
        bil[i, i] = 1.0
        for j in range(i + 1, n):
            try:
                v = jevons(periods[i], periods[j])
            except ValueError:
                continue
            bil[i, j] = v
            bil[j, i] = 1.0 / v

    out = []
    for t in range(n):
        logs = []
        for l in range(n):
            a, b = bil[0, l], bil[l, t]
            if np.isnan(a) or np.isnan(b) or a <= 0 or b <= 0:
                continue
            logs.append(math.log(a) + math.log(b))
        out.append(math.exp(sum(logs) / len(logs)) if logs else float("nan"))
    return out


def time_product_dummy(periods: Sequence[Period]) -> list[float]:
    """TPD index: the time dummies of ln(price) ~ time + item.

        ln p_it = mu_t + lambda_i + e_it

    Item fixed effects absorb quality, so mu_t is the pure price movement.
    Estimated by least squares on the pooled panel; returned as exp(mu_t)
    normalised to the first period.
    """
    n = len(periods)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    items = sorted({k for p in periods for k in p})
    item_ix = {k: i for i, k in enumerate(items)}

    rows, ys = [], []
    for t, period in enumerate(periods):
        for k, price in period.items():
            if price is None or price <= 0:
                continue
            # intercept + (n-1) time dummies + (m-1) item dummies
            row = np.zeros(1 + (n - 1) + (len(items) - 1))
            row[0] = 1.0
            if t > 0:
                row[t] = 1.0
            ix = item_ix[k]
            if ix > 0:
                row[(n - 1) + ix] = 1.0
            rows.append(row)
            ys.append(math.log(price))

    if not rows:
        return [float("nan")] * n

    X = np.asarray(rows)
    y = np.asarray(ys)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)

    out = [1.0]
    for t in range(1, n):
        out.append(math.exp(beta[t]))
    return out


def rolling_window(
    periods: Sequence[Period],
    window: int = 25,
    estimator=geks_jevons,
) -> list[float]:
    """Mean-splice a rolling window so published history is never revised.

    A multilateral index is only defined over the window it was computed on.
    When the window moves forward by one period we cannot simply recompute the
    whole series — that would restate figures already published. Instead we take
    the *movement* implied by the new window and link it onto the existing
    series. Mean splicing uses the geometric mean of every available link
    rather than a single one, which is less sensitive to the choice of anchor.
    """
    n = len(periods)
    if n == 0:
        return []
    if n <= window:
        return list(estimator(periods))

    series = list(estimator(periods[:window]))

    for end in range(window + 1, n + 1):
        chunk = estimator(periods[end - window : end])
        if any(math.isnan(v) for v in chunk[-2:]):
            series.append(series[-1])
            continue
        # every overlap position gives a candidate link; average them
        links = []
        for back in range(1, window):
            prev_pos = len(chunk) - 1 - back
            if prev_pos < 0:
                break
            anchor_ix = len(series) - back
            if anchor_ix < 0:
                break
            if chunk[prev_pos] <= 0:
                continue
            links.append(math.log(series[anchor_ix] * chunk[-1] / chunk[prev_pos]))
        series.append(math.exp(sum(links) / len(links)) if links else series[-1])

    return series


def drift(series: Sequence[float]) -> float:
    """How far an index has wandered from its own starting point, in points.

    Run this on a series built from prices with no true trend: anything other
    than roughly zero is drift the estimator invented.
    """
    if not series:
        return 0.0
    return (series[-1] / series[0] - 1.0) * 100.0
