"""Hedonic regression: valuing a quality difference from the market's own
pricing of characteristics.

A hedonic function relates an item's price to the characteristics that
buyers pay for -- capacity, warranty, brand, energy cost -- so the price
of a bundle of characteristics that was never sold can be estimated from
bundles that were. That is exactly what a replacement asks for: the old
item's price in the period it no longer exists, or the new item's price
in the period before it did. CPI Manual 2020, Chapter 6, "Hedonic
approach", and Table 6.6's washing-machine illustration.

Three variants, all built here on one design matrix:

time dummy          one pooled regression over all periods with a dummy
                    per period; the index *is* exp(dummy). Cheap, and it
                    constrains characteristics prices to be the same in
                    every period, which is its known weakness.
characteristics     one regression per period; the index prices a fixed
price               bundle of characteristics at each period's estimated
                    coefficients (Laspeyres-type with the base bundle,
                    Paasche-type with the current one, Fisher-type as
                    their geometric mean).
imputation          per-period regressions used to predict the missing
                    price of a specific item, so a matched comparison can
                    be completed; double imputation predicts both sides
                    of the comparison so any systematic error cancels.

Three functional forms: log-linear (log price on log continuous
characteristics), semi-log (log price on the characteristics' levels --
the manual's default, and Table 6.6's right-hand panel), and Box-Cox,
with lambda estimated by maximum likelihood on the regression itself
rather than assumed. Categorical characteristics are dummy-encoded with
one reference level. Weighted least squares with expenditure weights is
available throughout, because an unweighted regression over a scanner
dataset prices the characteristics of what is *listed*, not what is
*bought*.

The diagnostics are the point
----------------------------
A coefficient table is not a diagnostic. `HedonicResult` carries the
things a reviewer needs before believing the coefficients: adjusted R
squared; heteroskedasticity-robust (HC1) standard errors, since price
dispersion almost always grows with price level; a variance inflation
factor per regressor and the design's condition number, because
characteristics that travel together (capacity and drum size, brand and
warranty) make the individual coefficients -- which is what a quality
adjustment reads off -- arbitrary even while the fit looks excellent;
residuals and leverage for the plots; coefficient stability across
rolling windows of periods, since a characteristic whose price halves
from one window to the next is not something a pooled time-dummy model
should be asked to hold constant; and out-of-sample prediction error,
which is the honest measure of whether the model can price a bundle it
did not see, which is the only thing it is ever used for.

Severe multicollinearity raises `HedonicMulticollinearityWarning` and is
recorded in `warnings` on the result. It does not raise: the fit is
still informative about *fitted prices* even when it is uninformative
about individual coefficients, and imputation only needs the former. It
does refuse silently reporting unstable coefficients as if they meant
something, which is the failure the phase specification names.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import optimize
from scipy.special import boxcox as _boxcox
from scipy.special import inv_boxcox as _inv_boxcox

from .quality_adjustment import CellContext, QualityAdjustment, QualityAdjustmentError, ReasonCode
from .quality_adjustment import _build as _build_adjustment

FUNCTIONAL_FORMS = ("log_linear", "semi_log", "box_cox")
VARIANTS = ("time_dummy", "characteristics_price", "imputation")
TIME_PREFIX = "period="


class HedonicError(ValueError):
    """The data cannot support the regression asked for."""


class HedonicMulticollinearityWarning(UserWarning):
    """Regressors are close to collinear: individual coefficients, and so
    any quality adjustment read from them, are unstable."""


@dataclass(frozen=True)
class HedonicSpec:
    """What to regress on what, and how."""

    characteristics: tuple[str, ...] = ()
    """Continuous characteristics. Logged under `log_linear`, so they
    must be positive there."""
    categorical: tuple[str, ...] = ()
    """Characteristics encoded as dummies against a reference level (the
    first level in sorted order)."""
    functional_form: str = "semi_log"
    weight_col: str | None = None
    """Expenditure weights for WLS; None fits OLS."""
    box_cox_lambda: float | None = None
    """Fix lambda rather than estimate it. Ignored unless `box_cox`."""
    price_col: str = "price"
    period_col: str = "period"
    item_col: str = "item_id"
    vif_threshold: float = 10.0
    condition_threshold: float = 30.0

    def __post_init__(self) -> None:
        if self.functional_form not in FUNCTIONAL_FORMS:
            raise HedonicError(
                f"functional_form must be one of {FUNCTIONAL_FORMS}, not {self.functional_form!r}")
        if not self.characteristics and not self.categorical:
            raise HedonicError("a hedonic regression needs at least one characteristic")


@dataclass
class HedonicResult:
    """One fitted hedonic regression and its diagnostics."""

    spec: HedonicSpec
    variant: str
    n_obs: int
    n_params: int
    coefficients: pd.Series
    robust_se: pd.Series
    """Heteroskedasticity-consistent (HC1) standard errors."""
    p_values: pd.Series
    r_squared: float
    adj_r_squared: float
    vif: pd.Series
    """Variance inflation factor per non-constant regressor."""
    condition_number: float
    residuals: pd.Series
    fitted: pd.Series
    """In the transformed (log or Box-Cox) scale, like `residuals`."""
    leverage: pd.Series
    """Diagonal of the hat matrix: an observation's pull on its own fit."""
    box_cox_lambda: float | None
    periods: tuple[pd.Timestamp, ...]
    base_period: pd.Timestamp | None
    warnings: list[str] = field(default_factory=list)
    coefficient_stability: pd.DataFrame | None = None
    """Characteristic coefficients refitted on rolling windows of periods,
    one row per window, with a final `range` row."""
    out_of_sample: dict[str, float] = field(default_factory=dict)
    """`rmse_transformed` (k-fold RMSE in the regression's own scale),
    `mape_pct` (mean absolute percentage error in price levels), `folds`."""
    data_vintage: dict[str, Any] = field(default_factory=dict)
    """Provenance of the characteristics the fit used -- content hash, file,
    receipt -- set by the caller that loaded them (`data.store.record_upload`)
    and carried into every adjustment read from this fit, so a ledger entry
    names the characteristics vintage as well as the method."""

    # -- reading the fit ------------------------------------------------
    @property
    def characteristic_names(self) -> list[str]:
        return [c for c in self.coefficients.index if c != "const" and not c.startswith(TIME_PREFIX)]

    def summary_frame(self) -> pd.DataFrame:
        """Coefficient, robust standard error, p-value and VIF, one row
        per regressor -- the table a report prints."""
        return pd.DataFrame({
            "coefficient": self.coefficients, "robust_se": self.robust_se,
            "p_value": self.p_values, "vif": self.vif.reindex(self.coefficients.index)})

    def time_dummy_index(self, *, bias_corrected: bool = False) -> pd.Series:
        """The index implied by the period dummies, base period = 100.

        exp(delta_t) x 100; with `bias_corrected`, exp(delta_t - V(delta_t)/2)
        x 100, the Kennedy (1981) adjustment for reading a percentage off a
        dummy coefficient in a log model. Both are reported because the
        manual mentions the adjustment and agencies differ on applying it.
        """
        if self.variant != "time_dummy":
            raise HedonicError("only a time_dummy fit implies an index directly")
        levels = {}
        for p in self.periods:
            name = f"{TIME_PREFIX}{p:%Y-%m-%d}"
            if name in self.coefficients.index:
                delta = float(self.coefficients[name])
                if bias_corrected:
                    delta -= 0.5 * float(self.robust_se[name]) ** 2
                levels[p] = 100.0 * float(np.exp(delta))
            else:
                levels[p] = 100.0
        return pd.Series(levels, name="time_dummy_index").sort_index()

    def predict(self, characteristics: pd.DataFrame | Mapping[str, Any],
                period: pd.Timestamp | None = None) -> pd.Series:
        """Predicted price *level* for each row of characteristics.

        Retransformed from the regression's scale. For a log model this is
        exp(fitted), which is the median rather than the mean prediction;
        a quality adjustment takes the ratio of two such predictions, and
        the retransformation factor cancels in the ratio.
        """
        frame = pd.DataFrame([characteristics]) if isinstance(characteristics, Mapping) else characteristics
        X = _characteristic_columns(frame, self.spec)
        for col in self.coefficients.index:
            if col not in X.columns:
                X[col] = 0.0
        if period is not None:
            name = f"{TIME_PREFIX}{pd.Timestamp(period):%Y-%m-%d}"
            if name in X.columns:
                X[name] = 1.0
        X = X[self.coefficients.index]
        transformed = X.to_numpy(dtype=float) @ self.coefficients.to_numpy(dtype=float)
        return pd.Series(_inverse_transform(transformed, self.spec, self.box_cox_lambda),
                         index=frame.index, name="predicted_price")


# ---------------------------------------------------------------------
# Design matrix
# ---------------------------------------------------------------------
def _characteristic_columns(frame: pd.DataFrame, spec: HedonicSpec) -> pd.DataFrame:
    cols: dict[str, Any] = {"const": np.ones(len(frame))}
    for c in spec.characteristics:
        if c not in frame.columns:
            raise HedonicError(f"characteristic {c!r} is not a column of the data")
        values = pd.to_numeric(frame[c], errors="coerce").to_numpy(dtype=float)
        if spec.functional_form == "log_linear":
            if np.any(values <= 0):
                raise HedonicError(
                    f"log_linear takes the log of {c!r}, which has non-positive values; use "
                    "semi_log for a characteristic that can be zero")
            values = np.log(values)
        cols[c] = values
    X = pd.DataFrame(cols, index=frame.index)
    for c in spec.categorical:
        if c not in frame.columns:
            raise HedonicError(f"categorical characteristic {c!r} is not a column of the data")
        levels = sorted(frame[c].astype(str).unique())
        for level in levels[1:]:
            X[f"{c}={level}"] = (frame[c].astype(str) == level).astype(float)
    return X


def _transform_price(price: np.ndarray, spec: HedonicSpec, lam: float | None) -> np.ndarray:
    if np.any(~np.isfinite(price)) or np.any(price <= 0):
        raise HedonicError("prices must be finite and positive for a hedonic regression")
    if spec.functional_form == "box_cox":
        assert lam is not None
        return np.asarray(_boxcox(price, lam), dtype=float)
    return np.log(price)


def _inverse_transform(values: np.ndarray, spec: HedonicSpec, lam: float | None) -> np.ndarray:
    if spec.functional_form == "box_cox":
        assert lam is not None
        return np.asarray(_inv_boxcox(values, lam), dtype=float)
    return np.exp(values)


def _design(df: pd.DataFrame, spec: HedonicSpec, *, time_dummies: bool,
            base_period: pd.Timestamp | None) -> tuple[pd.DataFrame, np.ndarray, np.ndarray | None,
                                                      tuple[pd.Timestamp, ...], pd.Timestamp | None]:
    for col in (spec.price_col, spec.period_col):
        if col not in df.columns:
            raise HedonicError(f"column {col!r} is not in the data")
    frame = df.dropna(subset=[spec.price_col, *spec.characteristics, *spec.categorical])
    if len(frame) == 0:
        raise HedonicError("no complete rows (price and every characteristic present)")
    X = _characteristic_columns(frame, spec)
    periods = tuple(sorted(pd.to_datetime(frame[spec.period_col]).unique()))
    base = pd.Timestamp(base_period) if base_period is not None else (periods[0] if periods else None)
    if time_dummies:
        if base not in periods:
            raise HedonicError(f"base period {base} is not among the data's periods")
        period_values = pd.to_datetime(frame[spec.period_col])
        for p in periods:
            if p != base:
                X[f"{TIME_PREFIX}{p:%Y-%m-%d}"] = (period_values == p).astype(float).to_numpy()
    price = frame[spec.price_col].to_numpy(dtype=float)
    weights = None
    if spec.weight_col is not None:
        if spec.weight_col not in frame.columns:
            raise HedonicError(f"weight column {spec.weight_col!r} is not in the data")
        weights = frame[spec.weight_col].to_numpy(dtype=float)
        if np.any(~np.isfinite(weights)) or np.any(weights <= 0):
            raise HedonicError("expenditure weights must be finite and positive")
    return X, price, weights, periods, base


# ---------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------
def _estimate_lambda(X: np.ndarray, price: np.ndarray, weights: np.ndarray | None) -> float:
    """Box-Cox lambda by maximum likelihood on the regression: the
    concentrated log-likelihood -n/2 log(RSS(lambda)/n) + (lambda - 1)
    sum(log y), maximised over lambda in [-2, 2]."""
    log_y_sum = float(np.log(price).sum())
    n = len(price)

    def neg_ll(lam: float) -> float:
        y = np.asarray(_boxcox(price, lam), dtype=float)
        model = sm.WLS(y, X, weights=weights) if weights is not None else sm.OLS(y, X)
        rss = float(np.sum(model.fit().resid ** 2))
        if rss <= 0:
            return float("inf")
        return float(-(-n / 2.0 * np.log(rss / n) + (lam - 1.0) * log_y_sum))

    result = optimize.minimize_scalar(neg_ll, bounds=(-2.0, 2.0), method="bounded")
    return float(result.x)


def _vif(X: pd.DataFrame) -> pd.Series:
    """1 / (1 - R^2) of each non-constant regressor on the others."""
    out = {}
    cols = [c for c in X.columns if c != "const"]
    arr = X.to_numpy(dtype=float)
    for c in cols:
        j = list(X.columns).index(c)
        y = arr[:, j]
        others = np.delete(arr, j, axis=1)
        if others.shape[1] == 0 or np.allclose(y, y[0]):
            out[c] = float("nan")
            continue
        fit = sm.OLS(y, others).fit()
        r2 = float(fit.rsquared)
        out[c] = float("inf") if r2 >= 1.0 - 1e-12 else 1.0 / (1.0 - r2)
    return pd.Series(out, dtype=float)


def _leverage(X: np.ndarray, weights: np.ndarray | None) -> np.ndarray:
    w = np.ones(len(X)) if weights is None else weights
    Xw = X * np.sqrt(w)[:, None]
    xtx_inv = np.linalg.pinv(Xw.T @ Xw)
    return np.asarray(np.einsum("ij,jk,ik->i", Xw, xtx_inv, Xw), dtype=float)


def _fit_once(X: pd.DataFrame, price: np.ndarray, weights: np.ndarray | None,
              spec: HedonicSpec) -> tuple[Any, float | None]:
    lam: float | None = None
    if spec.functional_form == "box_cox":
        lam = spec.box_cox_lambda if spec.box_cox_lambda is not None else _estimate_lambda(
            X.to_numpy(dtype=float), price, weights)
    y = _transform_price(price, spec, lam)
    model = sm.WLS(y, X, weights=weights) if weights is not None else sm.OLS(y, X)
    return model.fit(cov_type="HC1"), lam


def _drop_empty_columns(X: pd.DataFrame) -> pd.DataFrame:
    """A dummy that is zero everywhere in this subset (a level or period
    absent from a window or fold) carries no information and would make
    the design singular."""
    keep = [c for c in X.columns if c == "const" or not np.allclose(X[c].to_numpy(dtype=float), 0.0)]
    return X[keep]


def fit_hedonic(
    df: pd.DataFrame,
    spec: HedonicSpec,
    *,
    time_dummies: bool = True,
    base_period: pd.Timestamp | None = None,
    stability_window: int | None = None,
    cv_folds: int = 5,
    variant: str = "time_dummy",
) -> HedonicResult:
    """Fit one hedonic regression over `df` and compute its diagnostics.

    With `time_dummies` (the default) this is the time dummy variant: one
    pooled regression, a dummy per period other than `base_period`, the
    index read from the dummies. Without, it is a single cross-section
    regression, which is what `fit_by_period` runs per period for the
    characteristics-price and imputation variants.

    `stability_window` refits the characteristics on each run of that
    many consecutive periods; `cv_folds` is the k for out-of-sample
    prediction error (0 disables it).
    """
    if variant not in VARIANTS:
        raise HedonicError(f"variant must be one of {VARIANTS}")
    X, price, weights, periods, base = _design(df, spec, time_dummies=time_dummies,
                                               base_period=base_period)
    if len(X) <= X.shape[1]:
        raise HedonicError(
            f"{len(X)} observations cannot estimate {X.shape[1]} parameters; a hedonic "
            "regression needs comfortably more items than characteristics")
    fit, lam = _fit_once(X, price, weights, spec)

    vif = _vif(X)
    condition = float(np.linalg.cond(X.to_numpy(dtype=float) / np.maximum(
        np.abs(X.to_numpy(dtype=float)).max(axis=0), 1e-12)))
    notes: list[str] = []
    severe = vif[vif > spec.vif_threshold].dropna()
    if len(severe) or condition > spec.condition_threshold:
        which = ", ".join(f"{k} (VIF {v:.1f})" for k, v in severe.items()) or "none above threshold"
        message = (
            f"severe multicollinearity: {which}; design condition number {condition:.1f}. "
            "Individual coefficients -- and any quality adjustment read from them -- are "
            "unstable; fitted prices may still be usable. Drop or combine the collinear "
            "characteristics, or use the imputation variant, which needs only fitted prices.")
        notes.append(message)
        warnings.warn(message, HedonicMulticollinearityWarning, stacklevel=2)

    params = pd.Series(np.asarray(fit.params, dtype=float), index=X.columns)
    result = HedonicResult(
        spec=spec, variant=variant, n_obs=int(fit.nobs), n_params=int(X.shape[1]),
        coefficients=params,
        robust_se=pd.Series(np.asarray(fit.bse, dtype=float), index=X.columns),
        p_values=pd.Series(np.asarray(fit.pvalues, dtype=float), index=X.columns),
        r_squared=float(fit.rsquared), adj_r_squared=float(fit.rsquared_adj),
        vif=vif, condition_number=condition,
        residuals=pd.Series(np.asarray(fit.resid, dtype=float), index=X.index),
        fitted=pd.Series(np.asarray(fit.fittedvalues, dtype=float), index=X.index),
        leverage=pd.Series(_leverage(X.to_numpy(dtype=float), weights), index=X.index),
        box_cox_lambda=lam, periods=tuple(pd.Timestamp(p) for p in periods), base_period=base,
        warnings=notes)

    if stability_window is not None and stability_window >= 1:
        result.coefficient_stability = _coefficient_stability(
            df, spec, stability_window, base_period=base_period, time_dummies=time_dummies)
    if cv_folds and cv_folds >= 2:
        result.out_of_sample = _cross_validate(X, price, weights, spec, cv_folds)
    return result


def _coefficient_stability(df: pd.DataFrame, spec: HedonicSpec, window: int, *,
                           base_period: pd.Timestamp | None, time_dummies: bool
                           ) -> pd.DataFrame | None:
    periods = sorted(pd.to_datetime(df[spec.period_col]).unique())
    if len(periods) < window:
        return None
    rows = {}
    for start in range(0, len(periods) - window + 1):
        chunk = periods[start:start + window]
        sub = df[pd.to_datetime(df[spec.period_col]).isin(chunk)]
        try:
            X, price, weights, _p, _b = _design(sub, spec, time_dummies=time_dummies and window > 1,
                                                base_period=chunk[0] if time_dummies else None)
            X = _drop_empty_columns(X)
            if len(X) <= X.shape[1]:
                continue
            fit, _lam = _fit_once(X, price, weights, spec)
        except (HedonicError, np.linalg.LinAlgError):
            continue
        label = f"{pd.Timestamp(chunk[0]):%Y-%m}..{pd.Timestamp(chunk[-1]):%Y-%m}"
        names = [c for c in X.columns if c != "const" and not c.startswith(TIME_PREFIX)]
        rows[label] = pd.Series(np.asarray(fit.params, dtype=float), index=X.columns)[names]
    if not rows:
        return None
    table = pd.DataFrame(rows).T
    table.loc["range"] = table.max() - table.min()
    return table


def _cross_validate(X: pd.DataFrame, price: np.ndarray, weights: np.ndarray | None,
                    spec: HedonicSpec, folds: int) -> dict[str, float]:
    """k-fold out-of-sample error: RMSE in the regression's own scale and
    mean absolute percentage error in price levels. Folds are assigned
    round-robin over the row order, so the split is deterministic."""
    n = len(X)
    folds = min(folds, n)
    assignment = np.arange(n) % folds
    errors_t, errors_pct = [], []
    for k in range(folds):
        train, test = assignment != k, assignment == k
        if train.sum() <= X.shape[1] or test.sum() == 0:
            continue
        Xtr = _drop_empty_columns(X.loc[train])
        try:
            fit, lam = _fit_once(Xtr, price[train], None if weights is None else weights[train], spec)
        except (HedonicError, np.linalg.LinAlgError):
            continue
        Xte = X.loc[test, Xtr.columns].to_numpy(dtype=float)
        pred_t = Xte @ np.asarray(fit.params, dtype=float)
        actual_t = _transform_price(price[test], spec, lam)
        errors_t.extend((pred_t - actual_t).tolist())
        pred_level = _inverse_transform(pred_t, spec, lam)
        errors_pct.extend((np.abs(pred_level - price[test]) / price[test] * 100.0).tolist())
    if not errors_t:
        return {}
    return {"rmse_transformed": float(np.sqrt(np.mean(np.square(errors_t)))),
            "mape_pct": float(np.mean(errors_pct)), "folds": float(folds)}


@dataclass
class HedonicPanel:
    """One regression per period: the characteristics-price and imputation
    variants."""

    spec: HedonicSpec
    fits: dict[pd.Timestamp, HedonicResult]

    @property
    def periods(self) -> list[pd.Timestamp]:
        return sorted(self.fits)

    def predict(self, period: pd.Timestamp, characteristics: pd.DataFrame | Mapping[str, Any]
                ) -> pd.Series:
        period = pd.Timestamp(period)
        if period not in self.fits:
            raise HedonicError(f"no regression was fitted for {period:%Y-%m-%d}")
        return self.fits[period].predict(characteristics)

    def characteristics_price_index(self, bundle: pd.DataFrame, base_period: pd.Timestamp | None = None,
                                    current_bundles: Mapping[pd.Timestamp, pd.DataFrame] | None = None
                                    ) -> pd.DataFrame:
        """Price a fixed bundle of characteristics at each period's
        coefficients.

        `bundle` is the base period's characteristics (one row per item);
        the Laspeyres-type column prices it everywhere. With
        `current_bundles` (period -> that period's bundle) the Paasche-type
        column prices each period's own bundle at both that period's and
        the base's coefficients, and the Fisher-type column is the
        geometric mean of the two. Each period's number is the ratio of
        geometric mean predicted prices, base = 100, following the manual's
        Jevons-consistent treatment at the elementary level.
        """
        base = pd.Timestamp(base_period) if base_period is not None else self.periods[0]
        if base not in self.fits:
            raise HedonicError(f"no regression was fitted for the base period {base:%Y-%m-%d}")

        def gm(values: pd.Series) -> float:
            return float(np.exp(np.log(values.to_numpy(dtype=float)).mean()))

        base_at_base = gm(self.predict(base, bundle))
        rows = {}
        for p in self.periods:
            lasp = gm(self.predict(p, bundle)) / base_at_base * 100.0
            row: dict[str, float] = {"laspeyres_type": lasp}
            if current_bundles is not None and p in current_bundles:
                cur = current_bundles[p]
                paas = gm(self.predict(p, cur)) / gm(self.predict(base, cur)) * 100.0
                row["paasche_type"] = paas
                row["fisher_type"] = float(np.sqrt(lasp * paas))
            rows[p] = row
        return pd.DataFrame(rows).T.sort_index()


def fit_by_period(
    df: pd.DataFrame, spec: HedonicSpec, *, cv_folds: int = 0, variant: str = "characteristics_price",
) -> HedonicPanel:
    """One cross-section regression per period. Periods with too few
    complete rows to estimate the specification are skipped and named in
    the panel's first fit's warnings, rather than fitted badly."""
    fits: dict[pd.Timestamp, HedonicResult] = {}
    skipped = []
    for p, sub in df.groupby(pd.to_datetime(df[spec.period_col])):
        try:
            fits[pd.Timestamp(p)] = fit_hedonic(sub, spec, time_dummies=False, cv_folds=cv_folds,
                                                variant=variant)
        except HedonicError as exc:
            skipped.append(f"{pd.Timestamp(p):%Y-%m-%d}: {exc}")
    if not fits:
        raise HedonicError("no period had enough complete rows to fit the specification; "
                           + "; ".join(skipped))
    if skipped:
        first = fits[min(fits)]
        first.warnings.append("periods not fitted: " + "; ".join(skipped))
    return HedonicPanel(spec=spec, fits=fits)


# ---------------------------------------------------------------------
# Hedonic quality adjustments
# ---------------------------------------------------------------------
def _hedonic_parameters(result: HedonicResult, variant: str) -> dict[str, Any]:
    return {"variant": variant, "functional_form": result.spec.functional_form,
            "characteristics": list(result.spec.characteristics),
            "categorical": list(result.spec.categorical),
            "n_obs": result.n_obs, "adj_r_squared": result.adj_r_squared,
            "max_vif": float(result.vif.replace(np.inf, np.nan).max()) if len(result.vif) else float("nan"),
            "multicollinearity_warning": bool(result.warnings),
            "characteristics_vintage": dict(result.data_vintage)}


def hedonic_adjustment(
    result: HedonicResult, old_item: str, new_item: str, category: str, period: pd.Timestamp,
    old_price: float, new_price: float,
    old_characteristics: Mapping[str, Any], new_characteristics: Mapping[str, Any],
    *, cell: CellContext | None = None, justification: str = "",
) -> QualityAdjustment:
    """Value the quality difference from the estimated characteristics
    prices: the ratio of the model's predicted prices for the two bundles.

        q = P_hat(z_new) / P_hat(z_old)

    For a log model this is exp(sum_k beta_k (z_new_k - z_old_k)), so
    period dummies and the retransformation factor cancel and the ratio
    depends only on the characteristics that differ. The variant recorded
    is "characteristics_price": the characteristics are priced and the
    replacement is adjusted by the priced difference.
    """
    pred = result.predict(pd.DataFrame([dict(old_characteristics), dict(new_characteristics)]))
    if not (np.isfinite(pred.iloc[0]) and np.isfinite(pred.iloc[1]) and pred.iloc[0] > 0):
        raise QualityAdjustmentError("the hedonic model could not price one of the bundles")
    ratio = float(pred.iloc[1] / pred.iloc[0])
    parameters = {**_hedonic_parameters(result, "characteristics_price"),
                  "predicted_old": float(pred.iloc[0]), "predicted_new": float(pred.iloc[1]),
                  "old_characteristics": dict(old_characteristics),
                  "new_characteristics": dict(new_characteristics)}
    return _build_adjustment(
        old_item=old_item, new_item=new_item, category=category, period=pd.Timestamp(period),
        method=ReasonCode.HEDONIC, old_price=old_price, new_price=new_price, quality_ratio=ratio,
        parameters=parameters, cell=cell, justification=justification)


def hedonic_imputation_adjustment(
    panel: HedonicPanel, old_item: str, new_item: str, category: str,
    period_prev: pd.Timestamp, period: pd.Timestamp, old_price: float, new_price: float,
    old_characteristics: Mapping[str, Any], *, double: bool = True,
    cell: CellContext | None = None, justification: str = "",
) -> QualityAdjustment:
    """Impute the old item's price in the period it is missing from that
    period's regression, and take the imputed relative as the pure price
    change; the replacement's remaining difference is quality.

        single imputation:  pure relative = P_hat_t(z_old) / p_old(t-1)
        double imputation:  pure relative = P_hat_t(z_old) / P_hat_(t-1)(z_old)

    Double imputation (the default) predicts both ends, so any systematic
    error of the model for that bundle -- an old model that always sold
    below its hedonic value, say -- cancels in the ratio; the manual
    prefers it for that reason. The variant recorded is "imputation".
    """
    period_prev, period = pd.Timestamp(period_prev), pd.Timestamp(period)
    predicted_t = float(panel.predict(period, old_characteristics).iloc[0])
    if double:
        predicted_prev = float(panel.predict(period_prev, old_characteristics).iloc[0])
        relative = predicted_t / predicted_prev
    else:
        relative = predicted_t / old_price
    if not np.isfinite(relative) or relative <= 0:
        raise QualityAdjustmentError("the hedonic imputation produced an unusable price relative")
    parameters = {**_hedonic_parameters(panel.fits[period], "imputation"),
                  "double_imputation": double, "imputed_price_relative": relative,
                  "predicted_old_at_t": predicted_t,
                  "old_characteristics": dict(old_characteristics)}
    return _build_adjustment(
        old_item=old_item, new_item=new_item, category=category, period=period,
        method=ReasonCode.HEDONIC, old_price=old_price, new_price=new_price,
        quality_ratio=(new_price / old_price) / relative, parameters=parameters,
        cell=cell, justification=justification)


def synthetic_hedonic_panel(
    n_items: int = 60, n_periods: int = 6, *, true_coefficients: Sequence[float] = (0.4, -0.15),
    period_effects: Sequence[float] | None = None, noise_sd: float = 0.05, seed: int = 0,
) -> pd.DataFrame:
    """A panel with a known hedonic structure, for recovery tests and
    demonstrations: log price = 3 + sum(beta_k z_k) + delta_t + noise,
    with continuous characteristics `z1`, `z2` and a categorical `brand`
    worth +0.1 for level "B". Not used by any production path."""
    rng = np.random.default_rng(seed)
    if period_effects is None:
        period_effects = [0.01 * t for t in range(n_periods)]
    periods = pd.date_range("2020-01-01", periods=n_periods, freq="MS")
    rows = []
    z = rng.uniform(1.0, 5.0, size=(n_items, len(true_coefficients)))
    brand = rng.choice(["A", "B"], size=n_items)
    for t, p in enumerate(periods):
        for i in range(n_items):
            log_price = 3.0 + float(z[i] @ np.asarray(true_coefficients)) + period_effects[t] \
                + (0.1 if brand[i] == "B" else 0.0) + rng.normal(0.0, noise_sd)
            row = {"period": p, "item_id": f"item{i:03d}", "brand": brand[i],
                   "price": float(np.exp(log_price)), "weight": 1.0}
            row.update({f"z{k + 1}": float(z[i, k]) for k in range(len(true_coefficients))})
            rows.append(row)
    return pd.DataFrame(rows)
