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
