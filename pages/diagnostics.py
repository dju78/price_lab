"""Diagnostics: the raw method-sensitivity, chain-drift, churn and
seasonality tables the original app only ever folded into Findings text
when they crossed a materiality threshold. Every table here comes from
`engine.diagnostics`, called directly on the already-compiled run; no new
diagnostic was written for this page."""

from __future__ import annotations

import streamlit as st

from pricelab import diagnostics as dg
from pricelab.core.models import Role
from pricelab.core.security import require_role

from . import common


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Diagnostics")
    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker(
            "Nobody has compiled a run yet. A compiler can do that on Ingest, "
            "or load a previously approved run below once one exists.")
        return

    res = analysis["result"]
    if "indices" not in res:
        st.error("This run's data failed validation and has nothing to diagnose.")
        return

    imputed, I, cfg = res["imputed"], res["indices"], res["config"]

    st.markdown("**Formula sensitivity**")
    st.caption("The final index level under each elementary formula, on the same cleaned "
               "data: a quantified consequence of the formula choice, not just a preference.")
    st.dataframe(dg.method_sensitivity(imputed, cfg.index), use_container_width=True)

    st.markdown("**Chain drift**")
    st.caption("The chained level against the direct fixed-base level over the same span, "
               "both rebased to the first period. A large gap in a seasonal series is the "
               "classic warning that chaining is accumulating drift rather than measuring "
               f"price change. Flagged above {cfg.index.chain_drift_threshold_pp:g} index "
               "points (engine/splicing.py).")
    drift = dg.chain_drift(imputed, cfg.index)
    flagged = drift[drift["exceeds_threshold"]]
    if len(flagged):
        st.warning(f"{len(flagged)} series exceed the drift threshold: "
                   + ", ".join(str(i) for i in flagged.index[:8]))
    st.dataframe(drift, use_container_width=True)

    st.markdown("**Matched vs. naive comparison**")
    st.caption("The matched index against a naive average of prices. The gap is the effect "
               "of item churn.")
    st.dataframe(dg.unmatched_comparison(imputed, I), use_container_width=True)

    seas = dg.seasonality(I)
    if len(seas):
        st.markdown("**Seasonal amplitude**")
        st.caption("Amplitude of the within-year cycle, measured on the index.")
        st.dataframe(seas, use_container_width=True)

    summary, life = dg.churn(imputed)
    st.markdown("**Item churn**")
    st.caption("Item lifespans and the level at which replacements enter relative to those "
               "already in the sample.")
    st.dataframe(summary, use_container_width=True)
    with st.expander("Every item's lifespan"):
        st.dataframe(life, use_container_width=True, hide_index=True)
