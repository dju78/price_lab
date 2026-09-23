"""Revisions: how much a published figure moved after it was published.

Built entirely on the registry's own vintages. A correction registered on
the Reports page adds a vintage and never touches the one it supersedes, so
the original stays approved, retrievable and reproducible; this page walks
that chain, re-executes each vintage from its own stored input and
configuration, and shows what each one said about each reference period.

There is no second store of past publications here and there should not be:
one would be unreproducible, and the first thing to drift.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pricelab.core import audit, db
from pricelab.core.config import RevisionConfig
from pricelab.core.models import Role
from pricelab.core.registry import IndexRunORM, vintage_chain
from pricelab.core.security import require_role, safe_csv_with_notice
from pricelab.engine import revision as rv

from . import common


def _registered_runs() -> list[dict[str, Any]]:
    with db.session_scope() as session:
        runs = session.query(IndexRunORM).order_by(IndexRunORM.id.desc()).limit(200).all()
        return [{"run_id": r.run_id, "label": r.label, "vintage": int(r.vintage),
                 "approved": bool(r.approved), "created_at": str(r.created_at),
                 "supersedes": r.supersedes_run_id,
                 "reason": r.correction_reason} for r in runs]


@require_role(Role.ADMINISTRATOR, Role.COMPILER, Role.ANALYST)
def render() -> None:
    st.markdown("### Revisions")
    st.caption(
        "A revision is not an error: a first estimate is made on the data that had arrived by "
        "the deadline and later ones on more of it. What a reader is entitled to know is how "
        "big that difference has been, and whether it has a sign — because an estimate revised "
        "up two years out of three is not early, it is biased.")

    runs = _registered_runs()
    if not runs:
        st.info("No run has been registered yet. Register one on Reports; a correction to an "
                "approved run then creates the second vintage this page compares against.")
        return

    with_history = [r for r in runs if r["vintage"] > 1 or r["supersedes"]]
    st.markdown("#### Registered runs")
    st.dataframe(pd.DataFrame(runs), use_container_width=True, height=200)
    if not with_history:
        st.info(
            "Every registered run is at vintage 1, so nothing has been revised. On Reports, "
            "approve a run and then register a correction to it with a stated reason: that "
            "creates vintage 2, leaves vintage 1 intact and retrievable, and this page will "
            "show the triangle.")
        return

    options = {f"{r['label']} — {r['run_id']} (vintage {r['vintage']})": r["run_id"]
               for r in with_history}
    chosen = st.selectbox("Run", list(options), key="rv_run")
    run_id = options[chosen]

    c1, c2 = st.columns(2)
    max_vintages = c1.number_input("Vintages to reproduce", 2, 24, 12, key="rv_max",
                                   help="Each one re-executes a whole pipeline, so this is a "
                                        "cost, not a preference.")
    alpha = c2.number_input("Bias test significance level", 0.001, 0.2, 0.05, 0.01,
                            key="rv_alpha")
    cfg = RevisionConfig(enabled=True, max_vintages=int(max_vintages), bias_alpha=float(alpha))

    with db.session_scope() as session:
        chain = vintage_chain(session, run_id)
    st.caption("Vintage chain, oldest first: "
               + " → ".join(f"v{r.vintage} ({r.run_id})" for r in chain))

    if not st.button("Reproduce the vintages and compare", type="primary", key="rv_go"):
        st.caption(f"Not run yet. It would re-execute up to {cfg.max_vintages} pipelines.")
        return

    with st.spinner("Re-executing each vintage from its own stored input and configuration…"):
        try:
            with db.session_scope() as session:
                vintages = rv.vintages_from_registry(
                    session, run_id, cfg, actor=common.current_username())
        except (rv.RevisionError, ValueError) as exc:
            st.error(str(exc))
            return
        analysis = rv.analyse(vintages, cfg)

    common.record(audit.REVISION_ANALYSIS, run_id, {
        "vintages": len(analysis.vintages), "revisions": analysis.n_revisions,
        "mean_revision": analysis.mean_revision,
        "mean_absolute_revision": analysis.mean_absolute_revision,
        "bias_significant": analysis.bias.significant})
    st.session_state["rv_analysis"] = analysis

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Vintages compared", f"{len(analysis.vintages)}")
    m2.metric("Mean revision", f"{analysis.mean_revision:+.3f} pts")
    m3.metric("Mean absolute revision", f"{analysis.mean_absolute_revision:.3f} pts")
    m4.metric("Periods revised", f"{analysis.revised_periods:,}")
    (st.warning if analysis.bias.significant else st.info)(analysis.bias.verdict)
    for note in analysis.notes:
        st.caption(note)

    st.markdown("#### Revision triangle")
    st.caption("One row per reference period, one column per vintage. A cell is empty because "
               "that vintage had nothing to say about that period, not because a value is "
               "missing.")
    st.dataframe(analysis.triangle.round(4), use_container_width=True, height=320)

    st.markdown("#### What each vintage changed")
    st.dataframe(analysis.revisions.round(4), use_container_width=True, height=240)

    st.markdown("#### Published against current")
    st.caption("The question anyone asking about revisions is actually asking: has this "
               "period's number changed since I used it, and by how much.")
    st.dataframe(analysis.comparison.round(4), use_container_width=True, height=280)
    st.download_button(
        "Download the triangle (CSV)",
        safe_csv_with_notice(analysis.triangle.reset_index()),
        file_name=f"revision_triangle_{run_id}.csv", mime="text/csv", key="rv_csv")

    st.markdown("#### Corrections on the record")
    corrections = pd.DataFrame([
        {"vintage": v.vintage, "run_id": v.run_id, "created_at": v.created_at,
         "approved": v.approved, "supersedes": v.supersedes_run_id,
         "reason": v.correction_reason or ""} for v in analysis.vintages])
    st.dataframe(corrections, use_container_width=True)
    st.caption("Every vintage above is still registered and still reproduces from its own "
               "stored input: correcting a run adds a record, it never edits one.")
    st.info(rv.revision_note(analysis))
