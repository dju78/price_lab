"""Trade prices: import and export price indices, unit value indices, and
the terms of trade.

Wherever a unit value index appears on this page, the unit value bias and
the conditions under which a unit value is a defensible stand-in for a
price are stated beside it, and the price index on the same data is shown
next to it so the gap can be seen rather than taken on trust.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import trade as tr

from . import common


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Trade prices")
    st.caption(
        "Upload customs-style transactions (CSV: period, flow, product, value, quantity, with "
        "flow 'export' or 'import'). Each product's price is its own value over quantity at "
        "the detail you supply; the price index aggregates products with a Fisher, Laspeyres "
        "or Paasche formula so the mix of trade enters as weights, not as price.")
    upload = st.file_uploader("Trade transactions (CSV)", type=["csv"], key="tr_file")
    if upload is None:
        return
    try:
        transactions = common.read_upload_table(
            upload, ("period", "flow", "product", "value", "quantity"))
    except ValueError as exc:
        st.error(str(exc))
        return
    periods = sorted(pd.DatetimeIndex(transactions["period"]).unique())
    c1, c2 = st.columns(2)
    formula = c1.selectbox("Price index formula", list(tr.FORMULAS), key="tr_formula")
    base = c2.selectbox("Base period", periods, key="tr_base",
                        format_func=lambda p: f"{pd.Timestamp(p):%b %Y}")
    if not st.button("Compile", type="primary", key="tr_go"):
        return

    flows = [f for f in tr.FLOWS
             if (transactions["flow"].astype(str).str.lower() == f).any()]
    results: dict[str, tr.UnitValueBias] = {}
    for flow in flows:
        try:
            results[flow] = tr.unit_value_bias(transactions, flow, formula=formula,
                                               base=pd.Timestamp(base))
        except tr.TradeError as exc:
            st.error(f"{flow}: {exc}")
    st.session_state["tr_results"] = results
    common.record(audit.TRADE_INDEX, upload.name, {"formula": formula, "flows": flows})

    # The bias statement comes before any unit value number.
    st.warning(f"{tr.UNIT_VALUE_WARNING} {tr.UNIT_VALUE_CONDITIONS}")
    for flow, bias in results.items():
        st.markdown(f"#### {flow.capitalize()}s")
        k = st.columns(2)
        k[0].metric(f"{flow.capitalize()} price index, latest", f"{bias.price.index.iloc[-1]:.2f}")
        k[1].metric("Largest unit value gap", f"{bias.max_gap_points:.2f} points",
                    help="Unit value index minus price index: the composition of trade, "
                         "reported as if it were price.")
        st.dataframe(bias.table.round(4), use_container_width=True)
        st.download_button(
            f"Download {flow} indices (CSV)",
            safe_csv_with_notice(bias.table.reset_index(names="period"),
                                 f"{bias.price.label}; unit value index shown beside it. "
                                 f"{bias.warning}"),
            file_name=f"{flow}_indices.csv", mime="text/csv", key=f"tr_csv_{flow}")

    if {"export", "import"} <= set(results):
        terms = tr.terms_of_trade(results["export"].price, results["import"].price)
        st.session_state["tr_terms"] = terms
        st.markdown("#### Terms of trade")
        st.caption("Export price index over import price index, times 100: above 100, a unit "
                   "of exports buys more imports than in the base period.")
        st.dataframe(terms.round(4).to_frame(), use_container_width=True)
