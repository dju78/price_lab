# Contract escalation (`engine/escalation.py`)

## What the module computes

A payment schedule under an indexation clause, and the clause restated in
plain language. The arithmetic is simple; the risk is presentational. A
schedule that a contract manager cannot check against the contract wording
without reading code is a dispute waiting to happen, so every result carries
a summary written for that reader.

## The clause, in the order it is applied

For each payment period t:

1. **Index reading**: the index `lag` periods before t, or the average of
   the `averaging` periods ending there; the base reading is taken the same
   way at the base period. A reading not yet published is refused — a
   payment is not made on an estimated index.
2. **Movement**: m = (reading / base reading − 1) × 100.
3. **Dead band**: a movement within ±d% is ignored; beyond it, either only
   the excess passes through ("excess", the usual drafting) or the whole
   movement does ("full").
4. **Trigger**: the price is re-set only when the movement since the last
   re-set reaches the trigger; until then the last adjusted price stands.
5. **Indexation factor**: only the share f of the amount is indexed and
   1 − f is fixed; adjustment a = f × movement.
6. **Cap and collar**: a is held to at most +cap% and at least −collar% of
   the base amount.
7. **Payment** = base amount × (1 + a/100).

Nothing is rounded until the schedule is displayed.

## The summary

`EscalationResult.summary` names the index; **which vintage of it** (a
registered run, an official release with its retrieval time and response
hash, or — for an uploaded index — a vintage the user must state before the
page will build a schedule); the base reading; the lag; the indexed share;
the dead band, trigger, cap and collar; the order they are applied in; the
totals with and without the limits; and, for **every period in which a cap,
collar, dead band or trigger changed the payment**, a sentence giving the
index reading, the movement, the adjustment, the limit that bound, and the
payment it would otherwise have been. The page shows the summary before the
schedule and offers it as a text download beside the CSV.

A session compilation that is not a registered vintage is named as such in
the summary, with the advice to register and approve the run before using it
in a contract.

## The worked example

`tests/test_escalation.py` reproduces a four-payment contract (base
GBP 100,000, 80% indexed, two-month lag, cap +5%, collar −3%) with the
arithmetic written out in the module docstring: the cap binds in June
(GBP 105,000 instead of 108,000) and the collar in July (GBP 97,000 instead
of 96,000), and the summary is checked sentence by sentence.

## Citation

The mechanics follow common drafting of price adjustment clauses, e.g. the
FIDIC conditions of contract (sub-clause 13.8 in the 1999 editions,
"Adjustments for Changes in Cost", with its fixed non-adjustable portion)
and the NEC Option X1 price adjustment for inflation, both of which index a
share of the amount on a lagged published index against a base date.

## What the module does not do

No multi-index (weighted basket) formulas, no provisional-then-final payment
when a lagged index is later revised, no currency conversion, and no
interest on late adjustments. A revision to the index after a payment is a
contractual matter the summary makes visible — by naming the vintage used —
but does not settle.
