"""Multilateral index methods for transaction and scanner data, and the
window-extension rules that turn a fixed window into a published series.

A bilateral index compares two periods. Chaining bilateral comparisons over
a long span is how most published indices are built, and on collected price
quotes it behaves: the basket barely moves month to month, so the chained
number and the direct number stay close. Scanner data breaks that. Products
enter and leave continuously, prices bounce between a shelf price and a sale
price, and quantities respond violently to the bounce -- a sale sells ten
times the units. A chained superlative index over data like that does not
merely approximate the direct comparison, it *drifts*: the CPI Manual 2020's
Table 10.1 shows a chained Tornqvist ending 22 percent below the direct one
on data where every price and every quantity returned exactly to where it
started. Nothing changed and the index said it had.

A multilateral method removes the choice of path by construction. It takes a
window of T periods, estimates all T levels simultaneously against the whole
window, and is therefore *transitive*: the comparison between any two periods
in the window does not depend on which periods lie between them, so there is
no path to drift along. What it costs is the thing that makes a published
index publishable -- a number that never changes once released. Adding a
period changes the window, and a new window re-estimates every level in it.
That is what the extension rules below negotiate: which movement from the new
window to graft onto the series already published, and therefore which part of
the revision to accept and which to discard.

The methods, and what each assumes:

GEKS-Fisher        Every pair of periods in the window is compared by a
                   Fisher index; the answer for a pair is the geometric mean
                   of the comparisons through every possible bridge period.
                   Assumes only that the bilateral building block is a
                   sensible comparison. Matched-model: an item must be in
                   both periods of a pair to enter that pair.
GEKS-Tornqvist     The same, with Tornqvist as the building block. Known in
(CCDI)             the productivity literature as CCDI, after Caves,
                   Christensen and Diewert and Inklaar and Timmer.
TPD                One regression of log price on period dummies and product
                   dummies over the whole window. Uses every observation, not
                   only matched pairs, so a product priced in one period
                   still contributes -- the product dummy carries its quality
                   difference. Assumes an item's quality is constant over the
                   window (that is exactly what a single product dummy says).
WTPD               TPD with expenditure share weights, so a product that sells
                   moves the index more than one that does not. Each period is
                   normalised to total weight one, so no period outvotes
                   another by being a bigger trading month.
TDH                The same regression with characteristics in place of
                   product dummies: quality is what the characteristics say it
                   is, so a product never seen before is still priced. Assumes
                   the characteristics explain quality and their valuation is
                   constant over the window. Fitted by `engine.hedonic`, whose
                   diagnostics (VIF, condition number, out-of-sample error)
                   are the evidence for that assumption.
Geary-Khamis       Solves simultaneously for a reference price per product
                   (a quantity-weighted average of its deflated prices) and a
                   price level per period (the value of the period's basket at
                   reference prices). Additive and therefore consistent in
                   aggregation, which is why it is favoured where the index
                   must decompose; the price is a stronger substitution
                   assumption than the superlative methods make.

None of them is right. The choice between them moves a published rate by
tenths of a point on real scanner data, which is why `method_comparison`
exists and why the interface puts the spread in front of the user rather than
a single number: the method is a judgement, and a judgement the reader cannot
see is a judgement nobody made.

Sources: CPI Manual 2020 chapters 7 (paras 7.128-7.187 on multilateral
methods) and 10 (paras 10.26-10.45 on scanner data and drift); Eurostat's
*Guide on Multilateral Methods in the HICP* (2022) for the extension rules
and their names; Chessa (2016) for FBEW as implemented by Statistics
Netherlands; de Haan and Krsinich (2018) for the weighted time product dummy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import pandas as pd

from .splicing import ChainDriftReport, chain_drift, rebase

#: The multilateral methods this engine implements, in the order the
#: comparison view lists them.
METHODS: tuple[str, ...] = (
    "geks_fisher", "geks_tornqvist", "tpd", "wtpd", "tdh", "geary_khamis")

#: Short labels for the interface and the reports.
METHOD_LABELS: dict[str, str] = {
    "geks_fisher": "GEKS-Fisher",
    "geks_tornqvist": "GEKS-Tornqvist (CCDI)",
    "tpd": "Time product dummy",
    "wtpd": "Weighted time product dummy",
    "tdh": "Time dummy hedonic",
    "geary_khamis": "Geary-Khamis",
}

#: What each method needs beyond a price and a period, so a caller can say
#: why a method is unavailable rather than hiding it.
METHOD_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "geks_fisher": ("quantities",),
    "geks_tornqvist": ("quantities",),
    "tpd": (),
    "wtpd": ("quantities",),
    "tdh": ("characteristics",),
    "geary_khamis": ("quantities",),
}

#: The window-extension rules, in increasing order of how much of the new
#: window's re-estimation they let through.
SPLICES: tuple[str, ...] = ("movement", "window", "half", "mean", "fbew", "fbmw")

SPLICE_LABELS: dict[str, str] = {
    "movement": "Movement splice",
    "window": "Window splice",
    "half": "Half splice",
    "mean": "Mean splice",
    "fbew": "FBEW (fixed base, expanding window)",
    "fbmw": "FBMW (fixed base, monthly window)",
}

#: Twenty-five months: a full two years plus the month being added, so a
#: monthly window always holds two observations of every seasonal month and
#: a seasonal product is never compared only against its own high season.
#: The Eurostat guide's default and the one most offices publish on.
DEFAULT_WINDOW = 25

#: Calendar month that restarts the window under FBEW and FBMW. December,
#: so the expanding window runs January to December within a year and the
#: annual re-anchoring coincides with the usual weight update.
DEFAULT_ANCHOR_MONTH = 12


class MultilateralError(ValueError):
    """Raised when the data cannot support the method asked for."""


@dataclass(frozen=True)
class MultilateralResult:
    """One multilateral index over one window."""

    index: pd.Series
    """Level series over the window's periods, `base_value` at the first."""
    method: str
    n_periods: int
    n_items: int
    """Distinct products contributing at least one observation."""
    n_observations: int
    parameters: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, float] = field(default_factory=dict)
    """Whatever the method can say about its own fit: the regression's
    R-squared, Geary-Khamis's iteration count, the share of the window's
    expenditure that the matched-model methods had to discard."""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtensionResult:
    """A published series built by extending a fixed window period by period."""

    index: pd.Series
    """The published series: the first window in full, then one spliced
    value per subsequent period."""
    method: str
    splice: str
    window: int
    n_windows: int
    splice_spread_pp: pd.Series
    """Per published period, the gap in index points between the highest and
    lowest level that period could have taken had the link been made at a
    different period of the overlap.

    This is the size of the judgement the splice made, and it is the only
    honest way to report it: a "revision against the previous window"
    measured at the splice point is zero by construction for whichever rule
    was used, which would flatter every choice equally. Zero here means the
    new window agreed with the published series everywhere in the overlap,
    and the choice of splice genuinely did not matter on this data."""
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------
# Panel preparation
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class Panel:
    """A window of transaction data as dense period x item matrices.

    Dense rather than long because every method here is a whole-window
    computation that touches each cell at least once: GEKS reads every pair
    of rows, the regressions read every cell, and Geary-Khamis reads all of
    them on every iteration. A 25-period window over even twenty thousand
    products is four megabytes of float, and paying that once is cheaper
    than re-filtering a long frame inside three nested loops.

    Missing cells are NaN and mean exactly one thing: that product was not
    sold in that period. Every method treats them explicitly -- the
    matched-model ones drop them pairwise, the regressions simply have no
    observation there -- and none of them imputes.
    """

    prices: np.ndarray
    """periods x items, NaN where unsold. Non-positive prices are NaN'd on
    construction: a log-price regression and a price relative both need a
    positive number, and a zero here is a sentinel, not a giveaway."""
    quantities: np.ndarray | None
    """periods x items, aligned to `prices`, or None for a price-only panel."""
    periods: pd.DatetimeIndex
    items: pd.Index
    quantity_source: str | None
    """"quantity" or "quantity_derived" -- which the quantities came from,
    carried so a result can say whether quantities were reported or divided
    out of expenditure."""

    @property
    def n_periods(self) -> int:
        return len(self.periods)

    @property
    def n_items(self) -> int:
        return len(self.items)

    @property
    def n_observations(self) -> int:
        return int(np.isfinite(self.prices).sum())

    @property
    def churn_share(self) -> float:
        """Share of the window's cells that are empty: 0.0 for a panel where
        every product is sold in every period, approaching 1.0 for a scanner
        file whose products turn over completely. The number that decides
        whether a matched-model method has anything left to work with."""
        total = self.n_periods * self.n_items
        return 1.0 - (self.n_observations / total) if total else float("nan")

    def subset(self, periods: Sequence[pd.Timestamp]) -> Panel:
        """The same panel restricted to `periods`, keeping only products
        sold in at least one of them."""
        # `get_loc` on a unique DatetimeIndex returns an int; the declared
        # return type is broader (it covers duplicate and monotonic indexes,
        # where a slice or a mask comes back), and `build_panel` unstacks a
        # groupby so this index cannot have duplicates. The cast records that.
        positions = np.array([cast(int, self.periods.get_loc(p)) for p in periods], dtype=int)
        prices = self.prices[positions, :]
        keep = np.isfinite(prices).any(axis=0)
        return Panel(
            prices=prices[:, keep],
            quantities=None if self.quantities is None
            else self.quantities[positions, :][:, keep],
            periods=pd.DatetimeIndex(list(periods)),
            items=self.items[keep],
            quantity_source=self.quantity_source)


def build_panel(df: pd.DataFrame, price_col: str = "price_imputed", *,
                period_col: str = "period", item_col: str = "item_id") -> Panel:
    """Pivot a long price panel into `Panel`'s matrices.

    Duplicate (period, item) rows -- the ordinary shape of transaction data,
    where one product sells in several stores or several times a day -- are
    collapsed to the unit value: total expenditure over total quantity,
    which is the only defensible single price for a product-period and the
    one every source on scanner data specifies. Without quantities there is
    nothing to weight by and the mean is taken instead, with that stated in
    `quantity_source`.
    """
    from .index import quantity_series

    if period_col not in df.columns or item_col not in df.columns:
        raise MultilateralError(
            f"a multilateral index needs {period_col!r} and {item_col!r} columns; "
            f"this frame has {list(df.columns)[:8]}")
    price = price_col if price_col in df.columns else "price_reported"
    if price not in df.columns:
        raise MultilateralError(
            f"neither {price_col!r} nor 'price_reported' is a column of this frame")

    frame = pd.DataFrame({
        "period": pd.to_datetime(df[period_col]),
        "item": df[item_col].astype(str),
        "price": pd.to_numeric(df[price], errors="coerce"),
    })
    quantities = quantity_series(df, price)
    source = None if quantities is None else str(quantities.name)
    if quantities is not None:
        frame["quantity"] = pd.to_numeric(quantities, errors="coerce").to_numpy()

    frame = frame[np.isfinite(frame["price"]) & (frame["price"] > 0)]
    if frame.empty:
        raise MultilateralError(
            "no positive, finite prices in this collection, so there is nothing to index")

    if quantities is not None:
        frame = frame[np.isfinite(frame["quantity"]) & (frame["quantity"] >= 0)]
        if frame.empty:
            raise MultilateralError(
                "every row carries a price but no usable quantity; a quantity-weighted "
                "multilateral method has nothing to weight by")
        frame["expenditure"] = frame["price"] * frame["quantity"]
        grouped = frame.groupby(["period", "item"], sort=True)[["expenditure", "quantity"]].sum()
        with np.errstate(divide="ignore", invalid="ignore"):
            unit_value = grouped["expenditure"] / grouped["quantity"]
        # A product-period whose whole quantity was zero has an undefined
        # unit value. It carried no expenditure either, so it is not an
        # observation of anything and is dropped rather than imputed.
        unit_value = unit_value.where(np.isfinite(unit_value) & (unit_value > 0))
        prices_long = unit_value.rename("price")
        quantities_long = grouped["quantity"].where(unit_value.notna())
    else:
        prices_long = frame.groupby(["period", "item"], sort=True)["price"].mean()
        quantities_long = None

    price_matrix = prices_long.unstack("item").sort_index()
    periods = pd.DatetimeIndex(price_matrix.index)
    items = pd.Index(price_matrix.columns, name="item")
    quantity_matrix = (None if quantities_long is None
                       else quantities_long.unstack("item")
                       .reindex(index=price_matrix.index, columns=price_matrix.columns)
                       .to_numpy(dtype=float))
    return Panel(prices=price_matrix.to_numpy(dtype=float), quantities=quantity_matrix,
                 periods=periods, items=items, quantity_source=source)


def _require_quantities(panel: Panel, method: str) -> np.ndarray:
    if panel.quantities is None:
        raise MultilateralError(
            f"{METHOD_LABELS.get(method, method)} weights each product by what it sold, and "
            "this collection carries no quantity or expenditure column; map one on upload, "
            "or use the unweighted time product dummy, which needs prices only")
    return panel.quantities


def _expenditure_shares(prices: np.ndarray, quantities: np.ndarray) -> np.ndarray:
    """Share of each period's expenditure by product, NaN where unsold.

    Normalised within the period, so every period carries total weight one:
    a regression weighted by raw expenditure would let a big trading month
    outvote a small one on the *price* level, which is not a statement about
    prices at all.
    """
    expenditure = prices * quantities
    totals = np.nansum(np.where(np.isfinite(expenditure), expenditure, 0.0), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        shares = expenditure / totals[:, None]
    return np.where(np.isfinite(shares), shares, np.nan)


# ---------------------------------------------------------------------
# GEKS
# ---------------------------------------------------------------------
def _bilateral_log_matrix(panel: Panel, block: str, min_matched_items: int
                          ) -> tuple[np.ndarray, list[int]]:
    """Log bilateral index between every ordered pair of periods, and the
    list of periods usable as bridges.

    Returns `L` with `L[s, t] = ln P(s -> t)`, NaN where the pair has fewer
    than `min_matched_items` products in common. Only the upper triangle is
    computed: every bilateral here satisfies time reversal exactly (Fisher
    and Tornqvist both do), so `L[t, s] = -L[s, t]` is an identity rather
    than a second computation, and using it keeps the matrix exactly
    antisymmetric -- which is what makes the GEKS result below exactly
    transitive rather than transitive to within rounding.
    """
    from . import bilateral as bl

    quantities = _require_quantities(panel, f"geks_{block}")
    fn = bl.fisher if block == "fisher" else bl.tornqvist
    T = panel.n_periods
    L = np.full((T, T), np.nan)
    np.fill_diagonal(L, 0.0)
    items = panel.items
    for s in range(T):
        p0 = pd.Series(panel.prices[s], index=items)
        q0 = pd.Series(quantities[s], index=items)
        for t in range(s + 1, T):
            result = fn(p0, pd.Series(panel.prices[t], index=items),
                        q0, pd.Series(quantities[t], index=items))
            if result.n_items < min_matched_items or not np.isfinite(result.value) \
                    or result.value <= 0:
                continue
            L[s, t] = float(np.log(result.value))
            L[t, s] = -L[s, t]

    bridges = [l for l in range(T) if np.isfinite(L[l]).all()]
    return L, bridges


def geks(panel: Panel, block: str = "fisher", *, base_value: float = 100.0,
         min_matched_items: int = 2) -> MultilateralResult:
    """GEKS over the whole window, with Fisher or Tornqvist as the block.

        P_GEKS(s, t) = prod over bridges l of [ P(l, t) / P(l, s) ] ^ (1/L)

    Read it as: compare s and t through every period in turn and take the
    geometric mean of the answers. Written in logs it collapses to a level
    per period,

        g(t) = (1/L) * sum over bridges l of ln P(l, t)

    with ln P_GEKS(s, t) = g(t) - g(s), which is where transitivity comes
    from -- the comparison between any two periods is a difference of two
    fixed numbers, so no path between them exists to accumulate drift along.
    CPI Manual 2020, paragraphs 7.137-7.146.

    Bias: the matched-model one. A product must be sold in both periods of a
    pair to enter that pair's comparison, so a window where products turn
    over fast discards the very transactions that motivated using scanner
    data. `diagnostics["matched_expenditure_share"]` says how much survived;
    where it is low, TPD or TDH -- which use unmatched observations -- are
    the answer, not a different splice.

    A period that cannot be compared to every other (nothing in common with
    some other period in the window) is excluded from the bridge set rather
    than allowed to make the matrix ragged, because averaging over a
    different bridge set per period is precisely what would destroy
    transitivity. It still receives a level, computed over the common
    bridges; the exclusion is reported in `warnings`.
    """
    if block not in ("fisher", "tornqvist"):
        raise MultilateralError(
            f"the GEKS building block must be 'fisher' or 'tornqvist', not {block!r}")
    method = f"geks_{block}"
    if panel.n_periods < 2:
        raise MultilateralError(
            "a multilateral index needs at least two periods in the window")

    L, bridges = _bilateral_log_matrix(panel, block, min_matched_items)
    warnings_: list[str] = []
    if not bridges:
        raise MultilateralError(
            "no period in this window can be compared to every other by a matched-model "
            f"bilateral index with at least {min_matched_items} products in common, so GEKS "
            "has no bridge to average over. The window is not connected: shorten it, or use "
            "the time product dummy, which does not need matched pairs.")
    if len(bridges) < panel.n_periods:
        missing = [f"{panel.periods[l]:%Y-%m}" for l in range(panel.n_periods)
                   if l not in bridges]
        warnings_.append(
            f"{len(missing)} of {panel.n_periods} periods could not be compared to every "
            f"other and were excluded from the bridge set ({', '.join(missing[:6])}"
            f"{'...' if len(missing) > 6 else ''}); every period still receives a level, "
            "averaged over the remaining bridges")

    g = np.nanmean(L[bridges, :], axis=0)
    levels = base_value * np.exp(g - g[0])
    index = pd.Series(levels, index=panel.periods, name=method)

    diagnostics = {
        "bridge_periods": float(len(bridges)),
        "pairs_compared": float(np.isfinite(L).sum() - panel.n_periods),
        "churn_share": panel.churn_share,
    }
    if panel.quantities is not None:
        diagnostics["matched_expenditure_share"] = _matched_expenditure_share(panel)
    return MultilateralResult(
        index=index, method=method, n_periods=panel.n_periods, n_items=panel.n_items,
        n_observations=panel.n_observations,
        parameters={"block": block, "min_matched_items": min_matched_items,
                    "base_value": base_value},
        diagnostics=diagnostics, warnings=tuple(warnings_))


def _matched_expenditure_share(panel: Panel) -> float:
    """Share of the window's expenditure carried by products sold in every
    period of it -- what a strictly matched-model comparison over the whole
    window would be left with. The single number that says whether a
    matched-model method is appropriate to this data at all."""
    assert panel.quantities is not None
    expenditure = panel.prices * panel.quantities
    total = float(np.nansum(expenditure))
    if total <= 0:
        return float("nan")
    always = np.isfinite(panel.prices).all(axis=0)
    return float(np.nansum(expenditure[:, always]) / total)


# ---------------------------------------------------------------------
# Time product dummy and its weighted form
# ---------------------------------------------------------------------
def time_product_dummy(panel: Panel, *, weighted: bool = False,
                       base_value: float = 100.0) -> MultilateralResult:
    """One regression over the whole window:

        ln p(i, t) = alpha + delta(t) + gamma(i) + e(i, t)

    with delta(0) = 0 and gamma for the first product absorbed into alpha.
    The index is exp(delta(t)). CPI Manual 2020, paragraphs 7.164-7.172.

    What it buys over GEKS is every observation. A matched-model method can
    only use a product sold in both periods being compared; this uses a
    product sold in *any* period, because the product dummy gamma(i) carries
    whatever makes that product dearer or cheaper than the others and the
    period dummies are then free to read price change off everything that
    was sold. On a scanner window where two-thirds of turnover is in
    products that did not exist at the window's start, that is the
    difference between an index and an anecdote.

    What it costs is the assumption inside gamma(i): one number per product
    for the whole window, so a product whose quality changed is asserted not
    to have. Over twenty-five months that is a real assumption, and it is
    the reason the window is not simply made longer.

    `weighted` (WTPD, de Haan and Krsinich) weights each observation by its
    share of its own period's expenditure, so the index follows what sold
    rather than what was listed; the unweighted form gives a clearance line
    with one unit sold the same say as the product beside it with ten
    thousand. Each period's weights sum to one, so no period dominates.

    Solved as a sparse least-squares problem: the design matrix is one
    column per period and one per product, which on a real window is tens of
    thousands of columns and 99.99 percent zeros. Dense, that is gigabytes
    and minutes; sparse, it is a second.
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.linalg import lsqr

    method = "wtpd" if weighted else "tpd"
    T, N = panel.n_periods, panel.n_items
    if T < 2:
        raise MultilateralError(
            "a multilateral index needs at least two periods in the window")
    rows, cols = np.nonzero(np.isfinite(panel.prices))
    if rows.size == 0:
        raise MultilateralError("no usable prices in this window")
    y = np.log(panel.prices[rows, cols])

    if weighted:
        quantities = _require_quantities(panel, method)
        shares = _expenditure_shares(panel.prices, quantities)
        w = shares[rows, cols]
        w = np.where(np.isfinite(w) & (w > 0), w, 0.0)
        if not np.any(w > 0):
            raise MultilateralError(
                "every expenditure share in this window is zero or undefined, so a weighted "
                "regression has nothing to weight by")
    else:
        w = np.ones(rows.size)

    # Columns: [intercept] [period 1..T-1] [item 1..N-1]. Period 0 and item 0
    # are the reference levels; dropping them is what makes the design full
    # rank, and the index reads off the period columns as a ratio to period 0
    # regardless of which product was chosen as the reference.
    # Period p contributes a 1 to column p (for p >= 1; period 0 is the
    # reference and contributes only to the intercept); product i a 1 to
    # column T + i - 1 (for i >= 1; product 0 likewise). Building both blocks
    # by arithmetic and masking out the two reference levels is cheaper, and
    # a great deal clearer, than assembling per-row index lists in Python.
    n_params = 1 + (T - 1) + (N - 1)
    block = rows.size
    entry_rows = np.concatenate([np.arange(block)] * 3)
    entry_cols = np.concatenate([
        np.zeros(block, dtype=int),             # intercept
        rows,                                   # period dummy
        T + cols - 1,                           # product dummy
    ])
    keep = np.ones(entry_rows.size, dtype=bool)
    keep[block:2 * block] = rows > 0
    keep[2 * block:] = cols > 0
    values = np.ones(entry_rows.size)
    A = csr_matrix((values[keep], (entry_rows[keep], entry_cols[keep])),
                   shape=(rows.size, n_params))

    sqrt_w = np.sqrt(w)
    A = csr_matrix(A.multiply(sqrt_w[:, None]))
    b = y * sqrt_w
    # atol/btol at machine-ish precision: the identity and proportionality
    # axioms are exact statements about this estimator, and a loose solver
    # tolerance would turn them into approximate ones.
    solution = lsqr(A, b, atol=1e-14, btol=1e-14, conlim=1e12,
                    iter_lim=max(2000, 20 * n_params))
    beta = np.asarray(solution[0], dtype=float)

    deltas = np.zeros(T)
    deltas[1:] = beta[1:T]
    levels = base_value * np.exp(deltas - deltas[0])
    index = pd.Series(levels, index=panel.periods, name=method)

    residuals = b - A @ beta
    ss_res = float(residuals @ residuals)
    mean = float((b * sqrt_w).sum() / (w.sum())) if w.sum() else 0.0
    centred = (y - mean) * sqrt_w
    ss_tot = float(centred @ centred)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    diagnostics = {
        "r_squared": r_squared,
        "n_parameters": float(n_params),
        "degrees_of_freedom": float(rows.size - n_params),
        "churn_share": panel.churn_share,
        "solver_iterations": float(solution[2]),
    }
    warnings_: list[str] = []
    if rows.size <= n_params:
        warnings_.append(
            f"{rows.size} observations against {n_params} parameters: the regression is "
            "saturated or under-determined, and the levels it returns fit the data exactly "
            "without evidence for any of them. Shorten the window or aggregate the products.")
    return MultilateralResult(
        index=index, method=method, n_periods=T, n_items=N,
        n_observations=panel.n_observations,
        parameters={"weighted": weighted, "base_value": base_value},
        diagnostics=diagnostics, warnings=tuple(warnings_))


# ---------------------------------------------------------------------
# Time dummy hedonic
# ---------------------------------------------------------------------
def time_dummy_hedonic(df: pd.DataFrame, spec: Any, *, base_value: float = 100.0,
                       price_col: str = "price_imputed") -> MultilateralResult:
    """The time product dummy with characteristics in place of product
    dummies, fitted by `engine.hedonic`.

    Swapping gamma(i) for a function of the characteristics is what lets the
    method price a product it has never seen: quality is whatever the
    characteristics say, so a new product entering the window contributes
    from its first period rather than waiting for a second one. That is the
    strongest assumption of the six -- the characteristics must explain
    quality, and their valuation must hold still over the window -- and it
    is why this returns the fit's own diagnostics alongside the index: the
    VIF, the condition number and the out-of-sample error are the evidence
    for the assumption, and a reader who does not see them is being asked
    to take it on trust.

    The regression, the dummy encoding, the robust standard errors and the
    Kennedy bias correction are all `engine.hedonic`'s, tested there; this
    only presents its `time_dummy_index` as a multilateral result so the
    comparison view can put it beside the other five.
    """
    from .hedonic import HedonicError, HedonicSpec, fit_hedonic

    if not isinstance(spec, HedonicSpec):
        raise MultilateralError(
            "time dummy hedonic needs a HedonicSpec naming the characteristics to regress on")
    frame = df.copy()
    if spec.price_col not in frame.columns and price_col in frame.columns:
        frame[spec.price_col] = frame[price_col]
    try:
        fit = fit_hedonic(frame, spec, variant="time_dummy")
    except HedonicError as exc:
        raise MultilateralError(f"the time dummy hedonic regression could not be fitted: {exc}"
                                ) from exc
    levels = fit.time_dummy_index()
    index = (levels / float(levels.iloc[0]) * base_value).rename("tdh")
    return MultilateralResult(
        index=index, method="tdh", n_periods=len(index),
        n_items=int(frame[spec.item_col].nunique()) if spec.item_col in frame.columns else 0,
        n_observations=fit.n_obs,
        parameters={"characteristics": list(spec.characteristics),
                    "categorical": list(spec.categorical),
                    "functional_form": spec.functional_form, "base_value": base_value},
        diagnostics={"r_squared": fit.r_squared, "adj_r_squared": fit.adj_r_squared,
                     "condition_number": fit.condition_number,
                     "n_parameters": float(fit.n_params)},
        warnings=tuple(fit.warnings))


# ---------------------------------------------------------------------
# Geary-Khamis
# ---------------------------------------------------------------------
def geary_khamis(panel: Panel, *, base_value: float = 100.0, tolerance: float = 1e-12,
                 max_iterations: int = 500) -> MultilateralResult:
    """Reference prices and period levels, solved together.

        b(i) = sum over t of [ q(i,t) / sum over s of q(i,s) ] * p(i,t) / P(t)
        P(t) = sum over i of p(i,t) q(i,t) / sum over i of b(i) q(i,t)

    b(i) is product i's *reference price*: a quantity-weighted average of
    its deflated prices, i.e. what the product is worth once price change
    has been taken out of it, which is a quality measure derived from the
    data rather than asserted from characteristics. P(t) is then the period's
    basket valued at actual prices over the same basket valued at reference
    prices. Each needs the other, so the pair is iterated from P = 1 until
    it stops moving. CPI Manual 2020, paragraphs 7.147-7.163.

    The property that makes it worth the trouble is additivity: because
    every period's quantities are valued at one common set of prices, the
    index decomposes exactly into product and group contributions that sum
    to the total. None of the superlative methods does, which is why an
    office that must publish contributions alongside the headline tends to
    end up here.

    The price of that additivity is a substitution assumption stronger than
    Fisher's or Tornqvist's: one fixed reference price per product across
    the window says the relative valuation of products does not respond to
    the relative prices they trade at. On data where a product is bought
    *because* it is on sale, that is exactly the thing being assumed away,
    and the gap between this and GEKS-Fisher in the comparison view is the
    size of the assumption.

    Convergence: the iteration is a positive-matrix fixed point and in
    practice converges linearly; the rare window that does not settle within
    `max_iterations` returns its last iterate with the failure in
    `warnings`, rather than raising, because a method that silently vanishes
    from a comparison view is worse than one that reports its own doubt.
    """
    quantities = _require_quantities(panel, "geary_khamis")
    if panel.n_periods < 2:
        raise MultilateralError(
            "a multilateral index needs at least two periods in the window")

    prices = np.where(np.isfinite(panel.prices), panel.prices, np.nan)
    q = np.where(np.isfinite(quantities) & np.isfinite(prices), quantities, 0.0)
    p = np.where(np.isfinite(prices), prices, 0.0)

    item_totals = q.sum(axis=0)
    usable = item_totals > 0
    if not usable.any():
        raise MultilateralError(
            "every product in this window sold zero units, so there is no basket to value")
    p, q, item_totals = p[:, usable], q[:, usable], item_totals[usable]
    shares = q / item_totals[None, :]          # product i's quantity split over periods

    P = np.ones(panel.n_periods)
    iterations, change = 0, float("nan")
    while iterations < max_iterations:
        iterations += 1
        b = (shares * (p / P[:, None])).sum(axis=0)
        value = (p * q).sum(axis=1)
        reference_value = (b[None, :] * q).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            new_P = np.where(reference_value > 0, value / reference_value, np.nan)
        if not np.isfinite(new_P).all() or (new_P <= 0).any():
            raise MultilateralError(
                "a period in this window has no expenditure at reference prices, so its "
                "Geary-Khamis level is undefined; that period carries no usable transactions")
        new_P = new_P / new_P[0]
        change = float(np.max(np.abs(np.log(new_P) - np.log(P))))
        P = new_P
        if change < tolerance:
            break

    warnings_: list[str] = []
    if change >= tolerance:
        warnings_.append(
            f"the reference prices had not settled after {max_iterations} iterations "
            f"(largest remaining move {change:.2e} in logs); the levels below are the last "
            "iterate and should not be published without understanding why")

    index = pd.Series(base_value * P / P[0], index=panel.periods, name="geary_khamis")
    return MultilateralResult(
        index=index, method="geary_khamis", n_periods=panel.n_periods,
        n_items=int(usable.sum()), n_observations=panel.n_observations,
        parameters={"tolerance": tolerance, "max_iterations": max_iterations,
                    "base_value": base_value},
        diagnostics={"iterations": float(iterations), "final_change": change,
                     "churn_share": panel.churn_share},
        warnings=tuple(warnings_))


# ---------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------
def multilateral_index(panel: Panel, method: str = "geks_fisher", *,
                       base_value: float = 100.0, min_matched_items: int = 2,
                       frame: pd.DataFrame | None = None, hedonic_spec: Any = None,
                       price_col: str = "price_imputed") -> MultilateralResult:
    """One window, one method, by name.

    `frame` and `hedonic_spec` are only read by "tdh", which needs the
    characteristics the panel matrices do not carry.
    """
    if method not in METHODS:
        raise MultilateralError(
            f"unknown multilateral method {method!r}; available: {', '.join(METHODS)}")
    if method == "geks_fisher":
        return geks(panel, "fisher", base_value=base_value,
                    min_matched_items=min_matched_items)
    if method == "geks_tornqvist":
        return geks(panel, "tornqvist", base_value=base_value,
                    min_matched_items=min_matched_items)
    if method == "tpd":
        return time_product_dummy(panel, weighted=False, base_value=base_value)
    if method == "wtpd":
        return time_product_dummy(panel, weighted=True, base_value=base_value)
    if method == "geary_khamis":
        return geary_khamis(panel, base_value=base_value)
    if frame is None or hedonic_spec is None:
        raise MultilateralError(
            "the time dummy hedonic needs the panel frame and a HedonicSpec naming the "
            "characteristics; the price/quantity matrices alone do not carry them")
    return time_dummy_hedonic(frame, hedonic_spec, base_value=base_value, price_col=price_col)


def method_availability(panel: Panel, *, has_characteristics: bool = False
                        ) -> dict[str, str | None]:
    """Every method mapped to None when this panel supports it, or to the
    reason it does not. The interface shows the reason rather than hiding
    the method, for the same reason `engine.index.formula_availability`
    does: a compiler who cannot see GEKS does not learn that GEKS needs
    quantities."""
    out: dict[str, str | None] = {}
    for method, needs in METHOD_REQUIREMENTS.items():
        reason: str | None = None
        if "quantities" in needs and panel.quantities is None:
            reason = ("needs a quantity column (or expenditure, from which quantity is "
                      "derived) mapped on upload; this collection carries prices only")
        elif "characteristics" in needs and not has_characteristics:
            reason = ("needs item characteristics to regress on; upload a characteristics "
                      "file on the Quality adjustment page first")
        out[method] = reason
    return out


# ---------------------------------------------------------------------
# Window extension
# ---------------------------------------------------------------------
def _link_candidates(published: pd.Series, new: pd.Series, periods: pd.DatetimeIndex,
                     overlap: Sequence[int], new_period: pd.Timestamp) -> dict[int, float]:
    """The level `new_period` would take if the new window were linked onto
    the published series at each period of the overlap in turn.

    Every splice rule below is a choice from this one dictionary -- take the
    last entry, the first, the middle, or the geometric mean of all of them --
    which is why they are computed together: the spread of these candidates
    is the whole disagreement between the rules on this data, and it is
    cheaper and more honest to measure it than to argue about it.
    """
    candidates: dict[int, float] = {}
    for pos in overlap:
        at = periods[pos]
        base = float(published.loc[at])
        ratio = float(new.loc[new_period]) / float(new.loc[at])
        candidates[pos] = base * ratio
    return candidates


def _splice_positions(overlap: list[int], splice: str) -> list[int]:
    """Which periods of the overlap a splice links through.

    Every splice but the fixed-base pair is the same operation -- take the
    new window's movement from some period in the overlap up to the new
    period, and apply it to the published level at that period -- and they
    differ only in which period, so they are one implementation with a
    different list of positions. That is not a simplification of the
    literature; it is what the literature says they are.
    """
    if splice == "movement":
        return [overlap[-1]]                      # the previous period only
    if splice == "window":
        return [overlap[0]]                       # the far end of the overlap
    if splice == "half":
        return [overlap[len(overlap) // 2]]       # half the window back
    if splice == "mean":
        return list(overlap)                      # all of them, geometrically averaged
    raise MultilateralError(f"unknown splice {splice!r}; available: {', '.join(SPLICES)}")


def with_characteristics(frame: pd.DataFrame, characteristics: pd.DataFrame | None,
                         item_col: str = "item_id") -> pd.DataFrame:
    """The price panel with an item-level characteristics table joined on.

    A left join, so a product with no characteristics row keeps its prices
    and arrives at the regression with missing characteristics, which
    `engine.hedonic` drops with the count in its own diagnostics. An inner
    join here would silently shrink the collection and every method would
    then be computed on different data depending on whether the time dummy
    hedonic happened to be among them.
    """
    if characteristics is None or characteristics.empty:
        return frame
    if item_col not in characteristics.columns:
        raise MultilateralError(
            f"the characteristics table has no {item_col!r} column, so it cannot be joined "
            "to the price panel")
    extra = [c for c in characteristics.columns if c == item_col or c not in frame.columns]
    return frame.merge(characteristics[extra], on=item_col, how="left")


def _window_index(panel: Panel, positions: Sequence[int], method: str, *,
                  min_matched_items: int, frame: pd.DataFrame | None,
                  hedonic_spec: Any, price_col: str,
                  characteristics: pd.DataFrame | None = None) -> pd.Series:
    periods = [panel.periods[i] for i in positions]
    window_panel = panel.subset(periods)
    window_frame = None
    if method == "tdh" and frame is not None:
        window_frame = with_characteristics(
            frame[pd.to_datetime(frame["period"]).isin(periods)], characteristics)
    result = multilateral_index(
        window_panel, method, min_matched_items=min_matched_items, frame=window_frame,
        hedonic_spec=hedonic_spec, price_col=price_col)
    return result.index


def extend(df: pd.DataFrame, method: str = "geks_fisher", *, window: int = DEFAULT_WINDOW,
           splice: str = "movement", base_value: float = 100.0, min_matched_items: int = 2,
           anchor_month: int = DEFAULT_ANCHOR_MONTH, hedonic_spec: Any = None,
           characteristics: pd.DataFrame | None = None,
           price_col: str = "price_imputed") -> ExtensionResult:
    """Publish a series by computing the method on a rolling window and
    splicing each new period onto what was published before.

    The problem this solves is not statistical, it is institutional. A
    multilateral index is transitive *within its window*, and the window has
    to move, and when it moves every level inside it is re-estimated. An
    office cannot revise a published consumer price index, so it must decide
    which part of the new window's answer to take. Every rule below is a
    different answer to that, and none of them is neutral:

    movement  take only the newest month-on-month movement. Least revision
              admitted, and the one most exposed to a single odd month.
    window    take the movement over the whole overlap. Admits the most new
              information, at the cost of the published series no longer
              reflecting the window that produced its recent history.
    half      take the movement from half a window back. The compromise the
              Eurostat guide recommends where no local evidence says better.
    mean      the geometric mean of every splice point in the overlap. No
              single month can dominate the link, which is what makes it the
              most stable of the four under churn.
    FBEW      re-anchor at each `anchor_month`: within the year the index is
              computed directly on the window expanding from the anchor, so
              there is no splicing at all inside a year and exactly one link
              per year.
    FBMW      the same expanding window, but each month is linked on by that
              window's own month-on-month movement rather than read directly
              from the anchor.

    Until there are `window` periods of data there is nothing to extend: the
    first window is published as computed, and splicing starts at the period
    after it. A span shorter than the window is therefore simply the direct
    multilateral index, with `n_windows` of 1 saying so.

    `splice_spread_pp` records, for each spliced period, how far apart the
    candidate levels were: the same new window linked at every period of the
    overlap in turn. It is the size of the judgement this rule made, in
    index points, and where it is small the argument between the rules is
    not worth having on this data.
    """
    if splice not in SPLICES:
        raise MultilateralError(f"unknown splice {splice!r}; available: {', '.join(SPLICES)}")
    if window < 2:
        raise MultilateralError(
            f"a window of {window} periods cannot support a multilateral index; it needs at "
            "least two periods to compare")
    panel = build_panel(df, price_col)
    T = panel.n_periods
    kwargs: dict[str, Any] = {"min_matched_items": min_matched_items, "frame": df,
                              "hedonic_spec": hedonic_spec, "price_col": price_col,
                              "characteristics": characteristics}

    if T <= window:
        levels = _window_index(panel, range(T), method, **kwargs)
        return ExtensionResult(
            index=rebase(levels, panel.periods[0], base_value), method=method, splice=splice,
            window=window, n_windows=1,
            splice_spread_pp=pd.Series(0.0, index=panel.periods, name="splice_spread_pp"),
            warnings=(f"the collection has {T} periods and the window is {window}, so the "
                      "whole span fits in one window and no splicing was needed",))

    if splice in ("fbew", "fbmw"):
        return _fixed_base_extend(panel, df, method, window=window, splice=splice,
                                  base_value=base_value, anchor_month=anchor_month,
                                  min_matched_items=min_matched_items,
                                  hedonic_spec=hedonic_spec, characteristics=characteristics,
                                  price_col=price_col)

    published = _window_index(panel, range(window), method, **kwargs)
    published = rebase(published, panel.periods[0], base_value)
    spread: dict[pd.Timestamp, float] = {p: 0.0 for p in panel.periods[:window]}

    for end in range(window, T):
        positions = list(range(end - window + 1, end + 1))
        new = _window_index(panel, positions, method, **kwargs)
        overlap = positions[:-1]
        new_period = panel.periods[end]
        candidates = _link_candidates(published, new, panel.periods, overlap, new_period)
        used = [candidates[pos] for pos in _splice_positions(overlap, splice)]
        level = float(np.exp(np.mean(np.log(used))))
        values = list(candidates.values())
        spread[new_period] = float(max(values) - min(values))
        published = pd.concat([published, pd.Series([level], index=[new_period])])

    published.name = f"{method}_{splice}"
    return ExtensionResult(
        index=published, method=method, splice=splice, window=window,
        n_windows=T - window + 1,
        splice_spread_pp=pd.Series(spread, name="splice_spread_pp").sort_index())


def extend_from_config(df: pd.DataFrame, cfg: Any, *, base_value: float = 100.0,
                       hedonic_spec: Any = None,
                       characteristics: pd.DataFrame | None = None,
                       price_col: str = "price_imputed") -> ExtensionResult:
    """`extend` driven by a `core.config.MultilateralConfig`.

    The single door between a saved configuration and a compiled
    multilateral series, so the settings a run is registered under and the
    settings it was actually computed with cannot drift apart -- the same
    reason `engine.index.build_index` takes an `IndexConfig` rather than
    seven keyword arguments. The interface builds the config from its
    widgets and calls this, rather than calling `extend` with the widget
    values and saving the config separately; that second path is how the
    two quietly diverge.
    """
    return extend(df, cfg.method, window=cfg.window, splice=cfg.splice,
                  base_value=base_value, min_matched_items=cfg.min_matched_items,
                  anchor_month=cfg.anchor_month, hedonic_spec=hedonic_spec,
                  characteristics=characteristics, price_col=price_col)


def _anchor_positions(periods: pd.DatetimeIndex, anchor_month: int, window: int) -> list[int]:
    """Positions of the periods that restart the expanding window.

    The first period always anchors -- there is nothing before it to link
    to -- and thereafter every occurrence of `anchor_month`. A window that
    would expand beyond `window` periods without meeting an anchor is
    re-anchored anyway, so the fixed-base rules honour the window length
    even on data that is not monthly or does not span a whole year.
    """
    positions = [0]
    for i, p in enumerate(periods):
        if i == 0:
            continue
        # Re-anchor at the anchor month, or as soon as the window that began
        # at the last anchor has reached its full length: `i - anchor + 1`
        # periods are in it, so `i - anchor >= window - 1` is the last period
        # it may contain, and the next one starts a new window from here.
        if p.month == anchor_month or (i - positions[-1]) >= window - 1:
            positions.append(i)
    return positions


def _fixed_base_extend(panel: Panel, df: pd.DataFrame, method: str, *, window: int,
                       splice: str, base_value: float, anchor_month: int,
                       min_matched_items: int, hedonic_spec: Any,
                       characteristics: pd.DataFrame | None,
                       price_col: str) -> ExtensionResult:
    """FBEW and FBMW: the window runs from a fixed anchor and expands, and
    the only link is at the anchor itself.

    Both compute the multilateral index on the window [anchor .. current].
    FBEW reads the level straight off it, relative to the anchor, so within
    a year the published series *is* a direct multilateral comparison and
    revision inside the year is simply not a question. FBMW instead takes
    that window's newest month-on-month movement and chains it, which
    reintroduces a chain within the year -- much shorter than a full chained
    bilateral, and over a window in which the method is transitive, so the
    drift is bounded by the re-estimation between consecutive expanding
    windows rather than by twelve independent links.
    """
    kwargs: dict[str, Any] = {"min_matched_items": min_matched_items, "frame": df,
                              "hedonic_spec": hedonic_spec, "price_col": price_col,
                              "characteristics": characteristics}
    anchors = _anchor_positions(panel.periods, anchor_month, window)
    levels: dict[pd.Timestamp, float] = {panel.periods[0]: base_value}
    spread: dict[pd.Timestamp, float] = {panel.periods[0]: 0.0}
    n_windows = 0

    for a, anchor in enumerate(anchors):
        # The expanding window runs from this anchor up to and including the
        # next one: the anchor period belongs to both windows, which is
        # exactly what lets the next one link onto a level already published.
        stop = anchors[a + 1] if a + 1 < len(anchors) else panel.n_periods - 1
        for current in range(anchor + 1, stop + 1):
            positions = list(range(anchor, current + 1))
            new = _window_index(panel, positions, method, **kwargs)
            n_windows += 1
            period = panel.periods[current]
            published = pd.Series(levels).sort_index()
            candidates = _link_candidates(published, new, panel.periods,
                                          positions[:-1], period)
            # FBEW reads the level straight off the expanding window relative
            # to the anchor; FBMW takes that window's newest movement and
            # chains it onto the previous published month. Both are entries
            # in the same candidate set, at its two ends.
            levels[period] = candidates[anchor if splice == "fbew" else current - 1]
            values = list(candidates.values())
            spread[period] = float(max(values) - min(values))
    index = pd.Series(levels, name=f"{method}_{splice}").sort_index()
    return ExtensionResult(
        index=index, method=method, splice=splice, window=window, n_windows=n_windows,
        splice_spread_pp=pd.Series(spread, name="splice_spread_pp").sort_index())


# ---------------------------------------------------------------------
# Comparison and drift
# ---------------------------------------------------------------------
def method_comparison(df: pd.DataFrame, *, methods: Sequence[str] | None = None,
                      windows: Sequence[int] = (DEFAULT_WINDOW,),
                      splices: Sequence[str] = ("movement",), base_value: float = 100.0,
                      min_matched_items: int = 2, anchor_month: int = DEFAULT_ANCHOR_MONTH,
                      hedonic_spec: Any = None, characteristics: pd.DataFrame | None = None,
                      price_col: str = "price_imputed") -> pd.DataFrame:
    """The same data under every method, window and splice asked for.

    One row per combination, with the final published level, the movement
    over the whole span and the annualised rate, plus whatever went wrong
    for the combinations that could not be computed -- listed with the
    reason rather than dropped, because a comparison view that quietly omits
    the methods this data cannot support teaches the reader that the
    remaining spread is the whole spread.

    This exists because the choice of method is a material judgement whose
    consequence is invisible in any single result. On collected quotes the
    six methods agree to a rounding error and the table says so; on scanner
    data with real churn they can differ by more than the inflation being
    measured, and a reader who has seen that table will never again read a
    single multilateral number as a fact about prices rather than a fact
    about prices *and* a method.
    """
    from .index import annualised_rate, years_span

    chosen = list(methods) if methods is not None else list(METHODS)
    rows: list[dict[str, Any]] = []
    for method in chosen:
        for window in windows:
            for splice in splices:
                row: dict[str, Any] = {
                    "method": method, "method_label": METHOD_LABELS.get(method, method),
                    "window": window, "splice": splice,
                    "splice_label": SPLICE_LABELS.get(splice, splice)}
                try:
                    result = extend(
                        df, method, window=window, splice=splice, base_value=base_value,
                        min_matched_items=min_matched_items, anchor_month=anchor_month,
                        hedonic_spec=hedonic_spec, characteristics=characteristics,
                        price_col=price_col)
                except (MultilateralError, ValueError) as exc:
                    rows.append({**row, "final_level": np.nan, "change_pct": np.nan,
                                 "annualised_pct": np.nan, "splice_spread_pp": np.nan,
                                 "n_windows": 0, "error": str(exc)})
                    continue
                series = result.index.dropna()
                if len(series) < 2:
                    rows.append({**row, "final_level": np.nan, "change_pct": np.nan,
                                 "annualised_pct": np.nan, "splice_spread_pp": np.nan,
                                 "n_windows": result.n_windows,
                                 "error": "fewer than two periods carry a level"})
                    continue
                ratio = float(series.iloc[-1]) / float(series.iloc[0])
                years = years_span(pd.DatetimeIndex(series.index))
                rows.append({
                    **row,
                    "final_level": float(series.iloc[-1]) / float(series.iloc[0]) * base_value,
                    "change_pct": (ratio - 1.0) * 100.0,
                    "annualised_pct": float(annualised_rate(ratio, years))
                    if years > 0 else np.nan,
                    "splice_spread_pp": float(result.splice_spread_pp.max()),
                    "n_windows": result.n_windows,
                    "error": "; ".join(result.warnings) if result.warnings else ""})
    return pd.DataFrame(rows)


def comparison_spread(table: pd.DataFrame) -> dict[str, float]:
    """How far apart the comparison's methods finished.

    Reported in index points of the final level and in percentage points of
    the annualised rate, because those are the two units a reader actually
    argues about: the first is what the series says, the second is what the
    press release says.
    """
    usable = table.dropna(subset=["final_level"])
    if usable.empty:
        return {"n_computed": 0.0, "level_spread_pp": float("nan"),
                "rate_spread_pp": float("nan"), "level_min": float("nan"),
                "level_max": float("nan")}
    levels = usable["final_level"].astype(float)
    rates = usable["annualised_pct"].astype(float).dropna()
    return {
        "n_computed": float(len(usable)),
        "level_min": float(levels.min()),
        "level_max": float(levels.max()),
        "level_spread_pp": float(levels.max() - levels.min()),
        "rate_spread_pp": float(rates.max() - rates.min()) if len(rates) else float("nan"),
    }


def drift_against_chained(df: pd.DataFrame, multilateral: pd.Series, *,
                          formula: str = "tornqvist", threshold_pp: float = 1.0,
                          price_col: str = "price_imputed") -> ChainDriftReport:
    """Chain the bilateral `formula` over the same data and measure the gap
    to the multilateral series.

    This is the diagnostic the acceptance criterion asks for, and it is
    deliberately the *same* `engine.splicing.chain_drift` the bilateral
    engine already uses: the multilateral series takes the place of the
    direct comparison, because that is exactly what it is -- a
    path-independent answer to "how much have prices changed between these
    two periods" against which the chained answer can be scored. A chained
    index that ends five points above a transitive one did not observe five
    points of inflation; it manufactured them at the joins.
    """
    from ..core.config import IndexConfig
    from .index import build_index

    chained = build_index(df, IndexConfig(formula=formula, chained=True), price_col)["index"]
    return chain_drift(chained.dropna(), multilateral.dropna(), threshold_pp=threshold_pp)


# ---------------------------------------------------------------------
# Rolling a multilateral series up to a headline
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class MultilateralAggregate:
    """Multilateral series per category, rolled up to a headline."""

    indices: pd.DataFrame
    """One column per category that produced a series, plus every parent
    node of the classification tree where one was supplied, plus
    "All items". The same shape `engine.index.build_all` returns, so
    anything that can read a bilateral result can read this one."""
    results: Mapping[str, ExtensionResult]
    """The per-category compilation behind each leaf column: method,
    window, splice, the windows computed and the splice spread."""
    weights: pd.Series | None
    """The weights the roll-up used, per node. None when the collection
    carries none and the aggregate is the equally weighted fallback."""
    skipped: Mapping[str, str]
    """Categories that produced no multilateral series, mapped to the
    reason. A category dropped silently would shift the headline without
    appearing anywhere in it."""
    problems: tuple[str, ...] = ()
    """Whatever `engine.aggregation.aggregate_tree` had to say about the
    weight hierarchy, carried through unchanged."""

    @property
    def headline(self) -> pd.Series:
        return (self.indices["All items"] if "All items" in self.indices.columns
                else self.indices.iloc[:, 0])

    @property
    def weighted(self) -> bool:
        return self.weights is not None


def build_multilateral_all(
    df: pd.DataFrame, cfg: Any, *, price_col: str = "price_imputed", group: str = "category",
    parent_of: Mapping[str, str | None] | None = None, base_value: float = 100.0,
    hedonic_spec: Any = None, characteristics: pd.DataFrame | None = None,
) -> MultilateralAggregate:
    """A multilateral index per category, rolled up to an all-items headline.

    Deliberately the same shape, and the same roll-up, as
    `engine.index.build_all`: the categories are aggregated by
    `engine.aggregation`'s weighted mean, through the classification tree
    where the categories are codes in one, and by the equally weighted
    geometric mean where the collection carries no weights -- indicative,
    and said to be. Nothing about the aggregation is specific to a
    multilateral leaf, and writing a second aggregator for it would be two
    implementations of one idea, one of them less tested.

    The multilateral methods are elementary-level: they compare products,
    and a category is where the products are. A category whose data cannot
    support the method -- no quantities, a window no product survives, too
    few periods -- is named in `skipped` with the reason rather than
    dropped, because a category missing from a weighted headline moves the
    headline, and a reader must be able to see which one and why.

    Note what the aggregation does *not* preserve. Each category's series is
    transitive within its own windows; the weighted mean of several
    transitive series is not itself the multilateral index of the pooled
    data, and is not transitive either. That is the same compromise every
    office makes when it publishes a multilateral elementary index inside a
    conventional aggregation structure, and it is the reason the headline
    here is reported beside its method rather than on its own.
    """
    from .aggregation import aggregate_tree, equally_weighted_aggregate, weighted_aggregate
    from .index import category_weights

    if group not in df.columns:
        raise MultilateralError(
            f"{group!r} is not a column of this collection, so there are no categories to "
            "compile a multilateral index for")

    series: dict[str, pd.Series] = {}
    results: dict[str, ExtensionResult] = {}
    skipped: dict[str, str] = {}
    for group_key, part in df.groupby(group):
        name = str(group_key)
        try:
            result = extend_from_config(part, cfg, base_value=base_value,
                                        hedonic_spec=hedonic_spec,
                                        characteristics=characteristics, price_col=price_col)
        except (MultilateralError, ValueError) as exc:
            skipped[name] = str(exc)
            continue
        if result.index.dropna().size < 2:
            skipped[name] = "fewer than two periods carry a level"
            continue
        series[name] = result.index
        results[name] = result

    if not series:
        raise MultilateralError(
            "no category in this collection produced a multilateral series. Reasons by "
            "category: " + "; ".join(f"{k}: {v}" for k, v in list(skipped.items())[:4]))

    indices = pd.DataFrame(series)
    weights = category_weights(df, group)
    problems: list[str] = []
    node_weights: pd.Series | None = None

    if len(indices.columns) > 1:
        if weights is not None and parent_of is not None and all(
                c in parent_of for c in indices.columns):
            rolled = aggregate_tree(indices, weights, parent_of)
            for node in rolled.indices.columns:
                if node not in indices.columns:
                    indices[node] = rolled.indices[node]
            problems.extend(rolled.problems)
            node_weights = rolled.weights
            roots = [n for n, p in parent_of.items() if p is None and n in indices.columns]
            indices["All items"] = (
                indices[roots[0]] if len(roots) == 1
                else weighted_aggregate(indices[roots],
                                        {r: float(rolled.weights.get(r, 0.0)) for r in roots}))
        elif weights is not None:
            indices["All items"] = weighted_aggregate(indices, weights)
            node_weights = pd.Series(weights)
        else:
            indices["All items"] = equally_weighted_aggregate(indices)
    else:
        indices["All items"] = indices.iloc[:, 0]
        if weights is not None:
            node_weights = pd.Series(weights)

    if skipped and weights is not None:
        listed = ", ".join(sorted(skipped)[:4]) + ("..." if len(skipped) > 4 else "")
        problems.append(
            f"{len(skipped)} of {len(skipped) + len(series)} categories produced no "
            f"multilateral series ({listed}) and carry no weight in this headline, which is "
            "therefore not compiled over the whole basket")

    return MultilateralAggregate(indices=indices, results=results, weights=node_weights,
                                 skipped=skipped, problems=tuple(problems))


# ---------------------------------------------------------------------
# Seasonal variants
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class SeasonalMultilateralResult:
    """The year-over-year and rolling-year forms of a multilateral index."""

    year_over_year: pd.Series
    """Per period, that calendar month's multilateral sub-index against the
    same month a year earlier, as an index on `base_value`."""
    levels_by_month: pd.DataFrame
    """One column per calendar period present, each a multilateral index
    over that month's observations across the years -- the sub-index the
    year-over-year figures are read from."""
    rolling_year: pd.Series
    """The geometric mean of the twelve most recent year-over-year indices,
    compounded into a level from `base_value`. The CPI Manual's rolling
    year index: smooth by construction, and centred half a year behind the
    month it is dated at, which is its cost."""
    method: str
    months_compiled: int
    skipped_months: Mapping[int, str]
    warnings: tuple[str, ...] = ()


def seasonal_multilateral(
    df: pd.DataFrame, cfg: Any, *, price_col: str = "price_imputed",
    base_value: float = 100.0, periods_per_year: int = 12, hedonic_spec: Any = None,
    characteristics: pd.DataFrame | None = None,
) -> SeasonalMultilateralResult:
    """The seasonal forms of a multilateral index: year-over-year monthly,
    and the rolling year built from it.

    A strictly seasonal product is off the shelf for part of the year, so a
    month-on-month comparison either has nothing to compare it with or
    compares it against a different product. Both the bilateral and the
    multilateral machinery above handle that by matching what is there,
    which means a seasonal product enters and leaves the index and its
    arrival is scored as price change unless something stops it.

    The manual's answer is to stop comparing adjacent months. A
    **year-over-year monthly index** compares each month only against the
    same month in other years, where the same products are in season, so
    seasonality never enters the comparison at all -- there is nothing to
    adjust away because nothing seasonal was ever measured. Here each
    calendar month gets its own multilateral index over its own
    observations across the years, and the year-over-year figure is read off
    that sub-index. The **rolling year index** is the geometric mean of the
    twelve most recent year-over-year indices, compounded into a level: a
    smooth series whose movement is real, at the cost of being centred six
    months behind the month it is dated at.

    CPI Manual 2020, Chapter 11, paragraphs 11.19-11.55. What is
    multilateral here is each calendar month's sub-index; the year-over-year
    and rolling-year construction on top of it is the manual's, unchanged.

    A calendar month with fewer than two years of data cannot produce a
    year-over-year comparison and is named in `skipped_months`. This is not
    a seasonal *adjustment*: nothing is estimated and removed, and
    `engine.seasonal` is where adjustment lives.
    """
    if periods_per_year < 2:
        raise MultilateralError(
            f"a year of {periods_per_year} periods has no seasonal structure to work with")
    if "period" not in df.columns:
        raise MultilateralError("a seasonal multilateral index needs a 'period' column")

    frame = df.copy()
    frame["period"] = pd.to_datetime(frame["period"])
    if periods_per_year == 12:
        season = frame["period"].dt.month
    elif periods_per_year == 4:
        season = frame["period"].dt.quarter
    else:
        season = frame["period"].dt.dayofyear

    levels: dict[int, pd.Series] = {}
    skipped: dict[int, str] = {}
    warnings_: list[str] = []
    for key, part in frame.groupby(season):
        month = int(cast(int, key))
        if part["period"].nunique() < 2:
            skipped[month] = ("only one year of this calendar period is present, so it has no "
                              "year-over-year comparison")
            continue
        try:
            result = extend_from_config(part, cfg, base_value=base_value,
                                        hedonic_spec=hedonic_spec,
                                        characteristics=characteristics, price_col=price_col)
        except (MultilateralError, ValueError) as exc:
            skipped[month] = str(exc)
            continue
        levels[month] = result.index
        warnings_.extend(result.warnings)

    if not levels:
        raise MultilateralError(
            "no calendar period in this collection has two years of data, so there is no "
            "year-over-year comparison to make. Reasons: "
            + "; ".join(f"{k}: {v}" for k, v in list(skipped.items())[:4]))

    # The year-over-year index at period p: that month's sub-index at p over
    # the same sub-index one year earlier. Read off the sub-index rather
    # than recomputed, so the two cannot disagree.
    yoy: dict[pd.Timestamp, float] = {}
    for sub in levels.values():
        ordered = sub.sort_index()
        values = ordered.to_numpy(dtype=float)
        periods = list(ordered.index)
        for position in range(1, len(values)):
            earlier, value = values[position - 1], values[position]
            if np.isfinite(earlier) and earlier > 0 and np.isfinite(value):
                yoy[cast(pd.Timestamp, periods[position])] = base_value * value / earlier
    year_over_year = pd.Series(yoy, name="year_over_year", dtype=float).sort_index()

    # The rolling year: the geometric mean of the last `periods_per_year`
    # year-over-year indices, compounded into a level.
    logs = pd.Series(np.log(year_over_year.to_numpy(dtype=float) / base_value),
                     index=year_over_year.index)
    rolling = logs.rolling(periods_per_year, min_periods=periods_per_year).mean()
    rolling_year = (base_value * np.exp(rolling.cumsum())).rename("rolling_year")
    if rolling_year.notna().sum() == 0:
        warnings_.append(
            f"the collection does not span {periods_per_year} year-over-year comparisons, so "
            "the rolling year index has no period it can be computed for")

    return SeasonalMultilateralResult(
        year_over_year=year_over_year,
        levels_by_month=pd.DataFrame({f"period_{m:02d}": s for m, s in sorted(levels.items())}),
        rolling_year=rolling_year, method=cfg.method, months_compiled=len(levels),
        skipped_months=skipped, warnings=tuple(dict.fromkeys(warnings_)))
