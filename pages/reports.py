"""Reports: the method note, the exportable deck and report, and the run
registry's register/approve controls. Moved from the original "Method note"
tab and the download row above the original tabs, unchanged in what they
produce.

Open to every role, including viewer: reading the method note and
reproducing a published figure is exactly the auditor's job in the
platform's audience list, and it requires no ability to change anything.
"""

from __future__ import annotations

import streamlit as st

from pricelab import build_all_charts, build_deck, build_docx, build_markdown, method_note
from pricelab.core import audit, db
from pricelab.core.models import Role
from pricelab.core.registry import approve_run, register_run
from pricelab.core.security import current_role, safe_csv

from . import common


def render() -> None:
    st.markdown("### Reports")
    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker(
            "Nobody has compiled a run yet. A compiler can do that on Ingest, "
            "or load a previously approved run below once one exists.")
        return

    res, nar = analysis["result"], analysis["narrative"]
    label = analysis.get("label", "analysis")

    if "indices" in res and nar is not None:
        charts = build_all_charts(res)
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            if st.download_button(
                "Slide deck", build_deck(res, nar, dict(charts), label),
                f"{label} analysis.pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                use_container_width=True, type="primary"):
                common.record(audit.EXPORT, f"{label} analysis.pptx")
        with d2:
            if st.download_button(
                "Written report", build_docx(res, nar, dict(charts), label),
                f"{label} report.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True):
                common.record(audit.EXPORT, f"{label} report.docx")
        with d3:
            if st.download_button(
                "Cleaned data", safe_csv(res["imputed"], index=False), f"{label} cleaned.csv",
                "text/csv", use_container_width=True):
                common.record(audit.EXPORT, f"{label} cleaned.csv")
        with d4:
            cfg = res["config"]
            if st.download_button(
                "Configuration", cfg.to_json(), "pricelab_config.json", "application/json",
                use_container_width=True, help="Reproduces every figure in this session"):
                common.record(audit.EXPORT, "pricelab_config.json")

    st.divider()

    if "input_df" in st.session_state and current_role() in (Role.ADMINISTRATOR, Role.COMPILER):
        st.markdown("#### Register and approve")
        st.caption("A registered run can be reproduced from its content hash, parameters, "
                   "code version and environment, by anyone with access to this database. "
                   "Approving it makes it immutable: a later correction creates a new "
                   "vintage rather than editing this one.")
        rc1, rc2 = st.columns(2)
        with rc1:
            if st.button("Register this run"):
                with db.session_scope() as s:
                    run = register_run(s, st.session_state["input_df"], res["config"], label)
                    st.session_state["last_run_id"] = run.run_id
                common.record(audit.CALCULATION_RUN, label, {"registered": True})
                st.success(f"Registered as run {st.session_state['last_run_id']}.")
        with rc2:
            run_id = st.session_state.get("last_run_id")
            if run_id and current_role() == Role.ADMINISTRATOR:
                if st.button(f"Approve run {run_id}"):
                    with db.session_scope() as s:
                        approve_run(s, run_id)
                    common.record(audit.CALCULATION_RUN, label, {"approved": run_id})
                    st.success(f"Run {run_id} approved and now immutable.")

    if "indices" in res:
        st.markdown("#### Method note")
        st.markdown(method_note(res))
        if st.download_button("Download report as Markdown", build_markdown(res, nar, label),
                              f"{label} report.md", "text/markdown"):
            common.record(audit.EXPORT, f"{label} report.md")
        with st.expander("Configuration used"):
            st.code(res["config"].to_json(), language="json")
