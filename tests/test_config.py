"""Tests for the Phase 1 pydantic migration of RunConfig.

The one property that matters here is continuity: every config JSON this
tool wrote before Phase 1 (dataclasses, no schema_version key) must still
load and mean exactly what it meant before, and a config saved before the
schema_version 2 price/weight/index reference period split must still
produce exactly the index series it always did.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from pricelab import infer_schema, run_pipeline, standardise
from pricelab.core.config import CURRENT_SCHEMA_VERSION, IndexConfig, RunConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "supermarket_price_collection.xlsx"

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


# ---------------------------------------------------------------------
# schema_version 1 -> 2: the price/weight/index reference period split
# ---------------------------------------------------------------------
def test_legacy_config_upconverts_all_three_reference_periods_from_base_period():
    legacy = json.loads(LEGACY_DATACLASS_JSON)
    legacy["index"]["base_period"] = "2020-02-01"
    cfg = RunConfig.from_dict(legacy)

    assert cfg.legacy_upconverted is True
    assert cfg.schema_version == "2"
    assert cfg.index.base_period == "2020-02-01"
    assert cfg.index.price_reference_period == "2020-02-01"
    assert cfg.index.weight_reference_period == "2020-02-01"
    assert cfg.index.index_reference_period == "2020-02-01"


def test_legacy_config_with_no_base_period_upconverts_to_none_not_a_guess():
    """base_period was never set, so there is nothing to upconvert into the
    new fields: they stay None (meaning "use the engine's own default"),
    rather than the migration inventing a period the old config never
    specified."""
    cfg = RunConfig.from_dict(json.loads(LEGACY_DATACLASS_JSON))
    assert cfg.legacy_upconverted is True
    assert cfg.index.price_reference_period is None
    assert cfg.index.weight_reference_period is None
    assert cfg.index.index_reference_period is None


def test_a_config_already_on_the_current_schema_is_not_flagged_upconverted():
    cfg = RunConfig.from_dict(json.loads(RunConfig(label="current").to_json()))
    assert cfg.legacy_upconverted is False


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture workbook not present")
def test_legacy_config_upconversion_produces_an_identical_index_series_on_the_fixture():
    """The real proof: a legacy config with an explicit base_period, loaded
    through from_dict's upconversion, must build exactly the same index
    series on the actual multi-year, multi-category fixture as a
    schema_version 2 config that sets price_reference_period and
    index_reference_period directly instead of the deprecated alias.
    Upconversion must be a change of representation, not of meaning.
    """
    raw = pd.read_excel(FIXTURE, sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))

    legacy = json.loads(LEGACY_DATACLASS_JSON)
    legacy["index"]["base_period"] = "2018-06-01"
    legacy["index"]["chained"] = False
    cfg_legacy = RunConfig.from_dict(legacy)
    assert cfg_legacy.legacy_upconverted is True

    cfg_direct = RunConfig(
        schema=cfg_legacy.schema_,
        quality=cfg_legacy.quality,
        imputation=cfg_legacy.imputation,
        index=IndexConfig(
            formula=cfg_legacy.index.formula, chained=False,
            price_reference_period="2018-06-01", index_reference_period="2018-06-01"),
        label="direct, schema_version 2 style")

    result_legacy = run_pipeline(df, cfg_legacy)
    result_direct = run_pipeline(df, cfg_direct)
    pd.testing.assert_frame_equal(result_legacy["indices"], result_direct["indices"])
