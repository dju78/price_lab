# Chain linking, rebasing, splicing and chain drift (`engine/splicing.py`)

## Operations

| Operation | Definition | Changes movements? |
|---|---|---|
| Rebase | `I'_t = I_t / I_r × base_value` for a reference period `r` in the series | No: one constant cancels from every ratio |
| Link factor | `f = old(overlap) / new(overlap)` | No |
| Splice | old series up to the overlap, `new × f` from it | No: each series' own movements are kept; the join assumes both measure the same thing there |
| Chain | `I_t = base_value × Π_{k≤t} link_k`, a non-finite link holding the level | Yes: a chained and a direct index over the same span are different numbers |
| Price update | `series(to) / series(from)` | The factor carrying a weight from the weight reference to the price reference period |

## Chain drift

`chain_drift` rebases a chained and a direct series to their first common
period and reports the gap at the last, in index points and as a share of
the direct level, against a configurable threshold (default 1 point). A
transitive elementary formula (Jevons, Dutot) gives exactly zero; Carli does
not; weighted chained indices drift in either direction when quantities lag
prices — the manual's Table 10.1 shows a chained Törnqvist ending 22 percent
below the direct index on data where nothing net changed.

## Citation

CPI Manual 2020, Chapter 9 (chaining, paras 9.101–9.117; rebasing;
link factors and the introduction of a new basket, paras 9.118 onward) and
Chapter 8, para. 8.18 and Table 8.3 (chained Carli). Chain drift diagnostic:
Appendix 2 test 5 of the platform specification.

## Assumptions

Rebasing needs the reference period to be in the series: an absent period is
refused rather than interpolated, because an interpolated divisor would be a
fabricated number under every published level. Splicing assumes the two
series measure the same thing at the overlap — if the basket changed, the
error is there, not in the arithmetic.

## Known biases

Chaining is not neutral. It is chosen because it lets a sample be refreshed
every period, and paid for in drift wherever the formula or the data is not
transitive. The diagnostic exists so the price is stated on every run.
