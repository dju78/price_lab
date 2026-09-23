"""Uncertainty: the sampling uncertainty of the headline's movement, and,
separately, its sensitivity to choices of method.

Two sections, two charts, two labels, and no control that combines them.
Sampling uncertainty asks how much the number would move had a different
sample been drawn; methodological sensitivity asks how much it would move
had a different defensible choice been made on the same data. A reader who
adds them understates one and overstates the other.

An interval needs a declared sampling design: which column is the cluster
(the outlet) and which the stratum. With none declared -- the state of any
ordinary upload -- the page refuses, and says why, rather than assume that
price quotes were a simple random sample.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role
from pricelab.engine import uncertainty as un

from . import common

NO_DESIGN = "(no design information)"
NO_STRATA = "(a single stratum)"


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Uncertainty")
    st.caption("Two different questions, answered separately and never combined: how much the "
               "headline would move under a different sample (a confidence interval), and how "
               "much it would move under a different defensible choice of method (a "
               "sensitivity range).")
    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker("Compile a run on Ingest, or load an approved run.")
        return
    res = analysis["result"]
    if "imputed" not in res:
        st.error("This run's data failed validation and has nothing to analyse.")
        return
    _sampling(res, str(analysis.get("label", "")))
    st.divider()
    _sensitivity(res, str(analysis.get("label", "")))


def _sampling(res: dict, label: str) -> None:
    from pricelab.engine.index import category_weights
    from pricelab.reporting.charts import interval_chart

    st.markdown("#### Sampling uncertainty (a confidence interval)")
    panel: pd.DataFrame = res["imputed"]
    columns = [c for c in panel.columns if c not in ("period", "price_imputed", "price_clean",
                                                     "price_reported")]
    c1, c2 = st.columns(2)
    cluster = c1.selectbox("Primary sampling unit (the cluster, usually the outlet)",
                           [NO_DESIGN, *columns], key="un_cluster")
    strata = c2.selectbox("Strata the clusters were sampled within", [NO_STRATA, *columns],
                          key="un_strata")
    if cluster == NO_DESIGN:
        st.warning(un.NOT_QUANTIFIED + " If you know how the sample was drawn, name the column "
                   "that identifies the outlet (or other primary sampling unit) above; the "
                   "interval will rest on that declaration and say so.")
        st.session_state.pop(common.UNCERTAINTY_STATE, None)
        return
    periods = sorted(pd.DatetimeIndex(panel["period"]).unique())
    p1, p2, p3 = st.columns(3)
    default_start = periods[-13] if len(periods) > 12 else periods[0]
    start = p1.selectbox("From", periods, index=periods.index(default_start), key="un_start",
                         format_func=lambda p: f"{pd.Timestamp(p):%b %Y}")
    end = p2.selectbox("To", periods, index=len(periods) - 1, key="un_end",
                       format_func=lambda p: f"{pd.Timestamp(p):%b %Y}")
    level = p3.selectbox("Confidence level", [0.90, 0.95, 0.99], index=1, key="un_level")
    if not st.button("Estimate the interval", type="primary", key="un_go"):
        return
    design = un.SamplingDesign(cluster=cluster, strata=None if strata == NO_STRATA else strata,
                               declared_by=f"{common.current_username()} on the Uncertainty page")
    try:
        relatives = un.matched_relatives(
            panel, pd.Timestamp(start), pd.Timestamp(end),
            extra=tuple(c for c in (design.cluster, design.strata) if c))
        comparison = un.compare_with_naive(
            relatives, design, replicates=999, level=float(level), seed=0,
            weights=category_weights(panel), start=pd.Timestamp(start), end=pd.Timestamp(end))
    except un.UncertaintyError as exc:
        st.error(str(exc))
        return
    result = comparison.design_based
    st.session_state[common.UNCERTAINTY_STATE] = {"label": label, "result": result}
    common.record(audit.UNCERTAINTY, label, {
        "design": design.statement, "level": level, "lower": result.lower_pct,
        "upper": result.upper_pct, "naive_width": comparison.naive.width_pp})
    st.success(result.label)
    st.pyplot(interval_chart(result), use_container_width=True)
    st.info(comparison.statement)
    st.dataframe(result.clusters_per_stratum.rename("clusters").to_frame(),
                 use_container_width=True)


def _sensitivity(res: dict, label: str) -> None:
    from pricelab.engine import sensitivity as se
    from pricelab.reporting.charts import sensitivity_chart

    st.markdown("#### Methodological sensitivity (not a confidence interval)")
    st.caption("The headline recomputed under each defensible alternative choice, one choice "
               "at a time. Choices the data cannot support are listed with the reason.")
    df = st.session_state.get("input_df")
    cfg = st.session_state.get("run_config") or res.get("config")
    if df is None or cfg is None:
        st.info("The sensitivity range recompiles the collection, so it needs the collection "
                "compiled in this session on Ingest.")
        return
    if not st.button("Recompute under the alternatives", key="un_sens_go"):
        held = st.session_state.get(common.SENSITIVITY_STATE)
        if held is None or held.get("label") != label:
            return
        result = held["result"]
    else:
        with st.spinner("Recompiling the headline under each alternative…"):
            try:
                result = se.sensitivity(df, cfg, baseline=res)
            except ValueError as exc:
                st.error(str(exc))
                return
        st.session_state[common.SENSITIVITY_STATE] = {"label": label, "result": result}
        common.record(audit.SENSITIVITY, label, {
            "low": list(result.low), "high": list(result.high),
            "alternatives": int(len(result.computed))})
    st.success(result.label)
    st.pyplot(sensitivity_chart(result), use_container_width=True)
    st.dataframe(result.table.round(3), use_container_width=True, hide_index=True)
