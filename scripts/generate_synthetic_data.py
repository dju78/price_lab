"""Generate a synthetic supermarket price collection for exercising PriceLab.

Run from the repository root as `python scripts/generate_synthetic_data.py`;
it writes `supermarket_price_collection.xlsx` next to this script's parent
directory (the project root).

This script is deliberately kept outside the pricelab package: it manufactures a
test fixture, it is not part of the analytical product, and the product must not
be built to expect anything this script happens to produce.

Design, so the file has the properties the brief asks PriceLab to handle:

- Monthly collection, January 2015 to December 2025 (132 periods).
- 10 categories, 87 items in total across the whole period (not concurrently).
- Item churn: each category is a fixed number of shelf "slots", each slot
  occupied by a sequence of items with randomised tenure. A departing item is
  replaced in the same slot the following month, at a price level nudged above
  or below the category's going rate by a per-category bias (some replacements
  land dear, some land cheap), which is what the sample-rotation finding
  measures.
- Sentinel zeros from three distinct mechanisms:
    1. Seasonal unavailability (Strawberries): off the shelf in the same seven
       calendar months every year.
    2. Collection failure (Pasta): every item unpriced for three consecutive
       months in 2020, a one-off, non-recurring shock.
    3. Sporadic (all categories): scattered, independent, single-item gaps.
- Unit errors: ~3.5% of genuine observations rescaled by exactly 100x or
  0.01x, away from the edges of each item's run so a centred rolling median
  has neighbours on both sides.
- Category-specific seasonality in price (not just availability): amplitude
  and peak month vary by category, including two categories with strong
  seasonal swings and several with almost none.
- Category-specific trend, including one category in sustained deflation.

Nothing about these choices is read by the pricelab package; they only make
the fixture worth analysing.
"""
import pathlib

import numpy as np
import pandas as pd

SEED = 20250115
rng = np.random.default_rng(SEED)

START, END = "2015-01-01", "2025-12-01"
PERIODS = pd.date_range(START, END, freq="MS")
N_PERIODS = len(PERIODS)

# name, slots (concurrent shelf positions), total items ever in that slot set,
# annual growth, seasonal amplitude (fraction), peak calendar month,
# starting price, entry bias for replacements (fraction vs category level)
CATEGORIES = [
    dict(name="Biscuits",           slots=5, n_items=8,  growth=0.041, amp=0.19, peak=12, price0=1.20, entry_bias=-0.05),
    dict(name="Bread",              slots=5, n_items=8,  growth=0.024, amp=0.02, peak=4,  price0=1.10, entry_bias=-0.01),
    dict(name="Chicken",            slots=5, n_items=7,  growth=0.040, amp=0.03, peak=12, price0=5.50, entry_bias=0.18),
    dict(name="Coffee",             slots=5, n_items=10, growth=0.014, amp=0.11, peak=1,  price0=3.80, entry_bias=0.09),
    dict(name="DVDs",               slots=5, n_items=8,  growth=-0.011, amp=0.14, peak=12, price0=8.00, entry_bias=0.02),
    dict(name="Ice cream",          slots=5, n_items=9,  growth=0.039, amp=0.39, peak=7,  price0=2.50, entry_bias=0.01),
    dict(name="Laundry detergent",  slots=5, n_items=8,  growth=0.027, amp=0.02, peak=5,  price0=4.00, entry_bias=-0.03),
    dict(name="Pasta",              slots=6, n_items=13, growth=0.035, amp=0.02, peak=11, price0=0.90, entry_bias=-0.025),
    dict(name="Strawberries",       slots=4, n_items=6,  growth=0.012, amp=0.02, peak=8,  price0=2.00, entry_bias=0.005),
    dict(name="Sun cream",          slots=5, n_items=10, growth=0.025, amp=0.36, peak=7,  price0=6.50, entry_bias=-0.05),
]
assert sum(c["n_items"] for c in CATEGORIES) == 87

STRAWBERRY_OFF_MONTHS = {1, 2, 3, 4, 10, 11, 12}   # 7 months off shelf
PASTA_COLLECTION_GAP = pd.date_range("2020-03-01", "2020-05-01", freq="MS")
SPORADIC_GAP_RATE = 0.006

item_counter = 0
rows = []
flagged_truth = []   # for our own sanity check, not read by pricelab


def item_id():
    global item_counter
    item_counter += 1
    return f"I{item_counter:04d}"


def make_lifespans(n_periods, n_items):
    """Split n_periods months among n_items sequential tenures, each >= 4
    months, in a random order so short and long tenures are interleaved."""
    if n_items == 1:
        return [n_periods]
    cuts = sorted(rng.choice(range(4, n_periods - 4 * (n_items - 1)), size=n_items - 1, replace=False)) \
        if n_periods - 4 * n_items > n_items - 1 else None
    # Fallback to a simple stick-breaking split guaranteeing a minimum tenure.
    remaining = n_periods
    spans = []
    for k in range(n_items - 1):
        max_span = remaining - 4 * (n_items - 1 - k)
        min_span = 4
        if max_span <= min_span:
            span = min_span
        else:
            span = int(rng.integers(min_span, max_span))
        spans.append(span)
        remaining -= span
    spans.append(remaining)
    return spans


for cat_num, cat in enumerate(CATEGORIES, start=1):
    name = cat["name"]
    slots = cat["slots"]
    n_items = cat["n_items"]
    # Distribute n_items across slots as evenly as possible.
    base, extra = divmod(n_items, slots)
    per_slot = [base + (1 if s < extra else 0) for s in range(slots)]

    monthly_growth = (1 + cat["growth"]) ** (1 / 12) - 1

    for slot in range(slots):
        k = per_slot[slot]
        if k == 0:
            continue
        spans = make_lifespans(N_PERIODS, k)
        cursor = 0
        prev_level_at_cutover = None
        for j, span in enumerate(spans):
            iid = item_id()
            iname = f"{name} item {iid[1:]}"
            idx0 = cursor
            idx1 = min(cursor + span, N_PERIODS)
            level = cat["price0"] * (1 + rng.normal(0, 0.15))
            if j > 0 and prev_level_at_cutover is not None:
                level = prev_level_at_cutover * (1 + cat["entry_bias"] + rng.normal(0, 0.03))
            for t in range(idx0, idx1):
                age = t - idx0
                trend = (1 + monthly_growth) ** t
                month = PERIODS[t].month
                seasonal = 1 + cat["amp"] * np.cos(2 * np.pi * (month - cat["peak"]) / 12)
                noise = 1 + rng.normal(0, 0.015)
                price = level * trend / ((1 + monthly_growth) ** idx0) * seasonal * noise
                rows.append(dict(Date=PERIODS[t], Category_Num=cat_num, Category=name,
                                  Item_ID=iid, Item_Name=iname, Reported_Price=round(max(price, 0.01), 4),
                                  _slot=slot, _age=age, _span=span))
            prev_level_at_cutover = rows[-1]["Reported_Price"] if idx1 > idx0 else level
            cursor = idx1

df = pd.DataFrame(rows)

# --- Sentinel zeros: three mechanisms -------------------------------------
# 1. Seasonal unavailability: Strawberries off shelf in fixed calendar months.
mask_season = (df["Category"] == "Strawberries") & (df["Date"].dt.month.isin(STRAWBERRY_OFF_MONTHS))
df.loc[mask_season, "Reported_Price"] = 0
flagged_truth += [("seasonal", i) for i in df.index[mask_season]]

# 2. Collection failure: every Pasta item unpriced for three consecutive months in 2020.
mask_collection = (df["Category"] == "Pasta") & (df["Date"].isin(PASTA_COLLECTION_GAP))
df.loc[mask_collection, "Reported_Price"] = 0
flagged_truth += [("collection", i) for i in df.index[mask_collection]]

# 3. Sporadic: independent scattered single-item gaps, away from the two
#    mechanisms above so they do not get folded into those counts.
eligible = df.index[~(mask_season | mask_collection)]
n_sporadic = int(len(eligible) * SPORADIC_GAP_RATE)
sporadic_idx = rng.choice(eligible, size=n_sporadic, replace=False)
df.loc[sporadic_idx, "Reported_Price"] = 0
flagged_truth += [("sporadic", i) for i in sporadic_idx]

# --- Unit errors: exactly 100x or exactly 0.01x, away from the run edges ---
# Restrict to observations at least 2 months from either end of their item's
# life and not already a sentinel zero, so the local rolling median used for
# detection has genuine neighbours on both sides.
non_zero = df.index[(df["Reported_Price"] > 0) & (df["_age"] >= 2) & (df["_age"] <= df["_span"] - 3)]
n_errors = int(len(df) * 0.035)
error_idx = rng.choice(non_zero, size=n_errors, replace=False)
half = len(error_idx) // 2
up_idx, down_idx = error_idx[:half], error_idx[half:]
df.loc[up_idx, "Reported_Price"] = (df.loc[up_idx, "Reported_Price"] * 100).round(4)
df.loc[down_idx, "Reported_Price"] = (df.loc[down_idx, "Reported_Price"] * 0.01).round(4)

df = df.drop(columns=["_slot", "_age", "_span"])
df = df.sort_values(["Category_Num", "Item_ID", "Date"]).reset_index(drop=True)

print("rows:", len(df))
print("items:", df.Item_ID.nunique())
print("categories:", df.Category.nunique())
print("period range:", df.Date.min(), df.Date.max(), "periods:", df.Date.nunique())
print("zero rows:", int((df.Reported_Price == 0).sum()))
print("up errors:", len(up_idx), "down errors:", len(down_idx))
print(df.groupby("Category")["Item_ID"].nunique())

data_dict = pd.DataFrame([
    dict(Column="Date", Description="First day of the collection month (monthly periodicity)."),
    dict(Column="Category_Num", Description="Numeric code for the product category, 1 to 10."),
    dict(Column="Category", Description="Product category name."),
    dict(Column="Item_ID", Description="Unique identifier for a priced item. Retired items are never reissued."),
    dict(Column="Item_Name", Description="Descriptive name of the item, one per Item_ID."),
    dict(Column="Reported_Price", Description="Price as collected, in GBP. 0 denotes unavailable/not collected, not a free item."),
])

out_path = pathlib.Path(__file__).resolve().parent.parent.parent / "supermarket_price_collection.xlsx"
with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
    df.to_excel(xw, sheet_name="Price_Data", index=False)
    data_dict.to_excel(xw, sheet_name="Data_Dictionary", index=False)
print("written:", out_path)
