"""Domain schemas for the objects this codebase actually has: a row of the
price panel, a flagged or imputed observation, an item's lifespan, a
classification node, an index level, a registered run, an audit event, a
user.

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
from pydantic import BaseModel, Field, ValidationError


class Role(StrEnum):
    """The four roles core/security.py enforces, matching the five audience
    types in the platform spec minus the auditor, who is a viewer with read
    access to the audit log rather than a fifth distinct role."""

    ADMINISTRATOR = "administrator"
    COMPILER = "compiler"
    ANALYST = "analyst"
    VIEWER = "viewer"


class User(BaseModel):
    """A login identity. `password_hash` is never the plain password; see
    core/security.py for hashing."""

    id: int | None = None
    username: str
    password_hash: str
    role: Role
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PriceQuote(BaseModel):
    """One row of the canonical price panel, after `data.upload.standardise`."""

    period: datetime
    """The observation period."""
    category: str
    """Classification group the item belongs to."""
    item_id: str
    """Stable identifier for the priced item."""
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
    method: str
    """none | carry_forward | class_mean | seasonal_hold."""
    price_imputed: float
    """The price actually used downstream, after imputation."""


class ItemSeries(BaseModel):
    """One item's lifespan summary, as `engine.diagnostics.churn` produces."""

    category: str
    item_id: str
    item_name: str | None = None
    first: datetime
    last: datetime
    periods: int = Field(ge=1)
    entry_price: float | None = None
    exit_price: float | None = None


class ClassificationNode(BaseModel):
    """One node of a classification tree (COICOP, CPA, HS, or a custom tree)."""

    code: str
    label: str
    level: int = Field(ge=0, description="0 for a root division, increasing with depth")
    parent_code: str | None = None
    scheme: str = "COICOP2018"


class IndexSeries(BaseModel):
    """One period's level for one series, as `engine.index.build_index` and
    `build_all` produce."""

    period: datetime
    series: str
    """The category name, or "All items" for the aggregate."""
    index: float | None = None
    matched_items: float | None = None
    """NaN for the base period, which has no prior period to match against."""
    insufficient_match: bool = False

    price_reference_period: datetime | None = None
    """The period whose prices are the denominator of every relative in this
    series. Carried alongside the series for display and audit; does not
    change how any row above was computed."""
    weight_reference_period: datetime | None = None
    """The period the weights or quantities were drawn from. Not read by any
    formula implemented yet (Lowe and Young are Phase 3); carried so a
    future reader knows what a run intended even before that lands."""
    index_reference_period: datetime | None = None
    """The period this series is rebased to read `base_value` at."""


class IndexRun(BaseModel):
    """Registry entry for one calculation run; see core/registry.py."""

    run_id: str
    content_hash: str
    """Hash of the input data plus the full parameter set."""
    config_json: str
    code_version: str
    environment_fingerprint: str
    label: str
    created_at: datetime
    approved: bool = False
    vintage: int = Field(default=1, ge=1)
    correction_reason: str | None = None

    price_reference_period: str | None = None
    weight_reference_period: str | None = None
    index_reference_period: str | None = None
    """The three reference periods `IndexConfig` recorded for this run, as
    ISO date strings exactly as configured (possibly None, if the run never
    set them and relied on the engine's own default). See
    `core.config.IndexConfig` for what each means."""


class AuditEvent(BaseModel):
    """One entry of the append-only audit log; see core/audit.py."""

    id: int | None = None
    prev_hash: str
    hash: str
    actor: str
    action: str
    target: str
    params_json: str
    created_at: datetime


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

QUALITY_FLAG_COLUMNS = [
    ColumnSpec(name="period", dtype_check=_is_datetime),
    ColumnSpec(name="item_id", dtype_check=_is_text),
    ColumnSpec(name="category", dtype_check=_is_text),
    ColumnSpec(name="flag", dtype_check=_is_text),
    ColumnSpec(name="price", dtype_check=_is_numeric, required=False),
]


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
        try:
            model.model_validate(row)
        except ValidationError as exc:
            problems.append(f"a sampled row failed {model.__name__} validation: {exc}")
            break
    return problems
