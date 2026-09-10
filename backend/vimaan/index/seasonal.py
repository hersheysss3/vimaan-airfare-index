"""
Seasonal adjustment, with Indian moving holidays.

Airfares in India have a seasonal shape that a Northern-Hemisphere default
would get wrong, and one large component of it does not sit still: Diwali
moves between mid-October and mid-November, so a fixed month-of-year factor
smears the festival premium across two months and never removes it from
either. Holi and Dussehra move the same way. That is what a moving-holiday
regressor is for — it is estimated separately from the month effect and
subtracted before the seasonal factors are computed.

ENGINE, STATED PLAINLY. X-13ARIMA-SEATS is a binary published by the US Census
Bureau. When it is present on the machine this module uses it and says so.
When it is not — which includes every serverless deployment of this API — the
fallback is a RegARIMA-style holiday and trading-day regression followed by an
STL decomposition, in pure Python. That is a real seasonal adjustment and it
is not X-13. Every result carries the engine that produced it in `engine`, and
callers are expected to publish that string rather than the one they wish were
true. Set X13PATH to the directory holding the binary to get the real thing.

References: X-13ARIMA-SEATS Reference Manual (US Census Bureau) for the
genhol construction; Cleveland et al. (1990) for STL; ONS, "Seasonal
adjustment of air fares" for the treatment of moving festivals in a fares
index.
"""
from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np

# --------------------------------------------------------------- holidays

#: Main-day dates for the Indian moving holidays that move air travel.
#: Compiled from the Government of India gazetted holiday lists and the
#: Drik Panchang almanac, and deliberately finite: a date that has not been
#: checked is absent rather than extrapolated from a lunar approximation,
#: because a regressor placed on the wrong week is worse than no regressor.
#: Extend it as the official calendar is published.
MOVING_HOLIDAYS: dict[str, dict[int, date]] = {
    "diwali": {
        2019: date(2019, 10, 27),
        2020: date(2020, 11, 14),
        2021: date(2021, 11, 4),
        2022: date(2022, 10, 24),
        2023: date(2023, 11, 12),
        2024: date(2024, 10, 31),
        2025: date(2025, 10, 20),
        2026: date(2026, 11, 8),
        2027: date(2027, 10, 29),
    },
    "holi": {
        2019: date(2019, 3, 21),
        2020: date(2020, 3, 10),
        2021: date(2021, 3, 29),
        2022: date(2022, 3, 18),
        2023: date(2023, 3, 8),
        2024: date(2024, 3, 25),
        2025: date(2025, 3, 14),
        2026: date(2026, 3, 4),
        2027: date(2027, 3, 22),
    },
    "dussehra": {
        2019: date(2019, 10, 8),
        2020: date(2020, 10, 25),
        2021: date(2021, 10, 15),
        2022: date(2022, 10, 5),
        2023: date(2023, 10, 24),
        2024: date(2024, 10, 12),
        2025: date(2025, 10, 2),
        2026: date(2026, 10, 20),
        2027: date(2027, 10, 9),
    },
}

#: Days before and after the main day over which travel is affected. Fares
#: move before the festival (outbound) and after it (return), which is why the
#: window is asymmetric rather than centred.
DEFAULT_WINDOW: dict[str, tuple[int, int]] = {
    "diwali": (-10, 5),
    "holi": (-4, 2),
    "dussehra": (-6, 3),
}

VERIFIED_THROUGH = 2027


def holiday_years(name: str) -> tuple[int, int]:
    """The first and last year the table actually covers for a holiday."""
    years = MOVING_HOLIDAYS[name]
    return min(years), max(years)


def _months_between(start: str, end: str) -> list[str]:
    y0, m0 = int(start[:4]), int(start[5:7])
    y1, m1 = int(end[:4]), int(end[5:7])
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def holiday_regressor(
    periods: Sequence[str],
    name: str = "diwali",
    window: Optional[tuple[int, int]] = None,
    centred: bool = True,
) -> list[float]:
    """A genhol-style moving-holiday regressor, one value per month.

    For each month the value is the share of the holiday's effect window that
    falls inside it. A Diwali on 8 November with a (-10, +5) window puts most
    of its weight in November and the rest in late October; a Diwali on
    20 October puts most in October.

    Centring subtracts, for each calendar month, the mean value that month
    takes across the whole table. Without it the regressor is collinear with
    the month-of-year seasonal factor and the estimation splits the festival
    effect arbitrarily between the two.
    """
    if name not in MOVING_HOLIDAYS:
        raise KeyError(f"no date table for {name!r}; "
                       f"have {sorted(MOVING_HOLIDAYS)}")
    lo, hi = window or DEFAULT_WINDOW.get(name, (-7, 3))
    span = hi - lo + 1
    table = MOVING_HOLIDAYS[name]

    def raw_for(period: str) -> float:
        y, m = int(period[:4]), int(period[5:7])
        share = 0.0
        # a window can straddle a year boundary, so check the neighbours too
        for yy in (y - 1, y, y + 1):
            main = table.get(yy)
            if main is None:
                continue
            for d in range(lo, hi + 1):
                day = main + timedelta(days=d)
                if day.year == y and day.month == m:
                    share += 1.0
        return share / span

    values = [raw_for(p) for p in periods]

    if not centred:
        return values

    # long-run mean per calendar month, taken over the verified table rather
    # than over the sample, so a short sample cannot distort the centring
    covered = sorted(table)
    all_periods = [f"{y:04d}-{m:02d}" for y in covered for m in range(1, 13)]
    by_month: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for p in all_periods:
        by_month[int(p[5:7])].append(raw_for(p))
    means = {m: (sum(v) / len(v) if v else 0.0) for m, v in by_month.items()}

    return [v - means[int(p[5:7])] for v, p in zip(values, periods)]


def uncovered_periods(periods: Sequence[str], name: str = "diwali") -> list[str]:
    """Periods whose holiday window falls outside the verified date table.

    A caller publishing an adjusted series is entitled to know that the tail
    of it was adjusted without a holiday regressor, because the table ran out.
    """
    first, last = holiday_years(name)
    return [p for p in periods if not (first <= int(p[:4]) <= last)]


# ------------------------------------------------------------------ engine

def x13_binary() -> Optional[str]:
    """Path to a real X-13ARIMA-SEATS binary, if this machine has one."""
    env = os.environ.get("X13PATH")
    if env:
        for exe in ("x13as", "x13as.exe", "x12a", "x12a.exe"):
            candidate = os.path.join(env, exe)
            if os.path.isfile(candidate):
                return env
        if os.path.isdir(env):
            return env
    found = shutil.which("x13as") or shutil.which("x12a")
    return os.path.dirname(found) if found else None


@dataclass
class SeasonalResult:
    """An adjusted series and an honest account of how it was produced."""

    periods: list[str]
    original: list[float]
    adjusted: list[float]
    seasonal: list[float]
    trend: list[float]
    #: exactly which engine ran: "X-13ARIMA-SEATS" or "RegARIMA+STL"
    engine: str
    holidays: list[str] = field(default_factory=list)
    holiday_coefficients: dict[str, float] = field(default_factory=dict)
    #: periods adjusted without a holiday regressor because the table ended
    holiday_gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "engine": self.engine,
            "periods": len(self.periods),
            "holidays_modelled": self.holidays,
            "holiday_coefficients": {k: round(v, 5)
                                     for k, v in self.holiday_coefficients.items()},
            "periods_without_holiday_cover": self.holiday_gaps,
            "notes": self.notes,
        }


def _ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def _stl(values: np.ndarray, period: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """Seasonal and trend components in logs, via STL."""
    from statsmodels.tsa.seasonal import STL

    res = STL(values, period=period, robust=True).fit()
    return np.asarray(res.seasonal), np.asarray(res.trend)


def adjust(
    periods: Sequence[str],
    values: Sequence[float],
    holidays: Sequence[str] = ("diwali",),
    prefer_x13: bool = True,
) -> SeasonalResult:
    """Seasonally adjust a monthly index series.

    Works multiplicatively, by decomposing the log series: an index is a ratio
    and its seasonal component scales rather than adds.

    Order of operations follows standard practice: estimate and remove the
    moving-holiday effect first, then decompose what is left. Doing it the
    other way round lets the festival contaminate the month factors.
    """
    periods = list(periods)
    values = [float(v) for v in values]
    if len(periods) != len(values):
        raise ValueError("periods and values must be the same length")
    if len(periods) < 24:
        raise ValueError(
            f"seasonal adjustment needs at least two full years; got "
            f"{len(periods)} months")
    if any(v <= 0 for v in values):
        raise ValueError("index values must be positive to work in logs")

    notes: list[str] = []
    y = np.log(np.asarray(values))

    # ---- moving-holiday regression (the RegARIMA part, kept in both paths)
    regressors: dict[str, np.ndarray] = {}
    gaps: list[str] = []
    for h in holidays:
        reg = np.asarray(holiday_regressor(periods, h))
        if np.allclose(reg, 0.0):
            notes.append(f"{h}: regressor is identically zero over this span, dropped")
            continue
        regressors[h] = reg
        missing = uncovered_periods(periods, h)
        if missing:
            gaps.extend(missing)
            notes.append(
                f"{h}: no verified date for {len(set(missing))} period(s) "
                f"({missing[0]}..{missing[-1]}); those months carry no holiday "
                f"correction")

    holiday_coef: dict[str, float] = {}
    y_corrected = y.copy()
    if regressors:
        names = list(regressors)
        n = len(y)

        # Month dummies and a linear trend go in alongside the holiday terms.
        # Without them the holiday regressor is identified off the level of
        # October and November rather than off the festival's movement between
        # them, and it absorbs the ordinary month effect wholesale — a Holi
        # coefficient of 1.05 on a series with no Holi effect at all, which is
        # what this looked like before the dummies were added. With them, the
        # effect is identified the way X-13 identifies it: from the fact that
        # the festival lands in different months in different years.
        cols = [np.ones(n), np.arange(n, dtype=float)]
        col_names = ["intercept", "trend"]
        months = [int(p[5:7]) for p in periods]
        for m in range(2, 13):                      # January is the reference
            cols.append(np.asarray([1.0 if mm == m else 0.0 for mm in months]))
            col_names.append(f"month[{m}]")
        for h in names:
            cols.append(regressors[h])
            col_names.append(h)

        X = np.column_stack(cols)
        beta = _ols(X, y)
        ix = {name: i for i, name in enumerate(col_names)}
        for h in names:
            holiday_coef[h] = float(beta[ix[h]])
            y_corrected = y_corrected - beta[ix[h]] * regressors[h]

    # ---- decomposition
    engine = "RegARIMA+STL"
    x13_dir = x13_binary() if prefer_x13 else None
    seasonal_log: Optional[np.ndarray] = None
    trend_log: Optional[np.ndarray] = None

    if x13_dir:
        try:
            import pandas as pd
            from statsmodels.tsa.x13 import x13_arima_analysis

            idx = pd.PeriodIndex(periods, freq="M").to_timestamp()
            series = pd.Series(np.exp(y_corrected), index=idx)
            res = x13_arima_analysis(series, x12path=x13_dir, outlier=True,
                                     print_stdout=False)
            adjusted_levels = np.asarray(res.seasadj, dtype=float)
            trend_levels = np.asarray(res.trend, dtype=float)
            seasonal_log = np.log(np.exp(y_corrected)) - np.log(adjusted_levels)
            trend_log = np.log(trend_levels)
            engine = "X-13ARIMA-SEATS"
            notes.append(f"X-13ARIMA-SEATS binary found at {x13_dir}")
        except Exception as exc:                      # pragma: no cover
            notes.append(f"X-13 present but failed ({exc}); fell back to STL")
            seasonal_log = None

    if seasonal_log is None:
        seasonal_log, trend_log = _stl(y_corrected, period=12)
        if not x13_dir:
            notes.append(
                "no X-13ARIMA-SEATS binary on this machine; used RegARIMA "
                "holiday regression + STL. Set X13PATH to use X-13.")

    adjusted = np.exp(y - seasonal_log)
    return SeasonalResult(
        periods=periods,
        original=values,
        adjusted=[float(v) for v in adjusted],
        seasonal=[float(v) for v in np.exp(seasonal_log)],
        trend=[float(v) for v in np.exp(np.asarray(trend_log))],
        engine=engine,
        holidays=list(regressors),
        holiday_coefficients=holiday_coef,
        holiday_gaps=sorted(set(gaps)),
        notes=notes,
    )
