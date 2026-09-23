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
IMPUTATION_METHODS = ["none", "class_mean", "carry_forward", "seasonal_hold",
                      "targeted_mean", "overall_mean"]
#: "custom" is last deliberately: it is the escape hatch, not a peer
#: of the four named formulae, and a run using it is marked
#: non-standard everywhere it is exported.
INDEX_FORMULAS = ["jevons", "dutot", "carli", "laspeyres", "paasche", "fisher", "tornqvist",
                  "walsh", "marshall_edgeworth", "geometric_laspeyres", "geometric_paasche",
                  "unit_value", "custom"]

LIFECYCLE_STAGES = (
    "Ingest", "Quality", "Imputation", "Quality adjustment", "Index build", "Findings",
    "Diagnostics", "Reports", "Audit")


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
        "Quality adjustment": computed,
        "Index build": computed,
        "Findings": computed,
        "Diagnostics": computed,
        "Reports": computed,
        "Audit": True,
    }
    st.sidebar.markdown("**Workflow**")
    lines = [f"{'✅' if reached[stage] else '▫️'} {stage}" for stage in LIFECYCLE_STAGES]
    st.sidebar.markdown("  \n".join(lines))


def compile_and_store(df: Any, cfg: Any, label: str, file_bytes: bytes, *,
                      trigger: str = "compile") -> dict[str, Any]:
    """Run (or fetch from the bounded cache) the analysis for this data and
    configuration, and make it the session's active analysis.

    Shared by Ingest, which compiles after upload, and Quality adjustment,
    which recompiles after an approval changes the ledger inside the
    configuration. The cache key is the file bytes, the full config JSON
    (ledger included) and the label, so an approval is a new key, never a
    stale hit.
    """
    from pricelab import analyse
    from pricelab.core.cache import content_key, get_analysis_cache

    cache = get_analysis_cache()
    key = content_key(file_bytes, cfg.to_json(), label)
    cached = cache.get(key)
    if cached is None:
        with st.spinner("Diagnosing, cleaning, indexing and writing the findings…"):
            cached = analyse(df, label, cfg)
        cached.pop("charts", None)  # rebuilt fresh on demand; see core/cache.py
        cache.set(key, cached)
        record(audit.CALCULATION_RUN, label, {
            "formula": cfg.index.formula, "chained": cfg.index.chained, "rows": len(df),
            "quality_adjustments": len(cfg.quality_adjustment.entries), "trigger": trigger,
            "correlation_id": cached["result"].get("correlation_id"),
            "content_hash": st.session_state.get("content_hash")})
        # The cleaned layer and its transformation log, beside the raw
        # layer the upload wrote: the raw-to-cleaned step is now on disk
        # and replayable for this run's exact configuration.
        if "indices" in cached["result"]:
            from pricelab.core.config import get_settings
            from pricelab.data import store

            store.write_cleaned_layer(df, cfg, get_settings().store_dir)

    st.session_state["analysis"] = {**cached, "label": label}
    st.session_state["input_df"] = df
    st.session_state["run_config"] = cfg
    st.session_state.pop("loaded_run_id", None)
    return cached



def read_upload_table(upload: Any, required: tuple[str, ...]) -> Any:
    """An uploaded CSV as a DataFrame with lower-case column names, refused
    with the missing column names rather than guessed at when a column the
    page needs is not there."""
    import io

    import pandas as pd

    frame = pd.read_csv(io.BytesIO(upload.getvalue()))
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"{upload.name} has no {', '.join(missing)} column(s); it needs "
                         f"{', '.join(required)}")
    if "period" in frame.columns:
        frame["period"] = pd.to_datetime(frame["period"], errors="raise")
    return frame


# ---------------------------------------------------------------------
# Uncertainty beside every headline
# ---------------------------------------------------------------------
#: Every page in the analyst view that shows a published headline figure,
#: and what it shows. Each calls `show_uncertainty` where the figure
#: appears, so the figure carries its interval or a statement that none has
#: been quantified. `tests/test_uncertainty.py` scans pages/ and fails when
#: a page shows a headline without being listed here, or is listed without
#: calling it -- the surface inventory pattern of the seasonal work.
HEADLINE_SURFACES: dict[str, str] = {
    "findings": "All items level, average annual rate and peak rate",
    "index_build": "category and All items indices, levels and annualised rates",
    "decomposition": "rates of change of the headline, and its change decomposed",
    "multilateral": "the multilateral All items headline",
    "quality_adjustment": "the effect of the quality adjustments on the headline",
    "seasonal": "the seasonally adjusted headline",
    "property": "residential property price indices",
    "trade": "import and export price indices and the terms of trade",
    "construction": "construction input cost and output price indices",
    "spatial": "purchasing power parities",
    "deflation": "real values",
    "housing": "the rental price index and the owner-occupied housing indices",
    "diagnostics": "the headline under alternative formulae, and its chain drift",
    "sources": "the compiled headline beside an official series",
}

#: Pages that read the indices or show metrics without showing a published
#: headline figure, and why -- so the scan can tell an exemption from an
#: omission.
HEADLINE_EXEMPT: dict[str, str] = {
    "audit_log": "reads the headline only to check it against the registered figure",
    "outliers": "counts of screened, flagged and excluded quotes",
    "revisions": "revision statistics about the headline, not the headline itself",
}

UNCERTAINTY_STATE = "un_interval"
SENSITIVITY_STATE = "un_sensitivity"


def show_uncertainty(figure: str, *, run_headline: bool = True, reason: str | None = None
                     ) -> list[str]:
    """Caption the figure with its sampling uncertainty, or with the
    statement that none has been quantified -- and, separately and labelled
    as such, with the methodological sensitivity range where one has been
    computed. The two are never combined into one statement.

    `run_headline` is True for the compiled run's All items figures, which
    the Uncertainty page can quantify; for other measures (property prices,
    trade, parities) `reason` says why no interval exists. Returns the
    captions shown, for tests.
    """
    from pricelab.engine.uncertainty import NOT_QUANTIFIED

    shown: list[str] = []
    label = (get_active_analysis() or {}).get("label")
    held = st.session_state.get(UNCERTAINTY_STATE) if run_headline else None
    if held is not None and held.get("label") == label:
        shown.append(f"{figure} -- sampling uncertainty: {held['result'].label}")
    elif run_headline:
        shown.append(f"{figure} -- {NOT_QUANTIFIED} Declare the design on the Uncertainty "
                     "page to estimate an interval.")
    else:
        shown.append(f"{figure} -- sampling uncertainty has not been quantified: "
                     + (reason or "no sampling design is available for this measure") + ".")
    sensitivity = st.session_state.get(SENSITIVITY_STATE) if run_headline else None
    if sensitivity is not None and sensitivity.get("label") == label:
        shown.append(f"{figure} -- {sensitivity['result'].label}")
    for text in shown:
        st.caption(text)
    return shown
