"""The characteristics file the hedonic module uses passes through the
same four provenance properties as a price upload: the immutable raw
Parquet layer, a transformation log that replays, the validation engine,
and a vintage receipt -- and the vintage travels into any adjustment
read from a fit on it, so the ledger, the configuration and the registry
hash all name the characteristics the valuation depended on.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from pricelab.data import store, validation
from pricelab.data.validation import Severity
from pricelab.engine import hedonic


def _chars() -> pd.DataFrame:
    return pd.DataFrame({
        " item_id": ["A", "B", "C", "D"],
        "capacity": ["7", "8", "9", "6"],          # numbers typed as text
        "brand": ["x", "y", "x", "y"],
        "warranty": [1, 2, None, 2],
    })


def test_the_raw_layer_is_immutable_and_the_vintage_receipt_names_the_upload(tmp_path):
    chars = _chars()
    v = store.record_upload(chars, kind="characteristics", file_name="chars.csv",
                            file_bytes=b"raw,bytes", actor="compiler1", directory=tmp_path)
    assert v.kind == "characteristics" and v.source == "upload:chars.csv"
    assert (tmp_path / "raw" / f"{v.content_hash}.parquet").exists()
    assert pd.read_parquet(v.raw_path).equals(chars)
    receipts = store.load_vintages(tmp_path, v.content_hash)
    assert len(receipts) == 1 and receipts[0].received_by == "compiler1"
    assert receipts[0].file_sha256 == __import__("hashlib").sha256(b"raw,bytes").hexdigest()
    # a second upload of the same content adds a receipt, never a second raw file
    store.record_upload(chars, kind="characteristics", file_name="again.csv", file_bytes=b"x",
                        actor="compiler2", directory=tmp_path)
    assert len(store.load_vintages(tmp_path, v.content_hash)) == 2
    assert len(list((tmp_path / "raw").glob("*.parquet"))) == 1


def test_the_transformation_log_replays_the_cleaned_layer_exactly(tmp_path):
    chars = _chars()
    raw_path, cleaned_path, log = store.write_characteristics_layers(chars, tmp_path)
    cleaned = pd.read_parquet(cleaned_path)
    assert list(cleaned.columns) == ["item_id", "capacity", "brand", "warranty"]
    assert pd.api.types.is_numeric_dtype(cleaned["capacity"])
    assert "capacity: parsed as numeric" in log.steps
    assert "stripped whitespace from column names" in log.steps
    replayed = store.replay_characteristics(pd.read_parquet(raw_path), log)
    pd.testing.assert_frame_equal(replayed, cleaned)
    assert store.content_hash_of(replayed) == log.cleaned_content_hash
    reloaded = store.CharacteristicsTransformationLog.from_json(
        (tmp_path / "cleaned" / f"characteristics-{raw_path.stem}.log.json").read_text(encoding="utf-8"))
    assert reloaded.steps == log.steps
    with pytest.raises(store.ImmutableLayerError, match="does not match"):
        store.replay_characteristics(chars.head(2), log)


def test_validation_finds_duplicates_missing_values_typing_slips_and_orphans():
    chars = pd.DataFrame({
        "item_id": ["A", "A", "B", "C"],
        "capacity": [7, 8, "nine?", 6],
        "empty": [None, None, None, None],
        "brand": ["x", "y", None, "x"],
    })
    a = validation.assess_characteristics(chars, price_item_ids=["A", "B", "Z"])
    dims = {(f.dimension, f.severity) for f in a.findings}
    assert ("uniqueness", Severity.CRITICAL) in dims
    assert a.blocking
    assert ("completeness", Severity.HIGH) in dims           # `empty`
    assert ("completeness", Severity.MEDIUM) in dims         # `brand` missing for one
    assert ("validity", Severity.HIGH) in dims               # `capacity` mostly numeric
    assert ("conformity", Severity.MEDIUM) in dims           # C has no prices
    assert ("conformity", Severity.LOW) in dims              # Z has no characteristics
    clean = validation.assess_characteristics(_chars().rename(columns={" item_id": "item_id"}),
                                              price_item_ids=["A", "B", "C", "D"])
    assert not clean.blocking
    assert validation.assess_characteristics(pd.DataFrame({"x": [1]})).blocking


def test_the_characteristics_vintage_travels_into_the_ledger_and_the_registry_hash(tmp_path):
    panel = hedonic.synthetic_hedonic_panel(n_items=40, n_periods=3, seed=2)
    spec = hedonic.HedonicSpec(characteristics=("z1", "z2"), categorical=("brand",))
    fit = hedonic.fit_hedonic(panel, spec, cv_folds=0)
    fit.data_vintage = {"content_hash": "chars-hash-123", "file_name": "chars.csv",
                        "received_by": "compiler1"}
    adj = hedonic.hedonic_adjustment(fit, "old", "new", "x", pd.Timestamp("2020-03-01"), 40.0, 50.0,
                                     {"z1": 2.0, "z2": 3.0, "brand": "A"},
                                     {"z1": 3.0, "z2": 3.0, "brand": "B"})
    assert adj.parameters["characteristics_vintage"]["content_hash"] == "chars-hash-123"
    entry = adj.to_entry("compiler1")
    assert entry.parameters["characteristics_vintage"]["file_name"] == "chars.csv"

    from pricelab.core.config import QualityAdjustmentConfig, RunConfig
    cfg = RunConfig(quality_adjustment=QualityAdjustmentConfig(entries=[entry]))
    assert "chars-hash-123" in cfg.to_json()
    other = adj.to_entry("compiler1").model_copy(update={"parameters": {
        **entry.parameters, "characteristics_vintage": {"content_hash": "different"}}})
    cfg2 = RunConfig(quality_adjustment=QualityAdjustmentConfig(entries=[other]))
    assert json.loads(cfg.to_json()) != json.loads(cfg2.to_json()), \
        "a different characteristics vintage is a different configuration, hence a different run"
