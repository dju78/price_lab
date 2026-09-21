# Hedonic regression (`engine/hedonic.py`)

A hedonic function relates price to the characteristics buyers pay for, so
the price of a bundle never sold can be estimated from bundles that were —
which is what a replacement asks for.

## Functional forms

| Form | Regression | Note |
|---|---|---|
| Semi-log (default) | `ln p = α + Σ β_k z_k + Σ γ_j D_j + ε` | The manual's default and Table 6.6's right-hand panel; a coefficient is a proportional price effect |
| Log-linear | `ln p = α + Σ β_k ln z_k + …` | Continuous characteristics must be positive; a coefficient is an elasticity |
| Box-Cox | `(p^λ − 1)/λ = α + Σ β_k z_k + …` | λ estimated by maximum likelihood on the regression itself (concentrated log-likelihood over [−2, 2]), or fixed |

Categorical characteristics are dummy-encoded against the first level in
sorted order. Weighted least squares with expenditure weights is available
throughout, because an unweighted regression over listings prices what is
*listed*, not what is *bought*.

## Variants

| Variant | Method | Index / adjustment |
|---|---|---|
| Time dummy | One pooled regression, a dummy per period | `100·exp(δ_t)`; also the Kennedy bias-corrected `100·exp(δ_t − V(δ_t)/2)` |
| Characteristics price | One regression per period | Laspeyres-type (base bundle at each period's coefficients), Paasche-type (current bundle), Fisher-type geometric mean; ratios of geometric mean predicted prices |
| Imputation | Per-period regressions predict a specific item's missing price | Single imputation `P̂_t(z_old)/p_old(t−1)`; double imputation `P̂_t(z_old)/P̂_{t−1}(z_old)` (default), so a model's systematic error for that bundle cancels |

A hedonic quality adjustment is `q = P̂(z_new)/P̂(z_old)` (characteristics
price) or the replacement's remaining gap after the imputed pure price
relative (imputation); both hand back the same `QualityAdjustment` object as
the non-hedonic methods, reason code `hedonic`, with the variant, functional
form, fit statistics, multicollinearity flag and the characteristics file's
vintage in the parameters — so the ledger names the data the valuation
depended on.

## Diagnostics reported, not buried

Adjusted R²; heteroskedasticity-robust (HC1) standard errors; a variance
inflation factor per regressor and the design's condition number; residual
and leverage series (charted); coefficient stability across rolling windows
of periods; k-fold out-of-sample error (RMSE in the regression's scale and
mean absolute percentage error in price levels). Severe multicollinearity
(VIF above 10 or condition number above 30) raises
`HedonicMulticollinearityWarning`, is recorded on the result and travels
with any adjustment read from it. It does not raise: fitted prices remain
usable for imputation even when individual coefficients are not.

## Citation

CPI Manual 2020, Chapter 6, "Hedonic approach" and Table 6.6 (washing
machines); the *Consumer Price Index Theory* companion (2025) for the
time-dummy and hedonic-imputation variants. Recovery of a known effect:
Appendix 2 test 6 of the platform specification (`tests/test_hedonic.py`).

## Assumptions and known biases

- Characteristics prices are stable within the estimation window; a pooled
  time-dummy model constrains them equal across periods, which is its known
  weakness and why stability across windows is reported.
- The specification captures the price-determining characteristics; an
  omitted characteristic correlated with an included one biases that
  coefficient, and a quality adjustment reads coefficients.
- Retransformation from the log scale gives a median prediction; the factor
  cancels in a ratio of two predictions, which is how the adjustments use it.
