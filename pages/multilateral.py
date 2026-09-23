"""Multilateral: the six methods, the window, the six extension rules, and
the spread between them.

The page is built around the comparison rather than the answer. A single
multilateral number is a fact about prices *and* about a method, and the
only way to stop a reader treating it as the first alone is to show them the
other five at the same time. Everything shown comes from
`engine.multilateral`; the page picks the arguments and prints the result.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import streamlit as st

from pricelab.core.config import MultilateralConfig
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import multilateral as ml
from pricelab.engine.hedonic import HedonicSpec

from . import common


def _hedonics_from_session() -> tuple[HedonicSpec | None, pd.DataFrame | None]:
    """The characteristics table and the specification the Quality
    adjustment page fitted with, if this session has both.

    The time dummy hedonic needs characteristics the price panel does not
    carry, and there is exactly one place in this application they are
    uploaded and one place their specification is chosen. Re-asking here
    would invite a second, quietly different specification of the same
    regression, and two hedonic indices in one run that disagree for a
    reason nobody wrote down.

    The spec's `price_col` is repointed at the multilateral price column:
    the fit on the Quality adjustment page regresses `price_clean`, and this
    index is built on the imputed panel like every other series on this
    page, so the two must not silently diverge on which price they mean.
    """
    fit = st.session_state.get("hedonic_fit")
    chars = st.session_state.get("characteristics")
    if fit is None or not isinstance(chars, pd.DataFrame) or chars.empty:
        return None, None
    spec = getattr(fit, "spec", None)
    if not isinstance(spec, HedonicSpec):
        return None, None
    return replace(spec, price_col="price_imputed"), chars


def _remember(settings: MultilateralConfig) -> None:
    """Carry the multilateral settings into the run's configuration.

    The multilateral series is not part of `run_pipeline`, so this changes
    nothing about the compiled bilateral result and a registered run
    reproduces byte-for-byte as before. What it changes is the record: a run
    registered after a multilateral compilation says which method, window
    and rule produced the series that was published alongside it, instead of
    leaving that in a session nobody can read back.
    """
    cfg = st.session_state.get("run_config")
    if cfg is None:
        return
    st.session_state["run_config"] = cfg.model_copy(update={"multilateral": settings})


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Multilateral index")
    st.caption(
        "A chained bilateral index on transaction data drifts: products churn, prices bounce "
        "between shelf and sale, quantities follow, and the chain accumulates the bounce as "
        "inflation. A multilateral method estimates every period in a window at once, so "
        "there is no path to drift along -- at the cost of a window that moves, and a rule "
        "for deciding how much of each re-estimation to publish.")

    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker(
            "Nobody has compiled a run yet. A compiler can do that on Ingest, or load a "
            "previously approved run below once one exists.")
        return
    res = analysis["result"]
    if "imputed" not in res:
        st.error("This run's data failed validation and has nothing to index.")
        return

    df = res["imputed"]
    try:
        panel = ml.build_panel(df)
    except ml.MultilateralError as exc:
        st.error(str(exc))
        return

    spec, chars = _hedonics_from_session()
    availability = ml.method_availability(panel, has_characteristics=spec is not None)
    usable = [m for m, reason in availability.items() if reason is None]

    c1, c2, c3 = st.columns(3)
    c1.metric("Periods in the collection", f"{panel.n_periods:,}")
    c2.metric("Distinct products", f"{panel.n_items:,}")
    c3.metric("Empty product-periods", f"{panel.churn_share:.1%}",
              help="Share of the period x product grid with no sale. This is the churn that "
                   "makes a matched-model method lose observations, and it is why the time "
                   "product dummy exists.")
    if panel.quantities is not None:
        st.caption(
            f"Quantities read from `{panel.quantity_source}`"
            + (" -- derived as expenditure / price, not reported directly."
               if panel.quantity_source == "quantity_derived" else "."))

    unavailable = {m: r for m, r in availability.items() if r is not None}
    if unavailable:
        with st.expander(f"{len(unavailable)} method(s) unavailable on this collection"):
            for method, reason in unavailable.items():
                st.markdown(f"- **{ml.METHOD_LABELS[method]}** — {reason}")
    if not usable:
        st.warning("No multilateral method can be computed on this collection.")
        return

    st.markdown("#### Compile")
    a, b, c = st.columns([2, 1, 2])
    method = a.selectbox("Method", usable, format_func=lambda m: ml.METHOD_LABELS[m],
                         key="ml_method")
    window = b.number_input("Window (periods)", min_value=2, max_value=max(2, panel.n_periods),
                            value=min(ml.DEFAULT_WINDOW, panel.n_periods), step=1,
                            key="ml_window",
                            help="Twenty-five months is the published default: two full years "
                                 "plus the month being added, so every seasonal month is seen "
                                 "twice. Longer windows are more stable and lean harder on the "
                                 "assumption that a product's quality held still throughout.")
    splice = c.selectbox("Extension rule", list(ml.SPLICES),
                         format_func=lambda s: ml.SPLICE_LABELS[s],
                         index=list(ml.SPLICES).index("mean"), key="ml_splice")

    if st.button("Compute", type="primary", key="ml_compute"):
        # Built as a config, not as seven loose arguments: this is what the
        # run is registered under, so the settings the series was computed
        # with and the settings a reader sees recorded are the same object.
        settings = MultilateralConfig(enabled=True, method=method, window=int(window),
                                      splice=splice)
        common.record("MULTILATERAL_INDEX", method,
                      {"window": settings.window, "splice": settings.splice,
                       "periods": panel.n_periods, "items": panel.n_items})
        try:
            result = ml.extend_from_config(df, settings, hedonic_spec=spec,
                                           characteristics=chars)
        except ml.MultilateralError as exc:
            st.error(str(exc))
            return
        st.session_state["ml_result"] = result
        _remember(settings)

    result = st.session_state.get("ml_result")
    if isinstance(result, ml.ExtensionResult):
        _show_result(df, result)

    st.divider()
    _show_headline(df, usable, spec, chars)

    st.divider()
    _show_comparison(df, usable, panel, spec, chars)


def _show_headline(df: pd.DataFrame, usable: list[str], spec: HedonicSpec | None,
                   chars: pd.DataFrame | None) -> None:
    """Roll the per-category multilateral series up to an all-items figure.

    The methods are elementary-level -- they compare products, and a
    category is where the products are -- so a headline needs the same
    weighted roll-up the bilateral index already uses. It is the same
    `engine.aggregation` code path, not a second one, which is why a
    category that cannot produce a series is named rather than dropped: a
    category missing from a weighted headline moves the headline.
    """
    st.markdown("#### Roll up to a headline")
    st.caption(
        "One multilateral series per category, aggregated the way the bilateral index is. "
        "What the aggregation does not preserve is transitivity: a weighted mean of "
        "transitive series is not itself the multilateral index of the pooled data. That is "
        "the ordinary compromise of publishing a multilateral elementary index inside a "
        "conventional structure, and it is why the method travels with the number.")
    a, b, c = st.columns([2, 1, 2])
    method = a.selectbox("Method ", usable, format_func=lambda m: ml.METHOD_LABELS[m],
                         key="ml_head_method")
    window = b.number_input("Window ", min_value=2, value=ml.DEFAULT_WINDOW, step=1,
                            key="ml_head_window")
    splice = c.selectbox("Extension rule ", list(ml.SPLICES),
                         format_func=lambda s: ml.SPLICE_LABELS[s],
                         index=list(ml.SPLICES).index("mean"), key="ml_head_splice")
    if not st.button("Compile the headline", key="ml_headline"):
        return

    settings = MultilateralConfig(enabled=True, method=method, window=int(window),
                                  splice=splice)
    common.record("MULTILATERAL_HEADLINE", method,
                  {"window": settings.window, "splice": settings.splice})
    try:
        aggregate = ml.build_multilateral_all(df, settings, hedonic_spec=spec,
                                              characteristics=chars)
    except ml.MultilateralError as exc:
        st.error(str(exc))
        return
    st.session_state["ml_aggregate"] = aggregate
    _remember(settings)

    headline = aggregate.headline.dropna()
    h1, h2, h3 = st.columns(3)
    h1.metric("All items, final period",
              "n/a" if headline.empty else f"{float(headline.iloc[-1]):.2f}")
    h2.metric("Categories compiled", f"{len(aggregate.results):,}")
    h3.metric("Weighted", "yes" if aggregate.weighted else "no, equally weighted")
    for problem in aggregate.problems:
        st.warning(problem)
    if aggregate.skipped:
        with st.expander(f"{len(aggregate.skipped)} category(ies) produced no series"):
            for name, reason in aggregate.skipped.items():
                st.markdown(f"- **{name}** — {reason}")

    st.line_chart(aggregate.indices)
    st.dataframe(aggregate.indices.tail(18).round(3), use_container_width=True)
    st.download_button(
        "Download the rolled-up series (CSV)",
        safe_csv_with_notice(aggregate.indices.reset_index(),
                             f"multilateral: {ml.METHOD_LABELS[method]}, {settings.window}-"
                             f"period window, {ml.SPLICE_LABELS[splice].lower()}"),
        file_name="multilateral_headline.csv", mime="text/csv", key="ml_headline_csv",
        help="The file names the method on its first line: a multilateral level without its "
             "method is not a number a reader can use.")


def _show_result(df: pd.DataFrame, result: ml.ExtensionResult) -> None:
    st.markdown(f"#### {ml.METHOD_LABELS.get(result.method, result.method)}, "
                f"{ml.SPLICE_LABELS[result.splice].lower()}, window {result.window}")
    for warning in result.warnings:
        st.warning(warning)

    series = result.index.dropna()
    frame = pd.DataFrame({"index": series,
                          "splice_spread_pp": result.splice_spread_pp.reindex(series.index)})
    st.line_chart(frame["index"])

    m1, m2, m3 = st.columns(3)
    m1.metric("Final level", f"{float(series.iloc[-1]):.2f}")
    m2.metric("Windows computed", f"{result.n_windows:,}")
    m3.metric("Largest splice spread", f"{float(result.splice_spread_pp.max()):.2f} pts",
              help="How far apart the published level could have been, at its worst period, "
                   "had the link been made at a different period of the overlap. This is the "
                   "size of the judgement the extension rule made -- not an error bar.")

    st.dataframe(frame, use_container_width=True)
    st.download_button(
        "Download the series (CSV)", safe_csv_with_notice(frame.reset_index()),
        file_name=f"multilateral_{result.method}_{result.splice}.csv", mime="text/csv",
        key="ml_download")

    st.markdown("**Against a chained bilateral index**")
    st.caption(
        "The same data chained period by period with a Tornqvist, scored against this "
        "transitive series. A chained index that ends above a transitive one did not observe "
        "the difference in the prices; it manufactured it at the joins.")
    try:
        report = ml.drift_against_chained(df, series, formula="tornqvist")
    except ValueError as exc:
        st.info(f"Chain drift could not be measured: {exc}")
        return
    (st.warning if report.exceeds_threshold else st.success)(report.message)


def _show_comparison(df: pd.DataFrame, usable: list[str], panel: ml.Panel,
                     spec: HedonicSpec | None, chars: pd.DataFrame | None) -> None:
    st.markdown("#### What the choice of method costs")
    st.caption(
        "The same collection under every available method and extension rule. The choice "
        "between them is a material judgement and this is its consequence, in the two units "
        "anyone argues about: index points of the final level, and percentage points of the "
        "annualised rate.")
    windows = sorted({min(ml.DEFAULT_WINDOW, panel.n_periods),
                      max(2, min(13, panel.n_periods))})
    if not st.button("Run the comparison", key="ml_compare",
                     help="Computes every method on every window and every extension rule; "
                          "on a long collection this is a few hundred window fits."):
        st.caption(
            f"Not run yet. It would compare {len(usable)} "
            f"method{'s' if len(usable) != 1 else ''} x {len(windows)} "
            f"window{'s' if len(windows) != 1 else ''} x {len(ml.SPLICES)} rules.")
        return

    common.record("MULTILATERAL_COMPARISON", ",".join(usable),
                  {"windows": windows, "splices": list(ml.SPLICES)})
    with st.spinner("Computing every method on every window..."):
        table = ml.method_comparison(df, methods=usable, windows=windows,
                                     splices=ml.SPLICES, hedonic_spec=spec,
                                     characteristics=chars)
    spread = ml.comparison_spread(table)
    st.session_state["ml_comparison"] = table

    s1, s2, s3 = st.columns(3)
    s1.metric("Combinations computed", f"{int(spread['n_computed']):,}")
    s2.metric("Spread in the final level", f"{spread['level_spread_pp']:.2f} pts")
    s3.metric("Spread in the annual rate",
              "n/a" if not np.isfinite(spread["rate_spread_pp"])
              else f"{spread['rate_spread_pp']:.2f} pts")

    shown = table[["method_label", "window", "splice_label", "final_level", "change_pct",
                   "annualised_pct", "splice_spread_pp", "n_windows", "error"]]
    st.dataframe(shown, use_container_width=True)
    st.download_button(
        "Download the comparison (CSV)", safe_csv_with_notice(shown),
        file_name="multilateral_comparison.csv", mime="text/csv", key="ml_comparison_download")
