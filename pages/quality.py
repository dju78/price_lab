"""Quality: what was found, what was repaired, and every altered
observation. Moved from the original "Data quality" tab, unchanged."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pricelab import build_all_charts
from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv

from . import common


@require_role(Role.ADMINISTRATOR, Role.COMPILER)
def render() -> None:
    st.markdown("### Quality")
    analysis = common.get_active_analysis()
    if analysis is None:
        st.info("Compile a run on the Ingest page first.")
        return

    res = analysis["result"]
    charts = build_all_charts(res)
    clean = res["clean"]

    st.pyplot(charts["quality_bands"], use_container_width=True)
    st.caption("Faults separate into bands rather than forming a continuous tail. Genuine "
               "volatility does not cluster at exactly one hundred times the local level; "
               "a unit of measurement fault does. That shape is the diagnosis, and it is "
               "what makes the true value recoverable.")

    mech = res["quality"]["missing_mechanisms"]
    if len(mech):
        st.markdown("**Gap mechanisms**")
        show = mech.copy()
        show["calendar_months"] = show["calendar_months"].apply(
            lambda ms: ", ".join(pd.Timestamp(2000, mth, 1).strftime("%b") for mth in ms))
        show["first"] = pd.to_datetime(show["first"]).dt.strftime("%b %Y")
        show["last"] = pd.to_datetime(show["last"]).dt.strftime("%b %Y")
        st.dataframe(show, use_container_width=True, hide_index=True)

    st.markdown("**Every altered observation**")
    st.caption("Nothing is changed silently. Adjust the method on the Ingest page and the "
               "whole analysis updates.")
    altered = clean.loc[clean["flag"] != "none",
                        ["period", "category", "item_name", "price_reported",
                         "price_clean", "flag", "log10_deviation"]]
    st.dataframe(altered, use_container_width=True, hide_index=True, height=320)

    label = analysis.get("label", "analysis")
    if st.download_button("Download flagged observations", safe_csv(altered, index=False),
                          f"{label} flagged.csv", "text/csv"):
        common.record(audit.EXPORT, f"{label} flagged.csv", {"rows": len(altered)})
