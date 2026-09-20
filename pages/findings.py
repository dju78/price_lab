"""Findings: the headline, the ranked findings, and the faults-repaired
count. Moved from the original "Findings" tab, unchanged. Reachable by
analysts and viewers as well as compilers: interpreting a compiled run is
not the same activity as compiling one, so it carries a wider audience."""

from __future__ import annotations

import streamlit as st

from pricelab import annualised_rate, build_all_charts, years_span
from pricelab.core.models import Role
from pricelab.core.security import require_role

from . import common


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Findings")
    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker(
            "Nobody has compiled a run yet. A compiler can do that on Ingest, "
            "or load a previously approved run below once one exists.")
        return

    res, nar = analysis["result"], analysis["narrative"]
    if nar is None:
        st.error("This run's data failed validation and produced no findings.")
        return
    charts = build_all_charts(res)
    I, yoy = res["indices"], res["inflation"]
    years = years_span(I.index)

    st.markdown(f"#### {nar.headline}")
    st.caption(nar.subtitle)

    m = st.columns(4)
    if "All items" in I.columns:
        m[0].metric(f"All items, {I.index[0]:%b %Y} = 100", f"{I['All items'].iloc[-1]:.1f}")
        rate = annualised_rate(I["All items"].iloc[-1] / 100, years)
        m[1].metric("Average annual rate", f"{rate:.1f}%" if years > 0 else "n/a (single period)")
        s_ = yoy["All items"].dropna()
        if len(s_):
            m[2].metric("Peak rate", f"{s_.max():.1f}%", f"{s_.idxmax():%b %Y}",
                        delta_color="off")
    n_repaired = res["quality"]["scale_errors_repaired"]
    n_detected = res["quality"]["scale_errors_detected"]
    m[3].metric("Faults repaired" if n_repaired == n_detected else "Faults repaired / detected",
                f"{n_repaired:,}" if n_repaired == n_detected else f"{n_repaired:,} / {n_detected:,}")
    st.markdown("")

    for f in nar.findings:
        st.markdown(f'<div class="pl-eyebrow">{common.KIND_LABEL.get(f.kind, f.kind)}</div>',
                    unsafe_allow_html=True)
        st.markdown(f"##### {f.headline}")
        st.write(f.detail)
        if f.evidence:
            st.markdown(f'<div class="pl-ev">{f.evidence}</div>', unsafe_allow_html=True)
        if f.chart and f.chart in charts:
            st.pyplot(charts[f.chart], use_container_width=True)
        if f.table is not None and len(f.table):
            with st.expander("Supporting figures"):
                st.dataframe(f.table, use_container_width=True, hide_index=True)
        if f.action:
            st.info(f.action, icon="➡️")
        st.divider()
