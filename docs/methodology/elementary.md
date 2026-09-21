# Elementary aggregate formulae (`engine/elementary.py`, `engine/index.py`)

The unweighted formulae applied below the level at which expenditure weights
exist. Every result carries the matched sample size, the count of imputed
values among them, the formula and its parameters, because an index of 103.2
over four matched items, two of them imputed, is a different claim from the
same number over forty observed ones.

| Formula | Definition | Citation | Known bias |
|---|---|---|---|
| Jevons | `Π (p_t/p_0)^(1/n)` — geometric mean of relatives (= ratio of geometric mean prices) | Ch. 8, eq. 8.1; recommended default, para. 8.86 | Assumes unit elasticity of substitution; sits below Carli and above the harmonic mean by the arithmetic ≥ geometric ≥ harmonic inequality (para. 8.85). Transitive, so chains without drift (para. 8.18). |
| Dutot | `(Σp_t/n)/(Σp_0/n)` — ratio of arithmetic mean prices | Ch. 8, eq. 8.3 | Fails commensurability: changing one item's unit of measurement changes the index. Safe only over homogeneous items priced in the same unit. Transitive. |
| Carli | `(1/n) Σ (p_t/p_0)` — arithmetic mean of relatives | Ch. 8, eq. 8.5 | Upward; fails time reversal, so a chained Carli accumulates bias — the manual's Table 8.3 chains it over seven months of prices that end where they began and reaches 106.7. "Should be avoided" chained (para. 8.19). |
| Harmonic mean | `n / Σ (p_0/p_t)` | Ch. 8, eq. 8.10 | Downward; the time antithesis of Carli, failing time reversal by the same amount in the opposite direction. |
| CSWD | `sqrt(Carli × Harmonic)` | Carruthers, Sellwood & Ward (1980), Dalén (1992); CPI Manual Theory, Ch. 6 | Near-cancelling: satisfies time reversal exactly and equals Jevons to second order. |
| Unit value | `(Σp_t q_t/Σq_t)/(Σp_0 q_0/Σq_0)` | Ch. 8, para. 8.87 | Unbiased only for strictly homogeneous items whose quantities are additive; over a mixed set it reports a change in purchase mix as price change, invisibly. The engine refuses to compute it without an explicit, recorded homogeneity assertion. |

## Golden values

`tests/test_golden_values.py` reproduces CPI Manual 2020 Chapter 8, Tables
8.1–8.3, at the manual's published precision, and supplies a hand-calculated
two-good example (with the arithmetic in the test) for the superlative
formulae the manual prints no worked example of.

## Assumptions

Matching before averaging: only items priced in both periods enter a
comparison. Prices are positive; a zero is a missing-value sentinel, recoded
before any formula runs ([data_quality.md](data_quality.md)).

## Which to choose

The manual recommends Jevons for elementary aggregates of close substitutes.
Dutot is appropriate for genuinely homogeneous items in one unit. Carli is
implemented so that its bias can be demonstrated, not so that it can be used.
The choice is recorded in the run configuration and named in every export.
