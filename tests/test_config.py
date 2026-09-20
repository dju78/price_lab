"""Tests for the Phase 1 pydantic migration of RunConfig.

The one property that matters here is continuity: every config JSON this
tool wrote before Phase 1 (dataclasses, no schema_version key) must still
load and mean exactly what it meant before.
"""

import json

import pytest

from pricelab.core.config import CURRENT_SCHEMA_VERSION, RunConfig

# Exactly the JSON `json.dumps(asdict(RunConfig(...)), indent=2, default=str)`
# produced before Phase 1: no "schema_version" key, and "schema" is a bare
# field name rather than an alias.
LEGACY_DATACLASS_JSON = """
{
  "schema": {
    "date": "Date",
    "item_id": "Item_ID",
    "item_name": "Item_Name",
    "category": "Category",
    "price": "Reported_Price",
    "weight": null
  },
  "quality": {
    "missing_codes": [0],
    "reference_window": 13,
    "min_reference_periods": 3,
    "scale_log10_low": 1.5,
    "scale_log10_high": 2.5,
    "repair_scale_errors": true,
    "residual_tolerance": 0.5
  },
  "imputation": {
    "default_method": "none",
    "by_category": {"Bread": "class_mean"}
  },
  "index": {
    "formula": "jevons",
    "chained": true,
    "base_period": null,
    "base_value": 100.0,
    "min_matched_items": 2
  },
  "label": "legacy run"
}
"""


def test_legacy_json_round_trips_through_pydantic():
    cfg = RunConfig.from_dict(json.loads(LEGACY_DATACLASS_JSON))

    assert cfg.label == "legacy run"
    assert cfg.schema_.date == "Date"
    assert cfg.schema_.weight is None
    assert cfg.quality.missing_codes == (0.0,)
    assert cfg.quality.reference_window == 13
    assert cfg.imputation.by_category == {"Bread": "class_mean"}
    assert cfg.index.formula == "jevons"
    assert cfg.index.chained is True
    # A file with no schema_version predates the field: treated as current.
    assert cfg.schema_version == CURRENT_SCHEMA_VERSION


def test_legacy_json_missing_codes_still_matches_zero_prices():
    """The dataclass version stored int 0; pydantic types this tuple as
    floats. That is a type change, not a behaviour change: a price of 0
    must still compare equal to the configured sentinel either way."""
    cfg = RunConfig.from_dict(json.loads(LEGACY_DATACLASS_JSON))
    assert 0 in cfg.quality.missing_codes
    assert 0.0 in cfg.quality.missing_codes


def test_round_trip_via_to_json_and_from_dict():
    original = RunConfig(label="round trip me")
    assert original.quality.reference_window == 13  # sanity: attribute access works
    restored = RunConfig.from_dict(json.loads(original.to_json()))
    assert restored == original


def test_construct_with_schema_keyword_still_works():
    """app.py constructs RunConfig(schema=..., ...); the alias must accept
    that keyword even though the Python attribute is named `schema_`."""
    from pricelab.core.config import Schema

    cfg = RunConfig(schema=Schema(date="Month"), label="kw test")
    assert cfg.schema_.date == "Month"


def test_unknown_schema_version_raises_rather_than_silently_misreading():
    with pytest.raises(ValueError, match="schema_version"):
        RunConfig.from_dict({"schema_version": "99", "label": "from the future"})
