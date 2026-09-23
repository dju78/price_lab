"""Seasonality: strictly seasonal items, the two treatments and the gap
between them, the Rothwell index, counter-seasonal estimation, and seasonal
adjustment.

Two rules are enforced by this page rather than left to the reader. The
engine that actually produced the adjusted series is named on screen, in the
chart caption and in every download, including -- especially -- when it is
the fallback rather than the method a reader would assume. And the
unadjusted series is drawn on the same axes and written into the same file
as the adjusted one, because the easiest way for this platform to mislead
somebody is a smooth line with nothing beside it.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit
from pricelab.core.config import MultilateralConfig, SeasonalConfig
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import seasonal as sn

from . import common


def _sentence(label: str) -> str:
    """The engine label as a sentence.

    `str.capitalize` would lowercase everything after the first character,
    turning "X-13ARIMA-SEATS" into "x-13arima-seats" -- which is the one
    thing on this page that must survive verbatim, because it is what tells
    a reader which method produced the line.
    """
    return (label[:1].upper() + label[1:] + ".") if label else ""


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Seasonality")
    st.caption(
        "Two different problems share the word. Strictly seasonal items are off the shelf for "
        "part of every year, and the question is what the basket means while they are gone. "
        "Seasonal adjustment is about prices collected all year that move with the calendar, "
        "and it is the easiest place here to manufacture a smooth line that has removed a "
        "real movement.")

    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker(
            "Nobody has compiled a run yet. A compiler can do that on Ingest, or load a "
            "previously approved run below once one exists.")
        return
    res = analysis["result"]
    if "imputed" not in res:
        st.error("This run's data failed validation and has nothing to analyse.")
        return
    df, indices, index_cfg = res["imputed"], res["indices"], res["config"].index

    c1, c2, c3 = st.columns(3)
    periods_per_year = c1.selectbox("Periods in a year", [12, 4], index=0,
                                    key="sn_ppy", help="12 for monthly, 4 for quarterly.")
    min_years = c2.number_input("Years before an absence counts as a season", 2, 10, 2,
                                key="sn_years",
                                help="Below this, an interrupted item would be classed as "
                                     "seasonal: there is no repetition yet to observe.")
    ceiling = c3.slider("Maximum share of the year in season", 0.1, 1.0, 0.9, 0.05,
                        key="sn_ceiling",
                        help="An item on the shelf more than this is an item with gaps, not a "
                             "seasonal item; the two need different treatment.")
    cfg = SeasonalConfig(enabled=True, periods_per_year=int(periods_per_year),
                         min_years=int(min_years), max_in_season_share=float(ceiling))

    items = sn.strictly_seasonal_items(df, cfg)
    qualifying = items[items["strictly_seasonal"]]
    st.markdown("#### Strictly seasonal items")
    st.metric("Items off the shelf for part of every year", f"{len(qualifying):,}",
              help=f"of {len(items):,} items in the collection")
    if len(qualifying):
        st.dataframe(qualifying.drop(columns=["excluded_because"]), use_container_width=True)
    else:
        st.info("No item qualifies. The table below shows every item and the count that "
                "disqualified it, so 'none found' is a finding you can check rather than "
                "take on trust.")
        st.dataframe(items.head(30), use_container_width=True)

    _treatments(df, index_cfg, cfg)
    _adjustment(indices, cfg)
    _rothwell_and_counter(df, cfg)
    _seasonal_multilateral(df)


def _treatments(df: pd.DataFrame, index_cfg: Any, cfg: SeasonalConfig) -> None:
    st.divider()
    st.markdown("#### Class confinement against weight update")
    st.caption(
        "Class confinement keeps each class's full weight in every period and lets whichever "
        "of its items are in season carry it. Weight update takes the absent item's weight out "
        "of the basket and renormalises, so the basket's shape varies month by month with what "
        "is on sale. Both are compiled on the same aggregator, so the gap below is the weights "
        "and nothing else.")
    try:
        comparison = sn.compare_treatments(df, index_cfg, cfg)
    except sn.SeasonalError as exc:
        st.error(str(exc))
        return

    t1, t2, t3 = st.columns(3)
    t1.metric("Largest gap", f"{comparison.max_gap_pp:.3f} pts")
    t2.metric("Gap at the final period", f"{comparison.final_gap_pp:+.3f} pts")
    t3.metric("Seasonal items in play", f"{len(comparison.seasonal_items):,}")
    if comparison.identical:
        st.info("The two treatments agree exactly, because this collection has no strictly "
                "seasonal item for them to disagree about. That is a fact about the data, not "
                "evidence that the choice does not matter.")
    for note in comparison.notes:
        st.caption(note)

    st.line_chart(comparison.table[["class_confinement", "weight_update"]])
    st.dataframe(comparison.table.tail(24), use_container_width=True)
    st.download_button(
        "Download both treatments (CSV)",
        safe_csv_with_notice(comparison.table.reset_index()),
        file_name="seasonal_treatments.csv", mime="text/csv", key="sn_treatments_csv")


def _adjustment(indices: pd.DataFrame, cfg: SeasonalConfig) -> None:
    st.divider()
    st.markdown("#### Seasonal adjustment")
    available, _, explanation = sn.x13_available()
    (st.success if available else st.warning)(explanation)

    a1, a2 = st.columns([2, 2])
    series_name = a1.selectbox("Series to adjust", list(indices.columns),
                               index=list(indices.columns).index("All items")
                               if "All items" in indices.columns else 0, key="sn_series")
    engine = a2.selectbox(
        "Engine", ["auto", "x13", "stl"], key="sn_engine",
        format_func=lambda e: {"auto": "Prefer X-13ARIMA-SEATS, fall back to STL",
                               "x13": "X-13ARIMA-SEATS only (fail if absent)",
                               "stl": "STL only"}[e])
    settings = cfg.model_copy(update={"adjustment_engine": engine,
                                      "adjustment_series": str(series_name)})
    if not st.button("Adjust", type="primary", key="sn_adjust"):
        return

    try:
        adjustment = sn.adjust(indices[series_name], settings, series_name=str(series_name))
    except sn.SeasonalError as exc:
        st.error(str(exc))
        return
    common.record(audit.SEASONAL_ADJUSTMENT, str(series_name), {
        "engine_requested": adjustment.engine_requested, "engine_used": adjustment.engine,
        "fell_back": adjustment.fell_back,
        "trend_difference_pp": adjustment.stability.trend_difference_pp})
    st.session_state["sn_adjustment"] = adjustment

    # The engine, before the chart. A reader who stops here has still been
    # told what produced the line.
    (st.warning if adjustment.fell_back else st.success)(_sentence(adjustment.label))
    for warning in adjustment.warnings:
        st.warning(warning)

    # The same figure the Word report, the deck and the bulletin embed, so
    # the chart on screen carries the engine in its title, legend and note
    # exactly as the exported one does.
    from pricelab.reporting.charts import seasonal_adjustment_chart

    st.pyplot(seasonal_adjustment_chart(adjustment), use_container_width=True)
    st.caption("Unadjusted and adjusted on the same axes. "
               + _sentence(adjustment.label))
    common.show_uncertainty(
        "The seasonally adjusted series", run_headline=False,
        reason="an interval, where the Uncertainty page has one, is for the unadjusted "
               "movement; the adjustment adds model uncertainty that is not quantified here")

    s = adjustment.stability
    k1, k2, k3 = st.columns(3)
    k1.metric("Seasonal amplitude",
              f"{adjustment.diagnostics['seasonal_amplitude_pct']:.2f} pts")
    k2.metric("Factor range across sub-samples",
              "n/a" if pd.isna(s.max_factor_range_pct) else f"{s.max_factor_range_pct:.2f} pts",
              help=f"{s.sub_samples} sub-samples; a factor that moves a great deal between "
                   "them is not a seasonal pattern, it is a curve fitted to noise.")
    k3.metric("Trend introduced by the adjustment", f"{s.trend_difference_pp:+.3f} pp/yr",
              help="Seasonal factors that average out over a year cannot change a trend, so "
                   "anything materially non-zero here is the adjustment, not the prices.")
    if len(s.factor_ranges):
        st.dataframe(s.factor_ranges.to_frame(), use_container_width=True)
    for note in s.notes:
        st.caption(note)

    st.dataframe(adjustment.frame.tail(24), use_container_width=True)
    st.download_button(
        "Download adjusted and unadjusted (CSV)",
        safe_csv_with_notice(adjustment.frame.reset_index(), adjustment.label),
        file_name="seasonally_adjusted.csv", mime="text/csv", key="sn_adjusted_csv",
        help="The file carries both series and names the engine on its first line.")


def _rothwell_and_counter(df: pd.DataFrame, cfg: SeasonalConfig) -> None:
    st.divider()
    st.markdown("#### Rothwell index and counter-seasonal estimation")
    st.caption(
        "The Rothwell index prices whatever is on the shelf against base-year average prices, "
        "which is the only denominator a seasonal item has. Counter-seasonal estimation does "
        "the opposite: it keeps the item in the basket through its off season at a price moved "
        "with the items that are in season, rather than held flat.")
    c1, c2 = st.columns(2)

    if c1.button("Compute the Rothwell index", key="sn_rothwell"):
        try:
            result = sn.rothwell(df)
        except sn.SeasonalError as exc:
            st.error(str(exc))
        else:
            st.session_state["sn_rothwell_result"] = result
    result = st.session_state.get("sn_rothwell_result")
    if result is not None:
        st.caption(f"Base year {result.base_year}, {result.items_in_base} items with a "
                   "base-year average price. A movement here mixes price change with the "
                   "changing composition of the basket, which is inherent to the form.")
        st.line_chart(result.index)
        for note in result.notes:
            st.caption(note)

    if c2.button("Estimate off-season prices", key="sn_counter"):
        try:
            estimate = sn.counter_seasonal_estimate(df, cfg)
        except (sn.SeasonalError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state["sn_counter_result"] = estimate
    estimate = st.session_state.get("sn_counter_result")
    if estimate is not None:
        st.metric("Off-season prices estimated", f"{estimate.estimated:,}")
        st.caption("Every estimate is marked `counter_seasonal` and is a construction, not an "
                   "observation.")
        if len(estimate.coverage):
            st.dataframe(estimate.coverage.tail(18), use_container_width=True)


def _seasonal_multilateral(df: pd.DataFrame) -> None:
    st.divider()
    st.markdown("#### Year-over-year and rolling year, multilateral")
    st.caption(
        "The manual's answer to seasonal products is to stop comparing adjacent months. Each "
        "calendar month gets its own multilateral index over its own observations across the "
        "years, so seasonality never enters the comparison and there is nothing to adjust "
        "away. The rolling year is the geometric mean of the last twelve of those, which is "
        "smooth because its movement is real -- at the cost of sitting six months behind the "
        "month it is dated at.")
    from pricelab.engine import multilateral as ml

    m1, m2 = st.columns(2)
    method = m1.selectbox("Method", list(ml.METHODS),
                          format_func=lambda m: ml.METHOD_LABELS[m], key="sn_ml_method")
    window = m2.number_input("Window (years of the same month)", 2, 40, 25, key="sn_ml_window")
    if not st.button("Compute the seasonal forms", key="sn_ml_go"):
        return
    cfg = MultilateralConfig(enabled=True, method=str(method), window=int(window))
    try:
        result = ml.seasonal_multilateral(df, cfg)
    except ml.MultilateralError as exc:
        st.error(str(exc))
        return
    st.session_state["sn_seasonal_multilateral"] = result
    st.metric("Calendar periods compiled", f"{result.months_compiled}")
    frame = pd.DataFrame({"year_over_year": result.year_over_year,
                          "rolling_year": result.rolling_year})
    st.line_chart(frame)
    st.dataframe(frame.tail(18), use_container_width=True)
    for month, reason in result.skipped_months.items():
        st.caption(f"Calendar period {month}: {reason}")
    for warning in result.warnings:
        st.warning(warning)
