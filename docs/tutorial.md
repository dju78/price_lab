# Onboarding tutorial: from the bundled fixture to a released bulletin

Forty minutes, using `supermarket_price_collection.xlsx` (ten categories,
132 months, 2015–2025, with the kinds of faults a real collection has).
You will compile it, value a replacement, register and approve the run,
release a bulletin, and then audit your own release back to a single quote.

## 0. Set up (5 minutes)

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
alembic upgrade head
python scripts/create_user.py --username admin --role administrator
streamlit run app.py
```

Sign in as `admin`. (An administrator can do everything below; in practice a
compiler compiles and an administrator approves.)

## 1. Ingest (10 minutes)

Upload `supermarket_price_collection.xlsx`, worksheet `Price_Data`.

- **Column mapping.** The suggestions score 1.0 for date, item, name,
  category and price; there is no weight column. Confirm. The mapping is
  remembered for this exact file.
- **Validation.** Two plausibility findings (medium and low), nothing
  critical, nothing blocks.
- **Automatic diagnosis.** Missing code `0`; a 13-period reference window;
  `class_mean` imputation for nine categories and `seasonal_hold` for
  Strawberries, because its gaps are calendar-aligned. Leave them.
- **Formula.** Jevons, chained. Compile.

Behind the scenes: the upload was written once to `store/raw/<content
hash>.parquet` with a receipt naming you, the file and the time; the
compile wrote `store/cleaned/<hash>.parquet` and its transformation log;
both were audited.

## 2. Read what it found (5 minutes)

**Quality**: 230 order-of-100 faults repaired by rescaling, each flagged, and no residual outliers left beyond tolerance.
**Imputation**: the response rate per category and period — look at
Strawberries out of season. **Findings**: the ranked narrative; the headline
finding is the peak twelve-month rate. **Diagnostics**: the final level under
each formula (Carli sits above Jevons above the harmonic mean, as the manual
says it must) and the chain-drift table (zero for Jevons, as it must be).

## 3. Value a replacement (10 minutes)

Open **Quality adjustment**. The candidates table is empty — the fixture has
no replacements — so make one to see the mechanics: on Ingest, download the
cleaned CSV, and in a copy rename item `I0001` to `I0001B` from January
2020 onward with its prices multiplied by 1.2; upload that as a new run.
Now the candidates table pairs `I0001` (left December 2019) with `I0001B`
(arrived January 2020).

Choose **Overlap pricing**? Not available: they were never priced in the same
month. Choose **Targeted mean imputation** with the other Biscuits items as
peers, write "new pack size, same product; peers move alike" as the
justification, and read the result before approving: the quality ratio, the
adjusted price, the part of the gap attributed to quality, and the effect on
the cell's link in index points. Approve. The run recompiles; the **Impact
on the headline** section states the adjustments' effect in points and in
percentage points of annual inflation, with the three scenarios charted.
The ledger row names you, the method, the justification and the time.

(For a hedonic valuation, upload a characteristics file — `item_id` plus one
column per characteristic — fit the model, and read its diagnostics before
using it; the file goes through the same raw layer, log, validation and
receipt as the prices.)

## 4. Register, approve, release (5 minutes)

On **Reports**, click **Register this run**. The registry now holds the
input's content hash, the full configuration (ledger included), the code
commit, the environment and the headline figure. Click **Approve** — the
run is immutable; a correction would be a new vintage with a reason.

Download the **PDF bulletin**. Its headline is the registry's number, not a
recomputation; its last page is the provenance stamp. Download the **Excel
evidence pack** and open the Elementary aggregates sheet: out-of-season
Strawberries cells read *suppressed* with the rule, and one small cell per
such month is suppressed alongside to protect it. Download the CSV and the
SDMX-ML file; both carry the same stamp.

## 5. Audit your own release (5 minutes)

```python
from pricelab.reporting import readback
stamp = readback.from_pdf(open("Supermarket prices bulletin.pdf", "rb").read())
stamp.run_id, stamp.data_vintage, stamp.code_version, stamp.quality_adjustments
```

The data vintage is the file name under `store/raw/`; `store/raw/<vintage>
.vintage.json` is the receipt; `store/cleaned/<vintage>.log.json` replays the
cleaned layer (`pricelab.data.store.replay`). In the evidence pack's Audit
extract sheet, find the `DATA_LOAD` event naming the raw file and the
`CALCULATION_RUN` event carrying the correlation id that is on every log
line of that run. `tests/test_end_to_end.py` performs this whole walk
automatically, ending by recomputing the headline from the raw layer and
the registered configuration.

## 6. Back up before you leave (2 minutes)

```bash
python scripts/backup.py --to backups
```

Then read `docs/admin_guide.md` section 4 and, once, restore it somewhere
harmless to see that it works.
