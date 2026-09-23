"""HM Land Registry price paid data, as property transactions.

Contains HM Land Registry data (c) Crown copyright and database right. The
data is licensed under the Open Government Licence v3.0.

The file has no header row: sixteen columns in a published order
(`PPD_COLUMNS`), every field quoted. `data.loaders.read_upload` recognises it
as headerless and numbers the columns; `parse_price_paid` names them and
turns the records into the shape `engine/asset.py` takes, applying the
rules below. Every rule is stated, every exclusion is counted by reason in
`PricePaidResult.findings`, and nothing is dropped without a line there.

What the real data holds that constructed data did not, and what is done
about it (2024 file, 930,559 records):

category B          18% of records are "additional price paid": repossessions,
                    buy-to-let purchases, transfers to companies. Not market
                    sales between individuals; excluded, as the UK House
                    Price Index excludes them.
property type O     "other" (non-residential, and all category B); excluded.
price bounds        prices from GBP 1 to GBP 180 million: nominal transfers at
                    one end, bulk and portfolio sales recorded against one
                    address at the other. Excluded outside [10,000, 5,000,000]
                    -- a stated bound, not a statistical one.
duplicates          the same property, date and price recorded twice;
                    the repeat is dropped.
no postcode         no reliable property identity; kept for the methods that
                    need none, left out of repeat sales.
no characteristics  no floor area, no rooms, no appraisal. The hedonic index
                    uses what there is (type, tenure, new build, county); the
                    mix-adjusted mean uses cells of type, tenure and new build
                    instead of size bands; the sale price appraisal ratio
                    cannot be computed at all.
re-sales            within one year, re-sales are mostly not price change:
                    a third are on the same day, the median gap is 44 days,
                    and price ratios run from 0.05 to 19.6. Repeat sales pairs
                    closer than six months are excluded (the interval the
                    S&P CoreLogic Case-Shiller indices also exclude), as are
                    pairs whose first sale was a new build (the new-build
                    premium is not price change) and pairs whose price moved
                    by more than a factor of two either way.
leasehold           23% of sales, and a majority in some districts (houses as
                    well as flats in parts of the north-west). Kept, and used
                    as a characteristic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "PPD_COLUMNS",
    "PricePaidResult",
    "parse_price_paid",
]

#: The published column order of the price paid data.
PPD_COLUMNS: tuple[str, ...] = (
    "transaction_id", "price", "date", "postcode", "property_type", "old_new", "duration",
    "paon", "saon", "street", "locality", "town", "district", "county", "ppd_category",
    "record_status")

PROPERTY_TYPES = {"D": "Detached", "S": "Semi-detached", "T": "Terraced", "F": "Flat",
                  "O": "Other"}
PRICE_BOUNDS = (10_000.0, 5_000_000.0)


@dataclass(frozen=True)
class PricePaidResult:
    transactions: pd.DataFrame
    """property_id, period (month), price, stratum (property type), new_build,
    leasehold, county, district: the shape `engine/asset.py` takes."""
    findings: pd.DataFrame
    """One row per rule: what it found, and what was done."""
    records: int
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def used(self) -> int:
        return len(self.transactions)


def parse_price_paid(raw: pd.DataFrame, *, price_bounds: tuple[float, float] = PRICE_BOUNDS
                     ) -> PricePaidResult:
    """Name the columns of a headerless price paid read and apply the rules
    in the module docstring. `raw` may also already carry the column names."""
    if list(raw.columns[:len(PPD_COLUMNS)]) != list(PPD_COLUMNS):
        if raw.shape[1] != len(PPD_COLUMNS):
            raise ValueError(
                f"price paid data has {len(PPD_COLUMNS)} columns in a fixed order; this has "
                f"{raw.shape[1]}")
        raw = raw.set_axis(list(PPD_COLUMNS), axis=1)
    frame = raw.copy()
    for column in ("postcode", "paon", "saon", "street", "property_type", "old_new", "duration",
                   "ppd_category", "county", "district"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    records = len(frame)
    rows: list[dict[str, object]] = []

    def rule(name: str, mask: pd.Series, action: str) -> None:
        rows.append({"rule": name, "records": int(mask.sum()),
                     "share_pct": round(float(mask.sum()) / max(records, 1) * 100.0, 3),
                     "action": action})

    unreadable = frame["price"].isna() | frame["date"].isna()
    rule("price or date unreadable", unreadable, "excluded")
    frame = frame[~unreadable]
    if "record_status" in frame.columns:
        deleted = frame["record_status"].astype(str).str.strip() == "D"
        rule("record status D (deletion, in monthly update files)", deleted, "excluded")
        frame = frame[~deleted]
    category_b = frame["ppd_category"] == "B"
    rule("category B: repossessions, buy-to-let, transfers to companies", category_b,
         "excluded: not a market sale between individuals")
    frame = frame[~category_b]
    other = frame["property_type"] == "O"
    rule("property type O (other)", other, "excluded: not a dwelling")
    frame = frame[~other]
    low, high = price_bounds
    outside = (frame["price"] < low) | (frame["price"] > high)
    rule(f"price outside GBP {low:,.0f} to {high:,.0f}", outside,
         "excluded: nominal transfers and bulk sales recorded against one address")
    frame = frame[~outside]
    has_address = frame["postcode"] != ""
    frame["property_id"] = (frame["postcode"] + "|" + frame["paon"] + "|" + frame["saon"]
                            + "|" + frame["street"]).where(has_address)
    duplicate = has_address & frame.duplicated(["property_id", "date", "price"])
    rule("same property, date and price recorded twice", duplicate, "the repeat dropped")
    frame = frame[~duplicate]
    rule("no postcode", frame["postcode"] == "",
         "kept for the methods needing no identity; out of repeat sales")
    rule("new build", frame["old_new"] == "Y", "kept; a characteristic")
    rule("leasehold", frame["duration"] == "L", "kept; a characteristic")
    rows.append({"rule": "floor area, rooms, appraisal", "records": records,
                 "share_pct": 100.0,
                 "action": "not in the data: hedonic on type, tenure, new build and county; "
                           "mix-adjusted cells without size bands; no sale price appraisal "
                           "ratio"})

    out = pd.DataFrame({
        "property_id": frame["property_id"],
        "period": frame["date"].dt.to_period("M").dt.to_timestamp(),
        "price": frame["price"].astype(float),
        "stratum": frame["property_type"].map(PROPERTY_TYPES),
        "new_build": np.where(frame["old_new"] == "Y", "new", "existing"),
        "leasehold": np.where(frame["duration"] == "L", "leasehold", "freehold"),
        "county": frame["county"], "district": frame["district"],
        "date": frame["date"]}).reset_index(drop=True)
    return PricePaidResult(transactions=out, findings=pd.DataFrame(rows), records=records)
