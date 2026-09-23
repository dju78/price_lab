# Construction price indices (`engine/construction.py`)

## Two indices, two questions

| | Input cost index | Output price index |
|---|---|---|
| Question | What do the inputs of construction cost the builder? | What does finished construction cost the client? |
| Measures | Materials, labour, plant and energy prices at fixed cost shares | The price of a fixed specification, including margins and overheads |
| Productivity and margins | Invisible to it | Included |
| Use | Escalating a contract's input costs; a builder's own costs | Deflating construction output; what building has cost the buyer |

They are routinely confused, so `CONCEPTS` states the difference on every
result and the page shows both statements before either number.

**Input cost index**: a Laspeyres-type weighted average of input price
relatives, Σ_i s_i I_i(t)/I_i(base) × 100. Every input must have a cost share
and every cost share an input — a share with no index behind it would be held
flat without anyone noticing.

**Output price index**: a fixed bill of quantities priced at each period's
tender rates, Σ_k q_k r_k(t) / Σ_k q_k r_k(base) × 100. The specification
never changes, so a change in what is built is not mistaken for a change in
price. A period that does not price every bill item is left out with a note,
not priced without the missing item.

**The gap between them** (`input_output_gap`, output over input × 100) is not
an error: it is the implied movement in contractors' margins and
productivity together. Inputs up 10% and output prices up 5% means clients
paid 105/110 of what input costs alone would suggest.

## Citation

OECD and Eurostat, *Construction Price Indices: Sources and Methods*
(1997); Eurostat, *Methodological Manual for
Short-term Business Statistics*, on construction costs and construction
output prices.

## What the module does not do

No hedonic or model-pricing output index, no matched-contract (repeat
tender) method, and no seasonal treatment. The bill of quantities is the
user's: its representativeness is what the output index rests on.
