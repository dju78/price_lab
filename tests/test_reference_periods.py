"""Engine-level tests for the price/weight/index reference period split.

Companions to tests/test_config.py (RunConfig-level upconversion) and
tests/test_registry.py (registry-level recording): these exercise
engine.index.build_index directly, where price_reference_period and
index_reference_period actually change what gets computed.
"""

import pandas as pd
import pytest
from pydantic import ValidationError

from pricelab.core.config import IndexConfig
from pricelab.engine.index import build_index


def _two_item_panel(n_periods: int = 4) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    periods = pd.date_range("2020-01-01", periods=n_periods, freq="MS")
    df = pd.DataFrame({
        "period": list(periods) * 2,
        "item_id": ["1"] * n_periods + ["2"] * n_periods,
        "price_imputed": [10.0, 11.0, 12.0, 13.0, 20.0, 19.0, 22.0, 26.0],
    })
    return df, periods


def test_rebasing_a_fixed_base_index_changes_the_level_not_the_movements():
    """A fixed-base index computed against price reference period A, then
    published (rebased) at index reference period B, must read exactly
    base_value at B, and its period-on-period movements must be identical
    to the same series published at A. Rebasing is a rescaling of the
    finished series, not a recomputation."""
    df, periods = _two_item_panel()
    a, b = periods[1], periods[3]

    published_at_a = build_index(df, IndexConfig(
        chained=False, price_reference_period=str(a.date()),
        index_reference_period=str(a.date())))
    published_at_b = build_index(df, IndexConfig(
        chained=False, price_reference_period=str(a.date()),
        index_reference_period=str(b.date())))

    # Value 100 (base_value) at whichever period was chosen to publish on.
    assert published_at_a.loc[a, "index"] == pytest.approx(100.0)
    assert published_at_b.loc[b, "index"] == pytest.approx(100.0)

    # The level itself differs between the two publications...
    assert not published_at_a["index"].equals(published_at_b["index"])

    # ...but every period-on-period movement is identical: rebasing
    # multiplies the whole series by one constant, which cancels out of
    # any ratio between two periods of the same series.
    movement_a = published_at_a["index"].pct_change()
    movement_b = published_at_b["index"].pct_change()
    pd.testing.assert_series_equal(movement_a, movement_b, check_names=False)


def test_rebasing_a_chained_index_changes_the_level_not_the_movements():
    """The chained counterpart of the fixed-base test above: this already
    worked before this patch (rebasing only ever ran on the chained
    branch), kept here so both methods are covered by the same property."""
    df, periods = _two_item_panel()
    a, b = periods[0], periods[2]

    published_at_a = build_index(df, IndexConfig(chained=True, index_reference_period=str(a.date())))
    published_at_b = build_index(df, IndexConfig(chained=True, index_reference_period=str(b.date())))

    assert published_at_a.loc[a, "index"] == pytest.approx(100.0)
    assert published_at_b.loc[b, "index"] == pytest.approx(100.0)
    assert not published_at_a["index"].equals(published_at_b["index"])

    movement_a = published_at_a["index"].pct_change()
    movement_b = published_at_b["index"].pct_change()
    pd.testing.assert_series_equal(movement_a, movement_b, check_names=False)


def test_fixed_base_index_reference_period_need_not_equal_price_reference_period():
    """The property the base_period conflation used to make impossible: a
    fixed-base index compiled against one period can be published reading
    100 at a completely different one."""
    df, periods = _two_item_panel()
    price_ref, index_ref = periods[0], periods[2]

    out = build_index(df, IndexConfig(
        chained=False, price_reference_period=str(price_ref.date()),
        index_reference_period=str(index_ref.date())))

    assert out.loc[index_ref, "index"] == pytest.approx(100.0)
    assert out.loc[price_ref, "index"] != pytest.approx(100.0)


# ---------------------------------------------------------------------
# Phase 3 entry condition: an unset index reference period defaults to the
# price reference period, not to the first observation -- and the level at
# a period *before* the price reference is computed rather than hardcoded.
# ---------------------------------------------------------------------
def test_unset_index_reference_defaults_to_the_price_reference_not_the_first_period():
    """With only `price_reference_period` set, the published series must
    read base_value at that period. Defaulting the rebasing step to the
    first period instead would renormalise the series onto a period the
    caller never nominated, quietly undoing the price reference they did."""
    df, periods = _two_item_panel()
    price_ref = periods[2]

    out = build_index(df, IndexConfig(
        chained=False, price_reference_period=str(price_ref.date())))

    assert out.loc[price_ref, "index"] == pytest.approx(100.0)
    assert out.loc[periods[0], "index"] != pytest.approx(100.0)


def test_the_same_default_applies_to_a_chained_series():
    """The rebasing step is method-agnostic, so the new default must be
    too: a chained series with only a price reference set is published at
    that period as well."""
    df, periods = _two_item_panel()
    price_ref = periods[2]

    out = build_index(df, IndexConfig(
        chained=True, price_reference_period=str(price_ref.date())))

    assert out.loc[price_ref, "index"] == pytest.approx(100.0)


def test_an_explicit_index_reference_still_wins_over_the_price_reference():
    df, periods = _two_item_panel()
    price_ref, index_ref = periods[1], periods[3]

    out = build_index(df, IndexConfig(
        chained=False, price_reference_period=str(price_ref.date()),
        index_reference_period=str(index_ref.date())))

    assert out.loc[index_ref, "index"] == pytest.approx(100.0)
    assert out.loc[price_ref, "index"] != pytest.approx(100.0)


def test_a_period_before_the_price_reference_is_computed_not_hardcoded():
    """The base_value quirk, closed. A fixed-base index's first row used to
    be assigned base_value outright, sharing the chained branch's genuine
    "no predecessor" case. With a price reference later than the series'
    start, that published a fabricated 100 for a period whose real level
    relative to the price reference is perfectly computable -- and which
    the new default rebasing no longer papers over.

    Item 1 runs 10, 11, 12, 13 and item 2 runs 20, 19, 22, 26. Against a
    price reference of period 3, period 1's Jevons relative is
    sqrt((10/12) * (20/22)), so the level is that times 100.
    """
    import numpy as np

    df, periods = _two_item_panel()
    price_ref = periods[2]

    out = build_index(df, IndexConfig(
        chained=False, price_reference_period=str(price_ref.date())))

    expected = 100.0 * float(np.sqrt((10.0 / 12.0) * (20.0 / 22.0)))
    assert out.loc[periods[0], "index"] == pytest.approx(expected)
    assert out.loc[periods[0], "index"] < 100.0  # prices rose, so the past is below 100


def test_defaulting_is_unchanged_when_no_reference_period_is_set_at_all():
    """The guarantee the whole phase rests on: a run that sets none of the
    three reference periods -- every existing run, including the committed
    fixture -- is completely unaffected by the new default."""
    df, periods = _two_item_panel()

    for chained in (True, False):
        out = build_index(df, IndexConfig(chained=chained))
        assert out.loc[periods[0], "index"] == pytest.approx(100.0)


# ---------------------------------------------------------------------
# Parameter hash coverage: the registry's content hash and the cache key
# must both change when only a reference period differs. Both already flow
# through RunConfig.to_json(), which serialises every real field including
# the three added by this patch, so this was correct before these tests
# were written; they exist to keep it that way against a future change
# (e.g. an `exclude=True` added to one of these fields by mistake).
# ---------------------------------------------------------------------
def test_registry_hash_differs_when_only_index_reference_period_differs(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'hash_test.db'}")
    from pricelab.core import db, registry
    from pricelab.core.config import RunConfig, get_settings
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()

    df, periods = _two_item_panel()
    df = df.rename(columns={"price_imputed": "price_reported"})
    df["category"] = "All"

    cfg_a = RunConfig(index=IndexConfig(index_reference_period=str(periods[0].date())))
    cfg_b = RunConfig(index=IndexConfig(index_reference_period=str(periods[1].date())))

    with db.session_scope() as s:
        run_a = registry.register_run(s, df, cfg_a, "a")
        run_b = registry.register_run(s, df, cfg_b, "b")
        assert run_a.run_id != run_b.run_id
        assert run_a.content_hash != run_b.content_hash

    db.reset_db_state()
    get_settings.cache_clear()


def test_cache_key_differs_when_only_index_reference_period_differs():
    from pricelab.core.cache import content_key

    file_bytes = b"same file, byte for byte"
    cfg_a = IndexConfig(index_reference_period="2020-01-01")
    cfg_b = IndexConfig(index_reference_period="2020-02-01")

    key_a = content_key(file_bytes, cfg_a.model_dump_json(), "label")
    key_b = content_key(file_bytes, cfg_b.model_dump_json(), "label")
    assert key_a != key_b


def test_cache_does_not_serve_one_reference_period_configs_entry_for_another():
    from pricelab.core.cache import BoundedCache, content_key

    file_bytes = b"same file, byte for byte"
    cfg_a = IndexConfig(index_reference_period="2020-01-01")
    cfg_b = IndexConfig(index_reference_period="2020-02-01")
    key_a = content_key(file_bytes, cfg_a.model_dump_json(), "label")
    key_b = content_key(file_bytes, cfg_b.model_dump_json(), "label")

    cache: BoundedCache[str] = BoundedCache(max_entries=4)
    cache.set(key_a, "result computed for A")
    assert cache.get(key_b) is None  # not served from A's entry
    cache.set(key_b, "result computed for B")
    assert cache.get(key_a) == "result computed for A"
    assert cache.get(key_b) == "result computed for B"


# ---------------------------------------------------------------------
# weight_reference_period must not be after price_reference_period
# ---------------------------------------------------------------------
def test_weight_reference_after_price_reference_is_rejected():
    with pytest.raises(ValidationError, match="weight_reference_period"):
        IndexConfig(price_reference_period="2020-01-01", weight_reference_period="2020-06-01")


def test_weight_reference_after_base_period_fallback_is_also_rejected():
    """The validator must resolve the same fallback engine.index.build_index
    uses (price_reference_period, else base_period), not only the new
    field, or a config that still sets the deprecated alias could smuggle
    an incoherent pair past it."""
    with pytest.raises(ValidationError, match="weight_reference_period"):
        IndexConfig(base_period="2020-01-01", weight_reference_period="2020-06-01")


def test_weight_reference_before_price_reference_is_accepted():
    IndexConfig(price_reference_period="2020-06-01", weight_reference_period="2020-01-01")


def test_weight_reference_equal_to_price_reference_is_accepted():
    IndexConfig(price_reference_period="2020-01-01", weight_reference_period="2020-01-01")


def test_weight_reference_with_no_price_reference_to_compare_is_accepted():
    """Nothing to validate against yet: not a coherence violation, just an
    under-specified config the engine will resolve its own way at run time."""
    IndexConfig(weight_reference_period="2020-01-01")


# ---------------------------------------------------------------------
# Upconversion audit coverage at the interface layer, not only inside
# core.registry.reproduce(): pages.common.load_registered_run is the one
# function the interface actually calls to load a registered run (the only
# way a legacy config can be loaded through the app at all), so proving
# *it* results in an audit record closes the loop end to end rather than
# only proving the lower-level registry function does.
# ---------------------------------------------------------------------
def test_interface_level_load_of_a_legacy_run_is_audited(tmp_path, monkeypatch):
    import json

    from pricelab.core import audit, db, registry
    from pricelab.core.config import RunConfig, get_settings

    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'interface_audit.db'}")
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()

    df = pd.DataFrame({
        "period": pd.to_datetime(["2020-01-01", "2020-02-01"] * 2),
        "category": ["Bread", "Bread", "Milk", "Milk"],
        "item_id": ["1", "1", "2", "2"],
        "item_name": ["White", "White", "Semi", "Semi"],
        "price_reported": [1.0, 1.1, 0.9, 0.95],
    })
    cfg = RunConfig(label="test", index=IndexConfig(chained=False, base_period="2020-01-01"))
    with db.session_scope() as s:
        run_id = registry.register_run(s, df, cfg, "test").run_id
        registry.approve_run(s, run_id)

    # Force the stored config back to schema_version 1 shape, simulating a
    # run registered before the reference-period split existed.
    with db.session_scope() as s:
        row = s.query(registry.IndexRunORM).filter_by(run_id=run_id).one()
        legacy = json.loads(row.config_json)
        legacy.pop("schema_version", None)
        for field in ("price_reference_period", "weight_reference_period",
                      "index_reference_period"):
            legacy["index"].pop(field, None)
        row.config_json = json.dumps(legacy)
        s.add(row)

    import pages.common as common_page

    result, upconverted = common_page.load_registered_run(run_id, actor="viewer1")

    assert upconverted is True
    assert "indices" in result

    with db.session_scope() as s:
        events = s.query(audit.AuditEventORM).filter_by(
            action=audit.LEGACY_CONFIG_UPCONVERTED).all()
        assert len(events) == 1
        assert events[0].actor == "viewer1"
        assert run_id in events[0].target

    db.reset_db_state()
    get_settings.cache_clear()


def test_interface_level_load_of_a_current_run_is_not_flagged_upconverted(tmp_path, monkeypatch):
    from pricelab.core import db, registry
    from pricelab.core.config import RunConfig, get_settings

    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'interface_audit2.db'}")
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()

    df = pd.DataFrame({
        "period": pd.to_datetime(["2020-01-01", "2020-02-01"] * 2),
        "category": ["Bread", "Bread", "Milk", "Milk"],
        "item_id": ["1", "1", "2", "2"],
        "item_name": ["White", "White", "Semi", "Semi"],
        "price_reported": [1.0, 1.1, 0.9, 0.95],
    })
    cfg = RunConfig(label="test")
    with db.session_scope() as s:
        run_id = registry.register_run(s, df, cfg, "test").run_id

    import pages.common as common_page

    _result, upconverted = common_page.load_registered_run(run_id, actor="viewer1")
    assert upconverted is False

    db.reset_db_state()
    get_settings.cache_clear()


# ---------------------------------------------------------------------
# An index reference period the series cannot be rebased to is an error,
# not a silent no-op. The label side (`resolve_index_reference_period`)
# would go on naming the requested period on every export; the arithmetic
# side must not quietly decline to honour it.
# ---------------------------------------------------------------------
def test_an_index_reference_period_absent_from_the_data_is_refused_not_ignored():
    df, _periods = _two_item_panel()
    cfg = IndexConfig(chained=False, index_reference_period="2021-06-01")
    with pytest.raises(ValueError, match="index_reference_period 2021-06-01 does not match"):
        build_index(df, cfg)


def test_the_same_refusal_applies_to_a_chained_series():
    df, _periods = _two_item_panel()
    cfg = IndexConfig(chained=True, index_reference_period="2021-06-01")
    with pytest.raises(ValueError, match="index_reference_period 2021-06-01 does not match"):
        build_index(df, cfg)


def test_an_index_reference_period_with_no_computable_level_yields_no_levels_at_all():
    """Fixed-base, and the requested index reference period is one where
    fewer than min_matched_items items matched the price reference, so the
    level there is NaN. The old behaviour skipped the rebase and returned
    the series at its price-reference scaling, under labels claiming it
    read 100 at the index reference period. A series that cannot be
    rebased has no publishable levels: every level is NaN, and the matched
    counts and flags that explain why are kept."""
    df, periods = _two_item_panel()
    # Item 2 is unpriced in period 3, leaving one matched item there.
    df = df.loc[~((df["item_id"] == "2") & (df["period"] == periods[2]))]
    cfg = IndexConfig(chained=False, min_matched_items=2,
                      price_reference_period=str(periods[0].date()),
                      index_reference_period=str(periods[2].date()))
    out = build_index(df, cfg)

    assert out["index"].isna().all()
    assert bool(out.loc[periods[2], "insufficient_match"])
    assert out.loc[periods[2], "matched_items"] == 1
    assert out.loc[periods[0], "matched_items"] == 2
