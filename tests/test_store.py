"""Tests for the immutable raw layer, the cleaned layer, and transformation
log replay -- including the acceptance test that matters most for this
phase: replay reproduces the cleaned layer exactly."""

from pathlib import Path

import pandas as pd
import pytest

from pricelab.core.config import IndexConfig, RunConfig
from pricelab.data import store


def _collection() -> pd.DataFrame:
    return pd.DataFrame({
        "period": pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01"] * 2),
        "category": ["Bread"] * 3 + ["Milk"] * 3,
        "item_id": ["1", "1", "1", "2", "2", "2"],
        "item_name": ["White"] * 3 + ["Semi"] * 3,
        "price_reported": [1.0, 1.1, 1.2, 0.9, 0.95, 1.0],
    })


def test_write_raw_layer_writes_a_parquet_file_at_its_content_hash(tmp_path):
    df = _collection()
    path = store.write_raw_layer(df, tmp_path)
    assert path.exists()
    assert path.name == f"{store.content_hash_of(df)}.parquet"
    reloaded = pd.read_parquet(path)
    assert store.content_hash_of(reloaded) == store.content_hash_of(df)


def test_write_raw_layer_is_idempotent_for_identical_content(tmp_path):
    df = _collection()
    path1 = store.write_raw_layer(df, tmp_path)
    mtime1 = path1.stat().st_mtime
    path2 = store.write_raw_layer(df.copy(), tmp_path)
    assert path1 == path2
    assert path1.stat().st_mtime == mtime1  # not rewritten


def test_write_raw_layer_refuses_to_overwrite_a_corrupted_file_at_the_same_hash(tmp_path):
    df = _collection()
    path = store.write_raw_layer(df, tmp_path)
    path.write_bytes(b"not a parquet file at all")
    with pytest.raises(Exception):  # noqa: B017 -- pyarrow raises its own error reading garbage
        store.write_raw_layer(df, tmp_path)


def test_write_cleaned_layer_and_replay_reproduce_the_same_dataframe(tmp_path):
    df = _collection()
    cfg = RunConfig(index=IndexConfig(chained=True))

    store.write_raw_layer(df, tmp_path)
    cleaned_path, log = store.write_cleaned_layer(df, cfg, tmp_path)
    original_cleaned = pd.read_parquet(cleaned_path)

    replayed = store.replay(df, log)

    pd.testing.assert_frame_equal(original_cleaned, replayed)


def test_replay_from_disk_round_trip_matches_exactly(tmp_path):
    """The full loop: write raw to disk, reload it, replay from the
    reloaded copy, and compare to what was written to the cleaned layer --
    proving the reproduction survives an actual disk round trip, not just
    reuse of the in-memory DataFrame."""
    df = _collection()
    cfg = RunConfig()

    raw_path = store.write_raw_layer(df, tmp_path)
    cleaned_path, log = store.write_cleaned_layer(df, cfg, tmp_path)

    raw_reloaded = pd.read_parquet(raw_path)
    replayed = store.replay(raw_reloaded, log)
    original_cleaned = pd.read_parquet(cleaned_path)

    pd.testing.assert_frame_equal(original_cleaned, replayed)


def test_transformation_log_survives_json_round_trip_and_still_replays_identically(tmp_path):
    df = _collection()
    cfg = RunConfig(index=IndexConfig(formula="dutot", chained=False))
    store.write_raw_layer(df, tmp_path)
    cleaned_path, log = store.write_cleaned_layer(df, cfg, tmp_path)

    log2 = store.TransformationLog.from_json(log.to_json())
    assert log2.raw_content_hash == log.raw_content_hash

    replayed_from_log2 = store.replay(df, log2)
    original_cleaned = pd.read_parquet(cleaned_path)
    pd.testing.assert_frame_equal(original_cleaned, replayed_from_log2)


def test_replay_refuses_to_run_against_the_wrong_raw_data(tmp_path):
    df = _collection()
    cfg = RunConfig()
    store.write_raw_layer(df, tmp_path)
    _cleaned_path, log = store.write_cleaned_layer(df, cfg, tmp_path)

    wrong_df = df.copy()
    wrong_df.loc[0, "price_reported"] = 999.0

    with pytest.raises(store.ImmutableLayerError, match="does not match"):
        store.replay(wrong_df, log)


def test_the_real_fixture_replays_byte_identical(tmp_path):
    """The acceptance test in the phase's own words, run against the real
    multi-year, multi-category fixture rather than a small synthetic
    example."""
    from pricelab import auto_configure, infer_schema, standardise

    fixture = Path(__file__).resolve().parents[1] / "supermarket_price_collection.xlsx"
    if not fixture.exists():
        pytest.skip("fixture workbook not present")

    raw = pd.read_excel(fixture, sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))
    cfg, _ = auto_configure(df, "store fixture test")

    store.write_raw_layer(df, tmp_path)
    cleaned_path, log = store.write_cleaned_layer(df, cfg, tmp_path)
    original_bytes = cleaned_path.read_bytes()

    replayed = store.replay(df, log)
    original_cleaned = pd.read_parquet(cleaned_path)
    pd.testing.assert_frame_equal(original_cleaned, replayed)

    # Genuinely byte-identical on disk, not just DataFrame-equal: pyarrow's
    # parquet writer embeds no timestamp or other non-deterministic
    # metadata, verified separately, so re-serialising the replay must
    # produce the exact bytes already on disk.
    import io
    buf = io.BytesIO()
    replayed.to_parquet(buf, index=False)
    assert buf.getvalue() == original_bytes
