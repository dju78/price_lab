"""Property prices: residential property price indices by five families of
method, the diagnostics they rest on, the revisions repeat sales makes by
construction, and a published index rebuilt from its published parts.

Every number on this page appears with what its method measures and its
principal limitation beside it, and the differences between methods are
explained, with numbers, underneath them: they answer different questions,
and a reader who takes them for disagreeing estimates of one number will
draw the wrong conclusion from the spread.
"""

from __future__ import annotations

import warnings

import pandas as pd
import streamlit as st

from pricelab.core import audit, db
from pricelab.core.config import get_settings
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import asset as ast

from . import common

#: How the methods run on price paid data, which has no floor area and no
#: appraisal, and whose re-sales within a year are mostly not price change.
PRICE_PAID_SETTINGS: dict[str, object] = {
    "size_col": None, "cell_cols": ("leasehold", "new_build"), "characteristics": (),
    "categorical": ("stratum", "leasehold", "new_build", "county"),
    "rules": ast.PairRules(min_months=6, exclude_new_build_first=True, max_ratio=2.0),
}


def _transactions() -> pd.DataFrame | None:
    st.caption("Transactions as a CSV: property_id, period, price, stratum, floor_area, and "
               "appraisal where the sale price appraisal ratio is wanted.")
    upload = st.file_uploader("Property transactions (CSV)", type=["csv"], key="pr_file")
    if upload is not None:
        try:
            frame = common.read_upload_table(upload, ("property_id", "period", "price", "stratum",
                                                      "floor_area"))
        except ValueError as exc:
            st.error(str(exc))
            return None
        st.session_state["pr_tx"] = frame
        st.session_state["pr_source"] = upload.name
    elif st.button("Use the demonstration market", key="pr_demo",
                   help="A synthetic market whose quality-constant index is known, and in which "
                        "larger homes sell increasingly often."):
        st.session_state["pr_tx"] = ast.synthetic_market()
        st.session_state["pr_source"] = "the demonstration market"
    ppd = st.file_uploader("Or: HM Land Registry price paid data (CSV, exactly as published)",
                           type=["csv"], key="pr_ppd_file",
                           help="Headerless, sixteen columns. A district or region extract "
                                "keeps within the upload limit.")
    if ppd is not None:
        from pricelab.data.loaders import read_upload
        from pricelab.data.price_paid import parse_price_paid

        try:
            parsed = parse_price_paid(read_upload(ppd.getvalue(), ppd.name).df)
        except ValueError as exc:
            st.error(f"{ppd.name}: {exc}")
            return None
        st.session_state["pr_tx"] = parsed.transactions
        st.session_state["pr_source"] = f"{ppd.name} (HM Land Registry price paid data)"
        st.session_state["pr_findings"] = parsed.findings
        st.session_state["pr_settings"] = PRICE_PAID_SETTINGS
    elif upload is not None or st.session_state.get("pr_source") == "the demonstration market":
        st.session_state.pop("pr_findings", None)
        st.session_state["pr_settings"] = {}
    frame = st.session_state.get("pr_tx")
    if frame is not None:
        st.caption(f"{len(frame):,} sales of {frame['property_id'].nunique():,} properties from "
                   f"{st.session_state.get('pr_source', 'upload')}.")
    findings = st.session_state.get("pr_findings")
    if findings is not None:
        st.markdown("##### What the price paid data held, and what was done about it")
        st.dataframe(findings, use_container_width=True, hide_index=True)
        st.caption("Contains HM Land Registry data (c) Crown copyright and database right, "
                   "licensed under the Open Government Licence v3.0.")
    return frame


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Property prices")
    st.caption("Five families of residential property price index. They measure different "
               "things, so each number below carries what it measures and what it rests on.")
    tx = _transactions()
    if tx is not None:
        _methods(tx)
        _diagnostics(tx)
        _revisions(tx)
    _published()


def _methods(tx: pd.DataFrame) -> None:
    st.divider()
    st.markdown("#### Every method on the same sales")
    if st.button("Compile all methods", type="primary", key="pr_go"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                comparison = ast.compare_methods(tx, **st.session_state.get("pr_settings", {}))
        except ast.PropertyError as exc:
            st.error(str(exc))
            return
        st.session_state["pr_comparison"] = comparison
        common.record(audit.PROPERTY_INDEX, st.session_state.get("pr_source", "upload"), {
            "methods": sorted(comparison.results), "sales": len(tx)})
    comparison = st.session_state.get("pr_comparison")
    if comparison is None:
        return
    last = comparison.table.dropna(how="all").index.max()
    for result in comparison.results.values():
        value = result.index.get(last, float("nan"))
        st.markdown(f"**{result.name}: {value:.2f}** at {last:%b %Y} ({result.base:%b %Y} = 100)")
        st.caption(f"Measures {result.measures}. Limitation: {result.limitation}.")
    common.show_uncertainty(
        "The property price indices", run_headline=False,
        reason="they are built from transactions, not a designed sample; what limits them is "
               "each method's selection or specification, stated beside it")
    st.markdown("##### Why they differ")
    for line in comparison.explanation:
        st.markdown(f"- {line}")
    st.line_chart(comparison.table)
    table = comparison.table.copy()
    table.columns = [comparison.results[c].name for c in table.columns]
    st.dataframe(table.round(2), use_container_width=True)
    st.download_button(
        "Download all methods (CSV)",
        safe_csv_with_notice(table.reset_index(names="period"),
                             "Each column is a different measure: see the method notes. "
                             + " ".join(r.label for r in comparison.results.values())),
        file_name="property_indices.csv", mime="text/csv", key="pr_csv")


def _diagnostics(tx: pd.DataFrame) -> None:
    st.divider()
    st.markdown("#### What the indices rest on")
    counts = ast.transaction_counts(tx)
    st.markdown("##### Sales per stratum per period")
    st.dataframe(counts, use_container_width=True)
    st.markdown("##### Share of the market each method uses")
    characteristics = st.session_state.get("pr_settings", {}).get("characteristics",
                                                                  ("floor_area",))
    st.dataframe(ast.market_coverage(tx, characteristics=characteristics).round(2),
                 use_container_width=True)

    st.markdown("##### Stratified median by stratum, under disclosure control")
    threshold = int(st.number_input("Minimum sales for a stratum to be published", 1, 1000,
                                    int(get_settings().suppression_min_count), key="pr_min"))
    try:
        protected = ast.suppress_strata(ast.stratified_median(tx), min_count=threshold)
    except ast.PropertyError as exc:
        st.error(str(exc))
        return
    st.session_state["pr_suppressed"] = protected
    n_primary = int((protected["suppressed"] & ~protected["secondary_suppressed"]).sum())
    n_secondary = int(protected["secondary_suppressed"].sum())
    st.caption(f"{n_primary} stratum-period(s) below {threshold} sales are suppressed, and "
               f"{n_secondary} more are suppressed so that the first cannot be recovered from "
               "the published all-strata index.")
    wide = protected.pivot(index="period", columns="stratum", values="published")
    st.dataframe(wide, use_container_width=True)


def _revisions(tx: pd.DataFrame) -> None:
    st.divider()
    st.markdown("#### Repeat sales revisions")
    st.caption("A repeat sales index re-estimates its whole history every time a period is "
               "added. Below is the index as it would have been published at the end of each "
               "period, through the same revision triangle every other revision in the "
               "platform uses; it also appears on the Revisions page.")
    weighted = st.checkbox("Case-Shiller weighted form", value=False, key="pr_rev_weighted")
    if not st.button("Estimate the revision profile", key="pr_rev_go"):
        return
    try:
        analysis = ast.repeat_sales_revisions(
            tx, weighted=weighted, rules=st.session_state.get("pr_settings", {}).get("rules"))
    except ast.PropertyError as exc:
        st.error(str(exc))
        return
    st.session_state["pr_revision"] = analysis
    common.record(audit.REVISION_ANALYSIS, "repeat sales house price index", {
        "vintages": len(analysis.vintages),
        "mean_absolute_revision": analysis.mean_absolute_revision})
    from .revisions import show_analysis

    show_analysis(analysis, key="pr_revision")


def _published() -> None:
    """A published house price index rebuilt from its published parts."""
    st.divider()
    st.markdown("#### A published index, rebuilt from its parts")
    st.caption("Eurostat publishes each country's house price index for new and for existing "
               "dwellings, and the weights that combine them. Rebuilding the total from those "
               "parts, chained at the fourth quarter, checks the platform's aggregation against "
               "the published number.")
    geo = st.text_input("Country (Eurostat geo code)", "IE", key="pr_geo")
    if not st.button("Fetch and rebuild", key="pr_fetch"):
        return
    from pricelab.data.connectors import base
    from pricelab.data.connectors.eurostat import (
        HPI_DATASET,
        HPI_WEIGHT_DATASET,
        EurostatConnector,
        hpi_components,
    )
    from pricelab.engine.decomposition import chain_linked_aggregate

    from .sources import connector_cache

    connector = EurostatConnector(cache=connector_cache())
    try:
        with db.session_scope() as s:
            indices = connector.fetch(dataset=f"{HPI_DATASET}/Q.TOTAL+DW_NEW+DW_EXST.I15_Q.{geo}",
                                      startPeriod="2019-Q4", audit_session=s,
                                      actor=common.current_username())
            weights = connector.fetch(dataset=f"{HPI_WEIGHT_DATASET}/A.TOTAL+DW_NEW+DW_EXST.{geo}",
                                      startPeriod="2019", audit_session=s,
                                      actor=common.current_username())
        parts, by_year, total = hpi_components(indices.data, weights.data, geo)
        rebuilt = chain_linked_aggregate(parts, by_year, link_month=10)
    except (base.ConnectorError, ValueError) as exc:
        st.error(str(exc))
        return
    start = rebuilt.first_valid_index()
    ours = rebuilt / 100.0 * float(total[start])
    comparison = pd.DataFrame({"published total": total, "rebuilt from new and existing": ours})
    comparison["gap, index points"] = comparison.iloc[:, 1] - comparison.iloc[:, 0]
    st.session_state["pr_rebuilt"] = comparison
    gap = float(comparison["gap, index points"].abs().max())
    st.metric("Largest gap to the published total", f"{gap:.3f} index points")
    st.caption("The published sub-indices are rounded to two decimals, so a rebuilt total can "
               "differ from the published one, which Eurostat compiles from unrounded parts, by "
               "a few hundredths of an index point; a larger gap would mean the weights or the "
               "link period differ from those assumed here.")
    st.dataframe(comparison.round(3), use_container_width=True)
