"""Reports: every export of the active run, each carrying the same
provenance stamp; the run registry's register/approve controls; and the
method note.

Open to every role, including viewer: reading the method note and
reproducing a published figure is exactly the auditor's job in the
platform's audience list, and it requires no ability to change anything.

The stamp (`core.provenance.build_stamp`) is built once per render and
handed to every exporter, so a deck, a report, an evidence pack, a CSV
and an SDMX message downloaded from the same screen name the same run,
data vintage, code version and parameters. The PDF bulletin is offered
only once the run is registered, because its headline is read from the
registry rather than from the screen.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from pricelab import build_all_charts, build_deck, build_docx, build_markdown, method_note
from pricelab.core import audit, db
from pricelab.core.models import Role
from pricelab.core.provenance import build_stamp
from pricelab.core.registry import IndexRunORM, approve_run, correct_run, register_run
from pricelab.core.security import current_role
from pricelab.engine.custom import non_standard_notice
from pricelab.reporting import bulletin, excel, exports

from . import common


def _registry_row(res: dict[str, Any]) -> IndexRunORM | None:
    """The registry row for the active run, if it has been registered
    (by this session or any earlier one)."""
    run_id = st.session_state.get("last_run_id") or st.session_state.get("loaded_run_id")
    with db.session_scope() as s:
        if run_id:
            row = s.query(IndexRunORM).filter_by(run_id=run_id).one_or_none()
            if row is not None:
                s.expunge(row)
                return row
        input_df = st.session_state.get("input_df")
        if input_df is None:
            return None
        import hashlib

        from pricelab.core.registry import _hash_dataframe
        content = hashlib.sha256(
            (_hash_dataframe(input_df) + res["config"].to_json()).encode("utf-8")).hexdigest()
        row = s.query(IndexRunORM).filter_by(run_id=content[:16]).one_or_none()
        if row is not None:
            s.expunge(row)
        return row


def _download(col: Any, label: str, data: Any, file_name: str, mime: str, **kwargs: Any) -> None:
    with col:
        if st.download_button(label, data, file_name, mime, use_container_width=True, **kwargs):
            common.record(audit.EXPORT, file_name)


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
    run = _registry_row(res)
    vintage = st.session_state.get("data_vintage") or {}
    stamp = build_stamp(
        res, label, run=run,
        data_vintage=vintage.get("content_hash") or st.session_state.get("content_hash"),
        data_source=vintage.get("source"), data_received_at=vintage.get("received_at"))

    if "indices" in res and nar is not None:
        st.caption(f"Every download below carries provenance stamp run `{stamp.run_id}`"
                   + ("" if stamp.registered else " (register the run to give it a registry "
                      "identifier)") + f", data vintage `{stamp.data_vintage[:16]}…`, code "
                   f"`{stamp.code_version}`.")
        charts = build_all_charts(res)
        notice = non_standard_notice(res["config"].index)
        d1, d2, d3, d4 = st.columns(4)
        _download(d1, "Slide deck", build_deck(res, nar, dict(charts), label, stamp=stamp),
                  f"{label} analysis.pptx",
                  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                  type="primary")
        _download(d2, "Written report", build_docx(res, nar, dict(charts), label, stamp=stamp),
                  f"{label} report.docx",
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        with db.session_scope() as s:
            audit_extract = audit.extract_for_run(
                s, label=label, content_hash=st.session_state.get("content_hash"),
                run_id=run.run_id if run else None, correlation_id=res.get("correlation_id"))
        _download(d3, "Excel evidence pack",
                  excel.build_evidence_pack(res, stamp, source=st.session_state.get("input_df", res["clean"]),
                                            decisions=analysis.get("decisions", []),
                                            audit_events=audit_extract),
                  f"{label} evidence pack.xlsx",
                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        _download(d4, "Configuration", res["config"].to_json(), "pricelab_config.json",
                  "application/json", help="Reproduces every figure in this session")

        e1, e2, e3, e4 = st.columns(4)
        _download(e1, "Index (CSV)", exports.index_csv(res, stamp, notice), f"{label} index.csv",
                  "text/csv", help="Publication table with disclosure control applied")
        _download(e2, "Index (SDMX-ML 2.1)", exports.to_sdmx_ml(res, stamp), f"{label} index.sdmx.xml",
                  "application/xml")
        _download(e3, "Cleaned data (CSV)", exports.stamped_csv(res["imputed"], stamp, notice),
                  f"{label} cleaned.csv", "text/csv")
        with e4:
            if run is None:
                st.button("PDF bulletin", disabled=True, use_container_width=True,
                          help="Register the run first: the bulletin's headline is read from "
                               "the registry, not from the screen.")
            else:
                try:
                    pdf = bulletin.build_bulletin(res, nar, charts, stamp, run=run)
                except bulletin.BulletinError as exc:
                    st.error(str(exc))
                else:
                    _download(e4, "PDF bulletin", pdf, f"{label} bulletin.pdf", "application/pdf")

    st.divider()

    if "input_df" in st.session_state and current_role() in (Role.ADMINISTRATOR, Role.COMPILER):
        st.markdown("#### Register and approve")
        st.caption("A registered run can be reproduced from its content hash, parameters, "
                   "code version and environment, by anyone with access to this database, "
                   "and its headline figure is recorded for release. Approving it makes it "
                   "immutable: a later correction creates a new vintage rather than editing "
                   "this one.")
        rc1, rc2 = st.columns(2)
        with rc1:
            if st.button("Register this run"):
                with db.session_scope() as s:
                    registered = register_run(
                        s, st.session_state["input_df"], res["config"], label, result=res,
                        data_source=vintage.get("source"),
                        data_received_at=vintage.get("received_at"))
                    st.session_state["last_run_id"] = registered.run_id
                common.record(audit.CALCULATION_RUN, label, {
                    "registered": True, "run_id": st.session_state["last_run_id"],
                    "correlation_id": res.get("correlation_id")})
                st.success(f"Registered as run {st.session_state['last_run_id']}.")
                st.rerun()
        with rc2:
            run_id = st.session_state.get("last_run_id")
            if run_id and current_role() == Role.ADMINISTRATOR:
                if st.button(f"Approve run {run_id}"):
                    with db.session_scope() as s:
                        approve_run(s, run_id)
                    common.record(audit.CALCULATION_RUN, label, {"approved": run_id})
                    st.success(f"Run {run_id} approved and now immutable.")
                    st.rerun()

        # A correction: this run registered as a new vintage of an approved
        # one, with a reason, the original left untouched (core.registry.
        # correct_run). The bulletin's revision statement reads this chain.
        with db.session_scope() as s:
            approved = s.query(IndexRunORM).filter_by(approved=True).order_by(
                IndexRunORM.created_at.desc()).limit(50).all()
            approved_options = {f"{r.label} — {r.run_id} (vintage {r.vintage})": r.run_id
                                for r in approved}
        if approved_options:
            st.markdown("**Register as a correction**")
            st.caption("An approved run cannot be edited. Registering the active run as a "
                       "correction of one creates vintage n+1 with your reason; the superseded "
                       "vintage stays on record and reproducible.")
            target = st.selectbox("Approved run being corrected", list(approved_options))
            reason = st.text_input("Reason for the correction (required)", key="correction_reason")
            if st.button("Register correction", disabled=not reason.strip()):
                try:
                    with db.session_scope() as s:
                        corrected = correct_run(
                            s, approved_options[target], st.session_state["input_df"],
                            res["config"], label, reason)
                        new_id, new_vintage = corrected.run_id, corrected.vintage
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["last_run_id"] = new_id
                    common.record(audit.CALCULATION_RUN, label, {
                        "correction_of": approved_options[target], "run_id": new_id,
                        "vintage": new_vintage, "reason": reason})
                    st.success(f"Registered as run {new_id}, vintage {new_vintage}, superseding "
                               f"{approved_options[target]}.")
                    st.rerun()

    if "indices" in res:
        st.markdown("#### Method note")
        st.markdown(method_note(res))
        if st.download_button("Download report as Markdown",
                              build_markdown(res, nar, label, stamp=stamp),
                              f"{label} report.md", "text/markdown"):
            common.record(audit.EXPORT, f"{label} report.md")
        with st.expander("Provenance stamp"):
            st.code(stamp.as_text())
        with st.expander("Configuration used"):
            st.code(res["config"].to_json(), language="json")
