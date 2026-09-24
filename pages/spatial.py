"""Spatial comparison: purchasing power parities between regions by the
country product dummy or Geary-Khamis method, the matched-product count
for every pair of regions, and conversion at the published parities.

A region whose products are too thinly shared with the rest of the
comparison is shown with its estimate and the reason, and withheld from the
published parities and from any conversion.

The prices come from an upload, or from Eurostat's published PPPs by
analytical category (`prc_ppp_ind`) through the Eurostat connector: each
country's most detailed categories stand in for products, their expenditure
for weights, and the result is set beside Eurostat's own aggregate parity
with the share of consumption the categories cover. With market exchange
rates, the parities become price level indices.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pricelab.core import audit, db
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.data.connectors.eurostat import PPP_DATASET
from pricelab.engine import spatial as sp

from . import common

UPLOAD = "Upload regional prices"
EUROSTAT = "Eurostat's published PPPs by category (prc_ppp_ind)"
PPP_KEY = f"{PPP_DATASET}/A.PPP_EU27_2020+EXP_NAC+PLI_EU27_2020.."


def _eurostat() -> tuple[pd.DataFrame, pd.Series, pd.Series, str] | None:
    """Fetch a year of Eurostat's PPPs by category through the connector
    (cached, audited) and turn them into a comparison's inputs."""
    from pricelab.data.connectors.eurostat import EurostatConnector, ppp_comparison_inputs

    year = int(st.number_input("Year", 2000, 2100, 2023, key="sp_year"))
    held = st.session_state.get("sp_eurostat")
    if st.button("Fetch from Eurostat", key="sp_fetch"):
        try:
            with db.session_scope() as s:
                fetched = EurostatConnector().fetch(
                    dataset=PPP_KEY, startPeriod=str(year), endPeriod=str(year),
                    audit_session=s, actor=common.current_username())
            prices, published, coverage = ppp_comparison_inputs(fetched.data, year=year)
        except Exception as exc:        # noqa: BLE001 - any failure is shown with its reason
            st.error(f"Eurostat's PPPs for {year} could not be used: {exc}")
            return None
        held = {"year": year, "inputs": (prices, published, coverage)}
        st.session_state["sp_eurostat"] = held
    if held is None or held["year"] != year:
        st.caption("Fetch a year to compare. The source, its retrieval time and any failure "
                   "are recorded in the audit log by the connector.")
        return None
    prices, published, coverage = held["inputs"]
    st.caption(f"{prices['region'].nunique()} regions, {prices['product'].nunique()} categories "
               f"from Eurostat prc_ppp_ind, {year}.")
    return prices, published, coverage, f"eurostat prc_ppp_ind {year}"


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Spatial comparison")
    st.caption(
        "Price levels across regions rather than across time. Upload one price per region and "
        "product (CSV: region, product, price, and quantity for Geary-Khamis or expenditure for "
        "weighted CPD). A parity resting on a handful of shared products is not a "
        "measurement, so the matched-product count for every pair is shown, and a region "
        "below the threshold is reported but not published.")
    source = st.radio("Prices", [UPLOAD, EUROSTAT], key="sp_source", horizontal=True)
    published: pd.Series | None = None
    coverage: pd.Series | None = None
    if source == UPLOAD:
        upload = st.file_uploader("Regional prices (CSV)", type=["csv"], key="sp_file")
        if upload is None:
            return
        try:
            prices = common.read_upload_table(upload, ("region", "product", "price"))
        except ValueError as exc:
            st.error(str(exc))
            return
        source_name = str(upload.name)
    else:
        fetched = _eurostat()
        if fetched is None:
            return
        prices, published, coverage, source_name = fetched
    regions = sorted(prices["region"].astype(str).unique())
    c1, c2, c3 = st.columns(3)
    method = c1.selectbox("Method", list(sp.METHODS), format_func=lambda m: sp.METHODS[m],
                          key="sp_method")
    base = c2.selectbox("Base region", regions, key="sp_base",
                        index=regions.index("EU27_2020") if "EU27_2020" in regions else 0)
    threshold = int(c3.number_input("Products a region must share to be published", 1, 500,
                                    sp.DEFAULT_MIN_OVERLAP, key="sp_min"))
    weighted = (method == "cpd" and "expenditure" in prices.columns
                and st.checkbox("Weight by expenditure shares", value=True, key="sp_weighted"))
    if not st.button("Compare", type="primary", key="sp_go"):
        return
    if weighted and prices["expenditure"].isna().any():
        dropped = int(prices["expenditure"].isna().sum())
        prices = prices.dropna(subset=["expenditure"])
        st.caption(f"{dropped} price(s) with no expenditure weight left out of the weighted "
                   "comparison.")
    try:
        if method == "cpd":
            result = sp.cpd(prices, base=base, weighted=bool(weighted), min_overlap=threshold)
        else:
            result = sp.geary_khamis(prices, base=base, min_overlap=threshold)
    except sp.SpatialError as exc:
        st.error(str(exc))
        return
    st.session_state["sp_result"] = result
    common.record(audit.SPATIAL_COMPARISON, source_name, {
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
    if published is not None and coverage is not None:
        st.markdown("##### Beside Eurostat's own aggregate parity")
        beside = pd.DataFrame({"estimated here": result.ppp, str(published.name): published,
                               "coverage of consumption": coverage}).dropna(
                                   subset=["estimated here"])
        beside["gap, %"] = (beside["estimated here"] / beside[str(published.name)] - 1) * 100
        st.caption("The categories with published expenditure cover only part of each "
                   "country's consumption (the coverage column), leaving out most non-traded "
                   "services, so the gap is expected and systematic, not rounding.")
        st.dataframe(beside.round(4), use_container_width=True)

    st.markdown("##### Price level indices")
    rates_upload = st.file_uploader(
        "Market exchange rates, local currency per unit of the base region's currency "
        "(CSV: region, value)", type=["csv"], key="sp_rates")
    if rates_upload is not None:
        try:
            rates = common.read_upload_table(rates_upload, ("region", "value"))
            pli = sp.price_level_indices(result, pd.Series(
                rates["value"].to_numpy(dtype=float), index=rates["region"].astype(str)))
        except (ValueError, sp.SpatialError) as exc:
            st.error(str(exc))
        else:
            st.session_state["sp_pli"] = pli
            st.caption(f"PPP over the exchange rate, times 100: above 100 a region is dearer "
                       f"than {base}, below 100 cheaper.")
            st.dataframe(pli.round(2), use_container_width=True)

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
