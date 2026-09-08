"""
Chain drift: where it actually comes from, and that our estimators remove it.

This file exists because the mechanism is easy to state loosely and get wrong.
Volatility alone does not produce drift in a Jevons index: on a fixed matched
sample the logs telescope and a chained series returns exactly to its starting
value when prices do.

Drift appears when the *matched sample changes between links* — a flight sells
out, a route is not scheduled that day, a portal omits a carrier. Each link is
then computed on a different set of items, the telescoping breaks, and the
error compounds. Airline fare data churns constantly, which is why a chained
index is the wrong tool for it.
"""
import random

import pytest

from vimaan.index.elementary import chained
from vimaan.index.multilateral import geks_jevons, rolling_window, drift


def round_trip_panel(n_periods=30, n_items=25, seed=11, churn=0.0):
    """A panel whose prices end exactly where they began.

    The true index movement is therefore zero by construction, and any
    departure from zero is error the estimator invented.

    `churn` is the probability that a given flight is simply absent from a
    given period, which is what makes the matched sample move.
    """
    rng = random.Random(seed)
    base = {f"f{i}": 4000.0 + 90 * i for i in range(n_items)}
    periods = [dict(base)]
    for _ in range(n_periods - 2):
        period = {}
        for k, v in base.items():
            if rng.random() < churn:
                continue                      # not on sale in this collection
            period[k] = v * rng.choice([0.62, 0.78, 1.0, 1.35, 1.9])
        periods.append(period)
    periods.append(dict(base))
    return periods


# ------------------------------------------------- the mechanism, stated
def test_volatility_alone_does_not_drift_a_chained_jevons():
    """Fixed sample, violent price swings, still no drift.

    Worth locking in: it is the reason "dynamic pricing causes chain drift" is
    an imprecise claim, and a reviewer is entitled to check it.
    """
    panel = round_trip_panel(churn=0.0)
    assert drift(chained(panel)) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("churn", [0.15, 0.30, 0.45])
def test_sample_churn_is_what_drifts_a_chained_index(churn):
    panel = round_trip_panel(churn=churn)
    assert abs(drift(chained(panel))) > 5.0


# ------------------------------------------------- the estimators we publish
@pytest.mark.parametrize("churn", [0.15, 0.30, 0.45])
def test_geks_is_drift_free_under_churn(churn):
    panel = round_trip_panel(churn=churn)
    assert drift(geks_jevons(panel)) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("churn", [0.15, 0.30, 0.45])
def test_spliced_rolling_window_stays_close_to_truth(churn):
    """Splicing gives up exactness for the ability to never revise history."""
    panel = round_trip_panel(n_periods=60, churn=churn, seed=33)
    assert abs(drift(rolling_window(panel, window=25))) < 2.0


@pytest.mark.parametrize("churn", [0.15, 0.30, 0.45])
def test_geks_beats_chaining_by_a_wide_margin(churn):
    panel = round_trip_panel(churn=churn)
    naive = abs(drift(chained(panel)))
    ours = abs(drift(geks_jevons(panel)))
    assert ours < naive / 10.0


def test_drift_is_reported_in_index_points():
    assert drift([100.0, 105.0, 112.9]) == pytest.approx(12.9)
    assert drift([1.0, 1.0]) == pytest.approx(0.0)
    assert drift([]) == 0.0
