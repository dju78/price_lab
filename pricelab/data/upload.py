"""Ingestion and structural validation.

Design choice: validation returns a report, it does not raise on the first
problem. A collection with three faults should surface all three in one pass,
because the person fixing them is usually not the person running the code.
"""

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..core.config import Schema


@dataclass
class ValidationReport:
    passed: bool = True
    errors: list[str] = field(default_factory=list)      # block the run
    warnings: list[str] = field(default_factory=list)    # worth a human look
    facts: dict[str, Any] = field(default_factory=dict)  # descriptive profile

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False

    def warn(self, msg: str) -> None:
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


def read_price_data(path: str, sheet_name: int | str = 0, schema: Schema | None = None) -> pd.DataFrame:
    """Read a file (CSV, Excel, Parquet, JSON) into the canonical long
    format, through the same `data.loaders.read_upload` the Ingest page
    uses -- encoding and delimiter detection, header-row inference -- so
    a notebook and the application read a file the same way."""
    from pathlib import Path

    from .loaders import read_upload

    file_path = Path(path)
    loaded = read_upload(file_path.read_bytes(), file_path.name, sheet_name=sheet_name)
    if schema is None:
        # Infer the mapping the way the Ingest page proposes it, so a file
        # with quantity or expenditure columns keeps them.
        from ..engine.auto import infer_schema

        schema = infer_schema(loaded.df)
    return standardise(loaded.df, schema)


def standardise(df: pd.DataFrame, schema: Schema | None = None) -> pd.DataFrame:
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
    if schema.quantity:
        mapping[schema.quantity] = "quantity"
    if schema.expenditure:
        mapping[schema.expenditure] = "expenditure"
    if schema.unit:
        mapping[schema.unit] = "unit"

    present = {k: v for k, v in mapping.items() if k in df.columns}
    out = df.rename(columns=present).copy()

    if "period" in out:
        out["period"] = pd.to_datetime(out["period"])
    if "price_reported" in out:
        out["price_reported"] = pd.to_numeric(out["price_reported"], errors="coerce")
    for col in ("quantity", "expenditure"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "item_id" in out:
        out["item_id"] = out["item_id"].astype(str)
    # A category or item name that happens to look numeric ("01", a
    # classification code) is still a label. Left as an integer it fails
    # the column contract and, worse, "01" and "1" would silently merge.
    for col in ("category", "item_name", "unit"):
        if col in out and not pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].where(out[col].isna(), out[col].astype(str))

    keep = [c for c in ["period", "category", "item_id", "item_name",
                        "price_reported", "weight", "quantity", "expenditure", "unit"]
            if c in out.columns]
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

    # The pydantic column contract (core.models): every declared column's
    # presence and dtype, then a bounded row sample through PriceQuote.
    from ..core.models import PRICE_QUOTE_COLUMNS, PriceQuote, validate_frame

    contract = validate_frame(df, PRICE_QUOTE_COLUMNS, PriceQuote)
    for problem in contract:
        r.error(f"column contract: {problem}")
    if any("unexpected dtype" in problem for problem in contract):
        return r          # the value checks below assume the declared types

    dup = df.duplicated(["period", "item_id"]).sum()
    if dup:
        r.error(f"{dup} duplicate period/item_id pairs; matched index is undefined")

    if df["price_reported"].isna().all():
        r.error("no numeric prices parsed; check the price column mapping")

    if (df["price_reported"] < 0).sum():
        r.error(f"{int((df['price_reported'] < 0).sum())} negative prices")
    for col in ("quantity", "expenditure"):
        if col in df.columns and (df[col] < 0).sum():
            r.error(f"{int((df[col] < 0).sum())} negative {col} values")

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
