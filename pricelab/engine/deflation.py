"""Deflation: real values, real wages and income, constant prices and
volumes, and purchasing power parity conversion.

A real value is a nominal value divided by a price index relative to its
level in a reference period:

    real(t) = nominal(t) / ( D(t) / D(ref) )

and it is expressed "in ref prices". Every result here carries three
things with the number, because the number means nothing without them:
which deflator was used, which reference period its prices are expressed
in, and at what frequency the two series were aligned. `DeflationResult.label`
says all three in one phrase, and every output prints it.

Alignment is explicit
---------------------
The commonest silent error in deflation is dividing a monthly series by a
quarterly deflator that some library quietly forward-filled or averaged.
Here the two series must have the same frequency and the deflator must
cover every nominal period, or `AlignmentError` is raised naming both
frequencies and the periods missing. Converting one of them is the
caller's decision, made with `to_frequency` and recorded in the result's
label -- not something this module does on anyone's behalf.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

__all__ = [
    "AlignmentError",
    "DeflationError",
    "DeflationResult",
    "PPPResult",
    "constant_prices",
    "deflate",
    "frequency_of",
    "ppp_convert",
    "price_level_index",
    "real_growth",
    "real_income",
    "real_wage",
    "to_frequency",
    "volume_index",
]

FREQUENCY_NAMES: dict[str, str] = {"M": "monthly", "Q": "quarterly", "A": "annual"}
_MONTHS: dict[str, int] = {"M": 1, "Q": 3, "A": 12}

Frequency = Literal["M", "Q", "A"]


class DeflationError(ValueError):
    """A deflation that cannot be computed as asked, and why."""


class AlignmentError(DeflationError):
    """The nominal series and the deflator do not line up period for period."""


# ---------------------------------------------------------------------
# Frequency and alignment
# ---------------------------------------------------------------------
def frequency_of(series: pd.Series, name: str = "series") -> Frequency:
    """Monthly, quarterly or annual, read off the spacing of the periods.

    Read from the data rather than from a declared frequency, because the
    mistake this guards against is a series that *says* monthly and holds
    one observation a quarter. A series whose spacing is irregular is
    refused: which frequency it "really" is would be a guess.
    """
    index = pd.DatetimeIndex(pd.Series(series).dropna().index)
    if len(index) < 2:
        raise AlignmentError(f"{name} has fewer than two periods, so its frequency is unknowable")
    months = (index.year[1:] - index.year[:-1]) * 12 + index.month[1:] - index.month[:-1]
    steps = set(np.asarray(months).tolist())
    for code, step in _MONTHS.items():
        if steps == {step}:
            return code  # type: ignore[return-value]
    raise AlignmentError(
        f"{name} is not regularly spaced (steps of {sorted(steps)} months between periods), so "
        "it cannot be aligned with anything period for period; fill or drop its gaps first")


def to_frequency(series: pd.Series, target: Frequency, *,
                 how: Literal["mean", "last", "sum"] = "mean") -> pd.Series:
    """Convert a series to a lower frequency, deliberately.

    "mean" for a price index or a rate (the quarter's average level),
    "last" for a stock observed at the period end, "sum" for a flow such as
    monthly earnings summed to a quarter. Only complete periods are kept --
    a quarter with two months is not a quarterly observation -- and going
    to a *higher* frequency is refused, since it would mean inventing the
    periods in between.
    """
    source = frequency_of(series)
    if _MONTHS[target] < _MONTHS[source]:
        raise AlignmentError(
            f"cannot convert a {FREQUENCY_NAMES[source]} series to {FREQUENCY_NAMES[target]}: "
            "that would invent the periods in between. Convert the other series down instead")
    if target == source:
        return pd.Series(series, dtype=float)
    s = pd.Series(series, dtype=float).dropna()
    index = pd.DatetimeIndex(s.index)
    span = _MONTHS[target]
    start_month = (index.month - 1) // span * span + 1
    keys = pd.DatetimeIndex([pd.Timestamp(y, m, 1) for y, m in zip(index.year, start_month,
                                                                     strict=True)])
    grouped = s.groupby(keys)
    per = _MONTHS[target] // _MONTHS[source]
    complete = grouped.count() == per
    converted = {"mean": grouped.mean(), "last": grouped.last(), "sum": grouped.sum()}[how]
    out: pd.Series = converted[complete]
    out.index.name = s.index.name
    out.name = s.name
    return out


def _align(nominal: pd.Series, deflator: pd.Series, nominal_name: str, deflator_name: str,
           allow_partial: bool) -> tuple[pd.Series, pd.Series, Frequency, tuple[str, ...]]:
    n = pd.Series(nominal, dtype=float).dropna()
    d = pd.Series(deflator, dtype=float).dropna()
    fn, fd = frequency_of(n, nominal_name), frequency_of(d, deflator_name)
    if fn != fd:
        raise AlignmentError(
            f"{nominal_name} is {FREQUENCY_NAMES[fn]} and {deflator_name} is "
            f"{FREQUENCY_NAMES[fd]}. They are not resampled silently: convert one of them "
            f"explicitly with to_frequency (for example the {FREQUENCY_NAMES[fn]} series to "
            f"{FREQUENCY_NAMES[fd]} by its mean) so the choice is yours and is recorded")
    n.index, d.index = pd.DatetimeIndex(n.index), pd.DatetimeIndex(d.index)
    missing = n.index.difference(d.index)
    notes: tuple[str, ...] = ()
    if len(missing):
        shown = ", ".join(f"{p:%Y-%m}" for p in missing[:6]) + (" ..." if len(missing) > 6 else "")
        if not allow_partial:
            raise AlignmentError(
                f"{deflator_name} has no value for {len(missing)} of {nominal_name}'s periods "
                f"({shown}); a deflator is not extrapolated. Pass allow_partial=True to deflate "
                "only the periods both series cover, which the result will record")
        notes = (f"{len(missing)} period(s) of {nominal_name} had no deflator value and were "
                 f"left out ({shown})",)
        n = n.drop(missing)
    return n, d, fn, notes


# ---------------------------------------------------------------------
# Deflation
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class DeflationResult:
    """A real series, and what it is real in terms of."""

    nominal: pd.Series
    real: pd.Series
    deflator: pd.Series
    """The deflator rebased so the reference period is 100."""
    nominal_name: str
    deflator_name: str
    reference: str
    """The reference period as printed: "Dec 2024", or "2020" for a year."""
    frequency: Frequency
    reference_periods: tuple[pd.Timestamp, ...] = ()
    """The period(s) the deflator is 100 in: one, or every period of a
    reference year."""
    kind: str = "real value"
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def unit(self) -> str:
        return f"constant {self.reference} prices"

    @property
    def label(self) -> str:
        """Which deflator, which reference period, at what frequency: the
        phrase printed beside every real number this produces."""
        return (f"{self.nominal_name}, {self.kind} in {self.unit}: deflated by "
                f"{self.deflator_name} ({self.reference} = 100), aligned "
                f"{FREQUENCY_NAMES[self.frequency]}")

    @property
    def frame(self) -> pd.DataFrame:
        return pd.DataFrame({"nominal": self.nominal,
                             f"deflator ({self.reference} = 100)": self.deflator.reindex(
                                 self.nominal.index),
                             f"real ({self.unit})": self.real})


def _reference_level(deflator: pd.Series, reference: pd.Timestamp | int | str
                     ) -> tuple[float, str, tuple[pd.Timestamp, ...]]:
    """The deflator's level in the reference period, and how to print it.

    A year (2020, or "2020") references the average over that year, which
    must be complete; a date references that period alone.
    """
    if isinstance(reference, (int, np.integer)) or (isinstance(reference, str)
                                                    and reference.isdigit()):
        year = int(reference)
        within = deflator[pd.DatetimeIndex(deflator.index).year == year]
        expected = 12 // _MONTHS[frequency_of(deflator, "the deflator")]
        if len(within) != expected:
            raise DeflationError(
                f"reference year {year} needs all {expected} periods of the deflator to average "
                f"over; it has {len(within)}")
        return (float(within.mean()), str(year),
                tuple(pd.Timestamp(t) for t in within.index))
    when = pd.Timestamp(reference)
    if when not in deflator.index:
        raise DeflationError(
            f"the reference period {when:%Y-%m} is not in the deflator; real values are "
            "expressed in the prices of a period the deflator actually observes")
    label = f"{when:%b %Y}" if frequency_of(deflator, "the deflator") != "A" else f"{when:%Y}"
    return float(deflator.loc[when]), label, (when,)


def deflate(nominal: pd.Series, deflator: pd.Series, *,
            reference: pd.Timestamp | int | str,
            nominal_name: str = "nominal series", deflator_name: str = "the deflator",
            allow_partial: bool = False, kind: str = "real value",
            conversion_note: str = "") -> DeflationResult:
    """real(t) = nominal(t) / (D(t) / D(ref)), with explicit alignment.

    Raises `AlignmentError` on a frequency mismatch or on nominal periods
    the deflator does not cover (unless `allow_partial`), and
    `DeflationError` on a reference period the deflator does not observe.
    `conversion_note` records any frequency conversion the caller made
    before calling, so it travels in the result's notes.
    """
    n, d, frequency, notes = _align(nominal, deflator, nominal_name, deflator_name,
                                    allow_partial)
    if (d <= 0).any():
        raise DeflationError(f"{deflator_name} has zero or negative values; a price index "
                             "cannot be divided by")
    level, printed, periods = _reference_level(d, reference)
    rebased = d / level * 100.0
    real = n / (rebased.loc[n.index] / 100.0)
    return DeflationResult(
        nominal=n.rename("nominal"), real=real.rename("real"), deflator=rebased.rename("deflator"),
        nominal_name=nominal_name, deflator_name=deflator_name, reference=printed,
        frequency=frequency, reference_periods=periods, kind=kind,
        notes=((conversion_note,) if conversion_note else ()) + notes)


def real_wage(nominal_wage: pd.Series, consumer_prices: pd.Series, *,
              reference: pd.Timestamp | int | str, wage_name: str = "average earnings",
              index_name: str = "the consumer price index", **kwargs: Any) -> DeflationResult:
    """A wage deflated by consumer prices: what the wage buys, in the
    reference period's prices."""
    return deflate(nominal_wage, consumer_prices, reference=reference, nominal_name=wage_name,
                   deflator_name=index_name, kind="real wage", **kwargs)


def real_income(nominal_income: pd.Series, consumer_prices: pd.Series, *,
                reference: pd.Timestamp | int | str, income_name: str = "household income",
                index_name: str = "the consumer price index", **kwargs: Any) -> DeflationResult:
    """Income deflated by consumer prices: purchasing power over time."""
    return deflate(nominal_income, consumer_prices, reference=reference,
                   nominal_name=income_name, deflator_name=index_name, kind="real income",
                   **kwargs)


def real_growth(result: DeflationResult, horizon: int = 1) -> pd.DataFrame:
    """Nominal, price and real growth over `horizon` periods, exactly.

        (1 + real) = (1 + nominal) / (1 + inflation)

    The familiar "nominal minus inflation" is shown beside it with the gap,
    because the approximation is poor exactly when it matters -- when
    inflation is high -- and a reader should see by how much.
    """
    nominal = result.nominal / result.nominal.shift(horizon) - 1.0
    deflator = result.deflator.reindex(result.nominal.index)
    inflation = deflator / deflator.shift(horizon) - 1.0
    real = (1.0 + nominal) / (1.0 + inflation) - 1.0
    approximate = nominal - inflation
    return pd.DataFrame({
        "nominal_growth_pct": nominal * 100.0,
        "inflation_pct": inflation * 100.0,
        "real_growth_pct": real * 100.0,
        "approximation_pct": approximate * 100.0,
        "approximation_error_pp": (approximate - real) * 100.0,
    })


def constant_prices(current_prices: pd.Series, price_index: pd.Series, *,
                    reference: pd.Timestamp | int | str, name: str = "current price value",
                    index_name: str = "the price index", **kwargs: Any) -> DeflationResult:
    """A current-price value series revalued at the reference period's prices.

    Fixed-base: one reference period throughout. Chain-linked volumes, which
    re-weight every year and so are not additive across components, are
    not produced here; see docs/methodology/deflation.md.
    """
    return deflate(current_prices, price_index, reference=reference, nominal_name=name,
                   deflator_name=index_name, kind="constant price value", **kwargs)


def volume_index(result: DeflationResult) -> pd.Series:
    """The constant-price series as an index, reference period = 100: the
    volume measure, movement in quantity with price movement removed."""
    covered = [t for t in result.reference_periods if t in result.real.index]
    if len(covered) != len(result.reference_periods):
        raise DeflationError(
            f"the constant-price series does not cover the reference period {result.reference}, "
            "so it has no level there to express the volume index relative to")
    base = float(result.real.loc[covered].mean())
    return (result.real / base * 100.0).rename(f"volume index ({result.reference} = 100)")


# ---------------------------------------------------------------------
# Purchasing power parities
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class PPPResult:
    converted: pd.Series
    values: pd.Series
    ppp: pd.Series
    value_name: str
    ppp_name: str
    currency: str
    notes: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return (f"{self.value_name} converted at {self.ppp_name}, in {self.currency}"
                + (f"; {self.notes[0]}" if self.notes else ""))


def ppp_convert(values: pd.Series, ppp: pd.Series, *, value_name: str = "local currency values",
                ppp_name: str = "purchasing power parities",
                currency: str = "international dollars",
                broadcast_annual: bool = False) -> PPPResult:
    """values(t) / PPP(t): local currency to a common currency at purchasing
    power parity, so the result compares volumes rather than exchange
    rates.

    PPPs are published annually. A monthly or quarterly series converted
    with them has to hold each year's PPP constant through the year, and
    that is a choice: it is refused unless `broadcast_annual=True`, and
    when made it is recorded in the label.
    """
    v = pd.Series(values, dtype=float).dropna()
    p = pd.Series(ppp, dtype=float).dropna()
    v_periods, p_periods = pd.DatetimeIndex(v.index), pd.DatetimeIndex(p.index)
    v.index, p.index = v_periods, p_periods
    fv, fp = frequency_of(v, value_name), frequency_of(p, ppp_name)
    notes: tuple[str, ...] = ()
    if fv != fp:
        if not (fp == "A" and broadcast_annual):
            raise AlignmentError(
                f"{value_name} is {FREQUENCY_NAMES[fv]} and {ppp_name} are "
                f"{FREQUENCY_NAMES[fp]}. Holding each year's PPP constant through the year is "
                "a choice, not a default: pass broadcast_annual=True to make it, and it will be "
                "stated with the result")
        by_year = {int(year): float(val)
                   for year, val in zip(p_periods.year, p.to_numpy(dtype=float), strict=True)}
        missing = sorted({int(y) for y in v_periods.year} - set(by_year))
        if missing:
            raise AlignmentError(f"no {ppp_name} for {missing}")
        p = pd.Series([by_year[int(year)] for year in v_periods.year], index=v_periods)
        notes = ("each year's annual PPP held constant through the year",)
    else:
        missing_periods = v_periods.difference(p_periods)
        if len(missing_periods):
            raise AlignmentError(f"no {ppp_name} for {len(missing_periods)} period(s) of "
                                 f"{value_name}")
        p = p.loc[v_periods]
    if (p <= 0).any():
        raise DeflationError(f"{ppp_name} has zero or negative values")
    return PPPResult(converted=(v / p).rename("converted"), values=v, ppp=p,
                     value_name=value_name, ppp_name=ppp_name, currency=currency, notes=notes)


def price_level_index(ppp: pd.Series, exchange_rate: pd.Series) -> pd.Series:
    """PPP over the market exchange rate, times 100: above 100 the country
    is more expensive than the reference, below it cheaper. Both series
    must be in local currency per unit of the same reference currency."""
    p = pd.Series(ppp, dtype=float).dropna()
    x = pd.Series(exchange_rate, dtype=float).dropna()
    if frequency_of(p, "the PPP series") != frequency_of(x, "the exchange rate"):
        raise AlignmentError("the PPPs and the exchange rate must have the same frequency")
    common = pd.DatetimeIndex(p.index).intersection(pd.DatetimeIndex(x.index))
    if common.empty:
        raise AlignmentError("the PPPs and the exchange rate share no period")
    return (p.loc[common] / x.loc[common] * 100.0).rename("price level index")
