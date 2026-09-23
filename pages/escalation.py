"""Contract escalation: a payment schedule under an indexation clause, and
the clause restated in plain language.

The summary is shown before the schedule, because the risk in escalation is
not the arithmetic but a reader misunderstanding it. It names the index,
which vintage of it was used, the lag, and every period in which a cap,
collar, dead band or trigger changed the payment.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import escalation as es

from . import common


def _index_choice() -> tuple[pd.Series, str, str] | None:
    """The index series, its name, and its vintage -- the vintage stated as
    precisely as the source allows, never left blank."""
    options: list[str] = []
    analysis = common.get_active_analysis()
    indices = analysis["result"].get("indices") if analysis is not None else None
    if indices is not None:
        options.append("A series of the compiled index")
    held = st.session_state.get("source_result")
    if held is not None and "period" in held["result"].data.columns:
        options.append("The official series fetched on Sources")
    options.append("Upload an index (CSV: period, value)")
    choice = st.radio("Index", options, key="es_source")

    if choice == "A series of the compiled index":
        assert analysis is not None and indices is not None
        column = st.selectbox("Series", list(indices.columns), key="es_series",
                              index=list(indices.columns).index("All items")
                              if "All items" in indices.columns else 0)
        run_id = st.session_state.get("loaded_run_id")
        vintage = (f"registered run {run_id}, as loaded from the registry" if run_id else
                   f"this session's compilation of '{analysis['label']}', not a registered "
                   "vintage -- register and approve the run before using it in a contract")
        return indices[column].dropna(), f"the {column} index", vintage
    if choice == "The official series fetched on Sources":
        result = held["result"]
        data = result.data
        series = data.set_index(pd.to_datetime(data["period"]))["value"].astype(float)
        v = result.vintage
        return (series.sort_index(), f"{held['source']} series {v.query}",
                f"retrieved {v.retrieved_at:%Y-%m-%d %H:%M} UTC, response hash "
                f"{v.response_hash[:16]}")
    upload = st.file_uploader("Index (CSV: period, value)", type=["csv"], key="es_file")
    if upload is None:
        return None
    try:
        frame = common.read_upload_table(upload, ("period", "value"))
    except ValueError as exc:
        st.error(str(exc))
        return None
    name = st.text_input("Index name, as the contract gives it", upload.name, key="es_name")
    vintage = st.text_input("Which vintage (publication date or release) of the index",
                            "", key="es_vintage",
                            help="Required: an index revised after the contract was priced "
                                 "gives a different payment.")
    if not vintage.strip():
        st.warning("State which vintage of the index this is before building the schedule.")
        return None
    series = pd.Series(frame["value"].to_numpy(dtype=float),
                       index=pd.DatetimeIndex(frame["period"])).sort_index()
    return series, name, vintage.strip()


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Contract escalation")
    st.caption("Apply an indexation clause to a payment schedule. The clause is restated in "
               "words above the schedule, so it can be checked against the contract wording "
               "without reading any code.")
    chosen = _index_choice()
    if chosen is None:
        return
    series, index_name, vintage = chosen
    periods = list(pd.DatetimeIndex(series.index))
    fmt: Any = lambda p: f"{pd.Timestamp(p):%b %Y}"      # noqa: E731

    c1, c2, c3 = st.columns(3)
    amount = c1.number_input("Base amount per payment", 0.01, 1e12, 100_000.0, key="es_amount")
    currency = c2.text_input("Currency", "GBP", key="es_currency")
    base = c3.selectbox("Base (tender) index period", periods, key="es_base", format_func=fmt)
    d1, d2, d3 = st.columns(3)
    lag = int(d1.number_input("Lag (periods)", 0, 24, 0, key="es_lag"))
    averaging = int(d2.number_input("Average the index over (periods)", 1, 12, 1,
                                    key="es_averaging"))
    factor = d3.slider("Indexed share of the amount", 0.0, 1.0, 1.0, 0.05, key="es_factor")
    e1, e2, e3, e4 = st.columns(4)
    band = e1.number_input("Dead band, ±%", 0.0, 50.0, 0.0, key="es_band")
    band_mode = e2.selectbox("Beyond the band", ["excess", "full"], key="es_band_mode",
                             format_func=lambda m: {"excess": "pass on only the excess",
                                                    "full": "pass on the whole movement"}[m])
    trigger = e3.number_input("Trigger, %", 0.0, 50.0, 0.0, key="es_trigger")
    use_cap = e4.checkbox("Cap", key="es_use_cap")
    f1, f2, f3 = st.columns(3)
    cap = f1.number_input("Cap, % above base", 0.0, 100.0, 5.0, key="es_cap")
    use_collar = f2.checkbox("Collar", key="es_use_collar")
    collar = f3.number_input("Collar, % below base", 0.0, 100.0, 5.0, key="es_collar")
    start_candidates = [p for p in periods if p > pd.Timestamp(base)] or periods
    first = st.selectbox("First payment period", start_candidates, key="es_first", format_func=fmt)
    count = int(st.number_input("Number of monthly payments", 1, 240, 12, key="es_count"))
    if not st.button("Build the schedule", type="primary", key="es_go"):
        return

    try:
        clause = es.EscalationClause(
            base_amount=float(amount), index_name=index_name, index_vintage=vintage,
            base_period=pd.Timestamp(base), lag=lag, averaging=averaging,
            indexation_factor=float(factor), dead_band_pct=float(band),
            dead_band_mode=band_mode, trigger_pct=float(trigger),
            cap_pct=float(cap) if use_cap else None,
            collar_pct=float(collar) if use_collar else None, currency=currency.strip())
        result = es.apply_clause(clause, series,
                                 pd.date_range(pd.Timestamp(first), periods=count, freq="MS"))
    except es.EscalationError as exc:
        st.error(str(exc))
        return
    st.session_state["es_result"] = result
    common.record(audit.ESCALATION, index_name, {
        "vintage": vintage, "lag": lag, "averaging": averaging, "factor": factor,
        "cap": clause.cap_pct, "collar": clause.collar_pct, "dead_band": clause.dead_band_pct,
        "trigger": clause.trigger_pct, "payments": count,
        "bound": int((result.schedule["limit_bound"] != "").sum())})

    st.markdown("#### The clause as applied")
    for line in result.summary.split("\n"):
        st.markdown(line)
    st.markdown("#### Payment schedule")
    shown = result.schedule.copy()
    for column in ("payment", "payment_without_cap_or_collar"):
        shown[column] = shown[column].round(2)
    st.dataframe(shown, use_container_width=True)
    st.download_button(
        "Download the schedule (CSV)",
        safe_csv_with_notice(result.schedule.reset_index(),
                             f"{index_name}, vintage: {vintage}; lag {lag}; base "
                             f"{pd.Timestamp(base):%b %Y}"),
        file_name="payment_schedule.csv", mime="text/csv", key="es_csv")
    st.download_button("Download the clause summary (text)", result.summary,
                       file_name="clause_summary.txt", mime="text/plain", key="es_txt")
