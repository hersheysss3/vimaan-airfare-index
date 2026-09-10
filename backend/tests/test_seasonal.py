"""
Seasonal adjustment, and the moving-holiday regressor in particular.

The claim being tested is narrow and checkable: a festival that lands in
October one year and November the next should have its effect removed from
both, without the ordinary month pattern being removed twice or the festival
effect being confused for it. An earlier version of this module failed
exactly that test — it reported a Holi coefficient of 1.05 on a series with
no Holi effect at all, because the regression had no month dummies and the
holiday term absorbed March wholesale.
"""
from __future__ import annotations

import math

import pytest

from vimaan.index import seasonal


def _series(periods, *, diwali=0.0, seasonal_amp=0.06, trend=0.002):
    reg = seasonal.holiday_regressor(periods, "diwali")
    out = []
    for i, (p, r) in enumerate(zip(periods, reg)):
        month = int(p[5:7])
        out.append(100 * (1 + trend) ** i
                   * (1 + seasonal_amp * math.sin((month - 3) / 12 * 2 * math.pi))
                   * math.exp(diwali * r))
    return out


PERIODS = seasonal._months_between("2019-01", "2027-12")


def test_diwali_moves_between_october_and_november():
    # the whole reason a moving-holiday regressor exists
    assert seasonal.MOVING_HOLIDAYS["diwali"][2022].month == 10
    assert seasonal.MOVING_HOLIDAYS["diwali"][2023].month == 11


def test_regressor_is_centred():
    reg = seasonal.holiday_regressor(PERIODS, "diwali")
    assert sum(reg) / len(reg) == pytest.approx(0.0, abs=1e-9)


def test_regressor_is_non_zero_around_the_festival():
    reg = dict(zip(PERIODS, seasonal.holiday_regressor(PERIODS, "diwali")))
    # 2024's Diwali is 31 October, so the window straddles both months
    assert abs(reg["2024-10"]) > 0.0
    assert abs(reg["2024-11"]) > 0.0


@pytest.mark.parametrize("true_effect", [0.0, 0.20, -0.10])
def test_recovers_a_known_holiday_effect(true_effect):
    values = _series(PERIODS, diwali=true_effect)
    result = seasonal.adjust(PERIODS, values, holidays=("diwali", "holi"))
    assert result.holiday_coefficients["diwali"] == pytest.approx(
        true_effect, abs=0.01)


def test_does_not_invent_an_effect_that_is_not_there():
    # Holi is modelled but the data has no Holi effect; the coefficient must
    # not soak up the ordinary March seasonal
    values = _series(PERIODS, diwali=0.20)
    result = seasonal.adjust(PERIODS, values, holidays=("diwali", "holi"))
    assert result.holiday_coefficients["holi"] == pytest.approx(0.0, abs=0.01)


def test_removes_the_seasonal_pattern():
    import statistics

    values = _series(PERIODS, diwali=0.15)
    result = seasonal.adjust(PERIODS, values, holidays=("diwali",))

    def month_factor_spread(series):
        by_month: dict[int, list[float]] = {}
        for p, v, t in zip(PERIODS, series, result.trend):
            by_month.setdefault(int(p[5:7]), []).append(v / t)
        means = {m: statistics.mean(v) for m, v in by_month.items()}
        return max(means.values()) - min(means.values())

    before = month_factor_spread(values)
    after = month_factor_spread(result.adjusted)
    assert after < before / 10


def test_engine_is_reported_honestly():
    values = _series(PERIODS)
    result = seasonal.adjust(PERIODS, values)
    # whichever ran, the result must say which, and it must be one of the two
    assert result.engine in ("X-13ARIMA-SEATS", "RegARIMA+STL")
    if result.engine == "RegARIMA+STL":
        assert any("X-13" in n for n in result.notes)


def test_short_series_is_refused_rather_than_guessed():
    periods = seasonal._months_between("2025-01", "2025-12")
    with pytest.raises(ValueError, match="two full years"):
        seasonal.adjust(periods, [100.0] * len(periods))


def test_periods_outside_the_date_table_are_reported():
    periods = seasonal._months_between("2030-01", "2032-12")
    missing = seasonal.uncovered_periods(periods, "diwali")
    assert len(missing) == len(periods)

    values = [100 + i * 0.1 for i in range(len(periods))]
    result = seasonal.adjust(periods, values, holidays=("diwali",))
    # a period the table cannot cover must be surfaced, not silently adjusted
    assert result.holiday_gaps or any("no verified date" in n
                                      for n in result.notes)


def test_non_positive_values_are_refused():
    values = _series(PERIODS)
    values[5] = 0.0
    with pytest.raises(ValueError, match="positive"):
        seasonal.adjust(PERIODS, values)
