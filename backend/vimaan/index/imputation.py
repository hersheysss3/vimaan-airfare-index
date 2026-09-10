"""
Imputation for cells the collection missed.

A price surface always has holes. A route is not scheduled that month, a
collection window is missed, a portal blocks a lane for a week. The question
is never whether to have holes but what to do with them, and there are only
three honest answers:

  * drop the cell        — silently changes the basket between periods, which
                           is the sample-rotation artefact the whole index
                           design exists to avoid
  * carry it forward     — asserts the price did not move, which biases the
                           index toward zero and is explicitly discouraged
                           beyond a period or two (CPI Manual 2020, 6.66)
  * impute the movement  — assume the missing cell moved like comparable cells
                           that were observed, which is the recommended
                           treatment, and the one implemented here

Imputation is "targeted mean": the missing cell keeps its own last observed
level and is moved by the average movement of the closest class of cells that
*were* seen. Closest is tried in order — same route first, then same booking
window across routes, then the whole surface — because a Delhi-Mumbai 7-day
fare behaves far more like a Delhi-Mumbai 14-day fare than like a Guwahati
60-day fare.

Every imputed cell is flagged, counted and published as `imputed_pct`. An
imputed value that cannot be told apart from an observed one is not a
statistic, and the whole point of the surrounding design is that a reviewer
can see exactly how much of a figure was measured and how much was inferred.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

#: Beyond this many consecutive periods without an observation, a cell is
#: dropped rather than imputed. Carrying an inference that far means the cell
#: is contributing nothing but assumption.
MAX_CONSECUTIVE_IMPUTATIONS = 6


@dataclass(frozen=True)
class Cell:
    """One point on the price surface."""
    period: str
    route: str
    lead_bucket: int
    cabin: str

    @property
    def stratum(self) -> str:
        return f"{self.route}|L{self.lead_bucket}|{self.cabin}"


@dataclass
class ImputedCell:
    cell: Cell
    value: float
    #: which donor class supplied the movement
    basis: str
    #: how many observed cells the movement was averaged over
    donors: int
    #: periods since this cell was last actually observed
    age: int


@dataclass
class ImputationResult:
    """Filled cells plus the accounting needed to publish an imputation share."""

    observed: dict[Cell, float]
    imputed: dict[Cell, ImputedCell] = field(default_factory=dict)
    #: strata abandoned because the gap ran past MAX_CONSECUTIVE_IMPUTATIONS
    dropped: list[Cell] = field(default_factory=list)

    @property
    def filled(self) -> dict[Cell, float]:
        out = dict(self.observed)
        out.update({c: i.value for c, i in self.imputed.items()})
        return out

    def share_for_period(self, period: str) -> float:
        """Percent of that period's cells that were imputed rather than seen."""
        obs = sum(1 for c in self.observed if c.period == period)
        imp = sum(1 for c in self.imputed if c.period == period)
        total = obs + imp
        return (imp / total * 100.0) if total else 0.0

    def summary(self) -> dict:
        by_basis: dict[str, int] = {}
        for i in self.imputed.values():
            by_basis[i.basis] = by_basis.get(i.basis, 0) + 1
        return {
            "observed_cells": len(self.observed),
            "imputed_cells": len(self.imputed),
            "dropped_cells": len(self.dropped),
            "imputed_by_basis": by_basis,
            "max_consecutive_imputations": MAX_CONSECUTIVE_IMPUTATIONS,
        }


def _movement(
    observed: Mapping[Cell, float],
    keys: Sequence[Cell],
    frm: str,
    to: str,
) -> Optional[tuple[float, int]]:
    """Geometric mean movement between two periods over a class of cells.

    Geometric, to match the Jevons elementary formula the index uses: an
    arithmetic mean of ratios here would import exactly the upward bias the
    elementary aggregate was chosen to avoid.
    """
    ratios = []
    for k in keys:
        a = observed.get(Cell(frm, k.route, k.lead_bucket, k.cabin))
        b = observed.get(Cell(to, k.route, k.lead_bucket, k.cabin))
        if a and b and a > 0 and b > 0:
            ratios.append(b / a)
    if not ratios:
        return None
    return statistics.geometric_mean(ratios), len(ratios)


def impute(
    observed: Mapping[Cell, float],
    periods: Optional[Sequence[str]] = None,
    strata: Optional[Sequence[Cell]] = None,
) -> ImputationResult:
    """Fill the holes in a price surface, recording how each was filled.

    `observed` maps a Cell to its measured median fare. The grid to fill is
    every stratum that has ever been observed, across every period in the
    span — a stratum that appears in month one and vanishes in month two is
    exactly the disappearance that must be handled, not ignored.
    """
    observed = dict(observed)
    if periods is None:
        periods = sorted({c.period for c in observed})
    periods = list(periods)

    if strata is None:
        seen: dict[tuple, Cell] = {}
        for c in observed:
            seen[(c.route, c.lead_bucket, c.cabin)] = c
        strata = list(seen.values())

    result = ImputationResult(observed=observed)

    # last period in which each stratum was actually observed, and its value
    last_seen: dict[tuple, tuple[str, float]] = {}

    for period in periods:
        for s in strata:
            key = (s.route, s.lead_bucket, s.cabin)
            cell = Cell(period, s.route, s.lead_bucket, s.cabin)

            if cell in observed:
                last_seen[key] = (period, observed[cell])
                continue

            prior = last_seen.get(key)
            if prior is None:
                # never observed before this period: nothing to carry a
                # movement onto, so the cell simply does not exist yet
                continue

            prev_period, prev_value = prior
            age = periods.index(period) - periods.index(prev_period)
            if age > MAX_CONSECUTIVE_IMPUTATIONS:
                result.dropped.append(cell)
                continue

            # donor classes, nearest first
            same_route = [c for c in strata
                          if c.route == s.route and c.cabin == s.cabin
                          and c.lead_bucket != s.lead_bucket]
            same_bucket = [c for c in strata
                           if c.lead_bucket == s.lead_bucket
                           and c.cabin == s.cabin and c.route != s.route]
            everything = [c for c in strata if c.cabin == s.cabin]

            for basis, donors in (("same_route", same_route),
                                  ("same_lead_bucket", same_bucket),
                                  ("all_cells", everything)):
                moved = _movement(observed, donors, prev_period, period)
                if moved is None:
                    continue
                ratio, n = moved
                result.imputed[cell] = ImputedCell(
                    cell=cell,
                    value=prev_value * ratio,
                    basis=basis,
                    donors=n,
                    age=age,
                )
                break
            else:
                # nothing comparable was observed either; the cell stays a hole
                result.dropped.append(cell)

    return result
