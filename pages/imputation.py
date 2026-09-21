"""Imputation: the method applied per category, and every observation it
filled. The original single-page app.py had no dedicated imputation view --
this reuses the `imputation` column `engine.imputation.run_imputation`
already adds to the pipeline's `imputed` frame, just surfaced on its own
page instead of folded only into a download."""

from __future__ import annotations

import streamlit as st

from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv
from pricelab.engine.imputation import imputation_summary, response_rates

from . import common


@require_role(Role.ADMINISTRATOR, Role.COMPILER)
def render() -> None:
    st.markdown("### Imputation")
    analysis = common.get_active_analysis()
    if analysis is None:
        st.info("Compile a run on the Ingest page first.")
        return

    res = analysis["result"]
    cfg = res["config"]
    imputed = res["imputed"]

    st.markdown("**Method by category**")
    st.caption("none | carry_forward | class_mean | seasonal_hold. Chosen from the "
               "diagnosis on Ingest; each method biases the index in a known direction, "
               "so the choice is stated rather than defaulted invisibly.")
    by_cat = dict(cfg.imputation.by_category)
    categories = sorted(imputed["category"].unique())
    st.dataframe(
        {"category": categories,
         "method": [by_cat.get(c, cfg.imputation.default_method) for c in categories]},
        hide_index=True, use_container_width=True)

    st.markdown("**Response rates**")
    st.caption("Observed, imputed and still-missing counts per category and period, with "
               "the share of each cell that is model output rather than collection. An "
               "index resting on three observed prices and seven imputed ones is a weaker "
               "claim than one resting on ten observed, and nothing in the level says so.")
    rates = response_rates(imputed).reset_index()
    rates[["response_rate", "imputed_share"]] = rates[["response_rate", "imputed_share"]].round(3)
    st.dataframe(rates, use_container_width=True, hide_index=True, height=280)
    summary = imputation_summary(imputed)
    if len(summary):
        st.markdown("**Values filled, by method**")
        st.dataframe(summary.reset_index().round(4), use_container_width=True, hide_index=True)

    filled = imputed.loc[imputed["imputation"] != "",
                         ["period", "category", "item_id", "item_name", "price_clean",
                          "price_imputed", "imputation"]]
    st.markdown("**Every imputed observation**")
    if len(filled):
        st.caption(f"{len(filled):,} of {len(imputed):,} observations "
                   f"({len(filled) / len(imputed):.1%}) were filled rather than collected.")
        st.dataframe(filled, use_container_width=True, hide_index=True, height=320)
        label = analysis.get("label", "analysis")
        if st.download_button("Download imputed observations", safe_csv(filled, index=False),
                              f"{label} imputed.csv", "text/csv"):
            common.record(audit.EXPORT, f"{label} imputed.csv", {"rows": len(filled)})
    else:
        st.caption("No gap in this collection needed imputation.")
