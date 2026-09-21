"""Quality adjustment: value each item replacement, approve it into the
ledger, and see what the ledger does to the headline.

Three parts. *Candidates* lists the replacements the collection appears
to contain (an item that leaves a category, another that arrives around
the same time). *Value a replacement* computes an adjustment by any of the
Chapter 6 methods -- including hedonic, once a characteristics file has
been fitted -- shows exactly what it does in price terms and index
points, and on approval records it against this data's content hash
(`core.ledger`) and recompiles with the ledger in the configuration.
*Impact* is the report the run then carries: the headline with the
adjustments, with them linked but unvalued, and with no linking at all.

Approvals are made here and take effect on compile; they are never
applied silently. Only a run compiled from an upload in this session can
be adjusted -- a run loaded from the registry is a published record, and
changing its ledger is a correction, which goes through `correct_run`.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from pricelab.core import audit, db, ledger
from pricelab.core.config import QualityAdjustmentConfig
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv
from pricelab.engine import hedonic
from pricelab.engine import quality_adjustment as qa
from pricelab.reporting import charts

from . import common

METHOD_LABELS = {
    "overlap": "Overlap pricing (both priced in one period)",
    "direct_comparison": "Direct comparison (judged comparable)",
    "quantity_adjustment": "Quantity adjustment (size differs)",
    "option_cost": "Option cost (a feature became standard, or was dropped)",
    "targeted_mean_imputation": "Targeted mean imputation (chosen peers' change)",
    "overall_mean_imputation": "Overall mean imputation (whole category's change)",
    "class_mean_imputation": "Class mean imputation (other replacements' adjusted change)",
    "hedonic": "Hedonic (from the fitted characteristics model)",
    "link_to_show_no_change": "Link to show no change (not recommended)",
}


def replacement_candidates(clean: pd.DataFrame, max_gap: int = 3) -> pd.DataFrame:
    """Exits paired with entrants in the same category within `max_gap`
    periods. A suggestion list, not a judgement: it is what the collection
    looks like, and the analyst decides which pairs are replacements."""
    d = clean.dropna(subset=["price_clean"])
    periods = sorted(d["period"].unique())
    if len(periods) < 2:
        return pd.DataFrame(columns=["category", "old_item", "old_last", "new_item", "new_first"])
    pos = {p: i for i, p in enumerate(periods)}
    life = d.groupby(["category", "item_id"])["period"].agg(["min", "max"]).reset_index()
    exits = life[life["max"] < periods[-1]]
    entrants = life[life["min"] > periods[0]]
    rows = []
    for _, e in exits.iterrows():
        for _, n in entrants[entrants["category"] == e["category"]].iterrows():
            if n["item_id"] == e["item_id"]:
                continue
            gap = pos[n["min"]] - pos[e["max"]]
            if 0 <= gap <= max_gap:
                rows.append({"category": e["category"], "old_item": e["item_id"],
                             "old_last": e["max"], "new_item": n["item_id"],
                             "new_first": n["min"], "gap_periods": gap})
    return pd.DataFrame(rows, columns=["category", "old_item", "old_last", "new_item",
                                       "new_first", "gap_periods"])


def _prices(clean: pd.DataFrame, category: str, item: str) -> pd.Series:
    d = clean[(clean["category"] == category) & (clean["item_id"] == item)]
    return d.set_index("period")["price_clean"].dropna().sort_index()


def _cell(res: dict[str, Any], category: str, period: pd.Timestamp) -> qa.CellContext:
    matched = res["matched_counts"]
    n = matched.loc[period, category] if (period in matched.index and category in matched.columns) else np.nan
    n_items = int(n) if np.isfinite(n) and n > 0 else 1
    return qa.CellContext(n_items=n_items + 1, formula=res["config"].index.formula
                          if res["config"].index.formula in ("jevons", "carli") else "jevons")


def _fit_hedonic_section(clean: pd.DataFrame) -> hedonic.HedonicResult | None:
    """Upload characteristics, fit a time-dummy hedonic model over the
    collection, and show its diagnostics. Returns the fit (also kept in
    session state) so the valuation form can use it."""
    st.markdown("#### Hedonic model")
    st.caption("Upload a characteristics file (CSV or Excel) with an `item_id` column and one "
               "column per characteristic. The model is fitted over every priced observation "
               "of every item in the file, with a dummy per period.")
    upload = st.file_uploader("Characteristics file", type=["csv", "xlsx"], key="qa_chars")
    if upload is None:
        return st.session_state.get("hedonic_fit")
    raw = upload.getvalue()
    chars = (pd.read_csv(io.BytesIO(raw)) if upload.name.lower().endswith(".csv")
             else pd.read_excel(io.BytesIO(raw)))
    if "item_id" not in chars.columns:
        st.error("The characteristics file needs an `item_id` column.")
        return None
    chars["item_id"] = chars["item_id"].astype(str)
    st.session_state["characteristics"] = chars
    candidates = [c for c in chars.columns if c != "item_id"]
    numeric = [c for c in candidates if pd.api.types.is_numeric_dtype(chars[c])]
    continuous = st.multiselect("Continuous characteristics", numeric, default=numeric)
    categorical = st.multiselect("Categorical characteristics",
                                 [c for c in candidates if c not in continuous],
                                 default=[c for c in candidates if c not in numeric])
    form = st.selectbox("Functional form", hedonic.FUNCTIONAL_FORMS, index=1)
    use_weights = "weight" in clean.columns and clean["weight"].notna().any()
    weighted = st.checkbox("Weight by expenditure weight", value=use_weights, disabled=not use_weights)
    window = st.number_input("Coefficient stability window (periods)", 2, 24, 6)
    if not st.button("Fit hedonic model"):
        return st.session_state.get("hedonic_fit")

    panel = clean.dropna(subset=["price_clean"]).merge(chars, on="item_id", how="inner")
    if panel.empty:
        st.error("No item in the characteristics file matches an item_id in the collection.")
        return None
    spec = hedonic.HedonicSpec(
        characteristics=tuple(continuous), categorical=tuple(categorical), functional_form=form,
        weight_col="weight" if weighted else None, price_col="price_clean")
    try:
        import warnings
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            fit = hedonic.fit_hedonic(panel, spec, stability_window=int(window), cv_folds=5)
    except hedonic.HedonicError as exc:
        st.error(f"The model could not be fitted: {exc}")
        return None
    st.session_state["hedonic_fit"] = fit
    common.record(audit.CONFIGURATION_CHANGE, "hedonic fit", {
        "functional_form": form, "characteristics": continuous, "categorical": categorical,
        "n_obs": fit.n_obs, "adj_r_squared": fit.adj_r_squared,
        "multicollinearity_warning": bool(fit.warnings)})
    return fit


def _show_hedonic_diagnostics(fit: hedonic.HedonicResult) -> None:
    for w in fit.warnings:
        st.warning(w)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Adjusted R²", f"{fit.adj_r_squared:.3f}")
    c2.metric("Observations", f"{fit.n_obs:,}")
    c3.metric("Condition number", f"{fit.condition_number:.0f}")
    oos = fit.out_of_sample.get("mape_pct")
    c4.metric("Out-of-sample error", f"{oos:.1f}%" if oos is not None else "n/a",
              help="Mean absolute percentage error of price predicted for items the model "
                   "did not see, from k-fold cross-validation.")
    st.markdown("**Coefficients** (heteroskedasticity-robust standard errors; VIF above 10 "
                "flags collinearity)")
    st.dataframe(fit.summary_frame().round(4), use_container_width=True)
    if fit.coefficient_stability is not None:
        st.markdown("**Coefficient stability across windows of periods**")
        st.dataframe(fit.coefficient_stability.round(4), use_container_width=True)
    if fit.box_cox_lambda is not None:
        st.caption(f"Box-Cox lambda estimated at {fit.box_cox_lambda:.3f}.")
    left, right = st.columns(2)
    left.pyplot(charts.hedonic_residual_chart(fit), use_container_width=True)
    right.pyplot(charts.hedonic_leverage_chart(fit), use_container_width=True)
    with st.expander("Index implied by the period dummies"):
        st.dataframe(pd.DataFrame({
            "time dummy index": fit.time_dummy_index(),
            "bias corrected": fit.time_dummy_index(bias_corrected=True)}).round(3),
            use_container_width=True)


def _valuation_form(res: dict[str, Any], clean: pd.DataFrame, fit: hedonic.HedonicResult | None
                    ) -> qa.QualityAdjustment | None:
    """Collect the inputs for one method and compute the adjustment. The
    adjustment is shown before it can be approved."""
    categories = sorted(clean["category"].unique())
    category = st.selectbox("Category", categories)
    items = sorted(clean.loc[clean["category"] == category, "item_id"].unique())
    if len(items) < 2:
        st.info("That category has only one item; there is nothing to link.")
        return None
    c1, c2 = st.columns(2)
    old_item = c1.selectbox("Old item (leaving)", items)
    new_item = c2.selectbox("New item (replacement)", [i for i in items if i != old_item])
    old_p, new_p = _prices(clean, category, old_item), _prices(clean, category, new_item)
    if old_p.empty or new_p.empty:
        st.info("Both items need at least one priced observation.")
        return None
    method = st.selectbox("Method", list(METHOD_LABELS), format_func=METHOD_LABELS.get)
    new_periods = list(new_p.index)
    period = st.selectbox("Link from (first period the replacement stands in for the old item)",
                          new_periods, index=0, format_func=lambda p: f"{p:%b %Y}")
    old_before = old_p[old_p.index < period]
    old_price = float(old_before.iloc[-1]) if len(old_before) else float(old_p.iloc[0])
    new_price = float(new_p.loc[period])
    st.caption(f"Old item's last price before the link: {old_price:.4g} "
               f"({old_before.index[-1]:%b %Y}); replacement at {period:%b %Y}: {new_price:.4g}."
               if len(old_before) else
               f"The old item has no price before {period:%b %Y}; using its first price.")
    justification = st.text_area("Justification (required)", key="qa_just")
    cell = _cell(res, category, period)
    common_kwargs = {"cell": cell, "justification": justification}

    try:
        if method == "overlap":
            both = old_p.index.intersection(new_p.index)
            if len(both) == 0:
                st.warning("These two items are never priced in the same period, so there is no "
                           "overlap. Impute one (targeted mean) or value it explicitly.")
                return None
            overlap = st.selectbox("Overlap period", list(both), format_func=lambda p: f"{p:%b %Y}")
            return qa.overlap(old_item, new_item, category, old_p, new_p, overlap,
                              period=period, **common_kwargs)
        if method == "direct_comparison":
            return qa.direct_comparison(old_item, new_item, category, period, old_price, new_price,
                                        **common_kwargs)
        if method == "quantity_adjustment":
            q1, q2, u = st.columns(3)
            oq = q1.number_input("Old quantity", min_value=0.0, value=1.0, format="%.4f")
            nq = q2.number_input("New quantity", min_value=0.0, value=1.0, format="%.4f")
            unit = u.text_input("Unit", "")
            return qa.quantity_adjustment(old_item, new_item, category, period, old_price,
                                          new_price, oq, nq, unit=unit, **common_kwargs)
        if method == "option_cost":
            o1, o2, o3 = st.columns(3)
            value = o1.number_input("Option value", min_value=0.0, value=0.0, format="%.4f")
            added = o2.radio("Feature", ["added as standard", "removed"], horizontal=True) == "added as standard"
            share = o3.slider("Share of option price valued", 0.0, 1.0, 1.0, 0.05)
            return qa.option_cost(old_item, new_item, category, period, old_price, new_price,
                                  value, feature_added=added, share_valued=share, **common_kwargs)
        if method in ("targeted_mean_imputation", "overall_mean_imputation"):
            prev_period = old_before.index[-1] if len(old_before) else None
            if prev_period is None:
                st.warning("Mean imputation needs the old item priced in the period before the link.")
                return None
            cat_rows = clean[(clean["category"] == category) & ~clean["item_id"].isin([old_item, new_item])]
            prev = cat_rows[cat_rows["period"] == prev_period].set_index("item_id")["price_clean"]
            curr = cat_rows[cat_rows["period"] == period].set_index("item_id")["price_clean"]
            peers = sorted(prev.index.intersection(curr.index))
            if method == "targeted_mean_imputation":
                peers = st.multiselect("Peer items (expected to move like the old item)", peers,
                                       default=peers)
            fn = qa.targeted_mean if method == "targeted_mean_imputation" else qa.overall_mean
            return fn(old_item, new_item, category, period, old_price, new_price,
                      prev.reindex(peers), curr.reindex(peers), **common_kwargs)
        if method == "class_mean_imputation":
            others = [e for e in res["config"].quality_adjustment.entries
                      if e.category == category and e.new_item != new_item]
            rels = [e.parameters.get("new_price", np.nan) / e.quality_ratio / e.parameters.get("old_price", np.nan)
                    for e in others]
            if not others:
                st.warning("Class mean imputation uses other replacements in this category that were "
                           "already valued; there are none yet.")
                return None
            st.caption(f"Using the quality-adjusted relatives of {len(others)} other replacement(s).")
            return qa.class_mean(old_item, new_item, category, period, old_price, new_price, rels,
                                 **common_kwargs)
        if method == "hedonic":
            chars = st.session_state.get("characteristics")
            if fit is None or chars is None:
                st.warning("Fit a hedonic model below first.")
                return None
            by_item = chars.set_index("item_id")
            if old_item not in by_item.index or new_item not in by_item.index:
                st.warning("Both items need a row in the characteristics file.")
                return None
            return hedonic.hedonic_adjustment(
                fit, old_item, new_item, category, period, old_price, new_price,
                by_item.loc[old_item].to_dict(), by_item.loc[new_item].to_dict(), **common_kwargs)
        if method == "link_to_show_no_change":
            import warnings
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                adj = qa.link_to_show_no_change(old_item, new_item, category, period, old_price,
                                                new_price, **common_kwargs)
            for w in caught:
                st.warning(str(w.message))
            return adj
    except (qa.QualityAdjustmentError, hedonic.HedonicError) as exc:
        st.error(str(exc))
    return None


def _show_adjustment(adj: qa.QualityAdjustment) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Quality ratio", f"{adj.quality_ratio:.4f}",
              help="New item's worth relative to the old, in price terms.")
    c2.metric("Adjusted new price", f"{adj.adjusted_new_price:.4g}",
              help="The replacement's price in old-quality terms; the old series continues at this.")
    c3.metric("Attributed to quality", f"{adj.adjustment_price:+.4g}",
              help="Collected price minus adjusted price.")
    c4.metric("Effect on the cell's link", f"{adj.index_points:+.2f} pts" if np.isfinite(adj.index_points) else "n/a",
              help="Index points, versus treating the raw replacement price as pure price change.")
    st.caption(f"Pure price relative across the replacement: {adj.pure_price_relative:.4f}. "
               f"Reason code `{adj.reason_code}`.")
    if adj.parameters:
        with st.expander("Method parameters"):
            st.json({k: v for k, v in adj.parameters.items()})


def _impact_section(res: dict[str, Any]) -> None:
    impact = res.get("quality_adjustment_impact")
    if impact is None:
        st.caption("No approved adjustment is applied to this run yet.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Effect of the adjustments on the headline",
              f"{impact.adjustment_effect_points:+.2f} pts",
              help="As compiled minus the same replacements linked at ratio 1.")
    c2.metric("In annual inflation", f"{impact.adjustment_effect_annual_pp:+.2f} pp",
              help=f"Measured as {impact.annual_measure.replace('_', ' ')} at {impact.final_period:%b %Y}.")
    c3.metric("Effect of linking at all", f"{impact.linking_effect_points:+.2f} pts",
              help="As compiled minus the matched-model default that skips every replacement.")
    st.pyplot(charts.impact_chart(impact), use_container_width=True)
    st.markdown("**Scenarios**")
    st.dataframe(impact.scenarios.round(3), use_container_width=True)
    st.markdown("**Attribution by replacement**")
    st.dataframe(impact.per_entry.round(4), use_container_width=True)
    st.markdown("**By category**")
    st.dataframe(impact.per_category.round(3), use_container_width=True)


@require_role(Role.ADMINISTRATOR, Role.COMPILER)
def render() -> None:
    st.markdown("### Quality adjustment")
    analysis = common.get_active_analysis()
    if analysis is None:
        st.info("Compile a run on the Ingest page first.")
        return
    if st.session_state.get("loaded_run_id") is not None or "content_hash" not in st.session_state:
        st.info("A run loaded from the registry is a published record. To change its quality "
                "adjustments, upload the data again on Ingest and compile a new run.")
        return

    res = analysis["result"]
    cfg = res["config"]
    clean = res["clean"]
    content_hash = st.session_state["content_hash"]
    label = analysis.get("label", "analysis")

    st.markdown("#### Replacement candidates")
    cands = replacement_candidates(clean)
    if len(cands):
        st.caption("Items that left a category paired with items that arrived within three "
                   "periods. A pairing is a suggestion; whether it is a replacement is your call.")
        st.dataframe(cands, use_container_width=True, hide_index=True)
    else:
        st.caption("No exit is paired with an arrival in the same category within three periods.")

    st.markdown("#### Value a replacement")
    fit = st.session_state.get("hedonic_fit")
    adj = _valuation_form(res, clean, fit)
    if adj is not None:
        _show_adjustment(adj)
        if st.button("Approve and apply", type="primary", disabled=not adj.justification.strip()):
            entry = adj.to_entry(common.current_username())
            try:
                new_cfg = cfg.model_copy(update={"quality_adjustment": QualityAdjustmentConfig(
                    entries=[*cfg.quality_adjustment.entries, entry])}, deep=True)
            except ValueError as exc:
                st.error(str(exc))
                return
            with db.session_scope() as s:
                ledger.record_adjustment(s, common.current_username(), content_hash, entry)
            common.record(audit.QUALITY_ADJUSTMENT_APPROVED, f"{entry.old_item} -> {entry.new_item}", {
                "category": entry.category, "method": entry.method,
                "quality_ratio": entry.quality_ratio, "period": entry.period})
            common.compile_and_store(st.session_state["input_df"], new_cfg, label,
                                     st.session_state["file_bytes"], trigger="quality_adjustment")
            st.success("Approved, recorded and applied. The run has been recompiled.")
            st.rerun()
        elif not adj.justification.strip():
            st.caption("A justification is required before an adjustment can be approved.")

    st.markdown("#### Ledger")
    with db.session_scope() as s:
        records = ledger.load_ledger(s, content_hash, include_withdrawn=True)
        rows = [{"id": r.id, "period": r.period, "category": r.category, "old_item": r.old_item,
                 "new_item": r.new_item, "method": r.method, "quality_ratio": r.quality_ratio,
                 "justification": r.justification, "approved_by": r.approved_by,
                 "approved_at": r.approved_at, "withdrawn_at": r.withdrawn_at or ""}
                for r in records]
    if rows:
        table = pd.DataFrame(rows)
        st.dataframe(table, use_container_width=True, hide_index=True)
        if st.download_button("Download ledger", safe_csv(table, index=False),
                              f"{label} quality adjustment ledger.csv", "text/csv"):
            common.record(audit.EXPORT, f"{label} quality adjustment ledger.csv", {"rows": len(table)})
        active = [r for r in rows if not r["withdrawn_at"]]
        if active:
            which = st.selectbox("Withdraw an approval", active,
                                 format_func=lambda r: f"#{r['id']} {r['old_item']} -> {r['new_item']} ({r['method']})")
            reason = st.text_input("Reason for withdrawal (required)", key="qa_withdraw_reason")
            if st.button("Withdraw", disabled=not reason.strip()):
                with db.session_scope() as s:
                    ledger.withdraw_adjustment(s, common.current_username(), int(which["id"]), reason)
                    remaining = ledger.active_entries(s, content_hash)
                common.record(audit.QUALITY_ADJUSTMENT_WITHDRAWN,
                              f"{which['old_item']} -> {which['new_item']}", {"reason": reason})
                new_cfg = cfg.model_copy(update={"quality_adjustment": QualityAdjustmentConfig(
                    entries=remaining)}, deep=True)
                common.compile_and_store(st.session_state["input_df"], new_cfg, label,
                                         st.session_state["file_bytes"], trigger="quality_adjustment")
                st.rerun()
    else:
        st.caption("No adjustment has been approved for this data.")

    st.markdown("#### Impact on the headline")
    _impact_section(res)

    st.divider()
    fit = _fit_hedonic_section(clean)
    if fit is not None:
        _show_hedonic_diagnostics(fit)
