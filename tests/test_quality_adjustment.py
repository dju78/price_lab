"""Quality adjustment: the seven non-hedonic methods against the CPI Manual
2020 Chapter 6 worked examples, the ledger's application to a panel, and
the impact report's attribution.

Golden values. The source is the IMF's published Chapter 6 of the CPI
Manual 2020, "Temporarily and Permanently Missing Prices and Quality
Change" (imf.org/-/media/files/data/cpi/chapter-6-missing-prices-and-
quality-adjustment.pdf). The worked examples reproduced here are the ones
whose inputs and outputs are both printed in the text:

  equation (6.4)   Jevons relative over outlets A-E, February to March
                   2020: 5.67 / 5.54 = 1.023765 (overall mean imputation)
  targeted mean    outlet F carried by outlets D and E: 5.99 -> 6.26 ->
                   6.32 -> 6.34, with relatives 1.04522, 1.00881, 1.00434
  Table 6.4a       old item 25, 28; replacement 35 linked to show no
                   change: January-to-March change 1.12
  Table 6.5        bags of flour at 0.25 kg / 0.75 and 0.5 kg / 1.50 are
                   both 3.00 per kg; 0.25 kg at 1.25 is 5.00 per kg
  option cost      10,000 -> 10,500 with a 300 option made standard:
                   10,500 / 10,300 = 1.01942

Table 6.1's full price tableau and Table 6.6's washing-machine regression
data are not reproducible from the published text (the tableau is not in
the extractable text and the regression's dataset is not printed), so
Table 6.2's full chain and Table 6.6's coefficients are not asserted.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from pricelab import run_pipeline
from pricelab.core.config import (
    IndexConfig,
    QualityAdjustmentConfig,
    QualityAdjustmentEntry,
    RunConfig,
)
from pricelab.engine import quality_adjustment as qa

P = pd.Timestamp


# ---------------------------------------------------------------------
# Golden values: CPI Manual 2020, Chapter 6
# ---------------------------------------------------------------------
def test_overall_mean_imputation_reproduces_equation_6_4():
    """Outlets A-E, February and March 2020. The Jevons relative of the
    matched outlets is 1.023765 to six decimals, and outlet F's 5.99 is
    carried to 6.13 by it."""
    feb = pd.Series([5.49, 5.10, 5.20, 5.49, 6.50], index=list("ABCDE"))
    mar = pd.Series([5.49, 5.25, 5.20, 5.65, 6.90], index=list("ABCDE"))
    # Outlet F's replacement is priced at the imputed level, so the whole
    # gap between 5.99 and the replacement is pure price and q = 1.
    adj = qa.overall_mean("F", "F_new", "rice", P("2020-03-01"), 5.99, 5.99 * 1.023765,
                          feb, mar)
    assert adj.parameters["imputed_price_relative"] == pytest.approx(1.023765, abs=5e-7)
    assert adj.parameters["n_peers"] == 5
    assert adj.adjusted_new_price == pytest.approx(6.13, abs=0.005)
    assert adj.quality_ratio == pytest.approx(1.0, abs=1e-6)


def test_targeted_mean_imputation_reproduces_the_outlet_f_chain():
    """Outlet F carried by outlets D and E only: 5.99 -> 6.26 -> 6.32 -> 6.34,
    chained unrounded as the manual does (6.32 x 1.00434 rounds to 6.35;
    6.3160 x 1.00434 = 6.3434 rounds to 6.34, which is what it prints)."""
    d = {"feb": 5.49, "mar": 5.65, "apr": 5.75, "may": 5.80}
    e = {"feb": 6.50, "mar": 6.90, "apr": 6.90, "may": 6.90}
    price = 5.99
    expected = [(1.04522, 6.26), (1.00881, 6.32), (1.00434, 6.34)]
    for (a, b), (rel, level) in zip([("feb", "mar"), ("mar", "apr"), ("apr", "may")], expected,
                                    strict=True):
        prev = pd.Series([d[a], e[a]], index=["D", "E"])
        curr = pd.Series([d[b], e[b]], index=["D", "E"])
        adj = qa.targeted_mean("F", "F", "rice", P("2020-03-01"), price, price, prev, curr)
        assert adj.parameters["imputed_price_relative"] == pytest.approx(rel, abs=5e-6)
        price = price * adj.parameters["imputed_price_relative"]
        assert price == pytest.approx(level, abs=0.005)
        assert adj.parameters["peers"] == ["D", "E"]


def test_link_to_show_no_change_reproduces_table_6_4a_and_warns():
    """Old 25 then 28; replacement 35. Linked to show no change, the
    January-to-March movement is 28/25 x 35/35 = 1.12: the whole 28 -> 35
    step is called quality, and the manual says not to do this."""
    with pytest.warns(qa.LinkToShowNoChangeWarning):
        adj = qa.link_to_show_no_change("m", "n", "x", P("2020-03-01"), 28.0, 35.0)
    assert adj.quality_ratio == pytest.approx(35 / 28)
    assert adj.pure_price_relative == pytest.approx(1.0)
    assert (28 / 25) * adj.pure_price_relative == pytest.approx(1.12)
    assert adj.reason_code == "link_to_show_no_change"


def test_half_of_the_gap_as_quality_is_expressible_through_option_cost():
    """The manual's counterfactual for Table 6.4a: if half of the 7 gap was
    quality, the February-to-March price change should be 31.5 / 28."""
    adj = qa.option_cost("m", "n", "x", P("2020-03-01"), 28.0, 35.0, option_value=7.0,
                         share_valued=0.5)
    assert adj.adjusted_new_price == pytest.approx(35.0 / (31.5 / 28.0))
    assert adj.pure_price_relative == pytest.approx(35.0 / 31.5)


def test_quantity_adjustment_reproduces_table_6_5():
    """Flour: 0.25 kg at 0.75 and 0.5 kg at 1.50 are both 3.00 per kg, so a
    replacement of one by the other is no price change; 0.25 kg at 1.25
    is a rise to 5.00 per kg."""
    same = qa.quantity_adjustment("flour250", "flour500", "flour", P("2020-02-01"),
                                  0.75, 1.50, 0.25, 0.5, unit="kg")
    assert same.parameters["old_unit_price"] == pytest.approx(3.0)
    assert same.parameters["new_unit_price"] == pytest.approx(3.0)
    assert same.quality_ratio == pytest.approx(2.0)
    assert same.pure_price_relative == pytest.approx(1.0)
    assert same.adjustment_price == pytest.approx(0.75)

    dearer = qa.quantity_adjustment("flour250", "flour250b", "flour", P("2020-02-01"),
                                    0.75, 1.25, 0.25, 0.25, unit="kg")
    assert dearer.parameters["new_unit_price"] == pytest.approx(5.0)
    assert dearer.pure_price_relative == pytest.approx(5.0 / 3.0)


def test_option_cost_reproduces_the_manuals_example():
    """10,000 then 10,500, the new one including as standard a feature that
    was a 300 option: 10,500 / 10,300 = 1.01942."""
    adj = qa.option_cost("a", "b", "x", P("2020-02-01"), 10_000.0, 10_500.0, option_value=300.0)
    assert adj.pure_price_relative == pytest.approx(1.01942, abs=5e-6)
    assert adj.parameters["adjusted_old_price"] == pytest.approx(10_300.0)
    assert adj.quality_ratio == pytest.approx(1.03)
    assert adj.reason_code == "option_cost"


# ---------------------------------------------------------------------
# Method behaviour
# ---------------------------------------------------------------------
def test_overlap_takes_the_overlap_ratio_and_records_the_new_items_own_change():
    old = pd.Series([10.0, 10.5, 11.0], index=pd.date_range("2020-01-01", periods=3, freq="MS"))
    new = pd.Series([13.2, 13.86], index=pd.date_range("2020-03-01", periods=2, freq="MS"))
    adj = qa.overlap("old", "new", "x", old, new, overlap_period=P("2020-03-01"))
    assert adj.quality_ratio == pytest.approx(13.2 / 11.0)
    assert adj.period == P("2020-04-01")
    assert adj.pure_price_relative == pytest.approx(13.86 / 13.2)   # the new item's own change
    assert adj.parameters["overlap_period"] == "2020-03-01"


def test_overlap_refuses_when_the_items_do_not_actually_overlap():
    old = pd.Series([10.0, 10.5], index=pd.date_range("2020-01-01", periods=2, freq="MS"))
    new = pd.Series([13.2, 13.86], index=pd.date_range("2020-03-01", periods=2, freq="MS"))
    with pytest.raises(qa.QualityAdjustmentError, match="needs both items priced"):
        qa.overlap("old", "new", "x", old, new, overlap_period=P("2020-03-01"))


def test_direct_comparison_is_ratio_one():
    adj = qa.direct_comparison("a", "b", "x", P("2020-02-01"), 10.0, 12.0)
    assert adj.quality_ratio == 1.0
    assert adj.adjustment_price == 0.0
    assert adj.pure_price_relative == pytest.approx(1.2)


def test_class_mean_uses_other_replacements_adjusted_relatives():
    adj = qa.class_mean("a", "b", "x", P("2020-02-01"), 10.0, 13.0, peer_relatives=[1.05, 1.03])
    expected_rel = float(np.exp(np.mean(np.log([1.05, 1.03]))))
    assert adj.parameters["imputed_price_relative"] == pytest.approx(expected_rel)
    assert adj.quality_ratio == pytest.approx(1.3 / expected_rel)


def test_class_mean_refuses_with_no_peers():
    with pytest.raises(qa.QualityAdjustmentError, match="at least one quality-adjusted"):
        qa.class_mean("a", "b", "x", P("2020-02-01"), 10.0, 13.0, peer_relatives=[])


def test_mean_imputation_refuses_with_no_matched_peer():
    prev = pd.Series([1.0], index=["p"])
    curr = pd.Series([1.1], index=["q"])
    with pytest.raises(qa.QualityAdjustmentError, match="at least one peer"):
        qa.targeted_mean("a", "b", "x", P("2020-02-01"), 10.0, 13.0, prev, curr)


def test_every_method_reports_price_terms_index_points_and_a_reason_code():
    """The specification's contract: adjustment in price terms, in index
    points, and a reason code, from every method."""
    cell = qa.CellContext(n_items=5)
    adjustments = [
        qa.direct_comparison("a", "b", "x", P("2020-02-01"), 10.0, 12.0, cell=cell),
        qa.quantity_adjustment("a", "b", "x", P("2020-02-01"), 10.0, 12.0, 1.0, 1.1, cell=cell),
        qa.option_cost("a", "b", "x", P("2020-02-01"), 10.0, 12.0, 1.0, cell=cell),
        qa.class_mean("a", "b", "x", P("2020-02-01"), 10.0, 12.0, [1.05], cell=cell),
        qa.targeted_mean("a", "b", "x", P("2020-02-01"), 10.0, 12.0,
                         pd.Series([1.0], index=["p"]), pd.Series([1.05], index=["p"]), cell=cell),
        qa.overall_mean("a", "b", "x", P("2020-02-01"), 10.0, 12.0,
                        pd.Series([1.0], index=["p"]), pd.Series([1.05], index=["p"]), cell=cell),
    ]
    codes = {a.reason_code for a in adjustments}
    assert codes == {"direct_comparison", "quantity_adjustment", "option_cost",
                     "class_mean_imputation", "targeted_mean_imputation",
                     "overall_mean_imputation"}
    for a in adjustments:
        assert np.isfinite(a.adjustment_price)
        assert np.isfinite(a.index_points)
        assert a.adjusted_new_price == pytest.approx(a.new_price / a.quality_ratio)
        assert a.adjustment_price == pytest.approx(a.new_price - a.adjusted_new_price)


def test_index_points_for_jevons_are_the_exact_effect_on_the_cells_link():
    """Five matched relatives, one of them divided by q: the Jevons link
    is multiplied by q^(-1/5) exactly."""
    relatives = np.array([1.02, 1.01, 1.03, 0.99, 1.30])
    q = 1.2
    raw = np.exp(np.log(relatives).mean())
    adjusted = np.exp(np.log(relatives / np.array([1, 1, 1, 1, q])).mean())
    points = qa.index_point_effect(q, qa.CellContext(n_items=5), 1.30, 13.0)
    assert points == pytest.approx(100 * (adjusted / raw - 1))
    assert points < 0


def test_index_points_for_carli_and_dutot_match_recomputation():
    relatives = np.array([1.02, 1.01, 1.03, 0.99, 1.30])
    prev = np.array([10.0, 20.0, 5.0, 8.0, 10.0])
    curr = prev * relatives
    q = 1.2
    carli_raw, carli_adj = relatives.mean(), (relatives / [1, 1, 1, 1, q]).mean()
    assert qa.index_point_effect(q, qa.CellContext(5, "carli"), 1.30, 13.0) == pytest.approx(
        100 * (carli_adj - carli_raw))
    dutot_raw = curr.sum() / prev.sum()
    dutot_adj = (curr.sum() - 13.0 + 13.0 / q) / prev.sum()
    assert qa.index_point_effect(
        q, qa.CellContext(5, "dutot", cell_sum_prev=float(prev.sum())), 1.30, 13.0
    ) == pytest.approx(100 * (dutot_adj - dutot_raw))


def test_index_points_are_nan_without_a_cell_and_dutot_needs_the_sum():
    assert np.isnan(qa.index_point_effect(1.2, None, 1.3, 13.0))
    with pytest.raises(qa.QualityAdjustmentError, match="cell_sum_prev"):
        qa.index_point_effect(1.2, qa.CellContext(5, "dutot"), 1.3, 13.0)


def test_non_positive_inputs_are_refused():
    with pytest.raises(qa.QualityAdjustmentError):
        qa.direct_comparison("a", "b", "x", P("2020-02-01"), 0.0, 12.0)
    with pytest.raises(qa.QualityAdjustmentError):
        qa.quantity_adjustment("a", "b", "x", P("2020-02-01"), 10.0, 12.0, 0.0, 1.0)
    with pytest.raises(qa.QualityAdjustmentError):
        qa.option_cost("a", "b", "x", P("2020-02-01"), 10.0, 12.0, -1.0)
    with pytest.raises(qa.QualityAdjustmentError):
        qa.option_cost("a", "b", "x", P("2020-02-01"), 10.0, 12.0, 20.0, feature_added=False)


# ---------------------------------------------------------------------
# The ledger as configuration
# ---------------------------------------------------------------------
def _entry(old="A", new="B", ratio=1.2, category="x", period="2020-03-01", method="overlap"):
    return QualityAdjustmentEntry(old_item=old, new_item=new, category=category, period=period,
                                  method=method, quality_ratio=ratio, justification="j",
                                  approved_by="tester")


def test_to_entry_carries_the_valuation_and_the_approver():
    adj = qa.direct_comparison("a", "b", "x", P("2020-02-01"), 10.0, 12.0, justification="same spec")
    e = adj.to_entry("compiler1")
    assert (e.old_item, e.new_item, e.method, e.quality_ratio) == ("a", "b", "direct_comparison", 1.0)
    assert e.parameters["old_price"] == 10.0 and e.parameters["new_price"] == 12.0
    assert e.approved_by == "compiler1" and e.approved_at
    assert e.justification == "same spec"


def test_the_ledger_is_covered_by_the_config_json_and_round_trips():
    cfg = RunConfig(quality_adjustment=QualityAdjustmentConfig(entries=[_entry()]))
    plain = RunConfig()
    assert cfg.to_json() != plain.to_json()
    reloaded = RunConfig.from_dict(__import__("json").loads(cfg.to_json()))
    assert reloaded.quality_adjustment.entries == cfg.quality_adjustment.entries
    assert not reloaded.legacy_upconverted


def test_a_config_without_the_ledger_key_still_loads_with_an_empty_ledger():
    import json
    data = json.loads(RunConfig().to_json())
    del data["quality_adjustment"]
    cfg = RunConfig.from_dict(data)
    assert cfg.quality_adjustment.entries == []


def test_one_replacement_per_item_is_enforced():
    with pytest.raises(ValidationError, match="replacement for both"):
        QualityAdjustmentConfig(entries=[_entry("A", "B"), _entry("C", "B")])
    with pytest.raises(ValidationError, match="replaced by both"):
        QualityAdjustmentConfig(entries=[_entry("A", "B"), _entry("A", "C")])
    with pytest.raises(ValidationError):
        _entry(ratio=0.0)


# ---------------------------------------------------------------------
# Applying the ledger to a panel
# ---------------------------------------------------------------------
def _panel_with_replacement(ratio_in_prices: float = 1.2, n_periods: int = 6, k: int = 3):
    """Item A priced for periods 0..k-1 (and at k as an overlap), item B from
    k-1 on at `ratio_in_prices` times A's path, item C throughout; one
    category. B is A's replacement."""
    periods = pd.date_range("2020-01-01", periods=n_periods, freq="MS")
    a_path = np.array([10.0, 10.2, 10.4, 10.6, 10.8, 11.0])[:n_periods]
    rows = []
    for i, p in enumerate(periods):
        if i <= k:
            rows.append({"period": p, "category": "x", "item_id": "A", "item_name": "A",
                         "price_reported": a_path[i]})
        if i >= k - 1:
            rows.append({"period": p, "category": "x", "item_id": "B", "item_name": "B",
                         "price_reported": a_path[i] * ratio_in_prices})
        rows.append({"period": p, "category": "x", "item_id": "C", "item_name": "C",
                     "price_reported": 20.0 + 0.5 * i})
    return pd.DataFrame(rows), periods


def test_apply_adjustments_links_the_replacement_and_logs_every_moved_row():
    df, periods = _panel_with_replacement()
    df["price_clean"] = df["price_reported"]
    entry = _entry("A", "B", ratio=1.2, period=str(periods[3].date()))
    out, log = qa.apply_adjustments(df, [entry])

    a_rows = out[out["item_id"] == "A"].sort_values("period")
    assert list(a_rows["period"]) == list(periods)                      # one continuous series
    assert "B" not in set(out["item_id"])
    linked = a_rows[a_rows["period"] >= periods[3]]
    assert (linked["replaced_item_id"] == "B").all()
    assert (linked["quality_adjustment_flag"] == "overlap").all()
    assert linked["replacement_flag"].sum() == 1
    assert bool(linked.iloc[0]["replacement_flag"])
    # B's price divided by 1.2 is exactly A's path
    np.testing.assert_allclose(linked["price_clean"].to_numpy(), [10.6, 10.8, 11.0])
    assert "price_reported" in out.columns and (out.loc[linked.index, "price_reported"] > 12).all()
    assert set(log["action"]) == {"old item row replaced", "new item row before link",
                                  "3 new item rows linked"}


def test_apply_adjustments_refuses_unknown_items_and_periods():
    df, periods = _panel_with_replacement()
    df["price_clean"] = df["price_reported"]
    with pytest.raises(qa.QualityAdjustmentError, match="not in the data"):
        qa.apply_adjustments(df, [_entry("A", "Z", period=str(periods[3].date()))])
    with pytest.raises(qa.QualityAdjustmentError, match="no rows at or after"):
        qa.apply_adjustments(df, [_entry("A", "B", period="2030-01-01")])


def test_an_empty_ledger_leaves_the_pipeline_output_unchanged():
    df, _periods = _panel_with_replacement()
    base = run_pipeline(df, RunConfig(index=IndexConfig(min_matched_items=1)))
    same = run_pipeline(df, RunConfig(index=IndexConfig(min_matched_items=1),
                                      quality_adjustment=QualityAdjustmentConfig(entries=[])))
    pd.testing.assert_frame_equal(base["indices"], same["indices"])
    assert "quality_adjustment_impact" not in base
    assert base["link_log"].empty


def test_the_committed_fixture_is_unchanged_by_the_ledger_machinery(tmp_path):
    """The Phase 3 hard gate, through run_pipeline (which now applies a
    ledger) rather than the engine pieces: the fixture's series is
    identical to its committed baseline."""
    from pathlib import Path

    from pricelab import auto_configure, infer_schema, standardise

    root = Path(__file__).resolve().parents[1]
    raw = pd.read_excel(root / "supermarket_price_collection.xlsx", sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))
    cfg, _ = auto_configure(df, "hard gate")
    res = run_pipeline(df, cfg)
    expected = pd.read_parquet(root / "tests" / "fixtures" / "phase3_baseline_index.parquet")
    expected.index = pd.to_datetime(expected.index)
    pd.testing.assert_frame_equal(res["indices"], expected, check_names=False)


# ---------------------------------------------------------------------
# The impact report
# ---------------------------------------------------------------------
def _cfg_with(entries):
    return RunConfig(index=IndexConfig(min_matched_items=1),
                     quality_adjustment=QualityAdjustmentConfig(entries=entries))


def test_a_correct_overlap_ratio_recovers_the_untouched_series_exactly():
    """B is A at exactly x1.2, so an overlap ratio of 1.2 must reproduce
    the index the panel would have had if A had never been replaced."""
    df, periods = _panel_with_replacement(ratio_in_prices=1.2)
    untouched = df[df["item_id"] != "B"].copy()
    # A continues to the end in the untouched world
    a_path = [10.0, 10.2, 10.4, 10.6, 10.8, 11.0]
    extra = [{"period": p, "category": "x", "item_id": "A", "item_name": "A",
              "price_reported": a_path[i]} for i, p in enumerate(periods) if i > 3]
    untouched = pd.concat([untouched, pd.DataFrame(extra)], ignore_index=True)
    reference = run_pipeline(untouched, _cfg_with([]))["indices"]

    entry = _entry("A", "B", ratio=1.2, period=str(periods[3].date()))
    res = run_pipeline(df, _cfg_with([entry]))
    np.testing.assert_allclose(res["indices"]["x"].to_numpy(), reference["x"].to_numpy())


def test_switching_method_changes_the_headline_and_is_fully_attributed():
    """Acceptance criterion: a different valuation moves the headline, and
    the impact report attributes the whole move -- per entry, plus an
    explicit residual that makes the attribution sum exactly."""
    df, periods = _panel_with_replacement(ratio_in_prices=1.2)
    period = str(periods[3].date())
    overlap = run_pipeline(df, _cfg_with([_entry("A", "B", ratio=1.2, period=period)]))
    direct = run_pipeline(df, _cfg_with([_entry("A", "B", ratio=1.0, period=period,
                                                 method="direct_comparison")]))
    assert overlap["indices"]["x"].iloc[-1] != pytest.approx(direct["indices"]["x"].iloc[-1])

    imp = overlap["quality_adjustment_impact"]
    assert imp.headline == "x"
    assert imp.scenarios.loc["linked_unadjusted", "final_level"] == pytest.approx(
        direct["indices"]["x"].iloc[-1])
    assert imp.adjustment_effect_points == pytest.approx(
        overlap["indices"]["x"].iloc[-1] - direct["indices"]["x"].iloc[-1])
    assert imp.adjustment_effect_points < 0            # valuing the +20% as quality lowers it
    per_entry = imp.per_entry["effect_points"]
    assert per_entry.drop("interaction residual").sum() + imp.interaction_residual_points == \
        pytest.approx(imp.adjustment_effect_points)
    assert imp.interaction_residual_points == pytest.approx(0.0, abs=1e-9)   # one entry
    assert imp.annual_measure == "annualised_over_span"                     # 6 periods
    assert np.isfinite(imp.adjustment_effect_annual_pp)
    assert set(imp.per_category.index) == {"x"}


def test_the_no_link_scenario_is_the_matched_model_default():
    df, periods = _panel_with_replacement(ratio_in_prices=1.2)
    entry = _entry("A", "B", ratio=1.2, period=str(periods[3].date()))
    res = run_pipeline(df, _cfg_with([entry]))
    no_link = run_pipeline(df, _cfg_with([]))
    imp = res["quality_adjustment_impact"]
    assert imp.scenarios.loc["no_link", "final_level"] == pytest.approx(
        no_link["indices"]["x"].iloc[-1])
    assert imp.linking_effect_points == pytest.approx(
        res["indices"]["x"].iloc[-1] - no_link["indices"]["x"].iloc[-1])


def test_two_entries_in_one_cell_report_their_interaction_rather_than_hiding_it():
    df, periods = _panel_with_replacement(ratio_in_prices=1.2)
    # A second replacement: C leaves at period 4, D arrives at x1.5.
    c_rows = df[df["item_id"] == "C"]
    d_rows = c_rows[c_rows["period"] >= periods[3]].copy()
    d_rows["item_id"] = "D"
    d_rows["price_reported"] *= 1.5
    df2 = pd.concat([df[~((df["item_id"] == "C") & (df["period"] >= periods[4]))], d_rows],
                    ignore_index=True)
    entries = [_entry("A", "B", ratio=1.2, period=str(periods[3].date())),
               _entry("C", "D", ratio=1.5, period=str(periods[4].date()))]
    imp = run_pipeline(df2, _cfg_with(entries))["quality_adjustment_impact"]
    assert imp.n_entries == 2
    effects = imp.per_entry["effect_points"]
    assert effects.drop("interaction residual").sum() + imp.interaction_residual_points == \
        pytest.approx(imp.adjustment_effect_points)


def test_ledger_frame_lists_one_row_per_entry():
    frame = qa.ledger_frame([_entry("A", "B"), _entry("C", "D", ratio=0.9)])
    assert list(frame["old_item"]) == ["A", "C"]
    assert list(frame["quality_ratio"]) == [1.2, 0.9]
    assert qa.ledger_frame([]).empty


def test_the_link_warning_is_a_warning_not_an_error():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(qa.LinkToShowNoChangeWarning):
            qa.link_to_show_no_change("m", "n", "x", P("2020-03-01"), 28.0, 35.0)
