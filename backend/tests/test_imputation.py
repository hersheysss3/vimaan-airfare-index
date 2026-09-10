"""
Imputation, and the accounting around it.

Two things must hold. The filled value has to follow comparable cells rather
than asserting no change — that is the whole difference between imputing a
movement and carrying a price forward. And every filled cell has to stay
distinguishable from an observed one, because `imputed_pct` is published and
a figure that cannot say how much of itself was inferred is not auditable.
"""
from __future__ import annotations

import pytest

from vimaan.index.imputation import (MAX_CONSECUTIVE_IMPUTATIONS, Cell,
                                     impute)


def _grid(periods, routes, buckets=(7, 14), cabin="economy", price=5000.0):
    return {Cell(p, r, b, cabin): price
            for p in periods for r in routes for b in buckets}


def test_a_complete_surface_needs_no_imputation():
    observed = _grid(["2025-01", "2025-02"], ["DEL-BOM", "BLR-DEL"])
    result = impute(observed)
    assert result.imputed == {}
    assert result.share_for_period("2025-01") == 0.0


def test_a_hole_is_filled_from_the_movement_of_the_same_route():
    periods = ["2025-01", "2025-02"]
    observed = _grid(periods, ["DEL-BOM"], buckets=(7, 14, 21))
    # everything on this route rose 10% except L14, which was not collected
    for b in (7, 21):
        observed[Cell("2025-02", "DEL-BOM", b, "economy")] = 5500.0
    del observed[Cell("2025-02", "DEL-BOM", 14, "economy")]

    result = impute(observed)
    filled = result.imputed[Cell("2025-02", "DEL-BOM", 14, "economy")]

    assert filled.basis == "same_route"
    assert filled.value == pytest.approx(5500.0)      # 5000 * 1.10
    assert filled.age == 1


def test_it_imputes_a_movement_not_a_carry_forward():
    periods = ["2025-01", "2025-02"]
    observed = _grid(periods, ["DEL-BOM"], buckets=(7, 14, 21))
    for b in (7, 21):
        observed[Cell("2025-02", "DEL-BOM", b, "economy")] = 6000.0
    del observed[Cell("2025-02", "DEL-BOM", 14, "economy")]

    filled = impute(observed).imputed[Cell("2025-02", "DEL-BOM", 14, "economy")]
    # a carry-forward would leave it at 5000; the movement says 6000
    assert filled.value != pytest.approx(5000.0)
    assert filled.value == pytest.approx(6000.0)


def test_it_falls_back_to_wider_donor_classes():
    periods = ["2025-01", "2025-02"]
    # the target route has only one bucket, so there is no same-route donor
    observed = {
        Cell("2025-01", "DEL-BOM", 7, "economy"): 5000.0,
        Cell("2025-01", "BLR-DEL", 7, "economy"): 4000.0,
        Cell("2025-02", "BLR-DEL", 7, "economy"): 4400.0,     # +10%
    }
    result = impute(observed, periods=periods)
    filled = result.imputed[Cell("2025-02", "DEL-BOM", 7, "economy")]
    assert filled.basis == "same_lead_bucket"
    assert filled.value == pytest.approx(5500.0)


def test_a_long_gap_is_dropped_rather_than_imputed_forever():
    periods = [f"2025-{m:02d}" for m in range(1, 12)]
    observed = {Cell("2025-01", "DEL-BOM", 7, "economy"): 5000.0}
    # a donor route present throughout, so a donor movement always exists
    for p in periods:
        observed[Cell(p, "BLR-DEL", 7, "economy")] = 4000.0

    result = impute(observed, periods=periods)
    imputed_periods = sorted(c.period for c in result.imputed
                             if c.route == "DEL-BOM")
    assert len(imputed_periods) == MAX_CONSECUTIVE_IMPUTATIONS
    assert any(c.route == "DEL-BOM" for c in result.dropped)


def test_a_cell_never_seen_before_is_not_invented():
    periods = ["2025-01", "2025-02"]
    observed = {
        Cell("2025-01", "BLR-DEL", 7, "economy"): 4000.0,
        Cell("2025-02", "BLR-DEL", 7, "economy"): 4400.0,
        Cell("2025-02", "DEL-BOM", 7, "economy"): 5000.0,      # first appearance
    }
    result = impute(observed, periods=periods)
    # DEL-BOM had no January price, so January must stay empty
    assert Cell("2025-01", "DEL-BOM", 7, "economy") not in result.imputed


def test_share_for_period_is_measured():
    periods = ["2025-01", "2025-02"]
    observed = _grid(periods, ["DEL-BOM"], buckets=(7, 14, 21, 30))
    del observed[Cell("2025-02", "DEL-BOM", 14, "economy")]

    result = impute(observed)
    # three observed and one imputed in February
    assert result.share_for_period("2025-02") == pytest.approx(25.0)
    assert result.share_for_period("2025-01") == 0.0


def test_filled_merges_observed_and_imputed():
    periods = ["2025-01", "2025-02"]
    observed = _grid(periods, ["DEL-BOM"], buckets=(7, 14))
    del observed[Cell("2025-02", "DEL-BOM", 14, "economy")]
    result = impute(observed)

    filled = result.filled
    assert len(filled) == 4
    assert set(result.observed) <= set(filled)


def test_summary_counts_by_basis():
    periods = ["2025-01", "2025-02"]
    observed = _grid(periods, ["DEL-BOM"], buckets=(7, 14, 21))
    del observed[Cell("2025-02", "DEL-BOM", 14, "economy")]
    summary = impute(observed).summary()

    assert summary["imputed_cells"] == 1
    assert summary["imputed_by_basis"] == {"same_route": 1}
    assert summary["observed_cells"] == 5
