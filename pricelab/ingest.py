"""Ingestion and structural validation.

Design choice: validation returns a report, it does not raise on the first
problem. A collection with three faults should surface all three in one pass,
because the person fixing them is usually not the person running the code.
"""

from dataclasses import dataclass, field
from typing import List
import pandas as pd

from .config import Schema


@dataclass
class ValidationReport:
    passed: bool = True
    errors: List[str] = field(default_factory=list)      # block the run
    warnings: List[str] = field(default_factory=list)    # worth a human look
    facts: dict = field(default_factory=dict)            # descriptive profile

    def error(self, msg: str):
        self.errors.append(msg)
        self.passed = False

    def warn(self, msg: str):
        self.warnings.append(msg)

    def __str__(self) -> str:
        lines = [f"Validation: {'PASSED' if self.passed else 'FAILED'}"]
        for k, v in self.facts.items():
            lines.append(f"  {k}: {v}")
        for e in self.errors:
            lines.append(f"  ERROR   {e}")
        for w in self.warnings:
            lines.append(f"  WARNING {w}")
        return "\n".join(lines)


def read_price_data(path: str, sheet_name=0, schema: Schema = None) -> pd.DataFrame:
    """Read CSV or Excel into the canonical long format."""
    schema = schema or Schema()
    if str(path).lower().endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(path, sheet_name=sheet_name)
    else:
        df = pd.read_csv(path)
    return standardise(df, schema)


def standardise(df: pd.DataFrame, schema: Schema = None) -> pd.DataFrame:
    """Rename to canonical column names and coerce types."""
    schema = schema or Schema()
    mapping = {
        schema.date: "period",
        schema.item_id: "item_id",
        schema.item_name: "item_name",
        schema.category: "category",
        schema.price: "price_reported",
    }
    if schema.weight:
        mapping[schema.weight] = "weight"

    present = {k: v for k, v in mapping.items() if k in df.columns}
    out = df.rename(columns=present).copy()

    if "period" in out:
        out["period"] = pd.to_datetime(out["period"])
    if "price_reported" in out:
        out["price_reported"] = pd.to_numeric(out["price_reported"], errors="coerce")
    if "item_id" in out:
        out["item_id"] = out["item_id"].astype(str)

    keep = [c for c in ["period", "category", "item_id", "item_name",
                        "price_reported", "weight"] if c in out.columns]
    return out[keep].sort_values(["item_id", "period"]).reset_index(drop=True)


def validate(df: pd.DataFrame) -> ValidationReport:
    """Structural checks only. Value-level quality is the quality module's job.

    The key uniqueness check matters more than it looks: a matched-model index
    is undefined if one item has two prices in one period, so this check is
    what licenses the index method downstream.
    """
    r = ValidationReport()

    required = ["period", "category", "item_id", "price_reported"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        r.error(f"required columns absent after mapping: {missing}")
        return r

    dup = df.duplicated(["period", "item_id"]).sum()
    if dup:
        r.error(f"{dup} duplicate period/item_id pairs; matched index is undefined")

    if df["price_reported"].isna().all():
        r.error("no numeric prices parsed; check the price column mapping")

    if (df["price_reported"] < 0).sum():
        r.error(f"{int((df['price_reported'] < 0).sum())} negative prices")

    # An item mapping to more than one name usually means a reused identifier,
    # which silently corrupts item matching across a relaunch.
    if "item_name" in df.columns:
        multi = df.groupby("item_id")["item_name"].nunique()
        if (multi > 1).any():
            r.warn(f"{int((multi > 1).sum())} item_id values carry more than one name")

    # Gaps in the period grid within an item affect chaining.
    # Spacing is checked in calendar months, not days: month lengths differ,
    # so a day-based check flags every monthly collection as irregular.
    periods = df["period"].drop_duplicates().sort_values()
    if len(periods) > 2:
        steps = periods.dt.to_period("M").astype("int64").diff().dropna()
        if steps.nunique() > 1:
            r.warn("period spacing is irregular; chained comparisons assume a regular grid")

    counts = df.groupby(["category", "period"]).size()
    thin = counts[counts < 2]
    if len(thin):
        r.warn(f"{len(thin)} category-periods have fewer than 2 priced items")

    r.facts = {
        "rows": len(df),
        "period range": f"{df.period.min().date()} to {df.period.max().date()}",
        "periods": df.period.nunique(),
        "categories": df.category.nunique(),
        "items": df.item_id.nunique(),
        "null prices": int(df.price_reported.isna().sum()),
    }
    return r
