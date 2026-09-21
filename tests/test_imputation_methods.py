"""The two imputation methods added in this phase, and response-rate
tracking.

The property that matters for both new methods is that they are anchored
rather than cascading: every fill traces back to a genuinely observed
price, so an imputed value is never an input to another imputation. The
existing class_mean deliberately does cascade, and the contrast is tested
directly rather than described.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricelab.core.config import ImputationConfig
from pricelab.engine.imputation import (
    METHODS,
    imputation_summary,
    response_rates,
    run_imputation,
)

PERIODS = pd.date_range("2020-01-01", periods=5, freq="MS")


def _panel(drift: float = 0.02) -> pd.DataFrame:
    """Two Bread items and one Milk item, every price rising by `drift`
    per period, so the correct imputed value for any gap is known
    exactly."""
    rows = []
    for i, period in enumerate(PERIODS):
        for item, category, base in [("1", "Bread", 1.0), ("2", "Bread", 1.2),
                                     ("3", "Milk", 0.9)]:
            rows.append((period, category, item, base * (1 + drift * i)))
    return pd.DataFrame(rows, columns=["period", "category", "item_id", "price_clean"])


def _with_gap(df: pd.DataFrame, item: str, periods) -> pd.DataFrame:
    out = df.copy()
    out.loc[(out["item_id"] == item) & (out["period"].isin(periods)), "price_clean"] = np.nan
    return out


# ---------------------------------------------------------------------
# Both new methods recover a known price
# ---------------------------------------------------------------------
@pytest.mark.parametrize("method", ["targeted_mean", "overall_mean"])
def test_the_new_methods_recover_the_true_price_of_a_single_period_gap(method):
    df = _with_gap(_panel(), "1", [PERIODS[2]])
    out = run_imputation(df, ImputationConfig(default_method=method))
    filled = out.loc[out["imputation"] == method]

    assert len(filled) == 1
    assert filled["price_imputed"].iloc[0] == pytest.approx(1.0 * 1.04)


@pytest.mark.parametrize("method", ["targeted_mean", "overall_mean"])
def test_the_new_methods_stay_anchored_across_a_multi_period_gap(method):
    """Every fill is computed from the item's last genuinely observed
    price, so a long gap does not compound imputations on imputations."""
    df = _with_gap(_panel(), "1", PERIODS[1:4])
    out = run_imputation(df, ImputationConfig(default_method=method))
    filled = out.loc[out["imputation"] == method].sort_values("period")

    assert filled["price_imputed"].tolist() == pytest.approx([1.02, 1.04, 1.06])


def test_a_matched_cell_factor_is_used_not_a_raw_mean_of_whatever_was_collected():
    """The trap this had to avoid. Bread's two items sit at different
    price levels, so the cell's raw mean jumps when the cheap one goes
    missing -- and imputing from that jump would write a fictitious rise
    onto the very item that disappeared. The matched factor gives the
    true value instead.
    """
    df = _with_gap(_panel(), "1", [PERIODS[2]])
    out = run_imputation(df, ImputationConfig(default_method="targeted_mean"))
    imputed = out.loc[out["imputation"] == "targeted_mean", "price_imputed"].iloc[0]

    observed_bread_march = df.loc[
        (df["category"] == "Bread") & (df["period"] == PERIODS[2]), "price_clean"].mean()
    observed_bread_feb = df.loc[
        (df["category"] == "Bread") & (df["period"] == PERIODS[1]), "price_clean"].mean()
    naive = 1.02 * (observed_bread_march / observed_bread_feb)

    assert imputed == pytest.approx(1.04)
    assert naive > 1.10                    # what the unmatched version would have written
    assert imputed != pytest.approx(naive)


def test_overall_mean_can_fill_where_targeted_mean_cannot():
    """The reason to keep both: where every item in a category is missing
    at once, that category's own cell has no matched pair to compute a
    change from, while the collection as a whole still does."""
    df = _with_gap(_panel(), "1", [PERIODS[2]])
    df = _with_gap(df, "2", [PERIODS[2]])          # all of Bread missing in March

    targeted = run_imputation(df.copy(), ImputationConfig(default_method="targeted_mean"))
    overall = run_imputation(df.copy(), ImputationConfig(default_method="overall_mean"))

    assert (targeted["imputation"] == "targeted_mean").sum() == 0
    assert (overall["imputation"] == "overall_mean").sum() == 2
    assert overall.loc[overall["imputation"] == "overall_mean", "price_imputed"].tolist() == \
        pytest.approx([1.04, 1.2 * 1.04])


def test_a_gap_with_no_prior_observation_is_left_unfilled():
    """An item whose first periods are missing has no anchor, and the
    honest answer is to leave it out of the index rather than invent a
    starting price for it."""
    df = _with_gap(_panel(), "1", PERIODS[:2])
    out = run_imputation(df, ImputationConfig(default_method="targeted_mean"))
    first_two = out[(out["item_id"] == "1") & (out["period"].isin(PERIODS[:2]))]
    assert first_two["price_imputed"].isna().all()


def test_a_collection_with_no_observed_prices_at_all_returns_unchanged():
    df = _panel()
    df["price_clean"] = np.nan
    out = run_imputation(df, ImputationConfig(default_method="targeted_mean"))
    assert out["price_imputed"].isna().all()


def test_the_new_methods_are_selectable_by_name():
    assert "targeted_mean" in METHODS
    assert "overall_mean" in METHODS
    assert set(METHODS) == {"none", "carry_forward", "class_mean", "seasonal_hold",
                            "targeted_mean", "overall_mean"}


# ---------------------------------------------------------------------
# Response rates
# ---------------------------------------------------------------------
def test_response_rates_separate_observed_from_imputed_from_still_missing():
    df = _with_gap(_panel(), "1", [PERIODS[2]])
    out = run_imputation(df, ImputationConfig(default_method="targeted_mean"))
    rates = response_rates(out)

    march_bread = rates.loc[("Bread", PERIODS[2])]
    assert march_bread["expected"] == 2
    assert march_bread["observed"] == 1
    assert march_bread["imputed"] == 1
    assert march_bread["still_missing"] == 0
    assert march_bread["response_rate"] == pytest.approx(0.5)
    assert march_bread["imputed_share"] == pytest.approx(0.5)


def test_a_full_response_reports_a_rate_of_one():
    out = run_imputation(_panel(), ImputationConfig(default_method="none"))
    rates = response_rates(out)
    assert (rates["response_rate"] == 1.0).all()
    assert (rates["imputed_share"] == 0.0).all()


def test_an_unfilled_gap_shows_up_as_still_missing_rather_than_vanishing():
    """The number that makes a conservative method's cost visible:
    targeted_mean leaves what it cannot anchor, and the count says so."""
    df = _with_gap(_panel(), "1", PERIODS[:2])
    out = run_imputation(df, ImputationConfig(default_method="targeted_mean"))
    rates = response_rates(out)
    assert rates.loc[("Bread", PERIODS[0]), "still_missing"] == 1


def test_response_rates_can_group_by_something_other_than_category():
    df = _with_gap(_panel(), "1", [PERIODS[2]])
    out = run_imputation(df, ImputationConfig(default_method="targeted_mean"))
    rates = response_rates(out, by="item_id")
    assert rates.loc[("1", PERIODS[2]), "imputed"] == 1


def test_response_rates_work_on_a_frame_that_was_never_imputed():
    rates = response_rates(_panel().assign(price_imputed=lambda d: d["price_clean"]))
    assert (rates["response_rate"] == 1.0).all()


# ---------------------------------------------------------------------
# Imputation summary
# ---------------------------------------------------------------------
def test_the_summary_reports_each_method_used_and_its_share_of_priced_values():
    df = _with_gap(_panel(), "1", [PERIODS[2], PERIODS[3]])
    out = run_imputation(df, ImputationConfig(default_method="targeted_mean"))
    summary = imputation_summary(out)

    assert summary.loc["targeted_mean", "values"] == 2
    assert summary.loc["targeted_mean", "share_of_priced"] == pytest.approx(2 / 15)


def test_the_summary_is_empty_when_nothing_was_imputed():
    out = run_imputation(_panel(), ImputationConfig(default_method="none"))
    assert imputation_summary(out).empty


def test_the_summary_handles_a_frame_with_no_imputation_column():
    assert imputation_summary(_panel()).empty
