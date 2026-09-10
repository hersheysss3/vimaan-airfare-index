"""
The hedonic model has to recover effects it was not told about.

Testing it against synthetic data with known coefficients is the only way to
know the adjustment is doing arithmetic rather than producing plausible
numbers: if the fit cannot find a -18% stop penalty that was put there on
purpose, it will not find the real one either.
"""
from __future__ import annotations

import math
import random

import pytest

from vimaan.index import hedonic


def _panel(n_periods: int = 12, *, stop=-0.18, refund=0.25, dur=0.30,
           trend=0.01, noise=0.03, seed=11):
    rng = random.Random(seed)
    periods = [f"2025-{m:02d}" for m in range(1, n_periods + 1)]
    rows = []
    for t, p in enumerate(periods):
        for carrier in ("6E", "AI", "SG"):
            for stops in (0, 1):
                for refundable in (0, 1):
                    for duration in (95, 140, 200):
                        price = 4000 * math.exp(
                            trend * t + stop * stops + refund * refundable
                            + dur * math.log(duration / 95)
                            + rng.gauss(0, noise))
                        rows.append({
                            "period": p, "price": price, "stops": stops,
                            "duration_min": duration,
                            "refundable": bool(refundable),
                            "checked_bag_kg": 15, "carrier": carrier,
                            "depart_slot": "morning", "cabin": "economy",
                            "lead_bucket": 14,
                        })
    return periods, rows


def test_recovers_known_coefficients():
    _, rows = _panel()
    model = hedonic.fit(rows)

    assert model.coefficients["stops"] == pytest.approx(-0.18, abs=0.02)
    assert model.coefficients["refundable"] == pytest.approx(0.25, abs=0.02)
    assert model.coefficients["log_duration"] == pytest.approx(0.30, abs=0.02)
    assert model.r_squared > 0.9
    assert model.usable


def test_time_dummies_track_the_real_trend():
    periods, rows = _panel(trend=0.01)
    model = hedonic.fit(rows)
    # eleven months of 1% compounding
    assert model.index[-1] == pytest.approx(math.exp(0.01 * 11), rel=0.02)
    assert model.index[0] == 1.0


def test_adjust_prices_a_single_characteristic_change():
    _, rows = _panel()
    model = hedonic.fit(rows)
    nonstop = {"stops": 0, "duration_min": 140, "refundable": False,
               "checked_bag_kg": 15, "carrier": "6E", "depart_slot": "morning",
               "cabin": "economy", "lead_bucket": 14}
    onestop = dict(nonstop, stops=1)

    # the substitute is the worse product, so its price must be marked down
    adjusted = hedonic.adjust(5000.0, nonstop, onestop, model)
    assert adjusted < 5000.0
    assert adjusted == pytest.approx(5000 * math.exp(-0.18), rel=0.03)


def test_adjustment_is_symmetric():
    _, rows = _panel()
    model = hedonic.fit(rows)
    a = {"stops": 0, "duration_min": 140, "refundable": False,
         "checked_bag_kg": 15, "carrier": "6E", "depart_slot": "morning",
         "cabin": "economy", "lead_bucket": 14}
    b = dict(a, stops=1, refundable=True)

    there = hedonic.adjust(5000.0, a, b, model)
    back = hedonic.adjust(there, b, a, model)
    assert back == pytest.approx(5000.0, rel=1e-9)


def test_identical_products_are_not_adjusted():
    _, rows = _panel()
    model = hedonic.fit(rows)
    same = {"stops": 1, "duration_min": 200, "refundable": True,
            "checked_bag_kg": 20, "carrier": "AI", "depart_slot": "evening",
            "cabin": "economy", "lead_bucket": 7}
    assert hedonic.adjust(4321.0, same, same, model) == pytest.approx(4321.0)


def test_quality_change_reports_the_direction():
    _, rows = _panel()
    model = hedonic.fit(rows)
    a = {"stops": 0, "duration_min": 140, "refundable": False,
         "checked_bag_kg": 15, "carrier": "6E", "depart_slot": "morning",
         "cabin": "economy", "lead_bucket": 14}
    worse = dict(a, stops=1)
    better = dict(a, refundable=True)

    assert hedonic.quality_change_pct(a, worse, model) < 0
    assert hedonic.quality_change_pct(a, better, model) > 0


def test_a_weak_fit_is_reported_as_unusable():
    # price is pure noise: no characteristic explains anything
    rng = random.Random(3)
    rows = [{
        "period": f"2025-{m:02d}", "price": 4000 * math.exp(rng.gauss(0, 0.8)),
        "stops": rng.choice((0, 1)), "duration_min": rng.choice((95, 200)),
        "refundable": False, "checked_bag_kg": 15, "carrier": "6E",
        "depart_slot": "morning", "cabin": "economy", "lead_bucket": 14,
    } for m in range(1, 13) for _ in range(30)]

    model = hedonic.fit(rows)
    assert model.r_squared < hedonic.MIN_USABLE_R2
    assert not model.usable


def test_rows_missing_a_characteristic_are_dropped_not_zeroed():
    _, rows = _panel()
    rows.append({"period": "2025-01", "price": 5000, "stops": None,
                 "duration_min": None, "refundable": False,
                 "checked_bag_kg": 15, "carrier": "6E",
                 "depart_slot": "morning", "cabin": "economy",
                 "lead_bucket": 14})
    model = hedonic.fit(rows)
    assert model.n_obs == len(rows) - 1


def test_one_period_is_refused():
    rows = [{"period": "2025-01", "price": 100, "stops": 0,
             "duration_min": 100, "refundable": False, "checked_bag_kg": 15,
             "carrier": "6E", "depart_slot": "morning", "cabin": "economy",
             "lead_bucket": 14}]
    with pytest.raises(ValueError):
        hedonic.fit(rows)
