"""Official sources: fetch a published series from a statistical agency's
API through the Phase 2 connectors, see where it came from, and set it
beside the compiled index.

Every fetch goes through `BaseConnector.fetch`: retry with backoff, a
response cache with a lifetime, schema validation before parsing, a
vintage stamp (source, query, retrieval time, response hash), and an
audit event whether it succeeded, was served from cache, or failed. When
the agency is unavailable the last successful response is shown with its
age and the reason, labelled as such, rather than nothing -- which is the
degradation Phase 10 built and this page is where a person sees it.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit, db
from pricelab.core.cache import BoundedCache
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv
from pricelab.data.connectors import base
from pricelab.data.connectors.bls import BLSConnector
from pricelab.data.connectors.eurostat import EurostatConnector
from pricelab.data.connectors.fao import FAOConnector
from pricelab.data.connectors.generic_sdmx import GenericSDMXConnector
from pricelab.data.connectors.imf import IMFConnector
from pricelab.data.connectors.oecd import OECDConnector
from pricelab.data.connectors.ons import ONSConnector
from pricelab.data.connectors.worldbank import WorldBankConnector

from . import common

#: Connector, the keyword arguments its `fetch` takes (label, default,
#: help), and an example a first-time user can run.
SOURCES: dict[str, dict[str, Any]] = {
    "ONS (UK)": {
        "cls": ONSConnector,
        "fields": [("series_uri", "Series URI", "/economy/inflationandpriceindices/timeseries/d7bt/mm23",
                    "The ONS time series path, from the series page's URL")]},
    "Eurostat": {
        "cls": EurostatConnector,
        "fields": [("dataset", "Dataset", "prc_hicp_midx", "Eurostat dataset code"),
                   ("geo", "geo", "EA", "Geography filter"),
                   ("coicop", "coicop", "CP00", "COICOP filter"),
                   ("unit", "unit", "I15", "Unit filter")]},
    "IMF": {
        "cls": IMFConnector,
        "fields": [("indicator", "Indicator", "PCPI_IX", "IMF indicator code"),
                   ("country", "Country", "GB", "ISO country code")]},
    "World Bank": {
        "cls": WorldBankConnector,
        "fields": [("indicator", "Indicator", "FP.CPI.TOTL", "World Bank indicator code"),
                   ("country", "Country", "GB", "ISO country code")]},
    "OECD": {
        "cls": OECDConnector,
        "fields": [("dataset", "Dataset", "PRICES_CPI", "OECD dataset id"),
                   ("filter_expr", "Filter", "GBR.CPALTT01.IXOB.M", "SDMX key filter")]},
    "BLS (US)": {
        "cls": BLSConnector,
        "fields": [("series_id", "Series id", "CUUR0000SA0", "BLS series id (API key from "
                    "PRICELAB_BLS_API_KEY when set)")]},
    "FAO food price index": {"cls": FAOConnector, "fields": []},
    "Generic SDMX 2.1": {
        "cls": None,
        "fields": [("base_url", "Base URL", "https://data-api.ecb.europa.eu/service/data",
                    "The agency's SDMX 2.1 REST data endpoint (JSON-stat)"),
                   ("dataset", "Dataflow", "EXR", "Dataflow identifier"),
                   ("FREQ", "FREQ filter", "M", "Any further key=value filters go on the query")]},
}

_cache: BoundedCache[base.ConnectorResult] | None = None


def connector_cache() -> BoundedCache[base.ConnectorResult]:
    """One process-wide cache for every connector, so the last good
    response of a series survives a page rerun and a later outage."""
    global _cache
    if _cache is None:
        _cache = BoundedCache(max_entries=64)
    return _cache


def make_connector(source: str, params: dict[str, str]) -> base.BaseConnector:
    spec = SOURCES[source]
    if spec["cls"] is None:
        return GenericSDMXConnector(name="generic_sdmx", base_url=params.pop("base_url"),
                                    cache=connector_cache())
    connector: base.BaseConnector = spec["cls"](cache=connector_cache())
    return connector


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Official sources")
    st.caption("Fetch a published series from a statistical agency's API. Every fetch is "
               "retried, cached, validated against the source's schema, stamped with a "
               "vintage and audited; if the agency is down, the last successful response is "
               "shown with its age.")

    source = st.selectbox("Source", list(SOURCES))
    spec = SOURCES[source]
    params: dict[str, str] = {}
    for key, label, default, help_text in spec["fields"]:
        params[key] = st.text_input(label, value=default, help=help_text, key=f"src_{source}_{key}")
    allow_stale = st.checkbox("If the agency is unavailable, show the last successful response",
                              value=True)

    if st.button("Fetch", type="primary"):
        connector = make_connector(source, dict(params))
        fetch_params = {k: v for k, v in params.items() if k != "base_url"}
        try:
            with db.session_scope() as s:
                result = connector.fetch(audit_session=s, actor=common.current_username(),
                                         allow_stale=allow_stale, **fetch_params)
        except base.ConnectorError as exc:
            st.error(f"{source}: {exc}")
            st.session_state.pop("source_result", None)
            return
        st.session_state["source_result"] = {"source": source, "result": result, "params": params}

    held = st.session_state.get("source_result")
    if not held:
        return
    result: base.ConnectorResult = held["result"]
    if result.stale:
        st.warning(result.message)
    elif result.from_cache:
        st.info(result.message)
    else:
        st.success(result.message)
    v = result.vintage
    st.caption(f"Vintage: source `{v.source}`, query `{v.query}`, retrieved "
               f"{v.retrieved_at:%Y-%m-%d %H:%M} UTC, response hash `{v.response_hash[:16]}…`"
               + (f", API version {v.api_version}" if v.api_version else ""))
    data = result.data
    st.dataframe(data.tail(24), use_container_width=True, hide_index=True)
    if st.download_button("Download series", safe_csv(data, index=False),
                          f"{held['source']} series.csv", "text/csv"):
        common.record(audit.EXPORT, f"{held['source']} series.csv", {"rows": len(data)})

    analysis = common.get_active_analysis()
    if analysis is not None and "indices" in analysis["result"] and "period" in data.columns:
        indices = analysis["result"]["indices"]
        headline = "All items" if "All items" in indices.columns else indices.columns[0]
        official = data.set_index(pd.to_datetime(data["period"]))["value"].astype(float)
        common_periods = indices.index.intersection(official.index)
        if len(common_periods) >= 2:
            st.markdown("**Beside the compiled index**")
            st.caption(f"Both rebased to 100 at {common_periods[0]:%b %Y}, the first period "
                       "they share. A comparison of movements, not of levels or coverage.")
            ours = indices.loc[common_periods, headline]
            theirs = official.loc[common_periods]
            frame = pd.DataFrame({
                f"compiled ({headline})": ours / ours.iloc[0] * 100,
                f"{held['source']}": theirs / theirs.iloc[0] * 100})
            st.line_chart(frame)
        else:
            st.caption("No overlap in periods with the compiled index, so no comparison.")
