# Bilateral aggregate indices (`engine/bilateral.py`)

Weighted comparisons of two periods using quantities or expenditure shares.
Notation: `p` price, `q` quantity, `s` expenditure share, period 0 the price
reference period, `t` the current period, `b` the weight reference period.

| Index | Definition | Citation | Known bias |
|---|---|---|---|
| Laspeyres | `Σp_t q_0 / Σp_0 q_0` | Ch. 8, paras 8.89–8.95 | Upward against a cost-of-living index: no credit for substitution away from what became dearer. |
| Paasche | `Σp_t q_t / Σp_0 q_t` | Ch. 8, paras 8.89–8.95 | Downward, the mirror; needs current quantities, so impractical as a live CPI. |
| Fisher | `sqrt(Laspeyres × Paasche)` | Ch. 8, para. 8.91 | Superlative; satisfies time reversal and factor reversal. The legs are reported with it, because a Fisher between legs twenty points apart is a weaker number than one whose legs agree. |
| Törnqvist | `Π (p_t/p_0)^((s_0+s_t)/2)` | Ch. 8, para. 8.91 | Superlative; exact for translog preferences; fails factor reversal. |
| Walsh | `Σp_t √(q_0 q_t) / Σp_0 √(q_0 q_t)` | Ch. 8, para. 8.91 | Superlative; prices a real intermediate basket. |
| Marshall-Edgeworth | `Σp_t (q_0+q_t) / Σp_0 (q_0+q_t)` | Ch. 8, para. 8.91 | Not superlative; the arithmetic-mean basket is dominated by the larger-quantity period. |
| Lowe | `Σp_t q_b / Σp_0 q_b` | Ch. 9, eq. 9.6, paras 9.57–9.59; Ch. 8 paras 8.100–8.104 | What most offices publish as "Laspeyres-type". Inherits Laspeyres' upward bias and adds to it as the gap between `b` and 0 grows. |
| Young | `Σ s_b (p_t/p_0) / Σ s_b` | Ch. 9, paras 9.37–9.38, 9.59 | Holds shares fixed (implicit unit elasticity) where Lowe holds quantities fixed (zero elasticity). The manual is explicit that neither can be said a priori to be higher. |
| Geometric Laspeyres / Paasche | `Π (p_t/p_0)^(s_0)` / `Π (p_t/p_0)^(s_t)` | Ch. 8, para. 8.99 | Below their arithmetic counterparts; their geometric mean is the Törnqvist. |

## Price updating

`price_update_shares` carries period-`b` shares to period-0 prices holding
quantities fixed: `s_0i = s_bi (p_0i/p_bi) / Σ_j s_bj (p_0j/p_bj)` (Ch. 9,
paras 9.37–9.38, Table 9.2). A Young index on price-updated shares *is* the
Lowe index, so `price_updating_effect` computes both over the same data and
reports the gap as the entire effect of the choice — the number an office
needs when it moves from one to the other. If no update ratio is computable
the function refuses rather than silently returning Young under Lowe's name.

## Axioms proven in the suite

Identity, proportionality, commensurability (Jevons, Fisher, Törnqvist
invariant; Dutot shown not to be), time reversal (Fisher, Törnqvist, Jevons,
Dutot, CSWD to machine precision; Carli and harmonic mean shown to fail),
factor reversal (Fisher price × Fisher quantity = value ratio), and the
Laspeyres ≥ Fisher ≥ Paasche ordering under substitution with the bias
quantified — all property-based with Hypothesis (`tests/test_axioms.py`).

## Scope note

`engine.index.laspeyres`, the formula the interface offers, is the *Young*
form (a share-weighted mean of relatives) and falls back to Jevons with no
weights. The quantity-basket forms here are library-level: the upload schema
carries no quantities yet, so they are not selectable from the Ingest page.
