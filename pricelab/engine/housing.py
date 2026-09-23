"""Rents, and the four approaches to owner-occupied housing.

Owner-occupied housing is where a consumer price index has to decide what
question it is answering, and the four approaches are four different
questions -- not four estimators of one quantity, and not a menu of
methods for the same number. Each result here carries the question it
answers (`OOH_QUESTIONS`), and the page presents them as four sections,
each under its own question, with no selector implying a choice between
them.

rental equivalence   What would owner-occupiers pay to rent the homes they
                     live in? The cost of the housing *service*, valued at
                     market rents. Uses a rental price index.
net acquisitions     What do households pay to acquire dwellings that are
                     new to the household sector? The price of the *asset*
                     as bought, excluding land where the index is to follow
                     the HICP's treatment. Uses new-dwelling prices.
user cost            What does it cost, each period, to own a home? The
                     forgone return on the money tied up, depreciation,
                     maintenance and taxes, less the expected capital gain.
                     Can go negative when prices rise faster than interest.
payments             What do owner-occupiers actually pay out? Mortgage
                     interest, repairs, insurance and property taxes -- cash
                     outlays, whatever they buy.

Sources: CPI Manual 2020, chapter on owner-occupied housing (the four
approaches); Eurostat, *Technical manual on Owner-Occupied Housing and
House Price Indices* (2017); Diewert, "The Treatment of Owner Occupied
Housing and Other Durables in a Consumer Price Index" (2009).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "OOH_QUESTIONS",
    "HousingError",
    "OOHResult",
    "RentIndexResult",
    "net_acquisitions",
    "payments",
    "rent_index",
    "rental_equivalence",
    "user_cost",
]


#: For each approach: the question it answers, what it counts, and what it
#: deliberately leaves out.
OOH_QUESTIONS: dict[str, dict[str, str]] = {
    "rental_equivalence": {
        "name": "Rental equivalence",
        "question": "What would owner-occupiers pay to rent the homes they live in?",
        "counts": "the housing service owners consume, valued at market rents",
        "leaves_out": "the asset: house prices enter only through what they do to rents",
    },
    "net_acquisitions": {
        "name": "Net acquisitions",
        "question": "What do households pay to acquire dwellings new to the household sector?",
        "counts": "the price of new dwellings (and major improvements) bought by households",
        "leaves_out": "the existing stock changing hands between households, and (in the "
                      "HICP's treatment) the land",
    },
    "user_cost": {
        "name": "User cost",
        "question": "What does it cost, each period, to own a home?",
        "counts": "the forgone return on the value of the home, depreciation, maintenance and "
                  "taxes, less the expected capital gain",
        "leaves_out": "nothing about ownership, which is why it can turn negative when prices "
                      "are expected to rise faster than interest rates",
    },
    "payments": {
        "name": "Payments",
        "question": "What do owner-occupiers actually pay out?",
        "counts": "cash outlays: mortgage interest, repairs, insurance, property taxes",
        "leaves_out": "the owner's own capital and any imputed cost; mortgage capital "
                      "repayment, which is saving, not consumption",
    },
}


class HousingError(ValueError):
    """The inputs cannot support the measure asked of them."""


@dataclass(frozen=True)
class OOHResult:
    approach: str
    index: pd.Series
    """Base period = 100 (for user cost, of the cost level, which can be
    negative)."""
    components: pd.DataFrame
    base: pd.Timestamp
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def question(self) -> str:
        return OOH_QUESTIONS[self.approach]["question"]

    @property
    def label(self) -> str:
        spec = OOH_QUESTIONS[self.approach]
        return (f"{spec['name']}, {self.base:%b %Y} = 100. Answers: {spec['question']} "
                f"Counts {spec['counts']}; leaves out {spec['leaves_out']}.")


# ---------------------------------------------------------------------
# Rents
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class RentIndexResult:
    index: pd.Series
    by_stratum: pd.DataFrame
    matched: pd.Series
    """Dwellings with a rent in both periods of each link."""
    base: pd.Timestamp
    notes: tuple[str, ...] = field(default_factory=tuple)

    measures = ("the change in rent for the same dwellings from one period to the next, "
                "chained, combined across strata with fixed weights")
    limitation = ("it follows sitting tenancies as much as new lets, which move differently; "
                  "and a dwelling ageing in the sample carries its depreciation into the index "
                  "unless it is adjusted for")

    @property
    def label(self) -> str:
        return (f"Rental price index, {self.base:%b %Y} = 100: measures {self.measures}. "
                f"Limitation: {self.limitation}.")


def rent_index(rents: pd.DataFrame, *, stratum_col: str = "stratum",
               weights: Mapping[str, float] | None = None) -> RentIndexResult:
    """Matched rents: in each stratum, the geometric mean of the same
    dwellings' rent relatives between consecutive periods (Jevons), chained;
    strata combined with fixed weights (the base period's total rent by
    default).

    `rents` needs `dwelling_id`, `period`, `rent` and the stratum column.
    """
    needed = {"dwelling_id", "period", "rent", stratum_col}
    missing = sorted(needed - set(rents.columns))
    if missing:
        raise HousingError(f"the rents have no {missing} column(s)")
    frame = rents.dropna(subset=list(needed)).copy()
    frame["period"] = pd.DatetimeIndex(frame["period"])
    if (frame["rent"] <= 0).any():
        raise HousingError("rents must be positive")
    periods = sorted(frame["period"].unique())
    if len(periods) < 2:
        raise HousingError("a rent index needs at least two periods")
    base = pd.Timestamp(periods[0])
    wide = frame.pivot_table(index=["dwelling_id", stratum_col], columns="period", values="rent")
    levels: dict[str, dict[pd.Timestamp, float]] = {}
    matched: dict[pd.Timestamp, int] = {base: 0}
    for stratum, group in wide.groupby(level=stratum_col):
        level = 100.0
        levels[str(stratum)] = {base: level}
        for previous, current in zip(periods[:-1], periods[1:], strict=True):
            both = group[[previous, current]].dropna()
            if len(both):
                level *= float(np.exp(np.log(both[current] / both[previous]).mean()))
            else:
                level = float("nan")
            levels[str(stratum)][pd.Timestamp(current)] = level
            matched[pd.Timestamp(current)] = matched.get(pd.Timestamp(current), 0) + len(both)
    by_stratum = pd.DataFrame(levels).sort_index()
    w = (pd.Series({str(k): float(v) for k, v in weights.items()}) if weights is not None else
         frame[frame["period"] == base].groupby(stratum_col)["rent"].sum().rename(index=str))
    w = w.reindex(by_stratum.columns).fillna(0.0)
    index = (by_stratum * w).sum(axis=1, min_count=1) / w.sum()
    return RentIndexResult(index=index, by_stratum=by_stratum,
                           matched=pd.Series(matched).sort_index(), base=base)


# ---------------------------------------------------------------------
# The four owner-occupied housing approaches
# ---------------------------------------------------------------------
def _rebase(series: pd.Series, name: str) -> tuple[pd.Series, pd.Timestamp]:
    s = pd.Series(series, dtype=float).dropna().sort_index()
    if s.empty:
        raise HousingError(f"{name} is empty")
    s.index = pd.DatetimeIndex(s.index)
    base = pd.Timestamp(s.index[0])
    if s.iloc[0] == 0:
        raise HousingError(f"{name} is zero in its first period and cannot be an index base")
    return s / float(s.iloc[0]) * 100.0, base


def rental_equivalence(rents: RentIndexResult | pd.Series) -> OOHResult:
    """The owner-occupiers' housing service at market rents: the rental
    price index, applied to the owner-occupied stock. Where rents for
    dwellings like owners' own are collected separately, pass that index."""
    series = rents.index if isinstance(rents, RentIndexResult) else rents
    index, base = _rebase(series, "the rental index")
    return OOHResult(approach="rental_equivalence", index=index,
                     components=pd.DataFrame({"rent index": index}), base=base)


def net_acquisitions(new_dwelling_prices: pd.Series, *, land_share: float = 0.0,
                     land_prices: pd.Series | None = None) -> OOHResult:
    """The price of dwellings new to the household sector.

    With `land_share` > 0 and a `land_prices` index, the land component is
    taken out -- the HICP treats land as an asset, not consumption -- and
    what remains is the structure:

        structure(t) = [P(t) - s x L(t)] / (1 - s), with P, L = 100 at base

    so a land price boom does not show up as consumer price inflation.
    """
    prices, base = _rebase(new_dwelling_prices, "the new-dwelling price index")
    notes: list[str] = []
    if land_share:
        if not 0.0 < land_share < 1.0:
            raise HousingError("the land share must be between 0 and 1")
        if land_prices is None:
            raise HousingError("excluding land needs a land price index")
        land, _ = _rebase(land_prices, "the land price index")
        land = land.reindex(prices.index)
        index = (prices - land_share * land) / (1.0 - land_share)
        notes.append(f"land excluded at a {land_share:.0%} share of the base-period price")
        components = pd.DataFrame({"new dwelling prices": prices, "land prices": land,
                                   "structure": index})
    else:
        index = prices
        notes.append("land is included: pass a land share and a land price index to exclude it")
        components = pd.DataFrame({"new dwelling prices": prices})
    return OOHResult(approach="net_acquisitions", index=index, components=components, base=base,
                     notes=tuple(notes))


def user_cost(house_prices: pd.Series, interest_rate_pct: pd.Series, *,
              depreciation_pct: float = 1.5, maintenance_pct: float = 1.0,
              tax_pct: float = 0.5, expectation_years: int = 5,
              periods_per_year: int = 4) -> OOHResult:
    """Cost per period of owning a home worth V(t):

        UC(t) = V(t) x [i(t) + d + m + tax - g(t)] / 100

    i the nominal interest rate (the forgone return), d depreciation, m
    maintenance, tax property taxes, all in percent a year, and g the
    *expected* capital gain, here the average annual house price growth over
    the previous `expectation_years` -- a smoothed, backward-looking
    expectation, stated as such because the result is highly sensitive to
    it. The realised-gain version is in `components` for comparison.

    The index is UC relative to its base-period value, times 100. When the
    expected gain exceeds the other terms the cost is negative, and so is
    the index: that is a property of the concept, not an error, and it is
    reported rather than floored.
    """
    prices, base = _rebase(house_prices, "the house price index")
    rate = pd.Series(interest_rate_pct, dtype=float)
    rate.index = pd.DatetimeIndex(rate.index)
    rate = rate.reindex(prices.index)
    if rate.isna().any():
        raise HousingError("the interest rate has no value for some house price periods")
    horizon = expectation_years * periods_per_year
    realised = ((prices / prices.shift(periods_per_year)) - 1.0) * 100.0
    expected = ((prices / prices.shift(horizon)) ** (1.0 / expectation_years) - 1.0) * 100.0
    expected = expected.fillna(realised.expanding().mean()).fillna(0.0)
    fixed = depreciation_pct + maintenance_pct + tax_pct
    rate_total = rate + fixed - expected
    cost = prices * rate_total / 100.0
    realised_cost = prices * (rate + fixed - realised.fillna(0.0)) / 100.0
    notes: list[str] = [
        f"expected capital gain: average annual house price growth over the previous "
        f"{expectation_years} years (the whole history where shorter)"]
    if (cost <= 0).any():
        notes.append(f"the user cost is zero or negative in {int((cost <= 0).sum())} period(s): "
                     "expected capital gains exceeded the cost of the funds and the upkeep")
    base_cost = float(cost.iloc[0])
    if base_cost == 0:
        raise HousingError("the user cost is zero in the base period, so it cannot be indexed")
    index = cost / base_cost * 100.0
    components = pd.DataFrame({
        "house prices": prices, "interest rate %": rate,
        "depreciation + maintenance + tax %": fixed, "expected gain %": expected,
        "user cost rate %": rate_total, "user cost": cost,
        "user cost with realised gains": realised_cost})
    return OOHResult(approach="user_cost", index=index, components=components, base=base,
                     notes=tuple(notes))


def payments(outlays: pd.DataFrame) -> OOHResult:
    """Owner-occupiers' cash outlays, summed and indexed.

    `outlays` has one column per kind of outlay (mortgage interest, repairs,
    insurance, property taxes) in money per period. A column called
    anything like capital repayment is refused: repaying the principal is
    saving, not consumption, and the payments approach excludes it.
    """
    frame = outlays.astype(float).sort_index()
    frame.index = pd.DatetimeIndex(frame.index)
    capital = [c for c in frame.columns if "capital" in str(c).lower()
               or "principal" in str(c).lower()]
    if capital:
        raise HousingError(
            f"{capital} look like repayments of mortgage principal, which are saving, not a "
            "consumption payment; the payments approach excludes them")
    total = frame.sum(axis=1, min_count=len(frame.columns))
    index, base = _rebase(total, "the total outlays")
    components = frame.assign(total=total)
    return OOHResult(approach="payments", index=index, components=components, base=base)
