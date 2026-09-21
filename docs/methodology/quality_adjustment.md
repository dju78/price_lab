# Quality adjustment (`engine/quality_adjustment.py`)

## The problem

A matched-model index compares an item only with itself. When an item leaves
and another arrives, the price gap between them — partly quality, partly
price — is never measured. That is itself a quality adjustment, an implicit
one that attributes the whole gap to quality, and it is systematically wrong:
old models leave at run-out prices, new ones arrive at launch prices.

Each method values the difference as a **quality ratio** `q` (the new item's
worth relative to the old, in price terms). Everything follows from `q`:

| Quantity | Definition |
|---|---|
| adjusted new price | `p_new / q` — the replacement in old-quality terms; the old series continues at this |
| adjustment in price terms | `p_new − p_new/q` — the part of the collected gap attributed to quality |
| pure price relative | `(p_new/q) / p_old` — what the index records across the replacement |
| index points | effect on the cell's link of applying the adjustment rather than treating the raw gap as price: Jevons `100(q^(−1/n) − 1)`, Carli `100(raw/q − raw)/n`, Dutot `100 p_new(1/q − 1)/Σp_prev` |

Multiplicative throughout (the manual's general advice; a fixed additive
worth such as a shipping charge is expressed through `option_cost`).

## Methods

| Method | `q` | Citation (CPI Manual 2020, Ch. 6) | Assumption / bias |
|---|---|---|---|
| Overlap pricing | `p_new(overlap) / p_old(overlap)` | "Overlap method", eqs 6.5–6.8 | The overlap period priced only the quality difference — no launch premium, no clearance discount. Replace before the old model's end of life. |
| Direct comparison | `1` | collector code C | The replacement is judged comparable; the judgement is the method, so a justification is recorded |
| Quantity adjustment | `q_new / q_old` | "Quantity adjustment", Table 6.5 | Price proportional to size — true for modest size changes and unit-priced goods, not for large ones |
| Option cost | `(p_old ± v·share) / p_old` | "Differences in feature/option costs": 10,500/10,300 = 1.01942 | A feature made standard is usually worth less than its option price; `share_valued` records the judgement |
| Targeted mean | `(p_new/p_old) / GM(peer relatives)` | "Targeted mean imputation": 5.99 → 6.26 by outlets D and E | The old item moved like a chosen set of similar items |
| Overall mean | as above over the whole aggregate | "Overall mean imputation", eq. 6.4 | The old item moved like everything; numerically equal to dropping it |
| Class mean | `(p_new/p_old) / GM(other replacements' adjusted relatives)` | "Class mean imputation" | Continuing items are a flawed proxy for a replacement's pure price component; needs other valued replacements in the class |
| Link to show no change | `p_new / p_old` | Table 6.4a: 28/25 × 35/35 = 1.12 | The whole gap is quality. "Should not be used"; implemented so it can be named, and it warns |
| Hedonic | `P̂(z_new) / P̂(z_old)` or via imputed relative | [hedonic.md](hedonic.md) | — |

## The ledger

An approved valuation is a `QualityAdjustmentEntry` in
`RunConfig.quality_adjustment`: old item, new item, period, method, ratio,
parameters, justification, approver, time. Because it is configuration, the
registry's content hash, the cache key, `reproduce()` and every export's
provenance stamp cover it with no second mechanism. The approval record
behind it is the `quality_adjustments` table, keyed by the input data's
content hash, so a re-uploaded collection brings its approved replacements
back; withdrawals are kept as rows, never deleted. One replacement per old
item, one continuation per new item.

`apply_adjustments` links the replacement onto the old item's series before
imputation and indexing: from the link period the new item's rows carry the
old identifier with prices divided by `q`, flagged (`quality_adjustment_flag`,
`replacement_flag`, `replaced_item_id`); every row moved or dropped is
logged. `price_reported` is never altered.

## The impact report

The most scrutinised number in a price index, computed as a first-class
result: the pipeline is run as configured, with every replacement linked at
ratio 1, and with none linked (the matched-model default). The differences
are the adjustments' effect and the linking's effect, in index points at
the final period and in percentage points of annual inflation (year on year
where the span allows, annualised over the span otherwise — and the report
says which). Per-entry attribution is leave-one-out on the ratio, with the
interaction residual reported as its own row so the column sums exactly.
It appears on the Quality adjustment page, in the Word and Markdown
reports, on its own deck slide, in the bulletin and in the evidence pack.

## Golden values

`tests/test_quality_adjustment.py` reproduces equation 6.4, the targeted-mean
chain, Table 6.4a, Table 6.5 and the option-cost example from the published
Chapter 6 text. Table 6.1's full tableau and Table 6.6's regression data are
not printed there, so Table 6.2's whole chain and Table 6.6's coefficients
are not asserted.
