"""Decomposition: rates of change, contributions at every level of the
classification tree, core measures, base effects, and how broad a price
movement is.

Two sources of components. The compiled run's own categories, where the
run has expenditure weights -- and where it has none, the page says why
nothing additive can be computed rather than decomposing some other
aggregate under the headline's name. Or the published HICP, fetched from
Eurostat through the Phase 2 connector with its item weights, which is real
agency data with a real three-level tree and the usual source of the
question "what is driving inflation".
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit, db
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import decomposition as dc

from . import common

SOURCES = ("This session's compiled run", "Eurostat HICP (official, with item weights)")
HORIZONS = {"Period on period": "pop", "Year on year": "yoy"}


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Decomposition")
    st.caption(
        "How fast prices are rising, what is driving it, and how broad the movement is. "
        "Contributions reconcile to the headline at every level of the tree, and the residual "
        "is shown rather than assumed; every core measure states its parameters and the data "
        "it needs, and one the data cannot support says why.")

    source = st.radio("Components from", SOURCES, key="dc_source", horizontal=True)
    components = _official() if source == SOURCES[1] else _from_run()
    if components is None:
        return
    for note in components.notes:
        st.info(note)

    periods_per_year = 12
    _rates(components, periods_per_year)
    _base_effects(components, periods_per_year)
    if components.weights is None:
        st.warning("Contributions, core measures, diffusion by weight and dispersion need "
                   "expenditure weights, and these components have none. " +
                   " ".join(components.notes))
        return
    _contributions(components)
    _core(components, periods_per_year)
    _breadth(components)


# ---------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------
def _from_run() -> dc.Components | None:
    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker(
            "Nobody has compiled a run yet. Compile one on Ingest, load an approved run below, "
            "or decompose the published HICP instead.")
        return None
    res = analysis["result"]
    if "indices" not in res or "All items" not in res["indices"].columns:
        st.error("This run has no all-items index to decompose.")
        return None
    return dc.components_from_run(res)


def _official() -> dc.Components | None:
    from pricelab.data.connectors import base
    from pricelab.data.connectors.eurostat import (
        HICP_CODES,
        HICP_INDEX_DATASET,
        HICP_ROOT,
        HICP_WEIGHT_DATASET,
        EurostatConnector,
        hicp_key,
        hicp_tree,
    )

    from .sources import connector_cache

    c1, c2 = st.columns(2)
    geo = c1.text_input("Geography", "EA", key="dc_geo",
                        help="Eurostat geo code: EA for the euro area, or a country code (DE, FR, "
                             "IE, ...)")
    year = int(c2.number_input("Weight year", 2016, 2030, 2025, key="dc_year",
                               help="The HICP is compiled one year at a time: that year's item "
                                    "weights, applied to indices rebased to December of the "
                                    "year before."))
    if st.button("Fetch from Eurostat", type="primary", key="dc_fetch"):
        connector = EurostatConnector(cache=connector_cache())
        try:
            with db.session_scope() as s:
                indices = connector.fetch(
                    dataset=hicp_key(HICP_INDEX_DATASET, list(HICP_CODES), geo),
                    startPeriod=f"{year - 2}-12", endPeriod=f"{year}-12",
                    audit_session=s, actor=common.current_username())
                weights = connector.fetch(
                    dataset=hicp_key(HICP_WEIGHT_DATASET, list(HICP_CODES), geo, unit=None),
                    startPeriod=str(year - 2), endPeriod=str(year),
                    audit_session=s, actor=common.current_username())
        except base.ConnectorError as exc:
            st.error(f"Eurostat: {exc}")
            return None
        st.session_state["dc_official"] = {"indices": indices, "weights": weights,
                                           "geo": geo, "year": year}
        common.record(audit.DECOMPOSITION, f"HICP {geo} {year}", {
            "source": "eurostat", "index_hash": indices.vintage.response_hash,
            "weight_hash": weights.vintage.response_hash})

    held = st.session_state.get("dc_official")
    if held is None:
        st.info("Fetch the published HICP to decompose it.")
        return None
    for fetched in (held["indices"], held["weights"]):
        if fetched.stale:
            st.warning(fetched.message)
        v = fetched.vintage
        st.caption(f"Vintage: `{v.source}` `{v.query}`, retrieved {v.retrieved_at:%Y-%m-%d %H:%M} "
                   f"UTC, response hash `{v.response_hash[:16]}…`")
    try:
        tree = hicp_tree(held["indices"].data, held["weights"].data, int(held["year"]))
    except ValueError as exc:
        st.error(str(exc))
        return None
    published = held["indices"].data
    headline = (published[published["coicop"] == HICP_ROOT]
                .set_index("period")["value"].astype(float).sort_index())
    headline.index = pd.DatetimeIndex(headline.index)
    return dc.Components(
        indices=tree.leaf_indices, weights=tree.leaf_weights, parent_of=tree.parent_of,
        root=HICP_ROOT, headline=headline.rename(HICP_ROOT), panel=None,
        source=f"Eurostat HICP, {held['geo']}, {held['year']} weights",
        supplied_parent_weights=tree.supplied_parent_weights,
        notes=(f"Leaves rebased to {tree.base_period:%B %Y} = 100 and weighted with the "
               f"{tree.weight_year} item weights (per mille), which is how the HICP itself is "
               "compiled within a year.", *tree.notes))


# ---------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------
def _rates(components: dc.Components, periods_per_year: int) -> None:
    from pricelab.reporting.charts import rates_chart

    st.divider()
    st.markdown("#### Rates of change")
    table = dc.rates(components.headline, periods_per_year)
    last = table.dropna(subset=["period_on_period_pct"]).iloc[-1]
    k = st.columns(4)
    k[0].metric("Period on period", f"{last['period_on_period_pct']:+.2f}%")
    k[1].metric("Year on year", "n/a" if pd.isna(last["year_on_year_pct"])
                else f"{last['year_on_year_pct']:+.2f}%")
    k[2].metric("Three months on three, annualised",
                "n/a" if pd.isna(last["three_month_on_three_month_annualised_pct"])
                else f"{last['three_month_on_three_month_annualised_pct']:+.2f}%")
    k[3].metric("Cumulative", f"{last['cumulative_pct']:+.2f}%",
                help=f"since {table.index[0]:%b %Y}")
    st.caption("Each rate answers a different question; a figure quoted without saying which "
               "one invites comparison with another.")
    st.pyplot(rates_chart({dc.RATE_LABELS["year_on_year_pct"]: table["year_on_year_pct"],
                           dc.RATE_LABELS["three_month_on_three_month_annualised_pct"]:
                               table["three_month_on_three_month_annualised_pct"]},
                          f"{components.root}: rates of change"), use_container_width=True)
    st.dataframe(table.tail(13).round(3), use_container_width=True)


def _base_effects(components: dc.Components, periods_per_year: int) -> None:
    st.divider()
    st.markdown("#### Base effects")
    st.caption(
        "The year-on-year rate is split two ways, both exact. Carry-over is the rise already in "
        "the bag by December of last year; the impulse is this year's movement since, on the "
        "same base, and the two sum to the rate. The month's change in the rate is this "
        "month's movement minus the base effect of last year's same month dropping out.")
    try:
        effects = dc.base_effects(components.headline, periods_per_year)
    except dc.DecompositionError as exc:
        st.info(str(exc))
        return
    frame = effects.frame.dropna(subset=["year_on_year_pct"])
    if frame.empty:
        st.info("The headline has less than a year of history, so it has no year-on-year rate "
                "to decompose.")
        return
    identity = (frame["carry_over_pp"] + frame["impulse_pp"] - frame["year_on_year_pct"]).abs()
    st.metric("Largest gap between carry-over plus impulse and the rate", f"{identity.max():.1e} pp")
    st.dataframe(frame.tail(13).round(3), use_container_width=True)


def _contributions(components: dc.Components) -> None:
    from pricelab.reporting.charts import contributions_chart

    st.divider()
    st.markdown("#### Contributions to the change")
    periods = list(components.indices.index)
    c1, c2 = st.columns(2)
    start = c1.selectbox("From", periods, index=0, key="dc_start",
                         format_func=lambda p: f"{pd.Timestamp(p):%b %Y}")
    end = c2.selectbox("To", periods, index=len(periods) - 1, key="dc_end",
                       format_func=lambda p: f"{pd.Timestamp(p):%b %Y}")
    if pd.Timestamp(end) <= pd.Timestamp(start):
        st.error("The end period must come after the start period.")
        return
    weights = components.weights or {}
    try:
        result = dc.tree_contributions(components.indices, weights, components.parent_of,
                                       start, end,
                                       supplied_parent_weights=components.supplied_parent_weights)
    except dc.DecompositionError as exc:
        st.error(str(exc))
        return
    st.session_state["dc_contributions"] = result
    m = st.columns(3)
    m[0].metric(f"{result.root} change", f"{result.headline_change_pct:+.4f}%")
    m[1].metric("Sum of top-level contributions",
                f"{result.children(result.root)['contribution_to_headline_pp'].sum():+.4f} pp")
    m[2].metric("Largest reconciliation gap, any level", f"{result.residual_pp:.1e} pp",
                help=f"Reconciles to {dc.RECONCILIATION_DECIMALS} decimal places: "
                     f"{'yes' if result.reconciles else 'NO'}")
    for problem in result.problems:
        st.caption(problem)

    depth = st.selectbox("Level", sorted(result.table["depth"].unique()), index=1
                         if result.table["depth"].max() >= 1 else 0, key="dc_depth")
    shown = result.table[result.table["depth"] == depth].sort_values(
        "contribution_to_headline_pp", ascending=False)
    st.dataframe(shown.round(6), use_container_width=True)
    st.download_button(
        "Download every level (CSV)",
        safe_csv_with_notice(result.table.reset_index(),
                             f"Contributions to the {result.root} change from "
                             f"{result.start:%b %Y} to {result.end:%b %Y}, percentage points; "
                             f"reconciliation gap {result.residual_pp:.1e} pp"),
        file_name="contributions.csv", mime="text/csv", key="dc_contrib_csv")

    top = [n for n in result.children(result.root).index]
    if result.indices is not None and result.weights is not None and top:
        over_time = dc.contributions_over_time(
            result.indices[top], {n: float(result.weights[n]) for n in top})
        st.pyplot(contributions_chart(over_time, f"Contributions to the period-on-period "
                                                 f"change in {result.root}"),
                  use_container_width=True)


def _core(components: dc.Components, periods_per_year: int) -> None:
    from pricelab.reporting.charts import rates_chart

    st.divider()
    st.markdown("#### Core and underlying measures")
    c1, c2, c3 = st.columns(3)
    horizon_name = c1.selectbox("Horizon", list(HORIZONS), key="dc_horizon")
    horizon = 1 if HORIZONS[horizon_name] == "pop" else periods_per_year
    trim = c2.slider("Trim from each tail, % of the basket", 0.0, 49.0, 15.0, 1.0,
                     key="dc_trim")
    window = int(c3.number_input("Volatility window, periods", 6, 60, 24, key="dc_window"))
    nodes = sorted(set(components.indices.columns)
                   | {p for p in components.parent_of.values() if p is not None}
                   - {components.root})
    exclude = st.multiselect("Exclude from the exclusion-based measure", nodes, key="dc_exclude",
                             help="A parent excludes everything beneath it.")
    threshold = st.number_input("Sticky if prices last longer than (months)", 1.0, 24.0, 4.3,
                                0.1, key="dc_sticky")

    availability = dc.core_measure_availability(
        components.indices, components.weights, horizon=horizon, window=window,
        panel=components.panel, exclude=exclude)
    for key, reason in availability.items():
        line = f"**{dc.CORE_LABELS[key]}** needs {dc.CORE_REQUIREMENTS[key]}."
        st.markdown(line + (f" Not available here: {reason}." if reason else " Available."))

    if not st.button("Compute core measures", key="dc_core_go"):
        return
    computed: dict[str, dc.CoreMeasure] = {}
    builders: dict[str, Any] = {
        "exclusion": lambda: dc.exclusion_measure(components.indices, components.weights, exclude,
                                                  horizon=horizon,
                                                  parent_of=components.parent_of),
        "trimmed_mean": lambda: dc.trimmed_mean(components.indices, components.weights,
                                                trim_pct=trim, horizon=horizon),
        "weighted_median": lambda: dc.weighted_median(components.indices, components.weights,
                                                      horizon=horizon),
        "variance_weighted": lambda: dc.variance_weighted(components.indices, components.weights,
                                                          window=window, horizon=horizon),
        "sticky_price": lambda: dc.sticky_price_measure(components.indices, components.weights,
                                                        components.panel,
                                                        threshold_months=threshold,
                                                        horizon=horizon),
    }
    for key, reason in availability.items():
        if reason is None:
            try:
                computed[key] = builders[key]()
            except dc.DecompositionError as exc:
                st.warning(f"{dc.CORE_LABELS[key]}: {exc}")
    st.session_state["dc_core"] = computed
    common.record(audit.DECOMPOSITION, components.source, {
        "core_measures": {k: dict(m.parameters) for k, m in computed.items()}})
    if not computed:
        st.info("No core measure could be computed on these components.")
        return
    from pricelab.engine.aggregation import weighted_aggregate

    headline = weighted_aggregate(components.indices, components.weights or {})
    series = {"All components (weighted)": (headline / headline.shift(horizon) - 1) * 100}
    series.update({m.label: m.series for m in computed.values()})
    st.pyplot(rates_chart(series, f"Core measures, {horizon_name.lower()}"),
              use_container_width=True)
    table = pd.DataFrame({m.label: m.series for m in computed.values()})
    st.dataframe(table.tail(13).round(3), use_container_width=True)
    for measure in computed.values():
        for note in measure.notes:
            st.caption(f"{measure.name}: {note}")


def _breadth(components: dc.Components) -> None:
    st.divider()
    st.markdown("#### How broad is it")
    threshold = st.number_input("Share of the basket rising faster than (% a period)", -5.0, 5.0,
                                0.2, 0.05, key="dc_threshold")
    spread = dc.diffusion(components.indices, components.weights, threshold_pct=threshold,
                          tolerance_pct=0.0)
    dispersion = dc.dispersion(components.indices, components.weights)
    last = spread.dropna(how="all").iloc[-1]
    k = st.columns(4)
    k[0].metric("Components rising", f"{last['share_rising_pct']:.0f}%")
    k[1].metric("Diffusion index", f"{last['diffusion_index']:.1f}",
                help="Share rising plus half the share unchanged: 50 is balanced.")
    if "weighted_share_above_threshold_pct" in last:
        k[2].metric(f"Basket rising faster than {threshold:g}%",
                    f"{last['weighted_share_above_threshold_pct']:.1f}%")
    d_last = dispersion.dropna(how="all").iloc[-1]
    k[3].metric("Relative price dispersion", f"{d_last['dispersion_pp']:.2f} pp",
                help=f"Skewness {d_last['skewness']:+.2f}")
    st.dataframe(spread.join(dispersion).tail(13).round(3), use_container_width=True)
