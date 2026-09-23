"""Rents and owner-occupied housing.

The four owner-occupied housing approaches answer four different questions.
The page opens with those questions side by side, then gives each approach
its own section under its own question, with its own inputs. There is no
selector: a selector would imply one quantity estimated four ways, which is
exactly the misreading this page exists to prevent.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role
from pricelab.engine import housing as hs

from . import common


def _series(label: str, key: str) -> pd.Series | None:
    upload = st.file_uploader(f"{label} (CSV: period, value)", type=["csv"], key=key)
    if upload is None:
        return None
    try:
        frame = common.read_upload_table(upload, ("period", "value"))
    except ValueError as exc:
        st.error(str(exc))
        return None
    return pd.Series(frame["value"].to_numpy(dtype=float),
                     index=pd.DatetimeIndex(frame["period"])).sort_index()


def _show(result: hs.OOHResult) -> None:
    st.success(result.label)
    for note in result.notes:
        st.caption(note)
    st.line_chart(result.index)
    st.dataframe(result.components.round(3).tail(12), use_container_width=True)


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Rents and owner-occupied housing")
    _rents()
    st.divider()
    st.markdown("#### Owner-occupied housing: four questions, not four methods")
    st.caption("Each approach answers a different question about what owning a home costs. "
               "They are not interchangeable estimates of one number, and the choice between "
               "them is a choice of question, made before any data is looked at.")
    st.table(pd.DataFrame({
        "Question it answers": {s["name"]: s["question"] for s in hs.OOH_QUESTIONS.values()},
        "Counts": {s["name"]: s["counts"] for s in hs.OOH_QUESTIONS.values()},
        "Leaves out": {s["name"]: s["leaves_out"] for s in hs.OOH_QUESTIONS.values()}}))
    _rental_equivalence()
    _net_acquisitions()
    _user_cost()
    _payments()


def _rents() -> None:
    st.markdown("#### Rental price index")
    upload = st.file_uploader("Rents (CSV: dwelling_id, period, rent, stratum)", type=["csv"],
                              key="hs_rents")
    if upload is None:
        return
    try:
        frame = common.read_upload_table(upload, ("dwelling_id", "period", "rent", "stratum"))
        result = hs.rent_index(frame)
    except (ValueError, hs.HousingError) as exc:
        st.error(str(exc))
        return
    st.session_state["hs_rent_result"] = result
    st.success(result.label)
    st.line_chart(result.index)
    st.dataframe(pd.DataFrame({"index": result.index, "matched dwellings": result.matched})
                 .round(3), use_container_width=True)


def _section(key: str) -> None:
    spec = hs.OOH_QUESTIONS[key]
    st.markdown(f"##### {spec['name']}: {spec['question']}")


def _rental_equivalence() -> None:
    _section("rental_equivalence")
    rents = st.session_state.get("hs_rent_result")
    if rents is None:
        st.caption("Uses the rental price index above; upload rents to compute it.")
        return
    result = hs.rental_equivalence(rents)
    st.session_state["hs_ooh_rental_equivalence"] = result
    _show(result)


def _net_acquisitions() -> None:
    _section("net_acquisitions")
    prices = _series("New-dwelling price index", "hs_new")
    if prices is None:
        return
    land_share = st.slider("Land share of the price, to exclude", 0.0, 0.9, 0.0, 0.05,
                           key="hs_land_share")
    land = _series("Land price index", "hs_land") if land_share else None
    try:
        result = hs.net_acquisitions(prices, land_share=land_share, land_prices=land)
    except hs.HousingError as exc:
        st.error(str(exc))
        return
    st.session_state["hs_ooh_net_acquisitions"] = result
    _show(result)


def _user_cost() -> None:
    _section("user_cost")
    prices = _series("House price index", "hs_hpi")
    rate = _series("Interest rate, % a year", "hs_rate")
    if prices is None or rate is None:
        return
    c1, c2, c3, c4 = st.columns(4)
    depreciation = c1.number_input("Depreciation, %", 0.0, 10.0, 1.5, key="hs_dep")
    maintenance = c2.number_input("Maintenance, %", 0.0, 10.0, 1.0, key="hs_maint")
    tax = c3.number_input("Property tax, %", 0.0, 10.0, 0.5, key="hs_tax")
    years = int(c4.number_input("Years of price growth behind the expected gain", 1, 20, 5,
                                key="hs_years"))
    try:
        result = hs.user_cost(prices, rate, depreciation_pct=depreciation,
                              maintenance_pct=maintenance, tax_pct=tax,
                              expectation_years=years)
    except hs.HousingError as exc:
        st.error(str(exc))
        return
    st.session_state["hs_ooh_user_cost"] = result
    _show(result)


def _payments() -> None:
    _section("payments")
    upload = st.file_uploader("Outlays (CSV: period, then one column per outlay)", type=["csv"],
                              key="hs_outlays")
    if upload is None:
        return
    try:
        frame = common.read_upload_table(upload, ("period",)).set_index("period")
        result = hs.payments(frame)
    except (ValueError, hs.HousingError) as exc:
        st.error(str(exc))
        return
    st.session_state["hs_ooh_payments"] = result
    common.record(audit.OWNER_OCCUPIED_HOUSING, upload.name, {"approach": "payments"})
    _show(result)
