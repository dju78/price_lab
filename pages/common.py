"""Shared state and helpers used by more than one page.

The active analysis lives in `st.session_state`, exactly as it did in the
original single-page app.py; splitting the workflow into pages changes how
that state is read, not where it lives. A page that cannot reach Ingest
(analyst, viewer) can still work from a previously *approved* run, loaded
through the run registry rather than through a fresh upload.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from pricelab.core import audit, db
from pricelab.core.registry import IndexRunORM, reproduce

KIND_LABEL = {
    "quality": "Data quality", "structure": "Sample structure",
    "trend": "Price movement", "seasonal": "Seasonality", "method": "Method",
}
IMPUTATION_METHODS = ["none", "class_mean", "carry_forward", "seasonal_hold"]
INDEX_FORMULAS = ["jevons", "dutot", "carli", "laspeyres"]

LIFECYCLE_STAGES = (
    "Ingest", "Quality", "Imputation", "Index build", "Findings", "Diagnostics", "Reports")


def current_username() -> str:
    return str(st.session_state.get("pricelab_username", "unknown"))


def record(action: str, target: str, params: dict[str, Any] | None = None) -> None:
    """Append one audit event for the signed-in user. Every page that
    changes something -- loads data, changes a parameter, runs a
    calculation, overrides a treatment, exports a file -- calls this rather
    than letting the action happen unlogged."""
    with db.session_scope() as s:
        audit.record_event(s, current_username(), action, target, params)


def get_active_analysis() -> dict[str, Any] | None:
    """The analysis this session should show: its own freshly computed one
    if it has one, otherwise a previously loaded approved run, otherwise
    None."""
    analysis = st.session_state.get("analysis")
    return analysis if isinstance(analysis, dict) else None


def has_active_analysis() -> bool:
    return get_active_analysis() is not None


def load_registered_run(run_id: str, actor: str) -> tuple[dict[str, Any], bool]:
    """Reproduce a registered run and report whether loading it just
    upconverted a legacy (pre-reference-period-split) config.

    `core.registry.reproduce` already writes the LEGACY_CONFIG_UPCONVERTED
    audit event itself (it has the session and the actor); this is a thin,
    directly testable wrapper so the interface layer that calls it -- the
    only place in the app a legacy config can be loaded from -- can also
    surface the fact to whoever is looking at the screen, rather than an
    automatic reinterpretation of an old run's parameters being visible
    only to someone who later goes looking in the audit log.
    """
    with db.session_scope() as s:
        result = reproduce(s, run_id, actor=actor)
    config = result.get("config")
    upconverted = bool(getattr(config, "legacy_upconverted", False))
    return result, upconverted


def load_approved_run_picker(empty_message: str = "No approved run is available yet.") -> None:
    """Let a page with no fresh analysis of its own load a previously
    approved, registered run instead. Used by pages an analyst or viewer
    can reach even though they cannot use Ingest."""
    with db.session_scope() as s:
        runs = (
            s.query(IndexRunORM)
            .filter_by(approved=True)
            .order_by(IndexRunORM.created_at.desc())
            .limit(50)
            .all()
        )
        options = {f"{r.label} — {r.run_id} (vintage {r.vintage})": r.run_id for r in runs}

    if not options:
        st.info(empty_message)
        return

    choice = st.selectbox("Load an approved run", list(options.keys()))
    if st.button("Load this run"):
        run_id = options[choice]
        result, upconverted = load_registered_run(run_id, current_username())
        if upconverted:
            st.warning(
                f"Run {run_id} was registered under a configuration format that predates "
                "the price/weight/index reference period split. Its parameters were "
                "upgraded automatically on load, and this has been recorded in the "
                "audit log.")

        from pricelab.engine.insights import build_narrative

        narrative = build_narrative(result) if "indices" in result else None
        st.session_state["analysis"] = {
            "result": result, "narrative": narrative, "decisions": [], "label": choice,
        }
        st.session_state["loaded_run_id"] = run_id
        record(audit.DATA_LOAD, f"approved run {run_id}", {"source": "registry"})
        st.rerun()


def workflow_progress() -> None:
    """Sidebar checklist of lifecycle stages reached this session.

    Quality, Imputation and Index build are computed together in one
    `run_pipeline` call and have no separate checkpoints of their own, so
    they are shown reached together, honestly reflecting how the engine
    actually runs rather than implying a staged compilation that does not
    exist.
    """
    ingested = "file_bytes" in st.session_state or st.session_state.get("loaded_run_id") is not None
    computed = has_active_analysis()
    reached = {
        "Ingest": ingested,
        "Quality": computed,
        "Imputation": computed,
        "Index build": computed,
        "Findings": computed,
        "Diagnostics": computed,
        "Reports": computed,
    }
    st.sidebar.markdown("**Workflow**")
    lines = [f"{'✅' if reached[stage] else '▫️'} {stage}" for stage in LIFECYCLE_STAGES]
    st.sidebar.markdown("  \n".join(lines))
