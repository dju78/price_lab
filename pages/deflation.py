"""Deflation: real values, real wages and income, constant prices and
volumes, and purchasing power parity conversion.

Every result names its deflator and the reference period its prices are
expressed in, on screen, in the chart and on the first line of the
download. A nominal series and a deflator of different frequencies are not
resampled behind the user's back: the page shows the error, and a
conversion happens only when the user picks one, after which it is recorded
with the result.
"""

from __future__ import annotations

import io
from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import deflation as df_

from . import common

KINDS = {"Real value": "real value", "Real wage": "real wage", "Real income": "real income",
         "Constant prices and a volume index": "constant price value"}
CONVERSIONS = {"No conversion (the frequencies must already match)": None,
               "Nominal series to the deflator's frequency, by its average": "mean",
               "Nominal series to the deflator's frequency, by its sum (a flow)": "sum",
               "Nominal series to the deflator's frequency, by its last value (a stock)": "last"}


def read_series(upload: Any) -> pd.Series:
    """A two-column CSV (period, value) as a Series indexed by period.

    The header row is optional; the first column must parse as dates and
    the second as numbers, and a file that does not is refused with the
    reason rather than guessed at.
    """
    raw = pd.read_csv(io.BytesIO(upload.getvalue()))
    if raw.shape[1] < 2:
        raise ValueError("the file needs two columns: the period and the value")
    periods = pd.to_datetime(raw.iloc[:, 0], errors="coerce")
    values = pd.to_numeric(raw.iloc[:, 1], errors="coerce")
    if periods.isna().any() or values.isna().any():
        bad = int(periods.isna().sum() + values.isna().sum())
        raise ValueError(f"{bad} cell(s) in the file are not a date and a number")
    return pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(periods),
                     name=str(raw.columns[1])).sort_index()


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Deflation")
    st.caption(
        "A real value is a nominal value divided by a price index relative to its level in a "
        "reference period, and it means nothing without the name of that index and that "
        "period. Both are stated with every result here.")
    _deflate()
    _ppp()


def _deflator_choice() -> tuple[pd.Series, str] | None:
    options: list[str] = []
    analysis = common.get_active_analysis()
    indices = (analysis["result"].get("indices") if analysis is not None else None)
    if indices is not None:
        options.append("A series of the compiled index")
    held = st.session_state.get("source_result")
    if held is not None and "period" in held["result"].data.columns:
        options.append("The official series fetched on Sources")
    options.append("Upload a deflator (CSV: period, value)")
    choice = st.radio("Deflator", options, key="df_deflator_source")
    if choice == "A series of the compiled index":
        assert indices is not None
        column = st.selectbox("Index series", list(indices.columns),
                              index=list(indices.columns).index("All items")
                              if "All items" in indices.columns else 0, key="df_index_series")
        return indices[column].dropna(), f"the compiled {column} index ({analysis['label']})"
    if choice == "The official series fetched on Sources":
        data = held["result"].data
        official = data.set_index(pd.to_datetime(data["period"]))["value"].astype(float)
        return official.sort_index(), f"{held['source']} ({held['result'].vintage.query})"
    upload = st.file_uploader("Deflator (CSV: period, value)", type=["csv"], key="df_deflator_file")
    if upload is None:
        return None
    try:
        return read_series(upload), st.text_input("Deflator name", upload.name,
                                                  key="df_deflator_name")
    except ValueError as exc:
        st.error(f"Deflator: {exc}")
        return None


def _deflate() -> None:
    st.markdown("#### Deflate a nominal series")
    upload = st.file_uploader("Nominal series (CSV: period, value)", type=["csv"],
                              key="df_nominal_file")
    if upload is None:
        st.info("Upload the nominal series to deflate: earnings, income, sales, expenditure.")
        return
    try:
        nominal = read_series(upload)
    except ValueError as exc:
        st.error(f"Nominal series: {exc}")
        return
    nominal_name = st.text_input("What the nominal series is", str(nominal.name or "nominal"),
                                 key="df_nominal_name")
    chosen = _deflator_choice()
    if chosen is None:
        return
    deflator, deflator_name = chosen

    c1, c2 = st.columns(2)
    kind_name = c1.selectbox("Result", list(KINDS), key="df_kind")
    conversion_name = c2.selectbox("Frequency conversion", list(CONVERSIONS), key="df_conversion")
    periods = list(pd.DatetimeIndex(deflator.index))
    years = sorted({p.year for p in periods})
    references = [f"{p:%Y-%m}" for p in periods] + [str(y) for y in years]
    reference = st.selectbox("Express in the prices of", references,
                             index=len(periods) - 1, key="df_reference",
                             help="A month, or a whole year (the deflator's average over it).")
    allow_partial = st.checkbox("Deflate only the periods the deflator covers", value=False,
                                key="df_partial")
    if not st.button("Deflate", type="primary", key="df_go"):
        return

    how = CONVERSIONS[conversion_name]
    conversion_note = ""
    try:
        if how is not None:
            target = df_.frequency_of(deflator, deflator_name)
            source = df_.frequency_of(nominal, nominal_name)
            nominal = df_.to_frequency(nominal, target, how=how)
            conversion_note = (f"{nominal_name} converted from {df_.FREQUENCY_NAMES[source]} to "
                               f"{df_.FREQUENCY_NAMES[target]} by its {how}, at the user's choice")
        result = df_.deflate(nominal, deflator,
                             reference=reference if reference.isdigit() else pd.Timestamp(reference),
                             nominal_name=nominal_name, deflator_name=deflator_name,
                             allow_partial=allow_partial, kind=KINDS[kind_name],
                             conversion_note=conversion_note)
    except df_.DeflationError as exc:
        st.error(str(exc))
        return
    st.session_state["df_result"] = result
    common.record(audit.DEFLATION, nominal_name, {
        "deflator": deflator_name, "reference": result.reference, "kind": result.kind,
        "frequency": result.frequency, "conversion": how or "none",
        "periods": int(result.real.notna().sum())})

    from pricelab.reporting.charts import deflation_chart

    st.success(result.label[:1].upper() + result.label[1:] + ".")
    for note in result.notes:
        st.caption(note)
    st.pyplot(deflation_chart(result), use_container_width=True)
    common.show_uncertainty(
        "The real series", run_headline=False,
        reason="the deflator's sampling uncertainty is not carried into the real values")
    st.dataframe(result.frame.tail(24).round(4), use_container_width=True)
    growth = df_.real_growth(result, horizon=1)
    st.markdown("##### Growth, nominal and real")
    st.caption("Real growth is (1 + nominal) / (1 + inflation) - 1, exactly. Nominal minus "
               "inflation is shown beside it with its error, which grows with inflation.")
    st.dataframe(growth.tail(13).round(4), use_container_width=True)
    if result.kind == "constant price value":
        try:
            volume = df_.volume_index(result)
        except df_.DeflationError as exc:
            st.warning(str(exc))
        else:
            st.markdown(f"##### {volume.name}")
            st.line_chart(volume)
    st.download_button(
        "Download (CSV)", safe_csv_with_notice(result.frame.reset_index(), result.label),
        file_name="deflated.csv", mime="text/csv", key="df_csv",
        help="The first line names the deflator and the reference period.")


def _ppp() -> None:
    st.divider()
    st.markdown("#### Purchasing power parity conversion")
    st.caption("Local-currency values divided by PPPs (local currency per international "
               "dollar) compare volumes across countries rather than exchange rates. PPPs are "
               "annual; holding a year's PPP through its months is a choice you make here, not "
               "a default.")
    values_upload = st.file_uploader("Values in local currency (CSV: period, value)", type=["csv"],
                                     key="df_ppp_values")
    ppp_upload = st.file_uploader("PPPs (CSV: period, value)", type=["csv"], key="df_ppp_file")
    if values_upload is None or ppp_upload is None:
        return
    broadcast = st.checkbox("Hold each year's PPP constant through the year", value=False,
                            key="df_ppp_broadcast")
    if not st.button("Convert", key="df_ppp_go"):
        return
    try:
        result = df_.ppp_convert(read_series(values_upload), read_series(ppp_upload),
                                 value_name=values_upload.name, ppp_name=ppp_upload.name,
                                 broadcast_annual=broadcast)
    except (ValueError, df_.DeflationError) as exc:
        st.error(str(exc))
        return
    st.session_state["df_ppp_result"] = result
    common.record(audit.DEFLATION, values_upload.name, {"ppp": ppp_upload.name,
                                                        "broadcast_annual": broadcast})
    st.success(result.label[:1].upper() + result.label[1:] + ".")
    st.dataframe(pd.DataFrame({"local currency": result.values, "PPP": result.ppp,
                               result.currency: result.converted}).tail(24),
                 use_container_width=True)
    rate_upload = st.file_uploader(
        "Market exchange rate, local currency per unit of the PPPs' reference currency "
        "(CSV: period, value), for the price level index", type=["csv"], key="df_ppp_rate")
    if rate_upload is None:
        return
    try:
        pli = df_.price_level_index(result.ppp, read_series(rate_upload))
    except (ValueError, df_.DeflationError) as exc:
        st.error(str(exc))
        return
    st.session_state["df_pli"] = pli
    st.caption("Price level index: the PPP over the exchange rate, times 100. Above 100 the "
               "country is dearer than the reference, below 100 cheaper.")
    st.dataframe(pli.round(2).tail(24), use_container_width=True)
