"""Index build: the compiled series, the annualised rate, and the matched
item count behind each comparison. Moved from the original "Index" tab,
unchanged."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pricelab import (
    annualised_rate,
    build_all_charts,
    resolve_index_reference_period,
    years_span,
)
from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine.custom import non_standard_notice

from . import common


@require_role(Role.ADMINISTRATOR, Role.COMPILER)
def render() -> None:
    st.markdown("### Index build")
    analysis = common.get_active_analysis()
    if analysis is None:
        st.info("Compile a run on the Ingest page first.")
        return

    res = analysis["result"]
    charts = build_all_charts(res)
    I = res["indices"]
    years = years_span(I.index)

    idx_cfg = res["config"].index
    first_period_label = f"{I.index[0]:%b %Y} (the first period present, the engine's default)"
    price_ref = idx_cfg.price_reference_period or idx_cfg.base_period or first_period_label
    # Resolved by the engine rather than re-derived here, so this panel
    # reports the period the series was actually rebased to -- including
    # the price-reference fallback, which applies when only that is set.
    resolved_index_ref = resolve_index_reference_period(idx_cfg, I.index)
    index_ref = (f"{resolved_index_ref:%b %Y}"
                 if (idx_cfg.index_reference_period or idx_cfg.price_reference_period
                     or idx_cfg.base_period) else first_period_label)
    weight_ref = idx_cfg.weight_reference_period or "not set"
    with st.expander("Reference periods", expanded=False):
        st.caption("A price index has three of these, easy to conflate and expensive to get "
                   "wrong. This run's:")
        st.markdown(
            f"- **Price reference period** — the denominator of every price relative: "
            f"**{price_ref}**. Only affects a fixed-base (non-chained) comparison.\n"
            f"- **Weight reference period** — where the expenditure weights or quantities "
            f"are drawn from: **{weight_ref}**. Read by the Lowe and Young formulae.\n"
            f"- **Index reference period** — the period this series is rebased to read "
            f"{idx_cfg.base_value:g}: **{index_ref}**. A presentational rescaling, not a "
            "recomputation, applied to a chained and a fixed-base series alike. Defaults "
            "to the price reference period when unset.")

    st.pyplot(charts["index"], use_container_width=True)
    c1, c2 = st.columns([3, 2])
    with c1:
        if "inflation" in charts:
            st.pyplot(charts["inflation"], use_container_width=True)
    with c2:
        st.markdown("**Level and annualised rate**")
        st.dataframe(pd.DataFrame({
            "Final level": I.iloc[-1].round(1),
            "Annualised %": annualised_rate(I.iloc[-1] / I.iloc[0], years).round(2),
        }).sort_values("Final level", ascending=False), use_container_width=True)

    st.markdown("**Matched items behind each comparison**")
    st.caption("An index built on two matched items is a weaker statistic than one built "
               "on six, and that difference is invisible in the published series.")
    st.pyplot(charts["coverage"], use_container_width=True)

    label = analysis.get("label", "analysis")
    notice = non_standard_notice(res["config"].index)
    if notice:
        st.warning(notice)
    if st.download_button("Download index series",
                          safe_csv_with_notice(I.round(3), notice),
                          f"{label} indices.csv", "text/csv"):
        common.record(audit.EXPORT, f"{label} indices.csv", {"periods": len(I)})
