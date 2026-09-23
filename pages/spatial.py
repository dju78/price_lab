"""Spatial comparison: purchasing power parities between regions by the
country product dummy or Geary-Khamis method, the matched-product count
for every pair of regions, and conversion at the published parities.

A region whose products are too thinly shared with the rest of the
comparison is shown with its estimate and the reason, and withheld from the
published parities and from any conversion.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import spatial as sp

from . import common


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Spatial comparison")
    st.caption(
        "Price levels across regions rather than across time. Upload one price per region and "
        "product (CSV: region, product, price, and quantity for Geary-Khamis or expenditure for "
        "weighted CPD). A parity resting on a handful of shared products is not a "
        "measurement, so the matched-product count for every pair is shown, and a region "
        "below the threshold is reported but not published.")
    upload = st.file_uploader("Regional prices (CSV)", type=["csv"], key="sp_file")
    if upload is None:
        return
    try:
        prices = common.read_upload_table(upload, ("region", "product", "price"))
    except ValueError as exc:
        st.error(str(exc))
        return
    regions = sorted(prices["region"].astype(str).unique())
    c1, c2, c3 = st.columns(3)
    method = c1.selectbox("Method", list(sp.METHODS), format_func=lambda m: sp.METHODS[m],
                          key="sp_method")
    base = c2.selectbox("Base region", regions, key="sp_base")
    threshold = int(c3.number_input("Products a region must share to be published", 1, 500,
                                    sp.DEFAULT_MIN_OVERLAP, key="sp_min"))
    weighted = (method == "cpd" and "expenditure" in prices.columns
                and st.checkbox("Weight by expenditure shares", value=True, key="sp_weighted"))
    if not st.button("Compare", type="primary", key="sp_go"):
        return
    try:
        if method == "cpd":
            result = sp.cpd(prices, base=base, weighted=bool(weighted), min_overlap=threshold)
        else:
            result = sp.geary_khamis(prices, base=base, min_overlap=threshold)
    except sp.SpatialError as exc:
        st.error(str(exc))
        return
    st.session_state["sp_result"] = result
    common.record(audit.SPATIAL_COMPARISON, upload.name, {
        "method": method, "base": base, "min_overlap": threshold,
        "withheld": sorted(result.withheld)})

    st.success(result.label + ".")
    common.show_uncertainty(
        "The parities", run_headline=False,
        reason="no design-based interval is estimated; the country product dummy's standard "
               "errors, where it gives them, are in the table below")
    for region, reason in result.withheld.items():
        st.warning(f"{region}: {reason}.")
    st.dataframe(result.table, use_container_width=True)
    st.markdown("##### Matched products for every pair of regions")
    st.dataframe(result.overlap, use_container_width=True)
    if len(result.thin_pairs):
        st.caption(f"{len(result.thin_pairs)} pair(s) share fewer than {threshold} products.")
        st.dataframe(result.thin_pairs, use_container_width=True, hide_index=True)
    st.download_button("Download the parities (CSV)",
                       safe_csv_with_notice(result.table.reset_index(), result.label),
                       file_name="parities.csv", mime="text/csv", key="sp_csv")

    st.divider()
    st.markdown("##### Convert values at the published parities")
    values_upload = st.file_uploader("Values in local currency (CSV: region, value)",
                                     type=["csv"], key="sp_values")
    if values_upload is None:
        return
    try:
        values = common.read_upload_table(values_upload, ("region", "value"))
    except ValueError as exc:
        st.error(str(exc))
        return
    converted = sp.convert(pd.Series(values["value"].to_numpy(dtype=float),
                                     index=values["region"].astype(str)), result)
    st.session_state["sp_converted"] = converted
    st.dataframe(converted, use_container_width=True)
