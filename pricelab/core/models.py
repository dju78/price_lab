"""Domain schemas for the DataFrame boundaries this codebase actually
checks: a row of the price panel, a flagged observation, an imputed
observation. (Pydantic mirrors of the ORM rows and of shapes nothing ever
instantiated were removed in the Phase 10.5 wiring audit; the ORM classes
in core/ and data/ are the record of those shapes.)

These validate shape at a boundary; they do not replace the DataFrames the
engine computes over. Converting a multi-year monthly panel into one pydantic
object per row would undo the vectorised numpy work documented in
engine/index.py and engine/imputation.py, for no benefit a column contract
does not already give. `validate_frame` below checks every declared column's
presence and dtype in one vectorised pass, then spot-samples a handful of
actual rows through the matching model, which is what catches a problem a
dtype check alone would miss (e.g. a level outside a model's declared range)
without re-validating a whole panel row by row.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from datetime import datetime
from enum import StrEnum
from typing import Any

import pandas as pd
from pydantic import BaseModel, ValidationError


class Role(StrEnum):
    """The four roles core/security.py enforces, matching the five audience
    types in the platform spec minus the auditor, who is a viewer with read
    access to the audit log rather than a fifth distinct role."""

    ADMINISTRATOR = "administrator"
    COMPILER = "compiler"
    ANALYST = "analyst"
    VIEWER = "viewer"



class PriceQuote(BaseModel):
    """One row of the canonical price panel, after `data.upload.standardise`."""

    period: datetime
    """The observation period."""
    category: str | None = None
    """Classification group the item belongs to. Optional at the row level
    because completeness is a *graded* validation finding
    (`data.validation`: a minority of nulls is high, a majority critical),
    not a type error; this contract checks shape and type, and the column
    itself is required."""
    item_id: str | None = None
    """Stable identifier for the priced item; optional here for the same
    reason as `category`."""
    item_name: str | None = None
    """Human-readable item label."""
    price_reported: float | None = None
    """Price as originally recorded, in the collection's currency, before
    any sentinel recoding or repair."""
    weight: float | None = None
    """Expenditure weight, if one was supplied."""


class QualityFlag(BaseModel):
    """One observation as flagged by `engine.quality`."""

    period: datetime
    item_id: str
    category: str
    flag: str
    """none | missing_code | scale_error_x100 | scale_error_div100."""
    price: float | None = None
    """Price after sentinel recoding, before repair."""
    price_clean: float | None = None
    """Price after repair (rescaled, dropped, or unchanged)."""
    reference: float | None = None
    """The item's local rolling-median reference level at this period."""
    log10_deviation: float | None = None
    """log10(price / reference); the basis for the scale-error band."""


class ImputationRecord(BaseModel):
    """One observation filled by `engine.imputation`."""

    period: datetime
    item_id: str
    category: str
    imputation: str
    """"" (observed) | carry_forward | class_mean | seasonal_hold |
    targeted_mean | overall_mean."""
    price_imputed: float | None = None
    """The price actually used downstream, after imputation; None where
    the gap was left unfilled."""







# ---------------------------------------------------------------------
# DataFrame column contracts
# ---------------------------------------------------------------------
class ColumnSpec(BaseModel):
    """One column's presence and dtype requirement for `validate_frame`."""

    model_config = {"arbitrary_types_allowed": True}

    name: str
    dtype_check: Callable[[pd.Series], bool]
    required: bool = True


def _is_datetime(s: pd.Series) -> bool:
    return bool(pd.api.types.is_datetime64_any_dtype(s))


def _is_numeric(s: pd.Series) -> bool:
    return bool(pd.api.types.is_numeric_dtype(s))


def _is_text(s: pd.Series) -> bool:
    return bool(pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s))


PRICE_QUOTE_COLUMNS = [
    ColumnSpec(name="period", dtype_check=_is_datetime),
    ColumnSpec(name="category", dtype_check=_is_text),
    ColumnSpec(name="item_id", dtype_check=_is_text),
    ColumnSpec(name="item_name", dtype_check=_is_text, required=False),
    ColumnSpec(name="price_reported", dtype_check=_is_numeric),
    ColumnSpec(name="weight", dtype_check=_is_numeric, required=False),
]

IMPUTATION_COLUMNS = [
    ColumnSpec(name="period", dtype_check=_is_datetime),
    ColumnSpec(name="item_id", dtype_check=_is_text),
    ColumnSpec(name="category", dtype_check=_is_text),
    ColumnSpec(name="imputation", dtype_check=_is_text),
    ColumnSpec(name="price_imputed", dtype_check=_is_numeric),
]

QUALITY_FLAG_COLUMNS = [
    ColumnSpec(name="period", dtype_check=_is_datetime),
    ColumnSpec(name="item_id", dtype_check=_is_text),
    ColumnSpec(name="category", dtype_check=_is_text),
    ColumnSpec(name="flag", dtype_check=_is_text),
    ColumnSpec(name="price", dtype_check=_is_numeric, required=False),
]


def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value)) if not isinstance(value, (list, dict, tuple)) else False
    except (TypeError, ValueError):
        return False


def validate_frame(
    df: pd.DataFrame,
    columns: list[ColumnSpec],
    model: type[BaseModel],
    sample: int = 25,
) -> list[str]:
    """Column-contract check plus a bounded row-level spot sample.

    Returns a list of problems, empty if the frame conforms. Checks every
    declared column's presence and dtype first, since that is cheap and
    vectorised; only if that passes does it parse up to `sample` actual rows
    through `model`, which catches a problem a dtype check cannot express
    (e.g. a negative `level` field, or a row of the wrong shape once
    `to_dict` runs). Deliberately bounded rather than run over the whole
    frame: this is a boundary check, not a substitute for the vectorised
    validation `data.upload.validate` already does over the full panel.
    """
    problems: list[str] = []
    for col in columns:
        if col.name not in df.columns:
            if col.required:
                problems.append(f"required column '{col.name}' is missing")
            continue
        if not col.dtype_check(df[col.name]):
            problems.append(f"column '{col.name}' has an unexpected dtype: {df[col.name].dtype}")

    if problems:
        return problems  # sampling rows against missing/mistyped columns is not meaningful

    # pandas types a DataFrame's columns as generically Hashable, not str;
    # every DataFrame this validates is already keyed by our own str column
    # names, so the narrower annotation reflects reality without a runtime
    # check that would only ever prove pandas' own construction correct.
    records: list[dict[Hashable, Any]] = df.head(sample).to_dict(orient="records")
    for row in records:
        # pandas writes a missing string as NaN (a float); to the row model
        # that is "absent", which is what None says.
        cleaned = {k: (None if _is_missing(v) else v) for k, v in row.items()}
        try:
            model.model_validate(cleaned)
        except ValidationError as exc:
            problems.append(f"a sampled row failed {model.__name__} validation: {exc}")
            break
    return problems
