"""Forecasts: the headline projected by ARIMA, SARIMAX, exponential
smoothing, or a pass-through or Phillips-curve regression.

Each forecast is shown with its benchmark verdict first -- whether it beats a
random walk (or, for a seasonal series, the seasonal naive) on the same
backtest -- then its chart, whose title repeats the verdict, then its
backtest against the interval it claims. A model that loses to the benchmark
says so above its own number. Regression coefficients are shown only beneath
the statement that they are correlational.

Nothing leaves this page except through `reporting/projections`, which
refuses a forecast missing its interval, backtest, benchmark comparison or
assumptions.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit, db
from pricelab.core.models import Role
from pricelab.core.security import require_role
from pricelab.engine import forecasting as fc
from pricelab.engine.projection import ProjectionIncomplete

from . import common

STATE = "fc_result"
NEEDS_DRIVER = ("pass_through", "phillips")


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Forecasts")
    st.caption("A forecast is a statement about a model, not a fact about the future. Each one "
               "here is backtested against a naive benchmark on the same periods, and shown "
               "with the interval its model claims beside the error its backtest measured.")
    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker("Compile a run on Ingest, or load an approved run.")
        return
    res = analysis["result"]
    if "indices" not in res:
        st.error("This run's data failed validation and has no headline to forecast.")
        return
    label = str(analysis.get("label", ""))
    indices: pd.DataFrame = res["indices"]
    series_name = "All items" if "All items" in indices.columns else str(indices.columns[0])
    series = indices[series_name]

    c1, c2, c3, c4 = st.columns(4)
    method = c1.selectbox("Method", list(fc.METHODS), format_func=fc.METHODS.get,
                          key="fc_method")
    horizon = int(c2.number_input("Horizon (months)", 1, 36, 12, key="fc_horizon"))
    origins = int(c3.number_input("Backtest origins", 6, 60, 24, key="fc_origins"))
    level = float(c4.selectbox("Interval", [0.80, 0.90, 0.95], index=2, key="fc_level",
                               format_func=lambda v: f"{v:.0%}"))
    driver: pd.Series | None = None
    driver_name, driver_source = "", ""
    if method in (*NEEDS_DRIVER, "sarimax"):
        st.markdown("**Driver series**" + ("" if method in NEEDS_DRIVER else " (optional)"))
        st.caption({
            "pass_through": "A price level (energy prices, import prices, an exchange rate) "
                            "whose monthly changes are regressed against the headline's.",
            "phillips": "A measure of slack (an unemployment rate, an output gap) whose level "
                        "is regressed against the headline's monthly rate.",
            "sarimax": "An external regressor for the SARIMAX model; without one it is a "
                       "seasonal ARIMA.",
        }[method] + " A CSV with period and value columns, covering every month of the run.")
        upload = st.file_uploader("Driver CSV", type=["csv"], key="fc_driver")
        d1, d2 = st.columns(2)
        driver_name = d1.text_input("What the driver is", key="fc_driver_name")
        driver_source = d2.text_input("Where it came from", key="fc_driver_source")
        if upload is not None:
            try:
                table = common.read_upload_table(upload, ("period", "value"))
            except ValueError as exc:
                st.error(str(exc))
                return
            driver = pd.Series(table["value"].to_numpy(dtype=float),
                               index=pd.DatetimeIndex(table["period"]),
                               name=driver_name or "driver")
        elif method in NEEDS_DRIVER:
            st.info(f"The {fc.METHODS[method].lower()} needs a driver series.")
            return
        if driver is not None and not driver_source.strip():
            st.warning("Say where the driver series came from: it is one of the forecast's "
                       "assumptions, and a forecast with an unstated assumption cannot be "
                       "exported.")

    if st.button("Produce the forecast", type="primary", key="fc_go"):
        spec = fc.ForecastSpec(method=method, horizon=horizon, origins=origins, level=level,
                               driver_name=driver_name, driver_source=driver_source)
        with st.spinner("Selecting the model, backtesting it and its benchmark…"):
            try:
                result = fc.forecast(series, spec, driver=driver)
            except fc.ForecastError as exc:
                st.error(str(exc))
                return
        st.session_state[STATE] = {"label": label, "result": result, "driver": driver}
        common.record(audit.FORECAST, label, {
            "method": method, "specification": result.specification, "horizon": horizon,
            "origins": origins, "beats_benchmark": result.beats_benchmark,
            "understates_uncertainty": result.backtest.understates})
    held = st.session_state.get(STATE)
    if held is None or held.get("label") != label:
        return
    _show(held["result"], held.get("driver"), res, label)


def _show(result: fc.ForecastResult, driver: pd.Series | None, res: dict[str, Any],
          label: str) -> None:
    from pricelab.reporting.charts import forecast_chart

    # The verdict first, in the same place as the number.
    if result.beats_benchmark:
        st.success(result.benchmark.statement)
    else:
        st.warning(result.benchmark.statement)
    st.markdown(result.headline)
    st.pyplot(forecast_chart(result), use_container_width=True)
    if result.backtest.understates:
        st.warning(result.backtest.statement)
    else:
        st.info(result.backtest.statement)
    st.dataframe(result.backtest.by_horizon.round(3), use_container_width=True)

    if result.regression is not None:
        st.markdown("**Regression coefficients**")
        st.caption(fc.CORRELATIONAL)
        st.dataframe(result.regression.coefficients.round(4), use_container_width=True,
                     hide_index=True)
        st.caption(f"Long-run association (sum of the driver terms over one minus the "
                   f"persistence): {result.regression.long_run:.4f}; R-squared "
                   f"{result.regression.r_squared:.3f} on {result.regression.observations} "
                   f"months. {fc.CORRELATIONAL}")

    st.markdown("**Residual diagnostics**")
    st.dataframe(result.diagnostics, use_container_width=True, hide_index=True)
    for note in result.warnings:
        st.caption(f"Diagnostic: {note}. The model-implied interval assumes this holds.")
    st.markdown("**Assumptions (part of the forecast)**")
    st.dataframe(pd.DataFrame([a.as_row() for a in result.assumptions]),
                 use_container_width=True, hide_index=True)
    _exports(result, driver, res, label)


def _exports(projection: Any, driver: pd.Series | None, res: dict[str, Any], label: str,
             prefix: str = "fc") -> None:
    """Downloads, each through `reporting/projections`, and registration."""
    from pricelab.core.provenance import build_stamp
    from pricelab.reporting import projections

    stamp = build_stamp(res, label)
    try:
        files = {
            "csv": projections.projection_csv(projection, stamp).encode("utf-8"),
            "xlsx": projections.projection_workbook(projection, stamp),
            "md": projections.projection_markdown(projection, stamp).encode("utf-8"),
        }
    except ProjectionIncomplete as exc:
        st.error(str(exc))
        return
    mimes = {"csv": "text/csv", "md": "text/markdown",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    columns = st.columns(3)
    for column, (extension, data) in zip(columns, files.items(), strict=True):
        if column.download_button(f"Download ({extension})", data,
                                  f"{projection.kind}_{label or 'run'}.{extension}",
                                  mimes[extension], key=f"{prefix}_dl_{extension}"):
            common.record(audit.PROJECTION_EXPORT, label, {"kind": projection.kind,
                                                            "format": extension})
    run_id = st.session_state.get("last_run_id") or st.session_state.get("loaded_run_id")
    if st.button(f"Register this {projection.kind}", key=f"{prefix}_register"):
        from pricelab.core import registry

        df = st.session_state.get("input_df")
        cfg = st.session_state.get("run_config")
        with db.session_scope() as s:
            if run_id is None:
                if df is None or cfg is None:
                    st.error("Register the run first: the projection is reproducible only "
                             "from a registered run.")
                    return
                run_id = registry.register_run(s, df, cfg, label).run_id
            row = registry.register_projection(s, run_id, projection, common.current_username(),
                                               driver=driver)
            projection_id = row.projection_id
        st.success(f"Registered as {projection.kind} {projection_id} against run {run_id}; "
                   "the registry can rebuild it and check its backtest to the last digit.")
