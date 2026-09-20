"""Index build: the compiled series, the annualised rate, and the matched
item count behind each comparison. Moved from the original "Index" tab,
unchanged."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pricelab import annualised_rate, build_all_charts, years_span
from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv

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
    index_ref = idx_cfg.index_reference_period or idx_cfg.base_period or first_period_label
    weight_ref = idx_cfg.weight_reference_period or "not set (no formula run here uses one yet)"
    with st.expander("Reference periods", expanded=False):
        st.caption("A price index has three of these, easy to conflate and expensive to get "
                   "wrong. This run's:")
        st.markdown(
            f"- **Price reference period** — the denominator of every price relative: "
            f"**{price_ref}**. Only affects a fixed-base (non-chained) comparison.\n"
            f"- **Weight reference period** — where the expenditure weights or quantities "
            f"are drawn from: **{weight_ref}**.\n"
            f"- **Index reference period** — the period this series is rebased to read "
            f"{idx_cfg.base_value:g}: **{index_ref}**. A presentational choice, not a "
            "recomputation. Only affects a chained comparison.")

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
    if st.download_button("Download index series", safe_csv(I.round(3)),
                          f"{label} indices.csv", "text/csv"):
        common.record(audit.EXPORT, f"{label} indices.csv", {"periods": len(I)})
