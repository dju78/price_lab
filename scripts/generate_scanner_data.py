"""Generate a synthetic scanner transaction file for exercising the
multilateral engine.

Run from the repository root as `python scripts/generate_scanner_data.py`;
it writes `tests/fixtures/scanner_transactions.csv` and
`tests/fixtures/scanner_characteristics.csv`.

Like `generate_synthetic_data.py`, this lives outside the pricelab package:
it manufactures a test fixture, and the product must not be built to expect
anything this script happens to produce.

The file is designed to have the properties that make multilateral methods
necessary rather than merely available, because a fixture without them would
let a broken implementation pass:

- Monthly, 30 periods, so a 25-month window leaves five periods to splice and
  a 13-month window leaves eighteen.
- Continuous product churn: each category holds a fixed number of shelf
  slots, and a slot's occupant is replaced after a randomised tenure of 5 to
  18 months. Around seventy percent of the period x product grid is empty,
  which is what strips a matched-model method of observations.
- A spine of staples alongside the churn: two products per category are on
  the shelf in every period. Real transaction data has these -- the own-label
  basics outlive every promotion around them -- and without them the fixture
  would be pathological rather than merely difficult: GEKS compares pairs of
  periods through bridge periods, and a window in which no product survives
  from one end to the other has no bridge at all, so GEKS would be untestable
  rather than tested. `tests/test_multilateral.py` covers that disconnected
  case separately, on a panel built to be disconnected.
- Price bounce with a quantity response: every product runs a promotion in
  scattered months, 20 to 35 percent off, during which it sells four to nine
  times its base volume -- and the volume stays elevated the month after the
  promotion ends, because households stockpile. That asymmetry between the
  price cycle and the quantity cycle is what makes a chained superlative
  index drift rather than merely wobble.
- Two stores per product-period, at slightly different prices and volumes,
  aggregated on the way out to one row per product-period at the unit value
  (total expenditure over total quantity). That aggregation is the ingestion
  step for scanner data and it happens here, in the fixture generator,
  because PriceLab's canonical schema is one row per period and item --
  duplicate period/item pairs are a validation error, since a matched index
  over them is undefined. `engine.multilateral.build_panel` performs the same
  collapse defensively for a caller that hands it raw transactions;
  `tests/test_multilateral.py` exercises that path on a synthetic frame.
- A quality gradient: each product has a size and a brand, and its price
  level is a function of both plus a product-specific residual. The
  characteristics file carries them, so the time dummy hedonic has something
  real to recover and its index can be compared against the others.
- A common inflation trend of about 3 percent a year running underneath all
  of it, so there is a right answer for the methods to disagree about.

Nothing here is read by the pricelab package.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

SEED = 20260922
rng = np.random.default_rng(SEED)

START, N_PERIODS = "2023-07-01", 30
PERIODS = pd.date_range(START, periods=N_PERIODS, freq="MS")

#: Annual drift applied to every product, compounded monthly.
ANNUAL_TREND = 0.03
MONTHLY_TREND = (1 + ANNUAL_TREND) ** (1 / 12)

# name, churning shelf slots, staple products, base price, price dispersion
CATEGORIES = [
    ("Coffee", 5, 2, 6.50, 0.30),
    ("Detergent", 4, 2, 9.00, 0.35),
    ("Yoghurt", 6, 2, 1.80, 0.25),
    ("Biscuits", 5, 2, 2.40, 0.30),
]
BRANDS = ("own-label", "mid", "premium")
BRAND_EFFECT = {"own-label": -0.25, "mid": 0.0, "premium": 0.35}
STORES = ("north", "south")


def main() -> None:
    rows: list[dict[str, object]] = []
    characteristics: list[dict[str, object]] = []
    serial = 0

    for category, slots, staples, base_price, dispersion in CATEGORIES:
        for slot in range(slots + staples):
            staple = slot >= slots
            t = 0
            # Stagger the first replacement so the whole category does not
            # turn over in the same month, which would make churn a single
            # cliff rather than the continuous process it is in real data.
            # A staple slot never turns over: one product, every period.
            tenure = N_PERIODS if staple else int(rng.integers(3, 18))
            while t < N_PERIODS:
                serial += 1
                item_id = f"{category[:3].upper()}{serial:04d}"
                size = float(np.round(rng.uniform(0.25, 2.5), 3))
                brand = str(rng.choice(BRANDS, p=[0.4, 0.4, 0.2]))
                # Price level: size and brand explain most of it, and a
                # product-specific residual the rest. The residual is what
                # stops the hedonic regression fitting perfectly, which is
                # the honest case -- a fixture with no residual would let a
                # time dummy hedonic with a bug still look excellent.
                quality = np.exp(0.55 * np.log(1 + size) + BRAND_EFFECT[brand]
                                 + rng.normal(0, dispersion * 0.4))
                level = base_price * quality
                volume = float(rng.uniform(40, 260))
                characteristics.append({
                    "Item_ID": item_id, "Size": size, "Brand": brand,
                    "Category": category})

                end = min(t + tenure, N_PERIODS)
                promo_months = set(rng.choice(
                    np.arange(t, end), size=max(1, (end - t) // 4), replace=False).tolist()
                ) if end > t else set()
                for period_index in range(t, end):
                    promo = period_index in promo_months
                    after_promo = (period_index - 1) in promo_months
                    discount = float(rng.uniform(0.20, 0.35)) if promo else 0.0
                    noise = float(rng.normal(0, 0.012))
                    price = (level * (MONTHLY_TREND ** period_index)
                             * (1 - discount) * np.exp(noise))
                    if promo:
                        multiplier = float(rng.uniform(4.0, 9.0))
                    elif after_promo:
                        multiplier = float(rng.uniform(1.4, 2.2))
                    else:
                        multiplier = float(rng.uniform(0.85, 1.15))
                    for store in STORES:
                        store_price = price * float(rng.uniform(0.97, 1.03))
                        store_quantity = volume * multiplier * float(rng.uniform(0.6, 1.4))
                        rows.append({
                            "Date": PERIODS[period_index].strftime("%Y-%m-%d"),
                            "Category": category,
                            "Item_ID": item_id,
                            "Item_Name": f"{brand} {category.lower()} {size:g}kg",
                            "Store": store,
                            "Reported_Price": round(store_price, 4),
                            "Quantity": round(store_quantity, 2),
                            "Expenditure": round(store_price * store_quantity, 4),
                            "Unit": "pack",
                        })
                t = end
                tenure = N_PERIODS if staple else int(rng.integers(5, 18))

    fixtures = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    transactions = pd.DataFrame(rows)
    # Collapse the stores to one product-period row at the unit value. Summing
    # quantity and expenditure and dividing is the only defensible single
    # price for a product-period: taking the mean of the two store prices
    # would weight a store that sold three packs the same as one that sold
    # three hundred.
    grouped = (transactions.groupby(["Date", "Category", "Item_ID", "Item_Name", "Unit"],
                                    as_index=False)[["Quantity", "Expenditure"]].sum())
    grouped["Reported_Price"] = (grouped["Expenditure"] / grouped["Quantity"]).round(4)
    grouped["Quantity"] = grouped["Quantity"].round(2)
    grouped["Expenditure"] = grouped["Expenditure"].round(4)
    frame = grouped[["Date", "Category", "Item_ID", "Item_Name", "Reported_Price",
                     "Quantity", "Expenditure", "Unit"]].sort_values(
        ["Date", "Category", "Item_ID"])
    frame.to_csv(fixtures / "scanner_transactions.csv", index=False)
    pd.DataFrame(characteristics).to_csv(fixtures / "scanner_characteristics.csv", index=False)

    products = frame["Item_ID"].nunique()
    grid = len(PERIODS) * products
    print(f"{len(transactions):,} store transactions collapsed to {len(frame):,} "
          f"product-period rows, {products} products over {len(PERIODS)} periods")
    print(f"grid occupancy {len(frame) / grid:.1%}")


if __name__ == "__main__":
    main()
