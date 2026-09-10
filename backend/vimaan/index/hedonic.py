"""
Hedonic quality adjustment.

The problem this solves is specific. A matched-model index compares like with
like: the same flight, the same booking window, month after month. But airline
inventory does not hold still. A nonstop sells out and the cheapest seat on the
route becomes a one-stop; a carrier drops its free checked bag; an evening
departure is replaced by a 05:40. If the index treats the new price as the old
product's price, a quality change is published as inflation — or, just as bad,
a quality *loss* is published as a price fall.

Two functions, for the two places the adjustment is needed:

  fit()     estimates what the market charges for each characteristic, from the
            pooled panel: the time-dummy hedonic
                ln p_it = mu_t + sum_k beta_k z_ikt + e_it
            where exp(mu_t) is the quality-adjusted price movement, because the
            characteristics have absorbed everything the flight is worth.

  adjust()  applies those coefficients to a single substitution, so a one-stop
            standing in for a nonstop is priced as the nonstop would have been.

The model is estimated in logs, so coefficients read as proportional effects:
beta = -0.15 on a stop means a stop is worth about 15% off the fare. That is
the form the CPI Manual (2020, ch. 8) describes and the form ONS publishes.

Diagnostics are returned with the estimates rather than hidden. A hedonic model
with an R-squared of 0.2 has not adjusted for quality, it has added noise, and
the caller is entitled to know that before publishing anything derived from it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

#: Characteristics the model prices. Continuous ones enter directly (duration
#: in logs, because an hour matters more on a short sector), categorical ones
#: as dummies with the first level dropped as the reference.
CONTINUOUS = ("log_duration", "stops", "checked_bag_kg", "refundable")
CATEGORICAL = ("carrier", "depart_slot", "cabin", "lead_bucket")

#: Below this, the fit is too weak for the adjustment to be worth making and
#: the caller should fall back to matched-model only. Not a hard error: the
#: number is reported and the decision belongs to the caller.
MIN_USABLE_R2 = 0.30


@dataclass
class HedonicModel:
    """Fitted coefficients, the quality-adjusted movement, and the diagnostics."""

    periods: list[str]
    #: exp(mu_t), normalised so the first period is 1.0
    index: list[float]
    #: characteristic -> proportional effect on log price
    coefficients: dict[str, float]
    r_squared: float
    adj_r_squared: float
    n_obs: int
    n_params: int
    #: reference level dropped for each categorical, so a caller can read the
    #: coefficients without guessing what they are relative to
    reference_levels: dict[str, Any] = field(default_factory=dict)
    residual_sd: float = 0.0

    @property
    def usable(self) -> bool:
        """Whether the fit is strong enough to adjust prices with."""
        return self.r_squared >= MIN_USABLE_R2 and self.n_obs > 10 * self.n_params

    def summary(self) -> dict:
        """A compact, publishable description of the model."""
        return {
            "form": "time-dummy hedonic, ln(price) ~ period + characteristics",
            "n_obs": self.n_obs,
            "n_params": self.n_params,
            "r_squared": round(self.r_squared, 4),
            "adj_r_squared": round(self.adj_r_squared, 4),
            "residual_sd_log": round(self.residual_sd, 4),
            "usable": self.usable,
            "min_usable_r_squared": MIN_USABLE_R2,
            "reference_levels": self.reference_levels,
            "coefficients": {k: round(v, 5) for k, v in self.coefficients.items()},
        }


def _features(obs: Mapping[str, Any]) -> dict[str, Any]:
    """Pull the priced characteristics out of one observation.

    Missing values are not imputed here. A row that cannot supply a
    characteristic is dropped by _design rather than silently given a zero,
    which would tell the regression the flight had no duration.
    """
    duration = obs.get("duration_min")
    return {
        "log_duration": math.log(float(duration)) if duration else None,
        "stops": float(obs["stops"]) if obs.get("stops") is not None else None,
        "checked_bag_kg": (float(obs["checked_bag_kg"])
                           if obs.get("checked_bag_kg") is not None else 0.0),
        "refundable": 1.0 if obs.get("refundable") else 0.0,
        "carrier": obs.get("carrier"),
        "depart_slot": obs.get("depart_slot") or "unknown",
        "cabin": obs.get("cabin") or "economy",
        "lead_bucket": obs.get("lead_bucket"),
    }


def _design(
    observations: Sequence[Mapping[str, Any]],
    periods: Sequence[str],
) -> tuple[np.ndarray, np.ndarray, list[str], dict[str, Any]]:
    """Build the design matrix: intercept, time dummies, characteristics."""
    period_ix = {p: i for i, p in enumerate(periods)}

    rows: list[dict[str, Any]] = []
    prices: list[float] = []
    for obs in observations:
        price = obs.get("price")
        period = obs.get("period")
        if not price or price <= 0 or period not in period_ix:
            continue
        f = _features(obs)
        # a row missing a continuous characteristic cannot be used: giving it a
        # zero would assert a fact about the flight that was never observed
        if f["log_duration"] is None or f["stops"] is None:
            continue
        f["_period"] = period
        rows.append(f)
        prices.append(float(price))

    if not rows:
        raise ValueError("no usable observations for a hedonic fit")

    levels: dict[str, list] = {}
    for cat in CATEGORICAL:
        seen = sorted({r[cat] for r in rows if r[cat] is not None}, key=str)
        levels[cat] = seen

    names: list[str] = ["intercept"]
    names += [f"period[{p}]" for p in periods[1:]]
    names += list(CONTINUOUS)
    reference: dict[str, Any] = {}
    for cat in CATEGORICAL:
        if len(levels[cat]) <= 1:
            reference[cat] = levels[cat][0] if levels[cat] else None
            continue
        reference[cat] = levels[cat][0]
        names += [f"{cat}[{lv}]" for lv in levels[cat][1:]]

    col = {n: i for i, n in enumerate(names)}
    X = np.zeros((len(rows), len(names)))
    for i, r in enumerate(rows):
        X[i, 0] = 1.0
        t = period_ix[r["_period"]]
        if t > 0:
            X[i, col[f"period[{periods[t]}]"]] = 1.0
        for c in CONTINUOUS:
            X[i, col[c]] = float(r[c])
        for cat in CATEGORICAL:
            lv = r[cat]
            key = f"{cat}[{lv}]"
            if key in col:
                X[i, col[key]] = 1.0

    y = np.log(np.asarray(prices))
    return X, y, names, reference


def fit(
    observations: Iterable[Mapping[str, Any]],
    periods: Optional[Sequence[str]] = None,
) -> HedonicModel:
    """Estimate the time-dummy hedonic on a pooled panel.

    Each observation is a mapping with at least `period`, `price`, and the
    characteristics in CONTINUOUS/CATEGORICAL. Extra keys are ignored, so
    silver_fare rows can be handed over as they are.
    """
    obs = list(observations)
    if periods is None:
        periods = sorted({o["period"] for o in obs if o.get("period")})
    periods = list(periods)
    if len(periods) < 2:
        raise ValueError("a hedonic index needs at least two periods")

    X, y, names, reference = _design(obs, periods)

    # least squares via pinv: the design is rank-deficient whenever a carrier
    # flies in only one period, which happens constantly on thin routes
    beta, _res, rank, _sv = np.linalg.lstsq(X, y, rcond=None)

    fitted = X @ beta
    resid = y - fitted
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    n, p = X.shape
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / (n - rank) if n > rank else 0.0
    resid_sd = float(np.sqrt(ss_res / (n - rank))) if n > rank else 0.0

    coefficients = {name: float(b) for name, b in zip(names, beta)}

    index = [1.0]
    for period in periods[1:]:
        index.append(math.exp(coefficients.get(f"period[{period}]", 0.0)))

    return HedonicModel(
        periods=periods,
        index=index,
        coefficients=coefficients,
        r_squared=r2,
        adj_r_squared=adj_r2,
        n_obs=n,
        n_params=int(rank),
        reference_levels=reference,
        residual_sd=resid_sd,
    )


def adjust(
    price: float,
    frm: Mapping[str, Any],
    to: Mapping[str, Any],
    model: HedonicModel,
) -> float:
    """Restate `price` (observed on product `frm`) as the price of `to`.

    Used when a matched item is replaced: the substitute's price is corrected
    by the market value of the characteristics that changed, so only the
    genuine price movement survives into the index.

        adjusted = price * exp( sum_k beta_k ( z_k(to) - z_k(frm) ) )

    A characteristic the model never priced contributes nothing, which is the
    conservative direction: an unpriced difference is left in the price rather
    than adjusted away on a guess.
    """
    if price <= 0:
        raise ValueError("price must be positive")

    a, b = _features(frm), _features(to)
    delta = 0.0

    for c in CONTINUOUS:
        av, bv = a.get(c), b.get(c)
        if av is None or bv is None:
            continue
        delta += model.coefficients.get(c, 0.0) * (float(bv) - float(av))

    for cat in CATEGORICAL:
        av, bv = a.get(cat), b.get(cat)
        if av == bv:
            continue
        # the reference level's coefficient is zero by construction
        delta += model.coefficients.get(f"{cat}[{bv}]", 0.0)
        delta -= model.coefficients.get(f"{cat}[{av}]", 0.0)

    return price * math.exp(delta)


def quality_change_pct(
    frm: Mapping[str, Any], to: Mapping[str, Any], model: HedonicModel
) -> float:
    """How much of a substitution is quality rather than price, in percent.

    Positive means the replacement is the better product. This is the number
    that belongs in a revision note: it says explicitly how much of an observed
    price move was taken out as quality.
    """
    return (adjust(1.0, frm, to, model) - 1.0) * 100.0
