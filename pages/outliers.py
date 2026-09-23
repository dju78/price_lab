"""Outliers: the four screens, and the review queue that is the only way a
quote leaves the index.

Nothing on this page deletes anything. A flagged quote is a question; an
analyst answers it with accept, reject or annotate and a written reason;
the answer goes to the ledger and to the audit log with their name on it;
and only a reject excludes the quote, by marking it, never by dropping the
row. The reason field is required by the widget, by the model
(`core.config.OutlierDecision`), by the ledger function and by the database
column, which is four times on purpose.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit, db, ledger
from pricelab.core.config import OutlierConfig, OutlierDecision
from pricelab.core.models import Role
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import outliers as ol

from . import common


def _content_hash() -> str:
    return str(st.session_state.get("content_hash", ""))


def _saved_entries(content_hash: str) -> list[OutlierDecision]:
    if not content_hash:
        return []
    with db.session_scope() as session:
        return ledger.active_outlier_entries(session, content_hash)


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Outlier review")
    st.caption(
        "Four screens, because they disagree: a quote caught by all four is a different "
        "proposition from one caught by the crude band alone. Every flag is a question for a "
        "person, and no quote leaves the index without an answer, a reason and a name.")

    analysis = common.get_active_analysis()
    if analysis is None:
        common.load_approved_run_picker(
            "Nobody has compiled a run yet. A compiler can do that on Ingest, or load a "
            "previously approved run below once one exists.")
        return
    res = analysis["result"]
    if "clean" not in res:
        st.error("This run's data failed validation and has nothing to screen.")
        return

    cfg = res["config"].outlier if res["config"].outlier.enabled else OutlierConfig()
    with st.expander("Screening parameters", expanded=False):
        st.caption(
            "The deadband is the one that decides how much work this queue creates: without "
            "it, a category whose price relatives are tightly clustered has fences a fraction "
            "of a percent wide and every rounding becomes a queue entry.")
        c1, c2, c3 = st.columns(3)
        deadband = c1.number_input("Minimum change to flag (%)", 0.0, 50.0,
                                   float(cfg.min_change_pct), 0.5, key="ol_deadband")
        tukey_k = c2.number_input("Tukey k (interquartile ranges)", 0.5, 10.0,
                                  float(cfg.tukey_k), 0.5, key="ol_k")
        hb_u = c3.number_input("Hidiroglou-Berthelot importance exponent u", 0.0, 1.0,
                               float(cfg.hb_u), 0.1, key="ol_u",
                               help="At 0 this screens the ratio alone; at 1 a small item must "
                                    "move much further than a large one before anyone looks.")
        chosen = st.multiselect("Screens", list(ol.METHODS), default=list(cfg.methods),
                                format_func=lambda m: ol.METHOD_LABELS[m], key="ol_methods")
    cfg = OutlierConfig(enabled=True, methods=tuple(chosen) or ol.METHODS,
                        min_change_pct=float(deadband), tukey_k=float(tukey_k),
                        hb_u=float(hb_u), min_cell_size=cfg.min_cell_size,
                        quartile_ratio=cfg.quartile_ratio, hb_c=cfg.hb_c,
                        ratio_low=cfg.ratio_low, ratio_high=cfg.ratio_high)

    try:
        scan = ol.detect(res["clean"], cfg)
    except ol.OutlierError as exc:
        st.error(str(exc))
        return

    content_hash = _content_hash()
    entries = _saved_entries(content_hash)
    queue = ol.pending(scan, entries)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Price relatives screened", f"{scan.relatives:,}")
    m2.metric("Flagged", f"{len(scan.queue):,}", help=f"{scan.flag_rate:.2%} of relatives")
    m3.metric("Decided", f"{len(entries):,}")
    m4.metric("Awaiting review", f"{len(queue):,}")
    for note in scan.notes:
        st.caption(note)

    if len(scan.flags):
        counts = scan.flags["method"].value_counts()
        st.caption("Flags by screen: " + ", ".join(
            f"{ol.METHOD_LABELS.get(str(m), str(m))} {int(n):,}" for m, n in counts.items()))

    st.markdown("#### Queue")
    if queue.empty:
        st.success("Nothing is awaiting review." if len(scan.queue)
                   else "No quote was flagged by any screen.")
    else:
        st.caption("Sorted by how many screens agreed. A quote caught by one screen and not "
                   "the others is evidence of a different kind from one caught by all four.")
        st.dataframe(queue, use_container_width=True, height=260)
        _decision_form(queue, scan, content_hash)

    if entries:
        st.markdown("#### Decisions taken")
        taken = ol.decided(scan, entries)
        st.dataframe(taken, use_container_width=True)
        st.download_button(
            "Download the decisions (CSV)", safe_csv_with_notice(taken),
            file_name="outlier_decisions.csv", mime="text/csv", key="ol_decisions_csv")

        cleaned, report = ol.apply_decisions(res["clean"], entries)
        st.markdown("#### What the exclusions removed")
        st.caption(
            "Reported as a share of the quotes they would have fed, in exactly the units "
            "imputation is already reported in: a reader who can see the imputation rate but "
            "not this one has been shown half the treatment.")
        e1, e2, e3 = st.columns(3)
        e1.metric("Quotes excluded", f"{report.excluded:,}")
        e2.metric("Share of the collection", f"{report.share:.2%}")
        e3.metric("Accepted / annotated",
                  f"{report.decisions.get('accept', 0):,} / "
                  f"{report.decisions.get('annotate', 0):,}")
        st.dataframe(report.by_category, use_container_width=True)
        st.info(ol.exclusion_note(report))
        _recompile_button(entries, cfg)
        del cleaned


def _decision_form(queue: pd.DataFrame, scan: ol.OutlierScan, content_hash: str) -> None:
    st.markdown("**Decide one**")
    labels = [
        f"{pd.Timestamp(row.period):%Y-%m} · {row.item_id} · {row.category} · "
        f"ratio {row.ratio:.3f} · {row.n_methods} screen(s)"
        for row in queue.head(200).itertuples()]
    if not labels:
        return
    choice = st.selectbox("Flagged quote", range(len(labels)),
                          format_func=lambda i: labels[i], key="ol_choice")
    row = queue.iloc[int(choice)]

    with st.form("outlier_decision"):
        decision = st.radio("Decision", list(ol.DECISIONS), horizontal=True,
                            format_func=lambda d: ol.DECISION_LABELS[d], key="ol_decision")
        reason = st.text_area(
            "Reason (required)", key="ol_reason",
            placeholder="e.g. confirmed with the shop: the price was keyed in pence, not pounds",
            help="A quote excluded from a published index without a stated reason is a "
                 "deletion nobody can review. This field is required here, on the model, in "
                 "the ledger and in the database column.")
        submitted = st.form_submit_button("Record decision", type="primary")

    if not submitted:
        return
    if not reason.strip():
        st.error("A decision needs a reason. Nothing was recorded.")
        return
    if not content_hash:
        st.error("This session has no uploaded collection to record a decision against.")
        return

    methods = str(row.get("methods", ""))
    entry = OutlierDecision(
        period=str(pd.Timestamp(row["period"]).date()), item_id=str(row["item_id"]),
        category=str(row.get("category", "")), method=methods,
        statistic=float(row.get("ratio", 0.0)), decision=decision, reason=reason.strip(),
        analyst=common.current_username(), decided_at=datetime.now(UTC).isoformat())
    with db.session_scope() as session:
        ledger.record_outlier_decision(session, common.current_username(), content_hash, entry)
    common.record(audit.OUTLIER_DECISION, f"{entry.item_id} at {entry.period}", {
        "decision": entry.decision, "reason": entry.reason, "screens": methods,
        "ratio": entry.statistic, "content_hash": content_hash})
    st.success(f"Recorded: {ol.DECISION_LABELS[decision]} — {reason.strip()}")
    del scan
    st.rerun()


def _recompile_button(entries: list[OutlierDecision], cfg: OutlierConfig) -> None:
    st.caption(
        "The decisions above are recorded but not yet in the compiled index. Recompiling "
        "applies them: rejected quotes are excluded and the gap is filled by whatever "
        "imputation this run configured.")
    if not st.button("Recompile with these decisions", key="ol_recompile"):
        return
    if "input_df" not in st.session_state or "run_config" not in st.session_state:
        st.error("The original upload is no longer in this session; re-upload to recompile.")
        return
    config: Any = st.session_state["run_config"]
    updated = config.model_copy(update={
        "outlier": cfg.model_copy(update={"enabled": True, "entries": list(entries)})})
    common.compile_and_store(
        st.session_state["input_df"], updated,
        st.session_state.get("analysis", {}).get("label", updated.label),
        st.session_state.get("file_bytes", b""), trigger="outlier_review")
    st.success("Recompiled. The excluded quotes and their share are in every export.")
    st.rerun()
