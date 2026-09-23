"""Construction: the input cost index and the output price index, with
what each measures stated before either number, because they answer
different questions and are routinely confused."""

from __future__ import annotations

import streamlit as st

from pricelab.core import audit
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import construction as cn

from . import common


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Construction prices")
    st.info(cn.CONCEPTS["input_cost"])
    st.info(cn.CONCEPTS["output_price"])
    st.caption("Where the two diverge, neither is wrong: the gap is the implied movement in "
               "contractors' margins and productivity together.")

    inputs_upload = st.file_uploader(
        "Input price indices and cost shares (CSV: period, input, index, share)", type=["csv"],
        key="cn_inputs")
    outputs_upload = st.file_uploader(
        "Tender rates and bill of quantities (CSV: period, item, rate, quantity)", type=["csv"],
        key="cn_outputs")
    if inputs_upload is None and outputs_upload is None:
        return
    if not st.button("Compile", type="primary", key="cn_go"):
        return

    built: dict[str, cn.ConstructionIndex] = {}
    try:
        if inputs_upload is not None:
            frame = common.read_upload_table(inputs_upload, ("period", "input", "index", "share"))
            wide = frame.pivot_table(index="period", columns="input", values="index")
            shares = frame.groupby("input")["share"].first().to_dict()
            built["input_cost"] = cn.input_cost_index(wide, shares)
        if outputs_upload is not None:
            frame = common.read_upload_table(outputs_upload, ("period", "item", "rate", "quantity"))
            bill = frame.groupby("item")["quantity"].first().to_dict()
            built["output_price"] = cn.output_price_index(frame[["period", "item", "rate"]], bill)
    except (ValueError, cn.ConstructionError) as exc:
        st.error(str(exc))
        return
    st.session_state["cn_built"] = built
    common.record(audit.CONSTRUCTION_INDEX, "construction", {"indices": sorted(built)})

    for kind, result in built.items():
        st.markdown(f"#### {'Input cost index' if kind == 'input_cost' else 'Output price index'}")
        st.caption(result.concept)
        common.show_uncertainty(
            result.label.split(",")[0], run_headline=False,
            reason="the input indices and tender rates arrive with no sampling design")
        st.line_chart(result.index)
        for note in result.notes:
            st.caption(note)
        st.download_button(
            f"Download the {kind.replace('_', ' ')} index (CSV)",
            safe_csv_with_notice(result.index.reset_index(), result.label),
            file_name=f"construction_{kind}.csv", mime="text/csv", key=f"cn_csv_{kind}")
    if len(built) == 2 and built["input_cost"].base == built["output_price"].base:
        gap = cn.input_output_gap(built["input_cost"], built["output_price"])
        st.session_state["cn_gap"] = gap
        st.markdown("#### Output prices over input costs")
        st.dataframe(gap.round(3), use_container_width=True)
    elif len(built) == 2:
        st.caption("The two indices have different base periods, so they are not compared.")
