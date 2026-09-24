"""Scenarios: the headline path if stated shocks to energy prices, the
exchange rate, wages or administered prices occur.

A scenario is not a forecast, and this page never calls one that. Its
assumption list is shown directly under the chart as part of the result:
every shock's size, timing and phase-in, and the coefficient applied with
its source. A scenario with any assumption unstated is shown, marked, and
cannot be exported.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role
from pricelab.engine import scenarios as sc

from . import common
from .forecasting import STATE as FORECAST_STATE
from .forecasting import _exports

STATE = "sc_result"
ENTERED = "Enter a coefficient and its source"
WEIGHTS = "The weight of chosen categories (direct effect only)"
REGRESSION = "The long-run coefficient of the pass-through regression on the Forecasts page"


def _slug(driver: str) -> str:
    return driver.replace(" ", "_")


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Scenarios")
    st.caption("A scenario is not a forecast. It shows what the headline would do if the "
               "stated shocks occurred, passing through at the stated rates, and says nothing "
               "about whether they will. Its assumptions are part of it.")
    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker("Compile a run on Ingest, or load an approved run.")
        return
    res = analysis["result"]
    if "indices" not in res:
        st.error("This run's data failed validation and has no headline to build on.")
        return
    label = str(analysis.get("label", ""))
    indices: pd.DataFrame = res["indices"]
    series_name = "All items" if "All items" in indices.columns else str(indices.columns[0])
    categories = [c for c in indices.columns if c != "All items"]

    c1, c2, c3 = st.columns(3)
    name = c1.text_input("Scenario name", "Scenario", key="sc_name")
    horizon = int(c2.number_input("Horizon (months)", 1, 48, 24, key="sc_horizon"))
    origins = int(c3.number_input("Backtest origins for the fan", 12, 96, 36, key="sc_origins"))

    held_forecast = st.session_state.get(FORECAST_STATE)
    regression = (held_forecast["result"] if held_forecast is not None
                  and held_forecast.get("label") == label
                  and held_forecast["result"].method == "pass_through" else None)
    shocks: list[sc.Shock] = []
    for driver, meaning in sc.DRIVERS.items():
        slug = _slug(driver)
        if not st.checkbox(f"A shock to {driver}", key=f"sc_{slug}_on"):
            continue
        with st.container(border=True):
            st.caption(f"A permanent change in {meaning}.")
            a, b, c = st.columns(3)
            size = a.number_input("Size of the change, %", -90.0, 500.0, 10.0,
                                  key=f"sc_{slug}_size")
            start = int(b.number_input("Starts in projected month", 1, horizon, 1,
                                       key=f"sc_{slug}_start"))
            phase = int(c.number_input("Passes through over (months)", 1, 36, 1,
                                       key=f"sc_{slug}_phase"))
            bases = [ENTERED, WEIGHTS] + ([REGRESSION] if regression is not None else [])
            basis = st.radio("Coefficient", bases, key=f"sc_{slug}_basis")
            coefficient: float | None
            source = ""
            if basis == ENTERED:
                e1, e2 = st.columns([1, 3])
                coefficient = float(e1.number_input(
                    "Elasticity of the headline to the driver", -5.0, 5.0, 0.0, step=0.01,
                    format="%.4f", key=f"sc_{slug}_coef"))
                source = e2.text_input("Source of the coefficient", key=f"sc_{slug}_source")
                if not source.strip():
                    st.warning("Say where this coefficient comes from. Without a source the "
                               "assumption is unstated and the scenario cannot be exported.")
            elif basis == WEIGHTS:
                chosen = st.multiselect("Categories whose prices the driver is",
                                        categories, key=f"sc_{slug}_cats")
                try:
                    coefficient, source = sc.coefficient_from_weights(res, chosen, label)
                except sc.ScenarioError as exc:
                    st.info(str(exc))
                    coefficient = None
            else:
                assert regression is not None
                coefficient, source = sc.coefficient_from_regression(regression)
            if coefficient is not None:
                st.caption(f"Coefficient {coefficient:.4f}: {source or '(no source given)'}")
            shocks.append(sc.Shock(driver, float(size), start, coefficient, source, phase))

    if st.button("Build the scenario", type="primary", key="sc_go"):
        try:
            result = sc.build_scenario(indices[series_name], shocks, name=name,
                                       horizon=horizon, origins=origins)
        except sc.ScenarioError as exc:
            st.error(str(exc))
            return
        st.session_state[STATE] = {"label": label, "result": result}
        common.record(audit.SCENARIO, label, {
            "name": name, "shocks": [s.driver for s in shocks],
            "unstated": result.unstated})
    held = st.session_state.get(STATE)
    if held is None or held.get("label") != label:
        return
    _show(held["result"], res, label)


def _show(result: sc.ScenarioResult, res: dict[str, Any], label: str) -> None:
    from pricelab.reporting import projections
    from pricelab.reporting.charts import scenario_chart

    st.info(result.label)
    st.pyplot(scenario_chart(result), use_container_width=True)
    st.markdown("**Assumptions (part of the scenario)**")
    table = pd.DataFrame([a.as_row() for a in result.assumptions])
    table["stated"] = [a.stated for a in result.assumptions]
    st.dataframe(table, use_container_width=True, hide_index=True)
    if result.unstated:
        st.error("Unstated: " + ", ".join(result.unstated) + ". This scenario cannot be "
                 "exported until every assumption has a value and a source.")
    st.markdown("**Scenario path, fan and each shock's contribution**")
    st.dataframe(projections.path_table(result).round(3), use_container_width=True,
                 hide_index=True)
    st.caption(result.benchmark.statement + " " + result.backtest.statement)

    held_forecast = st.session_state.get(FORECAST_STATE)
    if held_forecast is not None and held_forecast.get("label") == label:
        st.markdown("**Beside the forecast** (two different things, each column labelled)")
        try:
            st.dataframe(projections.side_by_side(held_forecast["result"], result).round(3),
                         use_container_width=True, hide_index=True)
        except Exception as exc:        # noqa: BLE001 - an incomplete one is refused with why
            st.caption(str(exc))
    _exports(result, None, res, label, prefix="sc")
